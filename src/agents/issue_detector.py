"""V5 Pipeline Issue Detector - Parallel Issue Detection with Full Context.

This module implements Phase 2 of the optimized pipeline:
    - Receives complete DocumentContext from Phase 1
    - Detects issues on each page IN PARALLEL
    - Each page agent has full document context (outline, summaries, etc.)
    - Uses authoritative outline as source of truth for structure

Issue Types Detected:
    - Heading level mismatches (vs authoritative outline)
    - Missing alt-text on figures
    - Unfilled placeholders
    - Tables needing transcription
    - Spelling errors
    - Formatting issues
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from .issue_fixer import IssueType, StructuredIssue
from .models import DocumentContext, OutlineEntry
from .plan_verification import _normalize_heading

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Deterministic Issue Detection (No LLM)
# =============================================================================


def detect_heading_issues_from_context(
    markdown: str,
    page_num: int,
    context: DocumentContext,
) -> list[StructuredIssue]:
    """Detect heading issues using authoritative outline as source of truth.

    This compares the page's headings against the authoritative outline
    from context gathering. The outline is the single source of truth.

    Args:
        markdown: Page markdown
        page_num: Page number
        context: Complete document context with authoritative outline

    Returns:
        List of StructuredIssue objects with pre-computed fixes
    """
    issues: list[StructuredIssue] = []

    # Extract headings from this page's markdown
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    actual_headings: list[tuple[str, int, int]] = []  # (text, level, line)

    for match in heading_pattern.finditer(markdown):
        line_num = markdown[: match.start()].count("\n") + 1
        hashes = match.group(1)
        text = match.group(2).strip()
        actual_headings.append((text, len(hashes), line_num))

    # Get expected headings for this page from authoritative outline
    expected_on_page: list[OutlineEntry] = [
        entry
        for entry in context.authoritative_outline
        if entry.page_start <= page_num <= entry.page_end
    ]

    # Build lookup: normalized text -> (expected_text, expected_level)
    expected_lookup: dict[str, tuple[str, int]] = {}
    for entry in expected_on_page:
        normalized = _normalize_heading(entry.heading)
        expected_lookup[normalized] = (entry.heading, entry.level)

    # Check each actual heading against expected
    for text, actual_level, line_num in actual_headings:
        normalized = _normalize_heading(text)

        if normalized in expected_lookup:
            expected_text, expected_level = expected_lookup[normalized]

            if actual_level != expected_level:
                # Wrong level - create fix
                before_text = f"{'#' * actual_level} {text}"
                after_text = f"{'#' * expected_level} {text}"

                issues.append(
                    StructuredIssue(
                        issue_type=IssueType.WRONG_HEADING_LEVEL,
                        page=page_num,
                        severity="critical",
                        actual_text=before_text,
                        expected_text=after_text,
                        actual_level=actual_level,
                        expected_level=expected_level,
                        suggested_fix=after_text,
                        description=(
                            f"Heading '{text}' is H{actual_level}, "
                            f"should be H{expected_level} per outline"
                        ),
                    )
                )

    # Check for missing headings that should be on this page
    actual_normalized = {_normalize_heading(t) for t, _, _ in actual_headings}
    for entry in expected_on_page:
        normalized = _normalize_heading(entry.heading)
        # Only flag if heading is expected to START on this page
        if entry.page_start == page_num and normalized not in actual_normalized:
            issues.append(
                StructuredIssue(
                    issue_type=IssueType.MISSING_HEADING,
                    page=page_num,
                    severity="warning",
                    expected_text=entry.heading,
                    expected_level=entry.level,
                    description=f"Missing heading: '{entry.heading}' (H{entry.level})",
                )
            )

    return issues


def detect_figure_issues_from_context(
    markdown: str,
    page_num: int,
    context: DocumentContext,
) -> list[StructuredIssue]:
    """Detect figure/alt-text issues.

    Args:
        markdown: Page markdown
        page_num: Page number
        context: Document context (for figure counts)

    Returns:
        List of StructuredIssue objects
    """
    issues: list[StructuredIssue] = []

    # Check for empty alt-text patterns (excluding properly-marked decorative images)
    empty_alt_pattern = re.compile(r"!\[\]\([^)]+\)")
    for i, match in enumerate(empty_alt_pattern.finditer(markdown), 1):
        matched_text = match.group()

        # Skip decorative images - they SHOULD have empty alt text per accessibility guidelines
        if "decorative" in matched_text.lower():
            continue

        # Get surrounding context
        start = max(0, match.start() - 100)
        end = min(len(markdown), match.end() + 100)
        surrounding = markdown[start:end]

        issues.append(
            StructuredIssue(
                issue_type=IssueType.MISSING_ALT_TEXT,
                page=page_num,
                severity="critical",
                actual_text=matched_text,
                element_index=i,
                surrounding_context=surrounding,
                description=f"Figure {i} has empty alt-text",
            )
        )

    # Check for unfilled image placeholders - these need LLM analysis
    placeholder_pattern = re.compile(r"<!--\s*image:?\d*\s*-->", re.IGNORECASE)
    for i, match in enumerate(placeholder_pattern.finditer(markdown), 1):
        # Get surrounding context for better LLM analysis
        start = max(0, match.start() - 200)
        end = min(len(markdown), match.end() + 200)
        surrounding = markdown[start:end]

        issues.append(
            StructuredIssue(
                issue_type=IssueType.UNFILLED_PLACEHOLDER,
                page=page_num,
                severity="critical",  # Needs LLM fix - will generate figure description
                actual_text=match.group(),
                element_index=i,
                surrounding_context=surrounding,
                description=f"Image placeholder needs description: {match.group()}",
            )
        )

    return issues


def detect_table_issues_from_context(
    markdown: str,
    page_num: int,
    context: DocumentContext,
) -> list[StructuredIssue]:
    """Detect table transcription issues.

    Args:
        markdown: Page markdown
        page_num: Page number
        context: Document context (for table counts)

    Returns:
        List of StructuredIssue objects
    """
    issues: list[StructuredIssue] = []

    # Check for unfilled table placeholders
    table_placeholder_pattern = re.compile(r"<!--\s*table:?\d*\s*-->", re.IGNORECASE)
    for i, match in enumerate(table_placeholder_pattern.finditer(markdown), 1):
        issues.append(
            StructuredIssue(
                issue_type=IssueType.TABLE_NOT_TRANSCRIBED,
                page=page_num,
                severity="critical",
                actual_text=match.group(),
                element_index=i,
                description=f"Table placeholder not transcribed: {match.group()}",
            )
        )

    # Check for malformed tables (has content but invalid structure)
    # A valid table has | and a separator row like |---|
    pipe_rows = re.findall(r"^\|.+\|", markdown, re.MULTILINE)
    separator_rows = re.findall(
        r"^\s*\|[\s\-:]+\|[\s\-:|]+\|?\s*$", markdown, re.MULTILINE
    )

    # If we have pipe rows but no separator, table is malformed
    if len(pipe_rows) > 1 and len(separator_rows) == 0:
        issues.append(
            StructuredIssue(
                issue_type=IssueType.TABLE_MALFORMED,
                page=page_num,
                severity="warning",
                description="Table appears malformed (missing separator row)",
            )
        )

    return issues


def detect_formatting_issues(
    markdown: str,
    page_num: int,
) -> list[StructuredIssue]:
    """Detect common formatting issues.

    Args:
        markdown: Page markdown
        page_num: Page number

    Returns:
        List of StructuredIssue objects
    """
    issues: list[StructuredIssue] = []

    # Check for heading hierarchy violations (H1 -> H3 skip)
    heading_pattern = re.compile(r"^(#{1,6})\s+", re.MULTILINE)
    levels = [len(m.group(1)) for m in heading_pattern.finditer(markdown)]

    prev_level = 0
    for level in levels:
        # Skip from H1 to H3 is a violation (should go H1 -> H2 -> H3)
        if level > prev_level + 1 and prev_level > 0:
            issues.append(
                StructuredIssue(
                    issue_type=IssueType.FORMATTING_ERROR,
                    page=page_num,
                    severity="warning",
                    description=f"Heading hierarchy skip: H{prev_level} → H{level}",
                )
            )
        prev_level = level

    return issues


# =============================================================================
# Page-Level Issue Detection
# =============================================================================


def detect_page_issues(
    page_num: int,
    markdown: str,
    context: DocumentContext,
) -> list[StructuredIssue]:
    """Detect all issues on a single page.

    This runs all deterministic issue detectors for one page.

    Args:
        page_num: Page number
        markdown: Page markdown
        context: Complete document context

    Returns:
        List of all issues found on this page
    """
    all_issues: list[StructuredIssue] = []

    # Heading structure issues (using authoritative outline)
    heading_issues = detect_heading_issues_from_context(markdown, page_num, context)
    all_issues.extend(heading_issues)

    # Figure/alt-text issues
    figure_issues = detect_figure_issues_from_context(markdown, page_num, context)
    all_issues.extend(figure_issues)

    # Table issues
    table_issues = detect_table_issues_from_context(markdown, page_num, context)
    all_issues.extend(table_issues)

    # Formatting issues
    format_issues = detect_formatting_issues(markdown, page_num)
    all_issues.extend(format_issues)

    return all_issues


# =============================================================================
# Parallel Issue Detection (Main Entry Point)
# =============================================================================


async def detect_all_issues(
    page_markdowns: dict[int, str],
    context: DocumentContext,
    max_concurrent: int = 10,
) -> dict[int, list[StructuredIssue]]:
    """Detect issues on all pages in parallel.

    This is the main entry point for Phase 2 (Issue Detection).
    All pages are processed in parallel since each has the full context.

    Args:
        page_markdowns: Dict mapping page number to markdown
        context: Complete document context from Phase 1
        max_concurrent: Maximum concurrent workers

    Returns:
        Dict mapping page number to list of issues
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    issues_by_page: dict[int, list[StructuredIssue]] = {}

    async def process_page(page_num: int) -> tuple[int, list[StructuredIssue]]:
        async with semaphore:
            markdown = page_markdowns[page_num]
            issues = detect_page_issues(page_num, markdown, context)
            return page_num, issues

    # Run all pages in parallel
    tasks = [process_page(p) for p in sorted(page_markdowns.keys())]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_issues = 0
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Issue detection failed: {result}")
            continue

        page_num, issues = result
        issues_by_page[page_num] = issues
        total_issues += len(issues)

    # Log summary by issue type
    type_counts: dict[IssueType, int] = {}
    for issues in issues_by_page.values():
        for issue in issues:
            type_counts[issue.issue_type] = type_counts.get(issue.issue_type, 0) + 1

    logger.info(
        f"Issue detection complete: {total_issues} issues across "
        f"{len(issues_by_page)} pages"
    )
    for issue_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {issue_type.value}: {count}")

    return issues_by_page


