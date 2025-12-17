"""Extraction with plain text output and validation-driven correction loop.

This module implements a simplified extraction approach:
1. Plain text output (output_type=str) - No JSON escaping overhead
2. Required page markers (<!-- Page N -->) for validation
3. Pure Python validation after each attempt
4. Correction loop with specific feedback on validation failures
5. Max 2 correction attempts before returning with issues flagged

Cost/Quality improvements over the old approach:
- No Reasoned[T] wrapper overhead
- Haiku focuses on transcription, not JSON formatting
- Heuristic confidence is more reliable than AI-provided values
- Specific correction guidance improves retry success rate
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from opentelemetry import trace
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.dependencies import AgentDependencies
from src.agents.factory import (
    aggregate_usage,
    extract_usage,
    get_tracer,
    load_prompts,
)
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, HeadingTree, PageFeatures
from src.utils.prompt_sanitizer import sanitize_for_prompt

from .models import ExtractionMetrics, ExtractionResult
from .validator import validate_extraction

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MAX_CORRECTION_ATTEMPTS = 2
MODEL_TIER = ModelTier.EFFICIENT

# =============================================================================
# Agent Setup
# =============================================================================

_agent: Agent[AgentDependencies, str] | None = None
_prompts: dict[str, Any] | None = None


def get_agent() -> Agent[AgentDependencies, str]:
    """Get or create the extraction agent with plain text output."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("extraction.yaml")
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        # Plain text output - no structured JSON
        _agent = Agent(
            model,
            deps_type=AgentDependencies,
            output_type=str,  # Plain text markdown output
            system_prompt=_prompts["system_prompt"],
            retries=1,  # We handle retries ourselves with specific feedback
        )
        _register_dynamic_instructions(_agent)
        logger.info(f"Extraction agent initialized with plain text output, model tier {MODEL_TIER.value}")

    return _agent


def reset_agent() -> None:
    """Reset agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


def _register_dynamic_instructions(agent: Agent[AgentDependencies, str]) -> None:
    """Register dynamic instruction generators."""
    from pydantic_ai import RunContext

    @agent.instructions
    def layout_guidance(ctx: RunContext[AgentDependencies]) -> str:
        """Provide layout-specific guidance from manifest."""
        if not ctx.deps.manifest:
            return ""

        two_column_pages = [
            pf.page_num
            for pf in ctx.deps.manifest.page_features
            if pf.layout_type == "two_column"
        ]

        if two_column_pages:
            pages_str = ", ".join(str(p) for p in two_column_pages)
            return f"""
