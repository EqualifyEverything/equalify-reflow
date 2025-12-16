"""Extraction Agent for guided markdown extraction.

This agent performs manifest-guided markdown extraction using Claude Haiku 4.5
(EFFICIENT tier) to transcribe PDF documents to accessible markdown.

The Extraction Agent:
1. Receives DocumentManifest with structure analysis from Analysis phase
2. Receives all page images
3. Transcribes content following the manifest structure
4. Produces markdown with image placeholders for specialized agents

Uses Claude Haiku 4.5 for cost-efficient transcription.
The hard analytical work was done by the Analysis agent (Sonnet).

Supports dynamic instructions via AgentDependencies for:
- Document type hints (syllabus, exam, lecture_notes)
- Retry guidance based on previous failures

NOTE: This agent uses Reasoned[T] wrappers for key determinations:
- confidence: Reasoned[float] - Transcription accuracy assessment
- reading_order_followed: Reasoned[bool] - Layout compliance verification
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent

from src.agents.dependencies import AgentDependencies
from src.agents.factory import create_agent, extract_usage, load_prompts
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.processing import LLMUsage
from src.shared.models.reasoned import Reasoned, ReasonedOutputMixin
from src.shared.models.remediation import DocumentManifest, HeadingTree, PageFeatures
from src.utils.prompt_sanitizer import sanitize_for_prompt

logger = logging.getLogger(__name__)


# =============================================================================
# Output Models
# =============================================================================


class ExtractionOutput(BaseModel, ReasonedOutputMixin):
    """Structured output from extraction phase with glass-box reasoning.

    Complex determinations (confidence, reading_order_followed) use Reasoned[T]
    wrapper to capture chain-of-thought reasoning before the value.

    Attributes:
        markdown: Complete markdown transcription of the document
        confidence: Transcription accuracy confidence with reasoning
        reading_order_followed: Whether layout-based reading order was followed
        pages_transcribed: List of page numbers successfully transcribed
        transcription_notes: Notes about issues, decisions, or [?] markers
    """

    markdown: str = Field(
        ..., description="Complete markdown transcription of the document"
    )
    confidence: Reasoned[float] = Field(
        ...,
        description=(
            "Confidence in transcription accuracy (0.0-1.0) with reasoning. "
            "Cite factors: image quality, unclear text count, layout complexity. "
            "Example: 'All 5 pages clear, 2 words marked [?], tables intact. 0.92.'"
        ),
    )
    reading_order_followed: Reasoned[bool] = Field(
        ...,
        description=(
            "Whether layout-based reading order was correctly followed, with reasoning. "
            "Cite which pages had which layout type and how you handled them. "
            "Example: 'Pages 1-2 single-column (top-to-bottom), pages 3-5 two-column "
            "(left then right). True.'"
        ),
    )
    pages_transcribed: list[int] = Field(
        ...,
        description=(
            "List of page numbers successfully transcribed. "
            "Should include all pages from 1 to total_pages unless page was blank/unreadable."
        ),
    )
    transcription_notes: str = Field(
        default="",
        description=(
            "Notes about transcription decisions, issues encountered, or [?] markers used. "
            "Include: unclear text locations, complex table handling, layout ambiguities."
        ),
    )

    @field_validator("confidence")
    @classmethod
    def validate_confidence_range(cls, v: Reasoned[float]) -> Reasoned[float]:
        """Validate confidence value is in valid range."""
        if not 0.0 <= v.value <= 1.0:
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v.value}")
        return v


# =============================================================================
# Module-level state for lazy initialization
# =============================================================================

# Document type hints for dynamic instructions
DOCUMENT_TYPE_HINTS: dict[str, str] = {
    "syllabus": (
        "This is a course syllabus. Pay attention to:\n"
        "- Course schedule tables (preserve structure carefully)\n"
        "- Grading policy sections (often contain nested lists)\n"
        "- Assignment due dates (accuracy critical)\n"
    ),
    "exam": (
        "This is an exam document. Pay attention to:\n"
        "- Question numbering (must be exact)\n"
        "- Point values (preserve in brackets)\n"
        "- Answer spaces (preserve blanks, do not fill in)\n"
    ),
    "lecture_notes": (
        "These are lecture notes. Pay attention to:\n"
        "- Slide numbers/titles (often serve as headings)\n"
        "- Bullet point hierarchies (preserve nesting)\n"
        "- Diagrams and figures (use placeholders)\n"
    ),
    "assignment": (
        "This is an assignment. Pay attention to:\n"
        "- Question/problem numbering (must be exact)\n"
        "- Point values and rubric info (preserve carefully)\n"
        "- Submission requirements (often in lists or tables)\n"
    ),
    "research_paper": (
        "This is a research paper. Pay attention to:\n"
        "- Two-column layout (common in academic papers)\n"
        "- Citations and references (preserve formatting)\n"
        "- Figures and tables (often have captions)\n"
        "- Abstract (usually single-column before two-column body)\n"
    ),
}

_agent: Agent[AgentDependencies, ExtractionOutput] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.EFFICIENT


# =============================================================================
# Agent Factory and Registration
# =============================================================================


def get_agent() -> Agent[AgentDependencies, ExtractionOutput]:
    """Get or create the extraction agent (lazy initialization)."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("extraction.yaml")
        _agent = create_agent(
            "extraction.yaml",
            ExtractionOutput,
            model_tier=_MODEL_TIER,
            use_deps=True,
            max_retries=2,
        )
        _register_dynamic_instructions(_agent)
        logger.info(f"ExtractionAgent initialized with model tier {_MODEL_TIER.value}")

    return _agent


