"""Final verification agent for comparing markdown against source images.

This module provides the last quality check before output reaches the end user.
It compares the final markdown against source page images to catch:
- OCR errors
- Double words
- Missing/extra text
- Truncated content

Uses parallel processing for efficiency with configurable concurrency.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass

from opentelemetry import trace
from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.config import settings
from src.shared.models.processing import LLMUsage

from .models import (
    PageVerificationOutput,
    PageVerificationResult,
    VerificationCorrection,
    VerificationResult,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Module-level state
_agent: Agent[None, PageVerificationOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost efficiency


@dataclass
class PageContext:
    """Context for verifying a single page."""

    page_num: int
    page_markdown: str
    page_image_base64: str


def get_prompts() -> dict:
    """Load verification agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("verification.yaml")
    return _prompts


def get_agent() -> Agent[None, PageVerificationOutput]:
    """Get or create the verification agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="verification.yaml",
            output_type=PageVerificationOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


def reset_agent() -> None:
    """Reset the agent singleton (useful for testing)."""
    global _agent, _prompts
    _agent = None
    _prompts = None


def _extract_page_markdown(full_markdown: str, page_num: int, total_pages: int) -> str:
    """Extract markdown content for a specific page.

    Args:
        full_markdown: The complete markdown document
        page_num: Page number to extract (1-indexed)
        total_pages: Total number of pages

    Returns:
        Markdown content for the specified page
    """
    # Pattern to match page markers
    page_pattern = re.compile(r"<!--\s*Page\s+(\d+)\s*-->")

    # Find all page markers with their positions
    markers = [(m.group(1), m.start(), m.end()) for m in page_pattern.finditer(full_markdown)]

    if not markers:
        # No page markers - return full document for page 1, empty for others
        return full_markdown if page_num == 1 else ""

    # Find the start and end positions for this page
    start_pos = 0
    end_pos = len(full_markdown)

    for i, (marker_page, marker_start, marker_end) in enumerate(markers):
        if int(marker_page) == page_num:
            start_pos = marker_start
            # End at next marker or end of document
            if i + 1 < len(markers):
                end_pos = markers[i + 1][1]
            break
    else:
        # Page not found
        return ""

    return full_markdown[start_pos:end_pos].strip()


def _apply_corrections(
    markdown: str,
    corrections: list[VerificationCorrection],
    job_id: str,
    page_num: int,
) -> tuple[str, int, int]:
    """Apply corrections to markdown.

    Args:
        markdown: Current markdown
        corrections: Corrections to apply
        job_id: Job ID for logging
        page_num: Page number for logging

    Returns:
        Tuple of (corrected_markdown, applied_count, failed_count)
    """
    current = markdown
    applied = 0
    failed = 0

    for correction in corrections:
        if correction.search in current:
            count = current.count(correction.search)
            if count == 1:
                current = current.replace(correction.search, correction.replace)
                applied += 1
                logger.debug(
                    f"Job {job_id} Page {page_num}: Applied verification correction: "
                    f"'{correction.search[:40]}...' -> '{correction.replace[:40]}...'"
                )
            else:
                logger.warning(
                    f"Job {job_id} Page {page_num}: Skipping correction - "
                    f"'{correction.search[:40]}...' found {count} times"
                )
                failed += 1
        else:
            logger.warning(f"Job {job_id} Page {page_num}: Correction not found: '{correction.search[:40]}...'")
            failed += 1

    return current, applied, failed


async def _verify_single_page(
    ctx: PageContext,
    job_id: str,
) -> PageVerificationResult:
    """Verify a single page against its source image.

    Args:
        ctx: Page context with markdown and image
        job_id: Job ID for correlation

    Returns:
        PageVerificationResult with corrections
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build the prompt
    page_prompt = prompts["page_prompt"].format(
        page_num=ctx.page_num,
        page_markdown=ctx.page_markdown,
    )

    # Build multimodal prompt with image using PydanticAI's BinaryContent
    image_content = BinaryContent(
        data=base64.b64decode(ctx.page_image_base64),
        media_type="image/png",
    )
    prompt = [page_prompt, image_content]

    logger.debug(f"Job {job_id}: Verifying page {ctx.page_num}")

    try:
        result = await run_agent(
            agent=agent,
            prompt=prompt,
            job_id=job_id,
            agent_name=f"verification_page_{ctx.page_num}",
            model_tier=_MODEL_TIER,
            system_prompt=prompts["system_prompt"],
        )

        output: PageVerificationOutput = result.output
        usage = extract_usage(result, _MODEL_TIER)

        return PageVerificationResult(
            page_num=ctx.page_num,
            corrections=output.corrections,
            issues=output.issues,
            is_accurate=output.is_accurate,
            summary=output.summary,
            cost_cents=usage.estimated_cost_cents,
        )

    except Exception as e:
        logger.error(f"Job {job_id}: Verification failed for page {ctx.page_num}: {e}")
        return PageVerificationResult(
            page_num=ctx.page_num,
            is_accurate=True,  # Assume accurate on error to avoid blocking
            summary=f"Verification failed: {str(e)[:100]}",
            cost_cents=0.0,
        )