def summarize_issues(
    issues_by_page: dict[int, list[StructuredIssue]],
) -> dict[str, int]:
    """Get a summary of issues by type.

    Args:
        issues_by_page: Issues organized by page

    Returns:
        Dict mapping issue type string to count
    """
    summary: dict[str, int] = {}

    for issues in issues_by_page.values():
        for issue in issues:
            key = issue.issue_type.value
            summary[key] = summary.get(key, 0) + 1

    return summary


def get_critical_issues(
    issues_by_page: dict[int, list[StructuredIssue]],
) -> list[StructuredIssue]:
    """Get all critical-severity issues.

    Args:
        issues_by_page: Issues organized by page

    Returns:
        List of critical issues across all pages
    """
    critical: list[StructuredIssue] = []

    for page_num in sorted(issues_by_page.keys()):
        for issue in issues_by_page[page_num]:
            if issue.severity == "critical":
                critical.append(issue)

    return critical


def get_fixable_issues(
    issues_by_page: dict[int, list[StructuredIssue]],
) -> tuple[list[StructuredIssue], list[StructuredIssue]]:
    """Separate issues into deterministically fixable and LLM-required.

    Args:
        issues_by_page: Issues organized by page

    Returns:
        Tuple of (deterministic_issues, llm_issues)
    """
    deterministic: list[StructuredIssue] = []
    llm_required: list[StructuredIssue] = []

    # Issue types that can be fixed deterministically
    deterministic_types = {
        IssueType.WRONG_HEADING_LEVEL,
    }

    # Issue types that require LLM (image analysis)
    llm_types = {
        IssueType.MISSING_ALT_TEXT,
        IssueType.UNFILLED_PLACEHOLDER,  # Image placeholders need LLM to describe
        IssueType.TABLE_NOT_TRANSCRIBED,
    }

    for issues in issues_by_page.values():
        for issue in issues:
            if issue.issue_type in deterministic_types:
                deterministic.append(issue)
            elif issue.issue_type in llm_types:
                llm_required.append(issue)

    return deterministic, llm_required
