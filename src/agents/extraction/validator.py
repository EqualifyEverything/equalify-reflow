"""Pure Python validation for extraction output.

This module provides zero-LLM-cost validation of extraction results.
Validation is heuristic-based, which is more reliable than AI-provided
confidence scores.
"""

from __future__ import annotations

import logging
import re

from src.shared.models.remediation import DocumentManifest, HeadingTree

from .models import ExtractionMetrics, ValidationIssue

logger = logging.getLogger(__name__)

# Regex patterns for parsing markdown
PAGE_MARKER_PATTERN = re.compile(r"<!--\s*Page\s+(\d+)\s*-->")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
IMAGE_PLACEHOLDER_PATTERN = re.compile(r"!\[.*?\]\(.*?\)")
TABLE_ROW_PATTERN = re.compile(r"^\|.*\|$", re.MULTILINE)
UNCLEAR_MARKER_PATTERN = re.compile(r"\[\?\]")

# Confidence calculation weights
CONFIDENCE_WEIGHTS = {
    "missing_page": -0.15,       # Per missing page
    "unclear_marker": -0.02,    # Per [?] marker
    "heading_mismatch": -0.10,  # Per heading order issue
    "short_page": -0.05,        # Per suspiciously short page
    "no_headings": -0.10,       # If no headings found at all
    "duplicate_content": -0.05, # Per duplicate paragraph found
}

# Duplicate detection settings
MIN_PARAGRAPH_LENGTH = 100  # Only check paragraphs with 100+ chars
DUPLICATE_SIMILARITY_THRESHOLD = 0.85  # 85% similar = likely duplicate

# Minimum chars per page (below this is suspicious)
MIN_CHARS_PER_PAGE = 100


def validate_extraction(
    markdown: str,
    manifest: DocumentManifest,
    job_id: str,
) -> ExtractionMetrics:
    """Validate extraction output using pure Python heuristics.

    This function checks:
    1. Page completeness (all pages have markers)
    2. Heading order (matches manifest)
    3. Content density (pages aren't truncated)
    4. Quality indicators (unclear markers, tables, images)

    Args:
        markdown: The extracted markdown content
        manifest: The document manifest from analysis phase
        job_id: Job identifier for logging

    Returns:
        ExtractionMetrics with validation results and computed confidence
    """
    issues: list[ValidationIssue] = []

    # Parse heading tree from manifest
    heading_tree = HeadingTree.model_validate_json(manifest.heading_tree_json)

    # 1. Check page markers
    pages_found = _extract_page_numbers(markdown)
    expected_pages = set(range(1, manifest.total_pages + 1))
    pages_missing = list(expected_pages - pages_found)
    pages_missing.sort()

    if pages_missing:
        for page in pages_missing:
            issues.append(ValidationIssue(
                issue_type="missing_page",
                severity="critical",
                message=f"Page {page} was not transcribed. Add <!-- Page {page} --> marker and content.",
                page_num=page,
            ))

    # 2. Check heading order
    extracted_headings = _extract_headings(markdown)
    expected_headings = [(s.level, s.title) for s in heading_tree.sections]
    heading_issues = _check_heading_order(extracted_headings, expected_headings)
    issues.extend(heading_issues)

    # 3. Check page content density
    page_contents = _split_by_pages(markdown)
    for page_num, content in page_contents.items():
        if len(content.strip()) < MIN_CHARS_PER_PAGE:
            # Check if page was expected to have content
            page_features = next(
                (pf for pf in manifest.page_features if pf.page_num == page_num),
                None
            )
            # Only flag if the page should have content (has features)
            if page_features and (
                page_features.has_images or
                page_features.has_tables or
                page_features.has_lists or
                page_features.has_code_blocks
            ):
                issues.append(ValidationIssue(
                    issue_type="truncated_content",
                    severity="warning",
                    message=f"Page {page_num} has very little content ({len(content.strip())} chars). May be truncated.",
                    page_num=page_num,
                ))

    # 4. Count quality indicators
    unclear_count = len(UNCLEAR_MARKER_PATTERN.findall(markdown))
    heading_count = len(extracted_headings)
    image_count = len(IMAGE_PLACEHOLDER_PATTERN.findall(markdown))
    table_count = _count_tables(markdown)

    # 5. Check for structural issues
    if heading_count == 0 and len(expected_headings) > 0:
        issues.append(ValidationIssue(
            issue_type="no_headings",
            severity="warning",
            message="No headings found in output, but document has headings in manifest.",
        ))

    # 6. Check expected images are present
    expected_images = sum(pf.image_count for pf in manifest.page_features)
    if expected_images > 0 and image_count == 0:
        issues.append(ValidationIssue(
            issue_type="missing_images",
            severity="warning",
            message=f"Expected {expected_images} image placeholders, found 0.",
        ))

    # 7. Check expected tables are present
    expected_tables = sum(pf.table_count for pf in manifest.page_features)
    if expected_tables > 0 and table_count == 0:
        issues.append(ValidationIssue(
            issue_type="missing_tables",
            severity="warning",
            message=f"Expected {expected_tables} tables, found 0.",
        ))

    # 8. Check for duplicate paragraphs
    duplicate_issues = _detect_duplicate_paragraphs(markdown)
    issues.extend(duplicate_issues)
    duplicate_count = len(duplicate_issues)

    if duplicate_count > 0:
        logger.warning(
            f"Job {job_id}: Found {duplicate_count} duplicate paragraph(s) in extraction"
        )

    # Compute confidence heuristically
    confidence = _compute_confidence(
        total_pages=manifest.total_pages,
        pages_missing=len(pages_missing),
        unclear_count=unclear_count,
        heading_issues=len([i for i in heading_issues if i.severity == "critical"]),
        has_headings=heading_count > 0 or len(expected_headings) == 0,
        duplicate_count=duplicate_count,
    )

    # Determine overall validity
    critical_count = len([i for i in issues if i.severity == "critical"])
    is_valid = critical_count == 0

    # Check reading order validity
    reading_order_valid = len(heading_issues) == 0

    metrics = ExtractionMetrics(
        confidence=confidence,
        pages_found=list(pages_found),
        pages_missing=pages_missing,
        unclear_text_count=unclear_count,
        heading_count=heading_count,
        image_placeholder_count=image_count,
        table_count=table_count,
        reading_order_valid=reading_order_valid,
        issues=issues,
        is_valid=is_valid,
    )

    logger.info(
        f"Job {job_id}: Extraction validation - "
        f"valid={is_valid}, confidence={confidence:.2f}, "
        f"pages={len(pages_found)}/{manifest.total_pages}, "
        f"issues={len(issues)} ({critical_count} critical)"
    )

    return metrics


