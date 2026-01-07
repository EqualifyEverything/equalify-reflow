"""V5 Pipeline Context Gatherer - Optimized Two-Phase Approach.

This module implements the optimized context gathering flow:

Phase 1A: Deterministic Extraction (Parallel, No LLM)
    - Extract all headings from all pages via regex
    - Count figures and tables per page
    - Fast, parallelizable, no LLM cost

Phase 1B: Structure Inference (Single LLM Call)
    - Send ALL extracted headings to one LLM call
    - LLM sees full document structure and infers correct levels
    - Output: Authoritative outline with corrected heading levels

Phase 1C: Page Summaries (Parallel LLM Calls)
    - Brief 2-3 sentence summary per page
    - Can run fully in parallel since structure is already known
    - Lightweight, minimal tokens

This approach replaces the sequential page chain for many-page documents,
reducing LLM calls from N (sequential) to 1 + N (parallel).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

from .models import (
    DocumentContext,
    DocumentType,
    ExtractedHeading,
    HeadingFix,
    HeadingInference,
    OutlineEntry,
    PageSummary,
    StructureInferenceResult,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Phase 1A: Deterministic Extraction
# =============================================================================


def extract_headings_from_page(page_num: int, markdown: str) -> list[ExtractedHeading]:
    """Extract all headings from a single page's markdown.

    This is a pure regex operation - no LLM required.

    Args:
        page_num: 1-indexed page number
        markdown: Markdown content for the page

    Returns:
        List of ExtractedHeading objects found on this page
    """
    headings: list[ExtractedHeading] = []
    pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    for match in pattern.finditer(markdown):
        line_num = markdown[: match.start()].count("\n") + 1
        hashes = match.group(1)
        text = match.group(2).strip()

        headings.append(
            ExtractedHeading(
                text=text,
                page=page_num,
                line=line_num,
                markdown_level=len(hashes),
            )
        )

    return headings


def count_figures_tables(markdown: str) -> tuple[int, int]:
    """Count figures and tables in markdown.

    Args:
        markdown: Markdown content

    Returns:
        Tuple of (figure_count, table_count)
    """
    # Count figures (markdown images and placeholders)
    figure_count = len(re.findall(r"!\[", markdown))
    figure_count += len(re.findall(r"<!--\s*image", markdown, re.IGNORECASE))

    # Count tables (pipe-delimited rows and placeholders)
    table_count = len(
        re.findall(r"^\s*\|[\s\-:]+\|[\s\-:|]+\|?\s*$", markdown, re.MULTILINE)
    )
    table_count += len(re.findall(r"<!--\s*table", markdown, re.IGNORECASE))

    return figure_count, table_count


def extract_all_headings(
    page_markdowns: dict[int, str],
) -> tuple[list[ExtractedHeading], dict[int, tuple[int, int]]]:
    """Extract headings from all pages (Phase 1A).

    This runs in parallel across pages - pure regex, no LLM.

    Args:
        page_markdowns: Dict mapping page number to markdown content

    Returns:
        Tuple of:
            - All headings in document order
            - Dict mapping page number to (figure_count, table_count)
    """
    all_headings: list[ExtractedHeading] = []
    content_counts: dict[int, tuple[int, int]] = {}

    # Process pages in order
    for page_num in sorted(page_markdowns.keys()):
        markdown = page_markdowns[page_num]

        # Extract headings
        page_headings = extract_headings_from_page(page_num, markdown)
        all_headings.extend(page_headings)

        # Count figures and tables
        content_counts[page_num] = count_figures_tables(markdown)

    logger.info(
        f"Phase 1A complete: {len(all_headings)} headings extracted "
        f"from {len(page_markdowns)} pages"
    )

    return all_headings, content_counts


# =============================================================================
# Phase 1B: Structure Inference (Single LLM Call)
# =============================================================================


STRUCTURE_INFERENCE_PROMPT = """You are a document structure analyst. You will receive a complete list of headings extracted from a document, in document order.

Your task is to:
1. Identify the document title and type
2. Infer the CORRECT heading level for each heading based on document hierarchy
3. Flag any headings where you have low confidence

## Heading Level Rules

Headings follow a strict hierarchy based on section numbering:
- Document title = H1 (level 1) - only ONE per document
- Major sections ("1", "2", "3", "Abstract", "Introduction") = H2 (level 2)
- Subsections ("1.1", "2.1") = H3 (level 3)
- Sub-subsections ("1.1.1", "2.1.1") = H4 (level 4)
- Deeper nesting follows the pattern

