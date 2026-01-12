"""Recovery Agent - Attempts to Fix Failed Pages.

The Recovery Agent attempts to fix pages that failed verification.
It's more conservative than the Worker agent - when in doubt, it escalates.

Recovery actions:
    - cleanup_edit: Fix minor issues like placeholder removal
    - remove_placeholder: Remove unfillable placeholders with explanation
    - accept_with_caveat: Accept page with documented limitations
    - escalate: Mark for human review (no automatic fix possible)

Max 2 attempts per page. If still failing after 2 attempts, escalate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

from .events import (
    EventBus,
    RecoveryCompleteEvent,
    RecoveryEditAppliedEvent,
    RecoveryStartedEvent,
)
from .models import (
    IssueCategory,
    PageVerification,
    ProcessingStatus,
    RecoveryAction,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryEdit,
    RecoveryReport,
    VerificationReport,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

MAX_RECOVERY_ATTEMPTS = 2


# =============================================================================
# Recovery Dependencies
# =============================================================================


@dataclass
class RecoveryDeps:
    """Dependencies passed to recovery agent tools."""

    page_num: int
    page_image: Image.Image
    current_markdown: str
    issues: list[str]

    # History from previous processing
    processing_history: list[str]

    # Event bus for streaming
    event_bus: EventBus | None = None

    # Mutable state
    edits_applied: list[RecoveryEdit] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


# =============================================================================
# Tool Return Types
# =============================================================================


class ViewPageResult(BaseModel):
    """Result from viewing the page."""

    markdown_content: str = Field(..., description="Current markdown")
    line_count: int = Field(..., description="Number of lines")


class ViewMarkdownResult(BaseModel):
    """Result from viewing markdown at specific lines."""

    content: str = Field(..., description="Requested lines")
    start_line: int = Field(..., description="First line returned")
    end_line: int = Field(..., description="Last line returned")


class ViewHistoryResult(BaseModel):
    """Result from viewing processing history."""

    history: list[str] = Field(..., description="Processing history entries")


class ProposedCleanup(BaseModel):
    """A proposed cleanup edit."""

    action: RecoveryAction = Field(..., description="Type of recovery action")
    target: str = Field(..., description="What is being fixed")
    before: str = Field(..., description="Original text to replace")
    after: str = Field(..., description="Replacement text")
    reasoning: str = Field(..., description="Why this fix is appropriate")


class ProposeCleanupResult(BaseModel):
    """Result from proposing a cleanup."""

    accepted: bool = Field(..., description="Whether cleanup was applied")
    feedback: str | None = Field(
        default=None,
        description="Feedback if rejected",
    )


# =============================================================================
# Recovery Agent Output
# =============================================================================


class RecoveryOutput(BaseModel):
    """Output from recovery agent."""

    issues_addressed: list[str] = Field(
        default_factory=list,
        description="Issues that were fixed",
    )
    issues_accepted_with_caveats: list[str] = Field(
        default_factory=list,
        description="Issues accepted with documented caveats",
    )
    issues_escalated: list[str] = Field(
        default_factory=list,
        description="Issues escalated for human review",
    )
    summary: str = Field(
        default="",
        description="Summary of recovery actions taken",
    )


# =============================================================================
# Issue Categorization
# =============================================================================


def categorize_issues(issues: list[str]) -> dict[IssueCategory, list[str]]:
    """Categorize verification issues by type for prioritization.

    Args:
        issues: List of issue strings from verification

    Returns:
        Dict mapping issue category to list of issues in that category
    """
    categorized: dict[IssueCategory, list[str]] = {category: [] for category in IssueCategory}

    for issue in issues:
        issue_lower = issue.lower()

        if "placeholder" in issue_lower or "unfilled" in issue_lower:
            categorized[IssueCategory.UNFILLED_PLACEHOLDER].append(issue)
        elif "alt-text" in issue_lower or "alt text" in issue_lower or "without alt" in issue_lower:
            categorized[IssueCategory.MISSING_ALT_TEXT].append(issue)
        elif "heading" in issue_lower or "hierarchy" in issue_lower or "skip" in issue_lower:
            categorized[IssueCategory.HEADING_HIERARCHY].append(issue)
        elif "format" in issue_lower or "syntax" in issue_lower or "pipe" in issue_lower:
            categorized[IssueCategory.FORMATTING].append(issue)
        else:
            # Default to cosmetic for unrecognized issues
            categorized[IssueCategory.COSMETIC].append(issue)

    return categorized


def should_attempt_recovery(
    verification: PageVerification,
    attempt_number: int,
) -> bool:
    """Determine if recovery should be attempted for a page.

    Args:
        verification: The page verification result
        attempt_number: Which attempt this would be (1 or 2)

    Returns:
        True if recovery should be attempted
    """
    # Don't attempt if page passed
    if verification.passed:
        return False

    # Don't attempt more than MAX_RECOVERY_ATTEMPTS
    if attempt_number > MAX_RECOVERY_ATTEMPTS:
        return False

    # Don't attempt if no issues to fix
    if not verification.issues:
        return False

    # Categorize issues to check if they're recoverable
    categorized = categorize_issues(verification.issues)

    # Recoverable categories
    recoverable_categories = [
        IssueCategory.UNFILLED_PLACEHOLDER,
        IssueCategory.MISSING_ALT_TEXT,
        IssueCategory.HEADING_HIERARCHY,
        IssueCategory.FORMATTING,
    ]

    # Check if any issues are in recoverable categories
    recoverable_count = sum(len(categorized[cat]) for cat in recoverable_categories)

    # Attempt recovery if at least one recoverable issue exists
    return recoverable_count > 0


# =============================================================================
# System Prompt
# =============================================================================


RECOVERY_SYSTEM_PROMPT = """You are a document recovery specialist. Your job is to fix pages that failed verification.

