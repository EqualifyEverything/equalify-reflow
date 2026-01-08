"""Document Planner - 3-Stage Document Analysis.

The Planner establishes the holistic view of the document before any
corrections are made. This is the single source of truth that workers follow.

Stages:
    1. Quick Scan - Extract headings, count elements (code, no LLM)
    2. Page Chain - Sequential page analysis with context chaining (LLM)
       Replaces old Stage 2+3. Processes pages one by one, building:
       - Document structure (title, type, outline)
       - Heading fixes (level corrections)
       - Page summaries
       - Figure and table context
       - Domain dictionary
    3. Job Generation - Create worker jobs from page plans (code, no LLM)
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

from .events import (
    EventBus,
    JobCreatedEvent,
    PageScannedEvent,
    PageSummarizedEvent,
    PlanningCompleteEvent,
    PlanningStartedEvent,
    StructureInferredEvent,
)
from .models import (
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    FigureContext,
    HeadingFix,
    Job,
    JobContext,
    JobType,
    OutlineEntry,
    PagePlan,
    PageSkeleton,
    PageType,
    TableContext,
    Task,
    TaskType,
)
from .page_chain import (
    PageChainState,
    run_page_chain,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Stage 1: Quick Scan (Code Only)
# =============================================================================


def _detect_page_type(markdown: str, page_num: int, total_pages: int) -> PageType:
    """Detect page type from content heuristics."""
    text_lower = markdown.lower()

    # Title page: first page with limited content, often has title-like heading
    if page_num == 1:
        if len(markdown) < 500 or markdown.count("#") <= 2:
            return PageType.TITLE

    # TOC: contains "table of contents" or many "..." patterns
    if "table of contents" in text_lower or "contents" in text_lower[:200]:
        return PageType.TOC
    if text_lower.count("...") > 5 or text_lower.count(". . .") > 5:
        return PageType.TOC

    # References: last pages with [1], [2] patterns
    if page_num > total_pages * 0.7:
        if re.findall(r"\[\d+\]", markdown):
            if "reference" in text_lower or "bibliography" in text_lower:
                return PageType.REFERENCES

    # Appendix: contains "appendix" heading
    if re.search(r"#.*appendix", text_lower):
        return PageType.APPENDIX

    # Blank page
    if len(markdown.strip()) < 50:
        return PageType.BLANK

    return PageType.CONTENT


def quick_scan_page(
    page_num: int,
    markdown: str,
    total_pages: int,
) -> PageSkeleton:
    """Stage 1: Extract structure from a single page without LLM.

    This is fast and cheap - pure regex/heuristics.

    Args:
        page_num: 1-indexed page number
        markdown: Raw markdown for this page
        total_pages: Total pages in document

    Returns:
        PageSkeleton with extracted structure
    """
    # Extract headings
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = heading_pattern.findall(markdown)

    headings = [m[1].strip() for m in matches]
    heading_levels = [len(m[0]) for m in matches]

    # Count figures and tables (from placeholders)
    figure_count = len(re.findall(r"<!--\s*image:?\d*\s*-->", markdown, re.IGNORECASE))
    table_count = len(re.findall(r"<!--\s*table:?\d*\s*-->", markdown, re.IGNORECASE))

    # Also count existing images without alt-text
    figure_count += len(re.findall(r"!\[\]\(", markdown))

    # Word count
    words = re.findall(r"\b\w+\b", markdown)
    word_count = len(words)

    # Citation detection
    has_citations = bool(re.search(r"\[\d+\]", markdown))

    # Page type
    page_type = _detect_page_type(markdown, page_num, total_pages)

    return PageSkeleton(
        page_num=page_num,
        headings=headings,
        heading_levels=heading_levels,
        figure_count=figure_count,
        table_count=table_count,
        word_count=word_count,
        has_citations=has_citations,
        page_type=page_type,
    )


async def stage1_quick_scan(
    page_markdowns: dict[int, str],
    event_bus: EventBus | None = None,
) -> list[PageSkeleton]:
    """Stage 1: Quick scan all pages.

    Args:
        page_markdowns: Markdown for each page (1-indexed)
        event_bus: Optional event bus for streaming

    Returns:
        List of PageSkeletons
    """
    total_pages = len(page_markdowns)
    skeletons = []

    for page_num in sorted(page_markdowns.keys()):
        markdown = page_markdowns[page_num]
        skeleton = quick_scan_page(page_num, markdown, total_pages)
        skeletons.append(skeleton)

        if event_bus:
            event_bus.emit(
                PageScannedEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    headings_found=skeleton.headings,
                    figures=skeleton.figure_count,
                    tables=skeleton.table_count,
                    page_type=skeleton.page_type.value,
                )
            )

    logger.info(f"Stage 1 complete: scanned {len(skeletons)} pages")
    return skeletons


# =============================================================================
# Stage 2: Page Chain (Sequential LLM Analysis)
# =============================================================================


def convert_chain_state_to_structure(
    chain_state: PageChainState,
) -> DocumentStructure:
    """Convert PageChainState to DocumentStructure for job generation.

    Args:
        chain_state: Completed page chain state

    Returns:
        DocumentStructure compatible with job generation
    """
    return DocumentStructure(
        title=chain_state.document_title,
        document_type=chain_state.document_type,
        outline=chain_state.outline,
        heading_fixes=chain_state.heading_fixes,
        key_terms=list(chain_state.dictionary),
        acronyms={},  # Could be extended in the chain
    )


def convert_chain_state_to_page_plans(
    chain_state: PageChainState,
) -> dict[int, PagePlan]:
    """Convert PageChainState to PagePlan dict for job generation.

    Args:
        chain_state: Completed page chain state

    Returns:
        Dict mapping page number to PagePlan
    """
    page_plans: dict[int, PagePlan] = {}

    for page_num, summary in chain_state.page_summaries.items():
        figures = chain_state.figures_by_page.get(page_num, [])
        tables = chain_state.tables_by_page.get(page_num, [])

        # Get paragraph issues for this page
        page_artifacts = chain_state.page_artifacts_by_page.get(page_num, [])
        footnote_issues = chain_state.footnote_issues_by_page.get(page_num, [])
        citation_issues = chain_state.citation_issues_by_page.get(page_num, [])
        list_issues = chain_state.list_issues_by_page.get(page_num, [])
        typography_issues = chain_state.typography_issues_by_page.get(page_num, [])
        has_page_continuation = chain_state.page_continuations.get(page_num, False)

        page_plans[page_num] = PagePlan(
            page_num=page_num,
            summary=summary,
            section_context="",  # Could be derived from outline
            keywords=[],  # Terms are accumulated in dictionary
            figures=figures,
            tables=tables,
            ocr_errors=[],  # Could be extended in the chain
            formatting_issues=[],
            # Paragraph issues for ParagraphAgent
            page_artifacts=page_artifacts,
            footnote_issues=footnote_issues,
            citation_issues=citation_issues,
            list_issues=list_issues,
            typography_issues=typography_issues,
            has_page_continuation=has_page_continuation,
        )

    return page_plans


async def stage2_page_chain(
    page_markdowns: dict[int, str],
    event_bus: EventBus | None = None,
) -> tuple[DocumentStructure, dict[int, PagePlan], int, int, list]:
    """Stage 2: Run page chain for sequential document analysis.

    This replaces the old Stage 2 (structure inference) and Stage 3
    (page summaries) with a unified sequential approach.

    Each page is analyzed with context from previous pages, ensuring:
    - Heading structure continuity
    - Accumulated dictionary
    - Proper outline building

    Args:
        page_markdowns: Markdown for each page (1-indexed)
        event_bus: Optional event bus for streaming

    Returns:
        Tuple of (structure, page_plans, input_tokens, output_tokens, llm_calls)
    """
    # Run the page chain
    chain_state, input_tokens, output_tokens, llm_calls = await run_page_chain(
        page_markdowns=page_markdowns,
        event_bus=event_bus,
    )

    # Emit structure inferred event
    if event_bus:
        event_bus.emit(
            StructureInferredEvent(
                document_id=event_bus.document_id,
                document_title=chain_state.document_title,
                document_type=chain_state.document_type,
                outline=[o.model_dump() for o in chain_state.outline],
                dictionary_size=len(chain_state.dictionary),
                heading_fixes_needed=len(chain_state.heading_fixes),
                dictionary_terms=list(chain_state.dictionary),
                heading_fixes=[h.model_dump() for h in chain_state.heading_fixes],
            )
        )

    # Convert to formats expected by job generation
    structure = convert_chain_state_to_structure(chain_state)
    page_plans = convert_chain_state_to_page_plans(chain_state)

    logger.info(
        f"Stage 2 complete: {len(structure.outline)} outline entries, "
        f"{len(structure.heading_fixes)} heading fixes, "
        f"{len(structure.key_terms)} terms"
    )

    return structure, page_plans, input_tokens, output_tokens, llm_calls


# =============================================================================
# Legacy Stage 2: Structure Inference (LLM) - DEPRECATED
# =============================================================================


class StructureInferenceOutput(BaseModel):
    """Output model for Stage 2 LLM call."""

    title: str = Field(..., description="Document title")
    document_type: DocumentType = Field(..., description="Type of document")

    outline: list[OutlineEntry] = Field(
        default_factory=list,
        description="Hierarchical outline with page spans",
    )

    key_terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific terms for dictionary",
    )

    acronyms: dict[str, str] = Field(
        default_factory=dict,
        description="Acronym expansions",
    )

    heading_fixes: list[HeadingFix] = Field(
        default_factory=list,
        description="Headings that need level adjustment",
    )


STRUCTURE_SYSTEM_PROMPT = """You are a document structure analyst. Your job is to:
1. Determine the document type and title
2. Build a hierarchical outline (table of contents)
3. Identify domain-specific terms for spell-checking
4. Flag any heading level issues

