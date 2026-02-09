"""Paragraph Agent - Orchestrates Subagent Tools for Text Flow Issues.

The ParagraphAgent handles per-page paragraph-related tasks by delegating
to specialized subagent tools. Each subagent returns recommendations with
confidence scores. The parent agent reviews these and decides whether to
apply edits based on confidence thresholds.

## Subagent Delegation Pattern

1. Agent receives page with paragraph tasks
2. Subagents are invoked IN PARALLEL before the main agent runs
3. Pre-computed results are stored in deps.precomputed_subagent_results
4. When agent calls subagent tools, they return pre-computed results instantly
5. Agent reviews and decides based on thresholds:
   - >= 0.8: Auto-apply (needs_review=False)
   - >= 0.5: Apply with review flag (needs_review=True)
   - < 0.5: Skip, log for manual review

All edits go through the validation gate before being committed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, ToolDefinition
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.metrics_service import record_llm_call
from src.shared.llm_cost import calculate_estimated_cost

from .debug_capture import extract_raw_response, serialize_prompt
from .events import (
    EditCommittedEvent,
    EditProposedEvent,
    EditValidatedEvent,
    EventBus,
    JobCompletedEvent,
    JobFailedEvent,
    JobStartedEvent,
)
from .models import (
    EditProposal,
    Job,
    JobResult,
    Ledger,
    LedgerEntry,
    LLMCallRecord,
    Task,
    TaskType,
)
from .subagents import (
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    invoke_text_structure_subagent,
    invoke_typography_subagent,
    invoke_typography_subagent_batch,
)
from .subagents.types import (
    TextStructureResult,
    TypographyResult,
)
from .validation import auto_fix_minor_issues, validate_edit

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# System Prompt
# =============================================================================

PARAGRAPH_AGENT_SYSTEM_PROMPT = """You are a document text flow specialist.

Your job is to fix text structure issues: page breaks, footnotes, citations, lists, and typography.

## Your Domain

You handle:
- Page break artifacts (---, split words like de-precate)
- Footnote placement and linking
- Citation references to bibliography
- List structure (nesting, numbering, bullets)
- Typography semantics (bold/italic that conveys meaning)

You do NOT handle:
- Images/figures (handled by Worker)
- Tables (handled by Worker)
- Heading levels (handled in planning)
- Cross-page paragraph merges (handled in separate pass)

## Available Tools

### View Tools
- view_page(): See page image and current markdown

### Analysis Tools
- find_text(pattern): Find exact text in markdown
- read_context(start_line, end_line): Read specific markdown lines

### Subagent Tools

These call specialized LLM subagents that return recommendations with confidence scores.
YOU decide whether to apply their recommendations.

- fix_text_structure(): Fix page artifacts, footnotes, citations, and lists (all in one call)
  -> Returns: {corrections: [{task_type, before, after, confidence, reasoning}], ...}
  -> Each correction specifies what to replace and with what

- fix_typography(text_region): Add semantic bold/italic/code
  -> Returns: {corrected_markdown, formatting_added, confidence, reasoning}

### Edit Tool
- propose_edit(before, after, reasoning, needs_review): Submit your edit for validation
  - Returns {accepted, applied, feedback}
  - If applied=False, the edit was NOT made! Check feedback and retry with exact text.
  - Use find_text() first to get the exact text before calling propose_edit()

## Workflow

1. View the page to understand context
2. Call fix_text_structure() to get all text corrections at once
3. Review each correction from the result:
   - Based on confidence:
     - If confidence >= 0.8: propose_edit(needs_review=False)
     - If confidence 0.5-0.8: propose_edit(needs_review=True)
     - If confidence < 0.5: skip the edit, note in your output
   - If you disagree with subagent, use your judgment
4. Call fix_typography() if there are typography tasks
5. Apply typography corrections with the same confidence logic

## Important Rules

1. ALWAYS view the page image before making decisions
2. Subagent recommendations are SUGGESTIONS - you have final judgment
3. When in doubt about author intent, preserve original
4. Page artifacts (---) are almost always extraction errors
5. Low confidence = flag for human review, don't skip entirely

## Output

