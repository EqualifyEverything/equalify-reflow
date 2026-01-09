"""DocumentWorker - Executes DocumentJobs Targeting Line Ranges in Merged Markdown.

Unlike the page-based Worker, the DocumentWorker operates on document sections
identified by line numbers in the merged (pageless) markdown. This enables
multi-round processing where issues are fixed based on their location in the
complete document rather than per-page.

Tools available:
    - view_context: View markdown around the issue area
    - view_source_page: View original PDF page image for visual reference
    - propose_edit: Propose a fix (goes through validation)

The worker receives DocumentJobs from the CriticAgent and executes them
sequentially to maintain markdown consistency.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.metrics_service import record_llm_call

from .debug_capture import extract_raw_response, serialize_prompt
from .events import EditCommittedEvent, EventBus, JobCompletedEvent, JobStartedEvent
from .models import (
    DocumentJob,
    DocumentJobType,
    Ledger,
    LedgerEntry,
    LLMCallRecord,
    PageBoundaryMap,
    TaskType,
)
from .validation import validate_edit

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Worker Dependencies
# =============================================================================


@dataclass
class DocumentWorkerDeps:
    """Dependencies for DocumentWorker."""

    job: DocumentJob
    current_markdown: str
    page_images: dict[int, "Image.Image"]
    page_boundary_map: PageBoundaryMap
    ledger: Ledger

    # Mutable state
    pending_edits: list[dict[str, Any]] = field(default_factory=list)  # before/after/reasoning

    # Event bus for streaming
    event_bus: EventBus | None = None


# =============================================================================
# Tool Return Types
# =============================================================================


class ViewContextResult(BaseModel):
    """Result from viewing markdown around an issue."""

    content: str
    start_line: int
    end_line: int
    total_lines: int


class ViewSourcePageResult(BaseModel):
    """Result from viewing a source page image."""

    success: bool
    message: str = ""
    # Note: Image is returned via BinaryContent in actual tool


class ProposeEditResult(BaseModel):
    """Result from proposing an edit."""

    accepted: bool
    feedback: str | None = None
    applied: bool = False


# =============================================================================
# System Prompt
# =============================================================================


DOCUMENT_WORKER_SYSTEM_PROMPT = """You are a document accessibility worker specializing in fixing issues in merged markdown.

