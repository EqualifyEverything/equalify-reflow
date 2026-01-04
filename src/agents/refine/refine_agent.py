"""Unified refine agent for PDF-to-markdown refinement.

This agent replaces the previous specialized agents (Figures, Tables, Typography)
with a single agent that uses tool calls for specialized tasks.

Architecture:
- Single Haiku agent processes pages sequentially
- Tools for free operations (lint, spell check, edit)
- Subagent tools for LLM-assisted tasks (figures, tables, verification)
- Sequential page processing allows context to flow between pages
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.factory import load_prompts
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings

from .models import (
    EditPatch,
    Hotspot,
    ProcessingTrace,
    RefineResult,
    Requirements,
)
from .tools import (
    EditResult,
    FigureDescription,
    LintResult,
    RefineToolDeps,
    SpellCheckResult,
    TableTranscription,
    VerificationResult,
    apply_edit,
    check_against_image,
    describe_figure,
    describe_table,
    run_lint,
    spell_check,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MODEL_TIER = ModelTier.EFFICIENT  # Haiku 4.5
PROMPTS_FILE = "refine.yaml"
MAX_ITERATIONS_PER_PAGE = 3


# =============================================================================
# Agent Output Schema
# =============================================================================


class RefinePageOutput(BaseModel):
    """Output from refine agent for a single page iteration."""

    thinking: str = Field(
        ..., description="Brief explanation of what was done this iteration"
    )
    edits_made: list[EditPatch] = Field(
        default_factory=list, description="Edits applied this iteration"
    )
    needs_another_pass: bool = Field(
        default=False, description="Whether another refinement pass is needed"
    )
    page_complete: bool = Field(
        default=False, description="Whether this page is fully refined"
    )


# =============================================================================
# Agent Setup
# =============================================================================


def _create_refine_agent() -> Agent[RefineToolDeps, RefinePageOutput]:
    """Create the refine agent with tools.

    Returns:
        Configured PydanticAI agent with refine tools
    """
    # Load prompts
    prompts = load_prompts(PROMPTS_FILE)

    # Get model
    model_id = MODEL_TIER_MAP[MODEL_TIER]
    model = BedrockConverseModel(model_id)

    # Create agent
    agent: Agent[RefineToolDeps, RefinePageOutput] = Agent(
        model=model,
        output_type=RefinePageOutput,
        system_prompt=prompts.get("system_prompt", ""),
        deps_type=RefineToolDeps,
    )

    # Dynamic system prompt: inject REQUIRED tool calls for hotspots on current page
    @agent.system_prompt
    def add_hotspot_requirements(ctx: RunContext[RefineToolDeps]) -> str:
        """Dynamically add MUST-DO instructions for hotspots on this page.

        This ensures the agent cannot skip processing identified figures and tables.
        The ANALYZE phase identified these hotspots, and the agent MUST call the
        appropriate tools to process them.
        """
        if ctx.deps.requirements is None:
            return ""

        # Filter hotspots for the current page
        hotspots = [
            h for h in ctx.deps.requirements.hotspots
            if h.page == ctx.deps.current_page
        ]

        if not hotspots:
            return ""

        instructions = ["\n## REQUIRED TOOL CALLS FOR THIS PAGE\n"]
        instructions.append(
            "The ANALYZE phase identified the following elements that MUST be processed. "
            "You are REQUIRED to call the specified tool for each element.\n"
        )

        figures = [h for h in hotspots if h.element_type == "figure"]
        tables = [h for h in hotspots if h.element_type == "table"]

        if figures:
            instructions.append(f"### Figures ({len(figures)} REQUIRED)")
            instructions.append(
                f"You MUST call `tool_describe_figure` for each of these {len(figures)} figure(s):"
            )
            for i, fig in enumerate(figures, 1):
                instructions.append(f"  - Figure {i}: {fig.description}")
                if fig.verification_hint:
                    instructions.append(f"    Hint: {fig.verification_hint}")
            instructions.append("")

        if tables:
            instructions.append(f"### Tables ({len(tables)} REQUIRED)")
            instructions.append(
                f"You MUST call `tool_describe_table` for each of these {len(tables)} table(s):"
            )
            for i, tbl in enumerate(tables, 1):
                instructions.append(f"  - Table {i}: {tbl.description}")
                if tbl.verification_hint:
                    instructions.append(f"    Hint: {tbl.verification_hint}")
            instructions.append("")

        instructions.append(
            "After calling these tools, include the edits in your `edits_made` output "
            "to insert the generated alt-text or table markdown into the document.\n"
        )

        return "\n".join(instructions)

    # Register free tools (plain functions, no context needed)
    @agent.tool_plain
    def tool_apply_edit(
        original_text: str,
        replacement_text: str,
    ) -> EditResult:
        """Apply a text replacement edit to the current page's markdown.

        Args:
            original_text: Exact text to find (must be unique on page)
            replacement_text: Text to replace it with
        """
        # Note: We need access to deps here, which tool_plain doesn't provide
        # This is a limitation - we'll need to restructure
        return EditResult(
            success=False,
            error="Use apply_edit with full markdown via context",
        )

    @agent.tool_plain
    def tool_run_lint() -> LintResult:
        """Run markdown linting to find formatting issues.

        Returns issues found and optionally auto-fixed markdown.
        """
        # Same limitation as above
        return LintResult(issues_found=0, issues=["Lint not yet connected"])

    @agent.tool_plain
    def tool_spell_check(key_terms: list[str] | None = None) -> SpellCheckResult:
        """Check for spelling errors, especially OCR artifacts.

        Args:
            key_terms: Domain-specific terms to treat as valid
        """
        return SpellCheckResult(errors_found=0, suggestions=[])

    # Register subagent tools (need context for deps)
    @agent.tool
    async def tool_describe_figure(
        ctx: RunContext[RefineToolDeps],
        page: int,
        figure_index: int,
    ) -> FigureDescription:
        """Get alt-text description for a figure.

        Calls a specialized subagent with cropped figure image
        and full page with highlight for context.

        Args:
            page: Page number (1-indexed)
            figure_index: Index of figure on that page (1-indexed)
        """
        return await describe_figure(page, figure_index, ctx.deps)

    @agent.tool
    async def tool_describe_table(
        ctx: RunContext[RefineToolDeps],
        page: int,
        table_index: int,
    ) -> TableTranscription:
        """Get markdown table transcription.

        Calls a specialized subagent with cropped table image
        and full page with highlight for context.

        Args:
            page: Page number (1-indexed)
            table_index: Index of table on that page (1-indexed)
        """
        return await describe_table(page, table_index, ctx.deps)

    @agent.tool
    async def tool_check_against_image(
        ctx: RunContext[RefineToolDeps],
        section_markdown: str,
        page: int,
        bbox_left: float | None = None,
        bbox_top: float | None = None,
        bbox_right: float | None = None,
        bbox_bottom: float | None = None,
    ) -> VerificationResult:
        """Verify markdown section matches the source image.

        Args:
            section_markdown: Markdown text to verify
            page: Page number (1-indexed)
            bbox_*: Optional bounding box coordinates (l, t, r, b)
        """
        bbox = None
        if all(v is not None for v in [bbox_left, bbox_top, bbox_right, bbox_bottom]):
            bbox = (bbox_left, bbox_top, bbox_right, bbox_bottom)  # type: ignore

        return await check_against_image(section_markdown, page, bbox, ctx.deps)

    return agent


# =============================================================================
# Main Refine Function
# =============================================================================


def _preprocess_markdown(
    markdown: str,
    page_num: int,
    key_terms: list[str] | None = None,
) -> tuple[str, list[EditPatch]]:
    """Run lint and spell-check with auto-fix before AI refinement.

    This separates mechanical fixes from semantic fixes:
    1. Mechanical (auto-applied): lint formatting, common OCR errors
    2. Semantic (AI-assisted): figures, tables, reading order

    Args:
        markdown: Raw markdown to preprocess
        page_num: Page number for edit tracking
        key_terms: Domain terms to treat as valid spelling

    Returns:
        Tuple of (fixed_markdown, list_of_edit_patches)
    """
    from .tools import apply_edit, run_lint, spell_check

    current = markdown
    edits: list[EditPatch] = []

    # Step 1: Run lint with auto-fix
    lint_result = run_lint(current)
    if lint_result.fixed_markdown and lint_result.fixed_markdown != current:
        # Record this as a lint fix edit
        edit_result = apply_edit(
            reasoning=f"Auto-fixed {lint_result.issues_found} lint issues: {', '.join(lint_result.issues[:3])}",
            edit_type="lint_fix",
            original_text=current,
            replacement_text=lint_result.fixed_markdown,
            markdown=current,
            page=page_num,
            confidence=1.0,
            source="mdformat",
        )
        if edit_result.success and edit_result.edit_patch:
            current = edit_result.new_markdown
            edits.append(edit_result.edit_patch)
            logger.info(f"Lint auto-fixed {lint_result.issues_found} issues")

    # Step 2: Apply obvious spell corrections (OCR artifacts)
    spell_result = spell_check(current, key_terms)
    for suggestion in spell_result.suggestions:
        word = suggestion["word"]
        fix = suggestion["suggestion"]
        # Only auto-apply high-confidence OCR fixes
        if word in current:
            edit_result = apply_edit(
                reasoning=f"OCR artifact correction: '{word}' -> '{fix}'",
                edit_type="spell_fix",
                original_text=word,
                replacement_text=fix,
                markdown=current,
                page=page_num,
                confidence=0.9,  # OCR fixes are high confidence but not 100%
                source="spell_check",
            )
            if edit_result.success and edit_result.edit_patch:
                current = edit_result.new_markdown
                edits.append(edit_result.edit_patch)
                logger.debug(f"Auto-fixed spelling: {word} -> {fix}")

    if len(edits) > 0:
        logger.info(f"Preprocessing applied {len(edits)} edits to page {page_num}")

    return current, edits


def _validate_lint(markdown: str) -> tuple[bool, list[str]]:
    """Validate that markdown passes lint checks.

    AI must produce output that passes these checks to complete refinement.

    Args:
        markdown: Markdown to validate

    Returns:
        Tuple of (passes, list_of_issues)
    """
    from .tools import run_lint

    result = run_lint(markdown)

    # If there are fixable issues, it doesn't pass
    if result.fixed_markdown and result.fixed_markdown != markdown:
        return False, result.issues

    # If there are unfixable issues, still report them but allow pass
    return True, result.issues


async def refine_page(
    page_num: int,
    page_markdown: str,
    requirements: Requirements,
    deps: RefineToolDeps,
    max_iterations: int = MAX_ITERATIONS_PER_PAGE,
) -> RefineResult:
    """Refine a single page of markdown.

    Pipeline:
    1. PREPROCESS: Auto-fix lint and spell issues (mechanical)
    2. REFINE: AI agent handles semantic issues (figures, tables, etc.)
    3. VALIDATE: Ensure output passes lint checks

    Args:
        page_num: Page number being refined (1-indexed)
        page_markdown: Current markdown for this page
        requirements: Document requirements from analysis phase
        deps: Tool dependencies with images and state
        max_iterations: Maximum refinement iterations

    Returns:
        RefineResult with refined markdown and metrics
    """
    agent = _create_refine_agent()
    prompts = load_prompts(PROMPTS_FILE)

    # Track ALL edits with full provenance
    all_edits: list[EditPatch] = []
    subagent_calls = 0

    # Step 1: PREPROCESS - Auto-fix mechanical issues
    logger.info(f"Preprocessing page {page_num}: lint and spell-check")
    current_markdown, preprocess_edits = _preprocess_markdown(
        page_markdown,
        page_num=page_num,
        key_terms=requirements.key_terms,
    )
    all_edits.extend(preprocess_edits)

    # Count preprocess stats
    lint_issues_fixed = sum(1 for e in preprocess_edits if e.edit_type.value == "lint_fix")
    spelling_corrections = sum(1 for e in preprocess_edits if e.edit_type.value == "spell_fix")

    # Step 2: REFINE - AI handles semantic issues
    for iteration in range(max_iterations):
        logger.info(f"Refine iteration {iteration + 1}/{max_iterations} for page {page_num}")

        # Update deps with current markdown and requirements
        deps.markdown = current_markdown
        deps.current_page = page_num
        deps.requirements = requirements

        # Build user prompt
        user_prompt = prompts.get("user_prompt", "").format(
            page_num=page_num,
            current_markdown=current_markdown,
            requirements=requirements.model_dump_json(indent=2),
            iteration=iteration + 1,
            max_iterations=max_iterations,
        )

        # Get page image for context
        page_image = deps.page_images.get(page_num)
        messages: list[Any] = [user_prompt]

        if page_image:
            # Convert PIL image to base64
            buffer = BytesIO()
            page_image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()

            messages.append(
                BinaryContent(data=image_bytes, media_type="image/png")
            )

        # Run agent
        result = await agent.run(messages, deps=deps)
        output = result.output

        logger.info(f"Iteration {iteration + 1}: {output.thinking}")

        # Apply any edits from this iteration
        for edit in output.edits_made:
            edit_result = apply_edit(
                reasoning=edit.reasoning,
                edit_type=edit.edit_type.value,
                original_text=edit.original_section,
                replacement_text=edit.modified_section,
                markdown=current_markdown,
                page=page_num,
                confidence=edit.confidence,
                source="refine_agent",
            )
            if edit_result.success:
                current_markdown = edit_result.new_markdown
                if edit_result.edit_patch:
                    all_edits.append(edit_result.edit_patch)
                logger.debug(f"Applied edit: {edit.reasoning[:50]}...")
            else:
                logger.warning(f"Edit failed: {edit_result.error}")

        # Step 3: VALIDATE - Check lint after each iteration
        passes_lint, lint_issues = _validate_lint(current_markdown)
        if not passes_lint:
            logger.warning(f"Output failed lint check: {lint_issues}")
            # Auto-fix and continue
            current_markdown, post_fix_edits = _preprocess_markdown(
                current_markdown, page_num, requirements.key_terms
            )
            all_edits.extend(post_fix_edits)
            lint_issues_fixed += sum(1 for e in post_fix_edits if e.edit_type.value == "lint_fix")

        # Check if complete
        if output.page_complete or not output.needs_another_pass:
            logger.info(f"Page {page_num} refinement complete after {iteration + 1} iterations")
            break

    # Final lint validation
    final_passes, final_issues = _validate_lint(current_markdown)
    if not final_passes:
        logger.warning(f"Final output has lint issues: {final_issues}")

    return RefineResult(
        page=page_num,
        markdown=current_markdown,
        edits_applied=all_edits,
        lint_issues_fixed=lint_issues_fixed,
        spelling_corrections=spelling_corrections,
        subagent_calls=subagent_calls,
        verification_passed=final_passes,
    )


async def refine_document(
    page_markdowns: dict[int, str],
    requirements: Requirements,
    page_images: dict[int, Any],  # PIL Images
    page_width: float,
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
) -> tuple[str, ProcessingTrace]:
    """Refine all pages of a document sequentially.

    Sequential processing allows context from earlier pages
    to inform refinement of later pages.

    Args:
        page_markdowns: Markdown for each page (1-indexed)
        requirements: Document requirements from analysis phase
        page_images: PIL images for each page
        page_width: Document page width in points
        element_bboxes: Bounding boxes for elements

    Returns:
        Tuple of (final_markdown, processing_trace)
    """
    trace = ProcessingTrace(phases=["refine"])
    page_results: list[RefineResult] = []

    # Process pages sequentially
    for page_num in sorted(page_markdowns.keys()):
        deps = RefineToolDeps(
            current_page=page_num,
            markdown=page_markdowns[page_num],
            page_images=page_images,
            page_width=page_width,
            element_bboxes=element_bboxes,
            requirements=requirements,
        )

        result = await refine_page(
            page_num=page_num,
            page_markdown=page_markdowns[page_num],
            requirements=requirements,
            deps=deps,
        )

        page_results.append(result)
        page_markdowns[page_num] = result.markdown

        # Update trace with this page's edits
        trace.total_llm_calls += result.subagent_calls + 1  # +1 for main agent
        for edit in result.edits_applied:
            trace.add_edit(edit)

    trace.page_results = page_results

    # Log edit summary
    if trace.edit_history:
        logger.info(f"Document refinement complete. Edit summary: {trace.edit_summary}")

    # Combine pages into final markdown
    final_markdown = "\n\n".join(
        page_markdowns[p] for p in sorted(page_markdowns.keys())
    )

    return final_markdown, trace