## Examples

- "Course Syllabus" as first heading → likely H1 (title)
- "1 Introduction" → H2 (one number = major section)
- "1.1 Background" → H3 (two numbers = subsection)
- "Abstract" without number at start → H2 (standard paper section)
- "Learning Objectives" under "Week 1" → H3 (subsection)

## Important Notes

- If a heading has section numbers, use them to determine level
- Consider document type (paper, syllabus, report) when inferring structure
- Flag headings as low_confidence if the correct level is ambiguous
- Extract key terms (product names, acronyms, technical terms) from headings
"""


class StructureInferenceLLMOutput(BaseModel):
    """LLM output format for structure inference."""

    document_title: str = Field(description="The document's title")
    document_type: str = Field(
        description="Type: paper, syllabus, slides, manual, report, article, other"
    )
    headings: list[HeadingInference] = Field(
        description="All headings with inferred correct levels"
    )
    key_terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific terms extracted from headings",
    )
    low_confidence_pages: list[int] = Field(
        default_factory=list,
        description="Page numbers where heading inference is uncertain",
    )


# Lazy-loaded agent
_structure_agent: Agent[None, StructureInferenceLLMOutput] | None = None


def _get_structure_agent() -> Agent[None, StructureInferenceLLMOutput]:
    """Get or create the structure inference agent."""
    global _structure_agent

    if _structure_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _structure_agent = Agent(
            model=model,
            output_type=StructureInferenceLLMOutput,
            system_prompt=STRUCTURE_INFERENCE_PROMPT,
        )
        logger.info("Structure inference agent initialized")

    return _structure_agent


def _build_structure_prompt(headings: list[ExtractedHeading]) -> str:
    """Build the prompt for structure inference.

    Args:
        headings: All extracted headings in document order

    Returns:
        Prompt string for the LLM
    """
    lines = ["## Headings in Document Order\n"]

    for i, h in enumerate(headings, 1):
        lines.append(
            f"{i}. Page {h.page}, Line {h.line}: "
            f"'{h.text}' (currently H{h.markdown_level})"
        )

    lines.append(
        f"\n## Task\n"
        f"Analyze these {len(headings)} headings and infer the correct level for each. "
        f"Return the document title, type, and corrected heading structure."
    )

    return "\n".join(lines)


async def infer_document_structure(
    headings: list[ExtractedHeading],
) -> tuple[StructureInferenceResult, int, int]:
    """Infer document structure from all headings (Phase 1B).

    Single LLM call that sees the complete heading list and infers
    the correct hierarchical structure.

    Args:
        headings: All extracted headings in document order

    Returns:
        Tuple of (StructureInferenceResult, input_tokens, output_tokens)
    """
    if not headings:
        # No headings - return minimal result
        return (
            StructureInferenceResult(
                document_title="Untitled Document",
                document_type=DocumentType.OTHER,
                headings=[],
                key_terms=[],
                low_confidence_pages=[],
            ),
            0,
            0,
        )

    agent = _get_structure_agent()
    prompt = _build_structure_prompt(headings)

    result = await agent.run(prompt)
    output = result.output

    usage = result.usage()
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0

    # Convert to our model
    try:
        doc_type = DocumentType(output.document_type.lower())
    except ValueError:
        doc_type = DocumentType.OTHER

    inference_result = StructureInferenceResult(
        document_title=output.document_title,
        document_type=doc_type,
        headings=output.headings,
        key_terms=output.key_terms,
        low_confidence_pages=output.low_confidence_pages,
    )

    logger.info(
        f"Phase 1B complete: '{output.document_title}' ({doc_type.value}), "
        f"{len(output.headings)} headings analyzed, "
        f"{len(output.low_confidence_pages)} low confidence pages"
    )

    return inference_result, input_tokens, output_tokens


# =============================================================================
# Phase 1C: Page Summaries (Parallel)
# =============================================================================


PAGE_SUMMARY_PROMPT = """You are a document analyst. Provide a brief 2-3 sentence summary of this page.

Focus on:
- What the page covers (main topic)
- Key information or concepts
- How it relates to the section it's in

