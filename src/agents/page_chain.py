"""Pipeline Page Chain - Sequential Page Analysis with Context Chaining.

This module implements a page-by-page analysis approach where each page:
1. Receives context from previous pages (heading structure, dictionary)
2. Analyzes the current page (headings, summary, figures, tables, terms)
3. Passes updated context to the next page

This ensures:
- Heading structure continuity (can't jump H4 -> H1 without detection)
- Accumulated dictionary for spell-checking
- Focused LLM context per page (avoids token limits)
- Single source of truth for document structure
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.agents.subagents import is_rate_limit_error, llm_circuit_breaker
from src.services.metrics_service import record_llm_call
from src.shared.llm_cost import calculate_estimated_cost
from src.utils.circuit_breaker import CircuitBreakerOpenError

from .debug_capture import extract_raw_response, serialize_text_prompt
from .models import (
    CitationIssue,
    DocumentType,
    FigureContext,
    FootnoteIssue,
    HeadingFix,
    ListIssue,
    LLMCallRecord,
    OutlineEntry,
    PageArtifactIssue,
    TableContext,
    TypographyIssue,
)

if TYPE_CHECKING:
    from .events import EventBus

logger = logging.getLogger(__name__)


# =============================================================================
# Edge Text Extraction
# =============================================================================


def _extract_edge_text(text: str, position: str) -> str:
    """Extract edge text from page content for merge detection.

    Args:
        text: The page markdown content
        position: "first" for first 100 chars, "last" for last 100 chars

    Returns:
        First or last 100 characters with markdown formatting stripped
    """
    if not text:
        return ""

    # Strip markdown formatting patterns
    # Remove headers (# symbols)
    stripped = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold (**text** or __text__)
    stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
    stripped = re.sub(r"__([^_]+)__", r"\1", stripped)
    # Remove italic (*text* or _text_)
    stripped = re.sub(r"\*([^*]+)\*", r"\1", stripped)
    stripped = re.sub(r"_([^_]+)_", r"\1", stripped)
    # Remove links [text](url) -> text
    stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
    # Remove inline code `text`
    stripped = re.sub(r"`([^`]+)`", r"\1", stripped)
    # Remove image references ![alt](url)
    stripped = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", stripped)
    # Remove HTML comments <!-- ... -->
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    # Remove horizontal rules (---, ***, ___)
    stripped = re.sub(r"^[-*_]{3,}\s*$", "", stripped, flags=re.MULTILINE)
    # Remove list markers (-, *, +, numbers)
    stripped = re.sub(r"^\s*[-*+]\s+", "", stripped, flags=re.MULTILINE)
    stripped = re.sub(r"^\s*\d+\.\s+", "", stripped, flags=re.MULTILINE)
    # Normalize whitespace (collapse multiple spaces/newlines to single space)
    stripped = re.sub(r"\s+", " ", stripped)
    # Strip leading/trailing whitespace
    stripped = stripped.strip()

    if not stripped:
        return ""

    # Extract based on position
    if position == "first":
        return stripped[:100]
    elif position == "last":
        return stripped[-100:]
    else:
        return ""


# =============================================================================
# Chain State Models
# =============================================================================


class HeadingInChain(BaseModel):
    """A heading with its position in the document structure."""

    text: str = Field(description="The heading text")
    level: int = Field(description="The heading level (1-6)")
    page: int = Field(description="Page number where this heading appears")


class PageChainState(BaseModel):
    """State passed between pages in the chain.

    This accumulates as we process each page, building the complete
    document structure.
    """

    # Document-level info (set on page 1)
    document_title: str = Field(default="", description="Document title from page 1")
    document_type: DocumentType = Field(default=DocumentType.OTHER, description="Document type")

    # Heading chain - the last heading provides context for next page
    last_heading: HeadingInChain | None = Field(default=None, description="Last heading seen (for continuity)")

    # Accumulated outline
    outline: list[OutlineEntry] = Field(default_factory=list, description="Hierarchical outline built so far")

    # Accumulated dictionary
    dictionary: set[str] = Field(default_factory=set, description="Domain terms accumulated")

    # Heading fixes needed
    heading_fixes: list[HeadingFix] = Field(default_factory=list, description="All heading fixes needed")

    # Page summaries
    page_summaries: dict[int, str] = Field(default_factory=dict, description="Summary for each page")

    # Figure and table context by page
    figures_by_page: dict[int, list[FigureContext]] = Field(default_factory=dict, description="Figures found per page")
    tables_by_page: dict[int, list[TableContext]] = Field(default_factory=dict, description="Tables found per page")

    # Paragraph issues by page (for ParagraphAgent)
    page_artifacts_by_page: dict[int, list[PageArtifactIssue]] = Field(
        default_factory=dict, description="Page artifacts found per page"
    )
    footnote_issues_by_page: dict[int, list[FootnoteIssue]] = Field(
        default_factory=dict, description="Footnote issues found per page"
    )
    citation_issues_by_page: dict[int, list[CitationIssue]] = Field(
        default_factory=dict, description="Citation issues found per page"
    )
    list_issues_by_page: dict[int, list[ListIssue]] = Field(
        default_factory=dict, description="List issues found per page"
    )
    typography_issues_by_page: dict[int, list[TypographyIssue]] = Field(
        default_factory=dict, description="Typography issues found per page"
    )
    page_continuations: dict[int, bool] = Field(default_factory=dict, description="Whether each page ends mid-sentence")

    # Edge text for cross-page merge detection
    first_100_chars_by_page: dict[int, str] = Field(
        default_factory=dict, description="First 100 chars of each page for merge detection"
    )
    last_100_chars_by_page: dict[int, str] = Field(
        default_factory=dict, description="Last 100 chars of each page for merge detection"
    )

    class Config:
        arbitrary_types_allowed = True


# =============================================================================
# Per-Page Analysis Models (LLM Output)
# =============================================================================


class HeadingAnalysis(BaseModel):
    """Analysis of a single heading on the page."""

    text: str = Field(description="The heading text as it appears")
    current_level: int = Field(description="Current level in markdown (1-6)")
    correct_level: int = Field(description="What level it SHOULD be based on document structure")
    reasoning: str = Field(description="Brief reason if correction needed, empty if correct")


class FigureAnalysis(BaseModel):
    """Analysis of a figure on the page."""

    figure_index: int = Field(description="1-based index of figure on this page")
    appears_to_be: str = Field(description="What the figure appears to show (diagram, chart, photo, etc.)")
    surrounding_context: str = Field(description="Brief context from surrounding text")
    is_decorative: bool = Field(default=False, description="True if purely decorative (no content value)")


class TableAnalysis(BaseModel):
    """Analysis of a table on the page."""

    table_index: int = Field(description="1-based index of table on this page")
    appears_to_contain: str = Field(description="What data the table appears to contain")
    row_count_estimate: int = Field(default=0, description="Estimated number of rows")
    has_header: bool = Field(default=True, description="Whether table has header row")


class PageAnalysisOutput(BaseModel):
    """LLM output for analyzing a single page."""

    # For page 1 only
    document_title: str | None = Field(default=None, description="Document title (page 1 only)")
    document_type: str | None = Field(
        default=None, description="Document type: paper, report, syllabus, manual, slides, article, other"
    )

    # Headings on this page
    headings: list[HeadingAnalysis] = Field(default_factory=list, description="All headings found on this page")

    # Page summary
    summary: str = Field(description="2-3 sentence summary of what this page covers")

    # Domain terms found
    terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific terms, acronyms, proper nouns found",
    )

    # Figures
    figures: list[FigureAnalysis] = Field(default_factory=list, description="Figures found on this page")

    # Tables
    tables: list[TableAnalysis] = Field(default_factory=list, description="Tables found on this page")

    # Paragraph issues (for ParagraphAgent)
    page_artifacts: list[PageArtifactIssue] = Field(
        default_factory=list,
        description="Page break artifacts found (---, split words)",
    )
    footnote_issues: list[FootnoteIssue] = Field(
        default_factory=list,
        description="Footnote problems (missing definitions, misplaced)",
    )
    citation_issues: list[CitationIssue] = Field(
        default_factory=list,
        description="Citation linking problems",
    )
    list_issues: list[ListIssue] = Field(
        default_factory=list,
        description="List structure problems",
    )
    typography_issues: list[TypographyIssue] = Field(
        default_factory=list,
        description="Semantic formatting needing markup",
    )
    has_page_continuation: bool = Field(
        default=False,
        description="True if page ends mid-sentence (for cross-page merge)",
    )


# =============================================================================
# Page Chain Agent
# =============================================================================

PAGE_ANALYSIS_SYSTEM_PROMPT = """You are a document structure analyst processing one page at a time.