<layout_warning>
CRITICAL: Pages {pages_str} have two-column layout.
For these pages: transcribe LEFT column completely (top to bottom), THEN right column.
</layout_warning>"""
        return ""


# =============================================================================
# Main Extraction Function
# =============================================================================


async def extract_with_validation(
    pages: list[PageData],
    manifest: DocumentManifest,
    job_id: str,
) -> tuple[ExtractionResult, LLMUsage]:
    """Extract markdown with validation-driven correction loop.

    Pipeline:
    1. Initial extraction (plain text output)
    2. Validate with pure Python heuristics
    3. If critical issues found, re-prompt with correction guidance
    4. Max 2 correction attempts before accepting with issues flagged

    Args:
        pages: List of page images from PDF conversion
        manifest: DocumentManifest from analysis phase
        job_id: Job identifier

    Returns:
        Tuple of (ExtractionResult with markdown and metrics, combined LLMUsage)

    Raises:
        ValueError: If no pages provided
    """
    if not pages:
        raise ValueError("No pages provided for extraction")

    tracer = get_tracer()
    usages: list[LLMUsage] = []

    with tracer.start_as_current_span(
        "extraction.extract_with_validation",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", manifest.total_pages)

        logger.info(
            f"Job {job_id}: Starting extraction for {manifest.total_pages} pages, "
            f"document: '{manifest.document_title}'"
        )

        # Create dependencies
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            document_type=manifest.document_type,
        )

        # Build initial prompt
        agent = get_agent()
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("extraction.yaml")

        messages = _build_messages(pages, manifest, _prompts)

        # Track attempts
        attempt = 0
        correction_applied = False
        current_markdown = ""
        current_metrics: ExtractionMetrics | None = None

        while attempt <= MAX_CORRECTION_ATTEMPTS:
            attempt += 1
            span.set_attribute(f"attempt_{attempt}.started", True)

            with tracer.start_as_current_span(f"extraction.attempt_{attempt}"):
                # Run extraction
                try:
                    result = await agent.run(messages, deps=deps)
                    current_markdown = result.output
                    usage = extract_usage(result, MODEL_TIER)
                    usages.append(usage)

                    span.set_attribute(f"attempt_{attempt}.tokens", usage.total_tokens)

                except Exception as e:
                    logger.error(f"Job {job_id}: Extraction attempt {attempt} failed: {e}")
                    span.set_attribute(f"attempt_{attempt}.error", str(e))
                    raise

                # Validate output
                current_metrics = validate_extraction(
                    markdown=current_markdown,
                    manifest=manifest,
                    job_id=job_id,
                )

                span.set_attribute(f"attempt_{attempt}.valid", current_metrics.is_valid)
                span.set_attribute(f"attempt_{attempt}.confidence", current_metrics.confidence)
                span.set_attribute(f"attempt_{attempt}.issues", len(current_metrics.issues))

                if current_metrics.is_valid:
                    logger.info(
                        f"Job {job_id}: Extraction validated on attempt {attempt}, "
                        f"confidence={current_metrics.confidence:.2f}"
                    )
                    break

                # Check if we have more attempts
                if attempt > MAX_CORRECTION_ATTEMPTS:
                    logger.warning(
                        f"Job {job_id}: Extraction has issues after {attempt} attempts, "
                        f"returning with {len(current_metrics.critical_issues)} critical issues"
                    )
                    break

                # Build correction prompt and retry
                correction_guidance = current_metrics.get_correction_guidance()
                logger.info(
                    f"Job {job_id}: Attempt {attempt} had {len(current_metrics.critical_issues)} "
                    f"critical issues, attempting correction"
                )

                # Add correction guidance to messages
                correction_message = _build_correction_prompt(
                    previous_markdown=current_markdown,
                    correction_guidance=correction_guidance,
                    manifest=manifest,
                )
                messages = _build_messages(pages, manifest, _prompts) + [correction_message]
                correction_applied = True

        # Build final result
        assert current_metrics is not None  # Should always be set after loop

        total_usage = aggregate_usage(usages)

        result = ExtractionResult(
            markdown=current_markdown,
            metrics=current_metrics,
            attempt_count=attempt,
            correction_applied=correction_applied,
        )

        # Record final metrics
        span.set_attribute("final.valid", current_metrics.is_valid)
        span.set_attribute("final.confidence", current_metrics.confidence)
        span.set_attribute("final.attempts", attempt)
        span.set_attribute("final.tokens_total", total_usage.total_tokens)
        span.set_attribute("final.cost_cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Extraction complete - "
            f"valid={current_metrics.is_valid}, "
            f"confidence={current_metrics.confidence:.2f}, "
            f"attempts={attempt}, "
            f"cost=${total_usage.estimated_cost_cents/100:.4f}"
        )

        return result, total_usage


# =============================================================================
# Message Building
# =============================================================================


def _build_messages(
    pages: list[PageData],
    manifest: DocumentManifest,
    prompts: dict[str, Any],
) -> list[str | BinaryContent]:
    """Build the message list with page images and extraction prompt."""
    messages: list[str | BinaryContent] = []

    # Add page images
    for page in pages:
        messages.append(f"[Page {page.page_num}]")
        if page.image_base64:
            image_bytes = base64.b64decode(page.image_base64)
            messages.append(
                BinaryContent(data=image_bytes, media_type="image/png")
            )

    # Parse heading tree
    heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
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
        heading_tree=_format_heading_tree(heading_tree),
        page_features=_format_page_features(manifest.page_features),
        layout_notes=sanitize_for_prompt(
            manifest.analysis_notes or "No additional notes.",
            max_length=500,
            context="layout_notes",
        ),
    )
    messages.append(user_prompt)

    return messages


def _build_correction_prompt(
    previous_markdown: str,
    correction_guidance: str,
    manifest: DocumentManifest,
) -> str:
    """Build the correction prompt for retry attempts."""
    # Show a preview of the previous output (first 500 chars)
    preview = previous_markdown[:500]
    if len(previous_markdown) > 500:
        preview += "..."

    return f"""
<correction_required>
Your previous extraction had issues that need to be fixed.

{correction_guidance}

<your_previous_output_preview>
{preview}
</your_previous_output_preview>

Please provide a CORRECTED extraction that addresses ALL the issues listed above.

CRITICAL REQUIREMENTS:
1. Include <!-- Page N --> markers for ALL {manifest.total_pages} pages
2. Follow the heading structure exactly as specified
3. Include image placeholders in format: ![TODO: describe](image-page-X-N.png)

Begin your corrected markdown now:
</correction_required>
"""


def _format_heading_tree(tree: HeadingTree) -> str:
    """Format heading tree as readable text."""
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
    """Format page features for the prompt."""
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


__all__ = [
    "extract_with_validation",
    "get_agent",
    "reset_agent",
    "MAX_CORRECTION_ATTEMPTS",
]