Keep it concise - this is for context, not detailed analysis.
"""


class PageSummaryLLMOutput(BaseModel):
    """LLM output for page summary."""

    summary: str = Field(description="2-3 sentence summary of the page")
    section_context: str = Field(
        description="Where this page fits (e.g., 'Part of Chapter 2: Methods')"
    )


# Lazy-loaded agent
_summary_agent: Agent[None, PageSummaryLLMOutput] | None = None


def _get_summary_agent() -> Agent[None, PageSummaryLLMOutput]:
    """Get or create the page summary agent."""
    global _summary_agent

    if _summary_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _summary_agent = Agent(
            model=model,
            output_type=PageSummaryLLMOutput,
            system_prompt=PAGE_SUMMARY_PROMPT,
        )
        logger.info("Page summary agent initialized")

    return _summary_agent


def _build_summary_prompt(
    page_num: int,
    markdown: str,
    section_heading: str,
    document_title: str,
) -> str:
    """Build prompt for page summary.

    Args:
        page_num: Page number
        markdown: Page markdown (truncated if needed)
        section_heading: Current section this page is in
        document_title: Document title for context

    Returns:
        Prompt string
    """
    # Truncate markdown if too long
    max_chars = 4000
    truncated = markdown[:max_chars]
    if len(markdown) > max_chars:
        truncated += "\n\n(truncated)"

    return f"""## Document: {document_title}
## Current Section: {section_heading}
## Page {page_num}

```markdown
{truncated}
```

Summarize this page briefly (2-3 sentences).
"""


async def summarize_page(
    page_num: int,
    markdown: str,
    section_heading: str,
    document_title: str,
    figure_count: int,
    table_count: int,
) -> tuple[PageSummary, int, int]:
    """Summarize a single page (Phase 1C worker).

    Args:
        page_num: Page number
        markdown: Page markdown
        section_heading: Current section heading
        document_title: Document title
        figure_count: Number of figures on page
        table_count: Number of tables on page

    Returns:
        Tuple of (PageSummary, input_tokens, output_tokens)
    """
    agent = _get_summary_agent()
    prompt = _build_summary_prompt(page_num, markdown, section_heading, document_title)

    result = await agent.run(prompt)
    output = result.output

    usage = result.usage()
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0

    summary = PageSummary(
        page_num=page_num,
        summary=output.summary,
        section_context=output.section_context,
        has_figures=figure_count > 0,
        has_tables=table_count > 0,
        figure_count=figure_count,
        table_count=table_count,
    )

    return summary, input_tokens, output_tokens


def _get_section_for_page(
    page_num: int,
    headings: list[HeadingInference],
) -> str:
    """Get the section heading for a given page.

    Finds the most recent heading at or before this page.

    Args:
        page_num: Page number
        headings: All headings with inferred levels

    Returns:
        Section heading text, or "Document Start" if none
    """
    section = "Document Start"

    for h in headings:
        if h.page > page_num:
            break
        # Prefer higher-level headings for section context
        if h.inferred_level <= 2:
            section = h.heading_text

    return section


async def summarize_all_pages(
    page_markdowns: dict[int, str],
    structure: StructureInferenceResult,
    content_counts: dict[int, tuple[int, int]],
    max_concurrent: int = 5,
) -> tuple[dict[int, PageSummary], int, int]:
    """Summarize all pages in parallel (Phase 1C).

    Args:
        page_markdowns: Dict mapping page number to markdown
        structure: Structure inference result from Phase 1B
        content_counts: Dict mapping page to (figure_count, table_count)
        max_concurrent: Maximum concurrent LLM calls

    Returns:
        Tuple of (summaries dict, total_input_tokens, total_output_tokens)
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    summaries: dict[int, PageSummary] = {}
    total_input = 0
    total_output = 0

    async def process_page(page_num: int) -> tuple[int, PageSummary, int, int]:
        async with semaphore:
            markdown = page_markdowns[page_num]
            section = _get_section_for_page(page_num, structure.headings)
            fig_count, tbl_count = content_counts.get(page_num, (0, 0))

            summary, inp, out = await summarize_page(
                page_num=page_num,
                markdown=markdown,
                section_heading=section,
                document_title=structure.document_title,
                figure_count=fig_count,
                table_count=tbl_count,
            )
            return page_num, summary, inp, out

    # Run all pages in parallel
    tasks = [process_page(p) for p in sorted(page_markdowns.keys())]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Page summary failed: {result}")
            continue

        page_num, summary, inp, out = result
        summaries[page_num] = summary
        total_input += inp
        total_output += out

    logger.info(f"Phase 1C complete: {len(summaries)} pages summarized in parallel")

    return summaries, total_input, total_output


