"""Plan Verification - Verify Processing Against Document Plan.

This module provides verification functions that compare the final markdown
against the original DocumentPlan to ensure processing completeness.

Verification checks:
    1. Heading structure - all outline headings exist at correct levels
    2. Figure completeness - all planned figures have alt-text
    3. Table completeness - all planned tables were transcribed
    4. Table accuracy - transcribed tables match source (vision-based)
    5. Spelling - no spelling issues using document dictionary
"""

from __future__ import annotations

import asyncio
import logging
import re
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .models import DocumentPlan, OutlineEntry
from .validation import _check_spelling

if TYPE_CHECKING:
    from PIL import Image

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
            issues.append(f"Missing heading: '{expected_text}' (expected H{expected_level})")
        else:
            # Heading exists - check level
            matches = actual_lookup[normalized_expected]
            level_matched = any(level == expected_level for _, level in matches)

            if not level_matched:
                actual_levels = [level for _, level in matches]
                issues.append(
                    f"Wrong heading level: '{expected_text}' (expected H{expected_level}, found H{actual_levels[0]})"
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
                issues.append(f"Extra heading not in plan: '{text}' (H{level})")

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
                    fixes_applied.append(f"Fixed '{heading_text}': H{current_level} → H{expected_level}")
                    logger.info(f"Auto-fixed heading level: '{heading_text}' H{current_level} → H{expected_level}")
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
        expected_count = sum(1 for fig in page_plan.figures if not fig.is_decorative)

        if expected_count == 0:
            continue

        # Get page markdown
        page_md = page_markdowns.get(page_num, "")
        if not page_md:
            issues.append(f"Page {page_num}: missing markdown (expected {expected_count} figures)")
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
            issues.append(f"Page {page_num}: {placeholder_count} unfilled figure placeholder(s)")

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
            issues.append(f"Page {page_num}: missing markdown (expected {expected_count} tables)")
            continue

        # Count actual markdown tables
        actual_count = _count_markdown_tables(page_md)

        # Count remaining placeholders
        placeholder_count = _count_table_placeholders(page_md)

        if actual_count < expected_count:
            missing = expected_count - actual_count
            issues.append(
                f"Page {page_num}: {missing} table(s) not transcribed (expected {expected_count}, found {actual_count})"
            )

        if placeholder_count > 0:
            issues.append(f"Page {page_num}: {placeholder_count} unfilled table placeholder(s)")

    logger.debug(f"Table verification found {len(issues)} issues")
    return issues


# =============================================================================
# Table Accuracy Verification (Vision-Based)
# =============================================================================


class TableMatchResult(BaseModel):
    """Result from vision-based table comparison."""

    matches: bool = Field(description="True if table data matches source image")
    mismatches: list[str] = Field(
        default_factory=list,
        description="List of specific mismatches found (cell values, missing data, etc.)",
    )
    confidence: float = Field(
        default=0.9,
        description="Confidence level of the comparison (0-1)",
    )


def _extract_nth_table(markdown: str, n: int) -> str | None:
    """Extract the nth markdown table from content (1-indexed).

    Args:
        markdown: The markdown content to search
        n: The 1-indexed table number to extract

    Returns:
        The markdown table text, or None if not found
    """
    # Find all table blocks (sequences of lines starting/ending with |)
    table_pattern = re.compile(
        r"(?:^\|.+\|$\n?)+",
        re.MULTILINE,
    )

    tables = table_pattern.findall(markdown)
    if 1 <= n <= len(tables):
        return tables[n - 1].strip()

    return None


async def verify_table_accuracy_vision(
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    plan: DocumentPlan,
    max_concurrent: int = 3,
) -> list[str]:
    """Verify transcribed table content matches source PDF using vision model.

    For each transcribed table, this function:
    1. Crops the original table image from the page
    2. Extracts the transcribed markdown table
    3. Asks a vision model if the data matches

    This catches errors like:
    - Wrong cell values (numbers, dates, text)
    - Missing rows or columns
    - Data in wrong positions
    - Truncated content

    Args:
        page_markdowns: Final markdown per page (1-indexed)
        page_images: Source page images (1-indexed)
        element_bboxes: Bounding boxes keyed by (page, "table", index)
        page_width: Page width for coordinate scaling
        plan: Document plan with table information
        max_concurrent: Maximum parallel vision API calls

    Returns:
        List of issue strings for tables that don't match source
    """
    from pydantic_ai import Agent, BinaryContent
    from pydantic_ai.models.bedrock import BedrockConverseModel

    from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
    from src.utils.image_utils import crop_element

    issues: list[str] = []

    # Collect tables to verify: (page_num, table_idx, table_md, cropped_image)
    tables_to_verify: list[tuple[int, int, str, Image.Image]] = []

    for page_num, page_plan in plan.pages.items():
        if not page_plan.tables:
            continue

        page_md = page_markdowns.get(page_num, "")
        page_img = page_images.get(page_num)

        if not page_md or not page_img:
            continue

        for table in page_plan.tables:
            # Extract the transcribed markdown table
            table_md = _extract_nth_table(page_md, table.table_index)

            if not table_md:
                # No transcribed table - this is caught by completeness check
                continue

            # Get the bounding box for cropping
            bbox_key = (page_num, "table", table.table_index)
            if bbox_key not in element_bboxes:
                logger.warning(f"No bounding box for table {table.table_index} on page {page_num}")
                continue

            # Crop the table from the source image
            try:
                cropped = crop_element(page_img, element_bboxes[bbox_key], page_width)
                tables_to_verify.append((page_num, table.table_index, table_md, cropped))
            except Exception as e:
                logger.warning(f"Failed to crop table {table.table_index} on page {page_num}: {e}")

    if not tables_to_verify:
        logger.debug("No tables to verify for accuracy")
        return issues

    logger.info(f"Verifying accuracy of {len(tables_to_verify)} tables")

    # Create the vision verification agent
    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
    agent = Agent(
        model=model,
        output_type=TableMatchResult,
        system_prompt="""You are verifying that a markdown table accurately represents the data in a source image.

Compare the markdown table with the source table image carefully.

Check for:
1. **Numeric accuracy**: All numbers, percentages, and values match exactly
2. **Text accuracy**: All text content matches (accounting for OCR-style differences)
3. **Completeness**: No missing rows or columns
4. **Structure**: Data is in the correct cells/positions
5. **Headers**: Column and row headers match

Return:
- matches=true if the table accurately represents the source data
- matches=false with specific mismatches if there are errors

Be lenient with:
- Minor whitespace differences
- Case differences in text (unless significant)
- Formatting differences (bold, italic)

Be strict with:
- Numeric values (must be exact)
- Missing or extra data
- Data in wrong cells""",
    )

    # Run verification in parallel with semaphore
    semaphore = asyncio.Semaphore(max_concurrent)

    async def verify_single_table(
        page_num: int,
        table_idx: int,
        table_md: str,
        cropped: Image.Image,
    ) -> str | None:
        """Verify a single table. Returns issue string or None if OK."""
        async with semaphore:
            try:
                # Convert image to BinaryContent
                buffer = BytesIO()
                cropped.save(buffer, format="PNG")
                image_content = BinaryContent(
                    data=buffer.getvalue(),
                    media_type="image/png",
                )

                # Build the verification prompt
                prompt = f"""Compare this transcribed markdown table with the source image:

```markdown
{table_md}
```

Does this table accurately represent the data shown in the image?
Report any mismatches in cell values, missing data, or structural differences."""

                result = await agent.run([image_content, prompt])

                if not result.output.matches:
                    # Build issue description
                    mismatch_summary = "; ".join(result.output.mismatches[:3])
                    if len(result.output.mismatches) > 3:
                        mismatch_summary += f" (+{len(result.output.mismatches) - 3} more)"

                    return f"Page {page_num} table {table_idx}: data mismatch - {mismatch_summary}"

            except Exception as e:
                logger.warning(f"Table accuracy verification failed for page {page_num} table {table_idx}: {e}")
                # Don't fail the whole process for verification errors
                return None

        return None

    # Run all verifications
    tasks = [
        verify_single_table(page_num, table_idx, table_md, cropped)
        for page_num, table_idx, table_md, cropped in tables_to_verify
    ]

    results = await asyncio.gather(*tasks)
    issues.extend([r for r in results if r is not None])

    logger.debug(f"Table accuracy verification found {len(issues)} issues")
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
            issues.append(f"Possible misspelling: '{spell_issue.word}' (suggestion: '{spell_issue.suggestion}')")
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

    total_issues = len(heading_issues) + len(figure_issues) + len(table_issues) + len(spelling_issues)

    logger.info(f"Plan verification complete: {total_issues} total issues found")

    return all_issues
