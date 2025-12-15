"""Analysis Agent for document accessibility analysis (PRD-012).

This agent performs deep document analysis using Claude Sonnet 4.5 (REASONING tier)
to guide the remediation pipeline.

The Analysis Agent:
1. Analyzes document structure and layout
2. Detects features on each page (images, tables, lists, etc.)
3. Determines which specialized agents need to run
4. Generates initial accessibility observations
5. Produces a DocumentManifest to guide extraction

Uses Claude Sonnet 4.5 for superior reasoning capabilities.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Output Models for Structured LLM Response
# =============================================================================


class AnalysisPageFeatures(BaseModel):
    """Page features detected during analysis (matches PageFeatures schema)."""

    page_num: int = Field(..., ge=1, description="1-indexed page number")
    has_images: bool = Field(
        default=False,
        description=(
            "True if page contains INFORMATIVE images (charts, diagrams, photos, "
            "screenshots with content). Exclude decorative borders, backgrounds, logos, "
            "spacers, and purely visual flourishes."
        ),
    )
    image_count: int = Field(
        default=0,
        ge=0,
        description="Count of informative images only (same criteria as has_images).",
    )
    has_tables: bool = Field(
        default=False,
        description=(
            "True if page contains DATA tables with rows and columns. "
            "Exclude layout tables used purely for positioning content."
        ),
    )
    table_count: int = Field(
        default=0,
        ge=0,
        description="Count of data tables only (same criteria as has_tables).",
    )
    has_lists: bool = Field(
        default=False,
        description=(
            "True if page contains ordered lists, unordered lists, or definition lists. "
            "Look for bullet points, numbered items, or term-definition pairs."
        ),
    )
    has_code_blocks: bool = Field(
        default=False,
        description=(
            "True if page contains programming code, command-line examples, or "
            "monospace-formatted technical content that should be in code blocks."
        ),
    )
    has_math: bool = Field(
        default=False,
        description=(
            "True if page contains mathematical notation, equations, formulas, "
            "or expressions that would need MathML or LaTeX representation."
        ),
    )
    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        default="single_column",
        description=(
            "Layout for THIS PAGE only. 'single_column'=one text flow; "
            "'two_column'=side-by-side columns; 'mixed'=ONLY if multiple layouts "
            "appear on the SAME page. If page 1 is single and page 2 is two-column, "
            "each gets their own layout_type (NOT 'mixed')."
        ),
    )
    has_headers_footers: bool = Field(
        default=False,
        description=(
            "True if page has repeating header/footer content (page numbers, "
            "document title, chapter headings, logos that appear on every page)."
        ),
    )
    complexity_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Page complexity: 0.0=simple (plain text, clear structure, minimal formatting); "
            "0.5=moderate (some tables, images, or lists); "
            "1.0=very complex (nested tables, multi-column, dense figures, mixed layouts). "
            "Consider: table nesting depth, list nesting depth, image density, column count, "
            "and overall visual density."
        ),
    )
    complexity_factors: list[str] = Field(
        default_factory=list,
        description=(
            "Specific factors contributing to complexity. Examples: 'nested_tables', "
            "'merged_cells', 'multi_column', 'dense_images', 'complex_lists', "
            "'mixed_layout', 'math_equations', 'code_blocks'."
        ),
    )


class AnalysisObservation(BaseModel):
    """Observation format for analysis output (converted to full Observation later)."""

    page_num: int = Field(..., ge=1, description="Page number where issue appears")
    visual_description: str = Field(
        ...,
        description=(
            "What is visually presented in the PDF at this location. "
            "Describe the actual visual content objectively without interpretation."
        ),
    )
    markup_issue: str = Field(
        ...,
        description=(
            "The accessibility issue with the markup. Describe what's wrong or missing, "
            "not how to fix it."
        ),
    )
    severity: Literal["critical", "major", "minor"] = Field(
        default="major",
        description=(
            "critical=Blocks access entirely (missing alt on key diagram, broken table "
            "structure, unreadable content). "
            "major=Significant barrier (skipped heading level, unclear reading order, "
            "missing image description). "
            "minor=Inconvenience (missing emphasis markup, suboptimal but functional, "
            "cosmetic issues)."
        ),
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence in this observation (0.0-1.0). Lower if: image is blurry, "
            "content is ambiguous, multiple interpretations possible, or visual "
            "evidence is unclear."
        ),
    )


class AnalysisOutput(BaseModel):
    """Structured output from analysis phase."""

    # Document metadata
    document_title: str = Field(default="Untitled")
    document_type: str = Field(
        default="unknown",
        description="syllabus, lecture_notes, exam, handout, research_paper, other",
    )

    # Structure
    heading_tree: HeadingTree

    # Per-page features
    page_features: list[AnalysisPageFeatures]

    # Agent routing
    required_agents: list[str] = Field(
        default_factory=list,
        description="Agents needed: figures, tables, structure, typography",
    )

    # Initial observations
    observations: list[AnalysisObservation] = Field(default_factory=list)

    # Confidence
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    notes: str = Field(default="", description="Notes about analysis decisions")


# =============================================================================
# Agent Configuration
# =============================================================================


@dataclass
class AnalysisAgentConfig:
    """Configuration for the analysis agent."""

    prompts_file: Path = field(default_factory=lambda: Path("analysis.yaml"))
    max_retries: int = 2
    temperature: float = 0.3  # Slightly higher for analytical reasoning
    max_tokens: int = 4096


# =============================================================================
# Analysis Agent
# =============================================================================


class AnalysisAgent:
    """Agent that performs deep document analysis using Sonnet.

    This agent:
    1. Analyzes document structure and layout
    2. Detects features on each page (images, tables, lists, etc.)
    3. Determines which specialized agents need to run
    4. Generates initial accessibility observations
    5. Produces a DocumentManifest to guide extraction

    Uses Claude Sonnet 4.5 for superior reasoning capabilities.

    Example:
        >>> agent = AnalysisAgent()
        >>> manifest, observations, usage = await agent.analyze(pages, job_id)
        >>> print(f"Found {len(manifest.required_agents)} agents needed")
    """

    # All possible specialized agents
    ALL_AGENTS = {"figures", "tables", "structure", "typography"}

    def __init__(self, config: AnalysisAgentConfig | None = None) -> None:
        """Initialize the analysis agent.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or AnalysisAgentConfig()
        self.model_tier = ModelTier.REASONING
        self.model_id = MODEL_TIER_MAP[self.model_tier]
        self.prompts = self._load_prompts()
        self._agent: Agent[None, AnalysisOutput] | None = None

        logger.info(
            f"AnalysisAgent initialized with model tier {self.model_tier.value} "
            f"({self.model_id})"
        )

    def _load_prompts(self) -> dict[str, Any]:
        """Load prompts from YAML configuration file.

        Raises:
            FileNotFoundError: If prompts file does not exist (fail fast, no fallback)
        """
        prompts_file = self.config.prompts_file
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        with open(prompts_file) as f:
            prompts: dict[str, Any] = yaml.safe_load(f)
            logger.debug(f"Loaded prompts from {prompts_file}")
            return prompts

    def _get_agent(self) -> Agent[None, AnalysisOutput]:
        """Get or create the analysis agent."""
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(model_name=self.model_id)
            self._agent = Agent(
                model,
                output_type=AnalysisOutput,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )
            logger.debug(f"Created analysis agent with model {self.model_id}")
        return self._agent

    async def analyze(
        self,
        pages: list[PageData],
        job_id: str,
    ) -> tuple[DocumentManifest, list[Observation], LLMUsage]:
        """Analyze document and produce manifest + initial observations.

        Args:
            pages: List of page images from PDF conversion
            job_id: Job identifier for observation tracking

        Returns:
            Tuple of (DocumentManifest, list[Observation], LLMUsage)

        Raises:
            ValueError: If no pages provided
            RuntimeError: If analysis fails after retries
        """
        if not pages:
            raise ValueError("No pages provided for analysis")

        total_pages = len(pages)
        logger.info(f"Starting analysis of {total_pages}-page document for job {job_id}")

        agent = self._get_agent()

        # Build messages with all page images
        messages = self._build_image_messages(pages)

        # Add user prompt
        user_prompt = self.prompts["user_prompt"].format(total_pages=total_pages)
        messages.append(user_prompt)

        # Run agent
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )

        # Extract usage
        usage_data = result.usage()
        input_tokens = usage_data.request_tokens or 0
        output_tokens = usage_data.response_tokens or 0
        total_tokens = input_tokens + output_tokens

        pricing = get_pricing_for_tier(self.model_tier)
        estimated_cost_cents = calculate_estimated_cost(
            input_tokens, output_tokens, pricing
        )

        usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        # Convert output to DocumentManifest
        output = result.output
        manifest = self._create_manifest(output, job_id, total_pages)

        # Convert observations to full Observation model
        observations = self._convert_observations(output.observations, job_id)

        logger.info(
            f"Analysis complete for job {job_id}: "
            f"{len(manifest.page_features)} pages analyzed, "
            f"{len(manifest.required_agents)} agents needed, "
            f"{len(observations)} initial observations, "
            f"cost: ${estimated_cost_cents/100:.4f}"
        )

        return manifest, observations, usage

    def _build_image_messages(
        self, pages: list[PageData]
    ) -> list[str | BinaryContent]:
        """Build message list with all page images."""
        messages: list[str | BinaryContent] = []

        for page in pages:
            messages.append(f"[Page {page.page_num}]")
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                messages.append(
                    BinaryContent(data=image_bytes, media_type="image/png")
                )

        return messages

    def _create_manifest(
        self,
        output: AnalysisOutput,
        job_id: str,
        total_pages: int,
    ) -> DocumentManifest:
        """Convert analysis output to DocumentManifest."""
        # Convert AnalysisPageFeatures to PageFeatures
        page_features = [
            PageFeatures(
                page_num=pf.page_num,
                has_images=pf.has_images,
                image_count=pf.image_count,
                has_tables=pf.has_tables,
                table_count=pf.table_count,
                has_lists=pf.has_lists,
                has_code_blocks=pf.has_code_blocks,
                has_math=pf.has_math,
                layout_type=pf.layout_type,
                has_headers_footers=pf.has_headers_footers,
                complexity_score=pf.complexity_score,
                complexity_factors=pf.complexity_factors,
            )
            for pf in output.page_features
        ]

        # Ensure we have features for all pages (fill in missing ones)
        existing_page_nums = {pf.page_num for pf in page_features}
        for page_num in range(1, total_pages + 1):
            if page_num not in existing_page_nums:
                page_features.append(PageFeatures(page_num=page_num))

        # Sort by page number
        page_features.sort(key=lambda pf: pf.page_num)

        # Compute skip agents
        skip_agents = self._determine_skip_agents(output.required_agents)

        return DocumentManifest(
            job_id=job_id,
            document_title=output.document_title,
            document_type=output.document_type,
            total_pages=total_pages,
            heading_tree_json=output.heading_tree.model_dump_json(),
            page_features=page_features,
            required_agents=output.required_agents,
            skip_agents=skip_agents,
            analysis_confidence=output.confidence,
            analysis_notes=output.notes,
            analysis_model=self.model_id,
        )

    def _convert_observations(
        self,
        analysis_observations: list[AnalysisObservation],
        job_id: str,
    ) -> list[Observation]:
        """Convert analysis observations to full Observation model."""
        return [
            Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="analysis",
                source="agent",
                visual_description=obs.visual_description,
                markup_description=obs.markup_issue,
                location=ObservationLocation(
                    location_type="region",
                    value=f"Page {obs.page_num}",
                    page_num=obs.page_num,
                ),
                confidence=obs.confidence,
                severity=obs.severity,
                route="auto" if obs.confidence >= settings.min_confidence_for_auto_approval else "manual",
            )
            for obs in analysis_observations
        ]

    def _determine_skip_agents(self, required: list[str]) -> list[str]:
        """Determine which agents can be skipped."""
        return list(self.ALL_AGENTS - set(required))


__all__ = [
    "AnalysisAgent",
    "AnalysisAgentConfig",
    "AnalysisOutput",
    "AnalysisObservation",
    "AnalysisPageFeatures",
    "HeadingNode",
    "HeadingTree",
]