def _extract_page_numbers(markdown: str) -> set[int]:
    """Extract page numbers from <!-- Page N --> markers."""
    matches = PAGE_MARKER_PATTERN.findall(markdown)
    return {int(m) for m in matches}


def _extract_headings(markdown: str) -> list[tuple[int, str]]:
    """Extract headings as (level, text) tuples."""
    headings = []
    for match in HEADING_PATTERN.finditer(markdown):
        level = len(match.group(1))  # Count # characters
        text = match.group(2).strip()
        headings.append((level, text))
    return headings


def _check_heading_order(
    extracted: list[tuple[int, str]],
    expected: list[tuple[int, str]],
) -> list[ValidationIssue]:
    """Check if extracted headings match expected order.

    Uses fuzzy matching since exact text may vary slightly.
    Returns list of issues found.
    """
    issues = []

    if not expected:
        return issues  # No expected headings, nothing to check

    # Build normalized versions for comparison
    def normalize(text: str) -> str:
        return text.lower().strip()

    extracted_normalized = [(level, normalize(text)) for level, text in extracted]
    expected_normalized = [(level, normalize(text)) for level, text in expected]

    # Check for heading level violations (H1 → H3 without H2)
    prev_level = 0
    for level, text in extracted:
        if level > prev_level + 1 and prev_level > 0:
            issues.append(ValidationIssue(
                issue_type="heading_skip",
                severity="warning",
                message=f"Heading '{text[:30]}...' is H{level} but previous was H{prev_level}. Skipped level.",
            ))
        prev_level = level

    # Check if major headings are in correct order
    expected_h1_h2 = [(l, t) for l, t in expected_normalized if l <= 2]
    extracted_h1_h2 = [(l, t) for l, t in extracted_normalized if l <= 2]

    # Simple order check - are the major headings in the same sequence?
    expected_texts = [t for _, t in expected_h1_h2]
    extracted_texts = [t for _, t in extracted_h1_h2]

    # Find headings that are out of order
    for i, exp_text in enumerate(expected_texts):
        # Find this heading in extracted
        matching_idx = None
        for j, ext_text in enumerate(extracted_texts):
            if _text_similarity(exp_text, ext_text) > 0.7:
                matching_idx = j
                break

        if matching_idx is not None and i > 0:
            # Check if previous expected heading comes before this one in extracted
            prev_exp = expected_texts[i - 1]
            prev_matching_idx = None
            for j, ext_text in enumerate(extracted_texts):
                if _text_similarity(prev_exp, ext_text) > 0.7:
                    prev_matching_idx = j
                    break

            if prev_matching_idx is not None and prev_matching_idx > matching_idx:
                issues.append(ValidationIssue(
                    issue_type="heading_order",
                    severity="critical",
                    message=f"Heading '{exp_text[:30]}' should come after '{prev_exp[:30]}' but appears before it.",
                ))

    return issues


