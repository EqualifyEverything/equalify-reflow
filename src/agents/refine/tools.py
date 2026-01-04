"""Tool implementations for the unified refine agent.

These tools are called by the refine agent during the refinement loop.
Some are free (Python-only), others invoke subagents (LLM calls).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from PIL import Image

    from .models import Requirements

from src.utils.image_utils import BBox, crop_and_highlight

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Dependencies (passed via RunContext)
# =============================================================================


@dataclass
class RefineToolDeps:
    """Dependencies available to refine agent tools.

    Contains page images and current markdown state needed by tools.
    """

    # Current page being refined
    current_page: int

    # Full markdown document (all pages)
    markdown: str

    # Page images indexed by page number (1-indexed)
    page_images: dict[int, Image.Image]

    # Document page width in points (for bbox coordinate scaling)
    page_width: float

    # Extracted images with bboxes, indexed by (page, element_type, index)
    element_bboxes: dict[tuple[int, str, int], BBox]

    # Document requirements with hotspots (set by refine_page)
    requirements: "Requirements | None" = None


# =============================================================================
# Tool Return Types
# =============================================================================


class EditResult(BaseModel):
    """Result from apply_edit tool."""

    success: bool = Field(..., description="Whether edit was applied successfully")
    new_markdown: str = Field(default="", description="Updated markdown if successful")
    error: str | None = Field(default=None, description="Error message if failed")
    edit_patch: "EditPatch | None" = Field(
        default=None, description="The edit that was applied (for history tracking)"
    )


# =============================================================================
# Simple Composable Editing Tools (v3 correction pipeline)
# =============================================================================


class SectionRead(BaseModel):
    """Result from reading a section of markdown."""

    content: str = Field(..., description="The requested lines")
    start_line: int = Field(..., description="Start line number (1-indexed)")
    end_line: int = Field(..., description="End line number (1-indexed)")
    total_lines: int = Field(..., description="Total lines in the document")


class FindReplaceResult(BaseModel):
    """Result from a find and replace operation."""

    success: bool = Field(..., description="Whether the replacement was successful")
    error: str | None = Field(
        default=None, description="Error message: 'not found' or 'not unique - found N occurrences'"
    )
    new_markdown: str | None = Field(
        default=None, description="Updated markdown if successful"
    )
    affected_line: int | None = Field(
        default=None, description="Line number where edit was made (1-indexed)"
    )


class LintResult(BaseModel):
    """Result from run_lint tool."""

    issues_found: int = Field(default=0, description="Number of lint issues found")
    issues: list[str] = Field(default_factory=list, description="List of issue descriptions")
    fixed_markdown: str | None = Field(
        default=None, description="Auto-fixed markdown if fixable issues found"
    )


class URLFixResult(BaseModel):
    """Result from fix_broken_urls tool."""

    urls_fixed: int = Field(default=0, description="Number of URLs fixed")
    fixes: list[dict[str, str]] = Field(
        default_factory=list, description="List of {original, fixed} URL pairs"
    )
    fixed_markdown: str | None = Field(
        default=None, description="Markdown with fixed URLs if any were found"
    )


class SpellCheckResult(BaseModel):
    """Result from spell_check tool."""

    errors_found: int = Field(default=0, description="Number of spelling errors found")
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of {word, suggestion} for each error",
    )


class FigureDescription(BaseModel):
    """Result from describe_figure tool (from subagent)."""

    alt_text: str = Field(..., description="Generated alt-text for the figure")
    figure_type: str = Field(
        default="image",
        description="Type of figure (chart, diagram, photo, etc.)",
    )
    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Confidence in description"
    )


class FigureDescriptionResult(BaseModel):
    """Result from describing a figure (simplified, does not apply edits)."""

    figure_type: Literal["decorative", "informative", "complex"] = Field(
        description="Type of figure: decorative (no alt needed), informative (needs alt), complex (needs long description)"
    )
    alt_text: str = Field(
        default="",
        description="Alt text for the figure. Empty string for decorative images.",
    )
    long_description: str | None = Field(
        default=None,
        description="Extended description for complex figures",
    )
    suggested_markdown: str = Field(
        description="The markdown to use, e.g., '![](image)' or '![Alt text](image)'",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in the description",
    )


class TableTranscription(BaseModel):
    """Result from describe_table tool (from subagent)."""

    markdown_table: str = Field(..., description="Markdown table transcription")
    row_count: int = Field(default=0, description="Number of rows")
    col_count: int = Field(default=0, description="Number of columns")
    has_header: bool = Field(default=True, description="Whether table has header row")
    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Confidence in transcription"
    )


class TableDescriptionResult(BaseModel):
    """Result from transcribing a table (info only, no edits applied)."""

    markdown_table: str = Field(
        ..., description="The table in markdown format"
    )
    row_count: int = Field(
        ..., description="Number of rows in the table"
    )
    col_count: int = Field(
        ..., description="Number of columns in the table"
    )
    has_header: bool = Field(
        default=True,
        description="Whether the table has a header row",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in the transcription",
    )


class VerificationResult(BaseModel):
    """Result from check_against_image tool."""

    matches: bool = Field(..., description="Whether markdown matches image content")
    issues: list[str] = Field(
        default_factory=list, description="List of discrepancies found"
    )
    suggested_fixes: list[str] = Field(
        default_factory=list, description="Suggested corrections"
    )


# =============================================================================
# Simple Composable Editing Functions (v3 correction pipeline)
# =============================================================================
# These are pure functions, not PydanticAI tools - they'll be wrapped later.
# Pattern: Simple tools that do one thing well, similar to Claude Code's Edit tool.


def read_section(markdown: str, start_line: int, end_line: int) -> SectionRead:
    """Read specific lines from markdown (1-indexed, inclusive).

    A simple tool for reading portions of a markdown document.
    Line numbers are 1-indexed and inclusive on both ends.

    Args:
        markdown: The full markdown document
        start_line: First line to read (1-indexed)
        end_line: Last line to read (1-indexed, inclusive)

    Returns:
        SectionRead with the requested content and metadata
    """
    lines = markdown.split("\n")
    total = len(lines)

    # Clamp to valid range
    start = max(1, start_line) - 1  # Convert to 0-indexed
    end = min(total, end_line)

    content = "\n".join(lines[start:end])

    return SectionRead(
        content=content,
        start_line=start_line,
        end_line=end_line,
        total_lines=total,
    )


def find_and_replace(
    markdown: str,
    original: str,
    replacement: str,
    reasoning: str,
) -> FindReplaceResult:
    """Replace exact text in markdown. Fails if not found or not unique.

    A simple find-and-replace tool modeled after Claude Code's Edit tool.
    Requires the original text to be unique in the document.

    Args:
        markdown: The full markdown document
        original: Exact text to find and replace
        replacement: Text to replace it with
        reasoning: Why this edit is being made (for logging/audit)

    Returns:
        FindReplaceResult with success status and updated markdown
    """
    count = markdown.count(original)

    if count == 0:
        return FindReplaceResult(success=False, error="not found")

    if count > 1:
        return FindReplaceResult(
            success=False, error=f"not unique - found {count} occurrences"
        )

    # Find line number (1-indexed)
    lines = markdown.split("\n")
    affected_line = None
    for i, line in enumerate(lines, 1):
        if original in line:
            affected_line = i
            break

    new_markdown = markdown.replace(original, replacement, 1)

    logger.debug(f"find_and_replace: {reasoning[:50]}... (line {affected_line})")

    return FindReplaceResult(
        success=True,
        new_markdown=new_markdown,
        affected_line=affected_line,
    )


def create_correction_from_edit(
    page: int,
    edit_type: str,
    original: str,
    replacement: str,
    reasoning: str,
    confidence: float = 1.0,
    source: str = "execution_agent",
) -> "Correction":
    """Create a Correction record from an edit operation.

    Bridges the simple editing tools to the correction tracking system.
    Used to record edits made by the execution agent for audit/review.

    Args:
        page: Page number this edit applies to (1-indexed)
        edit_type: Category of edit (e.g., 'ocr_fix', 'formatting', 'alt_text')
        original: Original text that was replaced
        replacement: New text that replaced it
        reasoning: Why this edit was made
        confidence: Confidence in this edit (0.0-1.0)
        source: What produced this edit (default: execution_agent)

    Returns:
        Correction model instance for tracking
    """
    from src.agents.refine.models import Correction

    return Correction(
        page=page,
        edit_type=edit_type,
        original=original,
        corrected=replacement,
        reasoning=reasoning,
        confidence=confidence,
        source=source,
    )


# =============================================================================
# Free Tools (Python-only, no LLM calls)
# =============================================================================


def apply_edit(
    reasoning: str,
    edit_type: str,
    original_text: str,
    replacement_text: str,
    markdown: str,
    page: int = 1,
    confidence: float = 1.0,
    source: str = "refine_agent",
) -> EditResult:
    """Apply a text replacement edit to the markdown with full provenance.

    This is a simple search-and-replace. For more complex edits,
    the agent should break them into multiple calls.

    Args:
        reasoning: Why this edit is being made (FIRST - most important)
        edit_type: Category of edit (lint_fix, spell_fix, figure_alt, etc.)
        original_text: Exact text to find and replace
        replacement_text: Text to replace it with
        markdown: Current markdown document
        page: Page number this edit applies to
        confidence: Confidence in this edit (0.0-1.0)
        source: What produced this edit (agent, lint, spell, subagent)

    Returns:
        EditResult with success status, updated markdown, and edit patch for history
    """
    from .models import EditPatch, EditType

    if original_text not in markdown:
        return EditResult(
            success=False,
            error=f"Original text not found in markdown: '{original_text[:50]}...'",
        )

    # Check for uniqueness
    count = markdown.count(original_text)
    if count > 1:
        return EditResult(
            success=False,
            error=f"Original text appears {count} times. Provide more context for uniqueness.",
        )

    new_markdown = markdown.replace(original_text, replacement_text, 1)

    # Create edit patch for history
    try:
        edit_type_enum = EditType(edit_type)
    except ValueError:
        edit_type_enum = EditType.CONTENT_FIX

    edit_patch = EditPatch(
        reasoning=reasoning,
        edit_type=edit_type_enum,
        original_section=original_text,
        modified_section=replacement_text,
        page=page,
        confidence=confidence,
        source=source,
    )

    logger.debug(
        f"Applied edit [{edit_type}]: {reasoning[:50]}... "
        f"(replaced {len(original_text)} chars with {len(replacement_text)} chars)"
    )

    return EditResult(success=True, new_markdown=new_markdown, edit_patch=edit_patch)


def run_lint(markdown: str) -> LintResult:
    """Run markdown linting on the document.

    Uses mdformat for auto-fixing and returns any remaining issues.

    Args:
        markdown: Markdown document to lint

    Returns:
        LintResult with issues and optionally fixed markdown
    """
    issues: list[str] = []

    # Basic checks (can be expanded)
    lines = markdown.split("\n")

    for i, line in enumerate(lines, 1):
        # Check for trailing whitespace
        if line.rstrip() != line:
            issues.append(f"Line {i}: trailing whitespace")

        # Check for multiple consecutive blank lines
        if i > 1 and line.strip() == "" and lines[i - 2].strip() == "":
            if i > 2 and lines[i - 3].strip() == "":
                issues.append(f"Line {i}: multiple consecutive blank lines")

        # Check for heading without space after #
        if line.startswith("#") and not line.startswith("# ") and len(line) > 1:
            if line[1] != "#":  # Not a multi-# heading
                issues.append(f"Line {i}: heading missing space after #")

    # Try mdformat if available (installed in container)
    fixed_markdown = None
    try:
        result = subprocess.run(
            ["mdformat", "-"],
            input=markdown,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout != markdown:
            fixed_markdown = result.stdout
            logger.debug("mdformat applied fixes to markdown")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("mdformat not available, skipping auto-fix")

    return LintResult(
        issues_found=len(issues),
        issues=issues,
        fixed_markdown=fixed_markdown,
    )


def fix_broken_urls(markdown: str) -> URLFixResult:
    """Fix URLs with internal spacing from OCR errors.

    Common OCR errors in URLs:
    - Space after protocol: "https:// github.com"
    - Space in path: "github.com/ IBM/data"

    Args:
        markdown: Markdown document to fix

    Returns:
        URLFixResult with fixed URLs and updated markdown
    """
    import re

    fixes: list[dict[str, str]] = []
    result = markdown

    patterns = [
        # Space after protocol: "https:// github.com" -> "https://github.com"
        (re.compile(r"(https?://)\s+(\S+)"), r"\1\2"),
        # Space in path (not before paren to avoid breaking markdown links): "github.com/ IBM" -> "github.com/IBM"
        (re.compile(r"(https?://[^\s]+)/\s+(?!\()([^\s\)\]]+)"), r"\1/\2"),
    ]

    for pattern, replacement in patterns:
        for match in pattern.finditer(result):
            original = match.group(0)
            fixed = pattern.sub(replacement, original)
            if original != fixed:
                fixes.append({"original": original, "fixed": fixed})
        result = pattern.sub(replacement, result)

    return URLFixResult(
        urls_fixed=len(fixes),
        fixes=fixes,
        fixed_markdown=result if fixes else None,
    )


# Module-level cached spell checker (initialized once to avoid 40s delay per call)
_spell_checker: "SpellChecker | None" = None


def _get_spell_checker() -> "SpellChecker":
    """Get or create the cached spell checker instance."""
    global _spell_checker
    if _spell_checker is None:
        from spellchecker import SpellChecker
        logger.info("Initializing SpellChecker (one-time, ~40s for dictionary load)...")
        _spell_checker = SpellChecker()
        logger.info("SpellChecker initialized")
    return _spell_checker


def spell_check(markdown: str, key_terms: list[str] | None = None) -> SpellCheckResult:
    """Lint markdown for potential spelling issues using dictionary validation.

    Acts as a LINTER - surfaces words not found in dictionary for LLM review.
    The LLM agent decides in context whether each flagged word is:
    - An actual typo/OCR error that needs fixing
    - A proper noun (product names, people, places)
    - A domain-specific technical term
    - Acceptable as-is

    Does NOT auto-fix anything. Returns flagged words with possible corrections
    for the agent to evaluate.

    Args:
        markdown: Markdown document to check
        key_terms: Domain-specific terms to add to dictionary whitelist

    Returns:
        SpellCheckResult with flagged words and suggested corrections
    """
    import re

    suggestions: list[dict[str, str]] = []

    # Use cached spell checker (avoids 40s init per call)
    spell = _get_spell_checker()

    # Build key_terms lookup for better suggestions
    key_terms_lower: dict[str, str] = {}
    if key_terms:
        for term in key_terms:
            key_terms_lower[term.lower()] = term  # Preserve original case

    # Extract words (3+ chars, skip very short words)
    words = re.findall(r"\b[a-zA-Z]{3,}\b", markdown)

    # Track already flagged to avoid duplicates
    already_flagged: set[str] = set()

    for word in words:
        word_lower = word.lower()

        # Skip if already flagged
        if word_lower in already_flagged:
            continue

        # Skip if in dictionary OR in key_terms (known good words)
        if word_lower in spell or word_lower in key_terms_lower:
            continue

        # Word not in dictionary - flag it for LLM review
        # First check if a key_term is a close match (e.g., "Doclign" -> "Docling")
        suggestion = ""
        for term_lower, term_original in key_terms_lower.items():
            # Simple edit distance check: if word is 1-2 chars different from key_term
            if _is_close_match(word_lower, term_lower):
                suggestion = term_original
                break

        # Fall back to spell checker's suggestion if no key_term match
        if not suggestion:
            spell_suggestion = spell.correction(word_lower)
            if spell_suggestion and spell_suggestion != word_lower:
                # Preserve original case in suggestion
                if word.isupper():
                    suggestion = spell_suggestion.upper()
                elif word[0].isupper():
                    suggestion = spell_suggestion.capitalize()
                else:
                    suggestion = spell_suggestion

        suggestions.append({
            "word": word,
            "suggestion": suggestion,  # Hint for LLM, may be empty
        })
        already_flagged.add(word_lower)

    return SpellCheckResult(
        errors_found=len(suggestions),
        suggestions=suggestions,
    )


def _is_close_match(word: str, term: str, max_distance: int = 2) -> bool:
    """Check if word is within max_distance edits of term.

    Uses simple length-based heuristic + character overlap for speed.
    Not a full Levenshtein but good enough for typo detection.
    """
    # Length must be similar
    if abs(len(word) - len(term)) > max_distance:
        return False

    # Count character differences
    differences = 0
    for i, (c1, c2) in enumerate(zip(word, term)):
        if c1 != c2:
            differences += 1
            if differences > max_distance:
                return False

    # Account for length difference
    differences += abs(len(word) - len(term))
    return differences <= max_distance


# =============================================================================
# Subagent Tools (invoke LLM for specialized tasks)
# =============================================================================
# These are stubs that will be implemented in subagents.py


async def describe_figure(
    page: int,
    figure_index: int,
    deps: RefineToolDeps,
) -> FigureDescription:
    """Get alt-text description for a figure from subagent.

    The subagent receives:
    1. Cropped image of just the figure
    2. Full page image with red highlight box around the figure

    Args:
        page: Page number (1-indexed)
        figure_index: Index of figure on that page (1-indexed)
        deps: Tool dependencies with page images and bboxes

    Returns:
        FigureDescription with alt-text and metadata
    """
    from .subagents import get_figure_description

    # Get bbox for this figure
    bbox_key = (page, "figure", figure_index)
    bbox = deps.element_bboxes.get(bbox_key)

    if bbox is None:
        logger.warning(f"No bbox found for figure {figure_index} on page {page}")
        return FigureDescription(
            alt_text="[Figure description unavailable - no bounding box]",
            confidence=0.0,
        )

    page_image = deps.page_images.get(page)
    if page_image is None:
        logger.warning(f"No page image for page {page}")
        return FigureDescription(
            alt_text="[Figure description unavailable - no page image]",
            confidence=0.0,
        )

    # Prepare images for subagent
    cropped, highlighted = crop_and_highlight(
        page_image, bbox, deps.page_width
    )

    logger.info(f"Calling figure subagent for page {page}, figure {figure_index}")

    # Call the figure subagent
    return await get_figure_description(
        cropped_image=cropped,
        highlighted_page=highlighted,
        page_num=page,
        caption="",  # TODO: pass caption if available
    )


async def describe_table(
    page: int,
    table_index: int,
    deps: RefineToolDeps,
) -> TableTranscription:
    """Get markdown table transcription from subagent.

    The subagent receives:
    1. Cropped image of just the table
    2. Full page image with red highlight box around the table

    Args:
        page: Page number (1-indexed)
        table_index: Index of table on that page (1-indexed)
        deps: Tool dependencies with page images and bboxes

    Returns:
        TableTranscription with markdown table and metadata
    """
    from .subagents import get_table_transcription

    # Get bbox for this table
    bbox_key = (page, "table", table_index)
    bbox = deps.element_bboxes.get(bbox_key)

    if bbox is None:
        logger.warning(f"No bbox found for table {table_index} on page {page}")
        return TableTranscription(
            markdown_table="| Error | No bounding box available |",
            confidence=0.0,
        )

    page_image = deps.page_images.get(page)
    if page_image is None:
        logger.warning(f"No page image for page {page}")
        return TableTranscription(
            markdown_table="| Error | No page image available |",
            confidence=0.0,
        )

    # Prepare images for subagent
    cropped, highlighted = crop_and_highlight(
        page_image, bbox, deps.page_width
    )

    logger.info(f"Calling table subagent for page {page}, table {table_index}")

    # Call the table subagent
    return await get_table_transcription(
        cropped_image=cropped,
        highlighted_page=highlighted,
        page_num=page,
        caption="",  # TODO: pass caption if available
    )


async def check_against_image(
    section_markdown: str,
    page: int,
    bbox: BBox | None,
    deps: RefineToolDeps,
) -> VerificationResult:
    """Verify that markdown section matches the source image.

    The subagent compares the markdown against the image and
    reports any discrepancies.

    Args:
        section_markdown: Markdown text to verify
        page: Page number (1-indexed)
        bbox: Optional bounding box to focus on (otherwise full page)
        deps: Tool dependencies with page images

    Returns:
        VerificationResult with match status and issues
    """
    from .subagents import verify_against_image

    page_image = deps.page_images.get(page)
    if page_image is None:
        logger.warning(f"No page image for page {page}")
        return VerificationResult(
            matches=False,
            issues=["No page image available for verification"],
        )

    # If bbox provided, prepare cropped and highlighted views
    cropped = None
    highlighted = None
    if bbox:
        cropped, highlighted = crop_and_highlight(
            page_image, bbox, deps.page_width
        )

    logger.info(f"Calling verification subagent for page {page}")

    # Call the verification subagent
    return await verify_against_image(
        section_markdown=section_markdown,
        page_image=page_image,
        page_num=page,
        cropped_image=cropped,
        highlighted_page=highlighted,
    )


# =============================================================================
# Simplified Info-Only Functions (return info, do not apply edits)
# =============================================================================


async def describe_table_from_image(
    cropped_image: "Image.Image",
    highlighted_page: "Image.Image | None" = None,
    page_num: int = 1,
) -> TableDescriptionResult:
    """Transcribe a table and return structured info (does not apply edits).

    Uses the existing table transcription subagent to analyze the image
    and convert it to markdown table format.

    This is a simplified wrapper that:
    1. Takes a cropped table image directly (no deps or bbox lookup)
    2. Calls the table subagent
    3. Returns structured result with markdown and dimensions
    4. Does NOT apply any edits to any document

    Args:
        cropped_image: PIL Image of the cropped table
        highlighted_page: Optional full page image with table highlighted
        page_num: Page number for context (default 1)

    Returns:
        TableDescriptionResult with markdown_table, dimensions, and confidence
    """
    from .subagents import get_table_transcription

    # If no highlighted page provided, use cropped image as both
    if highlighted_page is None:
        highlighted_page = cropped_image

    logger.info(f"describe_table_from_image: transcribing table from page {page_num}")

    # Call the existing table subagent
    transcription = await get_table_transcription(
        cropped_image=cropped_image,
        highlighted_page=highlighted_page,
        page_num=page_num,
        caption="",
    )

    # Convert TableTranscription to TableDescriptionResult
    return TableDescriptionResult(
        markdown_table=transcription.markdown_table,
        row_count=transcription.row_count,
        col_count=transcription.col_count,
        has_header=transcription.has_header,
        confidence=transcription.confidence,
    )


async def describe_figure_from_image(
    cropped_image: "Image.Image",
    highlighted_page: "Image.Image | None" = None,
    page_num: int = 1,
) -> FigureDescriptionResult:
    """Describe a figure and return structured info (does not apply edits).

    Uses the existing figure description subagent to analyze the image
    and determine if it's decorative, informative, or complex.

    This is a simplified wrapper that:
    1. Calls the figure description subagent
    2. Classifies the figure type based on the response
    3. Returns structured info for the caller to use

    Args:
        cropped_image: Cropped image of just the figure
        highlighted_page: Optional full page with red box around figure (for context)
        page_num: Page number for context (default: 1)

    Returns:
        FigureDescriptionResult with type, alt_text, and suggested_markdown
    """
    from .subagents import get_figure_description

    # If no highlighted page provided, use the cropped image as both
    if highlighted_page is None:
        highlighted_page = cropped_image

    logger.info(f"describe_figure_from_image: analyzing figure from page {page_num}")

    try:
        # Call the existing figure subagent
        description = await get_figure_description(
            cropped_image=cropped_image,
            highlighted_page=highlighted_page,
            page_num=page_num,
            caption="",
        )

        # Classify the figure type based on the description
        alt_text = description.alt_text.strip()
        figure_type_raw = description.figure_type.lower()
        confidence = description.confidence

        # Determine the figure classification
        # Decorative: empty alt, or explicitly marked as decorative
        if (
            not alt_text
            or alt_text.lower() in ("decorative", "decoration", "decorative image")
            or "decorative" in figure_type_raw
        ):
            return FigureDescriptionResult(
                figure_type="decorative",
                alt_text="",
                long_description=None,
                suggested_markdown="![](image)",
                confidence=confidence,
            )

        # Complex: detailed descriptions (charts, graphs, diagrams with data)
        # Heuristic: long alt text or specific figure types
        is_complex = (
            len(alt_text) > 200
            or figure_type_raw in ("chart", "graph", "diagram", "flowchart", "data visualization")
            or any(term in figure_type_raw for term in ("chart", "graph", "diagram", "flow"))
        )

        if is_complex:
            # For complex figures, use alt_text as the main alt and provide long_description
            # Keep alt concise, put detail in long_description
            if len(alt_text) > 125:
                # Split into concise alt and detailed description
                concise_alt = alt_text[:120].rsplit(" ", 1)[0] + "..."
                long_desc = alt_text
            else:
                concise_alt = alt_text
                long_desc = alt_text

            # Escape quotes in alt text for markdown
            safe_alt = concise_alt.replace('"', "'")

            return FigureDescriptionResult(
                figure_type="complex",
                alt_text=safe_alt,
                long_description=long_desc,
                suggested_markdown=f"![{safe_alt}](image)",
                confidence=confidence,
            )

        # Informative: standard images with alt text
        safe_alt = alt_text.replace('"', "'")

        return FigureDescriptionResult(
            figure_type="informative",
            alt_text=safe_alt,
            long_description=None,
            suggested_markdown=f"![{safe_alt}](image)",
            confidence=confidence,
        )

    except Exception as e:
        logger.error(f"describe_figure_from_image failed for page {page_num}: {e}")
        # Return a safe default on error
        return FigureDescriptionResult(
            figure_type="informative",
            alt_text="[Figure description unavailable]",
            long_description=None,
            suggested_markdown="![[Figure description unavailable]](image)",
            confidence=0.0,
        )
