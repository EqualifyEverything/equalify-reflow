"""Analysis Agent for document accessibility analysis.

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
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts
from src.agents.model_tiers import ModelTier
from src.config import settings
from src.services.debug_logging_service import debug_logger
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.reasoned import Reasoned, ReasonedOutputMixin
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)

# Type aliases for Reasoned[T] wrappers
LayoutType = Literal["single_column", "two_column", "mixed"]
DocumentType = Literal["syllabus", "lecture_notes", "exam", "handout", "research_paper", "other"]

logger = logging.getLogger(__name__)

# All possible specialized agents
ALL_AGENTS = {"figures", "tables", "structure", "typography"}

# Module-level state for lazy initialization
_agent: Agent[None, AnalysisOutput] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.REASONING


# =============================================================================
# Output Models for Structured LLM Response
# =============================================================================


class AnalysisPageFeatures(BaseModel, ReasonedOutputMixin):
    """Page features detected during analysis with glass-box reasoning.

    Complex determinations (layout_type, complexity_score) use Reasoned[T]
    wrapper to capture chain-of-thought reasoning before the value.
    """

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
    layout_type: Reasoned[LayoutType] = Field(
        ...,
        description=(
            "Layout classification with reasoning. "
            "Cite visual evidence: gutter presence, text block width, column structure. "
            "Example: 'Vertical gutter at ~50% width, parallel text blocks. Two-column.'"
        ),
    )
    has_headers_footers: bool = Field(
        default=False,
        description=(
            "True if page has repeating header/footer content (page numbers, "
            "document title, chapter headings, logos that appear on every page)."
        ),
    )
    complexity_score: Reasoned[float] = Field(
        ...,
        description=(
            "Page complexity (0.0-1.0) with reasoning. "
            "Cite factors: table density, nesting depth, column count, image density. "
            "Example: 'Nested 3-level table with merged cells, dense figures. 0.85.'"
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

    @field_validator("complexity_score")
    @classmethod
    def validate_complexity_range(cls, v: Reasoned[float]) -> Reasoned[float]:
        """Validate complexity_score value is in valid range."""
        if not 0.0 <= v.value <= 1.0:
            raise ValueError(f"complexity_score must be between 0.0 and 1.0, got {v.value}")
        return v


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


class AnalysisOutput(BaseModel, ReasonedOutputMixin):
    """Structured output from analysis phase with glass-box reasoning.

    Complex determination (document_type) uses Reasoned[T] wrapper to capture
    chain-of-thought reasoning before the value. Verification fields capture
    validation of heading order and agent routing decisions.
    """

    # Document metadata
    document_title: str = Field(default="Untitled")
    document_type: Reasoned[DocumentType] = Field(
        ...,
        description=(
            "Document classification with reasoning. "
            "Cite visual evidence: title patterns, structure, content type. "
            "Example: 'Course code CS101 in header, weekly schedule grid, grading section. Syllabus.'"
        ),
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
    agent_routing_reasoning: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description=(
            "Reasoning for agent routing decisions. "
            "State which agents included/excluded and why. "
            "Example: 'figures: 3 informative images on p2-3. tables: none detected. "
            "structure: two-column layout requires reading order verification.'"
        ),
    )

    # Heading order verification
    heading_order_verification: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description=(
            "Verification that heading tree respects reading order. "
            "For two-column pages: confirm reading down left column then right. "
            "Check: every subsection (5.1, 5.2) appears AFTER its parent (5). "
            "State any corrections made or confirm order is valid."
        ),
    )

    # Initial observations
    observations: list[AnalysisObservation] = Field(default_factory=list)

    # Confidence
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    notes: str = Field(default="", description="Notes about analysis decisions")


# =============================================================================
# Module-level Functions
# =============================================================================


def get_agent() -> Agent[None, AnalysisOutput]:
    """Get or create the analysis agent (lazy initialization)."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("analysis.yaml")
        _agent = create_agent(
            "analysis.yaml",
            AnalysisOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,  # Analysis agent doesn't use dynamic instructions
            max_retries=2,
        )
        logger.info(f"AnalysisAgent initialized with model tier {_MODEL_TIER.value}")

    return _agent


