"""Execution agent for the v3 correction pipeline.

This agent receives a list of issues from the analysis phase and applies fixes
using simple, composable tools - modeled after Claude Code's Edit tool approach.

Architecture:
- Takes list of issues (PageIssue from analysis_agent) and current markdown state
- For each issue, decides HOW to fix based on issue type and description
- Uses simple tools: describe_figure, describe_table, find_and_replace, read_section
- Can request page image with view_page() if uncertain
- Verifies each edit with read_section after applying
- Builds correction log for audit/review

Tools do simple things. Agent decides how to apply fixes.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.usage import UsageLimits

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

from .analysis_agent import PageIssue
from .models import Correction
from .tools import (
    FigureDescriptionResult,
    FindReplaceResult,
    SectionRead,
    TableDescriptionResult,
    describe_figure_from_image,
    describe_table_from_image,
    find_and_replace,
    read_section,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

MODEL_TIER = ModelTier.EFFICIENT  # Haiku 4.5


# =============================================================================
# Execution Result Model
# =============================================================================


class ExecutionUsage(BaseModel):
    """Usage info from execution agent."""

    input_tokens: int = 0
    output_tokens: int = 0


class ExecutionResult(BaseModel):
    """Result from executing corrections on a page."""

    page_num: int = Field(..., description="Page number that was processed")
    corrections_made: list[Correction] = Field(
        default_factory=list, description="Corrections applied"
    )
    issues_resolved: list[str] = Field(
        default_factory=list, description="IDs of issues that were successfully fixed"
    )
    issues_failed: list[str] = Field(
        default_factory=list, description="IDs of issues that couldn't be fixed"
    )
    final_markdown: str = Field(..., description="Markdown after all corrections")
    usage: ExecutionUsage = Field(default_factory=ExecutionUsage, description="Token usage")


# =============================================================================
# Execution Dependencies
# =============================================================================


@dataclass
class ExecutionDeps:
    """Dependencies for the execution agent.

    The agent updates markdown in place as edits are made.
    Page image is loaded on-demand via view_page() tool.
    """

    # Current page being processed
    page_num: int

    # Current markdown state (updated as edits are made)
    markdown: str

    # Page image (loaded on-demand via view_page())
    page_image: "Image.Image | None" = None

    # All page images for view_page() tool
    page_images: dict[int, "Image.Image"] = field(default_factory=dict)

    # Bounding boxes for figures/tables: (page, element_type, index) -> (l, t, r, b)
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = field(
        default_factory=dict
    )

    # Page width for coordinate scaling
    page_width: float = 612.0  # Standard US Letter width in points

    # Corrections log (built as edits are made)
    corrections: list[Correction] = field(default_factory=list)


# =============================================================================
# System Prompt
# =============================================================================

EXECUTION_SYSTEM_PROMPT = '''You are a document editor. You receive a list of issues and fix them.

## Your Tools
- describe_figure(id) -> Get figure info (type, alt_text, suggested_markdown)
- describe_table(id) -> Get table transcription (markdown_table)
- find_and_replace(original, replacement, reasoning) -> Apply an edit
- read_section(start, end) -> Read lines to verify edits
- view_page() -> See the page image if you need visual context

## CRITICAL: NEVER FABRICATE CONTENT
- You MUST only use data returned by tools (describe_figure, describe_table)
- NEVER invent table data, row counts, column values, or cell contents
- NEVER make up alt-text descriptions - only use what describe_figure returns
- If a tool returns an ERROR or low confidence (<0.5), report the issue as FAILED
- Better to fail an issue than to insert fabricated content

## Workflow for Each Issue

### For Figure Issues:
1. Call describe_figure(id) to get the description
2. CHECK: If error or confidence < 0.5, add to issues_failed and STOP
3. Call find_and_replace("<!-- image:N -->", suggested_markdown, reasoning)
4. Call read_section around the edit to verify

### For Table Issues:
1. Call describe_table(id) to get the transcription
2. CHECK: If error or confidence < 0.5, add to issues_failed and STOP
3. Call find_and_replace("<!-- table:N -->", markdown_table, reasoning)
4. Call read_section around the edit to verify

### For Spelling/Heading Issues:
1. Call find_and_replace(original, corrected, reasoning) directly
2. Call read_section around the edit to verify

### If Uncertain:
- Call view_page() to see the visual context
- Then proceed with the fix

## CRITICAL: Issue Descriptions Are NOT Content Sources
- Issue descriptions tell you WHAT to look for, not WHAT to write
- Content ONLY comes from tool results (describe_figure, describe_table, view_page)
- If you cannot verify content exists using a tool, mark the issue as FAILED
- NEVER add sections, paragraphs, or bullet lists based solely on issue descriptions
- When fixing "missing content" issues, you MUST call view_page() first to verify the content exists

## Important
- Process issues one at a time
- Verify each edit with read_section
- If find_and_replace fails ("not found" or "not unique"), report the issue as failed
- If tool returns error/low confidence, report the issue as failed
- Reasoning should explain WHY you made the fix
'''


# =============================================================================
# Agent Output Schema
# =============================================================================


class ExecutionOutput(BaseModel):
    """Output from one iteration of the execution agent."""

    thinking: str = Field(
        ..., description="Brief explanation of what was done"
    )
    issues_resolved: list[str] = Field(
        default_factory=list, description="IDs of issues resolved in this iteration"
    )
    issues_failed: list[str] = Field(
        default_factory=list, description="IDs of issues that couldn't be fixed"
    )
    needs_more_work: bool = Field(
        default=False, description="Whether there are more issues to process"
    )


# =============================================================================
# Agent Creation
# =============================================================================


def _create_execution_agent() -> Agent[ExecutionDeps, ExecutionOutput]:
    """Create the execution agent with tools.

    Returns:
        Configured PydanticAI agent with execution tools
    """
    model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

    agent: Agent[ExecutionDeps, ExecutionOutput] = Agent(
        model=model,
        output_type=ExecutionOutput,
        system_prompt=EXECUTION_SYSTEM_PROMPT,
        deps_type=ExecutionDeps,
    )

    # -------------------------------------------------------------------------
    # Tool: describe_figure
    # -------------------------------------------------------------------------
    @agent.tool
    async def tool_describe_figure(
        ctx: RunContext[ExecutionDeps],
        figure_id: int,
    ) -> FigureDescriptionResult:
        """Get description for a figure by its ID (1-indexed on the page).

        Calls the figure description subagent with the cropped image.
        Returns figure type, alt text, and suggested markdown.

        Args:
            figure_id: Which figure on the page (1-indexed)
        """
        deps = ctx.deps
        page = deps.page_num

        # Get bbox for this figure
        bbox_key = (page, "figure", figure_id)
        bbox = deps.element_bboxes.get(bbox_key)

        if bbox is None:
            logger.warning(f"No bbox found for figure {figure_id} on page {page}")
            return FigureDescriptionResult(
                figure_type="error",
                alt_text=f"ERROR: Figure {figure_id} DOES NOT EXIST on page {page}. Mark this issue as FAILED.",
                long_description=None,
                suggested_markdown=f"ERROR: Figure {figure_id} does not exist",
                confidence=0.0,
            )

        page_image = deps.page_images.get(page)
        if page_image is None:
            logger.warning(f"No page image for page {page}")
            return FigureDescriptionResult(
                figure_type="informative",
                alt_text="[Figure description unavailable - no page image]",
                long_description=None,
                suggested_markdown="![[Figure description unavailable]](image)",
                confidence=0.0,
            )

        # Crop the figure from the page image
        from src.utils.image_utils import crop_and_highlight

        cropped, highlighted = crop_and_highlight(page_image, bbox, deps.page_width)

        logger.info(f"Describing figure {figure_id} on page {page}: bbox={bbox}")

        # Debug image saving (only when DEBUG_IMAGE_DIR env var is set)
        debug_dir = os.environ.get("DEBUG_IMAGE_DIR")
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            cropped.save(f"{debug_dir}/figure_p{page}_f{figure_id}_cropped.png")
            highlighted.save(f"{debug_dir}/figure_p{page}_f{figure_id}_highlighted.png")
            logger.info(f"Saved debug images to {debug_dir}")

        # Call the subagent
        result = await describe_figure_from_image(
            cropped_image=cropped,
            highlighted_page=highlighted,
            page_num=page,
        )

        return result

    # -------------------------------------------------------------------------
    # Tool: describe_table
    # -------------------------------------------------------------------------
    @agent.tool
    async def tool_describe_table(
        ctx: RunContext[ExecutionDeps],
        table_id: int,
    ) -> TableDescriptionResult:
        """Get markdown transcription for a table by its ID (1-indexed on the page).

        Calls the table transcription subagent with the cropped image.
        Returns the markdown table and dimensions.

        Args:
            table_id: Which table on the page (1-indexed)
        """
        deps = ctx.deps
        page = deps.page_num

        # Get bbox for this table
        bbox_key = (page, "table", table_id)
        bbox = deps.element_bboxes.get(bbox_key)

        if bbox is None:
            logger.warning(f"No bbox found for table {table_id} on page {page}")
            return TableDescriptionResult(
                markdown_table=f"ERROR: Table {table_id} DOES NOT EXIST on page {page}. Mark this issue as FAILED.",
                row_count=0,
                col_count=0,
                has_header=False,
                confidence=0.0,
            )

        page_image = deps.page_images.get(page)
        if page_image is None:
            logger.warning(f"No page image for page {page}")
            return TableDescriptionResult(
                markdown_table="| Error | No page image available |",
                row_count=1,
                col_count=2,
                has_header=False,
                confidence=0.0,
            )

        # Crop the table from the page image
        from src.utils.image_utils import crop_and_highlight

        cropped, highlighted = crop_and_highlight(page_image, bbox, deps.page_width)

        logger.info(f"Transcribing table {table_id} on page {page}: bbox={bbox}")

        # Debug image saving (only when DEBUG_IMAGE_DIR env var is set)
        debug_dir = os.environ.get("DEBUG_IMAGE_DIR")
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            cropped.save(f"{debug_dir}/table_p{page}_t{table_id}_cropped.png")
            highlighted.save(f"{debug_dir}/table_p{page}_t{table_id}_highlighted.png")
            logger.info(f"Saved debug images to {debug_dir}")

        # Call the subagent
        result = await describe_table_from_image(
            cropped_image=cropped,
            highlighted_page=highlighted,
            page_num=page,
        )

        return result

    # -------------------------------------------------------------------------
    # Tool: find_and_replace
    # -------------------------------------------------------------------------
    @agent.tool
    def tool_find_and_replace(
        ctx: RunContext[ExecutionDeps],
        original: str,
        replacement: str,
        reasoning: str,
    ) -> FindReplaceResult:
        """Apply a text replacement edit to the markdown.

        Finds the original text and replaces it with the replacement.
        Fails if original text is not found or appears multiple times.

        Args:
            original: Exact text to find and replace (must be unique)
            replacement: Text to replace it with
            reasoning: Why this edit is being made (for audit log)
        """
        deps = ctx.deps

        result = find_and_replace(
            markdown=deps.markdown,
            original=original,
            replacement=replacement,
            reasoning=reasoning,
        )

        if result.success and result.new_markdown:
            # Update the deps with new markdown
            deps.markdown = result.new_markdown

            # Record the correction
            correction = Correction(
                page=deps.page_num,
                edit_type="content_fix",  # Will be refined based on context
                original=original,
                corrected=replacement,
                reasoning=reasoning,
                confidence=1.0,
                source="execution_agent",
            )
            deps.corrections.append(correction)

            logger.debug(f"Applied edit: {reasoning[:50]}...")

        return result

    # -------------------------------------------------------------------------
    # Tool: read_section
    # -------------------------------------------------------------------------
    @agent.tool
    def tool_read_section(
        ctx: RunContext[ExecutionDeps],
        start_line: int,
        end_line: int,
    ) -> SectionRead:
        """Read specific lines from the markdown to verify edits.

        Line numbers are 1-indexed and inclusive on both ends.

        Args:
            start_line: First line to read (1-indexed)
            end_line: Last line to read (1-indexed, inclusive)
        """
        return read_section(ctx.deps.markdown, start_line, end_line)

    # -------------------------------------------------------------------------
    # Tool: view_page
    # -------------------------------------------------------------------------
    @agent.tool
    def tool_view_page(ctx: RunContext[ExecutionDeps]) -> str:
        """Request the page image for visual context.

        Call this if you're uncertain about what an element looks like
        or need visual context to decide how to fix an issue.

        Returns a confirmation message - the image will be included
        in the next prompt.
        """
        deps = ctx.deps
        page = deps.page_num

        if page in deps.page_images:
            deps.page_image = deps.page_images[page]
            return f"Page {page} image loaded. It will be included in visual context."
        else:
            return f"No image available for page {page}."

    return agent


# =============================================================================
# Main Execution Function
# =============================================================================


def _extract_element_id(issue_id: str) -> int | None:
    """Extract element number from issue_id like 'image:1' or 'table:2'.

    Args:
        issue_id: The issue identifier (e.g., 'image:1', 'table:2', 'line:15')

    Returns:
        The element number if present, None otherwise
    """
    match = re.match(r"(?:image|table|figure):(\d+)", issue_id)
    if match:
        return int(match.group(1))
    return None


def _infer_resolved_from_corrections(
    corrections: list[Correction],
    issues: list[PageIssue],
) -> list[str]:
    """Infer resolved issues from corrections that were actually made.

    The LLM sometimes reports resolved issues in thinking text rather than
    the structured output field. This function uses corrections_made as the
    source of truth to infer which issues were likely resolved.

    Args:
        corrections: List of corrections that were applied
        issues: List of issues that were supposed to be fixed

    Returns:
        List of issue_ids that were likely resolved based on corrections made
    """
    resolved: list[str] = []

    for issue in issues:
        issue_id = issue.issue_id

        # Check if any correction's reasoning mentions the issue_id
        for correction in corrections:
            reasoning_lower = correction.reasoning.lower()
            if issue_id.lower() in reasoning_lower:
                resolved.append(issue_id)
                break

        if issue_id in resolved:
            continue

        # Match by element type for image/table placeholders
        # Issue IDs like "image:1", "table:2" correspond to placeholders
        if issue.issue_type in ("image_placeholder", "table_placeholder", "figure", "table"):
            # Check if any correction fixed this type of element
            for correction in corrections:
                original_lower = correction.original.lower()
                # Image placeholders: <!-- image --> or <!-- image:N -->
                if issue.issue_type in ("image_placeholder", "figure") and (
                    "<!-- image" in original_lower
                    or "image" in original_lower
                ):
                    resolved.append(issue_id)
                    break
                # Table placeholders: <!-- table --> or <!-- table:N -->
                if issue.issue_type in ("table_placeholder", "table") and (
                    "<!-- table" in original_lower
                    or "table" in original_lower
                ):
                    resolved.append(issue_id)
                    break

    return resolved


async def execute_corrections(
    page_num: int,
    issues: list[PageIssue],
    markdown: str,
    page_images: dict[int, "Image.Image"],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float = 612.0,
    max_iterations: int = 3,
    context_prompt: str = "",
) -> ExecutionResult:
    """Execute corrections for identified issues on a page.

    This is the main entry point for the execution agent. It:
    1. Creates dependencies with current state
    2. Runs the agent with the list of issues (PageIssue from analysis_agent)
    3. Tracks corrections and resolution status
    4. Returns final markdown and metrics

    Args:
        page_num: Page number being processed (1-indexed)
        issues: List of PageIssue items from analysis phase
        markdown: Current markdown state
        page_images: All page images (for view_page tool)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for coordinate scaling
        max_iterations: Maximum agent iterations
        context_prompt: Optional pre-computed document context to include in prompt

    Returns:
        ExecutionResult with corrections made and final markdown
    """
    if not issues:
        logger.info(f"No issues to fix on page {page_num}")
        return ExecutionResult(
            page_num=page_num,
            corrections_made=[],
            issues_resolved=[],
            issues_failed=[],
            final_markdown=markdown,
            usage=ExecutionUsage(),
        )

    agent = _create_execution_agent()

    # Initialize dependencies
    deps = ExecutionDeps(
        page_num=page_num,
        markdown=markdown,
        page_images=page_images,
        element_bboxes=element_bboxes,
        page_width=page_width,
    )

    # Track resolution status
    all_resolved: list[str] = []
    all_failed: list[str] = []

    # Track cumulative usage across iterations
    total_input_tokens = 0
    total_output_tokens = 0

    # Build element inventory for this page (prevents hallucinated figure/table IDs)
    valid_figures = sorted([
        idx for (pg, elem_type, idx) in element_bboxes.keys()
        if pg == page_num and elem_type == "figure"
    ])
    valid_tables = sorted([
        idx for (pg, elem_type, idx) in element_bboxes.keys()
        if pg == page_num and elem_type == "table"
    ])

    # Build user prompt with issues (using PageIssue fields)
    issues_text = "\n".join([
        f"- [{issue.issue_id}] {issue.issue_type.upper()} ({issue.severity}): {issue.description}"
        for issue in issues
    ])

    # Include document context if provided
    context_section = ""
    if context_prompt:
        context_section = f"\n{context_prompt}\n"

    user_prompt = f"""Fix issues on page {page_num}.
{context_section}
AVAILABLE ELEMENTS (only these exist):
- Figures: {valid_figures if valid_figures else 'NONE'}
- Tables: {valid_tables if valid_tables else 'NONE'}

