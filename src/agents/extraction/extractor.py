"""Page-by-page parallel extraction with validation-driven correction loop.

This module implements page-by-page extraction for improved accuracy:
1. Each page extracted independently with parallel processing (semaphore-controlled)
2. Continuation markers for cross-page sentence handling (no hallucination)
3. Per-page validation with retry on failure
4. Deterministic stitching of pages in post-processing
5. Scales to any document size (no page limit)

Key improvements over single-call extraction:
- Focused attention on single page prevents context overload
- Two-column reading order handled per-page with explicit guidance
- Parallel processing maintains reasonable latency
- Independent retry per page doesn't waste work on successful pages
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import defaultdict
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
from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, HeadingNode, HeadingTree

from .models import (
    CONTINUATION_MARKER_FROM,
    CONTINUATION_MARKER_TO,
    ExtractionMetrics,
    ExtractionResult,
    PageContext,
    PageExtractionResult,
    PageMetrics,
    ValidationIssue,
)
from .validator import validate_extraction, validate_page_extraction

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MODEL_TIER = ModelTier.EFFICIENT

# =============================================================================
# Custom Exception
# =============================================================================


class ExtractionError(Exception):
    """Raised when extraction fails after all retry attempts."""

    def __init__(
        self,
        message: str,
        page_num: int | None = None,
        issues: list[ValidationIssue] | None = None,
    ):
        self.page_num = page_num
        self.issues = issues or []
        super().__init__(message)

    def __str__(self) -> str:
        base = super().__str__()
        if self.issues:
            issue_details = "\n".join(f"  - {i.message}" for i in self.issues)
            return f"{base}\nValidation issues:\n{issue_details}"
        return base


# =============================================================================
# Agent Setup (Page-by-Page)
# =============================================================================

_page_agent: Agent[AgentDependencies, str] | None = None
_prompts: dict[str, Any] | None = None


def get_page_agent() -> Agent[AgentDependencies, str]:
    """Get or create the page extraction agent."""
    global _page_agent, _prompts

    if _page_agent is None:
        _prompts = load_prompts("extraction.yaml")
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        # Plain text output - no structured JSON
        _page_agent = Agent(
            model,
            deps_type=AgentDependencies,
            output_type=str,  # Plain text markdown output
            system_prompt=_prompts["page_system_prompt"],
            retries=1,  # We handle retries ourselves with specific feedback
        )
        logger.info(
            f"Page extraction agent initialized with plain text output, "
            f"model tier {MODEL_TIER.value}, "
            f"concurrency={settings.extraction_concurrency}"
        )

    return _page_agent


def reset_agent() -> None:
    """Reset agent singleton for testing."""
    global _page_agent, _prompts
    _page_agent = None
    _prompts = None


# Backward compatibility alias
def get_agent() -> Agent[AgentDependencies, str]:
    """Alias for get_page_agent for backward compatibility."""
    return get_page_agent()


# =============================================================================
# Main Extraction Function
# =============================================================================


async def extract_with_validation(
    pages: list[PageData],
    manifest: DocumentManifest,
    job_id: str,
) -> tuple[ExtractionResult, LLMUsage]:
    """Extract markdown with page-by-page parallel processing.

    Pipeline:
    1. Build page contexts with headings and layout info
    2. Extract pages in parallel (semaphore-controlled)
    3. Validate each page, retry on failure
    4. Stitch pages together handling continuation markers
    5. Final validation of complete document

    Args:
        pages: List of page images from PDF conversion
        manifest: DocumentManifest from analysis phase
        job_id: Job identifier

    Returns:
        Tuple of (ExtractionResult with markdown and metrics, combined LLMUsage)

    Raises:
        ValueError: If no pages provided
        ExtractionError: If any page fails validation after retries
    """
    if not pages:
        raise ValueError("No pages provided for extraction")

    tracer = get_tracer()

    with tracer.start_as_current_span(
        "extraction.extract_with_validation",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("total_pages", manifest.total_pages)
        span.set_attribute("extraction_mode", "page_by_page")
        span.set_attribute("concurrency", settings.extraction_concurrency)

        logger.info(
            f"Job {job_id}: Starting page-by-page extraction for {manifest.total_pages} pages, "
            f"document: '{manifest.document_title}', concurrency={settings.extraction_concurrency}"
        )

        # 1. Build page contexts
        page_contexts = _build_page_contexts(pages, manifest)

        # 2. Parallel extraction with semaphore
        semaphore = asyncio.Semaphore(settings.extraction_concurrency)

        async def extract_with_semaphore(ctx: PageContext) -> PageExtractionResult:
            async with semaphore:
                return await _extract_single_page(ctx, manifest, job_id)

        # Run all extractions in parallel
        results = await asyncio.gather(
            *[extract_with_semaphore(ctx) for ctx in page_contexts],
            return_exceptions=True,
        )

        # 3. Check for failures
        page_results: list[PageExtractionResult] = []
        for i, raw_result in enumerate(results):
            page_num = i + 1

            if isinstance(raw_result, Exception):
                logger.error(f"Job {job_id}: Page {page_num} extraction raised exception: {raw_result}")
                raise ExtractionError(
                    f"Page {page_num} extraction failed with exception: {raw_result}",
                    page_num=page_num,
                )

            # Type narrowing: after exception check, result is PageExtractionResult
            result: PageExtractionResult = raw_result

            if not result.metrics.is_valid:
                logger.error(
                    f"Job {job_id}: Page {result.page_num} failed validation after {result.attempt_count} attempts"
                )
                raise ExtractionError(
                    f"Page {result.page_num} failed validation after {result.attempt_count} attempts",
                    page_num=result.page_num,
                    issues=result.metrics.issues,
                )

            page_results.append(result)

        # 4. Stitch pages together
        final_markdown = _stitch_pages([r.markdown for r in page_results])

        # 5. Final validation of complete document
        total_usage = aggregate_usage([r.usage for r in page_results])
        final_metrics = validate_extraction(final_markdown, manifest, job_id)

        # Calculate aggregate stats
        max_attempts = max(r.attempt_count for r in page_results)
        any_correction = any(r.attempt_count > 1 for r in page_results)

        final_result = ExtractionResult(
            markdown=final_markdown,
            metrics=final_metrics,
            attempt_count=max_attempts,
            correction_applied=any_correction,
        )

        # Record telemetry
        span.set_attribute("final.valid", final_metrics.is_valid)
        span.set_attribute("final.confidence", final_metrics.confidence)
        span.set_attribute("final.max_page_attempts", max_attempts)
        span.set_attribute("final.tokens_total", total_usage.total_tokens)
        span.set_attribute("final.cost_cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Extraction complete - "
            f"valid={final_metrics.is_valid}, "
            f"confidence={final_metrics.confidence:.2f}, "
            f"max_attempts={max_attempts}, "
            f"cost=${total_usage.estimated_cost_cents / 100:.4f}"
        )

        return final_result, total_usage


# =============================================================================
# Page Context Building
# =============================================================================


def _build_page_contexts(
    pages: list[PageData],
    manifest: DocumentManifest,
) -> list[PageContext]:
    """Build extraction context for each page.

    Args:
        pages: List of page images
        manifest: Document manifest with structure info

    Returns:
        List of PageContext objects, one per page
    """
    heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

    # Group headings by page
    headings_by_page: dict[int, list[HeadingNode]] = defaultdict(list)
    for section in heading_tree.sections:
        headings_by_page[section.page].append(section)

    # Build document summary (medium context level)
    summary = _build_document_summary(manifest, heading_tree)

    # Create context for each page
    contexts: list[PageContext] = []
    for page in pages:
        # Get page features for layout type
        page_features = next(
            (pf for pf in manifest.page_features if pf.page_num == page.page_num),
            None,
        )
        layout_type = page_features.layout_type if page_features else "single_column"

        contexts.append(
            PageContext(
                page_num=page.page_num,
                image_base64=page.image_base64,
                layout_type=layout_type,
                headings_on_page=headings_by_page.get(page.page_num, []),
                document_summary=summary,
                document_title=manifest.document_title,
                document_type=manifest.document_type,
                total_pages=manifest.total_pages,
            )
        )

    return contexts


def _build_document_summary(manifest: DocumentManifest, heading_tree: HeadingTree) -> str:
    """Build medium-level context summary for page extraction.

    Provides enough context to prevent hallucination on key terms
    without overwhelming the model.
    """
    # Key entities from summary
    entities: list[str] = []
    if manifest.summary and manifest.summary.key_entities:
        entities = list(manifest.summary.key_entities[:5])  # Top 5

    # High-level section structure (H1 and H2 only)
    sections = [s.title for s in heading_tree.sections if s.level <= 2][:10]

    lines = [
        f'Document: "{manifest.document_title}"',
        f"Type: {manifest.document_type}",
        f"Total pages: {manifest.total_pages}",
    ]

    if entities:
        lines.append(f"Key entities: {', '.join(entities)}")

    if sections:
        lines.append(f"Main sections: {', '.join(sections)}")

    return "\n".join(lines)


# =============================================================================
# Single Page Extraction
# =============================================================================


async def _extract_single_page(
    ctx: PageContext,
    manifest: DocumentManifest,
    job_id: str,
) -> PageExtractionResult:
    """Extract a single page with retry logic.

    Args:
        ctx: Page context with image and hints
        manifest: Document manifest
        job_id: Job identifier

    Returns:
        PageExtractionResult with markdown and metrics
    """
    tracer = get_tracer()
    agent = get_page_agent()
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("extraction.yaml")

    usages: list[LLMUsage] = []
    max_retries = settings.max_page_retries

    with tracer.start_as_current_span(
        f"extraction.page_{ctx.page_num}",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("page_num", ctx.page_num)
        span.set_attribute("layout_type", ctx.layout_type)
        span.set_attribute("expected_headings", len(ctx.headings_on_page))

        current_markdown = ""
        current_metrics: PageMetrics | None = None
        attempt = 0  # Track which attempt we're on

        for attempt in range(1, max_retries + 1):
            span.set_attribute(f"attempt_{attempt}.started", True)

            # Build prompt (with correction guidance on retry)
            prompt = _build_page_prompt(ctx, _prompts, attempt)
            messages = _build_page_messages(ctx, prompt)

            # Create deps for this extraction
            deps = AgentDependencies(
                job_id=job_id,
                manifest=manifest,
                document_type=manifest.document_type,
            )

            try:
                result = await agent.run(messages, deps=deps)
                current_markdown = result.output
                usage = extract_usage(result, MODEL_TIER)
                usages.append(usage)

                span.set_attribute(f"attempt_{attempt}.tokens", usage.total_tokens)

            except Exception as e:
                logger.error(f"Job {job_id}: Page {ctx.page_num} attempt {attempt} failed: {e}")
                span.set_attribute(f"attempt_{attempt}.error", str(e))
                raise

            # Validate page output
            current_metrics = validate_page_extraction(current_markdown, ctx)

            span.set_attribute(f"attempt_{attempt}.valid", current_metrics.is_valid)
            span.set_attribute(f"attempt_{attempt}.word_count", current_metrics.word_count)
            span.set_attribute(f"attempt_{attempt}.issues", len(current_metrics.issues))

            if current_metrics.is_valid:
                logger.debug(
                    f"Job {job_id}: Page {ctx.page_num} validated on attempt {attempt}, "
                    f"word_count={current_metrics.word_count}"
                )
                break

            # Prepare for retry
            if attempt < max_retries:
                logger.info(
                    f"Job {job_id}: Page {ctx.page_num} attempt {attempt} had "
                    f"{len(current_metrics.critical_issues)} critical issues, retrying"
                )
                # Store issues for next attempt's correction prompt
                ctx.previous_issues = current_metrics.issues
            else:
                logger.warning(f"Job {job_id}: Page {ctx.page_num} failed after {attempt} attempts")

        assert current_metrics is not None

        total_usage = aggregate_usage(usages)

        return PageExtractionResult(
            page_num=ctx.page_num,
            markdown=current_markdown,
            metrics=current_metrics,
            attempt_count=attempt,
            usage=total_usage,
        )


def _build_page_prompt(
    ctx: PageContext,
    prompts: dict[str, Any],
    attempt: int,
) -> str:
    """Build prompt for page extraction.

    On first attempt, uses standard prompt.
    On retry, uses correction prompt with issue details.
    """
    # Format headings for this page
    if ctx.headings_on_page:
        headings_text = "\n".join(
            f"{'#' * h.level} {h.section_number + ' ' if h.section_number else ''}{h.title}"
            for h in ctx.headings_on_page
        )
    else:
        headings_text = "(No headings expected on this page - continues previous section)"

    # Reading order hint based on layout
    reading_order_hints = {
        "single_column": "Read top to bottom",
        "two_column": "Read LEFT column completely (top to bottom), THEN right column completely",
        "mixed": "Main content first, then sidebars",
    }
    reading_order_hint = reading_order_hints.get(ctx.layout_type, "Read top to bottom")

    if attempt > 1 and ctx.previous_issues:
        # Correction prompt
        issues_text = "\n".join(f"- {i.message}" for i in ctx.previous_issues)
        return prompts["page_correction_prompt"].format(
            page_num=ctx.page_num,
            issues=issues_text,
            layout_type=ctx.layout_type,
            reading_order_hint=reading_order_hint,
        )

    # Standard prompt
    return prompts["page_user_prompt"].format(
        page_num=ctx.page_num,
        total_pages=ctx.total_pages,
        document_type=ctx.document_type,
        document_summary=ctx.document_summary,
        layout_type=ctx.layout_type,
        headings_for_page=headings_text,
    )


def _build_page_messages(
    ctx: PageContext,
    prompt: str,
) -> list[str | BinaryContent]:
    """Build message list with single page image and prompt."""
    messages: list[str | BinaryContent] = []

    # Add page image
    if ctx.image_base64:
        image_bytes = base64.b64decode(ctx.image_base64)
        messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

    # Add prompt
    messages.append(prompt)

    return messages


# =============================================================================
# Page Stitching
# =============================================================================


def _stitch_pages(page_markdowns: list[str]) -> str:
    """Stitch page markdowns together, handling continuation markers.

    Continuation markers allow cross-page sentences without hallucination:
    - [CONTINUES_FROM_PREVIOUS] at page start = continues sentence from previous page
    - [CONTINUES_ON_NEXT] at page end = sentence continues to next page

    Args:
        page_markdowns: List of markdown strings, one per page in order

    Returns:
        Combined markdown with markers removed and sentences joined
    """
    if not page_markdowns:
        return ""

    result_parts: list[str] = []

    for i, markdown in enumerate(page_markdowns):
        text = markdown

        # Handle continuation from previous page
        if i > 0 and CONTINUATION_MARKER_FROM in text:
            # Remove the marker
            text = text.replace(CONTINUATION_MARKER_FROM, "")

            # Check if previous page ended with continuation marker
            if result_parts:
                prev = result_parts[-1]
                if CONTINUATION_MARKER_TO in prev:
                    # Remove trailing marker from previous
                    result_parts[-1] = prev.replace(CONTINUATION_MARKER_TO, "").rstrip()
                    # Append continuation directly to previous part (join with space)
                    result_parts[-1] += " " + text.lstrip()
                    continue  # Don't add as separate part

        # Handle standalone continuation-to marker (without matching from marker)
        # Just remove it, the next page will handle its continuation-from
        if CONTINUATION_MARKER_TO in text and i < len(page_markdowns) - 1:
            # Check if next page has matching marker
            next_page = page_markdowns[i + 1]
            if CONTINUATION_MARKER_FROM not in next_page:
                # Next page doesn't expect continuation, just remove marker
                text = text.replace(CONTINUATION_MARKER_TO, "")

        result_parts.append(text)

    # Join with double newlines (standard markdown paragraph separation)
    combined = "\n\n".join(result_parts)

    # Clean up any remaining markers that weren't handled
    combined = combined.replace(CONTINUATION_MARKER_FROM, "")
    combined = combined.replace(CONTINUATION_MARKER_TO, "")

    # Clean up excessive newlines
    while "\n\n\n" in combined:
        combined = combined.replace("\n\n\n", "\n\n")

    return combined.strip()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "extract_with_validation",
    "get_agent",
    "get_page_agent",
    "reset_agent",
    "ExtractionError",
]