# =============================================================================
# Build Outline from Structure
# =============================================================================


def build_outline_from_structure(
    structure: StructureInferenceResult,
    page_markdowns: dict[int, str],
) -> tuple[list[OutlineEntry], list[HeadingFix]]:
    """Build authoritative outline and heading fixes from structure inference.

    Args:
        structure: Structure inference result
        page_markdowns: Page markdowns for page range calculation

    Returns:
        Tuple of (outline entries, heading fixes needed)
    """
    outline: list[OutlineEntry] = []
    heading_fixes: list[HeadingFix] = []

    total_pages = max(page_markdowns.keys()) if page_markdowns else 1

    for i, h in enumerate(structure.headings):
        # Calculate page_end (next heading's page - 1, or total_pages)
        if i + 1 < len(structure.headings):
            page_end = structure.headings[i + 1].page - 1
            page_end = max(page_end, h.page)  # At least same page
        else:
            page_end = total_pages

        outline.append(
            OutlineEntry(
                heading=h.heading_text,
                level=h.inferred_level,
                page_start=h.page,
                page_end=page_end,
                children=[],  # Flat structure for now
            )
        )

        # Check if heading fix is needed
        if h.markdown_level != h.inferred_level:
            heading_fixes.append(
                HeadingFix(
                    page=h.page,
                    current_text=h.heading_text,
                    current_level=h.markdown_level,
                    should_be_level=h.inferred_level,
                    reason=h.reason or "Level inferred from document structure",
                )
            )

    return outline, heading_fixes


# =============================================================================
# Main Entry Point
# =============================================================================


async def gather_document_context(
    filename: str,
    page_markdowns: dict[int, str],
    event_bus=None,
) -> DocumentContext:
    """Gather complete document context (Phase 1 entry point).

    This orchestrates all three sub-phases:
    1A. Deterministic extraction (parallel, no LLM)
    1B. Structure inference (single LLM call)
    1C. Page summaries (parallel LLM calls)

    Args:
        filename: Original filename
        page_markdowns: Dict mapping page number to markdown
        event_bus: Optional event bus for streaming events

    Returns:
        Complete DocumentContext ready for issue detection
    """
    start_time = time.time()
    total_pages = len(page_markdowns)

    logger.info(f"Starting context gathering for {total_pages} pages")

    # Phase 1A: Deterministic extraction
    phase_1a_start = time.time()
    all_headings, content_counts = extract_all_headings(page_markdowns)
    extraction_duration = int((time.time() - phase_1a_start) * 1000)

    # Phase 1B: Structure inference
    phase_1b_start = time.time()
    structure, inp_1b, out_1b = await infer_document_structure(all_headings)
    inference_duration = int((time.time() - phase_1b_start) * 1000)

    # Build outline and heading fixes
    outline, heading_fixes = build_outline_from_structure(structure, page_markdowns)

    # Phase 1C: Page summaries (parallel)
    phase_1c_start = time.time()
    summaries, inp_1c, out_1c = await summarize_all_pages(
        page_markdowns=page_markdowns,
        structure=structure,
        content_counts=content_counts,
    )
    summary_duration = int((time.time() - phase_1c_start) * 1000)

    total_duration = int((time.time() - start_time) * 1000)

    # Build context
    context = DocumentContext(
        filename=filename,
        total_pages=total_pages,
        title=structure.document_title,
        document_type=structure.document_type,
        authoritative_outline=outline,
        heading_fixes=heading_fixes,
        page_summaries=summaries,
        dictionary=structure.key_terms,
        extraction_duration_ms=extraction_duration,
        inference_duration_ms=inference_duration,
        summary_duration_ms=summary_duration,
        total_tokens_input=inp_1b + inp_1c,
        total_tokens_output=out_1b + out_1c,
    )

    logger.info(
        f"Context gathering complete in {total_duration}ms: "
        f"extraction={extraction_duration}ms, "
        f"inference={inference_duration}ms, "
        f"summaries={summary_duration}ms, "
        f"tokens={context.total_tokens_input}in/{context.total_tokens_output}out"
    )

    return context