def _text_similarity(a: str, b: str) -> float:
    """Compute simple text similarity (0-1).

    Uses word overlap after normalizing text (lowercase, strip punctuation).
    """
    if not a or not b:
        return 0.0

    # Normalize: lowercase, remove punctuation, split into words
    def normalize(text: str) -> set[str]:
        cleaned = re.sub(r'[^\w\s]', '', text.lower().strip())
        return set(cleaned.split())

    words_a = normalize(a)
    words_b = normalize(b)

    if not words_a or not words_b:
        return 0.0

    overlap = len(words_a & words_b)
    return overlap / max(len(words_a), len(words_b))


def _split_by_pages(markdown: str) -> dict[int, str]:
    """Split markdown into page contents by markers."""
    pages: dict[int, str] = {}

    # Find all page marker positions
    markers = list(PAGE_MARKER_PATTERN.finditer(markdown))

    for i, match in enumerate(markers):
        page_num = int(match.group(1))
        start = match.end()

        # End is either next marker or end of string
        if i + 1 < len(markers):
            end = markers[i + 1].start()
        else:
            end = len(markdown)

        pages[page_num] = markdown[start:end]

    return pages


def _count_tables(markdown: str) -> int:
    """Count markdown tables by looking for header separator rows."""
    # Tables have a row like |---|---|
    separator_pattern = re.compile(r"^\|[\s\-:|]+\|$", re.MULTILINE)
    return len(separator_pattern.findall(markdown))


def _detect_duplicate_paragraphs(markdown: str) -> list[ValidationIssue]:
    """Detect near-duplicate paragraphs in the markdown.

    This catches cases where the model accidentally repeats content,
    which can happen at page boundaries or in two-column layouts.

    Args:
        markdown: The extracted markdown content

    Returns:
        List of ValidationIssue for each duplicate found
    """
    issues: list[ValidationIssue] = []

    # Split into paragraphs (double newline separated)
    # Filter to substantial paragraphs only
    paragraphs = [
        p.strip() for p in markdown.split('\n\n')
        if len(p.strip()) >= MIN_PARAGRAPH_LENGTH
    ]

    # Track which paragraphs we've already flagged as duplicates
    flagged_indices: set[int] = set()

    for i, p1 in enumerate(paragraphs):
        if i in flagged_indices:
            continue

        for j in range(i + 1, len(paragraphs)):
            if j in flagged_indices:
                continue

            p2 = paragraphs[j]

            # Compute similarity
            similarity = _text_similarity(p1.lower(), p2.lower())

            if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                # Found a duplicate - flag it
                flagged_indices.add(j)

                # Create a preview of the duplicate content
                preview = p2[:80].replace('\n', ' ')
                if len(p2) > 80:
                    preview += "..."

                issues.append(ValidationIssue(
                    issue_type="duplicate_content",
                    severity="warning",
                    message=f"Duplicate paragraph detected: \"{preview}\" (appears to repeat earlier content)",
                ))

    return issues


def _compute_confidence(
    total_pages: int,
    pages_missing: int,
    unclear_count: int,
    heading_issues: int,
    has_headings: bool,
    duplicate_count: int = 0,
) -> float:
    """Compute confidence score from heuristics.

    Starts at 1.0 and deducts for issues.
    """
    confidence = 1.0

    # Deduct for missing pages
    if total_pages > 0:
        confidence += CONFIDENCE_WEIGHTS["missing_page"] * pages_missing

    # Deduct for unclear markers (capped)
    unclear_penalty = min(unclear_count, 10) * CONFIDENCE_WEIGHTS["unclear_marker"]
    confidence += unclear_penalty

    # Deduct for heading issues
    confidence += CONFIDENCE_WEIGHTS["heading_mismatch"] * heading_issues

    # Deduct if no headings when expected
    if not has_headings:
        confidence += CONFIDENCE_WEIGHTS["no_headings"]

    # Deduct for duplicate content (capped at 5 duplicates)
    duplicate_penalty = min(duplicate_count, 5) * CONFIDENCE_WEIGHTS["duplicate_content"]
    confidence += duplicate_penalty

    # Clamp to valid range
    return max(0.0, min(1.0, confidence))


__all__ = [
    "validate_extraction",
]
