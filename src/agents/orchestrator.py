"""Pipeline Orchestrator - Main Pipeline Coordination.

The Orchestrator runs the complete agentic pipeline:
    1. Planning Phase - Analyze document, create jobs
    2. Execution Phase - Run worker jobs in parallel
    3. Verification Phase - Final quality check

It manages the event bus for streaming and coordinates
all components.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING
from uuid import uuid4

from ..utils.confidence_scoring import calculate_confidence_from_verification
from .events import (
    ConvergenceEvent,
    EditCommittedEvent,
    EventBus,
    PageVerifiedEvent,
    ProcessingErrorEvent,
    RecoveryPhaseCompleteEvent,
    RecoveryPhaseStartedEvent,
    RoundCompleteEvent,
    RoundStartedEvent,
    StreamEvent,
    VerificationCompleteEvent,
    VerificationStartedEvent,
)
from .issue_fixer import detect_and_fix_issues_async
from .models import (
    ConvergenceReason,
    CriticIssue,
    CriticReport,
    DocumentJob,
    DocumentJobType,
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    Job,
    Ledger,
    LedgerEntry,
    LLMCallRecord,
    PageBoundary,
    PageBoundaryMap,
    PagePlan,
    PageVerification,
    ProcessingResult,
    ProcessingStatus,
    RecoveryAttemptStatus,
    RecoveryReport,
    RoundContext,
    RoundLoopResult,
    StoredFigure,
    TaskType,
    VerificationReport,
)
from .plan_verification import (
    verify_figure_completeness,
    verify_heading_structure,
    verify_spelling,
    verify_table_accuracy_vision,
    verify_table_completeness,
)
from .planner import plan_document, plan_document_streaming
from .recovery import (
    MAX_RECOVERY_ATTEMPTS,
    attempt_page_recovery,
    determine_final_status,
    should_attempt_recovery,
)
from .subagents.paragraph_merge import invoke_paragraph_merge_subagent
from .worker import execute_jobs_parallel, execute_page_jobs

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Footnote Collection Helper
# =============================================================================


def collect_footnotes_at_end(markdown: str) -> str:
    """Collect all footnote definitions and move them to the document end.

    Also removes page separator markers (---).

    Args:
        markdown: Raw markdown with scattered footnote definitions

    Returns:
        Markdown with footnotes collected at the end
    """
    # Pattern for footnote definitions - captures multiline definitions
    # Matches [^1]: definition text that may span lines until next footnote or double newline
    footnote_pattern = r"^\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\n\n|\Z)"

    # Find all footnote definitions
    footnotes = {}
    for match in re.finditer(footnote_pattern, markdown, re.MULTILINE | re.DOTALL):
        num = int(match.group(1))
        text = match.group(2).strip()
        # Keep the first definition for each number (in case of duplicates)
        if num not in footnotes:
            footnotes[num] = text

    # Remove footnote definitions from their current locations
    cleaned = re.sub(footnote_pattern, "", markdown, flags=re.MULTILINE | re.DOTALL)

    # Remove page separator markers
    cleaned = re.sub(r"\n*---\n*", "\n\n", cleaned)

    # Clean up excessive whitespace
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    # Append footnotes at the end if any were found
    if footnotes:
        footnote_section = "\n\n---\n\n"  # Single separator before footnotes
        for num in sorted(footnotes.keys()):
            footnote_section += f"[^{num}]: {footnotes[num]}\n"
        cleaned += footnote_section.rstrip()

    return cleaned


# =============================================================================
# Figure URL Rewriting
# =============================================================================


def rewrite_figure_urls(
    markdown: str,
    stored_figures: list[StoredFigure],
) -> tuple[str, list[StoredFigure]]:
    """Replace placeholder image URLs with relative paths to stored figures.

    Transforms markdown image references to use relative paths pointing to
    stored S3 images. Also extracts alt-text from the markdown to populate
    the StoredFigure.alt_text field.

    Patterns handled:
    - ![alt text](image.png) -> ![alt text](images/figure-1.png)
    - ![alt text](figure_N.png) -> ![alt text](images/figure-1.png)
    - ![](decorative "title") -> ![](images/figure-1.png "title")
    - Empty src like ![]() -> ![]() (unchanged, no figure to map)

    Args:
        markdown: Final markdown with placeholder image URLs
        stored_figures: List of figures uploaded to S3

    Returns:
        Tuple of (updated_markdown, updated_stored_figures with alt_text filled)
    """
    if not stored_figures:
        return markdown, stored_figures

    # Pattern to match all markdown images: ![alt](url "optional title")
    # Captures: alt text, url, optional title
    img_pattern = re.compile(
        r'!\[([^\]]*)\]\(([^)\s"]*)\s*(?:"([^"]*)")?\)',
        re.MULTILINE,
    )

    # Find all image references in markdown
    matches = list(img_pattern.finditer(markdown))

    if not matches:
        logger.debug("No image patterns found in markdown")
        return markdown, stored_figures

    # Map figures by index (sequential assignment based on appearance order)
    # Assumption: figures appear in the same order as they were extracted
    updated_figures = list(stored_figures)  # Copy to update alt_text
    figure_idx = 0
    replacements: list[tuple[int, int, str]] = []  # (start, end, replacement)

    for match in matches:
        alt_text = match.group(1)
        url = match.group(2)
        title = match.group(3)

        # Skip if no URL (empty image reference)
        if not url or url.strip() == "":
            continue

        # Skip if URL is already a proper relative path to images/
        if url.startswith("images/figure-"):
            continue

        # Skip if we've run out of figures
        if figure_idx >= len(updated_figures):
            logger.warning(
                f"More image references in markdown ({len(matches)}) "
                f"than stored figures ({len(stored_figures)})"
            )
            break

        # Get the corresponding figure
        figure = updated_figures[figure_idx]
        new_url = f"images/{figure.figure_id}.png"

        # Build replacement string
        if title:
            replacement = f"![{alt_text}]({new_url} \"{title}\")"
        else:
            replacement = f"![{alt_text}]({new_url})"

        replacements.append((match.start(), match.end(), replacement))

        # Update the figure's alt_text if we have one
        if alt_text and not updated_figures[figure_idx].alt_text:
            updated_figures[figure_idx] = StoredFigure(
                figure_id=figure.figure_id,
                s3_key=figure.s3_key,
                page_num=figure.page_num,
                alt_text=alt_text,
                caption=figure.caption,
                ref_id=figure.ref_id,
            )

        figure_idx += 1

    # Apply replacements in reverse order to preserve positions
    result = markdown
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]

    logger.info(f"Rewrote {len(replacements)} image URLs to relative paths")

    return result, updated_figures


# =============================================================================
# Cross-Page Merge Pass
# =============================================================================


def validate_merge_result(
    page1_md: str,
    page2_md: str,
    page1_remove_chars: int,
    page2_remove_chars: int,
    merged_text: str,
) -> tuple[bool, str]:
    """Validate that a merge operation makes sense.

    Checks:
    - Character counts don't exceed available text
    - Merged result doesn't end with partial word
    - Result produces valid word boundaries

    Returns:
        (is_valid, error_message) tuple
    """
    # Check bounds
    if page1_remove_chars > len(page1_md):
        return False, f"page1_remove_chars ({page1_remove_chars}) exceeds page length ({len(page1_md)})"
    if page2_remove_chars > len(page2_md):
        return False, f"page2_remove_chars ({page2_remove_chars}) exceeds page length ({len(page2_md)})"

    # Check that removal doesn't split a word (page 1 end)
    if page1_remove_chars > 0:
        remaining_page1 = page1_md[:-page1_remove_chars]
        # If remaining text ends with a letter, it might be a split word
        if remaining_page1 and remaining_page1[-1].isalpha():
            # Check if what we're removing starts with a letter (continuing a word)
            removed = page1_md[-page1_remove_chars:]
            if removed and removed[0].isalpha():
                return (
                    False,
                    f"Merge would split word at page 1 end: '...{remaining_page1[-10:]}' | '{removed[:10]}...'",
                )

    # Check merged text produces valid word
    if merged_text:
        # Merged text should not end with hyphen or partial
        if merged_text.rstrip().endswith("-"):
            return False, f"Merged text ends with hyphen: '{merged_text[-20:]}'"

    return True, ""


async def merge_cross_page_paragraphs(
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    plan: DocumentPlan,
    ledger: Ledger,
    event_bus: EventBus | None = None,
) -> dict[int, str]:
    """Merge paragraphs that are split across page boundaries.

    This runs AFTER all per-page jobs complete, on stable markdown.
    Uses the paragraph_merge subagent to detect and merge split paragraphs.

    Args:
        page_markdowns: Current markdown for each page
        page_images: Images for each page
        plan: DocumentPlan with page continuation flags
        ledger: Ledger for recording changes
        event_bus: Optional event bus for streaming

    Returns:
        Updated page_markdowns dict with merges applied
    """
    # Find pages with continuation flags
    pages_with_continuation = [
        page_num for page_num, page_plan in plan.pages.items() if page_plan.has_page_continuation
    ]

    if not pages_with_continuation:
        logger.info("No cross-page merges needed")
        return page_markdowns

    logger.info(f"Processing {len(pages_with_continuation)} cross-page merges")

    for page_num in sorted(pages_with_continuation):
        next_page = page_num + 1

        if next_page not in page_markdowns:
            logger.warning(f"Page {next_page} not found for merge with page {page_num}")
            continue

        # Get page markdown boundaries
        page1_md = page_markdowns[page_num]
        page2_md = page_markdowns[next_page]

        # Extract ending/starting text for merge analysis
        page1_end = page1_md[-300:] if len(page1_md) > 300 else page1_md
        page2_start = page2_md[:300] if len(page2_md) > 300 else page2_md

        # Get images for both pages
        page1_image = page_images.get(page_num)
        page2_image = page_images.get(next_page)

        if not page1_image or not page2_image:
            logger.warning(f"Missing images for merge between pages {page_num}-{next_page}")
            continue

        try:
            # Call merge subagent
            merge_result = await invoke_paragraph_merge_subagent(
                page1_end=page1_end,
                page2_start=page2_start,
                page1_image=page1_image,
                page2_image=page2_image,
            )

            if not merge_result.should_merge:
                logger.info(f"Merge not needed for pages {page_num}-{next_page}: {merge_result.reasoning}")
                continue

            if merge_result.confidence < 0.5:
                logger.warning(
                    f"Skipping low-confidence merge for pages {page_num}-{next_page}: "
                    f"confidence={merge_result.confidence}"
                )
                continue

            # Determine if edit needs human review
            needs_review = merge_result.confidence < 0.8

            # Validate the merge before applying
            is_valid, error_msg = validate_merge_result(
                page1_md=page1_md,
                page2_md=page2_md,
                page1_remove_chars=merge_result.page1_remove_chars,
                page2_remove_chars=merge_result.page2_remove_chars,
                merged_text=merge_result.merged_text,
            )

            if not is_valid:
                logger.warning(
                    f"Invalid merge for pages {page_num}-{next_page}: {error_msg}. "
                    f"AI returned: remove {merge_result.page1_remove_chars} from p1, "
                    f"{merge_result.page2_remove_chars} from p2, "
                    f"merge to '{merge_result.merged_text[:50]}...'"
                )
                continue  # Skip this invalid merge

            # Apply the merge - remove chars from end of page 1
            if merge_result.page1_remove_chars > 0:
                old_end = page1_md[-merge_result.page1_remove_chars :]
                page_markdowns[page_num] = page1_md[: -merge_result.page1_remove_chars]

                ledger.append(
                    LedgerEntry(
                        job_id=f"merge:{page_num}-{next_page}",
                        page=page_num,
                        action=TaskType.PARAGRAPH_MERGE,
                        target=f"page_end:{page_num}",
                        before=old_end,
                        after="",
                        reasoning=f"Removed for merge: {merge_result.reasoning}",
                        confidence=merge_result.confidence,
                        validated=True,
                        needs_review=needs_review,
                    )
                )

            # Replace start of page 2 with merged text
            if merge_result.page2_remove_chars > 0:
                old_start = page2_md[: merge_result.page2_remove_chars]
                new_start = merge_result.merged_text
                page_markdowns[next_page] = new_start + page2_md[merge_result.page2_remove_chars :]

                entry = LedgerEntry(
                    job_id=f"merge:{page_num}-{next_page}",
                    page=next_page,
                    action=TaskType.PARAGRAPH_MERGE,
                    target=f"page_start:{next_page}",
                    before=old_start,
                    after=new_start,
                    reasoning=f"Merged paragraph: {merge_result.reasoning}",
                    confidence=merge_result.confidence,
                    validated=True,
                    needs_review=needs_review,
                )
                ledger.append(entry)

                if event_bus:
                    event_bus.emit(
                        EditCommittedEvent(
                            document_id=event_bus.document_id,
                            ledger_entry=entry,
                            content_preview=merge_result.merged_text[:100],
                        )
                    )

            logger.info(
                f"Merged pages {page_num}-{next_page} "
                f"(method: {merge_result.join_method}, confidence: {merge_result.confidence})"
            )

        except Exception as e:
            logger.error(f"Merge failed for pages {page_num}-{next_page}: {e}")
            continue

    return page_markdowns


# =============================================================================
# Verification Phase
# =============================================================================


async def verify_document(
    final_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    plan: DocumentPlan,
    ledger: Ledger,
    event_bus: EventBus | None = None,
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] | None = None,
    page_width: float | None = None,
) -> VerificationReport:
    """Run final verification on the processed document.

    This performs comprehensive verification:
    1. Basic checks: unfilled placeholders, empty alt-text, heading skips
    2. Plan verification: heading structure matches outline (V3.1)
    3. Figure completeness: all planned figures have alt-text (V3.2)
    4. Table completeness: all planned tables transcribed (V3.3)
    5. Table accuracy: transcribed tables match source images (V3.3.1) - NEW
    6. Spelling verification: check against document dictionary (V3.4)

    Args:
        final_markdowns: Final markdown for each page
        page_images: Page images for visual check
        plan: Document plan to verify against
        ledger: Ledger of all changes made
        event_bus: Optional event bus
        element_bboxes: Bounding boxes for cropping elements (for table accuracy check)
        page_width: Page width for coordinate scaling (for table accuracy check)

    Returns:
        VerificationReport with pass/fail and issues
    """
    import re

    start_time = time.time()

    if event_bus:
        event_bus.emit(
            VerificationStartedEvent(
                document_id=event_bus.document_id,
                pages_to_verify=len(final_markdowns),
            )
        )

    page_results: list[PageVerification] = []
    all_issues: list[str] = []
    critical_issues: list[str] = []

    # =================================================================
    # Phase 1: Per-page basic checks
    # =================================================================
    for page_num in sorted(final_markdowns.keys()):
        markdown = final_markdowns[page_num]
        issues: list[str] = []

        # Check for remaining placeholders
        if "<!-- image" in markdown.lower():
            issue = f"Page {page_num}: Unfilled image placeholder"
            issues.append(issue)
            critical_issues.append(issue)

        if "<!-- table" in markdown.lower():
            issue = f"Page {page_num}: Unfilled table placeholder"
            issues.append(issue)
            critical_issues.append(issue)

        # Check for empty alt-text (should be either filled or marked decorative)
        empty_alts = re.findall(r"!\[\]\([^)]+\)", markdown)
        # Empty alt is OK for decorative images, but flag if there are many
        if len(empty_alts) > 3:
            issues.append(f"Page {page_num}: Many images without alt-text ({len(empty_alts)})")

        # Check heading hierarchy
        headings = re.findall(r"^(#{1,6})\s+", markdown, re.MULTILINE)
        levels = [len(h) for h in headings]
        for i in range(1, len(levels)):
            if levels[i] > levels[i - 1] + 1:
                issues.append(f"Page {page_num}: Heading skip H{levels[i - 1]} -> H{levels[i]}")

        passed = len(issues) == 0
        page_results.append(
            PageVerification(
                page_num=page_num,
                passed=passed,
                issues=issues,
                confidence=0.9 if passed else 0.5,
            )
        )

        all_issues.extend(issues)

        if event_bus:
            event_bus.emit(
                PageVerifiedEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    passed=passed,
                    issues=issues,
                )
            )

    # =================================================================
    # Phase 2: Plan-based verification (V3.1 - V3.4)
    # =================================================================

    # Combine all pages into single markdown for structure checks
    combined_markdown = "\n\n---\n\n".join(final_markdowns[p] for p in sorted(final_markdowns.keys()))

    # V3.1: Verify heading structure matches DocumentPlan outline
    heading_issues = verify_heading_structure(combined_markdown, plan)
    for issue in heading_issues:
        all_issues.append(f"[Structure] {issue}")
    # Missing or wrong-level headings are critical
    critical_issues.extend(f"[Structure] {issue}" for issue in heading_issues if "Missing" in issue or "Wrong" in issue)

    # V3.2: Verify all planned figures have alt-text
    figure_issues = verify_figure_completeness(final_markdowns, plan)
    for issue in figure_issues:
        all_issues.append(f"[Figures] {issue}")
    # Missing alt-text is critical for accessibility
    critical_issues.extend(f"[Figures] {issue}" for issue in figure_issues if "missing alt-text" in issue.lower())

    # V3.3: Verify all planned tables were transcribed
    table_issues = verify_table_completeness(final_markdowns, plan)
    for issue in table_issues:
        all_issues.append(f"[Tables] {issue}")
    # Missing tables are critical
    critical_issues.extend(f"[Tables] {issue}" for issue in table_issues if "not transcribed" in issue.lower())

    # V3.3.1: Vision-based table accuracy verification (NEW)
    # Only run if bounding boxes are available (enables cropping tables from images)
    table_accuracy_issues: list[str] = []
    if element_bboxes and page_width and page_images:
        try:
            table_accuracy_issues = await verify_table_accuracy_vision(
                page_markdowns=final_markdowns,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                plan=plan,
            )
            for issue in table_accuracy_issues:
                all_issues.append(f"[TableAccuracy] {issue}")
            # Table data mismatches are critical errors
            critical_issues.extend(f"[TableAccuracy] {issue}" for issue in table_accuracy_issues)
        except Exception as e:
            logger.warning(f"Table accuracy verification failed: {e}")
            # Don't fail the whole verification if this optional check errors

    # V3.4: Verify spelling using document dictionary
    # Only include a sample of spelling issues to avoid noise
    spelling_issues = verify_spelling(combined_markdown, plan)
    if spelling_issues:
        # Limit to first 10 spelling issues to avoid overwhelming the report
        sample_issues = spelling_issues[:10]
        for issue in sample_issues:
            all_issues.append(f"[Spelling] {issue}")
        if len(spelling_issues) > 10:
            all_issues.append(f"[Spelling] ... and {len(spelling_issues) - 10} more spelling issues")

    # =================================================================
    # Final report
    # =================================================================
    duration_ms = int((time.time() - start_time) * 1000)

    pages_passed = sum(1 for p in page_results if p.passed)
    pages_failed = len(page_results) - pages_passed

    # Overall pass if no critical issues and >80% pages pass
    overall_passed = len(critical_issues) == 0 and pages_passed >= len(page_results) * 0.8

    report = VerificationReport(
        document_id=plan.document_id,
        passed=overall_passed,
        pages=page_results,
        total_issues=len(all_issues),
        critical_issues=critical_issues,
        warnings=[i for i in all_issues if i not in critical_issues],
        pages_passed=pages_passed,
        pages_failed=pages_failed,
        verification_duration_ms=duration_ms,
    )

    if event_bus:
        event_bus.emit(
            VerificationCompleteEvent(
                document_id=event_bus.document_id,
                passed=overall_passed,
                pages_passed=pages_passed,
                pages_failed=pages_failed,
                total_issues=len(all_issues),
                critical_issues=critical_issues,
                duration_ms=duration_ms,
            )
        )

    logger.info(
        f"Verification complete: {pages_passed}/{len(page_results)} passed, "
        f"{len(all_issues)} issues ({len(heading_issues)} heading, "
        f"{len(figure_issues)} figure, {len(table_issues)} table, "
        f"{len(spelling_issues)} spelling)"
    )

    return report


# =============================================================================
# Recovery Phase
# =============================================================================


async def run_recovery_phase(
    verification: VerificationReport,
    final_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    ledger: Ledger,
    event_bus: EventBus | None = None,
) -> tuple[dict[int, str], RecoveryReport]:
    """Run the recovery phase on failed pages.

    Attempts to fix pages that failed verification. Each page gets up to
    MAX_RECOVERY_ATTEMPTS tries. Pages can be recovered fully, accepted
    with caveats, or marked as unrecoverable.

    Args:
        verification: Initial verification report
        final_markdowns: Current markdown for each page
        page_images: Page images
        ledger: Ledger for recording changes
        event_bus: Optional event bus for streaming

    Returns:
        Tuple of (updated_markdowns, RecoveryReport)
    """
    start_time = time.time()
    document_id = verification.document_id

    # Find pages that need recovery
    failed_pages = [pv for pv in verification.pages if not pv.passed and should_attempt_recovery(pv, 1)]

    if not failed_pages:
        # No pages need recovery
        return final_markdowns, RecoveryReport(
            document_id=document_id,
            recovery_attempted=False,
        )

    pages_to_recover = [pv.page_num for pv in failed_pages]

    # Emit recovery phase started
    if event_bus:
        event_bus.emit(
            RecoveryPhaseStartedEvent(
                document_id=document_id,
                pages_to_recover=pages_to_recover,
                max_attempts_per_page=MAX_RECOVERY_ATTEMPTS,
            )
        )

    logger.info(f"Starting recovery phase for {len(failed_pages)} pages")

    # Track results
    updated_markdowns = dict(final_markdowns)
    all_attempts: list = []
    pages_recovered: list[int] = []
    pages_with_caveats: list[int] = []
    pages_unrecoverable: list[int] = []
    total_edits = 0

    for page_verification in failed_pages:
        page_num = page_verification.page_num
        current_markdown = updated_markdowns.get(page_num, "")
        page_image = page_images.get(page_num)

        if page_image is None:
            logger.warning(f"No image for page {page_num}, skipping recovery")
            pages_unrecoverable.append(page_num)
            continue

        # Build processing history (simplified for now)
        processing_history = [f"Initial processing completed with {len(page_verification.issues)} issues"]

        # Attempt recovery up to MAX_RECOVERY_ATTEMPTS times
        for attempt_num in range(1, MAX_RECOVERY_ATTEMPTS + 1):
            if not should_attempt_recovery(page_verification, attempt_num):
                break

            recovered_markdown, attempt = await attempt_page_recovery(
                page_num=page_num,
                page_image=page_image,
                current_markdown=current_markdown,
                issues=page_verification.issues,
                processing_history=processing_history,
                attempt_number=attempt_num,
                event_bus=event_bus,
            )

            all_attempts.append(attempt)
            current_markdown = recovered_markdown
            total_edits += attempt.edits_applied

            # Check if recovery succeeded
            if attempt.status == RecoveryAttemptStatus.SUCCEEDED:
                pages_recovered.append(page_num)
                updated_markdowns[page_num] = current_markdown
                break
            elif attempt.status == RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS:
                pages_with_caveats.append(page_num)
                updated_markdowns[page_num] = current_markdown
                break

            # Add to history for next attempt
            processing_history.append(f"Recovery attempt {attempt_num}: {attempt.status.value}")

        # If no successful recovery after all attempts
        if page_num not in pages_recovered and page_num not in pages_with_caveats:
            pages_unrecoverable.append(page_num)

    duration_ms = int((time.time() - start_time) * 1000)

    # Build recovery report
    report = RecoveryReport(
        document_id=document_id,
        recovery_attempted=True,
        pages_recovered=pages_recovered,
        pages_accepted_with_caveats=pages_with_caveats,
        pages_unrecoverable=pages_unrecoverable,
        attempts=all_attempts,
        total_recovery_edits=total_edits,
        recovery_duration_ms=duration_ms,
    )

    # Determine final status
    final_status = determine_final_status(
        verification=verification,
        recovery_report=report,
        total_pages=len(page_images),
    )

    # Emit recovery phase complete
    if event_bus:
        event_bus.emit(
            RecoveryPhaseCompleteEvent(
                document_id=document_id,
                pages_recovered=pages_recovered,
                pages_with_caveats=pages_with_caveats,
                pages_unrecoverable=pages_unrecoverable,
                final_status=final_status,
                duration_ms=duration_ms,
            )
        )

    logger.info(
        f"Recovery phase complete: {len(pages_recovered)} recovered, "
        f"{len(pages_with_caveats)} with caveats, "
        f"{len(pages_unrecoverable)} unrecoverable"
    )

    return updated_markdowns, report


# =============================================================================
# Main Orchestrator
# =============================================================================


async def run_agentic_pipeline(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_concurrent_jobs: int = 3,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
    stored_figures: list[StoredFigure] | None = None,
) -> tuple[ProcessingResult, EventBus]:
    """Run the complete agentic pipeline.

    This is the main entry point for document processing.

    Args:
        filename: Original document filename
        page_markdowns: Initial markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for bbox scaling
        document_id: Optional document ID (generated if not provided)
        max_concurrent_jobs: Max concurrent worker jobs
        event_bus: Optional pre-created event bus for streaming
        capture_debug: If True, capture full prompt/response for debug bundle
        stored_figures: List of figures already uploaded to S3 for URL rewriting

    Returns:
        Tuple of (ProcessingResult, EventBus)
    """
    start_time = time.time()
    doc_id = document_id or str(uuid4())

    # Use provided event bus or create new one
    if event_bus is None:
        event_bus = EventBus(doc_id)

    # Create ledger
    ledger = Ledger(document_id=doc_id)

    logger.info(f"Starting agentic pipeline for {filename} (id={doc_id})")

    try:
        # =================================================================
        # Phase 1: Planning
        # =================================================================
        plan = await plan_document(
            filename=filename,
            page_markdowns=page_markdowns,
            page_images=page_images,
            event_bus=event_bus,
            capture_debug=capture_debug,
        )

        # =================================================================
        # Phase 2: Execution
        # =================================================================
        execution_start = time.time()

        job_results = await execute_jobs_parallel(
            jobs=plan.jobs,
            page_images=page_images,
            element_bboxes=element_bboxes,
            page_width=page_width,
            ledger=ledger,
            max_concurrent=max_concurrent_jobs,
            event_bus=event_bus,
            page_markdowns=page_markdowns,  # For PARAGRAPH job routing
            capture_debug=capture_debug,
        )

        execution_duration_ms = int((time.time() - execution_start) * 1000)

        # Build final markdowns from job results
        final_markdowns = dict(page_markdowns)  # Start with original
        for job_result in job_results:
            if job_result.success:
                # Find the page for this job
                job = next((j for j in plan.jobs if j.job_id == job_result.job_id), None)
                if job:
                    final_markdowns[job.page] = job_result.updated_markdown

        # Aggregate execution stats
        total_input_tokens = plan.planning_tokens_input + sum(r.input_tokens for r in job_results)
        total_output_tokens = plan.planning_tokens_output + sum(r.output_tokens for r in job_results)

        # =================================================================
        # Phase 3: Assembly (Cross-Page Merge)
        # =================================================================
        # Merge paragraphs split across page boundaries.
        # Runs AFTER per-page jobs so we work with clean, artifact-free text.
        final_markdowns = await merge_cross_page_paragraphs(
            page_markdowns=final_markdowns,
            page_images=page_images,
            plan=plan,
            ledger=ledger,
            event_bus=event_bus,
        )

        # =================================================================
        # Phase 4: Lint & Fix (Issue Resolution)
        # =================================================================
        # Detect issues using structured types with full context.
        # Route to appropriate fixers:
        #   - Deterministic: heading levels, placeholders (fast, sync)
        #   - LLM-based: alt-text, tables (async, uses page images)
        # This catches worker failures before verification.
        final_markdowns, fixes_applied, fixes_failed = await detect_and_fix_issues_async(
            final_markdowns, plan, page_images
        )
        if fixes_applied:
            logger.info(f"Issue fixer applied {len(fixes_applied)} fixes:")
            for fix in fixes_applied:
                logger.info(f"  ✓ {fix}")
        if fixes_failed:
            logger.warning(f"Issue fixer failed on {len(fixes_failed)} issues:")
            for fail in fixes_failed[:5]:  # Log first 5
                logger.warning(f"  ✗ {fail}")

        # =================================================================
        # Phase 5: Verification
        # =================================================================
        verification = await verify_document(
            final_markdowns=final_markdowns,
            page_images=page_images,
            plan=plan,
            ledger=ledger,
            event_bus=event_bus,
            element_bboxes=element_bboxes,  # For table accuracy verification
            page_width=page_width,  # For table accuracy verification
        )

        # =================================================================
        # Phase 6: Recovery (if needed)
        # =================================================================
        recovery_report: RecoveryReport | None = None

        # Run recovery if verification failed but >= 50% passed
        total_pages = len(page_markdowns)
        pass_rate = verification.pages_passed / total_pages if total_pages > 0 else 0

        if not verification.passed and pass_rate >= 0.5:
            logger.info(
                f"Verification failed ({verification.pages_passed}/{total_pages} passed), attempting recovery phase"
            )

            final_markdowns, recovery_report = await run_recovery_phase(
                verification=verification,
                final_markdowns=final_markdowns,
                page_images=page_images,
                ledger=ledger,
                event_bus=event_bus,
            )

        # Determine final status
        final_status = determine_final_status(
            verification=verification,
            recovery_report=recovery_report,
            total_pages=total_pages,
        )

        # Consider success if status is SUCCESS or PARTIAL_SUCCESS
        is_success = final_status in [
            ProcessingStatus.SUCCESS,
            ProcessingStatus.PARTIAL_SUCCESS,
        ]

        # =================================================================
        # Build final result
        # =================================================================
        total_duration_ms = int((time.time() - start_time) * 1000)

        # Combine all page markdowns
        raw_markdown = "\n\n---\n\n".join(final_markdowns[p] for p in sorted(final_markdowns.keys()))

        # Post-process: collect footnotes at document end and remove page separators
        final_markdown = collect_footnotes_at_end(raw_markdown)

        # Rewrite figure URLs to relative paths if figures were stored
        final_stored_figures: list[StoredFigure] = []
        if stored_figures:
            final_markdown, final_stored_figures = rewrite_figure_urls(
                final_markdown, stored_figures
            )

        # Calculate cost (Haiku pricing)
        cost = (total_input_tokens * 0.00025 / 1000) + (total_output_tokens * 0.00125 / 1000)

        # Calculate confidence from verification data
        page_confidences = [pv.confidence for pv in verification.pages]
        recovery_edits = recovery_report.total_recovery_edits if recovery_report else 0

        confidence_score = calculate_confidence_from_verification(
            page_confidences=page_confidences,
            critical_issues_count=len(verification.critical_issues),
            recovery_edits=recovery_edits,
        )

        logger.info(
            f"Calculated confidence: {confidence_score:.3f} "
            f"(pages={len(page_confidences)}, critical={len(verification.critical_issues)}, "
            f"recovery={recovery_edits})"
        )

        # Aggregate all LLM calls from planning and execution
        all_llm_calls: list[LLMCallRecord] = []

        # Add planner LLM calls
        all_llm_calls.extend(plan.llm_calls)

        # Add worker LLM calls from job results
        for r in job_results:
            if r.llm_call:
                all_llm_calls.append(r.llm_call)

        # Sort by timestamp
        all_llm_calls.sort(key=lambda c: c.timestamp)

        result = ProcessingResult(
            document_id=doc_id,
            success=is_success,
            final_markdown=final_markdown,
            ledger=ledger,
            verification=verification,
            stored_figures=final_stored_figures,
            confidence_score=confidence_score,
            total_pages=len(page_markdowns),
            total_edits=ledger.total_edits,
            total_jobs=len(plan.jobs),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=cost,
            llm_calls=all_llm_calls,
            total_duration_ms=total_duration_ms,
            planning_duration_ms=plan.planning_duration_ms,
            execution_duration_ms=execution_duration_ms,
            verification_duration_ms=verification.verification_duration_ms,
            recovery_report=recovery_report,
        )

        # NOTE: ProcessingCompleteEvent is emitted by document_processing_service
        # AFTER markdown is stored to S3, ensuring the URL is available when UI fetches

        logger.info(
            f"Agentic pipeline complete: {ledger.total_edits} edits, "
            f"${cost:.4f}, {total_duration_ms}ms, status={final_status.value}"
        )

        return result, event_bus

    except Exception as e:
        logger.error(f"Agentic pipeline failed: {e}")

        event_bus.emit(
            ProcessingErrorEvent(
                document_id=doc_id,
                error=str(e),
                phase="unknown",
                recoverable=False,
            )
        )

        # Return error result
        return (
            ProcessingResult(
                document_id=doc_id,
                success=False,
                final_markdown="",
                ledger=ledger,
                verification=VerificationReport(
                    document_id=doc_id,
                    passed=False,
                ),
            ),
            event_bus,
        )


# =============================================================================
# Streaming Phase Handoff Pipeline
# =============================================================================


async def run_agentic_pipeline_with_streaming_handoff(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_concurrent_jobs: int = 3,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
) -> tuple[ProcessingResult, EventBus]:
    """Run the agentic pipeline with streaming phase handoff.

    This version overlaps planning and execution so that page execution starts
    as soon as each page is planned, rather than waiting for ALL pages to be
    planned first.

    The key difference from run_agentic_pipeline():
    - Planning and execution run concurrently
    - As each page completes planning, its jobs are immediately started
    - Total latency is reduced because early pages execute while later pages plan

    Args:
        filename: Original document filename
        page_markdowns: Initial markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for bbox scaling
        document_id: Optional document ID (generated if not provided)
        max_concurrent_jobs: Max concurrent worker jobs across all pages
        event_bus: Optional pre-created event bus for streaming
        capture_debug: If True, capture full prompt/response for debug bundle

    Returns:
        Tuple of (ProcessingResult, EventBus)
    """
    start_time = time.time()
    doc_id = document_id or str(uuid4())

    # Use provided event bus or create new one
    if event_bus is None:
        event_bus = EventBus(doc_id)

    # Create ledger
    ledger = Ledger(document_id=doc_id)

    logger.info(f"Starting streaming handoff pipeline for {filename} (id={doc_id})")

    try:
        # =================================================================
        # Phase 1+2: Streaming Planning + Execution
        # =================================================================
        execution_start = time.time()

        # Track all execution tasks and results
        execution_tasks: list[asyncio.Task] = []
        page_plans: dict[int, PagePlan] = {}
        all_llm_calls: list[LLMCallRecord] = []
        final_markdowns: dict[int, str] = dict(page_markdowns)
        final_structure: DocumentStructure | None = None

        # Use semaphore to limit concurrent execution
        semaphore = asyncio.Semaphore(max_concurrent_jobs)

        async def execute_with_semaphore(
            page_num: int,
            jobs: list[Job],
        ) -> tuple[int, str, list[LedgerEntry], list[LLMCallRecord], int, int]:
            """Execute jobs for a page with semaphore for concurrency control."""
            async with semaphore:
                page_image = page_images.get(page_num)
                if page_image is None:
                    logger.error(f"No image for page {page_num}")
                    return page_num, page_markdowns.get(page_num, ""), [], [], 0, 0

                updated_md, entries, llm_calls, inp_tokens, out_tokens = await execute_page_jobs(
                    page_num=page_num,
                    jobs=jobs,
                    page_markdown=page_markdowns.get(page_num, ""),
                    page_image=page_image,
                    element_bboxes=element_bboxes,
                    page_width=page_width,
                    ledger=ledger,
                    event_bus=event_bus,
                    capture_debug=capture_debug,
                )
                return page_num, updated_md, entries, llm_calls, inp_tokens, out_tokens

        # Stream through planning, launching execution as each page completes
        total_input_tokens = 0
        total_output_tokens = 0

        plan_stream = plan_document_streaming(
            filename=filename,
            page_markdowns=page_markdowns,
            page_images=page_images,
            event_bus=event_bus,
            capture_debug=capture_debug,
        )
        async for (
            page_num,
            jobs,
            page_plan,
            current_structure,
            inp_tokens,
            out_tokens,
            llm_call,
        ) in plan_stream:
            # Track planning stats
            total_input_tokens += inp_tokens
            total_output_tokens += out_tokens
            if llm_call:
                all_llm_calls.append(llm_call)

            # Store page plan
            page_plans[page_num] = page_plan
            final_structure = current_structure

            # Launch execution for this page immediately
            if jobs:
                task = asyncio.create_task(execute_with_semaphore(page_num, jobs))
                execution_tasks.append(task)
                logger.info(f"Launched execution for page {page_num} with {len(jobs)} jobs")

        # =================================================================
        # Wait for all execution to complete
        # =================================================================
        if execution_tasks:
            results = await asyncio.gather(*execution_tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, BaseException):
                    logger.error(f"Execution task failed: {result}")
                    continue

                page_num, updated_md, entries, llm_calls, inp_tokens, out_tokens = result
                final_markdowns[page_num] = updated_md
                total_input_tokens += inp_tokens
                total_output_tokens += out_tokens
                all_llm_calls.extend(llm_calls)

        execution_duration_ms = int((time.time() - execution_start) * 1000)

        # Build DocumentPlan for downstream phases (merge, verification, recovery)
        from .planner import generate_jobs_for_page

        all_jobs: list[Job] = []
        for page_num in sorted(page_plans.keys()):
            page_plan = page_plans[page_num]
            if final_structure:
                page_jobs = generate_jobs_for_page(
                    page_num=page_num,
                    page_plan=page_plan,
                    page_markdown=page_markdowns.get(page_num, ""),
                    structure=final_structure,
                )
                all_jobs.extend(page_jobs)

        plan = DocumentPlan(
            document_id=doc_id,
            filename=filename,
            total_pages=len(page_markdowns),
            structure=final_structure or DocumentStructure(title="Unknown", document_type=DocumentType.OTHER),
            pages=page_plans,
            jobs=all_jobs,
            full_dictionary=final_structure.key_terms if final_structure else [],
            planning_duration_ms=0,  # Not tracked separately in streaming mode
            planning_tokens_input=0,
            planning_tokens_output=0,
            llm_calls=[],  # LLM calls tracked in all_llm_calls
        )

        # =================================================================
        # Phase 3: Assembly (Cross-Page Merge)
        # =================================================================
        final_markdowns = await merge_cross_page_paragraphs(
            page_markdowns=final_markdowns,
            page_images=page_images,
            plan=plan,
            ledger=ledger,
            event_bus=event_bus,
        )

        # =================================================================
        # Phase 4: Lint & Fix (Issue Resolution)
        # =================================================================
        final_markdowns, fixes_applied, fixes_failed = await detect_and_fix_issues_async(
            final_markdowns, plan, page_images
        )
        if fixes_applied:
            logger.info(f"Issue fixer applied {len(fixes_applied)} fixes")
        if fixes_failed:
            logger.warning(f"Issue fixer failed on {len(fixes_failed)} issues")

        # =================================================================
        # Phase 5: Verification
        # =================================================================
        verification = await verify_document(
            final_markdowns=final_markdowns,
            page_images=page_images,
            plan=plan,
            ledger=ledger,
            event_bus=event_bus,
            element_bboxes=element_bboxes,
            page_width=page_width,
        )

        # =================================================================
        # Phase 6: Recovery (if needed)
        # =================================================================
        recovery_report: RecoveryReport | None = None
        total_pages = len(page_markdowns)
        pass_rate = verification.pages_passed / total_pages if total_pages > 0 else 0

        if not verification.passed and pass_rate >= 0.5:
            logger.info(f"Verification failed ({verification.pages_passed}/{total_pages} passed), attempting recovery")
            final_markdowns, recovery_report = await run_recovery_phase(
                verification=verification,
                final_markdowns=final_markdowns,
                page_images=page_images,
                ledger=ledger,
                event_bus=event_bus,
            )

        # Determine final status
        final_status = determine_final_status(
            verification=verification,
            recovery_report=recovery_report,
            total_pages=total_pages,
        )

        is_success = final_status in [ProcessingStatus.SUCCESS, ProcessingStatus.PARTIAL_SUCCESS]

        # =================================================================
        # Build final result
        # =================================================================
        total_duration_ms = int((time.time() - start_time) * 1000)

        raw_markdown = "\n\n---\n\n".join(final_markdowns[p] for p in sorted(final_markdowns.keys()))
        final_markdown = collect_footnotes_at_end(raw_markdown)

        cost = (total_input_tokens * 0.00025 / 1000) + (total_output_tokens * 0.00125 / 1000)

        page_confidences = [pv.confidence for pv in verification.pages]
        recovery_edits = recovery_report.total_recovery_edits if recovery_report else 0

        confidence_score = calculate_confidence_from_verification(
            page_confidences=page_confidences,
            critical_issues_count=len(verification.critical_issues),
            recovery_edits=recovery_edits,
        )

        # Sort all LLM calls by timestamp
        all_llm_calls.sort(key=lambda c: c.timestamp)

        result = ProcessingResult(
            document_id=doc_id,
            success=is_success,
            final_markdown=final_markdown,
            ledger=ledger,
            verification=verification,
            confidence_score=confidence_score,
            total_pages=len(page_markdowns),
            total_edits=ledger.total_edits,
            total_jobs=len(all_jobs),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=cost,
            llm_calls=all_llm_calls,
            total_duration_ms=total_duration_ms,
            planning_duration_ms=0,  # Not tracked separately
            execution_duration_ms=execution_duration_ms,
            verification_duration_ms=verification.verification_duration_ms,
            recovery_report=recovery_report,
        )

        # NOTE: ProcessingCompleteEvent is emitted by document_processing_service
        # AFTER markdown is stored to S3, ensuring the URL is available when UI fetches

        logger.info(
            f"Streaming handoff pipeline complete: {ledger.total_edits} edits, "
            f"${cost:.4f}, {total_duration_ms}ms, status={final_status.value}"
        )

        return result, event_bus

    except Exception as e:
        logger.error(f"Streaming handoff pipeline failed: {e}")

        event_bus.emit(
            ProcessingErrorEvent(
                document_id=doc_id,
                error=str(e),
                phase="unknown",
                recoverable=False,
            )
        )

        return (
            ProcessingResult(
                document_id=doc_id,
                success=False,
                final_markdown="",
                ledger=ledger,
                verification=VerificationReport(document_id=doc_id, passed=False),
            ),
            event_bus,
        )


# =============================================================================
# Multi-Round Pipeline Helpers
# =============================================================================


def build_page_boundary_map(
    page_markdowns: dict[int, str],
    merged_markdown: str,
    document_id: str,
) -> PageBoundaryMap:
    """Build a PageBoundaryMap from page markdowns and merged output.

    Maps line numbers in merged_markdown back to source pages.
    This enables pageless processing while retaining the ability to
    reference source page_images for visual context.

    Args:
        page_markdowns: Dict of page_num -> markdown content
        merged_markdown: The merged document markdown
        document_id: Document identifier

    Returns:
        PageBoundaryMap with line-to-page mappings
    """
    boundaries: list[PageBoundary] = []
    current_line = 1  # 1-indexed
    current_char = 0

    # Process pages in order
    for page_num in sorted(page_markdowns.keys()):
        page_md = page_markdowns[page_num]
        page_lines = page_md.split("\n")
        line_count = len(page_lines)
        char_count = len(page_md)

        if line_count > 0:
            boundaries.append(
                PageBoundary(
                    page_num=page_num,
                    start_line=current_line,
                    end_line=current_line + line_count - 1,
                    start_char=current_char,
                    end_char=current_char + char_count,
                )
            )

            current_line += line_count
            current_char += char_count

            # Account for page separator if not last page
            # (merged markdown uses "\n\n---\n\n" between pages = 5 chars, 3 lines)
            if page_num < max(page_markdowns.keys()):
                current_line += 2  # For the separator lines
                current_char += 5  # "\n\n---\n\n"

    total_lines = len(merged_markdown.split("\n"))

    return PageBoundaryMap(
        document_id=document_id,
        boundaries=boundaries,
        total_lines=total_lines,
    )


def check_convergence(
    round_number: int,
    max_rounds: int,
    critic_report: CriticReport | None,
    previous_quality: float,
    current_quality: float,
    quality_threshold: float = 0.85,
) -> tuple[bool, ConvergenceReason | None]:
    """Check if multi-round loop should stop.

    Convergence is checked after each round. The loop stops when any
    of the following conditions are met:

    1. max_rounds reached
    2. No critical issues AND quality >= threshold
    3. No improvement from previous round (quality_delta <= 0)
    4. CriticAgent marks document ready
    5. No issues found by critic

    Args:
        round_number: Current round number (1-indexed)
        max_rounds: Maximum allowed rounds
        critic_report: Report from CriticAgent (None for round 1)
        previous_quality: Quality score from previous round
        current_quality: Quality score from current round
        quality_threshold: Quality threshold for early exit

    Returns:
        Tuple of (should_stop, reason) - reason is None if should continue
    """
    # Check max rounds first
    if round_number >= max_rounds:
        return True, ConvergenceReason.MAX_ROUNDS_REACHED

    # For round 1, we don't have critic report yet
    if critic_report is None:
        return False, None

    # Check if critic marked document ready
    if critic_report.ready_for_output:
        return True, ConvergenceReason.CRITIC_MARKED_READY

    # Check if no issues found
    if len(critic_report.issues) == 0:
        return True, ConvergenceReason.NO_ISSUES_FOUND

    # Check quality threshold (no critical issues + quality met)
    if critic_report.critical_count == 0 and current_quality >= quality_threshold:
        return True, ConvergenceReason.QUALITY_THRESHOLD_MET

    # Check for no improvement (only for round 2+)
    if round_number > 1:
        quality_delta = current_quality - previous_quality
        if quality_delta <= 0:
            return True, ConvergenceReason.NO_IMPROVEMENT

    # Continue processing
    return False, None


def _critic_issue_to_document_job(issue: CriticIssue) -> DocumentJob:
    """Convert a CriticIssue into a DocumentJob.

    Maps critic issue categories to document job types and assigns
    priority based on severity.

    Args:
        issue: The CriticIssue to convert

    Returns:
        DocumentJob targeting the issue location
    """
    # Map category to job type
    category_to_type = {
        "structure": DocumentJobType.STRUCTURE_FIX,
        "accessibility": DocumentJobType.ACCESSIBILITY_FIX,
        "content": DocumentJobType.CONTENT_FIX,
        "formatting": DocumentJobType.FORMATTING_FIX,
    }
    job_type = category_to_type.get(issue.category, DocumentJobType.CONTENT_FIX)

    # Priority based on severity (critical=1, major=2, minor=3, cosmetic=4)
    severity_priority = {"critical": 1, "major": 2, "minor": 3, "cosmetic": 4}
    priority = severity_priority.get(issue.severity.value, 3)

    return DocumentJob(
        job_type=job_type,
        priority=priority,
        line_start=issue.line_start,
        line_end=issue.line_end,
        search_text=issue.search_text,
        issue_description=issue.description,
        suggested_fix=issue.suggested_fix,
        source_pages=issue.source_pages,
    )


# =============================================================================
# Multi-Round Pipeline Main Function
# =============================================================================


async def run_multi_round_pipeline(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_rounds: int = 1,
    max_concurrent_jobs: int = 3,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
    stored_figures: list[StoredFigure] | None = None,
    quality_threshold: float = 0.85,
) -> tuple[RoundLoopResult, EventBus]:
    """Multi-round pipeline wrapper.

    Round 1 runs the existing page-based agentic pipeline.
    Rounds 2+ use CriticAgent to identify issues in merged markdown,
    convert them to DocumentJobs, and execute fixes with DocumentWorker.

    The loop continues until convergence is reached (max rounds, quality
    threshold met, no improvement, or critic marks ready).

    Args:
        filename: Original document filename
        page_markdowns: Initial markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for bbox scaling
        document_id: Optional document ID (generated if not provided)
        max_rounds: Maximum processing rounds (default 1 = single round)
        max_concurrent_jobs: Max concurrent worker jobs
        event_bus: Optional pre-created event bus for streaming
        capture_debug: If True, capture full prompt/response for debug bundle
        stored_figures: List of figures already uploaded to S3
        quality_threshold: Quality score threshold for early exit

    Returns:
        Tuple of (RoundLoopResult, EventBus)
    """
    start_time = time.time()
    doc_id = document_id or str(uuid4())

    # Use provided event bus or create new one
    if event_bus is None:
        event_bus = EventBus(doc_id)

    logger.info(
        f"Starting multi-round pipeline for {filename} (id={doc_id}, max_rounds={max_rounds})"
    )

    # Track round contexts and aggregated stats
    round_contexts: list[RoundContext] = []
    all_llm_calls: list[LLMCallRecord] = []
    total_edits = 0
    total_input_tokens = 0
    total_output_tokens = 0

    # Current state
    current_markdown = ""
    current_quality = 0.0
    previous_quality = 0.0
    convergence_reason: ConvergenceReason | None = None
    page_boundary_map: PageBoundaryMap | None = None

    try:
        # =================================================================
        # Round 1: Run existing page-based pipeline
        # =================================================================
        round_start = time.time()

        if event_bus:
            event_bus.emit(
                RoundStartedEvent(
                    document_id=doc_id,
                    round_number=1,
                    previous_quality=0.0,
                    max_rounds=max_rounds,
                )
            )

        logger.info("Round 1: Running page-based agentic pipeline")

        result, event_bus = await run_agentic_pipeline(
            filename=filename,
            page_markdowns=page_markdowns,
            page_images=page_images,
            element_bboxes=element_bboxes,
            page_width=page_width,
            document_id=doc_id,
            max_concurrent_jobs=max_concurrent_jobs,
            event_bus=event_bus,
            capture_debug=capture_debug,
            stored_figures=stored_figures,
        )

        round_duration_ms = int((time.time() - round_start) * 1000)

        # Extract merged markdown from result
        current_markdown = result.final_markdown
        current_quality = result.confidence_score
        total_edits += result.total_edits
        total_input_tokens += result.total_input_tokens
        total_output_tokens += result.total_output_tokens
        all_llm_calls.extend(result.llm_calls)

        # Build page boundary map for subsequent rounds
        page_boundary_map = build_page_boundary_map(
            page_markdowns=page_markdowns,
            merged_markdown=current_markdown,
            document_id=doc_id,
        )

        # Record round 1 context
        round_1_context = RoundContext(
            round_number=1,
            input_markdown="",  # N/A for round 1
            page_boundary_map=page_boundary_map,
            output_markdown=current_markdown,
            critic_report=None,  # N/A for round 1
            jobs_executed=result.total_jobs,
            edits_applied=result.total_edits,
            duration_ms=round_duration_ms,
            quality_delta=current_quality,  # First round, delta = quality
            input_tokens=result.total_input_tokens,
            output_tokens=result.total_output_tokens,
            llm_calls=result.llm_calls,
        )
        round_contexts.append(round_1_context)

        if event_bus:
            event_bus.emit(
                RoundCompleteEvent(
                    document_id=doc_id,
                    round_number=1,
                    jobs_executed=result.total_jobs,
                    edits_applied=result.total_edits,
                    quality_score=current_quality,
                    quality_delta=current_quality,
                    duration_ms=round_duration_ms,
                )
            )

        logger.info(
            f"Round 1 complete: {result.total_edits} edits, "
            f"quality={current_quality:.2f}, duration={round_duration_ms}ms"
        )

        # Check convergence after round 1
        should_stop, convergence_reason = check_convergence(
            round_number=1,
            max_rounds=max_rounds,
            critic_report=None,
            previous_quality=0.0,
            current_quality=current_quality,
            quality_threshold=quality_threshold,
        )

        # =================================================================
        # Round 2+: Critic -> DocumentJobs -> DocumentWorker loop
        # =================================================================
        current_round = 1

        while not should_stop and current_round < max_rounds:
            current_round += 1
            round_start = time.time()
            previous_quality = current_quality

            # Lazy import to avoid circular imports
            from .critic import run_critic_analysis
            from .document_worker import execute_document_jobs

            if event_bus:
                event_bus.emit(
                    RoundStartedEvent(
                        document_id=doc_id,
                        round_number=current_round,
                        previous_quality=previous_quality,
                        max_rounds=max_rounds,
                    )
                )

            logger.info(f"Round {current_round}: Running CriticAgent analysis")

            # Run CriticAgent on merged markdown
            critic_report = await run_critic_analysis(
                document_id=doc_id,
                merged_markdown=current_markdown,
                page_boundary_map=page_boundary_map,
                page_images=page_images,
                round_number=current_round,
                event_bus=event_bus,
                capture_debug=capture_debug,
            )

            # Track critic tokens
            total_input_tokens += critic_report.input_tokens
            total_output_tokens += critic_report.output_tokens
            if critic_report.llm_call:
                all_llm_calls.append(critic_report.llm_call)

            current_quality = critic_report.overall_quality

            # Check convergence after critic analysis
            should_stop, convergence_reason = check_convergence(
                round_number=current_round,
                max_rounds=max_rounds,
                critic_report=critic_report,
                previous_quality=previous_quality,
                current_quality=current_quality,
                quality_threshold=quality_threshold,
            )

            round_edits = 0
            round_jobs = 0
            round_llm_calls: list[LLMCallRecord] = []
            if critic_report.llm_call:
                round_llm_calls.append(critic_report.llm_call)

            # If not converged and issues found, execute fixes
            if not should_stop and len(critic_report.issues) > 0:
                # Filter to critical and major issues only for fixing
                issues_to_fix = [
                    i for i in critic_report.issues
                    if i.severity.value in ("critical", "major")
                ]

                if issues_to_fix:
                    # Convert issues to DocumentJobs
                    jobs = [_critic_issue_to_document_job(issue) for issue in issues_to_fix]
                    round_jobs = len(jobs)

                    logger.info(
                        f"Round {current_round}: Executing {len(jobs)} DocumentJobs"
                    )

                    # Execute jobs
                    # Create a temporary ledger for this round
                    round_ledger = Ledger(document_id=doc_id)

                    updated_markdown, edits_applied, job_llm_calls = await execute_document_jobs(
                        jobs=jobs,
                        current_markdown=current_markdown,
                        page_images=page_images,
                        page_boundary_map=page_boundary_map,
                        ledger=round_ledger,
                        event_bus=event_bus,
                        capture_debug=capture_debug,
                    )

                    current_markdown = updated_markdown
                    round_edits = edits_applied
                    total_edits += edits_applied
                    round_llm_calls.extend(job_llm_calls)
                    all_llm_calls.extend(job_llm_calls)

                    # Track tokens from worker jobs
                    for call in job_llm_calls:
                        total_input_tokens += call.input_tokens
                        total_output_tokens += call.output_tokens

            round_duration_ms = int((time.time() - round_start) * 1000)
            quality_delta = current_quality - previous_quality

            # Record round context
            round_context = RoundContext(
                round_number=current_round,
                input_markdown=current_markdown,  # Before this round
                page_boundary_map=page_boundary_map,
                output_markdown=current_markdown,  # After this round
                critic_report=critic_report,
                jobs_executed=round_jobs,
                edits_applied=round_edits,
                duration_ms=round_duration_ms,
                quality_delta=quality_delta,
                input_tokens=critic_report.input_tokens + sum(c.input_tokens for c in round_llm_calls[1:]),
                output_tokens=critic_report.output_tokens + sum(c.output_tokens for c in round_llm_calls[1:]),
                llm_calls=round_llm_calls,
            )
            round_contexts.append(round_context)

            if event_bus:
                event_bus.emit(
                    RoundCompleteEvent(
                        document_id=doc_id,
                        round_number=current_round,
                        jobs_executed=round_jobs,
                        edits_applied=round_edits,
                        quality_score=current_quality,
                        quality_delta=quality_delta,
                        duration_ms=round_duration_ms,
                    )
                )

            logger.info(
                f"Round {current_round} complete: {round_edits} edits, "
                f"quality={current_quality:.2f} (delta={quality_delta:+.2f}), "
                f"duration={round_duration_ms}ms"
            )

            # Check convergence again after document worker execution
            if not should_stop:
                should_stop, convergence_reason = check_convergence(
                    round_number=current_round,
                    max_rounds=max_rounds,
                    critic_report=critic_report,
                    previous_quality=previous_quality,
                    current_quality=current_quality,
                    quality_threshold=quality_threshold,
                )

        # =================================================================
        # Finalize: Emit convergence event and build result
        # =================================================================
        total_duration_ms = int((time.time() - start_time) * 1000)
        rounds_completed = len(round_contexts)

        # Default convergence reason if not set
        if convergence_reason is None:
            convergence_reason = ConvergenceReason.MAX_ROUNDS_REACHED

        if event_bus:
            event_bus.emit(
                ConvergenceEvent(
                    document_id=doc_id,
                    reason=convergence_reason.value,
                    total_rounds=rounds_completed,
                    final_quality=current_quality,
                    total_edits=total_edits,
                    total_duration_ms=total_duration_ms,
                )
            )
            # NOTE: ProcessingCompleteEvent is emitted by document_processing_service
            # AFTER markdown is stored to S3, ensuring the URL is available when UI fetches

        logger.info(
            f"Multi-round pipeline complete: {rounds_completed} rounds, "
            f"reason={convergence_reason.value}, quality={current_quality:.2f}, "
            f"total_edits={total_edits}, duration={total_duration_ms}ms"
        )

        # Sort all LLM calls by timestamp
        all_llm_calls.sort(key=lambda c: c.timestamp)

        return (
            RoundLoopResult(
                document_id=doc_id,
                final_markdown=current_markdown,
                final_quality=current_quality,
                rounds_completed=rounds_completed,
                round_contexts=round_contexts,
                convergence_reason=convergence_reason,
                total_edits=total_edits,
                total_duration_ms=total_duration_ms,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                llm_calls=all_llm_calls,
            ),
            event_bus,
        )

    except Exception as e:
        logger.error(f"Multi-round pipeline failed: {e}")

        if event_bus:
            event_bus.emit(
                ProcessingErrorEvent(
                    document_id=doc_id,
                    error=str(e),
                    phase="multi_round",
                    recoverable=False,
                )
            )

        total_duration_ms = int((time.time() - start_time) * 1000)

        return (
            RoundLoopResult(
                document_id=doc_id,
                final_markdown=current_markdown or "",
                final_quality=current_quality,
                rounds_completed=len(round_contexts),
                round_contexts=round_contexts,
                convergence_reason=ConvergenceReason.ERROR,
                total_edits=total_edits,
                total_duration_ms=total_duration_ms,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                llm_calls=all_llm_calls,
            ),
            event_bus,
        )


# =============================================================================
# Async Generator for Streaming
# =============================================================================


async def run_agentic_pipeline_streaming(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_concurrent_jobs: int = 3,
) -> AsyncGenerator[StreamEvent, None]:
    """Process document with streaming events.

    This is an async generator that yields events as they occur.
    Use this for SSE streaming.

    Yields:
        StreamEvent instances
    """
    doc_id = document_id or str(uuid4())
    event_bus = EventBus(doc_id)

    # Subscribe to events
    queue = event_bus.subscribe()

    # Start processing in background
    async def run_pipeline() -> None:
        try:
            await run_agentic_pipeline(
                filename=filename,
                page_markdowns=page_markdowns,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                document_id=doc_id,
                max_concurrent_jobs=max_concurrent_jobs,
            )
        except Exception as e:
            logger.error(f"Pipeline error: {e}")

    # Start the pipeline task
    task = asyncio.create_task(run_pipeline())

    # Yield events as they come
    try:
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield event
            except TimeoutError:
                continue

        # Get any remaining events
        while not queue.empty():
            event = queue.get_nowait()
            yield event

    finally:
        event_bus.unsubscribe(queue)