## Your Role
You receive pages that failed initial processing. Your goal is to:
1. Fix what can be fixed with simple edits
2. Accept limitations with documented caveats when appropriate
3. Escalate truly unfixable issues for human review

## Available Tools

### view_page_tool()
Get the current markdown content for the page.

### view_markdown_tool(start_line, end_line)
View specific lines of the markdown.

### view_processing_history_tool()
See what was attempted during initial processing.

### propose_cleanup_tool(action, target, before, after, reasoning)
Propose a cleanup edit. Actions:
- cleanup_edit: Fix minor formatting or content issues
- remove_placeholder: Remove unfillable placeholder with note
- accept_with_caveat: Accept issue with documented caveat
- escalate: Mark for human review

## Recovery Guidelines

### DO:
- Remove unfillable placeholders (e.g., images that couldn't be processed)
- Fix simple formatting issues (trailing pipes, whitespace)
- Accept pages with minor cosmetic issues using caveats
- Escalate genuinely problematic issues

### DON'T:
- Guess at content that wasn't provided
- Make up alt-text for images you can't see
- Force fixes that might introduce errors
- Ignore critical issues

## Conservative Approach
When in doubt, use accept_with_caveat or escalate.
It's better to flag an issue than to introduce errors.

## Output
List which issues you addressed, accepted with caveats, or escalated.
"""


# =============================================================================
# Tool Implementations
# =============================================================================


async def view_page_tool(ctx: RunContext[RecoveryDeps]) -> ViewPageResult:
    """View the full page markdown content.

    Returns the current markdown and line count for reference.
    """
    deps = ctx.deps
    lines = deps.current_markdown.split("\n")

    return ViewPageResult(
        markdown_content=deps.current_markdown,
        line_count=len(lines),
    )


async def view_markdown_tool(
    ctx: RunContext[RecoveryDeps],
    start_line: int,
    end_line: int,
) -> ViewMarkdownResult:
    """View specific lines of the markdown.

    Args:
        start_line: First line to view (1-indexed)
        end_line: Last line to view (inclusive)
    """
    deps = ctx.deps
    lines = deps.current_markdown.split("\n")
    total = len(lines)

    # Clamp to valid range
    start = max(1, start_line) - 1
    end = min(total, end_line)

    content = "\n".join(lines[start:end])

    return ViewMarkdownResult(
        content=content,
        start_line=start_line,
        end_line=end_line,
    )


async def view_processing_history_tool(
    ctx: RunContext[RecoveryDeps],
) -> ViewHistoryResult:
    """View what was attempted during initial processing.

    This helps understand why issues weren't fixed initially.
    """
    return ViewHistoryResult(history=ctx.deps.processing_history)


async def propose_cleanup_tool(
    ctx: RunContext[RecoveryDeps],
    action: str,
    target: str,
    before: str,
    after: str,
    reasoning: str,
) -> ProposeCleanupResult:
    """Propose a cleanup edit.

    Args:
        action: Recovery action (cleanup_edit, remove_placeholder, accept_with_caveat, escalate)
        target: What is being fixed (e.g., "image placeholder", "heading")
        before: Exact text to replace (must exist in markdown)
        after: Replacement text
        reasoning: Why this fix is appropriate
    """
    deps = ctx.deps

    # Parse action
    try:
        recovery_action = RecoveryAction(action)
    except ValueError:
        return ProposeCleanupResult(
            accepted=False,
            feedback=f"Invalid action '{action}'. Use: cleanup_edit, remove_placeholder, accept_with_caveat, escalate",
        )

    # Handle accept_with_caveat - just record the caveat
    if recovery_action == RecoveryAction.ACCEPT_WITH_CAVEAT:
        deps.caveats.append(f"{target}: {reasoning}")

        if deps.event_bus:
            deps.event_bus.emit(
                RecoveryEditAppliedEvent(
                    document_id=deps.event_bus.document_id,
                    page_num=deps.page_num,
                    action=recovery_action,
                    target=target,
                    reasoning=reasoning,
                )
            )

        return ProposeCleanupResult(accepted=True)

    # Handle escalate - just record it
    if recovery_action == RecoveryAction.ESCALATE:
        deps.caveats.append(f"ESCALATED - {target}: {reasoning}")

        if deps.event_bus:
            deps.event_bus.emit(
                RecoveryEditAppliedEvent(
                    document_id=deps.event_bus.document_id,
                    page_num=deps.page_num,
                    action=recovery_action,
                    target=target,
                    reasoning=reasoning,
                )
            )

        return ProposeCleanupResult(accepted=True)

    # For actual edits, verify the before text exists
    if before and before not in deps.current_markdown:
        return ProposeCleanupResult(
            accepted=False,
            feedback="Target text not found. Use view_page_tool() to see exact content.",
        )

    # Apply the edit
    if before:
        deps.current_markdown = deps.current_markdown.replace(before, after, 1)

    # Record the edit
    edit = RecoveryEdit(
        page=deps.page_num,
        action=recovery_action,
        target=target,
        before=before,
        after=after,
        reasoning=reasoning,
    )
    deps.edits_applied.append(edit)

    # Emit event
    if deps.event_bus:
        deps.event_bus.emit(
            RecoveryEditAppliedEvent(
                document_id=deps.event_bus.document_id,
                page_num=deps.page_num,
                action=recovery_action,
                target=target,
                reasoning=reasoning,
            )
        )

    logger.info(f"Recovery edit applied: {action} on {target}")
    return ProposeCleanupResult(accepted=True)


# =============================================================================
# Recovery Agent
# =============================================================================


_recovery_agent: Agent[RecoveryDeps, RecoveryOutput] | None = None


def _get_recovery_agent() -> Agent[RecoveryDeps, RecoveryOutput]:
    """Get or create the recovery agent."""
    global _recovery_agent

    if _recovery_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        _recovery_agent = Agent(
            model=model,
            deps_type=RecoveryDeps,
            output_type=RecoveryOutput,
            system_prompt=RECOVERY_SYSTEM_PROMPT,
        )

        # Register tools
        _recovery_agent.tool(view_page_tool)
        _recovery_agent.tool(view_markdown_tool)
        _recovery_agent.tool(view_processing_history_tool)
        _recovery_agent.tool(propose_cleanup_tool)

        logger.info("Recovery agent initialized")

    return _recovery_agent


# =============================================================================
# Page Recovery
# =============================================================================


async def attempt_page_recovery(
    page_num: int,
    page_image: Image.Image,
    current_markdown: str,
    issues: list[str],
    processing_history: list[str],
    attempt_number: int,
    event_bus: EventBus | None = None,
) -> tuple[str, RecoveryAttempt]:
    """Attempt to recover a single page.

    Args:
        page_num: Page number to recover
        page_image: Image of the page
        current_markdown: Current markdown content
        issues: Issues to address
        processing_history: History from initial processing
        attempt_number: Which attempt this is (1 or 2)
        event_bus: Optional event bus for streaming

    Returns:
        Tuple of (updated_markdown, RecoveryAttempt record)
    """
    start_time = time.time()

    # Emit start event
    if event_bus:
        event_bus.emit(
            RecoveryStartedEvent(
                document_id=event_bus.document_id,
                page_num=page_num,
                attempt_number=attempt_number,
                issues=issues,
            )
        )

    # Create dependencies
    deps = RecoveryDeps(
        page_num=page_num,
        page_image=page_image,
        current_markdown=current_markdown,
        issues=issues,
        processing_history=processing_history,
        event_bus=event_bus,
    )

    agent = _get_recovery_agent()

    # Build prompt with image
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")

    issues_text = "\n".join(f"- {issue}" for issue in issues)

    prompt = f"""Recover page {page_num} (attempt {attempt_number}/{MAX_RECOVERY_ATTEMPTS}).

## Issues to Address
{issues_text}

## Recovery Instructions
1. Use view_page_tool() to see the current markdown
2. For each issue, determine the best action:
   - If fixable with a simple edit: use cleanup_edit or remove_placeholder
   - If acceptable with documentation: use accept_with_caveat
   - If truly unfixable: use escalate
3. Be conservative - when in doubt, accept_with_caveat or escalate

This is attempt {attempt_number}. Be thorough but don't force fixes.
"""

    try:
        result = await agent.run(
            [
                "Page image for reference:",
                image_content,
                prompt,
            ],
            deps=deps,
        )

        output = result.output
        duration_ms = int((time.time() - start_time) * 1000)

        # Determine status
        if output.issues_escalated:
            status = RecoveryAttemptStatus.FAILED
        elif output.issues_accepted_with_caveats and not output.issues_addressed:
            status = RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS
        elif output.issues_addressed:
            status = RecoveryAttemptStatus.SUCCEEDED
        else:
            status = RecoveryAttemptStatus.FAILED

        attempt = RecoveryAttempt(
            page_num=page_num,
            attempt_number=attempt_number,
            status=status,
            original_issues=issues,
            edits_proposed=len(deps.edits_applied) + len(deps.caveats),
            edits_applied=len(deps.edits_applied),
            caveats=deps.caveats,
            duration_ms=duration_ms,
        )

        # Emit complete event
        if event_bus:
            event_bus.emit(
                RecoveryCompleteEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    attempt_number=attempt_number,
                    status=status,
                    edits_applied=len(deps.edits_applied),
                    caveats=deps.caveats,
                )
            )

        logger.info(f"Page {page_num} recovery attempt {attempt_number}: {status.value}")

        return deps.current_markdown, attempt

    except Exception as e:
        logger.error(f"Recovery failed for page {page_num}: {e}")

        duration_ms = int((time.time() - start_time) * 1000)

        attempt = RecoveryAttempt(
            page_num=page_num,
            attempt_number=attempt_number,
            status=RecoveryAttemptStatus.FAILED,
            original_issues=issues,
            caveats=[f"Recovery error: {str(e)}"],
            duration_ms=duration_ms,
        )

        # Emit complete event with failure
        if event_bus:
            event_bus.emit(
                RecoveryCompleteEvent(
                    document_id=event_bus.document_id,
                    page_num=page_num,
                    attempt_number=attempt_number,
                    status=RecoveryAttemptStatus.FAILED,
                    caveats=[f"Recovery error: {str(e)}"],
                )
            )

        return current_markdown, attempt


# =============================================================================
# Pass Threshold Calculation
# =============================================================================


def calculate_pass_threshold(total_pages: int) -> float:
    """Calculate dynamic pass threshold based on document size.

    Larger documents get slightly more lenient thresholds.

    Args:
        total_pages: Total number of pages in document

    Returns:
        Pass threshold as a fraction (0.0 to 1.0)
    """
    if total_pages <= 5:
        # Small documents: strict 80% threshold
        return 0.80
    elif total_pages <= 20:
        # Medium documents: 75% threshold
        return 0.75
    elif total_pages <= 50:
        # Large documents: 70% threshold
        return 0.70
    else:
        # Very large documents: 65% threshold
        return 0.65


# =============================================================================
# Final Status Determination
# =============================================================================


def determine_final_status(
    verification: VerificationReport,
    recovery_report: RecoveryReport | None,
    total_pages: int,
) -> ProcessingStatus:
    """Determine final processing status after recovery.

    Args:
        verification: Initial verification report
        recovery_report: Recovery report (if recovery was attempted)
        total_pages: Total pages in document

    Returns:
        Final ProcessingStatus
    """
    # If verification passed initially, it's a success
    if verification.passed:
        return ProcessingStatus.SUCCESS

    # If no recovery was attempted
    if recovery_report is None or not recovery_report.recovery_attempted:
        # Check if it was close to passing
        pass_rate = verification.pages_passed / total_pages if total_pages > 0 else 0
        threshold = calculate_pass_threshold(total_pages)

        if pass_rate >= threshold:
            return ProcessingStatus.PARTIAL_SUCCESS
        elif pass_rate >= 0.5:
            return ProcessingStatus.NEEDS_REVIEW
        else:
            return ProcessingStatus.FAILED

    # Recovery was attempted - evaluate results
    total_failed_pages = len(recovery_report.pages_unrecoverable)
    total_caveat_pages = len(recovery_report.pages_accepted_with_caveats)
    total_recovered = len(recovery_report.pages_recovered)

    # Calculate effective pass rate including recovered pages
    original_passed = verification.pages_passed
    effective_passed = original_passed + total_recovered + total_caveat_pages
    effective_pass_rate = effective_passed / total_pages if total_pages > 0 else 0

    threshold = calculate_pass_threshold(total_pages)

    if total_failed_pages == 0 and total_caveat_pages == 0:
        # All pages recovered completely
        return ProcessingStatus.SUCCESS
    elif effective_pass_rate >= threshold:
        if total_caveat_pages > 0:
            # Has caveats but passes threshold
            return ProcessingStatus.PARTIAL_SUCCESS
        else:
            return ProcessingStatus.SUCCESS
    elif effective_pass_rate >= 0.5:
        return ProcessingStatus.NEEDS_REVIEW
    else:
        return ProcessingStatus.FAILED
