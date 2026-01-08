"""Worker Agent - Executes Individual Jobs with Pre-emptive Context Loading.

The Worker Agent receives a scoped Job and executes its tasks.
All edits go through a validation gate before being committed.

## Pre-emptive Context Loading (Optimization)

This module implements an optimized workflow where:
1. Element images (figures, tables) are pre-cropped before the agent runs
2. Placeholder text is pre-located in the markdown
3. All context is provided upfront in the initial prompt

This reduces tool calls from 5-10 per job to 1-2, cutting latency and token costs
by approximately 50%.

Tools available (for fallback/edge cases):
    - view_page: See the full page image AND get current markdown text
    - view_figure: See a cropped figure (fallback if pre-crop fails)
    - view_table: See a cropped table (fallback if pre-crop fails)
    - read_section: Read markdown lines
    - find_text: Find exact text in markdown (fallback if pre-find fails)
    - propose_edit: Propose an edit (goes through validation)

The agent can propose multiple edits per task. Each edit is validated
before being accepted. Invalid edits are returned with feedback.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

from .events import (
    AgentThinkingEvent,
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
    JobType,
    Ledger,
    LedgerEntry,
    LLMCallRecord,
    Task,
    TaskType,
)
from .validation import auto_fix_minor_issues, validate_edit

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Worker Dependencies
# =============================================================================


@dataclass
class WorkerDeps:
    """Dependencies passed to worker agent tools."""

    job: Job
    page_image: Image.Image
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]]
    page_width: float

    # Mutable state
    current_markdown: str
    pending_edits: list[EditProposal]
    validated_edits: list[LedgerEntry]

    # Validation
    dictionary: list[str]

    # Event bus for streaming
    event_bus: EventBus | None = None


# =============================================================================
# Tool Return Types
# =============================================================================


class ViewResult(BaseModel):
    """Result from viewing an element."""

    success: bool
    description: str = ""
    markdown_content: str | None = None
    error: str | None = None


class ReadResult(BaseModel):
    """Result from reading markdown."""

    content: str
    start_line: int
    end_line: int
    total_lines: int


class ProposeEditResult(BaseModel):
    """Result from proposing an edit."""

    accepted: bool
    feedback: str | None = None
    applied: bool = False


class FindTextResult(BaseModel):
    """Result from searching for text in markdown."""

    found: bool
    exact_match: str | None = None
    context_before: str = ""
    context_after: str = ""
    line_number: int | None = None
    error: str | None = None


class TableMarkdownResult(BaseModel):
    """Result from getting table markdown."""

    success: bool
    markdown: str = ""  # The exact markdown for this table
    start_line: int = 0  # Line number where table starts (1-indexed)
    end_line: int = 0  # Line number where table ends (1-indexed)
    format_type: str = ""  # "comment", "markdown_table", "text_fragment"
    error: str | None = None


# =============================================================================
# Worker Output
# =============================================================================


class WorkerOutput(BaseModel):
    """Output from worker agent."""

    completed_tasks: list[str] = Field(
        default_factory=list,
        description="Task IDs that were completed",
    )
    failed_tasks: list[str] = Field(
        default_factory=list,
        description="Task IDs that could not be completed",
    )
    summary: str = Field(
        default="",
        description="Summary of work done",
    )


# =============================================================================
# System Prompt
# =============================================================================


WORKER_SYSTEM_PROMPT = """You are a document accessibility worker. Your job is to complete assigned tasks.

## Pre-loaded Context (Optimized Workflow)

Your prompt includes pre-loaded context to speed up processing:
- **Cropped element images**: Figures and tables are already cropped and shown (labeled with their target ID)
- **Placeholder text**: The exact text to replace is provided for each task (if found)

When pre-loaded context is available, you can skip the discovery tools and go straight to propose_edit().

## Available Tools

### propose_edit(target, before, after, reasoning, replace_line_start?, replace_line_end?)
**PRIMARY TOOL** - Propose an edit. Goes through validation:
- If approved: edit is applied
- If rejected: you get feedback to revise
- Use the pre-loaded placeholder as your 'before' field when available

### find_text(search_pattern) [FALLBACK]
Find exact text if placeholder wasn't pre-loaded.
Returns the exact_match field which you MUST copy as your 'before' field.

### view_figure(figure_index) / view_table(table_index) [FALLBACK]
View element if not pre-cropped above. Use when prompt says "use view_figure()".

### get_table_markdown(table_index)
Get the EXACT markdown for a table. Returns line numbers for robust replacement.

### read_section(start_line, end_line) / view_page()
Additional context tools if needed.

## CRITICAL: The 'before' Field MUST Be Exact

Your 'before' field MUST be an EXACT substring that exists in the current markdown.
- Use the pre-loaded placeholder text when provided
- If not provided, use find_text() to get the exact text
- NEVER guess or reconstruct text from the image

## Task Types

### ALT_TEXT
1. Look at the pre-cropped figure image above (labeled with target ID)
2. Use the pre-loaded placeholder as 'before' (or find_text() if not provided)
3. Determine if decorative or informative
4. If informative, write concise alt-text (max 125 chars)
5. Propose edit: use placeholder as 'before', ![alt text](image.png) as 'after'