## Your Task
You are given a specific issue to fix in a document. The issue has:
- Line range (where the problem is)
- Description (what's wrong)
- Suggested fix (how to approach it)
- Source pages (for visual reference if needed)

## Available Tools

### view_context(start_line, end_line)
View the markdown around the issue. Use this to understand the full context.

### view_source_page(page_num)
View the original PDF page image. Use this when you need visual reference to:
- Check figure content for alt-text
- Verify table structure
- Understand layout

### propose_edit(before, after, reasoning)
Propose a fix. The 'before' text must exactly match existing markdown.
- If approved: edit is applied
- If rejected: you get feedback

## Workflow
1. Use view_context() to see the issue area
2. Optionally use view_source_page() for visual reference
3. Use propose_edit() to fix the issue
4. Be precise with 'before' text - it must match exactly

## Quality Standards
- Alt-text should be descriptive and contextual
- Table headers should use proper markdown syntax
- Fix only what's needed - don't over-edit
- Preserve existing formatting where possible"""


# =============================================================================
# Tool Implementations
# =============================================================================


async def view_context_tool(
    ctx: RunContext[DocumentWorkerDeps],
    start_line: int,
    end_line: int,
) -> ViewContextResult:
    """View markdown around the issue.

    Args:
        start_line: First line to read (1-indexed)
        end_line: Last line to read (inclusive)
    """
    deps = ctx.deps
    lines = deps.current_markdown.splitlines()
    total_lines = len(lines)

    # Clamp to valid range
    start = max(1, start_line) - 1  # Convert to 0-indexed
    end = min(total_lines, end_line)

    # Extract the requested lines
    content_lines = lines[start:end]
    content = "\n".join(content_lines)

    logger.debug(f"DocumentWorker viewing lines {start_line}-{end_line} of {total_lines}")

    return ViewContextResult(
        content=content,
        start_line=start_line,
        end_line=min(end_line, total_lines),
        total_lines=total_lines,
    )


async def view_source_page_tool(
    ctx: RunContext[DocumentWorkerDeps],
    page_num: int,
) -> tuple[ViewSourcePageResult, BinaryContent | None]:
    """View the original PDF page image.

    Args:
        page_num: Page number to view (1-indexed)
    """
    deps = ctx.deps

    if page_num not in deps.page_images:
        available = list(deps.page_images.keys())
        return ViewSourcePageResult(
            success=False,
            message=f"Page {page_num} not found. Available pages: {available}",
        ), None

    # Get the page image and convert to BinaryContent
    page_image = deps.page_images[page_num]
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")

    logger.debug(f"DocumentWorker viewing source page {page_num}")

    return ViewSourcePageResult(
        success=True,
        message=f"Page {page_num} image is shown above.",
    ), image_content


async def propose_edit_tool(
    ctx: RunContext[DocumentWorkerDeps],
    before: str,
    after: str,
    reasoning: str,
) -> ProposeEditResult:
    """Propose a fix to the markdown.

    The edit goes through validation. If rejected, you'll get feedback.

    Args:
        before: EXACT original text to replace - must exist verbatim in markdown
        after: New text to replace it with
        reasoning: Why this edit is needed
    """
    deps = ctx.deps

    # Check if the 'before' text exists in the markdown
    if before not in deps.current_markdown:
        # Provide helpful feedback
        preview = before[:100] + "..." if len(before) > 100 else before
        return ProposeEditResult(
            accepted=False,
            feedback=(
                f"Target text not found in markdown. Your 'before' text was: '{preview}'. "
                "Use view_context() to see the EXACT text, then copy it precisely."
            ),
        )

    # Check if replacement is different
    if before == after:
        return ProposeEditResult(
            accepted=False,
            feedback="Replacement is identical to original - no change needed.",
        )

    # Check for unreasonably large edits
    if len(after) > 10000:
        return ProposeEditResult(
            accepted=False,
            feedback="Edit is very large (>10000 chars). Break into smaller edits.",
        )

    # Validate markdown syntax in the 'after' content
    lint_issues = _check_markdown_syntax(after)
    if lint_issues:
        return ProposeEditResult(
            accepted=False,
            feedback=f"Markdown validation failed: {'; '.join(lint_issues[:3])}",
        )

    # Apply the edit
    deps.current_markdown = deps.current_markdown.replace(before, after, 1)

    # Store in pending_edits for ledger creation
    deps.pending_edits.append({
        "before": before,
        "after": after,
        "reasoning": reasoning,
        "applied": True,
    })

    logger.info(f"DocumentWorker applied edit: {reasoning[:50]}...")

    return ProposeEditResult(
        accepted=True,
        applied=True,
    )


def _check_markdown_syntax(text: str) -> list[str]:
    """Check markdown for basic formatting issues.

    Args:
        text: Markdown text to check

    Returns:
        List of issue descriptions
    """
    issues: list[str] = []
    lines = text.split("\n")

    for i, line in enumerate(lines, 1):
        # Check for heading without space after #
        if re.match(r"^#+[^#\s]", line):
            issues.append(f"Line {i}: heading missing space after #")

        # Check for unclosed brackets
        if line.count("[") != line.count("]"):
            issues.append(f"Line {i}: unclosed square brackets")

        if line.count("(") != line.count(")"):
            # Skip if it's alt-text with parentheses in content
            if not re.search(r"!\[.*\]\(", line):
                issues.append(f"Line {i}: unclosed parentheses")

        # Check for broken table syntax
        if line.startswith("|") and not line.rstrip().endswith("|"):
            issues.append(f"Line {i}: table row missing closing pipe")

    return issues


# =============================================================================
# Agent Definition
# =============================================================================


# Lazily initialized agent
_document_worker_agent: Agent[DocumentWorkerDeps, None] | None = None


def _get_document_worker_agent() -> Agent[DocumentWorkerDeps, None]:
    """Get or create the document worker agent."""
    global _document_worker_agent

    if _document_worker_agent is None:
        # Use REASONING tier (Sonnet) for quality document-level fixes
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.REASONING])

        _document_worker_agent = Agent(
            model=model,
            deps_type=DocumentWorkerDeps,
            output_type=None,
            system_prompt=DOCUMENT_WORKER_SYSTEM_PROMPT,
        )

        # Register tools
        _document_worker_agent.tool(view_context_tool)
        _document_worker_agent.tool(view_source_page_tool)
        _document_worker_agent.tool(propose_edit_tool)

        logger.info("DocumentWorker agent initialized")

    return _document_worker_agent


# Expose getter function for agent access
# Use _get_document_worker_agent() when you need the agent instance
document_worker_agent = _get_document_worker_agent


# =============================================================================
# Job Execution Functions
# =============================================================================


async def execute_document_job(
    job: DocumentJob,
    current_markdown: str,
    page_images: dict[int, "Image.Image"],
    page_boundary_map: PageBoundaryMap,
    ledger: Ledger,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
) -> tuple[str, list[LedgerEntry], LLMCallRecord | None]:
    """Execute a single DocumentJob.

    Args:
        job: The job to execute
        current_markdown: Current markdown content
        page_images: Page images for visual reference
        page_boundary_map: Line-to-page mapping
        ledger: Ledger to record edits
        event_bus: Optional event bus
        capture_debug: Whether to capture debug info

    Returns:
        Tuple of (updated_markdown, ledger_entries, llm_call_record)
    """
    start_time = time.time()

    deps = DocumentWorkerDeps(
        job=job,
        current_markdown=current_markdown,
        page_images=page_images,
        page_boundary_map=page_boundary_map,
        ledger=ledger,
        event_bus=event_bus,
    )

    # Build context-aware prompt
    markdown_lines = current_markdown.splitlines()
    total_lines = len(markdown_lines)

    # Get lines around the issue for context preview
    start_idx = max(0, job.line_start - 1)
    end_idx = min(total_lines, job.line_end)
    context_lines = markdown_lines[start_idx:end_idx]
    context_preview = "\n".join(context_lines[:20])

    # Truncate if too long
    if len(context_preview) > 2000:
        context_preview = context_preview[:2000] + "\n...[truncated]"

    initial_prompt = f"""Fix this issue in the document:

**Issue Type**: {job.job_type.value}
**Lines**: {job.line_start} - {job.line_end}
**Description**: {job.issue_description}
**Suggested Fix**: {job.suggested_fix or "Use your judgment"}
**Source Pages**: {job.source_pages}

**Context Preview**:
```
{context_preview}
```

Use view_context() to see more if needed. Use view_source_page() for visual reference.
Then propose_edit() to fix the issue."""

    # Emit start event
    if event_bus:
        event_bus.emit(JobStartedEvent(
            document_id=page_boundary_map.document_id,
            job_id=job.job_id,
            page=job.source_pages[0] if job.source_pages else 0,
            tasks=[job.issue_description],
        ))

    # Get the agent
    agent = _get_document_worker_agent()

    # Run agent
    result = await agent.run(
        initial_prompt,
        deps=deps,
    )

    duration_ms = int((time.time() - start_time) * 1000)

    # Extract usage
    usage = result.usage()
    input_tokens = usage.request_tokens or 0
    output_tokens = usage.response_tokens or 0

    # Apply accepted edits and create ledger entries
    updated_markdown = deps.current_markdown
    ledger_entries: list[LedgerEntry] = []

    for edit in deps.pending_edits:
        if edit.get("applied"):
            # Determine task type from job type
            task_type = _map_job_type_to_task_type(job.job_type)

            entry = LedgerEntry(
                job_id=job.job_id,
                page=job.source_pages[0] if job.source_pages else 0,
                action=task_type,
                target=f"lines_{job.line_start}_{job.line_end}",
                before=edit["before"],
                after=edit["after"],
                reasoning=edit["reasoning"],
                confidence=0.8,
                validated=True,
            )
            ledger_entries.append(entry)
            ledger.append(entry)

            if event_bus:
                event_bus.emit(EditCommittedEvent(
                    document_id=page_boundary_map.document_id,
                    ledger_entry=entry,
                    content_preview=edit["after"][:200],
                ))

    # Record Prometheus metrics
    record_llm_call(
        agent="document_worker",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=_calculate_cost_cents(input_tokens, output_tokens),
        duration_ms=duration_ms,
    )

    # Capture debug if requested
    llm_call: LLMCallRecord | None = None
    if capture_debug:
        llm_call = LLMCallRecord(
            agent="document_worker",
            purpose=f"job_{job.job_id[:8]}",
            page=job.source_pages[0] if job.source_pages else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=_calculate_cost_cents(input_tokens, output_tokens),
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
            prompt_text=serialize_prompt(result.all_messages()[:1]),
            response_raw=extract_raw_response(result),
        )

    # Emit complete event
    if event_bus:
        event_bus.emit(JobCompletedEvent(
            document_id=page_boundary_map.document_id,
            job_id=job.job_id,
            page=job.source_pages[0] if job.source_pages else 0,
            edits_made=len(ledger_entries),
            duration_ms=duration_ms,
            tokens_used=input_tokens + output_tokens,
        ))

    return updated_markdown, ledger_entries, llm_call


async def execute_document_jobs(
    jobs: list[DocumentJob],
    current_markdown: str,
    page_images: dict[int, "Image.Image"],
    page_boundary_map: PageBoundaryMap,
    ledger: Ledger,
    event_bus: EventBus | None = None,
    capture_debug: bool = False,
    max_concurrent: int = 1,  # Sequential by default for markdown safety
) -> tuple[str, int, list[LLMCallRecord]]:
    """Execute multiple DocumentJobs.

    Args:
        jobs: Jobs to execute
        current_markdown: Current markdown
        page_images: Page images
        page_boundary_map: Line mapping
        ledger: Ledger
        event_bus: Event bus
        capture_debug: Capture debug info
        max_concurrent: Max concurrent jobs (1 = sequential)

    Returns:
        Tuple of (final_markdown, total_edits, llm_calls)
    """
    updated_markdown = current_markdown
    total_edits = 0
    all_llm_calls: list[LLMCallRecord] = []

    # Sort jobs by priority (lower priority value = higher priority)
    sorted_jobs = sorted(jobs, key=lambda j: j.priority)

    # Execute sequentially for markdown consistency
    for job in sorted_jobs:
        try:
            updated_markdown, entries, llm_call = await execute_document_job(
                job=job,
                current_markdown=updated_markdown,
                page_images=page_images,
                page_boundary_map=page_boundary_map,
                ledger=ledger,
                event_bus=event_bus,
                capture_debug=capture_debug,
            )
            total_edits += len(entries)
            if llm_call:
                all_llm_calls.append(llm_call)
            job.status = "completed"
        except Exception as e:
            logger.error(f"Job {job.job_id} failed: {e}")
            job.status = "failed"

    return updated_markdown, total_edits, all_llm_calls


# =============================================================================
# Helper Functions
# =============================================================================


def _map_job_type_to_task_type(job_type: DocumentJobType) -> TaskType:
    """Map DocumentJobType to TaskType for ledger entries.

    Args:
        job_type: The document job type

    Returns:
        Corresponding TaskType
    """
    mapping = {
        DocumentJobType.STRUCTURE_FIX: TaskType.HEADING_FIX,
        DocumentJobType.ACCESSIBILITY_FIX: TaskType.ALT_TEXT,
        DocumentJobType.CONTENT_FIX: TaskType.OCR_FIX,
        DocumentJobType.FORMATTING_FIX: TaskType.FORMAT_FIX,
    }
    return mapping.get(job_type, TaskType.FORMAT_FIX)


def _calculate_cost_cents(input_tokens: int, output_tokens: int) -> float:
    """Calculate estimated cost in cents for Sonnet model.

    Sonnet 4.5 pricing via AWS Bedrock:
    - Input: $3.00 per 1M tokens
    - Output: $15.00 per 1M tokens

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens

    Returns:
        Estimated cost in cents
    """
    input_cost = (input_tokens / 1_000_000) * 3.00 * 100  # Convert to cents
    output_cost = (output_tokens / 1_000_000) * 15.00 * 100
    return input_cost + output_cost


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    "DocumentWorkerDeps",
    "execute_document_job",
    "execute_document_jobs",
    "document_worker_agent",
    "ViewContextResult",
    "ViewSourcePageResult",
    "ProposeEditResult",
]