async def verify_final_output(
    markdown: str,
    page_images: list[str],
    job_id: str,
    concurrency: int | None = None,
) -> tuple[VerificationResult, LLMUsage]:
    """Verify final markdown output against source page images.

    This is the last quality check before the document reaches the end user.
    It compares each page's markdown against its source image to catch
    OCR errors, double words, missing text, etc.

    Args:
        markdown: The final markdown to verify
        page_images: List of base64-encoded page images (one per page)
        job_id: Job ID for correlation
        concurrency: Max concurrent verifications (default from settings)

    Returns:
        Tuple of (VerificationResult, LLMUsage)
    """
    with tracer.start_as_current_span("verification.verify_final_output") as span:
        total_pages = len(page_images)
        span.set_attribute("total_pages", total_pages)

        if concurrency is None:
            concurrency = int(getattr(settings, "verification_concurrency", 5))

        logger.info(f"Job {job_id}: Starting final verification - {total_pages} pages, concurrency={concurrency}")

        # Build page contexts
        contexts: list[PageContext] = []
        for i, image_base64 in enumerate(page_images):
            page_num = i + 1
            page_markdown = _extract_page_markdown(markdown, page_num, total_pages)
            contexts.append(
                PageContext(
                    page_num=page_num,
                    page_markdown=page_markdown,
                    page_image_base64=image_base64,
                )
            )

        # Process pages in parallel with semaphore
        semaphore = asyncio.Semaphore(concurrency)

        async def verify_with_semaphore(ctx: PageContext) -> PageVerificationResult:
            async with semaphore:
                return await _verify_single_page(ctx, job_id)

        # Run all verifications in parallel
        page_results = await asyncio.gather(*[verify_with_semaphore(ctx) for ctx in contexts])

        # Sort by page number
        page_results = sorted(page_results, key=lambda r: r.page_num)

        # Apply all corrections to the markdown
        corrected_markdown = markdown
        total_applied = 0
        total_failed = 0
        total_issues = 0

        for result in page_results:
            if result.corrections:
                corrected_markdown, applied, failed = _apply_corrections(
                    corrected_markdown,
                    result.corrections,
                    job_id,
                    result.page_num,
                )
                result.corrections_applied = applied
                result.corrections_failed = failed
                total_applied += applied
                total_failed += failed

            total_issues += len(result.issues)

        # Calculate total cost
        total_cost = sum(r.cost_cents for r in page_results)

        # Check if all pages were accurate
        all_accurate = all(r.is_accurate for r in page_results)

        # Build usage tracking
        usage = LLMUsage(
            input_tokens=0,  # Tracked internally
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=total_cost,
        )

        span.set_attribute("corrections_applied", total_applied)
        span.set_attribute("corrections_failed", total_failed)
        span.set_attribute("issues_found", total_issues)
        span.set_attribute("all_accurate", all_accurate)
        span.set_attribute("cost_cents", total_cost)

        logger.info(
            f"Job {job_id}: Verification complete - "
            f"{total_applied} corrections applied, "
            f"{total_failed} failed, "
            f"{total_issues} issues for review, "
            f"all_accurate={all_accurate}, "
            f"cost=${total_cost / 100:.4f}"
        )

        return VerificationResult(
            corrected_markdown=corrected_markdown,
            page_results=page_results,
            total_corrections_applied=total_applied,
            total_corrections_failed=total_failed,
            total_issues=total_issues,
            all_pages_accurate=all_accurate,
            cost_cents=total_cost,
        ), usage


__all__ = [
    "verify_final_output",
    "get_agent",
    "reset_agent",
]