def reset_agent() -> None:
    """Reset the agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


def _register_dynamic_instructions(
    agent: Agent[AgentDependencies, ExtractionOutput]
) -> None:
    """Register dynamic instruction generators on the agent.

    These instructions are evaluated at runtime and can access
    AgentDependencies via ctx.deps to provide context-aware guidance.
    """

    @agent.instructions
    def document_type_guidance(ctx: RunContext[AgentDependencies]) -> str:
        """Provide document-type-specific extraction tips."""
        doc_type = ctx.deps.document_type
        if doc_type and doc_type in DOCUMENT_TYPE_HINTS:
            hint = DOCUMENT_TYPE_HINTS[doc_type]
            return f"""
<document_type_guidance type="{doc_type.upper()}">
{hint}
</document_type_guidance>"""
        return ""

    @agent.instructions
    def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
        """Provide guidance for retry attempts."""
        if not ctx.deps.is_retry:
            return ""

        # Show last 3 failures
        failures = "\n".join(
            f"- {f}" for f in ctx.deps.previous_failures[-3:]
        )
        return f"""
<retry_attempt number="{ctx.deps.attempt_number}">
Previous attempts encountered issues:
{failures}

Adjust your approach to avoid these issues.
</retry_attempt>"""

    @agent.instructions
    def layout_guidance(ctx: RunContext[AgentDependencies]) -> str:
        """Provide layout-specific guidance from manifest."""
        if not ctx.deps.manifest:
            return ""

        # Check for two-column pages
        two_column_pages = []
        for pf in ctx.deps.manifest.page_features:
            if pf.layout_type == "two_column":
                two_column_pages.append(pf.page_num)

        if two_column_pages:
            pages_str = ", ".join(str(p) for p in two_column_pages)
            return f"""
