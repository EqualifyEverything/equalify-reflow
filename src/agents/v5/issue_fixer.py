"""V5 Pipeline Issue Fixer - Agent-based error correction.

This module provides structured issue types and intelligent routing
to fix verification failures. Each issue type has a specialized fixer
that receives full context to make corrections.

Architecture:
    1. Verification produces StructuredIssue objects (not strings)
    2. IssueFixer routes each issue to appropriate handler
    3. Handlers apply fixes (deterministic or LLM-based)
    4. Results feed back for re-verification if needed

LLM-Based Fixers:
    - AltTextFixer: Generates alt-text for figures using page image
    - TableFixer: Transcribes tables from page image
    Each receives full context and validates output before applying.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

if TYPE_CHECKING:
    from PIL import Image

    from .models import DocumentPlan, FigureContext, TableContext

logger = logging.getLogger(__name__)


# =============================================================================
# Structured Issue Types
# =============================================================================


class IssueType(str, Enum):
    """Types of issues that can be detected and fixed."""

    WRONG_HEADING_LEVEL = "wrong_heading_level"
    MISSING_HEADING = "missing_heading"
    EXTRA_HEADING = "extra_heading"
    MISSING_ALT_TEXT = "missing_alt_text"
    UNFILLED_PLACEHOLDER = "unfilled_placeholder"
    TABLE_NOT_TRANSCRIBED = "table_not_transcribed"
    TABLE_MALFORMED = "table_malformed"
    SPELLING_ERROR = "spelling_error"
    FORMATTING_ERROR = "formatting_error"


class StructuredIssue(BaseModel):
    """A verification issue with full context for fixing.

    Unlike string issues, this contains all the information needed
    to apply a fix without re-analyzing the document.
    """

    issue_type: IssueType = Field(..., description="Type of issue")
    page: int = Field(..., description="Page number (1-indexed)")
    severity: str = Field(default="warning", description="critical, warning, info")

    # What was found vs expected
    actual_text: str = Field(default="", description="What's in the markdown")
    expected_text: str = Field(default="", description="What should be there")

    # For heading issues
    actual_level: int | None = Field(default=None)
    expected_level: int | None = Field(default=None)

    # For figure/table issues
    element_index: int | None = Field(default=None)

    # Context for LLM-based fixes
    surrounding_context: str = Field(default="", description="Text around the issue")

    # Pre-computed fix (if deterministic)
    suggested_fix: str | None = Field(
        default=None,
        description="Exact fix to apply (for deterministic issues)",
    )

    # Human-readable description
    description: str = Field(default="", description="Human-readable issue description")


@dataclass
class FixResult:
    """Result of attempting to fix an issue."""

    success: bool
    fixed_text: str | None = None
    error: str | None = None
    was_deterministic: bool = False
    generated_content: str | None = None  # The actual generated text (alt-text, table, etc.)


# =============================================================================
# Issue Detection (Enhanced Verification)
# =============================================================================


def detect_heading_issues(
    markdown: str,
    plan: DocumentPlan,
    page_num: int = 0,
) -> list[StructuredIssue]:
    """Detect heading issues and return structured issues with fix suggestions.

    Args:
        markdown: Markdown content to check
        plan: DocumentPlan with expected structure
        page_num: Page number (0 for combined markdown)

    Returns:
        List of StructuredIssue objects with pre-computed fixes
    """
    from .plan_verification import _extract_headings_from_markdown, _flatten_outline, _normalize_heading

    issues: list[StructuredIssue] = []

    # Get expected headings from plan
    expected_headings = _flatten_outline(plan.structure.outline)
    if not expected_headings:
        return issues

    # Build lookup: normalized text -> expected level
    expected_levels: dict[str, tuple[str, int]] = {}
    for text, level in expected_headings:
        normalized = _normalize_heading(text)
        expected_levels[normalized] = (text, level)

    # Extract actual headings
    actual_headings = _extract_headings_from_markdown(markdown)

    # Build lookup: normalized text -> (original text, actual level)
    actual_lookup: dict[str, tuple[str, int]] = {}
    for text, level in actual_headings:
        normalized = _normalize_heading(text)
        actual_lookup[normalized] = (text, level)

    # Check for wrong levels and missing headings
    for normalized, (expected_text, expected_level) in expected_levels.items():
        if normalized in actual_lookup:
            actual_text, actual_level = actual_lookup[normalized]
            if actual_level != expected_level:
                # Wrong level - create fix
                before_text = f"{'#' * actual_level} {actual_text}"
                after_text = f"{'#' * expected_level} {actual_text}"

                issues.append(
                    StructuredIssue(
                        issue_type=IssueType.WRONG_HEADING_LEVEL,
                        page=page_num,
                        severity="critical",
                        actual_text=before_text,
                        expected_text=after_text,
                        actual_level=actual_level,
                        expected_level=expected_level,
                        suggested_fix=after_text,
                        description=(
                            f"Heading '{actual_text}' is H{actual_level}, "
                            f"should be H{expected_level}"
                        ),
                    )
                )
        else:
            # Missing heading
            issues.append(
                StructuredIssue(
                    issue_type=IssueType.MISSING_HEADING,
                    page=page_num,
                    severity="warning",
                    expected_text=expected_text,
                    expected_level=expected_level,
                    description=f"Missing heading: '{expected_text}' (H{expected_level})",
                )
            )

    return issues


def detect_figure_issues(
    page_markdown: str,
    plan: DocumentPlan,
    page_num: int,
) -> list[StructuredIssue]:
    """Detect figure/alt-text issues.

    Args:
        page_markdown: Markdown for a single page
        plan: DocumentPlan with expected figures
        page_num: Page number

    Returns:
        List of StructuredIssue objects
    """
    issues: list[StructuredIssue] = []

    page_plan = plan.pages.get(page_num)
    if not page_plan or not page_plan.figures:
        return issues

    # Check for empty alt-text patterns (excluding decorative images)
    empty_alt_pattern = re.compile(r"!\[\]\([^)]+\)")
    placeholder_pattern = re.compile(r"<!--\s*image:?\d*\s*-->", re.IGNORECASE)

    for match in empty_alt_pattern.finditer(page_markdown):
        matched_text = match.group()
        # Skip decorative images - they SHOULD have empty alt text
        if "decorative" in matched_text.lower():
            continue
        issues.append(
            StructuredIssue(
                issue_type=IssueType.MISSING_ALT_TEXT,
                page=page_num,
                severity="critical",
                actual_text=matched_text,
                description="Figure has empty alt-text",
            )
        )

    for match in placeholder_pattern.finditer(page_markdown):
        issues.append(
            StructuredIssue(
                issue_type=IssueType.UNFILLED_PLACEHOLDER,
                page=page_num,
                severity="critical",
                actual_text=match.group(),
                description="Unfilled image placeholder",
            )
        )

    return issues


# =============================================================================
# Issue Fixers
# =============================================================================


def fix_heading_level(
    markdown: str,
    issue: StructuredIssue,
) -> FixResult:
    """Fix a heading level issue deterministically.

    Args:
        markdown: Current markdown content
        issue: The heading level issue to fix

    Returns:
        FixResult with success status and fixed text
    """
    if issue.issue_type != IssueType.WRONG_HEADING_LEVEL:
        return FixResult(success=False, error="Not a heading level issue")

    if not issue.actual_text or not issue.suggested_fix:
        return FixResult(success=False, error="Missing actual_text or suggested_fix")

    # Simple string replacement
    if issue.actual_text in markdown:
        fixed = markdown.replace(issue.actual_text, issue.suggested_fix, 1)
        logger.info(
            f"Fixed heading: '{issue.actual_text}' → '{issue.suggested_fix}'"
        )
        return FixResult(
            success=True,
            fixed_text=fixed,
            was_deterministic=True,
        )
    else:
        return FixResult(
            success=False,
            error=f"Could not find '{issue.actual_text}' in markdown",
        )


def fix_missing_heading(
    markdown: str,
    issue: StructuredIssue,
) -> FixResult:
    """Handle missing heading - usually can't fix automatically.

    Args:
        markdown: Current markdown content
        issue: The missing heading issue

    Returns:
        FixResult (usually failure - needs human intervention)
    """
    # Can't automatically add headings without knowing where
    return FixResult(
        success=False,
        error="Missing headings require manual intervention",
        was_deterministic=False,
    )


# =============================================================================
# LLM-Based Fixers
# =============================================================================


class ImageClassification(str, Enum):
    """Image classification for alt text strategy."""

    DECORATIVE = "decorative"  # No unique information, empty alt text
    SIMPLE = "simple"  # Essential info in <150 chars
    COMPLEX = "complex"  # Data/relationships requiring extended description


class AltTextResult(BaseModel):
    """Result from alt-text generation agent following accessibility best practices.

    Based on: https://dylanisa.ac/resources/alt-text-generation/
    """

    classification: ImageClassification = Field(
        ...,
        description="Image type: 'decorative' (no info), 'simple' (<150 chars), 'complex' (needs extended desc)",
    )
    alt_text: str = Field(
        ...,
        description="Concise alt text, MAX 150 CHARACTERS. Lead with meaning, not visual description.",
    )
    extended_description: str | None = Field(
        default=None,
        description="For COMPLEX images only: detailed description of data, relationships, or structure (1-3 sentences)",
    )
    reasoning: str = Field(
        ...,
        description="Why this classification? What information does the image convey?",
    )

    @property
    def is_decorative(self) -> bool:
        """Backwards compatibility."""
        return self.classification == ImageClassification.DECORATIVE


class TableResult(BaseModel):
    """Result from table transcription agent."""

    markdown_table: str = Field(
        ...,
        description="The table in markdown format with proper alignment",
    )
    row_count: int = Field(..., description="Number of rows (including header)")
    col_count: int = Field(..., description="Number of columns")
    caption: str = Field(default="", description="Table caption if visible")


def _get_model() -> BedrockConverseModel:
    """Get the model for LLM-based fixing (Haiku 4.5 for efficiency)."""
    return BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])


def _image_to_binary_content(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for the agent."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


# Alt-text generation system prompt (based on https://dylanisa.ac/resources/alt-text-generation/)
ALT_TEXT_SYSTEM_PROMPT = """You are an accessibility expert generating alt-text for figures in academic documents.

