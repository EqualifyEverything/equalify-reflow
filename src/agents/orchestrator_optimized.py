"""V5 Pipeline Optimized Orchestrator - Five-Phase Architecture.

This orchestrator implements the optimized pipeline with:
    1. Context Gathering Phase - Parallel extraction + single LLM structure inference
    2. Issue Detection Phase - Parallel detection with full context
    3. Issue Fixing Phase - Deterministic + LLM-based fixes
    4. Verification Phase - Quality gate
    5. Final Pass Phase - Pageless reading optimization (removes --- separators)

Key Differences from Original:
    - Structure inference happens in ONE LLM call (not N sequential)
    - Page summaries happen in PARALLEL (not chained)
    - Issue detection has FULL context (not accumulated forward-only)
    - Fixes are applied AFTER detection (not mixed with execution)
    - Final pass optimizes for continuous digital reading (no page breaks)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from .context_gatherer import gather_document_context
from .events import (
    EditCommittedEvent,
    EventBus,
    FinalPassCompleteEvent,
    FinalPassStartedEvent,
    JobCompletedEvent,
    JobCreatedEvent,
    JobStartedEvent,
    PageVerifiedEvent,
    PlanningCompleteEvent,
    PlanningStartedEvent,
    ProcessingCompleteEvent,
    ProcessingErrorEvent,
    StructureInferredEvent,
    VerificationCompleteEvent,
    VerificationStartedEvent,
)
from .issue_detector import (
    detect_all_issues,
    get_critical_issues,
    get_fixable_issues,
    summarize_issues,
)
from .issue_fixer import (
    IssueType,
    StructuredIssue,
    fix_heading_level,
    fix_missing_alt_text,
    fix_table_not_transcribed,
    optimize_for_pageless_reading,
)
from .models import (
    DocumentContext,
    Ledger,
    LedgerEntry,
    PageVerification,
    ProcessingResult,
    ProcessingStatus,
    RecoveryReport,
    TaskType,
    VerificationReport,
)
from .recovery import determine_final_status

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Issue Fixing Phase
# =============================================================================


async def apply_deterministic_fixes(
    page_markdowns: dict[int, str],
    issues: list[StructuredIssue],
    ledger: Ledger,
) -> tuple[dict[int, str], list[str], list[str]]:
    """Apply deterministic fixes (no LLM needed).

    Args:
        page_markdowns: Current markdown per page
        issues: List of issues to fix
        ledger: Ledger for recording changes

    Returns:
        Tuple of (updated_markdowns, fixes_applied, fixes_failed)
    """
    updated = dict(page_markdowns)
    applied: list[str] = []
    failed: list[str] = []

    for issue in issues:
        if issue.issue_type == IssueType.WRONG_HEADING_LEVEL:
            page_md = updated.get(issue.page, "")
            result = fix_heading_level(page_md, issue)

            if result.success and result.fixed_text:
                updated[issue.page] = result.fixed_text
                applied.append(f"Page {issue.page}: {issue.description}")

                # Record in ledger
                ledger.append(
                    LedgerEntry(
                        job_id="deterministic-fix",
                        page=issue.page,
                        action=TaskType.HEADING_FIX,
                        target=issue.actual_text or "heading",
                        before=issue.actual_text or "",
                        after=issue.suggested_fix or "",
                        reasoning="Heading level corrected per authoritative outline",
                        validated=True,
                        confidence=0.95,
                    )
                )
            else:
                failed.append(f"Page {issue.page}: {issue.description} - {result.error}")

    return updated, applied, failed


async def apply_llm_fixes(
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    issues: list[StructuredIssue],
    context: DocumentContext,
    ledger: Ledger,
    max_concurrent: int = 3,
    event_bus: EventBus | None = None,
) -> tuple[dict[int, str], list[str], list[str]]:
    """Apply LLM-based fixes (alt-text, tables).

    Args:
        page_markdowns: Current markdown per page
        page_images: Page images for visual analysis
        issues: List of issues requiring LLM
        context: Document context
        ledger: Ledger for recording changes
        max_concurrent: Max concurrent LLM calls

    Returns:
        Tuple of (updated_markdowns, fixes_applied, fixes_failed)
    """
    updated = dict(page_markdowns)
    applied: list[str] = []
    failed: list[str] = []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def fix_issue(issue: StructuredIssue) -> tuple[bool, str, str | None, str | None]:
        """Returns (success, message, fixed_text, generated_content)."""
        async with semaphore:
            page_md = updated.get(issue.page, "")
            page_image = page_images.get(issue.page)

            if page_image is None:
                return False, f"No image for page {issue.page}", None, None

            try:
                if issue.issue_type in (IssueType.MISSING_ALT_TEXT, IssueType.UNFILLED_PLACEHOLDER):
                    # Get figure context from page summary if available
                    figure_context = None
                    page_summary = context.page_summaries.get(issue.page)
                    if page_summary:
                        figure_context = type(
                            "FigureContext",
                            (),
                            {
                                "appears_to_be": "figure",
                                "surrounding_text": page_summary.summary,
                                "location": None,  # Optional field
                            },
                        )()

                    # For unfilled placeholders, we need to convert the issue to alt-text format
                    alt_text_issue = issue
                    if issue.issue_type == IssueType.UNFILLED_PLACEHOLDER:
                        # Create a modified issue that fix_missing_alt_text can handle
                        alt_text_issue = StructuredIssue(
                            issue_type=IssueType.MISSING_ALT_TEXT,
                            page=issue.page,
                            severity=issue.severity,
                            actual_text=issue.actual_text,
                            surrounding_context=issue.surrounding_context,
                            description=f"Generate figure description for: {issue.actual_text}",
                        )

                    result = await fix_missing_alt_text(
                        markdown=page_md,
                        issue=alt_text_issue,
                        page_image=page_image,
                        figure_context=figure_context,
                    )

                    if result.success and result.fixed_text:
                        return True, f"Page {issue.page}: Figure description generated", result.fixed_text, result.generated_content
                    else:
                        return False, f"Page {issue.page}: {result.error}", None, None

                elif issue.issue_type == IssueType.TABLE_NOT_TRANSCRIBED:
                    result = await fix_table_not_transcribed(
                        markdown=page_md,
                        issue=issue,
                        page_image=page_image,
                        table_context=None,
                    )

                    if result.success and result.fixed_text:
                        return True, f"Page {issue.page}: Table transcribed", result.fixed_text, result.generated_content
                    else:
                        return False, f"Page {issue.page}: {result.error}", None, None

                else:
                    return False, f"Unknown LLM issue type: {issue.issue_type}", None, None

            except Exception as e:
                return False, f"Page {issue.page}: Exception - {e}", None, None

    # Run fixes in parallel
    tasks = [fix_issue(issue) for issue in issues]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        issue = issues[i]

        if isinstance(result, Exception):
            failed.append(f"Page {issue.page}: {result}")
            continue

        success, message, fixed_text, generated_content = result

        if success and fixed_text:
            updated[issue.page] = fixed_text
            applied.append(message)

            # Record in ledger with actual generated content
            action = (
                TaskType.ALT_TEXT
                if issue.issue_type in (IssueType.MISSING_ALT_TEXT, IssueType.UNFILLED_PLACEHOLDER)
                else TaskType.TABLE_TRANSCRIPTION
            )
            # Truncate for ledger display but show meaningful content
            after_text = generated_content[:500] if generated_content else "[generated]"
            entry = LedgerEntry(
                job_id="llm-fix",
                page=issue.page,
                action=action,
                target=issue.actual_text or "element",
                before=issue.actual_text or "",
                after=after_text,
                reasoning="Generated via LLM analysis",
                validated=True,
                confidence=0.85,
            )
            ledger.append(entry)

            # Emit edit committed event
            if event_bus:
                event_bus.emit(
                    EditCommittedEvent(
                        document_id=event_bus.document_id,
                        ledger_entry=entry,
                        content_preview=generated_content[:200] if generated_content else "",
                    )
                )
        else:
            failed.append(message)

    return updated, applied, failed


# =============================================================================
# Verification Phase (Simplified)
# =============================================================================


async def verify_optimized(
    final_markdowns: dict[int, str],
    context: DocumentContext,
    event_bus: EventBus | None = None,
) -> VerificationReport:
    """Run simplified verification after fixes.

    This re-runs issue detection and checks if critical issues remain.

    Args:
        final_markdowns: Final markdown per page
        context: Document context
        event_bus: Optional event bus

    Returns:
        VerificationReport
    """
    import re

    start_time = time.time()

    if event_bus:
        event_bus.emit(
            VerificationStartedEvent(
                document_id=context.document_id,
                pages_to_verify=len(final_markdowns),
            )
        )

    # Re-run issue detection
    remaining_issues = await detect_all_issues(final_markdowns, context)

    page_results: list[PageVerification] = []
    all_issues: list[str] = []
    critical_issues: list[str] = []

    for page_num in sorted(final_markdowns.keys()):
        page_issues = remaining_issues.get(page_num, [])
        issues_str = [issue.description for issue in page_issues]
        critical = [
            issue.description
            for issue in page_issues
            if issue.severity == "critical"
        ]

        passed = len(critical) == 0

        page_results.append(
            PageVerification(
                page_num=page_num,
                passed=passed,
                issues=issues_str,
                confidence=0.9 if passed else 0.5,
            )
        )

        all_issues.extend(issues_str)
        critical_issues.extend(critical)

        if event_bus:
            event_bus.emit(
                PageVerifiedEvent(
                    document_id=context.document_id,
                    page_num=page_num,
                    passed=passed,
                    issues=issues_str,
                )
            )

    duration_ms = int((time.time() - start_time) * 1000)

    pages_passed = sum(1 for p in page_results if p.passed)
    overall_passed = len(critical_issues) == 0 and pages_passed >= len(page_results) * 0.8

    report = VerificationReport(
        document_id=context.document_id,
        passed=overall_passed,
        pages=page_results,
        total_issues=len(all_issues),
        critical_issues=critical_issues,
        warnings=[i for i in all_issues if i not in critical_issues],
        pages_passed=pages_passed,
        pages_failed=len(page_results) - pages_passed,
        verification_duration_ms=duration_ms,
    )

    if event_bus:
        event_bus.emit(
            VerificationCompleteEvent(
                document_id=context.document_id,
                passed=overall_passed,
                pages_passed=pages_passed,
                pages_failed=len(page_results) - pages_passed,
                total_issues=len(all_issues),
                critical_issues=critical_issues,
                duration_ms=duration_ms,
            )
        )

    return report


# =============================================================================
# Main Orchestrator
# =============================================================================


async def process_document_v5_optimized(
    filename: str,
    page_markdowns: dict[int, str],
    page_images: dict[int, Image.Image],
    document_id: str | None = None,
    max_concurrent_fixes: int = 3,
    event_bus: EventBus | None = None,
) -> tuple[ProcessingResult, EventBus]:
    """Run the optimized V5 pipeline.

    This is the main entry point for the optimized two-phase architecture.

    Args:
        filename: Original document filename
        page_markdowns: Initial markdown for each page (1-indexed)
        page_images: Images for each page (1-indexed)
        document_id: Optional document ID
        max_concurrent_fixes: Max concurrent LLM fix calls
        event_bus: Optional event bus for streaming

    Returns:
        Tuple of (ProcessingResult, EventBus)
    """
    start_time = time.time()
    doc_id = document_id or str(uuid4())

    if event_bus is None:
        event_bus = EventBus(doc_id)

    ledger = Ledger(document_id=doc_id)

    logger.info(f"Starting optimized V5 pipeline for {filename} (id={doc_id})")

    try:
        # =================================================================
        # Phase 1: Context Gathering (Planning)
        # =================================================================
        event_bus.emit(
            PlanningStartedEvent(
                document_id=doc_id,
                filename=filename,
                total_pages=len(page_markdowns),
            )
        )

        logger.info("Phase 1: Context Gathering")
        context = await gather_document_context(
            filename=filename,
            page_markdowns=page_markdowns,
            event_bus=event_bus,
        )

        logger.info(
            f"Context gathered: '{context.title}' ({context.document_type.value}), "
            f"{len(context.authoritative_outline)} outline entries, "
            f"{len(context.heading_fixes)} heading fixes needed"
        )

        # Emit structure inferred event with document plan
        event_bus.emit(
            StructureInferredEvent(
                document_id=doc_id,
                document_title=context.title,
                document_type=context.document_type,
                outline=[
                    {"heading": e.heading, "level": e.level, "page": e.page_start}
                    for e in context.authoritative_outline
                ],
                dictionary_size=len(context.dictionary),
                heading_fixes_needed=len(context.heading_fixes),
            )
        )

        # =================================================================
        # Phase 2: Issue Detection
        # =================================================================
        logger.info("Phase 2: Issue Detection")
        issues_by_page = await detect_all_issues(page_markdowns, context)

        issue_summary = summarize_issues(issues_by_page)
        total_issues = sum(issue_summary.values())
        logger.info(f"Detected {total_issues} issues: {issue_summary}")

        # Separate fixable issues
        deterministic_issues, llm_issues = get_fixable_issues(issues_by_page)
        all_issues = deterministic_issues + llm_issues

        # Create jobs for each issue and emit job events
        job_ids: dict[int, str] = {}  # issue index -> job_id
        for i, issue in enumerate(all_issues):
            job_id = f"fix-{i+1:03d}"
            job_ids[i] = job_id
            task_type = "heading_fix" if issue.issue_type == IssueType.WRONG_HEADING_LEVEL else issue.issue_type.value
            event_bus.emit(
                JobCreatedEvent(
                    document_id=doc_id,
                    job_id=job_id,
                    job_type="content",
                    page=issue.page,
                    task_count=1,
                    task_types=[task_type],
                    section_context=issue.description,
                )
            )

        # Emit planning complete
        event_bus.emit(
            PlanningCompleteEvent(
                document_id=doc_id,
                total_jobs=len(all_issues),
                structure_jobs=len(deterministic_issues),
                content_jobs=len(llm_issues),
                dictionary_terms=context.dictionary[:20],
                duration_ms=context.extraction_duration_ms + context.inference_duration_ms,
            )
        )

        # =================================================================
        # Phase 3: Issue Fixing
        # =================================================================
        logger.info("Phase 3: Issue Fixing")
        current_markdowns = dict(page_markdowns)
        all_applied: list[str] = []
        all_failed: list[str] = []
        jobs_completed = 0

        # 3a. Deterministic fixes (fast, no LLM)
        if deterministic_issues:
            logger.info(f"Applying {len(deterministic_issues)} deterministic fixes")
            for i, issue in enumerate(deterministic_issues):
                job_id = job_ids[i]
                event_bus.emit(JobStartedEvent(document_id=doc_id, job_id=job_id, page=issue.page))

                page_md = current_markdowns.get(issue.page, "")
                result = fix_heading_level(page_md, issue)

                if result.success and result.fixed_text:
                    current_markdowns[issue.page] = result.fixed_text
                    all_applied.append(f"Page {issue.page}: {issue.description}")

                    # Create ledger entry
                    entry = LedgerEntry(
                        job_id=job_id,
                        page=issue.page,
                        action=TaskType.HEADING_FIX,
                        target=issue.actual_text or "heading",
                        before=issue.actual_text or "",
                        after=issue.suggested_fix or "",
                        reasoning="Heading level corrected per authoritative outline",
                        validated=True,
                        confidence=0.95,
                    )
                    ledger.append(entry)

                    # Emit edit committed
                    event_bus.emit(
                        EditCommittedEvent(
                            document_id=doc_id,
                            ledger_entry=entry,
                            content_preview=issue.suggested_fix or "",
                        )
                    )

                    event_bus.emit(
                        JobCompletedEvent(
                            document_id=doc_id, job_id=job_id, page=issue.page, edits_made=1, success=True
                        )
                    )
                else:
                    all_failed.append(f"Page {issue.page}: {issue.description} - {result.error}")
                    event_bus.emit(
                        JobCompletedEvent(
                            document_id=doc_id, job_id=job_id, page=issue.page, edits_made=0, success=False
                        )
                    )
                jobs_completed += 1

        # 3b. LLM fixes (alt-text, tables)
        if llm_issues:
            logger.info(f"Applying {len(llm_issues)} LLM-based fixes")
            logger.info(f"Page images available: {sorted(page_images.keys())}")
            logger.info(f"Issue pages: {[i.page for i in llm_issues]}")

            # Emit job started for all LLM jobs
            for i, issue in enumerate(llm_issues):
                job_id = job_ids[len(deterministic_issues) + i]
                event_bus.emit(JobStartedEvent(document_id=doc_id, job_id=job_id, page=issue.page))

            current_markdowns, applied, failed = await apply_llm_fixes(
                page_markdowns=current_markdowns,
                page_images=page_images,
                issues=llm_issues,
                context=context,
                ledger=ledger,
                max_concurrent=max_concurrent_fixes,
                event_bus=event_bus,
            )

            # Emit job completed for LLM jobs
            for i, issue in enumerate(llm_issues):
                job_id = job_ids[len(deterministic_issues) + i]
                success = any(f"Page {issue.page}" in a for a in applied)
                event_bus.emit(
                    JobCompletedEvent(
                        document_id=doc_id,
                        job_id=job_id,
                        page=issue.page,
                        edits_made=1 if success else 0,
                        success=success,
                    )
                )
                jobs_completed += 1

            all_applied.extend(applied)
            all_failed.extend(failed)
            if failed:
                for f in failed:
                    logger.warning(f"LLM fix failed: {f}")

        logger.info(
            f"Fixes: {len(all_applied)} applied, {len(all_failed)} failed"
        )

        # =================================================================
        # Phase 4: Verification
        # =================================================================
        logger.info("Phase 4: Verification")
        verification = await verify_optimized(
            final_markdowns=current_markdowns,
            context=context,
            event_bus=event_bus,
        )

        # =================================================================
        # Determine Final Status
        # =================================================================
        recovery_report: RecoveryReport | None = None
        final_status = determine_final_status(
            verification=verification,
            recovery_report=recovery_report,
            total_pages=len(page_markdowns),
        )

        is_success = final_status in [
            ProcessingStatus.SUCCESS,
            ProcessingStatus.PARTIAL_SUCCESS,
        ]

        # =================================================================
        # Phase 5: Final Pass - Pageless Optimization
        # =================================================================
        logger.info("Phase 5: Final Pass (Pageless Optimization)")

        # First join pages with separators
        joined_markdown = "\n\n---\n\n".join(
            current_markdowns[p] for p in sorted(current_markdowns.keys())
        )

        # Count page separators
        separator_count = joined_markdown.count("\n\n---\n\n")

        # Emit final pass started event
        event_bus.emit(
            FinalPassStartedEvent(
                document_id=doc_id,
                page_separators_found=separator_count,
            )
        )

        # Run final pass to optimize for pageless reading
        final_markdown, final_pass_changes = await optimize_for_pageless_reading(
            markdown=joined_markdown,
            document_title=context.title or filename,
            max_retries=1,
        )

        # Emit edit events for each final pass change (so humans can review)
        for change_description in final_pass_changes:
            entry = LedgerEntry(
                job_id="final-pass",
                page=0,  # Document-wide change
                action=TaskType.PAGELESS_OPTIMIZATION,
                target="document",
                before="(page-separated)",
                after="(continuous)",
                reasoning=change_description,
                validated=True,
                confidence=0.9,
            )
            ledger.append(entry)

            event_bus.emit(
                EditCommittedEvent(
                    document_id=doc_id,
                    ledger_entry=entry,
                    content_preview=change_description[:100],
                )
            )

        # Emit final pass complete event
        event_bus.emit(
            FinalPassCompleteEvent(
                document_id=doc_id,
                changes_made=len(final_pass_changes),
                success=True,
            )
        )

        if final_pass_changes:
            logger.info(f"Final pass made {len(final_pass_changes)} optimizations")

        # =================================================================
        # Build Result
        # =================================================================
        total_duration_ms = int((time.time() - start_time) * 1000)

        # Token usage from context gathering
        total_input_tokens = context.total_tokens_input
        total_output_tokens = context.total_tokens_output

        # Estimate cost (Haiku pricing)
        cost = (total_input_tokens * 0.00025 / 1000) + (
            total_output_tokens * 0.00125 / 1000
        )

        result = ProcessingResult(
            document_id=doc_id,
            success=is_success,
            final_markdown=final_markdown,
            ledger=ledger,
            verification=verification,
            total_pages=len(page_markdowns),
            total_edits=ledger.total_edits,
            total_jobs=len(all_issues),  # Jobs = issues fixed
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=cost,
            total_duration_ms=total_duration_ms,
            planning_duration_ms=(
                context.extraction_duration_ms
                + context.inference_duration_ms
                + context.summary_duration_ms
            ),
            execution_duration_ms=0,  # No separate execution phase
            verification_duration_ms=verification.verification_duration_ms,
            recovery_report=recovery_report,
        )

        event_bus.emit(
            ProcessingCompleteEvent(
                document_id=doc_id,
                success=is_success,
                total_edits=ledger.total_edits,
                total_jobs=len(all_issues),
                total_cost=cost,
                total_duration_ms=total_duration_ms,
                result_url=f"/api/v5/jobs/{doc_id}/result",
            )
        )

        logger.info(
            f"Optimized V5 pipeline complete: {ledger.total_edits} edits, "
            f"${cost:.4f}, {total_duration_ms}ms, status={final_status.value}"
        )

        return result, event_bus

    except Exception as e:
        logger.error(f"Optimized V5 pipeline failed: {e}")

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
                verification=VerificationReport(
                    document_id=doc_id,
                    passed=False,
                ),
            ),
            event_bus,
        )