ISSUES:
{issues_text}

MARKDOWN:
```
{markdown}
```

Process each issue. If an issue references a non-existent element, mark it FAILED immediately.
"""

    # Prepare messages
    messages: list[Any] = [user_prompt]

    # Run agent with iterations
    for iteration in range(max_iterations):
        logger.info(f"Execution iteration {iteration + 1}/{max_iterations} for page {page_num}")

        # If page image was requested, include it
        if deps.page_image is not None:
            buffer = BytesIO()
            deps.page_image.save(buffer, format="PNG")
            messages.append(
                BinaryContent(data=buffer.getvalue(), media_type="image/png")
            )
            deps.page_image = None  # Clear after including

        # Run agent (limit to 200 requests to prevent runaway costs)
        result = await agent.run(
            messages, deps=deps, usage_limits=UsageLimits(request_limit=200)
        )
        output = result.output

        # Accumulate usage
        usage_info = result.usage()
        total_input_tokens += usage_info.input_tokens or 0
        total_output_tokens += usage_info.output_tokens or 0

        logger.info(f"Iteration {iteration + 1}: {output.thinking}")

        # Collect resolved/failed
        all_resolved.extend(output.issues_resolved)
        all_failed.extend(output.issues_failed)

        # Check if done
        if not output.needs_more_work:
            logger.info(f"Execution complete after {iteration + 1} iterations")
            break

        # Update messages for next iteration with remaining issues
        remaining = [
            i for i in issues
            if i.issue_id not in all_resolved and i.issue_id not in all_failed
        ]
        if not remaining:
            break

        remaining_text = "\n".join([
            f"- [{issue.issue_id}] {issue.issue_type.upper()}: {issue.description}"
            for issue in remaining
        ])

        messages = [
            f"Continue fixing remaining issues:\n\n{remaining_text}\n\n"
            f"Current markdown:\n```markdown\n{deps.markdown}\n```"
        ]

    # Infer resolved issues from corrections made (in case LLM didn't report them)
    inferred_resolved = _infer_resolved_from_corrections(deps.corrections, issues)

    # Merge model-reported resolved with inferred resolved
    merged_resolved = list(set(all_resolved) | set(inferred_resolved))

    logger.info(
        f"Resolved issues: model_reported={len(all_resolved)}, "
        f"inferred={len(inferred_resolved)}, merged={len(merged_resolved)}, "
        f"tokens={total_input_tokens}/{total_output_tokens}"
    )

    return ExecutionResult(
        page_num=page_num,
        corrections_made=deps.corrections,
        issues_resolved=merged_resolved,
        issues_failed=list(set(all_failed)),
        final_markdown=deps.markdown,
        usage=ExecutionUsage(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        ),
    )


# =============================================================================
# Convenience Functions
# =============================================================================


async def execute_single_issue(
    issue: PageIssue,
    page_num: int,
    markdown: str,
    page_images: dict[int, "Image.Image"],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float = 612.0,
) -> ExecutionResult:
    """Execute correction for a single issue.

    Convenience wrapper around execute_corrections for single-issue processing.

    Args:
        issue: The PageIssue to fix (from analysis_agent)
        page_num: Page number where the issue is located
        markdown: Current markdown state
        page_images: All page images
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for coordinate scaling

    Returns:
        ExecutionResult with correction made
    """
    return await execute_corrections(
        page_num=page_num,
        issues=[issue],
        markdown=markdown,
        page_images=page_images,
        element_bboxes=element_bboxes,
        page_width=page_width,
        max_iterations=1,
    )


# =============================================================================
# Reset for Testing
# =============================================================================

# Agent instance cache
_execution_agent: Agent[ExecutionDeps, ExecutionOutput] | None = None


def reset_execution_agent() -> None:
    """Reset the execution agent singleton for testing."""
    global _execution_agent
    _execution_agent = None
    logger.debug("Execution agent reset")
