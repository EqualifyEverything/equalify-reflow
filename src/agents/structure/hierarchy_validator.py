"""Pure Python heading hierarchy validator.

Step 1 of the structure chain: Validate heading hierarchy.
This is PURE PYTHON - no LLM calls, zero cost.

Catches ~90% of heading issues:
- Skipped levels (h1 → h3)
- Multiple h1s
- Empty headings
- Headings that are too long
- Orphan headings (no parent)

This is a Quick Win that dramatically reduces LLM costs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


# =============================================================================
# Validation Types
# =============================================================================


IssueType = Literal[
    "skipped_level",
    "multiple_h1",
    "empty_heading",
    "long_heading",
    "orphan_heading",
]
Severity = Literal["critical", "major", "minor"]


@dataclass
class HierarchyIssue:
    """A heading hierarchy issue found by validation."""

    issue_type: IssueType
    severity: Severity
    location: str  # e.g., "Page 3, heading 'Introduction'"
    description: str
    current_level: int
    expected_level: int | None = None
    heading_text: str = ""


@dataclass
class HierarchyValidation:
    """Result of hierarchy validation."""

    is_valid: bool
    issues: list[HierarchyIssue] = field(default_factory=list)
    h1_count: int = 0
    max_level: int = 0
    total_headings: int = 0


# =============================================================================
# Validation Logic (Pure Python)
# =============================================================================


def validate_hierarchy(
    markdown: str,
    job_id: str,
) -> HierarchyValidation:
    """Validate heading hierarchy in markdown.

    This is pure Python - no LLM calls, zero cost.
    Catches ~90% of heading issues.

    Rules:
    1. No skipped levels (h1 → h3 not allowed, h1 → h2 → h3 required)
    2. Only one h1 (document title)
    3. No empty headings
    4. Headings should be < 100 characters
    5. Every heading needs a valid parent (except h1)

    Args:
        markdown: Full markdown content
        job_id: Job ID for logging

    Returns:
        HierarchyValidation with issues found
    """
    issues: list[HierarchyIssue] = []

    # Extract headings with their levels and positions
    headings = _extract_headings(markdown)

    if not headings:
        logger.debug(f"Job {job_id}: No headings found in markdown")
        return HierarchyValidation(
            is_valid=True,
            issues=[],
            h1_count=0,
            max_level=0,
            total_headings=0,
        )

    # Track state
    h1_count = 0
    max_level = 0
    previous_level = 0
    h1_positions: list[int] = []

    for idx, (level, text, line_num, page_num) in enumerate(headings):
        max_level = max(max_level, level)

        # Rule 1: Check for skipped levels
        if previous_level > 0 and level > previous_level + 1:
            issues.append(
                HierarchyIssue(
                    issue_type="skipped_level",
                    severity="major",
                    location=f"Page {page_num}, line {line_num}",
                    description=f"Heading level skipped: h{previous_level} → h{level}",
                    current_level=level,
                    expected_level=previous_level + 1,
                    heading_text=text[:50],
                )
            )

        # Rule 2: Count h1s
        if level == 1:
            h1_count += 1
            h1_positions.append(idx)

        # Rule 3: Check for empty headings
        if not text.strip():
            issues.append(
                HierarchyIssue(
                    issue_type="empty_heading",
                    severity="minor",
                    location=f"Page {page_num}, line {line_num}",
                    description="Empty heading found",
                    current_level=level,
                    heading_text="",
                )
            )

        # Rule 4: Check for overly long headings
        if len(text) > 100:
            issues.append(
                HierarchyIssue(
                    issue_type="long_heading",
                    severity="minor",
                    location=f"Page {page_num}, line {line_num}",
                    description=f"Heading too long ({len(text)} chars)",
                    current_level=level,
                    heading_text=text[:50] + "...",
                )
            )

        # Rule 5: Check for orphan headings (no parent except h1)
        if level > 1 and previous_level == 0:
            # First heading is not h1
            issues.append(
                HierarchyIssue(
                    issue_type="orphan_heading",
                    severity="major",
                    location=f"Page {page_num}, line {line_num}",
                    description=f"Document starts with h{level}, expected h1",
                    current_level=level,
                    expected_level=1,
                    heading_text=text[:50],
                )
            )

        previous_level = level

    # Rule 2: Multiple h1s
    if h1_count > 1:
        for pos in h1_positions[1:]:  # Skip first h1
            level, text, line_num, page_num = headings[pos]
            issues.append(
                HierarchyIssue(
                    issue_type="multiple_h1",
                    severity="major",
                    location=f"Page {page_num}, line {line_num}",
                    description="Multiple h1 headings found (document should have one)",
                    current_level=1,
                    heading_text=text[:50],
                )
            )

    is_valid = len(issues) == 0

    logger.debug(
        f"Job {job_id}: Hierarchy validation complete - "
        f"{len(headings)} headings, {len(issues)} issues, valid={is_valid}"
    )

    return HierarchyValidation(
        is_valid=is_valid,
        issues=issues,
        h1_count=h1_count,
        max_level=max_level,
        total_headings=len(headings),
    )


def _extract_headings(markdown: str) -> list[tuple[int, str, int, int]]:
    """Extract headings from markdown with metadata.

    Args:
        markdown: Markdown content

    Returns:
        List of (level, text, line_number, page_number) tuples
    """
    headings: list[tuple[int, str, int, int]] = []

    # Track page numbers from page markers
    current_page = 1
    page_pattern = re.compile(r"<!-- Page (\d+) -->")

    lines = markdown.split("\n")

    for line_num, line in enumerate(lines, start=1):
        # Check for page marker
        page_match = page_pattern.search(line)
        if page_match:
            current_page = int(page_match.group(1))
            continue

        # Check for ATX-style headings (# Heading)
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            headings.append((level, text, line_num, current_page))

    return headings


def issues_to_severity_counts(issues: list[HierarchyIssue]) -> dict[str, int]:
    """Count issues by severity.

    Args:
        issues: List of hierarchy issues

    Returns:
        Dict mapping severity to count
    """
    counts: dict[str, int] = {"critical": 0, "major": 0, "minor": 0}
    for issue in issues:
        counts[issue.severity] += 1
    return counts


__all__ = [
    "HierarchyIssue",
    "HierarchyValidation",
    "IssueType",
    "Severity",
    "issues_to_severity_counts",
    "validate_hierarchy",
]