### TABLE_TRANSCRIPTION
1. Look at the pre-cropped table image above
2. Use get_table_markdown(N) to get exact current markdown and line numbers
3. Transcribe as markdown table with proper | separators and header row
4. Propose edit with replace_line_start and replace_line_end from get_table_markdown

### HEADING_FIX
1. Use the pre-loaded placeholder (FIND text) as 'before'
2. Use the REPLACE WITH text from task context as 'after'
3. Only change the heading prefix (# symbols), not the text

**Level Reference:**
- H1 = # (document title only)
- H2 = ## (major sections)
- H3 = ### (subsections)
- H4 = #### (sub-subsections)

### OCR_FIX / SPELLING_FIX
1. Use pre-loaded placeholder or find_text() with the misspelled word
2. Propose edit with the correction

## Important Rules

1. Use pre-loaded context when available - skip discovery tools
2. If placeholder shows "Not found", use find_text() as fallback
3. Keep alt-text concise but meaningful (max 125 chars)
4. For decorative images, use empty alt: ![](image)
5. If an edit is rejected, read the feedback and try again
6. Complete all tasks before finishing

## Output
List which tasks you completed and which failed (with reason).
"""


# =============================================================================
# Tool Implementations
# =============================================================================


async def view_page_tool(ctx: RunContext[WorkerDeps]) -> ViewResult:
    """View the full page image and get the current markdown content.

    Returns both the page image (shown above) and the raw markdown text.
    IMPORTANT: Use the markdown_content field to copy exact text for your 'before' field in edits.
    """
    deps = ctx.deps

    logger.debug(f"Worker viewing page {deps.job.page}")

    return ViewResult(
        success=True,
        description=(
            f"Page {deps.job.page} image is shown above. "
            "The markdown_content field contains the EXACT text you must use "
            "for the 'before' field in propose_edit."
        ),
        markdown_content=deps.current_markdown,
    )


async def view_figure_tool(
    ctx: RunContext[WorkerDeps],
    figure_index: int,
) -> ViewResult:
    """View a specific figure.

    Args:
        figure_index: 1-indexed figure number on the page
    """
    deps = ctx.deps
    bbox_key = (deps.job.page, "figure", figure_index)

    if bbox_key not in deps.element_bboxes:
        return ViewResult(
            success=False,
            error=f"Figure {figure_index} not found on page {deps.job.page}",
        )

    logger.debug(f"Worker viewing figure {figure_index} on page {deps.job.page}")

    return ViewResult(
        success=True,
        description=f"Figure {figure_index} image is shown above.",
    )


async def view_table_tool(
    ctx: RunContext[WorkerDeps],
    table_index: int,
) -> ViewResult:
    """View a specific table.

    Args:
        table_index: 1-indexed table number on the page
    """
    deps = ctx.deps
    bbox_key = (deps.job.page, "table", table_index)

    if bbox_key not in deps.element_bboxes:
        return ViewResult(
            success=False,
            error=f"Table {table_index} not found on page {deps.job.page}",
        )

    logger.debug(f"Worker viewing table {table_index} on page {deps.job.page}")

    return ViewResult(
        success=True,
        description=f"Table {table_index} image is shown above.",
    )


async def read_section_tool(
    ctx: RunContext[WorkerDeps],
    start_line: int,
    end_line: int,
) -> ReadResult:
    """Read specific lines of markdown.

    Args:
        start_line: First line to read (1-indexed)
        end_line: Last line to read (inclusive)
    """
    deps = ctx.deps
    lines = deps.current_markdown.split("\n")
    total = len(lines)

    # Clamp to valid range
    start = max(1, start_line) - 1
    end = min(total, end_line)

    content = "\n".join(lines[start:end])

    return ReadResult(
        content=content,
        start_line=start_line,
        end_line=end_line,
        total_lines=total,
    )


async def find_text_tool(
    ctx: RunContext[WorkerDeps],
    search_pattern: str,
) -> FindTextResult:
    """Find text in the markdown and return the EXACT match with surrounding context.

    Use this tool to find placeholders or text you want to replace.
    The 'exact_match' field in the result is what you MUST use as your 'before' field in propose_edit.

    Args:
        search_pattern: Text or pattern to search for (e.g., "<!-- image", "![](", "Table 1")
    """
    deps = ctx.deps
    markdown = deps.current_markdown

    # Try exact substring match first
    if search_pattern in markdown:
        idx = markdown.find(search_pattern)
        # Get context around the match
        start = max(0, idx - 100)
        end = min(len(markdown), idx + len(search_pattern) + 100)

        # Find line number
        line_num = markdown[:idx].count("\n") + 1

        # For placeholders and images, try to get the full element
        full_match = search_pattern
        if "<!--" in search_pattern:
            # Get full comment
            comment_end = markdown.find("-->", idx)
            if comment_end > idx:
                full_match = markdown[idx : comment_end + 3]
        elif "![" in search_pattern:
            # Get full image markdown
            bracket_start = markdown.rfind("![", max(0, idx - 10), idx + 3)
            if bracket_start >= 0:
                paren_end = markdown.find(")", bracket_start)
                if paren_end > bracket_start:
                    full_match = markdown[bracket_start : paren_end + 1]

        return FindTextResult(
            found=True,
            exact_match=full_match,
            context_before=markdown[start:idx],
            context_after=markdown[idx + len(search_pattern) : end],
            line_number=line_num,
        )

    # Try regex pattern match
    try:
        pattern = re.compile(search_pattern, re.IGNORECASE)
        match = pattern.search(markdown)
        if match:
            idx = match.start()
            start = max(0, idx - 100)
            end = min(len(markdown), match.end() + 100)
            line_num = markdown[:idx].count("\n") + 1

            return FindTextResult(
                found=True,
                exact_match=match.group(0),
                context_before=markdown[start:idx],
                context_after=markdown[match.end() : end],
                line_number=line_num,
            )
    except re.error:
        pass

    return FindTextResult(
        found=False,
        error=f"Could not find '{search_pattern}' in the markdown. Try a different search pattern.",
    )


def _find_table_blocks(markdown: str) -> list[dict]:
    """Find all markdown table blocks in content.

    Returns list of dicts with 'content', 'start_line', 'end_line' keys.
    """
    blocks = []
    lines = markdown.split("\n")

    in_table = False
    table_start = 0
    table_lines = []

    for i, line in enumerate(lines):
        # Table detection: line starts with | or contains | - | pattern
        is_table_line = (line.strip().startswith("|") and line.strip().endswith("|")) or re.match(
            r"^\s*\|[\s\-:]+\|", line
        )

        if is_table_line and not in_table:
            in_table = True
            table_start = i + 1  # 1-indexed
            table_lines = [line]
        elif is_table_line and in_table:
            table_lines.append(line)
        elif not is_table_line and in_table:
            # End of table
            blocks.append(
                {
                    "content": "\n".join(table_lines),
                    "start_line": table_start,
                    "end_line": table_start + len(table_lines) - 1,
                }
            )
            in_table = False
            table_lines = []

    # Handle table at end of document
    if in_table and table_lines:
        blocks.append(
            {
                "content": "\n".join(table_lines),
                "start_line": table_start,
                "end_line": table_start + len(table_lines) - 1,
            }
        )

    return blocks


async def get_table_markdown_tool(
    ctx: RunContext[WorkerDeps],
    table_index: int,
) -> TableMarkdownResult:
    """Get the exact markdown representation of a table.

    Use this BEFORE proposing table edits to see exactly what you're replacing.
    The markdown field contains the EXACT text, and start_line/end_line can be
    used for line-range replacement.

    Args:
        table_index: 1-indexed table number on the page
    """
    deps = ctx.deps
    markdown = deps.current_markdown

    # Try to find table in different formats

    # Format 1: HTML comment placeholder <!-- table:N -->
    comment_pattern = rf"<!--\s*table:{table_index}\s*-->"
    match = re.search(comment_pattern, markdown, re.IGNORECASE)
    if match:
        start_line = markdown[: match.start()].count("\n") + 1
        return TableMarkdownResult(
            success=True,
            markdown=match.group(0),
            start_line=start_line,
            end_line=start_line,
            format_type="comment",
        )

    # Format 2: Markdown table blocks
    table_blocks = _find_table_blocks(markdown)
    if table_index <= len(table_blocks):
        block = table_blocks[table_index - 1]
        return TableMarkdownResult(
            success=True,
            markdown=block["content"],
            start_line=block["start_line"],
            end_line=block["end_line"],
            format_type="markdown_table",
        )

    # Format 3: Generic table placeholder (e.g., [Table N] or similar)
    generic_patterns = [
        rf"\[Table\s*{table_index}\]",
        rf"Table\s+{table_index}[:\.]",
    ]
    for pattern in generic_patterns:
        match = re.search(pattern, markdown, re.IGNORECASE)
        if match:
            start_line = markdown[: match.start()].count("\n") + 1
            return TableMarkdownResult(
                success=True,
                markdown=match.group(0),
                start_line=start_line,
                end_line=start_line,
                format_type="text_fragment",
            )

    return TableMarkdownResult(
        success=False,
        error=f"Table {table_index} not found in markdown. "
        f"Found {len(table_blocks)} markdown table blocks. "
        "Try using find_text() to search for the table content.",
    )


async def propose_edit_tool(
    ctx: RunContext[WorkerDeps],
    target: str,
    task_type: str,
    before: str,
    after: str,
    reasoning: str,
    replace_line_start: int | None = None,
    replace_line_end: int | None = None,
) -> ProposeEditResult:
    """Propose an edit to the markdown.

    The edit goes through validation. If rejected, you'll get feedback.

    For TEXT edits: Use 'before' field with exact text from find_text().
    For TABLE edits: Use replace_line_start/end from get_table_markdown() for robust multi-line replacement.

    Args:
        target: What's being edited (e.g., "fig:1", "table:2")
        task_type: Type of edit (alt_text, table_transcription, ocr_fix, etc.)
        before: EXACT original text to replace - must exist verbatim in markdown
        after: New text to replace it with
        reasoning: Why this edit is needed
        replace_line_start: Optional start line for block replacement (1-indexed, from get_table_markdown)
        replace_line_end: Optional end line for block replacement (1-indexed, from get_table_markdown)
    """
    deps = ctx.deps

    # Handle line-range replacement (for tables)
    if replace_line_start is not None and replace_line_end is not None:
        lines = deps.current_markdown.split("\n")

        # Validate line range
        if replace_line_start < 1 or replace_line_end > len(lines):
            return ProposeEditResult(
                accepted=False,
                feedback=f"Invalid line range {replace_line_start}-{replace_line_end}. "
                f"Document has {len(lines)} lines.",
            )

        # Extract the content being replaced for logging/validation
        before = "\n".join(lines[replace_line_start - 1 : replace_line_end])

    # Parse task type
    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        task_type_enum = TaskType.OCR_FIX

    proposal = EditProposal(
        target=target,
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
                target=target,
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
                target=target,
                approved=result.approved,
                feedback=result.feedback,
            )
        )

    if not result.approved:
        logger.warning(f"Edit rejected for {target}: {result.feedback}")
        return ProposeEditResult(
            accepted=False,
            feedback=result.feedback,
        )

    # Auto-fix minor issues
    fixed_after = auto_fix_minor_issues(after)

    # Apply the edit - use line-range replacement if specified
    if replace_line_start is not None and replace_line_end is not None:
        # Line-range replacement (more robust for multi-line content like tables)
        lines = deps.current_markdown.split("\n")
        new_lines = lines[: replace_line_start - 1] + fixed_after.split("\n") + lines[replace_line_end:]
        deps.current_markdown = "\n".join(new_lines)
        edit_applied = True
    elif before and before in deps.current_markdown:
        # Standard text replacement
        deps.current_markdown = deps.current_markdown.replace(before, fixed_after, 1)
        edit_applied = True
    else:
        edit_applied = False

    if edit_applied:
        # Create ledger entry
        entry = LedgerEntry(
            job_id=deps.job.job_id,
            page=deps.job.page,
            action=task_type_enum,
            target=target,
            before=before,
            after=fixed_after,
            reasoning=reasoning,
            confidence=0.9,
            validated=True,
        )
        deps.validated_edits.append(entry)

        # Emit commit event
        if deps.event_bus:
            deps.event_bus.emit(
                EditCommittedEvent(
                    document_id=deps.event_bus.document_id,
                    ledger_entry=entry,
                    content_preview=fixed_after,
                )
            )

        logger.info(f"Edit applied for {target}")
        return ProposeEditResult(accepted=True, applied=True)

    return ProposeEditResult(
        accepted=True,
        feedback="Edit accepted but target text not found to replace",
        applied=False,
    )


# =============================================================================
# Worker Agent
# =============================================================================


_worker_agent: Agent[WorkerDeps, WorkerOutput] | None = None


def _get_worker_agent() -> Agent[WorkerDeps, WorkerOutput]:
    """Get or create the worker agent."""
    global _worker_agent

    if _worker_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

        _worker_agent = Agent(
            model=model,
            deps_type=WorkerDeps,
            output_type=WorkerOutput,
            system_prompt=WORKER_SYSTEM_PROMPT,
        )

        # Register tools
        _worker_agent.tool(view_page_tool)
        _worker_agent.tool(view_figure_tool)
        _worker_agent.tool(view_table_tool)
        _worker_agent.tool(read_section_tool)
        _worker_agent.tool(find_text_tool)
        _worker_agent.tool(get_table_markdown_tool)
        _worker_agent.tool(propose_edit_tool)

        logger.info("Worker agent initialized")

    return _worker_agent


# =============================================================================
# Job Execution
# =============================================================================


# =============================================================================
# Agent Conversation Helpers
# =============================================================================


def _extract_message_type(msg: Any) -> str:
    """Extract message type from PydanticAI message object."""
    msg_class = msg.__class__.__name__
    if "SystemPrompt" in msg_class:
        return "system"
    if "UserPrompt" in msg_class:
        return "user"
    if "ToolReturn" in msg_class or "ToolResult" in msg_class:
        return "tool_result"
    if "ToolCall" in msg_class:
        return "tool_call"
    if "Text" in msg_class or "Model" in msg_class:
        return "assistant"
    return "unknown"


def _extract_content_preview(msg: Any, max_len: int = 500) -> str:
    """Extract preview of message content."""
    try:
        # Try common content attributes
        if hasattr(msg, "content"):
            content = str(msg.content)
        elif hasattr(msg, "parts"):
            parts = []
            for part in msg.parts:
                if hasattr(part, "content"):
                    parts.append(str(part.content)[:200])
                elif hasattr(part, "args"):
                    parts.append(f"[tool call: {getattr(part, 'tool_name', '?')}]")
            content = " | ".join(parts)
        else:
            content = str(msg)

        return content[:max_len] + "..." if len(content) > max_len else content
    except Exception:
        return str(msg)[:max_len]


def _extract_full_content(msg: Any) -> str | None:
    """Extract full message content."""
    try:
        if hasattr(msg, "content"):
            return str(msg.content)
        elif hasattr(msg, "parts"):
            parts = []
            for part in msg.parts:
                if hasattr(part, "content"):
                    parts.append(str(part.content))
            return "\n".join(parts) if parts else None
        return str(msg)
    except Exception:
        return None


def _extract_tool_info(msg: Any) -> tuple[str | None, dict | None, str | None]:
    """Extract tool name, args, and result from message.

    Returns: (tool_name, tool_args, tool_result_preview)
    """
    tool_name = None
    tool_args = None
    tool_result = None

    try:
        # Check for tool call parts
        if hasattr(msg, "parts"):
            for part in msg.parts:
                part_class = part.__class__.__name__
                if "ToolCall" in part_class:
                    tool_name = getattr(part, "tool_name", None)
                    if hasattr(part, "args"):
                        args = part.args
                        if hasattr(args, "model_dump"):
                            tool_args = args.model_dump()
                        elif isinstance(args, dict):
                            tool_args = args
                        else:
                            tool_args = {"raw": str(args)[:500]}
                elif "ToolReturn" in part_class or "ToolResult" in part_class:
                    tool_name = getattr(part, "tool_name", None)
                    content = getattr(part, "content", None)
                    if content:
                        tool_result = str(content)[:500]

        # Also check direct attributes
        if not tool_name and hasattr(msg, "tool_name"):
            tool_name = msg.tool_name
        if not tool_result and hasattr(msg, "content"):
            tool_result = str(msg.content)[:500]

    except Exception:
        pass

    return tool_name, tool_args, tool_result


def _emit_conversation_events(
    result: Any,
    job_id: str,
    agent_type: str,
    event_bus: EventBus,
) -> None:
    """Emit AgentThinkingEvent for each message in the conversation."""
    try:
        messages = result.all_messages()
        for i, msg in enumerate(messages):
            msg_type = _extract_message_type(msg)
            tool_name, tool_args, tool_result = _extract_tool_info(msg)

            event_bus.emit(
                AgentThinkingEvent(
                    document_id=event_bus.document_id,
                    job_id=job_id,
                    agent_type=agent_type,
                    step=i,
                    message_type=msg_type,
                    content_preview=_extract_content_preview(msg),
                    full_content=_extract_full_content(msg),
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result_preview=tool_result,
                )
            )
    except Exception as e:
        logger.warning(f"Failed to emit conversation events: {e}")


# =============================================================================
# Prompt Formatting Helpers
# =============================================================================


def _format_outline_entry(entry: Any, indent: int = 0) -> str:
    """Format a single outline entry with proper indentation.

    Args:
        entry: OutlineEntry object
        indent: Current indentation level

    Returns:
        Formatted string representation
    """
    prefix = "  " * indent
    level_indicator = f"H{entry.level}"
    page_info = f"(pp. {entry.page_start}"
    if entry.page_end != entry.page_start:
        page_info += f"-{entry.page_end}"
    page_info += ")"

    result = f"{prefix}- {level_indicator}: {entry.heading} {page_info}\n"

    # Recursively format children
    for child in entry.children:
        result += _format_outline_entry(child, indent + 1)

    return result


def _format_outline_for_prompt(outline: list[Any]) -> str:
    """Format the document outline for inclusion in the worker prompt.

    Args:
        outline: List of OutlineEntry objects

    Returns:
        Formatted outline string showing heading hierarchy
    """
    if not outline:
        return "No outline available for this section."

    result = ""
    for entry in outline:
        result += _format_outline_entry(entry)

    return result.strip()


def _format_task_description(task: Task) -> str:
    """Format a task description for the prompt.

    Args:
        task: Task object to format

    Returns:
        Formatted task description string
    """
    task_type = task.task_type.value
    target = task.target
    context = task.context

    # Provide task-specific formatting hints
    if task.task_type == TaskType.HEADING_FIX:
        # Parse the context to extract current and target levels if possible
        return f"- {task_type}: {target} - {context}"
    elif task.task_type == TaskType.ALT_TEXT:
        return f"- {task_type}: {target} - {context}"
    elif task.task_type == TaskType.TABLE_TRANSCRIPTION:
        return f"- {task_type}: {target} - {context}"
    elif task.task_type == TaskType.OCR_FIX:
        return f"- {task_type}: {target} - {context}"
    elif task.task_type == TaskType.SPELLING_FIX:
        return f"- {task_type}: {target} - {context}"
    else:
        return f"- {task_type}: {target} ({context})"


# =============================================================================
# Pre-emptive Context Loading (Optimization)
# =============================================================================


def _precrop_elements_for_job(
    job: Job,
    page_image: Image.Image,
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
) -> dict[str, Image.Image]:
    """Pre-crop all element images needed for a job's tasks.

    This eliminates the need for workers to call view_figure() or view_table()
    by providing cropped images upfront in the prompt.

    Args:
        job: The job containing tasks to analyze
        page_image: Full page image (PIL Image)
        element_bboxes: Bounding boxes for elements keyed by (page, type, index)
        page_width: Page width for coordinate scaling

    Returns:
        Dict mapping target (e.g., "fig:1", "table:2") to cropped PIL Image
    """
    from src.utils.image_utils import crop_element

    cropped_images: dict[str, Image.Image] = {}

    for task in job.tasks:
        if task.task_type == TaskType.ALT_TEXT:
            # Parse "fig:1" -> ("figure", 1)
            if task.target.startswith("fig:"):
                try:
                    idx = int(task.target.split(":")[1])
                    bbox_key = (job.page, "figure", idx)
                    if bbox_key in element_bboxes:
                        cropped_images[task.target] = crop_element(page_image, element_bboxes[bbox_key], page_width)
                        logger.debug(f"Pre-cropped figure {idx} for {task.target}")
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse figure target {task.target}: {e}")

        elif task.task_type == TaskType.TABLE_TRANSCRIPTION:
            # Parse "table:1" -> ("table", 1)
            if task.target.startswith("table:"):
                try:
                    idx = int(task.target.split(":")[1])
                    bbox_key = (job.page, "table", idx)
                    if bbox_key in element_bboxes:
                        cropped_images[task.target] = crop_element(page_image, element_bboxes[bbox_key], page_width)
                        logger.debug(f"Pre-cropped table {idx} for {task.target}")
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse table target {task.target}: {e}")

    return cropped_images


def _prefind_placeholders_for_job(
    job: Job,
    markdown: str,
) -> dict[str, str | None]:
    """Pre-find placeholder text for each task in the markdown.

    This eliminates the need for workers to call find_text() by providing
    the exact placeholder text upfront.

    Args:
        job: The job containing tasks to analyze
        markdown: The page's markdown content

    Returns:
        Dict mapping target to exact placeholder text found (None if not found)
    """
    placeholders: dict[str, str | None] = {}

    for task in job.tasks:
        placeholder = None

        if task.task_type == TaskType.ALT_TEXT:
            # Look for image placeholders: <!-- image N --> or ![](...)
            idx = task.target.split(":")[1] if ":" in task.target else "1"
            patterns = [
                f"<!-- image {idx} -->",
                f"<!-- image{idx} -->",
                f"<!--image {idx}-->",
                f"<!--image{idx}-->",
            ]
            # First try specific patterns
            for pattern in patterns:
                if pattern in markdown:
                    placeholder = pattern
                    break

            # If not found, try to find any image placeholder or empty image markdown
            if placeholder is None:
                # Look for empty image markdown like ![](figure_N.png)
                img_patterns = [
                    rf"!\[\]\([^)]*figure[_-]?{idx}[^)]*\)",
                    rf"!\[\]\([^)]*image[_-]?{idx}[^)]*\)",
                    rf"!\[\]\([^)]*{idx}[^)]*\.(?:png|jpg|jpeg)\)",
                ]
                for pattern in img_patterns:
                    match = re.search(pattern, markdown, re.IGNORECASE)
                    if match:
                        placeholder = match.group(0)
                        break

        elif task.task_type == TaskType.TABLE_TRANSCRIPTION:
            idx = task.target.split(":")[1] if ":" in task.target else "1"
            patterns = [
                f"<!-- table {idx} -->",
                f"<!-- table{idx} -->",
                f"<!--table {idx}-->",
                f"<!--table{idx}-->",
                f"[TABLE {idx}]",
                f"[Table {idx}]",
                f"[table {idx}]",
            ]
            for pattern in patterns:
                if pattern in markdown:
                    placeholder = pattern
                    break

        elif task.task_type == TaskType.HEADING_FIX:
            # Parse from task.context which has "FIND: ## Heading"
            if "FIND:" in task.context:
                lines = task.context.split("\n")
                for line in lines:
                    if line.strip().startswith("FIND:"):
                        find_text = line.replace("FIND:", "").strip()
                        if find_text in markdown:
                            placeholder = find_text
                        break

        placeholders[task.target] = placeholder

    return placeholders


def _build_preloaded_prompt(
    job: Job,
    context: Any,
    outline_summary: str,
    prefound_placeholders: dict[str, str | None],
    precropped_targets: set[str],
) -> str:
    """Build a prompt with all context pre-loaded for efficient processing.

    This creates a simplified prompt that tells the agent exactly what to do
    without requiring exploratory tool calls.

    Args:
        job: The job to execute
        context: JobContext with document info
        outline_summary: Formatted document outline
        prefound_placeholders: Pre-found placeholder text for each task
        precropped_targets: Set of targets that have pre-cropped images

    Returns:
        Formatted prompt string
    """
    task_sections = []
    for task in job.tasks:
        placeholder = prefound_placeholders.get(task.target)
        has_cropped_image = task.target in precropped_targets

        # Build task-specific instructions
        if placeholder:
            placeholder_info = f"**Placeholder to replace:** `{placeholder}`"
        else:
            placeholder_info = "**Placeholder:** Not found - use `find_text()` to locate it"

        if has_cropped_image:
            image_info = f"**Element image:** Shown above (labeled `{task.target}`)"
        else:
            image_info = "**Element image:** Use `view_figure()` or `view_table()` to see it"

        task_sections.append(f"""
### Task: {task.task_type.value} for `{task.target}`
- **Context:** {task.context}
- {placeholder_info}
- {image_info}
""")

    # Determine workflow based on pre-loading success
    has_all_placeholders = all(
        prefound_placeholders.get(t.target) is not None
        for t in job.tasks
        if t.task_type in (TaskType.ALT_TEXT, TaskType.TABLE_TRANSCRIPTION, TaskType.HEADING_FIX)
    )

    if has_all_placeholders and precropped_targets:
        workflow = """## Optimized Workflow (Context Pre-loaded)

For each task above:
1. The cropped element image is already shown above (if applicable)
2. The placeholder text to replace is provided above
3. Call `propose_edit()` with:
   - `before`: the exact placeholder text shown above
   - `after`: your generated content (alt-text, transcribed table, etc.)

If a placeholder shows "Not found", use `find_text()` to locate it first.
"""
    else:
        workflow = """## Standard Workflow

For each task:
1. Use `find_text()` to locate the placeholder if not provided above
2. View the element using `view_figure()` or `view_table()` if not shown above
3. Call `propose_edit()` with the exact placeholder as `before` and your content as `after`
4. If rejected, read the feedback and try again
"""

    return f"""Complete these tasks for page {job.page} of "{context.document_title}":

## Tasks (with pre-loaded context)
{"".join(task_sections)}

## Section Context
{context.section_context}

## Document Outline (Heading Hierarchy)
{outline_summary}

## Dictionary (domain terms)
{", ".join(context.dictionary[:20])}

## Current Markdown (AUTHORITATIVE SOURCE)
```
{job.page_markdown[:4000]}
```

{workflow}
Report which tasks you completed and which failed.
"""


@dataclass
class JobResult:
    """Result of executing a job."""

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


async def execute_job(
    job: Job,
    page_image: Image.Image,
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    ledger: Ledger,
    event_bus: EventBus | None = None,
) -> JobResult:
    """Execute a single job.

    Args:
        job: The job to execute
        page_image: Image of the page
        element_bboxes: Bounding boxes for elements
        page_width: Page width for bbox scaling
        ledger: Ledger to append entries to
        event_bus: Optional event bus for streaming

    Returns:
        JobResult with updated markdown and ledger entries
    """
    start_time = time.time()

    if event_bus:
        event_bus.emit(
            JobStartedEvent(
                document_id=event_bus.document_id,
                job_id=job.job_id,
                page=job.page,
                tasks=[f"{t.task_type.value}: {t.target}" for t in job.tasks],
            )
        )

    # Build task summary for prompt
    "\n".join(_format_task_description(t) for t in job.tasks)

    # Build context summary
    context = job.context

    # Build outline summary for heading context
    outline_summary = _format_outline_for_prompt(context.relevant_outline)

    # Create dependencies
    deps = WorkerDeps(
        job=job,
        page_image=page_image,
        element_bboxes=element_bboxes,
        page_width=page_width,
        current_markdown=job.page_markdown,
        pending_edits=[],
        validated_edits=[],
        dictionary=context.dictionary,
        event_bus=event_bus,
    )

    agent = _get_worker_agent()

    # =================================================================
    # Pre-emptive Context Loading (Optimization)
    # =================================================================

    # Pre-crop element images to eliminate view_figure/view_table calls
    precropped_images = _precrop_elements_for_job(job, page_image, element_bboxes, page_width)
    logger.debug(f"Pre-cropped {len(precropped_images)} elements for job {job.job_id}")

    # Pre-find placeholder text to eliminate find_text calls
    prefound_placeholders = _prefind_placeholders_for_job(job, job.page_markdown)
    found_count = sum(1 for v in prefound_placeholders.values() if v is not None)
    logger.debug(f"Pre-found {found_count}/{len(prefound_placeholders)} placeholders for job {job.job_id}")

    # =================================================================
    # Build Multimodal Message with Pre-cropped Images
    # =================================================================

    messages: list[Any] = []

    # Add full page image first
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")
    messages.extend(["Full page image:", image_content])

    # Add pre-cropped element images
    for task in job.tasks:
        if task.target in precropped_images:
            cropped = precropped_images[task.target]
            crop_buffer = BytesIO()
            cropped.save(crop_buffer, format="PNG")
            messages.append(f"\nCropped element `{task.target}`:")
            messages.append(BinaryContent(data=crop_buffer.getvalue(), media_type="image/png"))

    # Build the optimized prompt with pre-loaded context
    prompt = _build_preloaded_prompt(
        job=job,
        context=context,
        outline_summary=outline_summary,
        prefound_placeholders=prefound_placeholders,
        precropped_targets=set(precropped_images.keys()),
    )
    messages.append(prompt)

    try:
        result = await agent.run(
            messages,
            deps=deps,
        )

        output = result.output
        usage = result.usage()

        # Emit agent conversation events for visibility
        if event_bus:
            _emit_conversation_events(
                result=result,
                job_id=job.job_id,
                agent_type="worker",
                event_bus=event_bus,
            )

        # Add ledger entries
        for entry in deps.validated_edits:
            ledger.append(entry)

        duration_ms = int((time.time() - start_time) * 1000)

        # Create LLM call record for tracking
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        cost_cents = ((input_tokens * 0.00025) + (output_tokens * 0.00125)) / 10

        llm_call = LLMCallRecord(
            agent="worker",
            purpose=f"page_{job.page}_{job.tasks[0].task_type.value if job.tasks else 'unknown'}",
            page=job.page,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            timestamp=datetime.now(UTC),
            duration_ms=duration_ms,
        )

        if event_bus:
            event_bus.emit(
                JobCompletedEvent(
                    document_id=event_bus.document_id,
                    job_id=job.job_id,
                    page=job.page,
                    edits_made=len(deps.validated_edits),
                    edits_rejected=0,  # TODO: track this
                    duration_ms=duration_ms,
                    tokens_used=input_tokens + output_tokens,
                )
            )

        return JobResult(
            job_id=job.job_id,
            success=True,
            updated_markdown=deps.current_markdown,
            ledger_entries=deps.validated_edits,
            tasks_completed=len(output.completed_tasks),
            tasks_failed=len(output.failed_tasks),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            llm_call=llm_call,
        )

    except Exception as e:
        logger.error(f"Job {job.job_id} failed: {e}")

        duration_ms = int((time.time() - start_time) * 1000)

        if event_bus:
            event_bus.emit(
                JobFailedEvent(
                    document_id=event_bus.document_id,
                    job_id=job.job_id,
                    page=job.page,
                    error=str(e),
                    recoverable=False,
                )
            )

        return JobResult(
            job_id=job.job_id,
            success=False,
            updated_markdown=job.page_markdown,
            ledger_entries=[],
            tasks_completed=0,
            tasks_failed=len(job.tasks),
            input_tokens=0,
            output_tokens=0,
            duration_ms=duration_ms,
            error=str(e),
        )


# =============================================================================
# Parallel Job Execution
# =============================================================================


async def execute_jobs_parallel(
    jobs: list[Job],
    page_images: dict[int, Image.Image],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float,
    ledger: Ledger,
    max_concurrent: int = 3,
    event_bus: EventBus | None = None,
    page_markdowns: dict[int, str] | None = None,
) -> list[JobResult]:
    """Execute multiple jobs in parallel with agent routing.

    Routes jobs to appropriate agents based on job type:
    - PARAGRAPH jobs → ParagraphAgent (text flow fixes)
    - STRUCTURE/CONTENT jobs → Worker (alt-text, tables, headings)

    Args:
        jobs: Jobs to execute
        page_images: Page images indexed by page number
        element_bboxes: Element bounding boxes
        page_width: Page width for scaling
        ledger: Shared ledger
        max_concurrent: Max concurrent jobs
        event_bus: Optional event bus
        page_markdowns: Current markdown for each page (needed for PARAGRAPH jobs)

    Returns:
        List of job results
    """
    # Lazy import to avoid circular dependency
    from .paragraph_agent import execute_with_paragraph_agent

    # Build full document markdown for citation linking (ParagraphAgent needs this)
    full_document_markdown = ""
    if page_markdowns:
        full_document_markdown = "\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

    semaphore = asyncio.Semaphore(max_concurrent)

    async def execute_with_semaphore(job: Job) -> JobResult:
        async with semaphore:
            page_image = page_images.get(job.page)
            if page_image is None:
                logger.error(f"No image for page {job.page}")
                return JobResult(
                    job_id=job.job_id,
                    success=False,
                    updated_markdown=job.page_markdown,
                    ledger_entries=[],
                    tasks_completed=0,
                    tasks_failed=len(job.tasks),
                    input_tokens=0,
                    output_tokens=0,
                    duration_ms=0,
                    error="No page image available",
                )

            # Route PARAGRAPH jobs to ParagraphAgent
            if job.job_type == JobType.PARAGRAPH:
                current_markdown = (
                    page_markdowns.get(job.page, job.page_markdown)
                    if page_markdowns
                    else job.page_markdown
                )
                return await execute_with_paragraph_agent(
                    job=job,
                    page_image=page_image,
                    current_markdown=current_markdown,
                    full_document_markdown=full_document_markdown,
                    ledger=ledger,
                    event_bus=event_bus,
                    dictionary=job.context.dictionary if job.context else None,
                )

            # Route other jobs (STRUCTURE, CONTENT) to Worker
            return await execute_job(
                job=job,
                page_image=page_image,
                element_bboxes=element_bboxes,
                page_width=page_width,
                ledger=ledger,
                event_bus=event_bus,
            )

    # Sort jobs by priority
    sorted_jobs = sorted(jobs, key=lambda j: j.priority)

    # Execute in parallel with semaphore
    tasks = [execute_with_semaphore(job) for job in sorted_jobs]
    results = await asyncio.gather(*tasks)

    return list(results)
