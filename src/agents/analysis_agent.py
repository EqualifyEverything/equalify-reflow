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
import time
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
from src.services.debug_logging_service import debug_logger
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
    has_images: bool = Field(default=False)
    image_count: int = Field(default=0, ge=0)
    has_tables: bool = Field(default=False)
    table_count: int = Field(default=0, ge=0)
    has_lists: bool = Field(default=False)
    has_code_blocks: bool = Field(default=False)
    has_math: bool = Field(default=False)
    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        default="single_column"
    )
    has_headers_footers: bool = Field(default=False)
    complexity_score: float = Field(default=0.5, ge=0.0, le=1.0)
    complexity_factors: list[str] = Field(default_factory=list)


class AnalysisObservation(BaseModel):
    """Observation format for analysis output (converted to full Observation later)."""

    page_num: int = Field(..., ge=1, description="Page number where issue appears")
    visual_description: str = Field(
        ..., description="What is visually presented in the PDF"
    )
    markup_issue: str = Field(
        ..., description="The accessibility issue with the markup"
    )
    severity: Literal["critical", "major", "minor"] = Field(default="major")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


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
        """Load prompts from YAML configuration file."""
        prompts_file = self.config.prompts_file
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        try:
            with open(prompts_file) as f:
                prompts: dict[str, Any] = yaml.safe_load(f)
                logger.debug(f"Loaded prompts from {prompts_file}")
                return prompts
        except FileNotFoundError:
            logger.warning(f"Prompts file not found: {prompts_file}, using defaults")
            return self._default_prompts()

    def _default_prompts(self) -> dict[str, Any]:
        """Default prompts for analysis agent."""
        return {
            "system_prompt": ANALYSIS_SYSTEM_PROMPT,
            "user_prompt": ANALYSIS_USER_PROMPT,
        }

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

        # Save debug images if enabled
        images_to_save = [
            (base64.b64decode(p.image_base64), p.page_num)
            for p in pages if p.image_base64
        ]
        if images_to_save:
            debug_logger.save_debug_images_batch(
                job_id=job_id,
                agent_name="analysis_agent",
                images=images_to_save,
            )

        # Add user prompt
        user_prompt = self.prompts["user_prompt"].format(total_pages=total_pages)
        messages.append(user_prompt)

        # Debug log: prompt being sent
        image_info = None
        if settings.debug_log_images:
            image_info = {
                "page_count": len(pages),
                "pages_with_images": sum(1 for p in pages if p.image_base64),
            }

        debug_logger.log_prompt(
            job_id=job_id,
            agent_name="analysis_agent",
            system_prompt=self.prompts.get("system_prompt"),
            user_message=user_prompt,
            image_info=image_info,
            model_id=self.model_id,
            model_tier=self.model_tier.value,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        # Run agent
        start_time = time.time()
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )
        duration_ms = (time.time() - start_time) * 1000

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

        # Debug log: response received
        debug_logger.log_response(
            job_id=job_id,
            agent_name="analysis_agent",
            response_text=None,  # Structured output, not raw text
            parsed_output=result.output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
            duration_ms=duration_ms,
            model_id=self.model_id,
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
                route="auto" if obs.confidence >= 0.7 else "manual",
            )
            for obs in analysis_observations
        ]

    def _determine_skip_agents(self, required: list[str]) -> list[str]:
        """Determine which agents can be skipped."""
        return list(self.ALL_AGENTS - set(required))


# =============================================================================
# Default Prompts
# =============================================================================

ANALYSIS_SYSTEM_PROMPT = """You are an expert document accessibility analyst specializing in \
PDF remediation for educational materials.

Your task is to perform deep analysis of PDF documents to guide the accessibility remediation pipeline.

ANALYSIS OBJECTIVES:

1. DOCUMENT CLASSIFICATION
   - Identify document type: syllabus, lecture_notes, exam, handout, research_paper, other
   - Note any special characteristics (multi-column layout, scanned content, etc.)

2. STRUCTURE ANALYSIS
   - Build complete heading hierarchy (H1-H6)
   - Detect layout: single_column, two_column, mixed
   - Identify reading order issues in multi-column layouts
   - Note section numbering patterns

3. PER-PAGE FEATURE DETECTION
   For each page, report:
   - has_images: true/false (informative images, not decorative)
   - image_count: number of images
   - has_tables: true/false
   - table_count: number of tables
   - has_lists: true/false (ordered, unordered, definition)
   - has_code_blocks: true/false
   - has_math: true/false (mathematical notation)
   - layout_type: single_column/two_column/mixed
   - has_headers_footers: true/false
   - complexity_score: 0.0-1.0 (based on content density and structure)
   - complexity_factors: ["dense tables", "nested lists", "complex images", etc.]

4. INITIAL OBSERVATIONS
   Note any clear accessibility issues:
   - Missing or incorrect heading levels
   - Images that appear informative but likely lack descriptions
   - Tables with complex structures (merged cells, nested headers)
   - Typography conveying meaning (color-coded text, bold for emphasis)
   - Reading order problems in multi-column layouts

5. AGENT ROUTING
   Based on content, recommend which specialized agents should run:
   - "figures": Document has images that need description
   - "tables": Document has data tables that need structure markup
   - "structure": Heading hierarchy needs verification or is complex
   - "typography": Visual styling appears to convey semantic meaning

Be thorough but focused on accessibility-relevant details.
"""

ANALYSIS_USER_PROMPT = """Analyze this {total_pages}-page PDF document.

Examine each page image and provide:

1. Document title and type classification

2. Complete heading structure with:
   - Level (1-6)
   - Title text as it appears
   - Page number
   - Section numbering if present

3. Page-by-page feature analysis (for EVERY page)

4. List of required specialized agents based on content

5. Initial accessibility observations for any clear issues you notice

Focus on content that will need accessibility remediation.
"""


__all__ = [
    "AnalysisAgent",
    "AnalysisAgentConfig",
    "AnalysisOutput",
    "AnalysisObservation",
    "AnalysisPageFeatures",
    "HeadingNode",
    "HeadingTree",
]
