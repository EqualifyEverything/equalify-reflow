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
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.metrics_service import record_llm_call

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
    Ledger,
    LedgerEntry,
    LLMCallRecord,
    Task,
    TaskType,
)
from .subagents import (
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    invoke_citation_subagent,
    invoke_footnote_subagent,
    invoke_list_subagent,
    invoke_page_artifact_subagent,
    invoke_typography_subagent,
    invoke_typography_subagent_batch,
)
from .subagents.types import (
    CitationResult,
    FootnoteResult,
    ListResult,
    PageArtifactResult,
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

- remove_page_artifacts(text_region): Clean up ---, split words
  -> Returns: {cleaned_text, artifacts_removed, words_rejoined, confidence, reasoning}

- correct_footnote(): Fix footnote placement and linking
  -> Returns: {corrected_markdown, footnotes_fixed, confidence, reasoning}

- fix_citation_links(): Link citations to bibliography
  -> Returns: {corrected_markdown, citations_linked, bibliography_found, confidence, reasoning}

- fix_list_semantics(list_markdown): Fix list structure
  -> Returns: {corrected_markdown, issues_fixed, confidence, reasoning}

- fix_typography(text_region): Add semantic bold/italic/code
  -> Returns: {corrected_markdown, formatting_added, confidence, reasoning}

### Edit Tool
- propose_edit(before, after, reasoning, needs_review): Submit your edit for validation
  - Returns {accepted, applied, feedback}
  - If applied=False, the edit was NOT made! Check feedback and retry with exact text.
  - Use find_text() first to get the exact text before calling propose_edit()

## Workflow

1. View the page to understand context
2. For each task:
   a. Read the relevant text region
   b. Call the appropriate subagent tool
   c. Review the subagent's recommendation
   d. Based on confidence:
      - If confidence >= 0.8: propose_edit(needs_review=False)
      - If confidence 0.5-0.8: propose_edit(needs_review=True)
      - If confidence < 0.5: skip the edit, note in your output
   e. If you disagree with subagent, use your judgment

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


async def remove_page_artifacts_tool(
    ctx: RunContext[ParagraphAgentDeps],
    text_region: str,
) -> PageArtifactResult:
    """Clean up page break artifacts and split words.

    Invokes a specialized subagent that analyzes the text and page image
    to identify and remove extraction artifacts.

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Args:
        text_region: The markdown text that may contain artifacts

    Returns:
        PageArtifactResult with cleaned text and confidence score
    """
    # Check for pre-computed result from parallel execution
    for key, result in ctx.deps.precomputed_subagent_results.items():
        if key.startswith(TaskType.PAGE_ARTIFACT_REMOVAL.value + ":"):
            if isinstance(result, PageArtifactResult):
                logger.debug(f"Using pre-computed page artifact result for {key}")
                return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_page_artifact_subagent(
        text_region=text_region,
        page_image=ctx.deps.page_image,
    )

    return result


async def correct_footnote_tool(
    ctx: RunContext[ParagraphAgentDeps],
) -> FootnoteResult:
    """Fix footnote placement and linking.

    Invokes a specialized subagent that finds footnote markers,
    locates definitions, and creates proper markdown linking.

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Returns:
        FootnoteResult with corrected markdown and confidence score
    """
    # Check for pre-computed result from parallel execution
    for key, result in ctx.deps.precomputed_subagent_results.items():
        if key.startswith(TaskType.FOOTNOTE_CORRECTION.value + ":"):
            if isinstance(result, FootnoteResult):
                logger.debug(f"Using pre-computed footnote result for {key}")
                return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_footnote_subagent(
        page_markdown=ctx.deps.current_markdown,
        page_image=ctx.deps.page_image,
    )

    return result


async def fix_citation_links_tool(
    ctx: RunContext[ParagraphAgentDeps],
) -> CitationResult:
    """Link citations to bibliography entries.

    Invokes a specialized subagent that finds citation markers
    and matches them to references. Uses full document context
    to locate the bibliography section.

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Returns:
        CitationResult with linked citations and confidence score
    """
    # Check for pre-computed result from parallel execution
    for key, result in ctx.deps.precomputed_subagent_results.items():
        if key.startswith(TaskType.CITATION_LINKING.value + ":"):
            if isinstance(result, CitationResult):
                logger.debug(f"Using pre-computed citation result for {key}")
                return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_citation_subagent(
        page_markdown=ctx.deps.current_markdown,
        page_image=ctx.deps.page_image,
        full_document=ctx.deps.full_document_markdown,
    )

    return result


async def fix_list_semantics_tool(
    ctx: RunContext[ParagraphAgentDeps],
    list_markdown: str,
) -> ListResult:
    """Fix list structure (nesting, numbering, bullets).

    Invokes a specialized subagent that compares the visual
    list layout to the markdown structure.

    Note: Results may be pre-computed in parallel before this tool is called.
    The tool checks for pre-computed results first and returns them instantly.

    Args:
        list_markdown: The list section to analyze

    Returns:
        ListResult with corrected structure and confidence score
    """
    # Check for pre-computed result from parallel execution
    for key, result in ctx.deps.precomputed_subagent_results.items():
        if key.startswith(TaskType.LIST_FIX.value + ":"):
            if isinstance(result, ListResult):
                logger.debug(f"Using pre-computed list result for {key}")
                return result

    # Fallback to direct invocation if no pre-computed result
    result = await invoke_list_subagent(
        list_markdown=list_markdown,
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

    This function groups tasks by type and invokes the appropriate subagent
    for each task type concurrently. Results are stored in a dict keyed by
    task identifier for fast lookup by the tool functions.

    Typography tasks are batched into a single LLM call when there are multiple,
    reducing token usage by 30-40%.

    Args:
        job: The Job containing tasks to process
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        Dict mapping task keys to their subagent results
    """
    if not job.tasks:
        return {}

    # Separate typography tasks from other tasks for batch processing
    typography_tasks: list[Task] = []
    other_tasks: list[Task] = []

    for task in job.tasks:
        if task.task_type == TaskType.TYPOGRAPHY_FIX:
            typography_tasks.append(task)
        else:
            other_tasks.append(task)

    results: dict[str, Any] = {}

    # Single task optimization: skip parallelization overhead
    if len(job.tasks) == 1 and not typography_tasks:
        task = job.tasks[0]
        result = await _invoke_subagent_for_task(task, deps)
        key = _get_subagent_key(task.task_type, task.target)
        return {key: result}

    # Semaphore to limit concurrent calls
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SUBAGENTS)

    async def run_with_semaphore(task: Task) -> tuple[str, Any]:
        """Run a single subagent call with semaphore limiting."""
        async with semaphore:
            result = await _invoke_subagent_for_task(task, deps)
            key = _get_subagent_key(task.task_type, task.target)
            return key, result

    async def run_typography_batch() -> dict[str, Any]:
        """Run typography tasks as a batch."""
        if not typography_tasks:
            return {}

        # Build list of (task_target, text_region) tuples
        text_regions: list[tuple[str, str]] = []
        for task in typography_tasks:
            text_region = task.context if task.context else deps.current_markdown
            text_regions.append((task.target, text_region))

        # Call batch function
        async with semaphore:
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

    # Build list of coroutines to run
    coroutines: list = []

    # Add individual non-typography tasks
    for task in other_tasks:
        coroutines.append(run_with_semaphore(task))

    # Add batch typography task if there are typography tasks
    if typography_tasks:
        logger.info(
            f"Batching {len(typography_tasks)} typography tasks into single LLM call"
        )
        coroutines.append(run_typography_batch())

    # Run all coroutines in parallel
    results_list = await asyncio.gather(*coroutines, return_exceptions=True)

    # Process results for non-typography tasks
    non_typo_results = results_list[: len(other_tasks)]
    for i, result in enumerate(non_typo_results):
        task = other_tasks[i]
        key = _get_subagent_key(task.task_type, task.target)

        if isinstance(result, Exception):
            logger.warning(
                f"Subagent for task {task.task_type.value} failed: {result}"
            )
            results[key] = _create_failed_result(task.task_type, str(result), deps)
        else:
            # Result is a tuple of (key, actual_result)
            result_tuple: tuple[str, Any] = result  # type: ignore[assignment]
            _, actual_result = result_tuple
            results[key] = actual_result

    # Process typography batch results
    if typography_tasks:
        typo_result = results_list[-1]
        if isinstance(typo_result, Exception):
            logger.warning(f"Typography batch failed: {typo_result}")
            # Create failed results for all typography tasks
            for task in typography_tasks:
                key = _get_subagent_key(task.task_type, task.target)
                results[key] = _create_failed_result(
                    task.task_type, str(typo_result), deps
                )
        else:
            # Merge batch results into main results dict
            typo_dict: dict[str, Any] = typo_result  # type: ignore[assignment]
            results.update(typo_dict)

    return results


async def _invoke_subagent_for_task(
    task: Task,
    deps: ParagraphAgentDeps,
) -> Any:
    """Invoke the appropriate subagent based on task type.

    Args:
        task: The task to process
        deps: ParagraphAgentDeps with page image and markdown

    Returns:
        The subagent result (type depends on task_type)
    """
    task_type = task.task_type

    if task_type == TaskType.PAGE_ARTIFACT_REMOVAL:
        # Use task context as text region, or fall back to markdown
        text_region = task.context if task.context else deps.current_markdown
        return await invoke_page_artifact_subagent(
            text_region=text_region,
            page_image=deps.page_image,
        )

    elif task_type == TaskType.FOOTNOTE_CORRECTION:
        return await invoke_footnote_subagent(
            page_markdown=deps.current_markdown,
            page_image=deps.page_image,
        )

    elif task_type == TaskType.CITATION_LINKING:
        return await invoke_citation_subagent(
            page_markdown=deps.current_markdown,
            page_image=deps.page_image,
            full_document=deps.full_document_markdown,
        )

    elif task_type == TaskType.LIST_FIX:
        # Use task context as list markdown, or fall back to full markdown
        list_markdown = task.context if task.context else deps.current_markdown
        return await invoke_list_subagent(
            list_markdown=list_markdown,
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
    if task_type == TaskType.PAGE_ARTIFACT_REMOVAL:
        return PageArtifactResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            cleaned_text=deps.current_markdown,
            artifacts_removed=[],
            words_rejoined=[],
        )
    elif task_type == TaskType.FOOTNOTE_CORRECTION:
        return FootnoteResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            corrected_markdown=deps.current_markdown,
            footnotes_fixed=[],
        )
    elif task_type == TaskType.CITATION_LINKING:
        return CitationResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            corrected_markdown=deps.current_markdown,
            citations_linked=[],
            bibliography_found=False,
        )
    elif task_type == TaskType.LIST_FIX:
        return ListResult(
            confidence=0.0,
            reasoning=f"Subagent error: {error_message}",
            corrected_markdown=deps.current_markdown,
            issues_fixed=[],
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

        # Register tools
        _paragraph_agent.tool(view_page_tool)
        _paragraph_agent.tool(find_text_tool)
        _paragraph_agent.tool(read_context_tool)
        _paragraph_agent.tool(remove_page_artifacts_tool)
        _paragraph_agent.tool(correct_footnote_tool)
        _paragraph_agent.tool(fix_citation_links_tool)
        _paragraph_agent.tool(fix_list_semantics_tool)
        _paragraph_agent.tool(fix_typography_tool)
        _paragraph_agent.tool(propose_edit_tool)

        logger.info("ParagraphAgent initialized")

    return _paragraph_agent


# =============================================================================
# Job Result (shared with Worker)
# =============================================================================


@dataclass
class JobResult:
    """Result of executing a paragraph job."""

    job_id: str
    success: bool
    updated_markdown: str
    ledger_entries: list[LedgerEntry]

    tasks_completed: int
    tasks_failed: int

    input_tokens: int
    output_tokens: int
    duration_ms: int

    error: str | None = None
    llm_call: LLMCallRecord | None = None


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
        cost_cents = ((input_tokens * 0.00025) + (output_tokens * 0.00125)) / 10

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
    "_invoke_subagent_for_task",
    "_get_subagent_key",
    "_create_failed_result",
    # Agent (for testing)
    "_get_paragraph_agent",
    "reset_agent",
    # Result
    "JobResult",
]
