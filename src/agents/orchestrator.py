"""V5 Pipeline Orchestrator - Main Pipeline Coordination.

The Orchestrator runs the complete V5 pipeline:
    1. Planning Phase - Analyze document, create jobs
    2. Execution Phase - Run worker jobs in parallel
    3. Verification Phase - Final quality check

It manages the event bus for streaming and coordinates
all components.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from ..utils.confidence_scoring import calculate_confidence_from_verification
from .events import (
    EventBus,
    PageVerifiedEvent,
    ProcessingCompleteEvent,
    ProcessingErrorEvent,
    RecoveryPhaseCompleteEvent,
    RecoveryPhaseStartedEvent,
    VerificationCompleteEvent,
    VerificationStartedEvent,
)
from .issue_fixer import detect_and_fix_issues_async
from .models import (
    DocumentPlan,
    Ledger,
    PageVerification,
    ProcessingResult,
    ProcessingStatus,
    RecoveryAttemptStatus,
    RecoveryReport,
    VerificationReport,
)
from .plan_verification import (
    verify_figure_completeness,
    verify_heading_structure,
    verify_spelling,
    verify_table_accuracy_vision,
    verify_table_completeness,
)
from .planner import plan_document
from .recovery import (
    MAX_RECOVERY_ATTEMPTS,
    attempt_page_recovery,
    determine_final_status,
    should_attempt_recovery,
)
from .worker import execute_jobs_parallel

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


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


async def process_document_v5(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_concurrent_jobs: int = 3,
    event_bus: EventBus | None = None,
) -> tuple[ProcessingResult, EventBus]:
    """Run the complete V5 pipeline.

    This is the main entry point for V5 processing.

    Args:
        filename: Original document filename
        page_markdowns: Initial markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for bbox scaling
        document_id: Optional document ID (generated if not provided)
        max_concurrent_jobs: Max concurrent worker jobs
        event_bus: Optional pre-created event bus for streaming

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

    logger.info(f"Starting V5 pipeline for {filename} (id={doc_id})")

    try:
        # =================================================================
        # Phase 1: Planning
        # =================================================================
        plan = await plan_document(
            filename=filename,
            page_markdowns=page_markdowns,
            page_images=page_images,
            event_bus=event_bus,
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
        )

        execution_duration_ms = int((time.time() - execution_start) * 1000)

        # Build final markdowns from job results
        final_markdowns = dict(page_markdowns)  # Start with original
        for result in job_results:
            if result.success:
                # Find the page for this job
                job = next((j for j in plan.jobs if j.job_id == result.job_id), None)
                if job:
                    final_markdowns[job.page] = result.updated_markdown

        # Aggregate execution stats
        total_input_tokens = plan.planning_tokens_input + sum(r.input_tokens for r in job_results)
        total_output_tokens = plan.planning_tokens_output + sum(r.output_tokens for r in job_results)

        # =================================================================
        # Phase 2.5: Structured Issue Detection & Fixing
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
        # Phase 3: Verification
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
        # Phase 4: Recovery (if needed)
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
        final_markdown = "\n\n---\n\n".join(final_markdowns[p] for p in sorted(final_markdowns.keys()))

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

        result = ProcessingResult(
            document_id=doc_id,
            success=is_success,
            final_markdown=final_markdown,
            ledger=ledger,
            verification=verification,
            confidence_score=confidence_score,
            total_pages=len(page_markdowns),
            total_edits=ledger.total_edits,
            total_jobs=len(plan.jobs),
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=cost,
            total_duration_ms=total_duration_ms,
            planning_duration_ms=plan.planning_duration_ms,
            execution_duration_ms=execution_duration_ms,
            verification_duration_ms=verification.verification_duration_ms,
            recovery_report=recovery_report,
        )

        event_bus.emit(
            ProcessingCompleteEvent(
                document_id=doc_id,
                success=is_success,
                total_edits=ledger.total_edits,
                total_jobs=len(plan.jobs),
                total_cost=cost,
                total_duration_ms=total_duration_ms,
                result_url=f"/api/v1/documents/{doc_id}",
            )
        )

        logger.info(
            f"V5 pipeline complete: {ledger.total_edits} edits, "
            f"${cost:.4f}, {total_duration_ms}ms, status={final_status.value}"
        )

        return result, event_bus

    except Exception as e:
        logger.error(f"V5 pipeline failed: {e}")

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
# Async Generator for Streaming
# =============================================================================


async def process_document_v5_streaming(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    document_id: str | None = None,
    max_concurrent_jobs: int = 3,
):
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
    async def run_pipeline():
        try:
            await process_document_v5(
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