## Input Format
You receive a summary of each page with:
- Headings found and their levels
- Element counts (figures, tables)
- Page type (title, content, references, etc.)

## Output Requirements

### Document Type
Classify as: paper, syllabus, slides, manual, report, article, or other

### Outline Rules
Build a proper hierarchical outline:
- H1 should be the document title (usually on page 1)
- Section numbers map to levels: "1" = H2, "1.1" = H3, "1.1.1" = H4
- Include page_start and page_end for each section
- Nest children properly

### Heading Fixes
Flag headings that violate hierarchy:
- Skips (H1 -> H3 without H2)
- Wrong levels based on section numbering
- Include the current level and what it should be

### Key Terms
Extract domain-specific terms that should be added to spell-check dictionary:
- Product names (Docling, TableFormer)
- Technical terms
- Proper nouns
- Acronyms

## Important
- Be thorough but concise
- Focus on structure, not content
- The outline you create becomes the source of truth for workers
"""


_structure_agent: Agent[None, StructureInferenceOutput] | None = None


def _get_structure_agent() -> Agent[None, StructureInferenceOutput]:
    """Get or create the structure inference agent."""
    global _structure_agent

    if _structure_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _structure_agent = Agent(
            model=model,
            output_type=StructureInferenceOutput,
            system_prompt=STRUCTURE_SYSTEM_PROMPT,
        )
        logger.info("Structure inference agent initialized")

    return _structure_agent


async def stage2_structure_inference(
    skeletons: list[PageSkeleton],
    event_bus: EventBus | None = None,
) -> tuple[DocumentStructure, int, int]:
    """Stage 2: Infer document structure from page skeletons.

    Args:
        skeletons: Page skeletons from Stage 1
        event_bus: Optional event bus for streaming

    Returns:
        Tuple of (DocumentStructure, input_tokens, output_tokens)
    """
    agent = _get_structure_agent()

    # Build prompt from skeletons
    pages_summary = []
    for skeleton in skeletons:
        pages_summary.append(
            f"Page {skeleton.page_num} ({skeleton.page_type.value}):\n"
            f"  Headings: {skeleton.headings}\n"
            f"  Levels: {skeleton.heading_levels}\n"
            f"  Figures: {skeleton.figure_count}, Tables: {skeleton.table_count}\n"
            f"  Words: {skeleton.word_count}"
        )

    prompt = f"""Analyze this document structure:

{chr(10).join(pages_summary)}

Provide:
1. Document title and type
2. Hierarchical outline with page spans
3. Key terms for dictionary
4. Any heading level fixes needed
"""

    try:
        result = await agent.run(prompt)
        output = result.output

        # Extract usage
        usage = result.usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0

        structure = DocumentStructure(
            title=output.title,
            document_type=output.document_type,
            outline=output.outline,
            heading_fixes=output.heading_fixes,
            key_terms=output.key_terms,
            acronyms=output.acronyms,
        )

        if event_bus:
            event_bus.emit(
                StructureInferredEvent(
                    document_id=event_bus.document_id,
                    document_title=structure.title,
                    document_type=structure.document_type,
                    outline=[o.model_dump() for o in structure.outline],
                    dictionary_size=len(structure.key_terms),
                    heading_fixes_needed=len(structure.heading_fixes),
                    # Detailed fields for verbose output
                    dictionary_terms=structure.key_terms,
                    heading_fixes=[h.model_dump() for h in structure.heading_fixes],
                )
            )

        logger.info(
            f"Stage 2 complete: {structure.document_type.value}, "
            f"{len(structure.outline)} outline entries, "
            f"{len(structure.key_terms)} terms"
        )

        return structure, input_tokens, output_tokens

    except Exception as e:
        logger.error(f"Stage 2 failed: {e}")
        # Return minimal structure on error
        return (
            DocumentStructure(
                title="Unknown Document",
                document_type=DocumentType.OTHER,
            ),
            0,
            0,
        )


# =============================================================================
# Stage 3: Page Summaries (Batched LLM)
# =============================================================================


class PageSummaryOutput(BaseModel):
    """Output for a single page summary."""

    page_num: int
    summary: str
    section_context: str
    keywords: list[str] = Field(default_factory=list)

    figures: list[FigureContext] = Field(default_factory=list)
    tables: list[TableContext] = Field(default_factory=list)

    ocr_errors: list[str] = Field(default_factory=list)
    formatting_issues: list[str] = Field(default_factory=list)


class BatchSummaryOutput(BaseModel):
    """Output for a batch of page summaries."""

    pages: list[PageSummaryOutput]


PAGE_SUMMARY_SYSTEM_PROMPT = """You are a document page analyst. For each page, provide:

1. **Summary**: 1-2 sentences describing the page content
2. **Section Context**: Where this page fits (e.g., "Part of 2.1 Architecture")
3. **Keywords**: Domain terms found on this page
4. **Figures**: For each figure placeholder, describe what it appears to be
5. **Tables**: For each table placeholder, describe its structure
6. **Issues**: OCR errors or formatting problems spotted

## Figure Analysis
For each figure (<!-- image:N --> or ![]()):
- What does it appear to be? (diagram, chart, photo, logo)
- Is it decorative (no alt needed) or informative (needs alt)?
- What's the surrounding context?

## Table Analysis
For each table (<!-- table:N -->):
- What type of table? (data, comparison, layout)
- Approximate dimensions?
- Does it have a header row?

## OCR Errors
Look for obvious typos, especially:
- Technical terms misspelled
- Common OCR errors: rn→m, l→1, O→0

Be thorough but concise. Focus on what workers need to know.
"""


_summary_agent: Agent[None, BatchSummaryOutput] | None = None


def _get_summary_agent() -> Agent[None, BatchSummaryOutput]:
    """Get or create the page summary agent."""
    global _summary_agent

    if _summary_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _summary_agent = Agent(
            model=model,
            output_type=BatchSummaryOutput,
            system_prompt=PAGE_SUMMARY_SYSTEM_PROMPT,
        )
        logger.info("Page summary agent initialized")

    return _summary_agent


async def _summarize_batch(
    batch_pages: list[int],
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    structure: DocumentStructure,
) -> tuple[list[PageSummaryOutput], int, int]:
    """Summarize a batch of pages.

    Args:
        batch_pages: Page numbers in this batch
        page_markdowns: All page markdowns
        page_images: All page images
        structure: Document structure for context

    Returns:
        Tuple of (summaries, input_tokens, output_tokens)
    """
    from io import BytesIO

    from pydantic_ai.messages import BinaryContent

    agent = _get_summary_agent()

    # Build prompt with page content
    pages_content = []
    for page_num in batch_pages:
        markdown = page_markdowns.get(page_num, "")
        pages_content.append(f"=== Page {page_num} ===\n{markdown[:2000]}")  # Truncate long pages

    # Include structure context
    outline_text = "\n".join(
        f"- {o.heading} (pp. {o.page_start}-{o.page_end})"
        for o in structure.outline[:10]  # Limit outline size
    )

    prompt = f"""Document: {structure.title} ({structure.document_type.value})

Outline:
{outline_text}

Analyze these pages:

{chr(10).join(pages_content)}