## Core Principle
Extract author intent from context. Ask: Why was this image placed here? What information would be lost without it?

## Classification (CHOOSE ONE)
1. **DECORATIVE**: Purely visual (dividers, backgrounds, decorative icons). Use empty alt text.
2. **SIMPLE**: Essential info can be conveyed in <150 characters. Most images fall here.
3. **COMPLEX**: Charts, diagrams, flowcharts with data/relationships. Needs extended description.

## Alt Text Rules (CRITICAL)
- **MAX 150 CHARACTERS** - Users cannot navigate within alt text
- **Lead with MEANING** - Prioritize function and significance over visual appearance
- **NEVER say** "image of", "picture of", "figure showing", "graphic of" (screen readers announce this)
- **Serve author intent** - Include only information the audience needs in THIS context

## Examples

BAD: "Image of a graph with blue and red bars showing different heights across quarters"
GOOD: "Quarterly sales up 40%, mobile revenue leading growth"

BAD: "Picture of a flowchart with boxes connected by arrows showing a process"
GOOD: "Document processing: PDF → Parse → AI Analysis → Markdown output"

BAD: "Figure showing the architecture of the system with multiple components"
GOOD: "Three-tier architecture: API layer, processing workers, Redis queue"

## For COMPLEX Images
Provide extended_description (1-3 sentences) with:
- Data values and trends
- Relationships between components
- Key takeaways the reader needs