List which tasks you completed:
- Applied (auto): edits applied with high confidence
- Applied (review): edits applied but flagged for review
- Skipped: edits skipped due to very low confidence
- Failed: tasks that encountered errors
"""


# =============================================================================
# Dependencies
# =============================================================================


@dataclass
class ParagraphAgentDeps:
    """Dependencies for ParagraphAgent tools."""

    job: Job
    page_image: Image.Image
    current_markdown: str

    # For edits
    pending_edits: list[EditProposal] = field(default_factory=list)
    validated_edits: list[LedgerEntry] = field(default_factory=list)

    # For citations (need full document)
    full_document_markdown: str | None = None

    # Context
    dictionary: list[str] = field(default_factory=list)
    event_bus: EventBus | None = None

    # Pre-computed subagent results (populated by _run_subagents_parallel)
    # Keys are task identifiers, values are subagent result objects
    precomputed_subagent_results: dict[str, Any] = field(default_factory=dict)

    # Pipeline dossier for context injection
    dossier: Any | None = None


# =============================================================================
# Tool Return Types
# =============================================================================


class ViewResult(BaseModel):
    """Result from viewing the page."""

    success: bool
    description: str = ""
    markdown_content: str | None = None
    error: str | None = None


class ReadResult(BaseModel):
    """Result from reading markdown lines."""

    content: str
    start_line: int
    end_line: int
    total_lines: int


class FindTextResult(BaseModel):
    """Result from finding text in markdown."""

    found: bool
    exact_match: str | None = None
    context_before: str = ""
    context_after: str = ""
    line_number: int | None = None
    error: str | None = None


class ProposeEditResult(BaseModel):
    """Result from proposing an edit."""

    accepted: bool
    feedback: str | None = None
    applied: bool = False


# =============================================================================
# Output Model
# =============================================================================


class ParagraphAgentOutput(BaseModel):
    """Output from ParagraphAgent."""

    tasks_applied_auto: list[str] = Field(default_factory=list)
    tasks_applied_review: list[str] = Field(default_factory=list)
    tasks_skipped: list[str] = Field(default_factory=list)
    tasks_failed: list[str] = Field(default_factory=list)
    summary: str = Field(default="")


# =============================================================================
# View Tool
# =============================================================================


async def view_page_tool(ctx: RunContext[ParagraphAgentDeps]) -> ViewResult:
    """View page image and current markdown.

    Call this first to understand the page context before making decisions.

    Returns:
        ViewResult with page description and current markdown content
    """
    deps = ctx.deps
    return ViewResult(
        success=True,
        description=f"Page {deps.job.page} image is shown above.",
        markdown_content=deps.current_markdown,
    )


# =============================================================================
# Analysis Tools
# =============================================================================


async def find_text_tool(
    ctx: RunContext[ParagraphAgentDeps],
    pattern: str,
) -> FindTextResult:
    """Find exact text in the markdown.

    Use this to locate specific text before proposing edits.
    The exact_match field contains the text you should use in propose_edit().

    Args:
        pattern: Text or regex pattern to search for

    Returns:
        FindTextResult with match details and surrounding context
    """
    deps = ctx.deps
    markdown = deps.current_markdown
    lines = markdown.split("\n")

    # Try exact match first
    if pattern in markdown:
        # Find line number
        for i, line in enumerate(lines, 1):
            if pattern in line:
                # Get context
                context_before = lines[i - 2] if i > 1 else ""
                context_after = lines[i] if i < len(lines) else ""

                return FindTextResult(
                    found=True,
                    exact_match=pattern,
                    context_before=context_before,
                    context_after=context_after,
                    line_number=i,
                )

    # Try regex
    try:
        match = re.search(pattern, markdown, re.MULTILINE)
        if match:
            matched_text = match.group(0)
            # Find line number
            pos = match.start()
            line_num = markdown[:pos].count("\n") + 1

            context_before = lines[line_num - 2] if line_num > 1 else ""
            context_after = lines[line_num] if line_num < len(lines) else ""

            return FindTextResult(
                found=True,
                exact_match=matched_text,
                context_before=context_before,
                context_after=context_after,
                line_number=line_num,
            )
    except re.error as e:
        return FindTextResult(
            found=False,
            error=f"Invalid regex: {e}",
        )

    return FindTextResult(
        found=False,
        error=f"Pattern not found: {pattern[:50]}...",
    )


async def read_context_tool(
    ctx: RunContext[ParagraphAgentDeps],
    start_line: int,
    end_line: int,
) -> ReadResult:
    """Read specific lines of markdown.

    Use this to get context around a specific region.

    Args:
        start_line: First line to read (1-indexed)
        end_line: Last line to read (1-indexed, inclusive)

    Returns:
        ReadResult with the requested content
    """
    deps = ctx.deps
    lines = deps.current_markdown.split("\n")
    total_lines = len(lines)

    # Clamp to valid range
    start = max(1, min(start_line, total_lines))
    end = max(start, min(end_line, total_lines))

    # Extract lines (convert to 0-indexed)
    selected = lines[start - 1 : end]
    content = "\n".join(selected)

    return ReadResult(
        content=content,
        start_line=start,
        end_line=end,
        total_lines=total_lines,
    )


# =============================================================================
# Subagent Wrapper Tools
# =============================================================================


# Task types that are handled by the consolidated text structure subagent
TEXT_STRUCTURE_TASK_TYPES = {
    TaskType.PAGE_ARTIFACT_REMOVAL,
    TaskType.FOOTNOTE_CORRECTION,
    TaskType.CITATION_LINKING,
    TaskType.LIST_FIX,
}


async def fix_text_structure_tool(
    ctx: RunContext[ParagraphAgentDeps],
) -> TextStructureResult:
    """Fix page artifacts, footnotes, citations, and lists in one call.

    This consolidated tool handles all text structure issues:
    - Page artifacts (---, split words, orphaned page numbers)
    - Footnotes (markers, definitions, linking)
    - Citations ([1], (Smith, 2023))
    - Lists (indentation, numbering, bullets)

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Returns:
        TextStructureResult with list of corrections and confidence scores.
        Each correction specifies: task_type, before, after, confidence, reasoning
    """
    # Check for pre-computed result from parallel execution
    precomputed_key = "text_structure:page"
    if precomputed_key in ctx.deps.precomputed_subagent_results:
        result = ctx.deps.precomputed_subagent_results[precomputed_key]
        if isinstance(result, TextStructureResult):
            logger.debug("Using pre-computed text structure result")
            return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_text_structure_subagent(
        page_markdown=ctx.deps.current_markdown,
        page_image=ctx.deps.page_image,
    )

    return result


async def fix_typography_tool(
    ctx: RunContext[ParagraphAgentDeps],
    text_region: str,
) -> TypographyResult:
    """Add semantic typography markup (bold, italic, code).

    Invokes a specialized subagent that compares visual
    formatting to markdown and identifies semantic formatting.

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Args:
        text_region: The text to analyze for formatting

    Returns:
        TypographyResult with formatted text and confidence score
    """
    # Check for pre-computed result from parallel execution
    for key, result in ctx.deps.precomputed_subagent_results.items():
        if key.startswith(TaskType.TYPOGRAPHY_FIX.value + ":"):
            if isinstance(result, TypographyResult):
                logger.debug(f"Using pre-computed typography result for {key}")
                return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_typography_subagent(
        text_region=text_region,
        page_image=ctx.deps.page_image,
    )

    return result


# =============================================================================
# Parallel Subagent Execution
# =============================================================================

# Maximum concurrent subagent calls (for rate limiting)
MAX_CONCURRENT_SUBAGENTS = 5


def _get_subagent_key(task_type: TaskType, target: str) -> str:
    """Generate a unique key for storing pre-computed subagent results.

    Args:
        task_type: The TaskType of the task
        target: The task target identifier

    Returns:
        A unique string key for the result cache
    """
    return f"{task_type.value}:{target}"


async def _run_subagents_parallel(
    job: Job,
    deps: ParagraphAgentDeps,
) -> dict[str, Any]:
    """Run subagent calls in parallel before the main agent reasoning loop.

    This function consolidates text structure tasks (page artifacts, footnotes,
    citations, lists) into a SINGLE LLM call via the TextStructure subagent.
    Typography tasks are handled separately and batched when multiple exist.

    This consolidation reduces LLM calls from 6/page to 2-3/page.

    Args:
        job: The Job containing tasks to process
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        Dict mapping task keys to their subagent results
    """
    if not job.tasks:
        return {}

    # Separate tasks into text structure vs typography
    text_structure_tasks: list[Task] = []
    typography_tasks: list[Task] = []

    for task in job.tasks:
        if task.task_type == TaskType.TYPOGRAPHY_FIX:
            typography_tasks.append(task)
        elif task.task_type in TEXT_STRUCTURE_TASK_TYPES:
            text_structure_tasks.append(task)

    results: dict[str, Any] = {}
    coroutines: list = []

    # Add text structure subagent call if there are any text structure tasks
    if text_structure_tasks:
        task_types = [t.task_type.value for t in text_structure_tasks]
        logger.info(
            f"Consolidating {len(text_structure_tasks)} text structure tasks "
            f"({', '.join(task_types)}) into single LLM call"
        )
        coroutines.append(_run_text_structure_subagent(deps))

    # Add typography batch call if there are typography tasks
    if typography_tasks:
        logger.info(
            f"Batching {len(typography_tasks)} typography tasks into single LLM call"
        )
        coroutines.append(_run_typography_batch(typography_tasks, deps))

    # If no tasks, return empty
    if not coroutines:
        return {}

    # Run all subagent calls in parallel (at most 2 calls: text_structure + typography)
    results_list = await asyncio.gather(*coroutines, return_exceptions=True)

    # Process text structure results
    result_idx = 0
    if text_structure_tasks:
        text_result = results_list[result_idx]
        result_idx += 1

        if isinstance(text_result, Exception):
            logger.warning(f"Text structure subagent failed: {text_result}")
            # Create a failed result
            results["text_structure:page"] = TextStructureResult(
                confidence=0.0,
                reasoning=f"Subagent error: {text_result}",
                corrections=[],
            )
        else:
            results["text_structure:page"] = text_result

    # Process typography results
    if typography_tasks:
        typo_result = results_list[result_idx]

        if isinstance(typo_result, Exception):
            logger.warning(f"Typography batch failed: {typo_result}")
            # Create failed results for all typography tasks
            for task in typography_tasks:
                key = _get_subagent_key(task.task_type, task.target)
                results[key] = TypographyResult(
                    confidence=0.0,
                    reasoning=f"Subagent error: {typo_result}",
                    corrected_markdown=deps.current_markdown,
                    formatting_added=[],
                )
        elif isinstance(typo_result, dict):
            # Merge batch results into main results dict
            results.update(typo_result)

    return results


async def _run_text_structure_subagent(deps: ParagraphAgentDeps) -> TextStructureResult:
    """Run the consolidated text structure subagent.

    Args:
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        TextStructureResult with all corrections
    """
    return await invoke_text_structure_subagent(
        page_markdown=deps.current_markdown,
        page_image=deps.page_image,
    )


async def _run_typography_batch(
    typography_tasks: list[Task],
    deps: ParagraphAgentDeps,
) -> dict[str, Any]:
    """Run typography tasks as a batch.

    Args:
        typography_tasks: List of typography tasks
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        Dict mapping task keys to TypographyResult
    """
    if not typography_tasks:
        return {}

    # Build list of (task_target, text_region) tuples
    text_regions: list[tuple[str, str]] = []
    for task in typography_tasks:
        text_region = task.context if task.context else deps.current_markdown
        text_regions.append((task.target, text_region))

    # Call batch function
    batch_results = await invoke_typography_subagent_batch(
        text_regions=text_regions,
        page_image=deps.page_image,
    )

    # Map results back to task keys
    batch_dict: dict[str, Any] = {}
    for task_target, result in batch_results:
        # Find the corresponding task to get the proper key
        for task in typography_tasks:
            if task.target == task_target:
                key = _get_subagent_key(task.task_type, task.target)
                batch_dict[key] = result
                break

    return batch_dict


async def _invoke_subagent_for_task(
    task: Task,
    deps: ParagraphAgentDeps,
) -> Any:
    """Invoke the appropriate subagent based on task type.

    Text structure tasks (page artifacts, footnotes, citations, lists) are
    handled by the consolidated text structure subagent.
    Typography is handled separately due to higher resolution needs.

    Args:
        task: The task to process
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        The subagent result (type depends on task_type)
    """
    task_type = task.task_type

    # Text structure tasks use consolidated subagent
    if task_type in TEXT_STRUCTURE_TASK_TYPES:
        return await invoke_text_structure_subagent(
            page_markdown=deps.current_markdown,
            page_image=deps.page_image,
        )

    elif task_type == TaskType.TYPOGRAPHY_FIX:
        # Use task context as text region, or fall back to markdown
        text_region = task.context if task.context else deps.current_markdown
        return await invoke_typography_subagent(
            text_region=text_region,
            page_image=deps.page_image,
        )

    else:
        # Unsupported task type for parallel execution
        logger.warning(f"Unsupported task type for parallel execution: {task_type}")
        return None


def _create_failed_result(
    task_type: TaskType,
    error_message: str,
    deps: ParagraphAgentDeps,
) -> Any:
    """Create a failed result for a subagent that raised an exception.

    Args:
        task_type: The TaskType that failed
        error_message: The error message
        deps: ParagraphAgentDeps for fallback content

    Returns:
        An appropriate result object with confidence 0.0
    """
    # Text structure tasks use consolidated result type
    if task_type in TEXT_STRUCTURE_TASK_TYPES:
        return TextStructureResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            corrections=[],
            artifacts_removed=0,
            footnotes_fixed=0,
            citations_linked=0,
            lists_fixed=0,
        )
    elif task_type == TaskType.TYPOGRAPHY_FIX:
        return TypographyResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            corrected_markdown=deps.current_markdown,
            formatting_added=[],
        )
    else:
        # Generic fallback
        return None


# =============================================================================
# Propose Edit Tool
# =============================================================================


async def propose_edit_tool(
    ctx: RunContext[ParagraphAgentDeps],
    before: str,
    after: str,
    reasoning: str,
    needs_review: bool = False,
    task_type: str = "format_fix",
) -> ProposeEditResult:
    """Propose an edit to the markdown.

    The edit goes through validation. If approved, it's applied and
    recorded in the ledger.

    Args:
        before: Exact text to replace (must exist in markdown)
        after: New text to replace it with
        reasoning: Why this edit is needed
        needs_review: If True, flags edit for human review
        task_type: Type of edit (page_artifact_removal, footnote_correction, etc.)

    Returns:
        ProposeEditResult with acceptance status
    """
    deps = ctx.deps

    # Parse task type
    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        task_type_enum = TaskType.FORMAT_FIX

    proposal = EditProposal(
        target=f"paragraph:{deps.job.page}",
        task_type=task_type_enum,
        before=before,
        after=after,
        reasoning=reasoning,
    )

    # Emit proposed event
    if deps.event_bus:
        deps.event_bus.emit(
            EditProposedEvent(
                document_id=deps.event_bus.document_id,
                job_id=deps.job.job_id,
                target=proposal.target,
                task_type=task_type_enum,
                preview=after[:200],
            )
        )

    # Validate
    result = validate_edit(
        proposal=proposal,
        dictionary=deps.dictionary,
        current_markdown=deps.current_markdown,
    )

    # Emit validation result
    if deps.event_bus:
        deps.event_bus.emit(
            EditValidatedEvent(
                document_id=deps.event_bus.document_id,
                job_id=deps.job.job_id,
                target=proposal.target,
                approved=result.approved,
                feedback=result.feedback,
            )
        )

    if not result.approved:
        logger.warning(f"Edit rejected for {proposal.target}: {result.feedback}")
        return ProposeEditResult(
            accepted=False,
            feedback=result.feedback,
        )

    # Auto-fix minor issues
    fixed_after = auto_fix_minor_issues(after)

    # Apply the edit
    if before in deps.current_markdown:
        deps.current_markdown = deps.current_markdown.replace(before, fixed_after, 1)

        # Determine confidence based on needs_review flag
        confidence = 0.6 if needs_review else 0.9

        # Create ledger entry
        entry = LedgerEntry(
            job_id=deps.job.job_id,
            page=deps.job.page,
            action=task_type_enum,
            target=proposal.target,
            before=before,
            after=fixed_after,
            reasoning=reasoning,
            confidence=confidence,
            validated=True,
            needs_review=needs_review,
        )
        deps.validated_edits.append(entry)

        # Emit commit event
        if deps.event_bus:
            deps.event_bus.emit(
                EditCommittedEvent(
                    document_id=deps.event_bus.document_id,
                    ledger_entry=entry,
                    content_preview=fixed_after[:200],
                )
            )

        logger.info(f"Edit applied for {proposal.target} (needs_review={needs_review})")
        return ProposeEditResult(accepted=True, applied=True)

    # Try to find similar text to help the agent retry

    # Get lines from current markdown
    markdown_lines = deps.current_markdown.split("\n")
    before_lines = before.split("\n")
    first_before_line = before_lines[0].strip() if before_lines else before[:50]

    # Search for similar lines
    similar_lines = []
    for i, line in enumerate(markdown_lines):
        if first_before_line[:20] in line or (
            len(first_before_line) > 10 and first_before_line[:10] in line
        ):
            context_start = max(0, i - 1)
            context_end = min(len(markdown_lines), i + 2)
            similar_lines.append(
                f"Lines {context_start + 1}-{context_end}: "
                + "\n".join(markdown_lines[context_start:context_end])
            )

    if similar_lines:
        feedback = (
            f"Edit NOT applied - 'before' text not found exactly. "
            f"Similar text found:\n{similar_lines[0][:300]}\n\n"
            f"Please retry with the exact text from find_text()."
        )
    else:
        # Show a snippet of the markdown around where we might expect it
        feedback = (
            f"Edit NOT applied - 'before' text not found in markdown. "
            f"First 500 chars of markdown:\n{deps.current_markdown[:500]}\n\n"
            f"Please use find_text() to locate the exact text before calling propose_edit()."
        )

    return ProposeEditResult(
        accepted=True,
        feedback=feedback,
        applied=False,
    )


# =============================================================================
# Agent Singleton
# =============================================================================

_paragraph_agent: Agent[ParagraphAgentDeps, ParagraphAgentOutput] | None = None


async def _prepare_typography_tool(
    ctx: RunContext[ParagraphAgentDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Hide typography tool when no typography tasks are present."""
    has_typography = any(t.task_type == TaskType.TYPOGRAPHY_FIX for t in ctx.deps.job.tasks)
    return tool_def if has_typography else None