<layout_warning>
CRITICAL: Pages {pages_str} have two-column layout.
For these pages: transcribe LEFT column completely (top to bottom), THEN right column completely.
Verify heading order matches this reading pattern.
</layout_warning>"""
        return ""

    logger.debug("ExtractionAgent: Dynamic instructions registered")


# =============================================================================
# Main Extraction Function
# =============================================================================


async def extract(
    pages: list[PageData],
    manifest: DocumentManifest,
    job_id: str,
    deps: AgentDependencies | None = None,
) -> tuple[ExtractionOutput, LLMUsage]:
    """Extract markdown guided by manifest.

    Args:
        pages: List of page images from PDF conversion
        manifest: DocumentManifest from analysis phase
        job_id: Job identifier
        deps: Optional AgentDependencies for dynamic instructions.
              If not provided, creates default deps from manifest.

    Returns:
        Tuple of (ExtractionOutput, LLMUsage)
        ExtractionOutput contains:
        - markdown: Complete transcription
        - confidence: Reasoned[float] with accuracy assessment
        - reading_order_followed: Reasoned[bool] with layout compliance
        - pages_transcribed: List of page numbers transcribed
        - transcription_notes: Issues and decisions

    Raises:
        ValueError: If no pages provided
        RuntimeError: If extraction fails after retries
    """
    if not pages:
        raise ValueError("No pages provided for extraction")

    logger.info(
        f"Starting extraction for job {job_id}: "
        f"{len(pages)} pages, manifest title='{manifest.document_title}'"
    )

    # Create or use provided deps
    if deps is None:
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            document_type=manifest.document_type,
        )

    # Ensure agent is created (triggers instruction registration)
    agent = get_agent()

    # Get prompts
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("extraction.yaml")

    # Parse heading tree from manifest
    heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

    # Build messages with all page images
    messages = _build_image_messages(pages)

    # Format heading tree for prompt
    heading_tree_text = _format_heading_tree(heading_tree)

    # Format page features for prompt
    page_features_text = _format_page_features(manifest.page_features)

    # Build user prompt with manifest context
    # SECURITY: Sanitize user-influenced fields to prevent prompt injection
    # document_title and document_type come from PDF metadata (attacker-controlled)
    # layout_notes may contain extracted text content
    user_prompt = _prompts["user_prompt"].format(
        total_pages=manifest.total_pages,
        document_title=sanitize_for_prompt(
            manifest.document_title,
            max_length=200,
            context="document_title",
        ),
        document_type=sanitize_for_prompt(
            manifest.document_type,
            max_length=50,
            context="document_type",
        ),
        heading_tree=heading_tree_text,
        page_features=page_features_text,
        layout_notes=sanitize_for_prompt(
            manifest.analysis_notes or "No additional notes.",
            max_length=500,
            context="layout_notes",
        ),
    )
    messages.append(user_prompt)

    # Run agent with deps for dynamic instructions
    result = await agent.run(
        messages,
        deps=deps,
    )

    # Extract usage with model tier
    usage = extract_usage(result, _MODEL_TIER)

    output = result.data  # type: ignore[attr-defined]

    # Log reasoning corpus for analysis and debugging
    await _log_reasoning_corpus(output, job_id)

    logger.info(
        f"Extraction complete for job {job_id}: "
        f"{len(output.markdown)} chars, "
        f"confidence: {output.confidence.value:.2f}, "
        f"reading_order: {output.reading_order_followed.value}, "
        f"pages: {len(output.pages_transcribed)}, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


# =============================================================================
# Helper Functions
# =============================================================================


async def _log_reasoning_corpus(
    output: ExtractionOutput,
    job_id: str,
) -> None:
    """Log reasoning corpus from Reasoned[T] fields for analysis.

    Extracts reasoning from:
    - ExtractionOutput.confidence
    - ExtractionOutput.reading_order_followed
    """
    try:
        corpus_service = get_reasoning_corpus_service()

        # Extract reasoning corpus from output
        corpus = output.extract_reasoning_corpus()
        if corpus:
            await corpus_service.log_corpus_batch(
                job_id=job_id,
                agent_name="extraction",
                corpus=corpus,
            )

        logger.debug(
            f"Logged reasoning corpus for job {job_id}: "
            f"{len(corpus)} reasoning entries from extraction"
        )
    except Exception as e:
        # Don't fail extraction if corpus logging fails
        logger.warning(f"Failed to log reasoning corpus for job {job_id}: {e}")


def _build_image_messages(
    pages: list[PageData]
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


def _format_heading_tree(tree: HeadingTree) -> str:
    """Format heading tree as readable text for the extraction prompt."""
    lines = [
        f"Document Title: {tree.document_title}",
        f"Layout: {tree.layout_type}",
        f"Total Pages: {tree.total_pages}",
        "",
        "Heading Structure (follow this exactly):",
    ]

    for section in tree.sections:
        indent = "  " * (section.level - 1)
        number = f"{section.section_number} " if section.section_number else ""
        lines.append(
            f"{indent}{'#' * section.level} {number}{section.title} (page {section.page})"
        )

    return "\n".join(lines)


def _format_page_features(features: list[PageFeatures]) -> str:
    """Format page features for the extraction prompt."""
    lines = ["Page-by-Page Content:"]

    for pf in features:
        parts = [f"Page {pf.page_num}:"]

        if pf.has_images:
            parts.append(f"{pf.image_count} image(s)")
        if pf.has_tables:
            parts.append(f"{pf.table_count} table(s)")
        if pf.has_lists:
            parts.append("lists")
        if pf.has_code_blocks:
            parts.append("code")
        if pf.has_math:
            parts.append("math")

        parts.append(f"[{pf.layout_type}]")

        lines.append("  " + ", ".join(parts))

    return "\n".join(lines)


# =============================================================================
# Wrapper Class for Backward Compatibility
# =============================================================================


class ExtractionAgent:
    """Wrapper class for backward compatibility.

    This thin wrapper allows the module to be used with existing code
    that expects a class with an extract() method.
    """

    # Expose constants for tests
    DOCUMENT_TYPE_HINTS = DOCUMENT_TYPE_HINTS

    def __init__(self, config: Any | None = None) -> None:
        """Initialize the agent with optional config.

        Args:
            config: Optional AgentConfig for customization (used in tests)
        """
        from pathlib import Path

        from src.agents.base_agent import AgentConfig

        # Store custom config or create default
        if config is not None:
            self._config = config
            # Try to load prompts with the custom config to validate it exists
            # This will raise FileNotFoundError if file doesn't exist (and not mocked)
            try:
                global _prompts
                _prompts = load_prompts(str(config.prompts_file))
            except FileNotFoundError:
                # Re-raise to maintain expected behavior
                raise
        else:
            self._config = AgentConfig(
                name="extraction",
                prompts_file=Path("extraction.yaml"),
                output_type=ExtractionOutput,
                max_retries=2,
                temperature=0.2,
                max_tokens=16384,
            )

    @property
    def model_tier(self) -> ModelTier:
        """Return the model tier for backward compatibility."""
        return _MODEL_TIER

    @property
    def model_id(self) -> str:
        """Return the model ID for backward compatibility."""
        from src.agents.model_tiers import MODEL_TIER_MAP
        return MODEL_TIER_MAP[_MODEL_TIER]

    @property
    def config(self) -> Any:
        """Return config for backward compatibility."""
        return self._config

    @property
    def prompts(self) -> dict[str, Any]:
        """Return prompts for backward compatibility."""
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("extraction.yaml")
        return _prompts

    def _get_agent(self) -> Agent[AgentDependencies, ExtractionOutput]:
        """Get the agent instance (delegates to module function)."""
        return get_agent()

    def _format_page_features(self, features: list[PageFeatures]) -> str:
        """Format page features (delegates to module function)."""
        return _format_page_features(features)

    def _format_heading_tree(self, tree: HeadingTree) -> str:
        """Format heading tree (delegates to module function)."""
        return _format_heading_tree(tree)

    def _build_image_messages(self, pages: list[PageData]) -> list[str | BinaryContent]:
        """Build image messages (delegates to module function)."""
        return _build_image_messages(pages)

    async def _log_reasoning_corpus(
        self,
        output: ExtractionOutput,
        job_id: str,
    ) -> None:
        """Log reasoning corpus (delegates to module function)."""
        return await _log_reasoning_corpus(output, job_id)

    async def _run_with_deps(
        self,
        messages: list[str | BinaryContent],
        deps: AgentDependencies,
    ) -> tuple[ExtractionOutput, LLMUsage]:
        """Run agent with dependencies (for test compatibility)."""
        agent = self._get_agent()
        result = await agent.run(messages, deps=deps)
        usage = extract_usage(result, _MODEL_TIER)
        return result.data, usage  # type: ignore[attr-defined]

    async def extract(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[ExtractionOutput, LLMUsage]:
        """Extract markdown guided by manifest (wrapper delegates to module function).

        This method uses self._get_agent() to allow test mocking while delegating
        the actual work to the module-level extract function.
        """
        if not pages:
            raise ValueError("No pages provided for extraction")

        # Create or use provided deps
        if deps is None:
            deps = AgentDependencies(
                job_id=job_id,
                manifest=manifest,
                document_type=manifest.document_type,
            )

        # Ensure agent is created (using self._get_agent for test mocking)
        agent = self._get_agent()

        # Get prompts
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("extraction.yaml")

        # Parse heading tree from manifest
        heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

        # Build messages with all page images
        messages = _build_image_messages(pages)

        # Format heading tree for prompt
        heading_tree_text = _format_heading_tree(heading_tree)

        # Format page features for prompt
        page_features_text = _format_page_features(manifest.page_features)

        # Build user prompt with manifest context
        # SECURITY: Sanitize user-influenced fields to prevent prompt injection
        from src.utils.prompt_sanitizer import sanitize_for_prompt

        user_prompt = _prompts["user_prompt"].format(
            total_pages=manifest.total_pages,
            document_title=sanitize_for_prompt(
                manifest.document_title,
                max_length=200,
                context="document_title",
            ),
            document_type=sanitize_for_prompt(
                manifest.document_type,
                max_length=50,
                context="document_type",
            ),
            heading_tree=heading_tree_text,
            page_features=page_features_text,
            layout_notes=sanitize_for_prompt(
                manifest.analysis_notes or "No additional notes.",
                max_length=500,
                context="layout_notes",
            ),
        )
        messages.append(user_prompt)

        # Run agent with deps for dynamic instructions
        result = await agent.run(messages, deps=deps)

        # Extract usage with model tier
        usage = extract_usage(result, _MODEL_TIER)

        # Support both .output (old) and .data (new) for backward compatibility
        output = getattr(result, "output", None) or result.data  # type: ignore[attr-defined]

        # Log reasoning corpus for analysis and debugging
        await _log_reasoning_corpus(output, job_id)

        return output, usage


__all__ = [
    "ExtractionAgent",
    "ExtractionOutput",
    "extract",
    "get_agent",
    "reset_agent",
]