## Process
1. Understand page context (what section is this in?)
2. Identify what information the image conveys
3. Classify the image
4. Write alt text that delivers the MEANING, not the appearance
5. For complex images, add extended description
"""

# Table transcription system prompt
TABLE_SYSTEM_PROMPT = """You are transcribing tables from PDF images into markdown format.

Your task: Create an accurate, accessible markdown table from the image.

Guidelines:
1. Preserve ALL data exactly as shown
2. Use proper markdown table alignment (| left | center | right |)
3. First row is the header, followed by separator (|---|---|---|)
4. Merge cells should be expanded (repeat content in each cell)
5. Empty cells should contain a single space or dash
6. Numbers should match exactly (don't round)
7. Include the caption if visible

Example output:
| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data 1   | Data 2   | Data 3   |
| More     | Values   | Here     |
"""

# Lazy-loaded agents (created on first use)
_alt_text_agent: Agent[None, AltTextResult] | None = None
_table_agent: Agent[None, TableResult] | None = None


def _get_alt_text_agent() -> Agent[None, AltTextResult]:
    """Get or create the alt-text generation agent."""
    global _alt_text_agent
    if _alt_text_agent is None:
        _alt_text_agent = Agent(
            model=_get_model(),
            output_type=AltTextResult,
            system_prompt=ALT_TEXT_SYSTEM_PROMPT,
        )
    return _alt_text_agent


def _get_table_agent() -> Agent[None, TableResult]:
    """Get or create the table transcription agent."""
    global _table_agent
    if _table_agent is None:
        _table_agent = Agent(
            model=_get_model(),
            output_type=TableResult,
            system_prompt=TABLE_SYSTEM_PROMPT,
        )
    return _table_agent


async def fix_missing_alt_text(
    markdown: str,
    issue: StructuredIssue,
    page_image: Image.Image,
    figure_context: FigureContext | None = None,
    max_retries: int = 2,
) -> FixResult:
    """Fix a missing alt-text issue using LLM with retry support.

    Args:
        markdown: Current markdown content
        issue: The alt-text issue to fix
        page_image: The page image for context
        figure_context: Optional context about the figure from the plan
        max_retries: Maximum number of retry attempts

    Returns:
        FixResult with success status and fixed text
    """
    if issue.issue_type != IssueType.MISSING_ALT_TEXT:
        return FixResult(success=False, error="Not an alt-text issue")

    if not issue.actual_text:
        return FixResult(success=False, error="No actual_text to replace")

    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            # Build context prompt - add feedback from previous attempt if any
            context_parts = ["Generate alt-text for this figure."]
            if figure_context:
                if figure_context.appears_to_be:
                    context_parts.append(f"Figure type: {figure_context.appears_to_be}")
                if figure_context.surrounding_text:
                    context_parts.append(f"Page context: {figure_context.surrounding_text[:300]}")
                if figure_context.location:
                    context_parts.append(f"Location: {figure_context.location}")

            # Add feedback from previous failure
            if last_error and attempt > 0:
                context_parts.append(
                    f"\nPrevious attempt failed: {last_error}. "
                    "Remember: alt_text must be MAX 150 CHARACTERS. Lead with meaning."
                )

            user_prompt = "\n".join(context_parts)

            # Run the agent with image as part of the prompt
            agent = _get_alt_text_agent()
            result = await agent.run(
                [user_prompt, _image_to_binary_content(page_image)],
            )

            alt_text_result = result.output

            # Validate the alt-text (150 CHARACTER limit, not words)
            if alt_text_result.classification != ImageClassification.DECORATIVE:
                char_count = len(alt_text_result.alt_text)
                if char_count < 10:
                    last_error = f"Alt-text too short ({char_count} chars)"
                    logger.warning(f"Alt-text validation failed: {last_error}")
                    continue
                if char_count > 200:  # Allow slight overflow, truncate if needed
                    logger.warning(f"Alt-text over limit ({char_count} chars), truncating")
                    alt_text_result.alt_text = alt_text_result.alt_text[:150]

            # Format output based on classification
            if alt_text_result.classification == ImageClassification.DECORATIVE:
                # Decorative images get empty alt-text
                new_text = '![](decorative "decorative image")'
                generated = "(decorative - no alt text needed)"
                logger.info(f"Figure marked as decorative on page {issue.page}")

            elif alt_text_result.classification == ImageClassification.COMPLEX:
                # Complex images: alt text + extended description
                alt = alt_text_result.alt_text
                ext = alt_text_result.extended_description or ""

                # Check if this is an HTML comment placeholder (<!-- image -->)
                if re.match(r"<!--\s*image", issue.actual_text, re.IGNORECASE):
                    # Format: **Figure:** alt text\n\nExtended description
                    if ext:
                        new_text = f"**Figure:** {alt}\n\n{ext}"
                    else:
                        new_text = f"**Figure:** {alt}"
                else:
                    # Markdown image format with extended description following
                    url_match = re.search(r"\!\[\]\(([^)]+)\)", issue.actual_text)
                    if url_match:
                        url = url_match.group(1)
                        if ext:
                            new_text = f"![{alt}]({url})\n\n{ext}"
                        else:
                            new_text = f"![{alt}]({url})"
                    else:
                        if ext:
                            new_text = f"**Figure:** {alt}\n\n{ext}"
                        else:
                            new_text = f"**Figure:** {alt}"

                generated = f"{alt}" + (f" | {ext[:100]}..." if ext else "")
                logger.info(f"Complex figure on page {issue.page}: {alt[:50]}...")

            else:
                # Simple informative images: just alt text
                alt = alt_text_result.alt_text

                if re.match(r"<!--\s*image", issue.actual_text, re.IGNORECASE):
                    new_text = f"**Figure:** {alt}"
                else:
                    url_match = re.search(r"\!\[\]\(([^)]+)\)", issue.actual_text)
                    if url_match:
                        url = url_match.group(1)
                        new_text = f"![{alt}]({url})"
                    else:
                        new_text = f"**Figure:** {alt}"

                generated = alt
                logger.info(f"Simple figure on page {issue.page}: {alt[:50]}...")

            # Apply the fix
            if issue.actual_text in markdown:
                fixed = markdown.replace(issue.actual_text, new_text, 1)
                return FixResult(
                    success=True,
                    fixed_text=fixed,
                    was_deterministic=False,
                    generated_content=generated,
                )
            else:
                return FixResult(
                    success=False,
                    error=f"Could not find '{issue.actual_text}' in markdown",
                )

        except Exception as e:
            last_error = str(e)
            logger.warning(f"Alt-text generation attempt {attempt + 1} failed: {e}")
            if attempt == max_retries:
                return FixResult(
                    success=False,
                    error=f"LLM error after {max_retries + 1} attempts: {e!s}",
                )

    return FixResult(
        success=False,
        error=f"Failed after {max_retries + 1} attempts: {last_error}",
    )


async def fix_table_not_transcribed(
    markdown: str,
    issue: StructuredIssue,
    page_image: Image.Image,
    table_context: TableContext | None = None,
    max_retries: int = 2,
) -> FixResult:
    """Fix a table that wasn't transcribed using LLM with retry support.

    Args:
        markdown: Current markdown content
        issue: The table issue to fix
        page_image: The page image for context
        table_context: Optional context about the table from the plan
        max_retries: Maximum number of retry attempts

    Returns:
        FixResult with success status and fixed text
    """
    if issue.issue_type not in (IssueType.TABLE_NOT_TRANSCRIBED, IssueType.TABLE_MALFORMED):
        return FixResult(success=False, error="Not a table issue")

    if not issue.actual_text:
        return FixResult(success=False, error="No actual_text to replace")

    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            # Build context prompt - add feedback from previous attempt if any
            context_parts = ["Transcribe this table from the image."]
            if table_context:
                if table_context.appears_to_be:
                    context_parts.append(f"Table type: {table_context.appears_to_be}")
                if table_context.caption:
                    context_parts.append(f"Caption: {table_context.caption}")
                if table_context.estimated_rows:
                    context_parts.append(f"Approx rows: {table_context.estimated_rows}")
                if table_context.estimated_cols:
                    context_parts.append(f"Approx cols: {table_context.estimated_cols}")

            # Add feedback from previous failure
            if last_error and attempt > 0:
                context_parts.append(
                    f"\nPrevious attempt failed: {last_error}. "
                    "Please ensure proper markdown table format with | separators."
                )

            user_prompt = "\n".join(context_parts)

            # Run the agent with image as part of the prompt
            agent = _get_table_agent()
            result = await agent.run(
                [user_prompt, _image_to_binary_content(page_image)],
            )

            table_result = result.output

            # Validate the table
            table_md = table_result.markdown_table.strip()
            if not table_md:
                last_error = "Empty table returned"
                logger.warning(f"Table validation failed: {last_error}")
                continue

            # Check for basic markdown table structure
            if "|" not in table_md:
                last_error = "No pipe separators in table"
                logger.warning(f"Table validation failed: {last_error}")
                continue

            # Check for separator row (|---|---|)
            if not re.search(r"\|[\s\-:]+\|", table_md):
                last_error = "No separator row found"
                logger.warning(f"Table validation failed: {last_error}")
                continue

            # Build the replacement text
            new_text = table_md
            if table_result.caption:
                new_text = f"**{table_result.caption}**\n\n{new_text}"

            # Apply the fix
            if issue.actual_text in markdown:
                fixed = markdown.replace(issue.actual_text, new_text, 1)
                logger.info(
                    f"Transcribed table on page {issue.page} (attempt {attempt + 1}): "
                    f"{table_result.row_count}x{table_result.col_count}"
                )
                return FixResult(
                    success=True,
                    fixed_text=fixed,
                    was_deterministic=False,
                    generated_content=table_md,  # Actual generated table
                )
            else:
                return FixResult(
                    success=False,
                    error=f"Could not find '{issue.actual_text}' in markdown",
                )

        except Exception as e:
            last_error = str(e)
            logger.warning(f"Table transcription attempt {attempt + 1} failed: {e}")
            if attempt == max_retries:
                return FixResult(
                    success=False,
                    error=f"LLM error after {max_retries + 1} attempts: {e!s}",
                )

    return FixResult(
        success=False,
        error=f"Failed after {max_retries + 1} attempts: {last_error}",
    )


# =============================================================================
# Issue Router / Dispatcher
# =============================================================================


class IssueFixer:
    """Routes issues to appropriate fixers and applies corrections.

    Supports both deterministic fixes (heading levels, placeholders) and
    LLM-based fixes (alt-text, tables) that require page images.
    """

    def __init__(
        self,
        plan: DocumentPlan,
        page_images: dict[int, Image.Image] | None = None,
    ):
        """Initialize with DocumentPlan and optional page images.

        Args:
            plan: The DocumentPlan with expected structure
            page_images: Optional dict mapping page number to PIL Image
        """
        self.plan = plan
        self.page_images = page_images or {}
        self.fixes_applied: list[str] = []
        self.fixes_failed: list[str] = []

    async def fix_all_async(
        self,
        markdown: str,
        issues: list[StructuredIssue],
        page_num: int,
    ) -> tuple[str, list[str], list[str]]:
        """Apply all possible fixes to the markdown (async for LLM fixes).

        Args:
            markdown: Current markdown content
            issues: List of structured issues to fix
            page_num: Page number for context lookup

        Returns:
            Tuple of (fixed_markdown, fixes_applied, fixes_failed)
        """
        current_markdown = markdown
        applied: list[str] = []
        failed: list[str] = []

        # Separate deterministic and LLM-based issues
        deterministic_issues: list[StructuredIssue] = []
        llm_issues: list[StructuredIssue] = []

        for issue in issues:
            if issue.issue_type in (
                IssueType.WRONG_HEADING_LEVEL,
                IssueType.MISSING_HEADING,
                IssueType.UNFILLED_PLACEHOLDER,
            ):
                deterministic_issues.append(issue)
            else:
                llm_issues.append(issue)

        # Apply deterministic fixes first (fast, no async needed)
        for issue in deterministic_issues:
            result = self._fix_deterministic(current_markdown, issue)
            if result.success and result.fixed_text:
                current_markdown = result.fixed_text
                applied.append(f"[{issue.issue_type.value}] {issue.description}")
            else:
                failed.append(
                    f"[{issue.issue_type.value}] {issue.description}: {result.error}"
                )

        # Apply LLM-based fixes (async)
        for issue in llm_issues:
            result = await self._fix_with_llm(current_markdown, issue, page_num)
            if result.success and result.fixed_text:
                current_markdown = result.fixed_text
                applied.append(f"[{issue.issue_type.value}] {issue.description}")
            else:
                failed.append(
                    f"[{issue.issue_type.value}] {issue.description}: {result.error}"
                )

        return current_markdown, applied, failed

    def fix_all(
        self,
        markdown: str,
        issues: list[StructuredIssue],
    ) -> tuple[str, list[str], list[str]]:
        """Apply deterministic fixes only (sync version for backward compat).

        Args:
            markdown: Current markdown content
            issues: List of structured issues to fix

        Returns:
            Tuple of (fixed_markdown, fixes_applied, fixes_failed)
        """
        current_markdown = markdown
        applied: list[str] = []
        failed: list[str] = []

        for issue in issues:
            result = self._fix_deterministic(current_markdown, issue)

            if result.success and result.fixed_text:
                current_markdown = result.fixed_text
                applied.append(f"[{issue.issue_type.value}] {issue.description}")
            else:
                failed.append(
                    f"[{issue.issue_type.value}] {issue.description}: {result.error}"
                )

        return current_markdown, applied, failed

    def _fix_deterministic(
        self,
        markdown: str,
        issue: StructuredIssue,
    ) -> FixResult:
        """Apply a deterministic fix (no LLM needed).

        Args:
            markdown: Current markdown content
            issue: The issue to fix

        Returns:
            FixResult from the appropriate fixer
        """
        if issue.issue_type == IssueType.WRONG_HEADING_LEVEL:
            return fix_heading_level(markdown, issue)

        if issue.issue_type == IssueType.MISSING_HEADING:
            return fix_missing_heading(markdown, issue)

        if issue.issue_type == IssueType.UNFILLED_PLACEHOLDER:
            if issue.actual_text and issue.actual_text in markdown:
                fixed = markdown.replace(issue.actual_text, "", 1)
                return FixResult(
                    success=True,
                    fixed_text=fixed,
                    was_deterministic=True,
                )
            return FixResult(success=False, error="Placeholder not found")

        # For LLM-based issues, indicate they need async handling
        if issue.issue_type in (
            IssueType.MISSING_ALT_TEXT,
            IssueType.TABLE_NOT_TRANSCRIBED,
            IssueType.TABLE_MALFORMED,
        ):
            return FixResult(
                success=False,
                error="Requires LLM-based fixing (use fix_all_async)",
                was_deterministic=False,
            )

        return FixResult(
            success=False,
            error=f"Unknown issue type: {issue.issue_type}",
        )

    async def _fix_with_llm(
        self,
        markdown: str,
        issue: StructuredIssue,
        page_num: int,
    ) -> FixResult:
        """Apply an LLM-based fix.

        Args:
            markdown: Current markdown content
            issue: The issue to fix
            page_num: Page number for image lookup

        Returns:
            FixResult from the LLM fixer
        """
        # Check if we have a page image
        page_image = self.page_images.get(page_num)
        if not page_image:
            return FixResult(
                success=False,
                error=f"No page image available for page {page_num}",
            )

        # Get context from plan
        page_plan = self.plan.pages.get(page_num)

        if issue.issue_type == IssueType.MISSING_ALT_TEXT:
            # Find figure context
            figure_context = None
            if page_plan and page_plan.figures:
                # Try to match by element_index or find first non-decorative
                if issue.element_index is not None:
                    for fig in page_plan.figures:
                        if fig.figure_index == issue.element_index:
                            figure_context = fig
                            break
                elif page_plan.figures:
                    figure_context = page_plan.figures[0]

            return await fix_missing_alt_text(
                markdown, issue, page_image, figure_context
            )

        if issue.issue_type in (
            IssueType.TABLE_NOT_TRANSCRIBED,
            IssueType.TABLE_MALFORMED,
        ):
            # Find table context
            table_context = None
            if page_plan and page_plan.tables:
                if issue.element_index is not None:
                    for tbl in page_plan.tables:
                        if tbl.table_index == issue.element_index:
                            table_context = tbl
                            break
                elif page_plan.tables:
                    table_context = page_plan.tables[0]

            return await fix_table_not_transcribed(
                markdown, issue, page_image, table_context
            )

        return FixResult(
            success=False,
            error=f"No LLM fixer for issue type: {issue.issue_type}",
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def detect_and_fix_issues(
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
) -> tuple[dict[int, str], list[str], list[str]]:
    """Detect issues in all pages and fix what we can (sync, deterministic only).

    This is the main entry point for the issue fixer system.
    For LLM-based fixes (alt-text, tables), use detect_and_fix_issues_async.

    Args:
        page_markdowns: Dict mapping page number to markdown
        plan: DocumentPlan with expected structure

    Returns:
        Tuple of (fixed_page_markdowns, all_fixes_applied, all_fixes_failed)
    """
    all_applied: list[str] = []
    all_failed: list[str] = []
    fixed_markdowns: dict[int, str] = {}

    fixer = IssueFixer(plan)

    for page_num, markdown in page_markdowns.items():
        # Detect issues for this page
        issues: list[StructuredIssue] = []

        # Heading issues (check combined for structure)
        heading_issues = detect_heading_issues(markdown, plan, page_num)
        issues.extend(heading_issues)

        # Figure issues
        figure_issues = detect_figure_issues(markdown, plan, page_num)
        issues.extend(figure_issues)

        # Apply fixes (sync - deterministic only)
        fixed_md, applied, failed = fixer.fix_all(markdown, issues)
        fixed_markdowns[page_num] = fixed_md

        for fix in applied:
            all_applied.append(f"Page {page_num}: {fix}")
        for fail in failed:
            all_failed.append(f"Page {page_num}: {fail}")

    if all_applied:
        logger.info(f"Issue fixer applied {len(all_applied)} fixes")
    if all_failed:
        logger.warning(f"Issue fixer failed on {len(all_failed)} issues")

    return fixed_markdowns, all_applied, all_failed


async def detect_and_fix_issues_async(
    page_markdowns: dict[int, str],
    plan: DocumentPlan,
    page_images: dict[int, Image.Image] | None = None,
) -> tuple[dict[int, str], list[str], list[str]]:
    """Detect issues in all pages and fix them (async, supports LLM fixes).

    This is the main entry point for the full issue fixer system.
    Supports both deterministic fixes (heading levels, placeholders) and
    LLM-based fixes (alt-text, tables) when page images are provided.

    Args:
        page_markdowns: Dict mapping page number to markdown
        plan: DocumentPlan with expected structure
        page_images: Optional dict mapping page number to PIL Image

    Returns:
        Tuple of (fixed_page_markdowns, all_fixes_applied, all_fixes_failed)
    """
    all_applied: list[str] = []
    all_failed: list[str] = []
    fixed_markdowns: dict[int, str] = {}

    fixer = IssueFixer(plan, page_images)

    for page_num, markdown in page_markdowns.items():
        # Detect issues for this page
        issues: list[StructuredIssue] = []

        # Heading issues (check combined for structure)
        heading_issues = detect_heading_issues(markdown, plan, page_num)
        issues.extend(heading_issues)

        # Figure issues
        figure_issues = detect_figure_issues(markdown, plan, page_num)
        issues.extend(figure_issues)

        # Table issues
        table_issues = detect_table_issues(markdown, plan, page_num)
        issues.extend(table_issues)

        # Apply fixes (async - supports LLM fixes)
        fixed_md, applied, failed = await fixer.fix_all_async(
            markdown, issues, page_num
        )
        fixed_markdowns[page_num] = fixed_md

        for fix in applied:
            all_applied.append(f"Page {page_num}: {fix}")
        for fail in failed:
            all_failed.append(f"Page {page_num}: {fail}")

    if all_applied:
        logger.info(f"Issue fixer applied {len(all_applied)} fixes")
    if all_failed:
        logger.warning(f"Issue fixer failed on {len(all_failed)} issues")

    return fixed_markdowns, all_applied, all_failed


def detect_table_issues(
    page_markdown: str,
    plan: DocumentPlan,
    page_num: int,
) -> list[StructuredIssue]:
    """Detect table transcription issues.

    Args:
        page_markdown: Markdown for a single page
        plan: DocumentPlan with expected tables
        page_num: Page number

    Returns:
        List of StructuredIssue objects
    """
    issues: list[StructuredIssue] = []

    page_plan = plan.pages.get(page_num)
    if not page_plan or not page_plan.tables:
        return issues

    # Check for table placeholders
    table_placeholder_patterns = [
        (r"\[TABLE\]", "TABLE placeholder"),
        (r"\[Table \d+\]", "Table N placeholder"),
        (r"<!--\s*table\s*-->", "Table comment placeholder"),
        (r"\[table transcription needed\]", "Transcription needed marker"),
    ]

    for pattern, desc in table_placeholder_patterns:
        for match in re.finditer(pattern, page_markdown, re.IGNORECASE):
            issues.append(
                StructuredIssue(
                    issue_type=IssueType.TABLE_NOT_TRANSCRIBED,
                    page=page_num,
                    severity="critical",
                    actual_text=match.group(),
                    description=f"Table not transcribed: {desc}",
                )
            )

    return issues


# =============================================================================
# Final Pass Agent - Pageless Document Optimization
# =============================================================================


class FinalPassResult(BaseModel):
    """Result from final pass document optimization."""

    optimized_markdown: str = Field(
        ...,
        description="The optimized markdown document for pageless reading",
    )
    changes_made: list[str] = Field(
        default_factory=list,
        description="List of changes made during optimization",
    )
    sections_merged: int = Field(
        default=0,
        description="Number of page breaks removed or sections merged",
    )


FINAL_PASS_SYSTEM_PROMPT = """You are a document optimization expert preparing academic documents for pageless digital reading.

## Your Task
Transform a page-separated markdown document into a smooth, continuous reading experience.

## Rules

### REMOVE Page Separators
- Remove ALL `---` horizontal rules that were used as page separators
- These artifacts of PDF pagination disrupt reading flow

### PRESERVE Document Structure
- Keep ALL headings (H1, H2, H3, etc.) exactly as they are
- Keep ALL figures, tables, and their descriptions
- Keep ALL content - do not summarize or remove anything
- Keep code blocks intact

### SMOOTH Transitions
- If a paragraph was split across pages, merge it into one paragraph
- Ensure sentences flow naturally (no orphaned fragments)
- Fix any mid-word breaks from hyphenation

### DO NOT
- Add new content or commentary
- Change heading levels
- Reorder sections
- Modify figure descriptions or alt text
- Alter table data
- Add introductory or concluding text

## Example

BEFORE:
```
## Introduction

This is the beginning of a paragraph that continues

---

on the next page with more content.

## Methods
```

AFTER:
```
## Introduction

This is the beginning of a paragraph that continues on the next page with more content.

## Methods
```

## Output
Return the complete optimized document. Every word of original content must be preserved.

## Changes Made (IMPORTANT)
For each change you make, add a clear description to changes_made list:
- "Removed page break between sections X and Y"
- "Merged split paragraph in section Z"
- "Fixed hyphenated word 'docu-ment' → 'document'"
- "Removed 8 page separators for continuous reading"

These descriptions will be shown to human reviewers, so be specific and clear.
"""

_final_pass_agent: Agent[None, FinalPassResult] | None = None


def _get_final_pass_agent() -> Agent[None, FinalPassResult]:
    """Get or create the final pass optimization agent."""
    global _final_pass_agent
    if _final_pass_agent is None:
        _final_pass_agent = Agent(
            model=_get_model(),
            output_type=FinalPassResult,
            system_prompt=FINAL_PASS_SYSTEM_PROMPT,
        )
    return _final_pass_agent


async def optimize_for_pageless_reading(
    markdown: str,
    document_title: str = "",
    max_retries: int = 1,
) -> tuple[str, list[str]]:
    """Optimize a document for pageless digital reading.

    This is the final pass after all other processing is complete.
    It removes page separators and smooths transitions.

    Args:
        markdown: The complete markdown document (all pages joined)
        document_title: Optional title for context
        max_retries: Number of retry attempts

    Returns:
        Tuple of (optimized_markdown, changes_made)
    """
    # Quick check - if no page separators, return as-is
    if "---" not in markdown:
        logger.info("No page separators found, skipping final pass")
        return markdown, []

    # Count separators for logging
    separator_count = markdown.count("\n---\n") + markdown.count("\n\n---\n\n")
    logger.info(f"Final pass: optimizing document with {separator_count} page separators")

    for attempt in range(max_retries + 1):
        try:
            context = f"Document: {document_title}\n\n" if document_title else ""
            user_prompt = f"{context}Optimize this document for pageless reading:\n\n{markdown}"

            agent = _get_final_pass_agent()
            result = await agent.run(user_prompt)

            final_result = result.output

            # Validate output preserves content
            original_words = len(markdown.split())
            optimized_words = len(final_result.optimized_markdown.split())

            # Allow some variance (merged paragraphs may have fewer line breaks)
            if optimized_words < original_words * 0.9:
                logger.warning(
                    f"Final pass lost content: {original_words} -> {optimized_words} words"
                )
                if attempt < max_retries:
                    continue
                # On final attempt, return original if too much was lost
                return markdown, ["Optimization skipped - content preservation failed"]

            logger.info(
                f"Final pass complete: {final_result.sections_merged} merges, "
                f"{len(final_result.changes_made)} changes"
            )

            return final_result.optimized_markdown, final_result.changes_made

        except Exception as e:
            logger.warning(f"Final pass attempt {attempt + 1} failed: {e}")
            if attempt == max_retries:
                logger.error(f"Final pass failed after {max_retries + 1} attempts: {e}")
                return markdown, [f"Optimization failed: {e}"]

    return markdown, ["Optimization failed after all retries"]


def remove_page_separators_simple(markdown: str) -> str:
    """Simple deterministic removal of page separators.

    Use this for a quick, deterministic approach without LLM.
    Does not attempt to merge split paragraphs.

    Args:
        markdown: The complete markdown document

    Returns:
        Markdown with page separators removed
    """
    # Remove various separator patterns
    result = re.sub(r"\n\n---\n\n", "\n\n", markdown)
    result = re.sub(r"\n---\n", "\n\n", result)

    # Clean up excessive blank lines (more than 2 in a row)
    result = re.sub(r"\n{4,}", "\n\n\n", result)

    return result