def reset_agent() -> None:
    """Reset the agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


async def analyze(
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

    # Get prompts and agent
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("analysis.yaml")

    agent = get_agent()

    # Build messages with all page images
    messages = _build_image_messages(pages)

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
    user_prompt = _prompts["user_prompt"].format(total_pages=total_pages)
    messages.append(user_prompt)

    # Debug log: prompt being sent
    image_info = None
    if settings.debug_log_images:
        image_info = {
            "page_count": len(pages),
            "pages_with_images": sum(1 for p in pages if p.image_base64),
        }

    from src.agents.model_tiers import MODEL_TIER_MAP
    debug_logger.log_prompt(
        job_id=job_id,
        agent_name="analysis_agent",
        system_prompt=_prompts.get("system_prompt") if _prompts else None,
        user_message=user_prompt,
        image_info=image_info,
        model_id=MODEL_TIER_MAP[_MODEL_TIER],
        model_tier=_MODEL_TIER.value,
        temperature=0.3,
        max_tokens=4096,
    )

    # Run agent
    start_time = time.time()
    result = await agent.run(
        messages,
        model_settings={
            "max_tokens": 4096,
            "temperature": 0.3,
        },
    )
    duration_ms = (time.time() - start_time) * 1000

    # Extract usage with model_tier parameter
    usage = extract_usage(result, _MODEL_TIER)

    # Debug log: response received
    debug_logger.log_response(
        job_id=job_id,
        agent_name="analysis_agent",
        response_text=None,  # Structured output, not raw text
        parsed_output=result.data,  # type: ignore[attr-defined]
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_cents=usage.estimated_cost_cents,
        duration_ms=duration_ms,
        model_id=MODEL_TIER_MAP[_MODEL_TIER],
    )

    # Convert output to DocumentManifest
    output = result.data  # type: ignore[attr-defined]
    manifest = _create_manifest(output, job_id, total_pages)

    # Convert observations to full Observation model
    observations = _convert_observations(output.observations, job_id)

    # Log reasoning corpus for analysis and debugging
    await _log_reasoning_corpus(output, job_id)

    logger.info(
        f"Analysis complete for job {job_id}: "
        f"{len(manifest.page_features)} pages analyzed, "
        f"{len(manifest.required_agents)} agents needed, "
        f"{len(observations)} initial observations, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return manifest, observations, usage


async def _log_reasoning_corpus(
    output: AnalysisOutput,
    job_id: str,
) -> None:
    """Log reasoning corpus from Reasoned[T] fields for analysis.

    Extracts reasoning from:
    - AnalysisOutput.document_type
    - AnalysisPageFeatures.layout_type (per page)
    - AnalysisPageFeatures.complexity_score (per page)
    """
    try:
        corpus_service = get_reasoning_corpus_service()

        # Extract from page features (layout_type, complexity_score per page)
        for pf in output.page_features:
            corpus = pf.extract_reasoning_corpus()
            if corpus:
                await corpus_service.log_corpus_batch(
                    job_id=job_id,
                    agent_name="analysis",
                    corpus=corpus,
                )

        # Extract from output level (document_type)
        output_corpus = output.extract_reasoning_corpus()
        if output_corpus:
            await corpus_service.log_corpus_batch(
                job_id=job_id,
                agent_name="analysis",
                corpus=output_corpus,
            )

        logger.debug(
            f"Logged reasoning corpus for job {job_id}: "
            f"{len(output.page_features)} page analyses, 1 document analysis"
        )
    except Exception as e:
        # Don't fail analysis if corpus logging fails
        logger.warning(f"Failed to log reasoning corpus for job {job_id}: {e}")


def _build_image_messages(
    pages: list[PageData],
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
    output: AnalysisOutput,
    job_id: str,
    total_pages: int,
) -> DocumentManifest:
    """Convert analysis output to DocumentManifest.

    Extracts .value from Reasoned[T] fields for downstream compatibility.
    """
    # Convert AnalysisPageFeatures to PageFeatures
    # Extract .value and .reasoning from Reasoned[T] fields
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
            layout_type=pf.layout_type.value,  # Extract value from Reasoned[T]
            layout_type_reasoning=pf.layout_type.reasoning,  # Extract reasoning
            has_headers_footers=pf.has_headers_footers,
            complexity_score=pf.complexity_score.value,  # Extract value from Reasoned[T]
            complexity_score_reasoning=pf.complexity_score.reasoning,  # Extract reasoning
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
    skip_agents = _determine_skip_agents(output.required_agents)

    # Get model_id from the agent
    agent = get_agent()
    model_id = agent.model.model_id if hasattr(agent.model, "model_id") else "unknown"  # type: ignore[union-attr]

    return DocumentManifest(
        job_id=job_id,
        document_title=output.document_title,
        document_type=output.document_type.value,  # Extract value from Reasoned[T]
        document_type_reasoning=output.document_type.reasoning,  # Extract reasoning
        total_pages=total_pages,
        heading_tree_json=output.heading_tree.model_dump_json(),
        page_features=page_features,
        required_agents=output.required_agents,
        skip_agents=skip_agents,
        analysis_confidence=output.confidence,
        analysis_notes=output.notes,
        heading_order_verification=output.heading_order_verification,
        agent_routing_reasoning=output.agent_routing_reasoning,
        analysis_model=model_id,
    )


def _convert_observations(
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


def _determine_skip_agents(required: list[str]) -> list[str]:
    """Determine which agents can be skipped."""
    return list(ALL_AGENTS - set(required))


# =============================================================================
# Wrapper Class for Backward Compatibility
# =============================================================================


class AnalysisAgent:
    """Wrapper class for backward compatibility.

    This thin wrapper allows the module to be used with existing code
    that expects a class with an analyze() method.

    Example:
        >>> agent = AnalysisAgent()
        >>> manifest, observations, usage = await agent.analyze(pages, job_id)
        >>> print(f"Found {len(manifest.required_agents)} agents needed")
    """

    ALL_AGENTS = ALL_AGENTS  # Class-level constant for compatibility

    def __init__(self, config: Any | None = None) -> None:
        """Initialize the agent wrapper.

        Args:
            config: Optional AgentConfig. If provided, validates prompts_file exists.

        Raises:
            FileNotFoundError: If config.prompts_file does not exist.
        """
        from src.agents.core import AgentConfig

        if config is not None:
            # Validate prompts file exists (fail fast)
            try:
                load_prompts(str(config.prompts_file))
            except FileNotFoundError:
                # Re-raise to maintain expected behavior
                raise
            self._config = config
        else:
            self._config = AgentConfig(
                name="analysis",
                prompts_file=Path("analysis.yaml"),
                output_type=AnalysisOutput,
                max_retries=2,
                temperature=0.3,
                max_tokens=4096,
                model_tier=_MODEL_TIER,
            )

    @property
    def config(self) -> Any:
        """Return config for backward compatibility."""
        return self._config

    @property
    def model_tier(self) -> ModelTier:
        """Return model tier for backward compatibility."""
        return _MODEL_TIER

    @property
    def model_id(self) -> str:
        """Return model ID for backward compatibility."""
        agent = get_agent()
        return agent.model.model_id if hasattr(agent.model, "model_id") else "unknown"  # type: ignore[union-attr]

    @property
    def prompts(self) -> dict[str, Any]:
        """Return prompts for backward compatibility."""
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("analysis.yaml")
        return _prompts

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
        return await analyze(pages, job_id)

    # Expose internal methods for backward compatibility with tests
    def _determine_skip_agents(self, required: list[str]) -> list[str]:
        """Determine which agents can be skipped."""
        return _determine_skip_agents(required)

    def _build_image_messages(self, pages: list[PageData]) -> list[str | BinaryContent]:
        """Build message list with all page images."""
        return _build_image_messages(pages)

    def _create_manifest(
        self, output: AnalysisOutput, job_id: str, total_pages: int
    ) -> DocumentManifest:
        """Convert analysis output to DocumentManifest."""
        return _create_manifest(output, job_id, total_pages)

    def _convert_observations(
        self, analysis_observations: list[AnalysisObservation], job_id: str
    ) -> list[Observation]:
        """Convert analysis observations to full Observation model."""
        return _convert_observations(analysis_observations, job_id)

    def _get_agent(self) -> Agent[None, AnalysisOutput]:
        """Get the underlying agent (for test mocking)."""
        return get_agent()


__all__ = [
    "AnalysisAgent",
    "AnalysisOutput",
    "AnalysisObservation",
    "AnalysisPageFeatures",
    "HeadingNode",
    "HeadingTree",
    "analyze",
    "get_agent",
    "reset_agent",
]
