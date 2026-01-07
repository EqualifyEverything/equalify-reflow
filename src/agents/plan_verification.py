"""V5 Pipeline Plan Verification.

This module provides verification functions that compare the final markdown
against the original DocumentPlan to ensure processing completeness.

Verification checks:
    1. Heading structure - all outline headings exist at correct levels
    2. Figure completeness - all planned figures have alt-text
    3. Table completeness - all planned tables were transcribed
    4. Spelling - no spelling issues using document dictionary
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .models import DocumentPlan, OutlineEntry, PagePlan
from .validation import _check_spelling

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Heading Structure Verification
# =============================================================================


def _flatten_outline(
    entries: list[OutlineEntry],
    result: list[tuple[str, int]] | None = None,
) -> list[tuple[str, int]]:
    """Flatten a hierarchical outline into a list of (heading, level) tuples.

    Args:
        entries: List of outline entries (may have children)
        result: Accumulator for recursive calls

    Returns:
        Flat list of (heading_text, level) tuples
    """
    if result is None:
        result = []

    for entry in entries:
        result.append((entry.heading, entry.level))
        if entry.children:
            _flatten_outline(entry.children, result)

    return result


def _extract_headings_from_markdown(markdown: str) -> list[tuple[str, int]]:
    """Extract all headings from markdown content.

    Args:
        markdown: Markdown content to analyze

    Returns:
        List of (heading_text, level) tuples
    """
    headings: list[tuple[str, int]] = []

    # Match ATX-style headings (# Heading, ## Heading, etc.)
    # Pattern: start of line, 1-6 hashes, space, heading text
    pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    for match in pattern.finditer(markdown):
        hashes = match.group(1)
        text = match.group(2).strip()
        level = len(hashes)
        headings.append((text, level))

    return headings


def _normalize_heading(heading: str) -> str:
    """Normalize a heading for comparison.

    Removes extra whitespace, punctuation variations, and lowercases.

    Args:
        heading: Raw heading text

    Returns:
        Normalized heading for comparison
    """
    # Remove leading/trailing whitespace
    normalized = heading.strip()
    # Lowercase for case-insensitive comparison
    normalized = normalized.lower()
    # Remove trailing punctuation
    normalized = re.sub(r"[:\.]$", "", normalized)
    # Normalize whitespace
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def verify_heading_structure(
    final_markdown: str,
    plan: DocumentPlan,
) -> list[str]:
    """Verify headings match the DocumentPlan outline.

    Compares the headings found in the final markdown against the expected
    structure defined in the DocumentPlan. Checks for:
    - Missing headings (in plan but not in markdown)
    - Extra headings (in markdown but not in plan)
    - Wrong heading levels (heading exists but at different level)

    Args:
        final_markdown: The complete final markdown document
        plan: The DocumentPlan with expected structure

    Returns:
        List of issue strings describing discrepancies
    """
    issues: list[str] = []

    # Flatten the outline to get expected headings
    expected_headings = _flatten_outline(plan.structure.outline)

    if not expected_headings:
        logger.debug("No outline entries in plan, skipping heading verification")
        return issues

    # Extract actual headings from markdown
    actual_headings = _extract_headings_from_markdown(final_markdown)

    # Build normalized lookup for actual headings
    # Maps normalized heading -> list of (original_text, level) tuples
    actual_lookup: dict[str, list[tuple[str, int]]] = {}
    for text, level in actual_headings:
        normalized = _normalize_heading(text)
        if normalized not in actual_lookup:
            actual_lookup[normalized] = []
        actual_lookup[normalized].append((text, level))

    # Check each expected heading
    for expected_text, expected_level in expected_headings:
        normalized_expected = _normalize_heading(expected_text)

        if normalized_expected not in actual_lookup:
            issues.append(
                f"Missing heading: '{expected_text}' (expected H{expected_level})"
            )
        else:
            # Heading exists - check level
            matches = actual_lookup[normalized_expected]
            level_matched = any(level == expected_level for _, level in matches)

            if not level_matched:
                actual_levels = [level for _, level in matches]
                issues.append(
                    f"Wrong heading level: '{expected_text}' "
                    f"(expected H{expected_level}, found H{actual_levels[0]})"
                )

    # Build normalized lookup for expected headings to find extras
    expected_normalized = {_normalize_heading(text) for text, _ in expected_headings}

    # Check for extra headings (in markdown but not in plan)
    # Only flag H1-H3 as significant extras, lower levels are often subheadings
    for text, level in actual_headings:
        normalized = _normalize_heading(text)
        if normalized not in expected_normalized and level <= 3:
            # Skip common structural headings that might not be in outline
            skip_patterns = [
                "table of contents",
                "contents",
                "references",
                "bibliography",
                "appendix",
                "acknowledgments",
                "acknowledgements",
            ]
            if normalized not in skip_patterns:
                issues.append(
                    f"Extra heading not in plan: '{text}' (H{level})"
                )

    logger.debug(f"Heading verification found {len(issues)} issues")
    return issues


def fix_heading_levels(
    markdown: str,
    plan: DocumentPlan,
) -> tuple[str, list[str]]:
    """Directly fix heading levels based on DocumentPlan.

    This is a post-verification auto-fix that corrects heading levels
    using the outline as the source of truth. No LLM needed - just regex.

    Args:
        markdown: The markdown to fix
        plan: The DocumentPlan with expected structure

    Returns:
        Tuple of (fixed_markdown, list of fixes applied)
    """
    fixes_applied: list[str] = []

    # Flatten the outline to get expected headings with levels
    expected_headings = _flatten_outline(plan.structure.outline)

    if not expected_headings:
        return markdown, fixes_applied

    # Build a map of normalized heading text -> expected level
    expected_levels: dict[str, int] = {}
    for text, level in expected_headings:
        normalized = _normalize_heading(text)
        expected_levels[normalized] = level

    # Process markdown line by line to fix heading levels
    lines = markdown.split("\n")
    fixed_lines: list[str] = []

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            current_hashes = match.group(1)
            heading_text = match.group(2).strip()
            current_level = len(current_hashes)

            # Check if this heading has an expected level
            normalized = _normalize_heading(heading_text)
            if normalized in expected_levels:
                expected_level = expected_levels[normalized]

                if current_level != expected_level:
                    # Fix it!
                    new_hashes = "#" * expected_level
                    new_line = f"{new_hashes} {heading_text}"
                    fixed_lines.append(new_line)
                    fixes_applied.append(
                        f"Fixed '{heading_text}': H{current_level} → H{expected_level}"
                    )
                    logger.info(
                        f"Auto-fixed heading level: '{heading_text}' "
                        f"H{current_level} → H{expected_level}"
                    )
                else:
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)

    return "\n".join(fixed_lines), fixes_applied


def fix_heading_levels_per_page(
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
) -> tuple[dict[int, str], list[str]]:
    """Fix heading levels in each page markdown.

    Args:
        page_markdowns: Dict mapping page number to markdown
        plan: The DocumentPlan with expected structure

    Returns:
        Tuple of (fixed_page_markdowns, all fixes applied)
    """
    all_fixes: list[str] = []
    fixed_markdowns: dict[int, str] = {}

    for page_num, markdown in page_markdowns.items():
        fixed_md, fixes = fix_heading_levels(markdown, plan)
        fixed_markdowns[page_num] = fixed_md
        for fix in fixes:
            all_fixes.append(f"Page {page_num}: {fix}")

    if all_fixes:
        logger.info(f"Auto-fixed {len(all_fixes)} heading levels")

    return fixed_markdowns, all_fixes


# =============================================================================
# Figure Completeness Verification
# =============================================================================


def _count_figures_with_alt_text(markdown: str) -> int:
    """Count figures in markdown that have non-empty alt text.

    Args:
        markdown: Markdown content to analyze

    Returns:
        Count of figures with meaningful alt text
    """
    # Pattern for markdown images: ![alt-text](url)
    # We want non-empty alt text (not just ![](url) or decorative)
    pattern = re.compile(r"!\[([^\]]+)\]\([^)]+\)")

    count = 0
    for match in pattern.finditer(markdown):
        alt_text = match.group(1).strip()
        # Check for meaningful alt text (not just "image" or similar placeholders)
        if alt_text and alt_text.lower() not in ("image", "figure", "img"):
            count += 1

    return count


def _count_figure_placeholders(markdown: str) -> int:
    """Count unfilled figure placeholders in markdown.

    Args:
        markdown: Markdown content to analyze

    Returns:
        Count of unfilled figure placeholders
    """
    # Look for common placeholder patterns
    placeholder_patterns = [
        r"!\[\]\([^)]+\)",  # Empty alt text
        r"\[IMAGE\]",  # Common placeholder
        r"\[FIGURE\]",
        r"\[Figure \d+\]",
        r"<!-- figure placeholder -->",
        r"\[image description needed\]",
    ]

    total = 0
    for pattern in placeholder_patterns:
        matches = re.findall(pattern, markdown, re.IGNORECASE)
        total += len(matches)

    return total


def verify_figure_completeness(
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
) -> list[str]:
    """Verify all planned figures have alt-text.

    For each page in the DocumentPlan, checks if the expected number
    of figures (excluding decorative ones) have been processed with
    meaningful alt-text.

    Args:
        page_markdowns: Dict mapping page number to markdown content
        plan: The DocumentPlan with expected figures per page

    Returns:
        List of issue strings describing missing alt-text
    """
    issues: list[str] = []

    for page_num, page_plan in plan.pages.items():
        if not page_plan.figures:
            continue

        # Count non-decorative figures expected
        expected_count = sum(
            1 for fig in page_plan.figures if not fig.is_decorative
        )

        if expected_count == 0:
            continue

        # Get page markdown
        page_md = page_markdowns.get(page_num, "")
        if not page_md:
            issues.append(
                f"Page {page_num}: missing markdown (expected {expected_count} figures)"
            )
            continue

        # Count actual figures with alt text
        actual_count = _count_figures_with_alt_text(page_md)

        # Count remaining placeholders
        placeholder_count = _count_figure_placeholders(page_md)

        if actual_count < expected_count:
            missing = expected_count - actual_count
            issues.append(
                f"Page {page_num}: {missing} figure(s) missing alt-text "
                f"(expected {expected_count}, found {actual_count})"
            )

        if placeholder_count > 0:
            issues.append(
                f"Page {page_num}: {placeholder_count} unfilled figure placeholder(s)"
            )

    logger.debug(f"Figure verification found {len(issues)} issues")
    return issues


# =============================================================================
# Table Completeness Verification
# =============================================================================


def _count_markdown_tables(markdown: str) -> int:
    """Count properly formatted markdown tables.

    A markdown table has:
    - Header row with pipes
    - Separator row with dashes and pipes
    - One or more data rows

    Args:
        markdown: Markdown content to analyze

    Returns:
        Count of markdown tables
    """
    # Pattern for table separator row (required for valid markdown table)
    # Matches: |---|---|---| or | --- | --- | or variations
    separator_pattern = re.compile(r"^\s*\|[\s\-:]+\|[\s\-:|]+\|?\s*$", re.MULTILINE)

    # Count separator rows as proxy for tables
    # Each table has exactly one separator row
    matches = separator_pattern.findall(markdown)
    return len(matches)


def _count_table_placeholders(markdown: str) -> int:
    """Count unfilled table placeholders in markdown.

    Args:
        markdown: Markdown content to analyze

    Returns:
        Count of unfilled table placeholders
    """
    placeholder_patterns = [
        r"\[TABLE\]",
        r"\[Table \d+\]",
        r"<!-- table placeholder -->",
        r"\[table content needed\]",
        r"\[table transcription needed\]",
    ]

    total = 0
    for pattern in placeholder_patterns:
        matches = re.findall(pattern, markdown, re.IGNORECASE)
        total += len(matches)

    return total


def verify_table_completeness(
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
) -> list[str]:
    """Verify all planned tables were transcribed.

    For each page in the DocumentPlan, checks if the expected number
    of tables have been transcribed into markdown format.

    Args:
        page_markdowns: Dict mapping page number to markdown content
        plan: The DocumentPlan with expected tables per page

    Returns:
        List of issue strings describing missing tables
    """
    issues: list[str] = []

    for page_num, page_plan in plan.pages.items():
        if not page_plan.tables:
            continue

        expected_count = len(page_plan.tables)

        # Get page markdown
        page_md = page_markdowns.get(page_num, "")
        if not page_md:
            issues.append(
                f"Page {page_num}: missing markdown (expected {expected_count} tables)"
            )
            continue

        # Count actual markdown tables
        actual_count = _count_markdown_tables(page_md)

        # Count remaining placeholders
        placeholder_count = _count_table_placeholders(page_md)

        if actual_count < expected_count:
            missing = expected_count - actual_count
            issues.append(
                f"Page {page_num}: {missing} table(s) not transcribed "
                f"(expected {expected_count}, found {actual_count})"
            )

        if placeholder_count > 0:
            issues.append(
                f"Page {page_num}: {placeholder_count} unfilled table placeholder(s)"
            )

    logger.debug(f"Table verification found {len(issues)} issues")
    return issues


# =============================================================================
# Spelling Verification
# =============================================================================


def verify_spelling(
    final_markdown: str,
    plan: DocumentPlan,
) -> list[str]:
    """Verify spelling using the document dictionary.

    Uses the document's full_dictionary to check for spelling issues
    in the final markdown. Domain-specific terms from the dictionary
    are not flagged as errors.

    Args:
        final_markdown: The complete final markdown document
        plan: The DocumentPlan with the full dictionary

    Returns:
        List of issue strings describing spelling problems
    """
    issues: list[str] = []

    # Use the validation module's spell checker
    spell_issues = _check_spelling(final_markdown, plan.full_dictionary)

    for spell_issue in spell_issues:
        if spell_issue.suggestion:
            issues.append(
                f"Possible misspelling: '{spell_issue.word}' "
                f"(suggestion: '{spell_issue.suggestion}')"
            )
        else:
            issues.append(f"Unknown word: '{spell_issue.word}'")

    logger.debug(f"Spelling verification found {len(issues)} issues")
    return issues


# =============================================================================
# Comprehensive Verification
# =============================================================================


def verify_against_plan(
    final_markdown: str,
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
) -> list[str]:
    """Run all verification checks against the DocumentPlan.

    This is a convenience function that runs all verification checks
    and aggregates the results.

    Args:
        final_markdown: The complete final markdown document
        page_markdowns: Dict mapping page number to markdown content
        plan: The DocumentPlan to verify against

    Returns:
        List of all issues found across all verification checks
    """
    all_issues: list[str] = []

    # Heading structure
    heading_issues = verify_heading_structure(final_markdown, plan)
    if heading_issues:
        all_issues.append("=== Heading Structure Issues ===")
        all_issues.extend(heading_issues)

    # Figure completeness
    figure_issues = verify_figure_completeness(page_markdowns, plan)
    if figure_issues:
        all_issues.append("=== Figure Completeness Issues ===")
        all_issues.extend(figure_issues)

    # Table completeness
    table_issues = verify_table_completeness(page_markdowns, plan)
    if table_issues:
        all_issues.append("=== Table Completeness Issues ===")
        all_issues.extend(table_issues)

    # Spelling
    spelling_issues = verify_spelling(final_markdown, plan)
    if spelling_issues:
        all_issues.append("=== Spelling Issues ===")
        all_issues.extend(spelling_issues)

    total_issues = (
        len(heading_issues)
        + len(figure_issues)
        + len(table_issues)
        + len(spelling_issues)
    )

    logger.info(f"Plan verification complete: {total_issues} total issues found")

    return all_issues