async def _prepare_text_structure_tool(
    ctx: RunContext[ParagraphAgentDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Hide text structure tool when no text structure tasks are present."""
    from .models import TEXT_ONLY_TASK_TYPES

    has_text_tasks = any(t.task_type in TEXT_ONLY_TASK_TYPES for t in ctx.deps.job.tasks)
    return tool_def if has_text_tasks else None


def _get_paragraph_agent() -> Agent[ParagraphAgentDeps, ParagraphAgentOutput]:
    """Get or create the ParagraphAgent."""
    global _paragraph_agent

    if _paragraph_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        _paragraph_agent = Agent(
            model=model,
            deps_type=ParagraphAgentDeps,
            output_type=ParagraphAgentOutput,
            system_prompt=PARAGRAPH_AGENT_SYSTEM_PROMPT,
        )

        # Register tools — subagent tools use prepare for conditional registration
        _paragraph_agent.tool(view_page_tool)
        _paragraph_agent.tool(find_text_tool)
        _paragraph_agent.tool(read_context_tool)
        _paragraph_agent.tool(fix_text_structure_tool, prepare=_prepare_text_structure_tool)
        _paragraph_agent.tool(fix_typography_tool, prepare=_prepare_typography_tool)
        _paragraph_agent.tool(propose_edit_tool)

        # Dynamic instructions from dossier
        @_paragraph_agent.instructions
        def _inject_dossier_context(ctx: RunContext[ParagraphAgentDeps]) -> str:
            from .shared_prompts import (
                format_document_identity,
                format_page_summary,
                format_section_context,
            )

            d = ctx.deps.dossier
            if d is None:
                return ""
            parts = [
                format_document_identity(d),
                format_section_context(d, ctx.deps.job.page),
                format_page_summary(d, ctx.deps.job.page),
            ]
            return "\n".join(p for p in parts if p)

        logger.info("ParagraphAgent initialized")

    return _paragraph_agent


# =============================================================================
# Job Execution
# =============================================================================


async def execute_with_paragraph_agent(
    job: Job,
    page_image: Image.Image,
    current_markdown: str,
    full_document_markdown: str,
    ledger: Ledger,
    event_bus: EventBus | None = None,
    dictionary: list[str] | None = None,
    capture_debug: bool = False,
    dossier: Any | None = None,
) -> JobResult:
    """Execute a paragraph job using ParagraphAgent.

    Args:
        job: The paragraph job to execute
        page_image: Image of the page
        current_markdown: Current markdown for this page
        full_document_markdown: Full document (for citations)
        ledger: Ledger to append entries
        event_bus: Optional event bus for streaming
        dictionary: Optional document-specific dictionary
        capture_debug: If True, capture full prompt/response for debug bundle

    Returns:
        JobResult with updated markdown and ledger entries
    """
    start_time = time.time()

    # Emit job started event
    if event_bus:
        event_bus.emit(
            JobStartedEvent(
                document_id=event_bus.document_id,
                job_id=job.job_id,
                page=job.page,
                tasks=[t.task_type.value for t in job.tasks],
            )
        )

    # Create dependencies
    deps = ParagraphAgentDeps(
        job=job,
        page_image=page_image,
        current_markdown=current_markdown,
        full_document_markdown=full_document_markdown,
        dictionary=dictionary or [],
        event_bus=event_bus,
        dossier=dossier,
    )

    # Pre-compute subagent results in parallel before running the main agent
    # This allows all subagent LLM calls to run concurrently instead of sequentially
    logger.info(f"Running {len(job.tasks)} subagent(s) in parallel for page {job.page}")
    deps.precomputed_subagent_results = await _run_subagents_parallel(job, deps)
    logger.info(
        f"Pre-computed {len(deps.precomputed_subagent_results)} subagent result(s)"
    )

    # Build task prompt
    task_descriptions = "\n".join(
        f"- {t.task_type.value}: {t.context}" for t in job.tasks
    )

    prompt = f"""Process these paragraph tasks for page {job.page}:

{task_descriptions}

View the page, call the appropriate subagent tools, and apply edits based on confidence.
Remember:
- confidence >= 0.8: propose_edit(needs_review=False)
- confidence 0.5-0.8: propose_edit(needs_review=True)
- confidence < 0.5: skip and note in output
"""

    # Prepare message with page image
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")

    agent = _get_paragraph_agent()

    try:
        result = await agent.run(
            [f"Page {job.page} image:", image_content, prompt],
            deps=deps,
        )

        output = result.output
        usage = result.usage()

        # Add ledger entries
        for entry in deps.validated_edits:
            ledger.append(entry)

        duration_ms = int((time.time() - start_time) * 1000)

        # Calculate tasks completed
        tasks_completed = len(output.tasks_applied_auto) + len(
            output.tasks_applied_review
        )
        tasks_failed = len(output.tasks_failed)

        # Create LLM call record for tracking
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        cost_cents = calculate_estimated_cost(input_tokens, output_tokens)

        # Capture debug data if requested
        prompt_text = None
        response_raw = None
        model_id = None
        if capture_debug:
            messages = [f"Page {job.page} image:", image_content, prompt]
            prompt_text = serialize_prompt(messages)
            response_raw = extract_raw_response(result)
            model_id = MODEL_TIER_MAP.get(ModelTier.EFFICIENT, "unknown")

        llm_call = LLMCallRecord(
            agent="paragraph_agent",
            purpose=f"page_{job.page}_paragraph_fixes",
            page=job.page,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
            prompt_text=prompt_text,
            response_raw=response_raw,
            model_id=model_id,
        )

        # Emit Prometheus metrics for this LLM call
        record_llm_call(
            agent="paragraph",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            duration_ms=duration_ms,
        )

        # Emit completion event
        if event_bus:
            event_bus.emit(
                JobCompletedEvent(
                    document_id=event_bus.document_id,
                    job_id=job.job_id,
                    page=job.page,
                    edits_made=len(deps.validated_edits),
                    duration_ms=duration_ms,
                )
            )

        # Extract subagent findings for dossier
        subagent_findings = _extract_subagent_findings(
            page=job.page,
            precomputed=deps.precomputed_subagent_results,
            applied_edits=deps.validated_edits,
        )

        return JobResult(
            job_id=job.job_id,
            success=tasks_failed == 0,
            updated_markdown=deps.current_markdown,
            ledger_entries=deps.validated_edits,
            tasks_completed=tasks_completed,
            tasks_failed=tasks_failed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            llm_call=llm_call,
            subagent_findings=subagent_findings,
        )

    except Exception as e:
        logger.error(f"ParagraphAgent failed: {e}")
        duration_ms = int((time.time() - start_time) * 1000)

        # Emit failure event
        if event_bus:
            event_bus.emit(
                JobFailedEvent(
                    document_id=event_bus.document_id,
                    job_id=job.job_id,
                    page=job.page,
                    error=str(e),
                )
            )

        return JobResult(
            job_id=job.job_id,
            success=False,
            updated_markdown=current_markdown,
            ledger_entries=[],
            tasks_completed=0,
            tasks_failed=len(job.tasks),
            input_tokens=0,
            output_tokens=0,
            duration_ms=duration_ms,
            error=str(e),
        )


# =============================================================================
# Subagent Findings Extraction
# =============================================================================


def _extract_subagent_findings(
    page: int,
    precomputed: dict[str, Any],
    applied_edits: list,
) -> list[dict]:
    """Extract subagent findings into dossier-compatible dicts.

    Converts pre-computed subagent results and applied edits into
    a list of finding dicts for the dossier.

    Args:
        page: Page number
        precomputed: Pre-computed subagent results keyed by task identifier
        applied_edits: List of validated LedgerEntry objects

    Returns:
        List of finding dicts for SubagentFinding construction
    """
    findings: list[dict] = []
    applied_targets = {e.target for e in applied_edits}

    for key, result in precomputed.items():
        # Determine task type from key pattern
        task_type = key.split(":")[0] if ":" in key else key

        # Try to extract description and confidence from result
        confidence = 0.8
        if hasattr(result, "corrections"):
            # TextStructureResult — corrections have task_type, before, after, confidence, reasoning
            for correction in result.corrections:
                # Match against applied edits by before text
                before_text = getattr(correction, "before", "")
                is_applied = before_text in applied_targets if before_text else len(applied_edits) > 0
                findings.append({
                    "page": page,
                    "task_type": getattr(correction, "task_type", task_type),
                    "description": getattr(correction, "reasoning", str(correction)),
                    "confidence": getattr(correction, "confidence", confidence),
                    "applied": is_applied,
                })
        elif hasattr(result, "fixes"):
            # TypographyResult
            for fix in result.fixes:
                findings.append({
                    "page": page,
                    "task_type": "typography",
                    "description": getattr(fix, "description", str(fix)),
                    "confidence": getattr(fix, "confidence", confidence),
                    "applied": True,  # Typography fixes are always applied
                })
        else:
            # Generic result
            findings.append({
                "page": page,
                "task_type": task_type,
                "description": str(result)[:200],
                "confidence": confidence,
                "applied": len(applied_edits) > 0,
            })

    return findings


# =============================================================================
# Agent Reset (for testing)
# =============================================================================


def reset_agent() -> None:
    """Reset the ParagraphAgent singleton.

    This is used by tests to ensure agent isolation between test cases.
    """
    global _paragraph_agent
    _paragraph_agent = None


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Constants
    "PARAGRAPH_AGENT_SYSTEM_PROMPT",
    "CONFIDENCE_AUTO_APPLY",
    "CONFIDENCE_APPLY_WITH_REVIEW",
    "MAX_CONCURRENT_SUBAGENTS",
    "TEXT_STRUCTURE_TASK_TYPES",
    # Dependencies
    "ParagraphAgentDeps",
    # Output
    "ParagraphAgentOutput",
    # Tool results
    "ViewResult",
    "ReadResult",
    "FindTextResult",
    "ProposeEditResult",
    # Main functions
    "execute_with_paragraph_agent",
    # Parallel execution (for testing)
    "_run_subagents_parallel",
    "_run_text_structure_subagent",
    "_run_typography_batch",
    "_get_subagent_key",
    # Agent (for testing)
    "_get_paragraph_agent",
    "reset_agent",
    # Result
    "JobResult",
]