Your job is to analyze the current page and:
1. Identify all headings and determine their CORRECT level in the document hierarchy
2. Summarize what the page covers
3. Extract domain-specific terms for spell-checking
4. Describe any figures and tables
5. Detect paragraph-level issues

## Heading Level Rules

Headings follow a strict hierarchy based on section numbering:
- Document title = H1 (level 1) - only ONE per document
- Major sections ("1", "2", "3") = H2 (level 2)
- Subsections ("1.1", "2.1") = H3 (level 3)
- Sub-subsections ("1.1.1", "2.1.1") = H4 (level 4)
- Deeper nesting follows the pattern

IMPORTANT:
- If a heading has a section number, use it to determine the correct level
- "1 Introduction" → level 2 (one number = H2)
- "1.1 Background" → level 3 (two numbers = H3)
- "Abstract", "References", "Appendix" without numbers → typically H2
- The document title is the ONLY H1

## Context from Previous Pages

You'll receive the last heading from the previous page. Use this to:
- Ensure continuity (no unexpected jumps)
- Understand where we are in the document structure

## Output Requirements

1. **Headings**: List ALL headings on the page with current_level (what's in markdown)
   and correct_level (what it SHOULD be). Add reasoning if they differ.

2. **Summary**: 2-3 sentences about what this page covers.

3. **Terms**: Domain-specific words that should be in the spell-check dictionary.
   Include: product names, technical terms, acronyms, proper nouns.

4. **Figures**: For each figure, describe what it appears to show.

5. **Tables**: For each table, describe what data it contains.

6. **Page Artifacts**: Look for extraction artifacts:
   - `---`, `~~~`, `***` page break markers
   - Words split across lines with hyphens: `infor-` (end of line) `mation` (next line)
   - These are usually extraction errors, not intentional content

7. **Footnotes**: Look for footnote markers and issues:
   - Markers: `[^1]`, `¹`, `(1)`, `*`
   - Issues: marker without definition, misplaced definition, orphaned text

8. **Citations**: Look for citation patterns:
   - Numbered: `[1]`, `[2]`, `[1-3]`
   - Author-date: `(Smith, 2023)`, `(Smith & Jones, 2023)`
   - Note if citations appear unlinked to references

9. **List Structure**: Check lists for issues:
   - Inconsistent indentation (should be 2 spaces per level)
   - Mixed ordered/unordered at same nesting level
   - Broken numbering sequences (1, 2, 5 instead of 1, 2, 3)

10. **Typography**: Identify SEMANTIC formatting visible in the image but missing in markdown:
    - **Bold** for key terms, warnings, definitions (not just styling)
    - *Italic* for emphasis, foreign words, titles
    - `Code` for commands, technical terms
    - Only flag if the formatting conveys MEANING

11. **Page Continuity**: Set `has_page_continuation=True` if:
    - The page ends mid-sentence (no terminal punctuation)
    - A word appears to be split at the page boundary
    - This helps identify paragraphs that need merging across pages

For page 1 only: Also provide document_title and document_type.
"""


_page_analysis_agent: Agent[None, PageAnalysisOutput] | None = None


def _get_page_analysis_agent() -> Agent[None, PageAnalysisOutput]:
    """Get or create the page analysis agent."""
    global _page_analysis_agent

    if _page_analysis_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _page_analysis_agent = Agent(
            model=model,
            output_type=PageAnalysisOutput,
            system_prompt=PAGE_ANALYSIS_SYSTEM_PROMPT,
        )
        logger.info("Page analysis agent initialized")

    return _page_analysis_agent


def _build_page_prompt(
    page_num: int,
    markdown: str,
    state: PageChainState,
    total_pages: int,
) -> str:
    """Build the prompt for analyzing a single page."""

    # Extract current headings from markdown for reference
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = heading_pattern.findall(markdown)
    current_headings = [f"  - '{m[1].strip()}' (currently H{len(m[0])})" for m in matches]

    # Count figures and tables
    figure_count = len(re.findall(r"!\[", markdown))
    figure_count += len(re.findall(r"<!--\s*image", markdown, re.IGNORECASE))
    table_count = len(re.findall(r"^\|.*\|.*\|", markdown, re.MULTILINE))
    table_count += len(re.findall(r"<!--\s*table", markdown, re.IGNORECASE))

    # Build context section
    context_parts = []

    if page_num == 1:
        context_parts.append("This is PAGE 1 - identify the document title and type.")
    else:
        context_parts.append(f"This is page {page_num} of {total_pages}.")
        if state.last_heading:
            context_parts.append(f"Previous page ended with: '{state.last_heading.text}' (H{state.last_heading.level})")
        if state.document_title:
            context_parts.append(f"Document: '{state.document_title}' ({state.document_type.value})")

    prompt = f"""## Context
{chr(10).join(context_parts)}

## Headings Found on This Page
{chr(10).join(current_headings) if current_headings else "(no headings)"}

## Content Counts
- Figures: {figure_count}
- Tables: {table_count}

## Page Markdown
```markdown
{markdown[:8000]}
```
{"(truncated)" if len(markdown) > 8000 else ""}

Analyze this page. Determine if heading levels are correct based on section numbering.
"""
    return prompt


def _update_outline(
    outline: list[OutlineEntry],
    heading: HeadingAnalysis,
    page_num: int,
) -> list[OutlineEntry]:
    """Add a heading to the outline at the correct position.

    This maintains a proper hierarchical structure.
    """
    new_entry = OutlineEntry(
        heading=heading.text,
        level=heading.correct_level,
        page_start=page_num,
        page_end=page_num,
        children=[],
    )

    if not outline:
        return [new_entry]

    # For simplicity, we'll build a flat list and let consumers handle hierarchy
    # A more sophisticated approach would nest children properly
    return outline + [new_entry]


async def analyze_page(
    page_num: int,
    markdown: str,
    state: PageChainState,
    total_pages: int,
    capture_debug: bool = False,
) -> tuple[PageAnalysisOutput, int, int, LLMCallRecord]:
    """Analyze a single page with context from previous pages.

    Args:
        page_num: 1-indexed page number
        markdown: Markdown content for this page
        state: Current chain state from previous pages
        total_pages: Total pages in document
        capture_debug: If True, capture full prompt/response for debug bundle

    Returns:
        Tuple of (PageAnalysisOutput, input_tokens, output_tokens, llm_call_record)

    Raises:
        CircuitBreakerOpenError: If LLM circuit breaker is open
    """
    # Check circuit breaker before making LLM call
    llm_circuit_breaker.check_state()

    start_time = time.time()
    agent = _get_page_analysis_agent()
    prompt = _build_page_prompt(page_num, markdown, state, total_pages)

    try:
        result = await agent.run(prompt)
        output = result.output

        # Record success
        llm_circuit_breaker.record_success()

        usage = result.usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        duration_ms = int((time.time() - start_time) * 1000)

        # Create LLM call record
        cost_cents = calculate_estimated_cost(input_tokens, output_tokens)

        # Capture debug data if requested
        prompt_text = None
        response_raw = None
        model_id = None
        if capture_debug:
            prompt_text = serialize_text_prompt(prompt)
            response_raw = extract_raw_response(result)
            model_id = MODEL_TIER_MAP.get(ModelTier.EFFICIENT, "unknown")

        llm_call = LLMCallRecord(
            agent="planner",
            purpose=f"page_{page_num}_analysis",
            page=page_num,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
            prompt_text=prompt_text,
            response_raw=response_raw,
            model_id=model_id,
        )

        # Emit Prometheus metrics for this LLM call
        record_llm_call(
            agent="planner",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            duration_ms=duration_ms,
        )

        return output, input_tokens, output_tokens, llm_call

    except CircuitBreakerOpenError:
        # Re-raise circuit breaker errors
        raise
    except Exception as e:
        # Don't count rate limit errors as failures
        if not is_rate_limit_error(e):
            llm_circuit_breaker.record_failure()
        raise


def _normalize_for_matching(text: str) -> str:
    """Normalize heading text for fuzzy matching."""
    normalized = text.strip().lower()
    # Remove trailing punctuation
    normalized = re.sub(r"[:\.\!\?]+$", "", normalized)
    # Normalize whitespace
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _match_llm_heading_to_actual(
    llm_heading: HeadingAnalysis,
    actual_headings: list[tuple[str, int]],
) -> tuple[str, int] | None:
    """Match an LLM-returned heading to the actual heading from markdown.

    This handles cases where the LLM slightly modifies the heading text.

    Args:
        llm_heading: The heading analysis from LLM
        actual_headings: List of (text, level) from regex extraction

    Returns:
        Matching (text, level) tuple or None if no match
    """
    llm_normalized = _normalize_for_matching(llm_heading.text)

    # Try exact match first (normalized)
    for text, level in actual_headings:
        if _normalize_for_matching(text) == llm_normalized:
            return (text, level)

    # Try substring match (LLM might have truncated or expanded)
    for text, level in actual_headings:
        actual_normalized = _normalize_for_matching(text)
        if llm_normalized in actual_normalized or actual_normalized in llm_normalized:
            return (text, level)

    # Try matching by level if text is close
    for text, level in actual_headings:
        if level == llm_heading.current_level:
            # Check if they share significant words
            llm_words = set(llm_normalized.split())
            actual_words = set(_normalize_for_matching(text).split())
            if len(llm_words & actual_words) >= min(2, len(llm_words)):
                return (text, level)

    return None


def update_chain_state(
    state: PageChainState,
    page_num: int,
    output: PageAnalysisOutput,
    markdown: str = "",
) -> PageChainState:
    """Update chain state with results from analyzing a page.

    Uses the ACTUAL headings from markdown (via regex) for the outline,
    not the LLM's returned text. This ensures the outline matches what's
    actually in the markdown for verification.

    Args:
        state: Current chain state
        page_num: Page that was just analyzed
        output: Analysis output for the page
        markdown: Original markdown content for actual heading extraction

    Returns:
        Updated chain state
    """
    # Extract actual headings from markdown
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = heading_pattern.findall(markdown)
    actual_headings = [(m[1].strip(), len(m[0])) for m in matches]

    # Page 1: Set document info
    if page_num == 1 and output.document_title:
        state.document_title = output.document_title
        if output.document_type:
            try:
                state.document_type = DocumentType(output.document_type.lower())
            except ValueError:
                state.document_type = DocumentType.OTHER

    # Track which actual headings have been matched
    matched_actual_indices: set[int] = set()

    # Process LLM headings and match to actual headings
    for llm_heading in output.headings:
        # Find matching actual heading
        match = _match_llm_heading_to_actual(llm_heading, actual_headings)

        if match:
            actual_text, actual_level = match
            # Find the index of this match to avoid re-matching
            for i, (text, level) in enumerate(actual_headings):
                if i not in matched_actual_indices and text == actual_text and level == actual_level:
                    matched_actual_indices.add(i)
                    break

            # Create outline entry with ACTUAL text from markdown
            new_entry = OutlineEntry(
                heading=actual_text,  # Use actual text, not LLM text
                level=llm_heading.correct_level,
                page_start=page_num,
                page_end=page_num,
                children=[],
            )
            state.outline.append(new_entry)

            # Check if heading fix is needed
            if actual_level != llm_heading.correct_level:
                state.heading_fixes.append(
                    HeadingFix(
                        page=page_num,
                        current_text=actual_text,  # Use actual text
                        current_level=actual_level,
                        should_be_level=llm_heading.correct_level,
                        reason=llm_heading.reasoning or "Level mismatch based on section numbering",
                    )
                )
        else:
            # No match found - log warning and use LLM text as fallback
            logger.warning(
                f"Page {page_num}: Could not match LLM heading '{llm_heading.text}' to actual markdown headings"
            )
            # Still add to outline but this may cause verification issues
            new_entry = OutlineEntry(
                heading=llm_heading.text,
                level=llm_heading.correct_level,
                page_start=page_num,
                page_end=page_num,
                children=[],
            )
            state.outline.append(new_entry)

            if llm_heading.current_level != llm_heading.correct_level:
                state.heading_fixes.append(
                    HeadingFix(
                        page=page_num,
                        current_text=llm_heading.text,
                        current_level=llm_heading.current_level,
                        should_be_level=llm_heading.correct_level,
                        reason=llm_heading.reasoning or "Level mismatch based on section numbering",
                    )
                )

    # Update last heading (for context to next page)
    if state.outline:
        last_entry = state.outline[-1]
        state.last_heading = HeadingInChain(
            text=last_entry.heading,
            level=last_entry.level,
            page=page_num,
        )

    # Add terms to dictionary
    state.dictionary.update(output.terms)

    # Store page summary
    state.page_summaries[page_num] = output.summary

    # Store figure context
    if output.figures:
        state.figures_by_page[page_num] = [
            FigureContext(
                figure_index=fig.figure_index,
                appears_to_be=fig.appears_to_be,
                surrounding_text=fig.surrounding_context,
                is_decorative=fig.is_decorative,
            )
            for fig in output.figures
        ]

    # Store table context
    if output.tables:
        state.tables_by_page[page_num] = [
            TableContext(
                table_index=tbl.table_index,
                appears_to_contain=tbl.appears_to_contain,
                row_count=tbl.row_count_estimate,
                has_header=tbl.has_header,
            )
            for tbl in output.tables
        ]

    # Store paragraph issues
    if output.page_artifacts:
        state.page_artifacts_by_page[page_num] = output.page_artifacts

    if output.footnote_issues:
        state.footnote_issues_by_page[page_num] = output.footnote_issues

    if output.citation_issues:
        state.citation_issues_by_page[page_num] = output.citation_issues

    if output.list_issues:
        state.list_issues_by_page[page_num] = output.list_issues

    if output.typography_issues:
        state.typography_issues_by_page[page_num] = output.typography_issues

    # Store page continuation flag
    state.page_continuations[page_num] = output.has_page_continuation

    # Extract and store edge text for cross-page merge detection
    state.first_100_chars_by_page[page_num] = _extract_edge_text(markdown, "first")
    state.last_100_chars_by_page[page_num] = _extract_edge_text(markdown, "last")

    return state


async def run_page_chain(
    page_markdowns: dict[int, str],
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
) -> tuple[PageChainState, int, int, list[LLMCallRecord]]:
    """Run the page chain to analyze all pages sequentially.

    This is the main entry point. It processes pages in order,
    passing context from each page to the next.

    Args:
        page_markdowns: Dict mapping page number to markdown content
        event_bus: Optional event bus for streaming events
        capture_debug: If True, capture full prompt/response for debug bundle

    Returns:
        Tuple of (final_state, total_input_tokens, total_output_tokens, llm_calls)
    """
    state = PageChainState()
    total_input_tokens = 0
    total_output_tokens = 0
    llm_calls: list[LLMCallRecord] = []

    sorted_pages = sorted(page_markdowns.keys())
    total_pages = len(sorted_pages)

    logger.info(f"Starting page chain analysis for {total_pages} pages")

    for page_num in sorted_pages:
        markdown = page_markdowns[page_num]

        # Analyze this page
        output, input_tokens, output_tokens, llm_call = await analyze_page(
            page_num=page_num,
            markdown=markdown,
            state=state,
            total_pages=total_pages,
            capture_debug=capture_debug,
        )

        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        llm_calls.append(llm_call)

        # Update state with results (pass markdown for actual heading extraction)
        state = update_chain_state(state, page_num, output, markdown)

        # Emit event if we have an event bus
        if event_bus:
            from .events import PageSummarizedEvent

            event_bus.emit(
                PageSummarizedEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    summary=output.summary,
                    figures_found=len(output.figures),
                    tables_found=len(output.tables),
                    headings_found=len(output.headings),
                    heading_fixes_needed=sum(1 for h in output.headings if h.current_level != h.correct_level),
                )
            )

        logger.info(
            f"Page {page_num}/{total_pages}: "
            f"{len(output.headings)} headings, "
            f"{len(output.figures)} figures, "
            f"{len(output.tables)} tables, "
            f"{len(output.terms)} terms"
        )

    logger.info(
        f"Page chain complete: "
        f"{len(state.outline)} outline entries, "
        f"{len(state.heading_fixes)} heading fixes, "
        f"{len(state.dictionary)} dictionary terms"
    )

    return state, total_input_tokens, total_output_tokens, llm_calls


async def run_page_chain_streaming(
    page_markdowns: dict[int, str],
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
) -> AsyncGenerator[tuple[int, PageChainState, int, int, LLMCallRecord | None], None]:
    """Run the page chain and yield results after each page completes.

    This is the streaming version that yields incremental results as each page
    is analyzed, enabling execution to start on earlier pages while later pages
    are still being planned.

    Args:
        page_markdowns: Dict mapping page number to markdown content
        event_bus: Optional event bus for streaming events
        capture_debug: If True, capture full prompt/response for debug bundle

    Yields:
        Tuple of (page_num, updated_state, input_tokens, output_tokens, llm_call)
        after each page is analyzed. The state is cumulative - each yield contains
        all information gathered up to and including that page.
    """
    state = PageChainState()
    sorted_pages = sorted(page_markdowns.keys())
    total_pages = len(sorted_pages)

    logger.info(f"Starting streaming page chain analysis for {total_pages} pages")

    for page_num in sorted_pages:
        markdown = page_markdowns[page_num]

        # Analyze this page
        output, input_tokens, output_tokens, llm_call = await analyze_page(
            page_num=page_num,
            markdown=markdown,
            state=state,
            total_pages=total_pages,
            capture_debug=capture_debug,
        )

        # Update state with results (pass markdown for actual heading extraction)
        state = update_chain_state(state, page_num, output, markdown)

        # Emit event if we have an event bus
        if event_bus:
            from .events import PageSummarizedEvent

            event_bus.emit(
                PageSummarizedEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    summary=output.summary,
                    figures_found=len(output.figures),
                    tables_found=len(output.tables),
                    headings_found=len(output.headings),
                    heading_fixes_needed=sum(1 for h in output.headings if h.current_level != h.correct_level),
                )
            )

        logger.info(
            f"Page {page_num}/{total_pages} (streaming): "
            f"{len(output.headings)} headings, "
            f"{len(output.figures)} figures, "
            f"{len(output.tables)} tables, "
            f"{len(output.terms)} terms"
        )

        # Yield the result for this page immediately
        yield page_num, state, input_tokens, output_tokens, llm_call

    logger.info(
        f"Streaming page chain complete: "
        f"{len(state.outline)} outline entries, "
        f"{len(state.heading_fixes)} heading fixes, "
        f"{len(state.dictionary)} dictionary terms"
    )