For each page, provide summary, section context, figures, tables, and issues.
"""

    # Prepare images for the batch
    messages: list[str | BinaryContent] = [prompt]

    for page_num in batch_pages:
        if page_num in page_images:
            img = page_images[page_num]
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            messages.append(f"Page {page_num} image:")
            messages.append(BinaryContent(data=buffer.getvalue(), media_type="image/png"))

    try:
        result = await agent.run(messages)
        output = result.output

        usage = result.usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0

        return output.pages, input_tokens, output_tokens

    except Exception as e:
        logger.error(f"Batch summary failed for pages {batch_pages}: {e}")
        # Return empty summaries on error
        return [], 0, 0


async def stage3_page_summaries(
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    structure: DocumentStructure,
    batch_size: int = 3,
    event_bus: EventBus | None = None,
) -> tuple[dict[int, PagePlan], int, int]:
    """Stage 3: Get detailed summaries for all pages in batches.

    Runs batches in parallel for efficiency.

    Args:
        page_markdowns: Markdown for each page
        page_images: Images for each page
        structure: Document structure from Stage 2
        batch_size: Pages per batch
        event_bus: Optional event bus

    Returns:
        Tuple of (page_plans dict, total_input_tokens, total_output_tokens)
    """
    pages = sorted(page_markdowns.keys())

    # Create batches
    batches = [pages[i : i + batch_size] for i in range(0, len(pages), batch_size)]

    # Run batches in parallel
    tasks = [_summarize_batch(batch, page_markdowns, page_images, structure) for batch in batches]

    results = await asyncio.gather(*tasks)

    # Aggregate results
    page_plans: dict[int, PagePlan] = {}
    total_input = 0
    total_output = 0

    for summaries, input_tokens, output_tokens in results:
        total_input += input_tokens
        total_output += output_tokens

        for summary in summaries:
            page_plans[summary.page_num] = PagePlan(
                page_num=summary.page_num,
                summary=summary.summary,
                section_context=summary.section_context,
                keywords=summary.keywords,
                figures=summary.figures,
                tables=summary.tables,
                ocr_errors=summary.ocr_errors,
                formatting_issues=summary.formatting_issues,
            )

            if event_bus:
                event_bus.emit(
                    PageSummarizedEvent(
                        document_id=event_bus.document_id,
                        page_num=summary.page_num,
                        summary=summary.summary,
                        section_context=summary.section_context,
                        figures_found=len(summary.figures),
                        tables_found=len(summary.tables),
                        issues_found=len(summary.ocr_errors) + len(summary.formatting_issues),
                        # Detailed fields for verbose output
                        figures_detail=[f.model_dump() for f in summary.figures],
                        tables_detail=[t.model_dump() for t in summary.tables],
                        ocr_errors=summary.ocr_errors,
                        formatting_issues=summary.formatting_issues,
                        keywords=summary.keywords,
                    )
                )

    logger.info(f"Stage 3 complete: {len(page_plans)} page plans created")
    return page_plans, total_input, total_output


# =============================================================================
# Stage 4: Job Generation (Code Only)
# =============================================================================


def _get_relevant_outline(
    page_num: int,
    outline: list[OutlineEntry],
) -> list[OutlineEntry]:
    """Get outline entries relevant to a specific page."""
    relevant = []

    def check_entry(entry: OutlineEntry) -> bool:
        if entry.page_start <= page_num <= entry.page_end:
            relevant.append(entry)
            for child in entry.children:
                check_entry(child)
            return True
        return False

    for entry in outline:
        check_entry(entry)

    return relevant


def stage4_generate_jobs(
    page_markdowns: dict[int, str],
    page_plans: dict[int, PagePlan],
    structure: DocumentStructure,
    event_bus: EventBus | None = None,
) -> list[Job]:
    """Stage 4: Generate worker jobs from page plans.

    Args:
        page_markdowns: Markdown for each page
        page_plans: Page plans from Stage 3
        structure: Document structure from Stage 2
        event_bus: Optional event bus

    Returns:
        List of jobs to execute
    """
    jobs: list[Job] = []

    # First, create structure jobs for heading fixes
    # Group heading fixes by page
    heading_fixes_by_page: dict[int, list[HeadingFix]] = {}
    for fix in structure.heading_fixes:
        if fix.page not in heading_fixes_by_page:
            heading_fixes_by_page[fix.page] = []
        heading_fixes_by_page[fix.page].append(fix)

    # Create a structure job for each page with heading fixes
    for page_num, fixes in sorted(heading_fixes_by_page.items()):
        tasks: list[Task] = []

        for fix in fixes:
            # Pre-compute exact before/after text - don't make the LLM do arithmetic
            before_text = f"{'#' * fix.current_level} {fix.current_text}"
            after_text = f"{'#' * fix.should_be_level} {fix.current_text}"

            tasks.append(
                Task(
                    task_type=TaskType.HEADING_FIX,
                    target=fix.current_text,
                    context=(f"FIND: {before_text}\nREPLACE WITH: {after_text}\nReason: {fix.reason}"),
                    priority=1,
                )
            )

        # Build context for the structure job
        context = JobContext(
            document_title=structure.title,
            document_type=structure.document_type,
            section_context=f"Heading fixes for page {page_num}",
            dictionary=structure.key_terms,
            relevant_outline=_get_relevant_outline(page_num, structure.outline),
        )

        job = Job(
            job_type=JobType.STRUCTURE,
            priority=1,  # Structure jobs run BEFORE content jobs (priority 2+)
            page=page_num,
            tasks=tasks,
            context=context,
            page_markdown=page_markdowns.get(page_num, ""),
        )
        jobs.append(job)

        if event_bus:
            event_bus.emit(
                JobCreatedEvent(
                    document_id=event_bus.document_id,
                    job_id=job.job_id,
                    job_type=job.job_type.value,
                    page=job.page,
                    task_count=len(job.tasks),
                    task_types=[t.task_type.value for t in job.tasks],
                    # Detailed fields for verbose output
                    tasks_detail=[t.model_dump() for t in job.tasks],
                    section_context=context.section_context,
                )
            )

    # Create content jobs for each page
    for page_num, plan in sorted(page_plans.items()):
        tasks: list[Task] = []

        # Alt-text tasks
        for fig in plan.figures:
            if not fig.is_decorative:
                tasks.append(
                    Task(
                        task_type=TaskType.ALT_TEXT,
                        target=f"fig:{fig.figure_index}",
                        context=f"{fig.appears_to_be}. {fig.surrounding_text}",
                        priority=1,
                    )
                )

        # Table tasks
        for table in plan.tables:
            tasks.append(
                Task(
                    task_type=TaskType.TABLE_TRANSCRIPTION,
                    target=f"table:{table.table_index}",
                    context=f"{table.appears_to_be}. Caption: {table.caption or 'none'}",
                    priority=1,
                )
            )

        # OCR fix tasks
        for error in plan.ocr_errors:
            tasks.append(
                Task(
                    task_type=TaskType.OCR_FIX,
                    target=f"ocr:{error[:20]}",
                    context=error,
                    priority=2,
                )
            )

        # Only create job if there are tasks
        if tasks:
            context = JobContext(
                document_title=structure.title,
                document_type=structure.document_type,
                section_context=plan.section_context,
                dictionary=structure.key_terms + plan.keywords,
                relevant_outline=_get_relevant_outline(page_num, structure.outline),
            )

            job = Job(
                job_type=JobType.CONTENT,
                priority=page_num + 1,  # Content jobs run AFTER structure jobs (priority 1)
                page=page_num,
                tasks=tasks,
                context=context,
                page_markdown=page_markdowns.get(page_num, ""),
            )
            jobs.append(job)

            if event_bus:
                event_bus.emit(
                    JobCreatedEvent(
                        document_id=event_bus.document_id,
                        job_id=job.job_id,
                        job_type=job.job_type.value,
                        page=job.page,
                        task_count=len(job.tasks),
                        task_types=[t.task_type.value for t in job.tasks],
                        # Detailed fields for verbose output
                        tasks_detail=[t.model_dump() for t in job.tasks],
                        section_context=context.section_context,
                    )
                )

    # Create paragraph jobs for text flow fixes (ParagraphAgent)
    for page_num, plan in sorted(page_plans.items()):
        paragraph_tasks: list[Task] = []

        # Page artifact removal tasks (---, split words, column breaks)
        for artifact in plan.page_artifacts:
            paragraph_tasks.append(
                Task(
                    task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                    target=f"artifact:{page_num}:{artifact.line_number}",
                    context=f"{artifact.artifact_type}: {artifact.text[:50]}",
                    priority=1,
                )
            )

        # Footnote correction tasks
        for fn in plan.footnote_issues:
            paragraph_tasks.append(
                Task(
                    task_type=TaskType.FOOTNOTE_CORRECTION,
                    target=f"footnote:{fn.marker}",
                    context=fn.issue_type,
                    priority=1,
                )
            )

        # Citation linking tasks
        for cite in plan.citation_issues:
            paragraph_tasks.append(
                Task(
                    task_type=TaskType.CITATION_LINKING,
                    target=f"citation:{cite.marker}",
                    context=cite.issue_type,
                    priority=2,
                )
            )

        # List structure fix tasks
        for lst in plan.list_issues:
            paragraph_tasks.append(
                Task(
                    task_type=TaskType.LIST_FIX,
                    target=f"list:{lst.location}",
                    context=f"{lst.issue_type}: {lst.description}",
                    priority=2,
                )
            )

        # Typography fix tasks (semantic bold/italic/code)
        for typo in plan.typography_issues:
            paragraph_tasks.append(
                Task(
                    task_type=TaskType.TYPOGRAPHY_FIX,
                    target=f"typography:{typo.text[:20]}",
                    context=f"{typo.formatting_type}: {typo.semantic_purpose}",
                    priority=3,
                )
            )

        # Only create job if there are paragraph tasks
        if paragraph_tasks:
            context = JobContext(
                document_title=structure.title,
                document_type=structure.document_type,
                section_context=plan.section_context,
                dictionary=structure.key_terms + plan.keywords,
                relevant_outline=_get_relevant_outline(page_num, structure.outline),
            )

            job = Job(
                job_type=JobType.PARAGRAPH,
                priority=page_num + 100,  # Run after CONTENT jobs (priority 2+)
                page=page_num,
                tasks=paragraph_tasks,
                context=context,
                page_markdown=page_markdowns.get(page_num, ""),
            )
            jobs.append(job)

            if event_bus:
                event_bus.emit(
                    JobCreatedEvent(
                        document_id=event_bus.document_id,
                        job_id=job.job_id,
                        job_type=job.job_type.value,
                        page=job.page,
                        task_count=len(job.tasks),
                        task_types=[t.task_type.value for t in job.tasks],
                        tasks_detail=[t.model_dump() for t in job.tasks],
                        section_context=context.section_context,
                    )
                )

    logger.info(f"Stage 4 complete: {len(jobs)} jobs created")
    return jobs


# =============================================================================
# Main Planner Function
# =============================================================================


async def plan_document(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    event_bus: EventBus | None = None,
) -> DocumentPlan:
    """Run the complete 3-stage planning process.

    Stages:
        1. Quick Scan - Extract headings, count elements (code)
        2. Page Chain - Sequential page analysis with context chaining (LLM)
        3. Job Generation - Create worker jobs from analysis (code)

    Args:
        filename: Original document filename
        page_markdowns: Markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        event_bus: Optional event bus for streaming

    Returns:
        Complete DocumentPlan
    """
    start_time = time.time()
    total_pages = len(page_markdowns)

    document_id = event_bus.document_id if event_bus else "unknown"

    if event_bus:
        event_bus.emit(
            PlanningStartedEvent(
                document_id=document_id,
                total_pages=total_pages,
                filename=filename,
            )
        )

    logger.info(f"Starting planning for {filename} ({total_pages} pages)")

    # Stage 1: Quick Scan (code only - fast)
    await stage1_quick_scan(page_markdowns, event_bus)

    # Stage 2: Page Chain (sequential LLM analysis)
    # This replaces the old Stage 2 + Stage 3 with a unified approach
    structure, page_plans, input_tokens, output_tokens, llm_calls = await stage2_page_chain(page_markdowns, event_bus)

    # Stage 3: Job Generation (code only - fast)
    jobs = stage4_generate_jobs(page_markdowns, page_plans, structure, event_bus)

    # Dictionary is already built by the page chain
    full_dictionary = structure.key_terms

    duration_ms = int((time.time() - start_time) * 1000)

    plan = DocumentPlan(
        document_id=document_id,
        filename=filename,
        total_pages=total_pages,
        structure=structure,
        pages=page_plans,
        jobs=jobs,
        full_dictionary=full_dictionary,
        planning_duration_ms=duration_ms,
        planning_tokens_input=input_tokens,
        planning_tokens_output=output_tokens,
        llm_calls=llm_calls,
    )

    if event_bus:
        event_bus.emit(
            PlanningCompleteEvent(
                document_id=document_id,
                total_jobs=len(jobs),
                structure_jobs=sum(1 for j in jobs if j.job_type == JobType.STRUCTURE),
                content_jobs=sum(1 for j in jobs if j.job_type == JobType.CONTENT),
                dictionary_terms=full_dictionary,
                duration_ms=duration_ms,
            )
        )

    logger.info(f"Planning complete: {len(jobs)} jobs, {len(full_dictionary)} terms, {duration_ms}ms")

    return plan
