"""Experimental endpoint v3: Clean pipeline with page-by-page correction.

Pipeline:
    PDF IN → Docling → Analyze → Correction (loop) → Output

This implements a simplified architecture where:
1. Docling extracts semantics, page images, and initial markdown
2. Analyze phase extracts layout metadata, finds hotspots, creates validation criteria
3. Correction phase processes page-by-page with accumulated context
4. All corrections are recorded for human review

V4 Pipeline (new):
    PDF IN → Docling → Analysis → Execution → Verification → Output

The v4 pipeline uses three specialized agents:
1. Analysis Agent: Detects issues (figures needing alt-text, heading problems, etc.)
2. Execution Agent: Applies fixes using simple tools (describe_figure, find_and_replace)
3. Verification Agent: "Fresh eyes" check that markdown matches the page image

WARNING: Skips PII scanning - for internal testing only.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
from ..agents.refine.models import EditPatch, EditType
from ..agents.refine.models import Correction as RefineCorrection
from ..agents.refine.tools import (
    FigureDescription,
    TableTranscription,
    apply_edit,
    fix_broken_urls,
    run_lint,
    spell_check,
)
from ..agents.refine.subagents import (
    get_figure_description,
    get_table_transcription,
)
# V4 Pipeline Agents
from ..agents.refine import (
    analyze_page,
    AnalysisResult,
    AnalysisUsage,
    PageIssue,
    execute_corrections,
    ExecutionResult,
    ExecutionUsage,
    verify_page,
    VerificationResult,
    VerificationUsage,
    VerificationIssue,
)
from ..services.pdf_converter import PDFConverter, PDFConversionResult, PAGE_BREAK_MARKER
from ..utils.image_utils import crop_and_highlight

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments/v3", tags=["Experiments V3"])


# =============================================================================
# Models
# =============================================================================


class Hotspot(BaseModel):
    """Element requiring special attention during correction."""

    page: int = Field(..., description="Page number (1-indexed)")
    element_type: str = Field(..., description="figure, table, equation, code")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box (l, t, r, b) if available"
    )
    description: str = Field(default="", description="Brief description")
    tip: str = Field(default="", description="Processing tip for this element")


class HeadingInfo(BaseModel):
    """Tracked heading for document structure."""

    level: int = Field(..., ge=1, le=6, description="Heading level (1-6)")
    text: str = Field(..., description="Heading text")
    page: int = Field(..., description="Page where heading appears")


class DocumentContext(BaseModel):
    """Context extracted during Analyze phase, passed to Correction phase."""

    # Document classification
    document_type: str = Field(default="document", description="e.g., syllabus, lecture notes")
    document_type_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Layout metadata
    has_multi_column: bool = Field(default=False, description="Multi-column layout detected")
    has_headers_footers: bool = Field(default=False, description="Headers/footers detected")
    primary_language: str = Field(default="en", description="Primary language")

    # Heading structure (validation criteria)
    expected_heading_hierarchy: list[str] = Field(
        default_factory=list,
        description="Expected heading structure, e.g., ['Title', 'Section', 'Subsection']",
    )
    detected_headings: list[HeadingInfo] = Field(
        default_factory=list, description="Headings detected during analysis"
    )

    # Hotspots for special attention
    hotspots: list[Hotspot] = Field(
        default_factory=list, description="Elements needing special processing"
    )

    # Domain context
    key_terms: list[str] = Field(
        default_factory=list, description="Domain terms for spell-check whitelist"
    )

    # Style conventions
    footnote_style: str = Field(default="none", description="Footnote style detected")
    citation_style: str = Field(default="unknown", description="Citation style if detected")

    def tips_for_page(self, page: int) -> list[str]:
        """Get processing tips for a specific page based on detected hotspots."""
        tips = []
        page_hotspots = [h for h in self.hotspots if h.page == page]

        for h in page_hotspots:
            if h.element_type == "figure":
                tips.append(f"Figure detected: {h.description}. Generate descriptive alt-text.")
            elif h.element_type == "table":
                tips.append(f"Table detected: {h.description}. Ensure proper markdown table syntax.")
            elif h.element_type == "equation":
                tips.append(f"Equation detected. Transcribe to LaTeX if possible.")
            elif h.element_type == "code":
                tips.append(f"Code block detected. Preserve formatting and add language hint.")
            elif h.tip:
                tips.append(h.tip)

        return tips


class Correction(BaseModel):
    """Single correction made during processing, recorded for human review."""

    page: int = Field(..., description="Page number")
    edit_type: str = Field(..., description="Type of correction")
    original: str = Field(..., description="Original text/element")
    corrected: str = Field(..., description="Corrected text/element")
    reasoning: str = Field(..., description="Why this correction was made")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(default="correction_agent", description="What made this correction")
    status: str = Field(default="pending", description="pending, approved, rejected")


class PageResult(BaseModel):
    """Result from processing a single page."""

    page: int
    markdown: str
    corrections: list[Correction] = Field(default_factory=list)
    iterations: int = Field(default=1)
    complete: bool = Field(default=True)


class V3Response(BaseModel):
    """Response from v3 pipeline."""

    success: bool
    markdown: str = Field(default="", description="Final accessible markdown")

    # Context from analysis
    document_context: DocumentContext | None = None

    # All corrections for human review
    corrections: list[Correction] = Field(default_factory=list)
    correction_summary: dict[str, int] = Field(
        default_factory=dict, description="Count by correction type"
    )

    # Page-level results
    page_results: list[PageResult] = Field(default_factory=list)

    # Metrics
    total_pages: int = Field(default=0)
    llm_calls: int = Field(default=0)

    error: str | None = None


# =============================================================================
# Correction Agent Dependencies
# =============================================================================


@dataclass
class CorrectionDeps:
    """Dependencies for the correction agent."""

    current_page: int
    markdown: str
    page_image: "Image.Image | None"
    page_images: dict[int, "Image.Image"]
    page_width: float
    document_context: DocumentContext
    accumulated_headings: list[HeadingInfo] = field(default_factory=list)
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = field(
        default_factory=dict
    )
    # Trailing context from previous page for continuity
    trailing_context: str = ""
    # Corrections made by tools (figure/table) - collected after agent run
    tool_corrections: list["Correction"] = field(default_factory=list)


# =============================================================================
# Analyze Phase
# =============================================================================


class AnalysisOutput(BaseModel):
    """Output schema for analysis agent."""

    document_type: str = Field(..., description="Document classification")
    document_type_confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    has_multi_column: bool = Field(default=False)
    has_headers_footers: bool = Field(default=False)

    expected_heading_hierarchy: list[str] = Field(
        default_factory=list,
        description="Expected heading levels, e.g., ['Title', 'Chapter', 'Section']",
    )

    detected_figures: int = Field(default=0)
    detected_tables: int = Field(default=0)
    detected_equations: int = Field(default=0)
    detected_code_blocks: int = Field(default=0)

    key_terms: list[str] = Field(
        default_factory=list, description="Domain-specific terms found"
    )

    footnote_style: str = Field(default="none")
    citation_style: str = Field(default="unknown")


async def analyze_document(
    conversion_result: PDFConversionResult,
) -> DocumentContext:
    """Analyze document to extract context for correction phase.

    Looks at visual presentation to extract:
    - Layout metadata
    - Hotspots (figures, tables, equations, code)
    - Validation criteria (heading structure)
    - Document semantics
    """
    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    agent: Agent[None, AnalysisOutput] = Agent(
        model=model,
        output_type=AnalysisOutput,
        system_prompt="""You analyze PDF documents to prepare for accessibility remediation.

Examine the document and identify:
1. LAYOUT: Is it multi-column? Does it have headers/footers?
2. DOCUMENT TYPE: What kind of document is this? (syllabus, lecture notes, exam, etc.)
3. HEADING STRUCTURE: What hierarchy of headings should this document have?
4. HOTSPOTS: Count figures, tables, equations, code blocks that need special handling
5. KEY TERMS: Domain-specific terms that should not be flagged as misspellings
6. CONVENTIONS: Footnote style (numbered, symbols, none) and citation style if present

Be precise about counts - only count elements you can actually see.""",
    )

    # Build hotspots from Docling's extracted images
    hotspots: list[Hotspot] = []
    for img in conversion_result.extracted_images:
        element_type = "figure" if img.image_type == "picture" else "table"
        hotspots.append(
            Hotspot(
                page=img.page_num,
                element_type=element_type,
                bbox=img.bbox,
                description=img.caption[:100] if img.caption else f"{element_type} on page {img.page_num}",
                tip=f"Process {element_type}: generate alt-text" if element_type == "figure" else "Transcribe table to markdown",
            )
        )

    # Get first page for analysis
    first_page = conversion_result.pages[0] if conversion_result.pages else None
    if not first_page or not first_page.image_base64:
        logger.warning("No page image for analysis, using defaults")
        return DocumentContext(
            document_type="document",
            hotspots=hotspots,
            expected_heading_hierarchy=["Title", "Section", "Subsection"],
        )

    image_bytes = base64.b64decode(first_page.image_base64)

    try:
        result = await agent.run(
            [
                f"Analyze this {conversion_result.total_pages}-page document. "
                f"Docling already extracted {len(conversion_result.extracted_images)} images "
                f"(figures and tables). Verify this count and identify any additional hotspots.",
                BinaryContent(data=image_bytes, media_type="image/png"),
            ]
        )

        analysis = result.output
        logger.info(
            f"Analysis complete: {analysis.document_type} "
            f"({analysis.detected_figures} figures, {analysis.detected_tables} tables)"
        )

        return DocumentContext(
            document_type=analysis.document_type,
            document_type_confidence=analysis.document_type_confidence,
            has_multi_column=analysis.has_multi_column,
            has_headers_footers=analysis.has_headers_footers,
            expected_heading_hierarchy=analysis.expected_heading_hierarchy or ["Title", "Section", "Subsection"],
            hotspots=hotspots,
            key_terms=analysis.key_terms,
            footnote_style=analysis.footnote_style,
            citation_style=analysis.citation_style,
        )

    except Exception as e:
        logger.warning(f"Analysis failed: {e}, using defaults")
        return DocumentContext(
            document_type="document",
            hotspots=hotspots,
            expected_heading_hierarchy=["Title", "Section", "Subsection"],
        )


# =============================================================================
# Correction Phase
# =============================================================================


class CorrectionOutput(BaseModel):
    """Output from correction agent for a single page iteration."""

    thinking: str = Field(..., description="What was analyzed and decided")
    corrections_made: list[EditPatch] = Field(
        default_factory=list, description="Corrections applied this iteration"
    )
    detected_headings: list[HeadingInfo] = Field(
        default_factory=list, description="Headings found on this page"
    )
    needs_another_pass: bool = Field(default=False)
    page_complete: bool = Field(default=True)


def _create_correction_agent() -> Agent[CorrectionDeps, CorrectionOutput]:
    """Create the correction agent with tools."""
    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    agent: Agent[CorrectionDeps, CorrectionOutput] = Agent(
        model=model,
        output_type=CorrectionOutput,
        deps_type=CorrectionDeps,
        retries=3,  # Allow retries for output validation failures
        system_prompt="""You are an accessibility remediation agent for PDF-to-markdown conversions.

## PRIORITY ORDER (address in this sequence)
1. FIGURES: Replace image placeholders with alt-text (accessibility critical)
2. HEADINGS: Fix level skips (accessibility critical)
3. TABLES: Ensure proper markdown table structure
4. SPELLING: Fix OCR errors and obvious typos

## HEADING LEVEL RULES (mandatory)
- RULE: You can only go DOWN one level at a time (H1->H2->H3, not H1->H3)
- RULE: You can go UP any number of levels (H3->H1 is OK)
- INVALID: H1 followed by H3 (missing H2)
- INVALID: H2 followed by H4 (missing H3)
- FIX by adjusting the NEW heading level, not restructuring existing ones
- CHECK "Heading Structure So Far" before creating any heading

## FIGURE ALT-TEXT WORKFLOW
When you see `<!-- image -->` placeholder:
1. Call: tool_describe_figure(figure_index)
2. The tool automatically generates alt-text AND inserts it into the markdown
3. You'll receive a SUCCESS/ERROR message confirming the change
4. No need to call apply_edit - the tool handles everything

## SPELLING RULES
- FIX typos of technical terms: "Doclign"->"Docling", "Pytohn"->"Python"
- FIX when spell suggestion matches a key_term (indicates OCR error of known term)
- IGNORE correctly-spelled terms even if unfamiliar

## TOOL SELECTION
| Issue | Tool | Result |
|-------|------|--------|
| Image placeholder `<!-- image -->` | tool_describe_figure(index) | Auto-inserts alt-text |
| Table placeholder `<!-- table -->` | tool_describe_table(index) | Auto-inserts table markdown |
| Text/heading/spelling fix | apply_edit(...) | You provide original & replacement |

## PAGE CONTINUITY
- If "Previous Page Ending" is provided, ensure text flows naturally
- Content may continue mid-sentence from previous page - maintain coherence
- Don't treat continuation text as needing a new heading

## IMPORTANT
- tool_describe_figure and tool_describe_table handle their own edits automatically
- Use apply_edit only for text corrections (spelling, headings, content fixes)
- Be precise - only fix actual issues, don't over-edit
- Don't paraphrase - transcribe verbatim from image

## OUTPUT FORMAT
You MUST return a structured response with these fields:
- thinking: Brief summary of what you analyzed (required)
- corrections_made: List of EditPatch objects for changes made (can be empty [])
- detected_headings: List of HeadingInfo for headings on this page
- page_complete: true when no more fixes needed""",
    )

    @agent.system_prompt
    def add_page_context(ctx: RunContext[CorrectionDeps]) -> str:
        """Add page-specific context and tips."""
        deps = ctx.deps
        context = deps.document_context

        lines = ["\n## PAGE-SPECIFIC CONTEXT\n"]

        # Heading structure so far
        if deps.accumulated_headings:
            lines.append("### Heading Structure So Far")
            for h in deps.accumulated_headings[-5:]:  # Last 5 headings
                lines.append(f"  - H{h.level}: {h.text} (page {h.page})")
            lines.append("")

        # Tips for this page
        tips = context.tips_for_page(deps.current_page)
        if tips:
            lines.append("### Tips for This Page")
            for tip in tips:
                lines.append(f"  - {tip}")
            lines.append("")

        # Expected hierarchy
        if context.expected_heading_hierarchy:
            lines.append(f"### Expected Heading Hierarchy: {' > '.join(context.expected_heading_hierarchy)}")
            lines.append("")

        return "\n".join(lines)

    # Register tools
    @agent.tool
    async def tool_describe_figure(
        ctx: RunContext[CorrectionDeps],
        figure_index: int,
    ) -> str:
        """Generate alt-text for a figure and insert it into the markdown.

        This tool:
        1. Analyzes the figure image to generate descriptive alt-text
        2. Replaces the `<!-- image -->` placeholder with proper markdown
        3. Records the change for review

        Args:
            figure_index: Which figure on this page (1-indexed)

        Returns:
            Confirmation message describing what was inserted
        """
        deps = ctx.deps
        page = deps.current_page

        bbox_key = (page, "figure", figure_index)
        bbox = deps.element_bboxes.get(bbox_key)

        if bbox is None or deps.page_image is None:
            return f"ERROR: Figure {figure_index} not found (no bounding box or page image)"

        cropped, highlighted = crop_and_highlight(
            deps.page_image, bbox, deps.page_width
        )

        # Get description from subagent
        description = await get_figure_description(
            cropped_image=cropped,
            highlighted_page=highlighted,
            page_num=page,
        )

        # Build the replacement markdown
        alt_text = description.alt_text.replace('"', "'")  # Escape quotes
        replacement = f'![{alt_text}](image)'

        # Find and replace the placeholder
        placeholder = "<!-- image -->"
        if placeholder not in deps.markdown:
            return f"ERROR: No `<!-- image -->` placeholder found to replace for figure {figure_index}"

        # Apply the edit
        edit_result = apply_edit(
            reasoning=f"Generated alt-text for figure {figure_index}: {alt_text[:50]}...",
            edit_type="figure_alt",
            original_text=placeholder,
            replacement_text=replacement,
            markdown=deps.markdown,
            page=page,
            confidence=description.confidence,
            source="tool_describe_figure",
        )

        if not edit_result.success:
            return f"ERROR: Failed to apply edit: {edit_result.error}"

        # Update the markdown state
        deps.markdown = edit_result.new_markdown

        # Record the correction for tracking
        deps.tool_corrections.append(
            Correction(
                page=page,
                edit_type="figure_alt",
                original=placeholder,
                corrected=replacement,
                reasoning=f"Generated alt-text for figure {figure_index}: {alt_text}",
                confidence=description.confidence,
                source="tool_describe_figure",
            )
        )

        return f"SUCCESS: Inserted alt-text for figure {figure_index}: '{alt_text[:80]}...'"

    @agent.tool
    async def tool_describe_table(
        ctx: RunContext[CorrectionDeps],
        table_index: int,
    ) -> str:
        """Transcribe a table from the page image and insert it into the markdown.

        This tool:
        1. Analyzes the table image to generate proper markdown table syntax
        2. Replaces any `<!-- table -->` placeholder or inserts after table caption
        3. Records the change for review

        Args:
            table_index: Which table on this page (1-indexed)

        Returns:
            Confirmation message with the transcribed table
        """
        deps = ctx.deps
        page = deps.current_page

        bbox_key = (page, "table", table_index)
        bbox = deps.element_bboxes.get(bbox_key)

        if bbox is None or deps.page_image is None:
            return f"ERROR: Table {table_index} not found (no bounding box or page image)"

        cropped, highlighted = crop_and_highlight(
            deps.page_image, bbox, deps.page_width
        )

        # Get transcription from subagent
        transcription = await get_table_transcription(
            cropped_image=cropped,
            highlighted_page=highlighted,
            page_num=page,
        )

        table_md = transcription.markdown_table

        # Try to find and replace a table placeholder
        placeholder = "<!-- table -->"
        if placeholder in deps.markdown:
            edit_result = apply_edit(
                reasoning=f"Transcribed table {table_index}: {transcription.row_count}x{transcription.col_count}",
                edit_type="table_transcription",
                original_text=placeholder,
                replacement_text=table_md,
                markdown=deps.markdown,
                page=page,
                confidence=transcription.confidence,
                source="tool_describe_table",
            )

            if edit_result.success:
                deps.markdown = edit_result.new_markdown
                deps.tool_corrections.append(
                    Correction(
                        page=page,
                        edit_type="table_transcription",
                        original=placeholder,
                        corrected=f"[{transcription.row_count}x{transcription.col_count} table]",
                        reasoning=f"Transcribed table {table_index} from image",
                        confidence=transcription.confidence,
                        source="tool_describe_table",
                    )
                )
                return f"SUCCESS: Inserted {transcription.row_count}x{transcription.col_count} table"

        # No placeholder found - return the table for manual insertion
        # Record that we generated it even if not auto-inserted
        deps.tool_corrections.append(
            Correction(
                page=page,
                edit_type="table_transcription",
                original="[table image]",
                corrected=f"[{transcription.row_count}x{transcription.col_count} table generated]",
                reasoning=f"Generated table {table_index} markdown (manual review needed)",
                confidence=transcription.confidence,
                source="tool_describe_table",
            )
        )

        return f"GENERATED: Table {table_index} ({transcription.row_count}x{transcription.col_count}):\n\n{table_md}\n\nNote: No placeholder found. Use apply_edit to insert this table."

    return agent


async def correct_page(
    page_num: int,
    page_markdown: str,
    deps: CorrectionDeps,
    max_iterations: int = 3,
) -> PageResult:
    """Correct a single page with the correction agent.

    Steps:
    1. Pre-process: auto-fix lint and obvious spelling
    2. Agent correction: semantic fixes with tools
    3. Validate: ensure output passes lint
    """
    agent = _create_correction_agent()
    all_corrections: list[Correction] = []
    detected_headings: list[HeadingInfo] = []

    # Step 1: Pre-process (mechanical fixes)
    current_markdown = page_markdown

    lint_result = run_lint(current_markdown)
    if lint_result.fixed_markdown and lint_result.fixed_markdown != current_markdown:
        all_corrections.append(
            Correction(
                page=page_num,
                edit_type="lint_fix",
                original=f"[{lint_result.issues_found} formatting issues]",
                corrected="[auto-fixed]",
                reasoning=f"Auto-fixed lint issues: {', '.join(lint_result.issues[:3])}",
                confidence=1.0,
                source="mdformat",
            )
        )
        current_markdown = lint_result.fixed_markdown

    # Step 1b: Fix broken URLs (OCR spacing issues)
    url_result = fix_broken_urls(current_markdown)
    if url_result.fixed_markdown:
        all_corrections.append(
            Correction(
                page=page_num,
                edit_type="url_fix",
                original=f"[{url_result.urls_fixed} broken URLs]",
                corrected="[auto-fixed]",
                reasoning=f"Fixed URL spacing: {url_result.fixes[:3]}",
                confidence=1.0,
                source="fix_broken_urls",
            )
        )
        current_markdown = url_result.fixed_markdown

    # Step 2: Spell check as linter (surfaces issues for LLM to review)
    spell_result = spell_check(current_markdown, deps.document_context.key_terms)
    spell_issues_text = ""
    if spell_result.suggestions:
        issues = []
        for s in spell_result.suggestions[:20]:  # Limit to top 20
            word = s["word"]
            hint = s["suggestion"]
            if hint:
                issues.append(f"  - '{word}' (suggestion: '{hint}')")
            else:
                issues.append(f"  - '{word}' (no suggestion)")
        spell_issues_text = (
            "\n\nSpell check flagged these words (review in context - may be proper nouns or technical terms):\n"
            + "\n".join(issues)
        )

    # Step 3: Agent correction (semantic fixes + spell review)
    deps.markdown = current_markdown

    for iteration in range(max_iterations):
        logger.info(f"Correction iteration {iteration + 1}/{max_iterations} for page {page_num}")

        # Build prompt with context
        tips = deps.document_context.tips_for_page(page_num)
        tips_text = "\n".join(f"- {t}" for t in tips) if tips else "No specific tips."

        # Add trailing context section if available
        trailing_context_text = ""
        if deps.trailing_context:
            trailing_context_text = f"""

## Previous Page Ending (for continuity)
```
...{deps.trailing_context[-300:]}
```
Ensure text flows naturally from previous page. If content continues mid-sentence, maintain coherence."""

        prompt_parts: list[Any] = [
            f"""Correct page {page_num} of this {deps.document_context.document_type}.{trailing_context_text}

## This Page's Markdown
```markdown
{current_markdown}
```

Processing tips:
{tips_text}{spell_issues_text}

Iteration {iteration + 1}/{max_iterations}. Review flagged words - if suggestion matches a key_term, it's likely an OCR error of that term (e.g., 'Doclign'->'Docling'). Fix typos but ignore correctly-spelled technical terms. Report any headings found."""
        ]

        if deps.page_image:
            buffer = BytesIO()
            deps.page_image.save(buffer, format="PNG")
            prompt_parts.append(
                BinaryContent(data=buffer.getvalue(), media_type="image/png")
            )

        result = await agent.run(prompt_parts, deps=deps)
        output = result.output

        logger.info(f"Iteration {iteration + 1}: {output.thinking[:100]}...")

        # Record headings found
        detected_headings.extend(output.detected_headings)

        # Collect corrections made by tools (figure/table) during this iteration
        # Tools update deps.markdown directly and record in deps.tool_corrections
        if deps.tool_corrections:
            all_corrections.extend(deps.tool_corrections)
            logger.info(f"Collected {len(deps.tool_corrections)} tool corrections")
            deps.tool_corrections = []  # Clear for next iteration

        # Sync markdown state (tools may have updated deps.markdown)
        current_markdown = deps.markdown

        # Apply corrections from agent's structured output
        for edit in output.corrections_made:
            edit_result = apply_edit(
                reasoning=edit.reasoning,
                edit_type=edit.edit_type.value,
                original_text=edit.original_section,
                replacement_text=edit.modified_section,
                markdown=current_markdown,
                page=page_num,
                confidence=edit.confidence,
                source="correction_agent",
            )
            if edit_result.success:
                all_corrections.append(
                    Correction(
                        page=page_num,
                        edit_type=edit.edit_type.value,
                        original=edit.original_section,
                        corrected=edit.modified_section,
                        reasoning=edit.reasoning,
                        confidence=edit.confidence,
                        source="correction_agent",
                    )
                )
                current_markdown = edit_result.new_markdown
                deps.markdown = current_markdown

        if output.page_complete or not output.needs_another_pass:
            logger.info(f"Page {page_num} complete after {iteration + 1} iterations")
            break

    # Update accumulated headings
    deps.accumulated_headings.extend(detected_headings)

    return PageResult(
        page=page_num,
        markdown=current_markdown,
        corrections=all_corrections,
        iterations=iteration + 1,
        complete=True,
    )


async def correct_document(
    page_markdowns: dict[int, str],
    page_images: dict[int, "Image.Image"],
    page_width: float,
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    document_context: DocumentContext,
    is_born_digital: bool = False,
) -> tuple[str, list[PageResult], list[Correction]]:
    """Correct all pages sequentially, accumulating context.

    Each page's corrections inform the next page's context
    (e.g., heading structure builds up).

    Architecture:
    - Each page has its own markdown slice to work on
    - Agent processes their slice with access to page image
    - Hotspot pages (with figures/tables) get extra processing
    - Trailing context from previous page provides continuity
    - Final output concatenates all corrected page slices
    """
    page_results: list[PageResult] = []
    all_corrections: list[Correction] = []
    accumulated_headings: list[HeadingInfo] = []
    corrected_pages: dict[int, str] = {}  # Store corrected markdown for trailing context

    # Identify pages that have hotspots (figures/tables needing special attention)
    pages_with_hotspots = {h.page for h in document_context.hotspots}

    # Process all pages in order
    pages_to_process = sorted(page_markdowns.keys())

    for page_num in pages_to_process:
        page_markdown = page_markdowns[page_num]
        has_hotspots = page_num in pages_with_hotspots

        # Skip pages with empty markdown UNLESS they have hotspots
        # (hotspot pages may have figures/tables that need alt-text even if text is minimal)
        if not page_markdown.strip() and not has_hotspots:
            logger.debug(f"Skipping page {page_num}: no content and no hotspots")
            continue

        if has_hotspots:
            logger.info(f"Page {page_num} has hotspots: {[h for h in document_context.hotspots if h.page == page_num]}")

        # Build trailing context from previous corrected page
        trailing_context = ""
        if page_num > 1:
            # Find the most recent corrected page
            for prev_page in range(page_num - 1, 0, -1):
                if prev_page in corrected_pages and corrected_pages[prev_page].strip():
                    prev_text = corrected_pages[prev_page]
                    # Get last ~300 chars or last paragraph
                    if len(prev_text) <= 300:
                        trailing_context = prev_text
                    else:
                        # Try to get the last complete paragraph
                        paragraphs = prev_text.split("\n\n")
                        trailing_context = paragraphs[-1] if paragraphs else prev_text[-300:]
                        # If paragraph is too short, add more context
                        if len(trailing_context) < 100 and len(paragraphs) > 1:
                            trailing_context = "\n\n".join(paragraphs[-2:])
                    break

        deps = CorrectionDeps(
            current_page=page_num,
            markdown=page_markdown,
            page_image=page_images.get(page_num),
            page_images=page_images,
            page_width=page_width,
            document_context=document_context,
            accumulated_headings=accumulated_headings.copy(),
            element_bboxes=element_bboxes,
            trailing_context=trailing_context,
        )

        result = await correct_page(
            page_num=page_num,
            page_markdown=page_markdown,
            deps=deps,
        )

        page_results.append(result)
        all_corrections.extend(result.corrections)
        page_markdowns[page_num] = result.markdown
        corrected_pages[page_num] = result.markdown  # For trailing context

        # Accumulate headings for next page's context
        # (In real impl, would parse from result)

    # Combine all page outputs into final document
    # Filter out empty pages and join with page separators
    final_parts: list[str] = []
    for page_num in sorted(page_markdowns.keys()):
        page_content = page_markdowns[page_num].strip()
        if page_content:
            final_parts.append(page_content)

    final_markdown = "\n\n".join(final_parts)

    return final_markdown, page_results, all_corrections


# =============================================================================
# Helper Functions
# =============================================================================


def _split_markdown_by_page(markdown: str, total_pages: int) -> dict[int, str]:
    """Split a markdown document into per-page slices using Docling's page break markers.

    Docling inserts PAGE_BREAK_MARKER (<!-- PAGE_BREAK -->) between pages when
    export_to_markdown() is called with page_break_placeholder. This function
    splits on those markers for accurate page-aligned content.

    Falls back to heuristic-based splitting if markers aren't present (legacy behavior).

    Args:
        markdown: Full document markdown from Docling (with page break markers)
        total_pages: Number of pages in the PDF

    Returns:
        Dict mapping page number (1-indexed) to markdown slice
    """
    if total_pages <= 1:
        return {1: markdown}

    # Strategy 1: Split on Docling's page break markers (preferred, accurate)
    if PAGE_BREAK_MARKER in markdown:
        segments = markdown.split(PAGE_BREAK_MARKER)
        page_markdowns: dict[int, str] = {}

        for i, segment in enumerate(segments, start=1):
            # Strip leading/trailing whitespace from each page segment
            page_markdowns[i] = segment.strip()

        # If we got more segments than pages, merge extras into last page
        # (shouldn't happen with proper Docling output, but defensive)
        if len(segments) > total_pages:
            logger.warning(
                f"Got {len(segments)} segments but only {total_pages} pages; "
                "merging extras into last page"
            )
            extra_content = "\n\n".join(segments[total_pages:])
            page_markdowns[total_pages] = (
                page_markdowns.get(total_pages, "") + "\n\n" + extra_content
            ).strip()
            # Remove extra keys
            for key in list(page_markdowns.keys()):
                if key > total_pages:
                    del page_markdowns[key]

        # If we got fewer segments than pages, create empty entries for missing pages
        if len(segments) < total_pages:
            logger.warning(
                f"Got {len(segments)} segments but {total_pages} pages; "
                "creating empty entries for missing pages"
            )
            for page_num in range(len(segments) + 1, total_pages + 1):
                page_markdowns[page_num] = ""

        logger.info(
            f"Split markdown using page break markers: {len(segments)} segments "
            f"for {total_pages} pages"
        )
        return page_markdowns

    # Fallback: Heuristic-based splitting (legacy behavior for older exports)
    logger.warning(
        "No page break markers found in markdown; falling back to heuristic splitting"
    )

    lines = markdown.split("\n")
    total_lines = len(lines)

    # Strategy 2: Look for horizontal rules as page breaks
    page_break_pattern = r"^---+$|^_{3,}$|^\*{3,}$"
    import re

    page_breaks: list[int] = []
    for i, line in enumerate(lines):
        if re.match(page_break_pattern, line.strip()):
            page_breaks.append(i)

    # If we have roughly the right number of breaks, use them
    if len(page_breaks) >= total_pages - 1:
        breaks = page_breaks[: total_pages - 1]
        page_markdowns = {}

        start = 0
        for page_num, break_idx in enumerate(breaks, start=1):
            page_markdowns[page_num] = "\n".join(lines[start:break_idx])
            start = break_idx + 1

        page_markdowns[total_pages] = "\n".join(lines[start:])
        return page_markdowns

    # Strategy 3: Split by heading structure
    heading_indices: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith("# ") or line.startswith("## "):
            heading_indices.append(i)

    if len(heading_indices) >= total_pages:
        page_markdowns = {}
        lines_per_page = total_lines // total_pages

        current_start = 0
        for page_num in range(1, total_pages + 1):
            expected_end = page_num * lines_per_page

            if page_num == total_pages:
                page_markdowns[page_num] = "\n".join(lines[current_start:])
            else:
                best_break = expected_end
                for h_idx in heading_indices:
                    if h_idx > current_start and abs(h_idx - expected_end) < abs(best_break - expected_end):
                        best_break = h_idx

                if any(h_idx > current_start and abs(h_idx - expected_end) < lines_per_page // 2 for h_idx in heading_indices):
                    best_break = min(
                        (h for h in heading_indices if h > current_start),
                        key=lambda x: abs(x - expected_end),
                        default=expected_end,
                    )

                page_markdowns[page_num] = "\n".join(lines[current_start:best_break])
                current_start = best_break

        return page_markdowns

    # Strategy 4: Simple line-based split (last resort)
    lines_per_page = max(1, total_lines // total_pages)
    page_markdowns = {}

    for page_num in range(1, total_pages + 1):
        start_line = (page_num - 1) * lines_per_page
        if page_num == total_pages:
            end_line = total_lines
        else:
            end_line = page_num * lines_per_page

        page_markdowns[page_num] = "\n".join(lines[start_line:end_line])

    return page_markdowns


def _renumber_placeholders_per_page(page_markdowns: dict[int, str]) -> dict[int, str]:
    """Renumber image/table placeholders to be 1-indexed per page.

    Docling uses document-wide numbering (<!-- image:5 -->) but bbox mapping
    uses per-page indexing (page, "figure", 1). This function renumbers
    placeholders AFTER markdown is split by page so they match the bbox keys.

    Args:
        page_markdowns: Dict mapping page number to markdown slice

    Returns:
        Dict with renumbered placeholders per page
    """
    import re

    result = {}
    for page_num, markdown in page_markdowns.items():
        image_count = 0
        table_count = 0

        def replace_image(match: re.Match) -> str:
            nonlocal image_count
            image_count += 1
            return f"<!-- image:{image_count} -->"

        def replace_table(match: re.Match) -> str:
            nonlocal table_count
            table_count += 1
            return f"<!-- table:{table_count} -->"

        new_md = re.sub(r"<!-- image:\d+ -->", replace_image, markdown)
        new_md = re.sub(r"<!-- table:\d+ -->", replace_table, new_md)
        result[page_num] = new_md

    return result


# =============================================================================
# Endpoint
# =============================================================================


@router.post(
    "/process",
    response_model=V3Response,
    status_code=status.HTTP_200_OK,
    summary="Process PDF with v3 pipeline",
    description="""
    Clean pipeline: PDF → Docling → Analyze → Correction (loop) → Output

    Features:
    - Analyze phase extracts layout metadata, hotspots, validation criteria
    - Correction phase processes page-by-page with accumulated context
    - All corrections recorded for human review
    - Subagent tools for figures and tables

    **WARNING**: Skips PII scanning - for internal testing only.
    """,
)
async def process_v3(
    pdf_file: UploadFile = File(..., description="PDF file to process"),
) -> V3Response:
    """Process PDF with the v3 clean pipeline."""
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    llm_calls = 0

    try:
        pdf_content = await pdf_file.read()
        logger.info(f"V3 pipeline: {pdf_file.filename} ({len(pdf_content)} bytes)")

        # =====================================================================
        # Phase 1: DOCLING - Extract semantics, page images, initial markdown
        # =====================================================================
        converter = PDFConverter()
        conversion = await converter.convert_with_page_images(pdf_content)

        logger.info(
            f"Docling complete: {conversion.total_pages} pages, "
            f"{len(conversion.extracted_images)} images, "
            f"is_scanned={conversion.is_scanned}"
        )

        # =====================================================================
        # Phase 2: ANALYZE - Layout metadata, hotspots, validation criteria
        # =====================================================================
        document_context = await analyze_document(conversion)
        llm_calls += 1

        logger.info(
            f"Analyze complete: {document_context.document_type}, "
            f"{len(document_context.hotspots)} hotspots"
        )

        # =====================================================================
        # Phase 3: CORRECTION - Page by page with accumulated context
        # =====================================================================
        # Prepare page markdowns - split into per-page slices
        page_markdowns: dict[int, str] = {}
        if not conversion.is_scanned and conversion.docling_markdown:
            # Born-digital: Split Docling markdown into per-page slices
            # Each page gets its portion of the document to work on
            page_markdowns = _split_markdown_by_page(
                conversion.docling_markdown,
                conversion.total_pages,
            )
            # Renumber placeholders to be 1-indexed per page (fixes bbox mapping)
            page_markdowns = _renumber_placeholders_per_page(page_markdowns)
            logger.info(
                f"Split markdown into {len(page_markdowns)} page slices: "
                f"{[len(page_markdowns.get(i, '')) for i in range(1, conversion.total_pages + 1)]} chars"
            )
        else:
            # Scanned: would need LLM extraction (not implemented in this version)
            # For now, use placeholder
            for page in conversion.pages:
                page_markdowns[page.page_num] = f"<!-- Page {page.page_num}: OCR needed -->"

        # Prepare page images
        from PIL import Image
        page_images: dict[int, Image.Image] = {}
        for page in conversion.pages:
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                page_images[page.page_num] = Image.open(BytesIO(image_bytes))

        # Build element bboxes
        element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
        figure_counts: dict[int, int] = {}
        table_counts: dict[int, int] = {}
        images_without_bbox = 0

        for img in conversion.extracted_images:
            if img.bbox:
                if img.image_type == "picture":
                    idx = figure_counts.get(img.page_num, 0) + 1
                    figure_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "figure", idx)] = img.bbox
                else:
                    idx = table_counts.get(img.page_num, 0) + 1
                    table_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "table", idx)] = img.bbox
            else:
                images_without_bbox += 1
                logger.warning(
                    f"Image without bbox: page={img.page_num}, type={img.image_type}"
                )

        logger.info(
            f"Element bboxes: {len(element_bboxes)} with bbox, "
            f"{images_without_bbox} without bbox"
        )

        # Get page width
        page_width = 612.0  # Default 8.5" at 72 DPI
        if page_images:
            first_img = next(iter(page_images.values()))
            page_width = first_img.width / converter.images_scale

        # Run correction loop
        is_born_digital = not conversion.is_scanned and bool(conversion.docling_markdown)
        final_markdown, page_results, all_corrections = await correct_document(
            page_markdowns=page_markdowns,
            page_images=page_images,
            page_width=page_width,
            element_bboxes=element_bboxes,
            document_context=document_context,
            is_born_digital=is_born_digital,
        )

        # Count LLM calls from page processing
        llm_calls += sum(r.iterations for r in page_results)

        # Build correction summary
        from collections import Counter
        correction_summary = dict(Counter(c.edit_type for c in all_corrections))

        logger.info(
            f"Correction complete: {len(all_corrections)} corrections, "
            f"summary: {correction_summary}"
        )

        # =====================================================================
        # Phase 4: OUTPUT
        # =====================================================================
        return V3Response(
            success=True,
            markdown=final_markdown,
            document_context=document_context,
            corrections=all_corrections,
            correction_summary=correction_summary,
            page_results=page_results,
            total_pages=conversion.total_pages,
            llm_calls=llm_calls,
        )

    except ValueError as e:
        logger.error(f"V3 pipeline failed: {e}")
        return V3Response(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"V3 pipeline unexpected error: {e}")
        return V3Response(success=False, error=f"Unexpected error: {str(e)}")


# =============================================================================
# V4 Pipeline: Three-Phase Architecture (Analysis → Execution → Verification)
# =============================================================================


import asyncio as _asyncio  # for page timeout
import hashlib as _hashlib  # for issue signature hashing
import re as _re  # local import for PageConstraints


# =============================================================================
# V4 Heading Hierarchy Models
# =============================================================================


class HeadingEntry(BaseModel):
    """Single heading in document hierarchy."""

    text: str = Field(..., description="Normalized heading text")
    level: int = Field(..., ge=1, le=6, description="Heading level 1-6 (H1-H6)")
    page_number: int = Field(..., description="First page where heading appears")
    section_number: str | None = Field(
        default=None, description="Section number if present (e.g., '3.1', 'IV.B')"
    )


class DocumentHeadingHierarchy(BaseModel):
    """Complete heading hierarchy for a document."""

    headings: list[HeadingEntry] = Field(default_factory=list)
    document_title: str | None = Field(
        default=None, description="Document title if detected"
    )

    def get_headings_for_page(self, page_number: int) -> list[HeadingEntry]:
        """Get all headings that appear on a specific page."""
        return [h for h in self.headings if h.page_number == page_number]

    def get_heading_context_up_to_page(self, page_number: int) -> list[HeadingEntry]:
        """Get all headings up to and including a specific page (for context)."""
        return [h for h in self.headings if h.page_number <= page_number]


def _parse_section_number(text: str) -> tuple[str | None, str]:
    """Parse section number from heading text.

    Returns (section_number, cleaned_text).
    Examples:
        "3.1 Methods" -> ("3.1", "Methods")
        "IV.B Introduction" -> ("IV.B", "Introduction")
        "Chapter 2: Overview" -> ("2", "Overview")
        "Introduction" -> (None, "Introduction")
    """
    # Pattern for numeric section numbers: "3", "3.1", "3.1.1", etc.
    numeric_match = _re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", text.strip())
    if numeric_match:
        return numeric_match.group(1), numeric_match.group(2).strip()

    # Pattern for Roman numeral sections: "IV", "IV.B", etc.
    roman_match = _re.match(
        r"^([IVXLCDM]+(?:\.[A-Z])?)\s+(.+)$", text.strip(), _re.IGNORECASE
    )
    if roman_match:
        return roman_match.group(1).upper(), roman_match.group(2).strip()

    # Pattern for "Chapter X:" format
    chapter_match = _re.match(r"^Chapter\s+(\d+):?\s*(.+)$", text.strip(), _re.IGNORECASE)
    if chapter_match:
        return chapter_match.group(1), chapter_match.group(2).strip()

    return None, text.strip()


def _section_depth_to_level(section_number: str | None) -> int | None:
    """Convert section number depth to heading level.

    Rules:
    - No section number: return None (use original markdown level)
    - "3" (no dots): H2
    - "3.1" (1 dot): H3
    - "3.1.1" (2 dots): H4
    - Formula: level = parts.count('.') + 2

    Returns None if section_number is None (preserve original level).
    """
    if not section_number:
        return None

    # Count dots to determine depth
    dot_count = section_number.count(".")
    # Roman numerals with letters (IV.B) also use dot counting
    return dot_count + 2


def extract_heading_hierarchy(
    full_markdown: str,
    page_markdowns: dict[int, str] | None = None,
) -> DocumentHeadingHierarchy:
    """Extract heading hierarchy from markdown.

    Parses headings using regex and determines appropriate levels based on
    section numbering depth.

    Args:
        full_markdown: Complete document markdown
        page_markdowns: Optional per-page markdown dict for page number attribution

    Returns:
        DocumentHeadingHierarchy with all headings and their levels
    """
    headings: list[HeadingEntry] = []
    document_title: str | None = None

    # Regex to match markdown headings: ^#{1,6}\s+(.+)$
    heading_pattern = _re.compile(r"^(#{1,6})\s+(.+)$", _re.MULTILINE)

    # If we have per-page markdown, extract headings with page attribution
    if page_markdowns:
        for page_num, page_md in sorted(page_markdowns.items()):
            for match in heading_pattern.finditer(page_md):
                hashes = match.group(1)
                original_level = len(hashes)
                raw_text = match.group(2).strip()

                # Parse section number
                section_number, clean_text = _parse_section_number(raw_text)

                # Determine final level
                if section_number:
                    # Section number depth determines level
                    computed_level = _section_depth_to_level(section_number)
                    level = computed_level if computed_level else original_level
                else:
                    # No section number - preserve original level
                    level = original_level

                # First H1 without section number is likely the title
                if original_level == 1 and not section_number and document_title is None:
                    document_title = clean_text

                headings.append(
                    HeadingEntry(
                        text=clean_text,
                        level=level,
                        page_number=page_num,
                        section_number=section_number,
                    )
                )
    else:
        # No per-page info - use full markdown, default to page 1
        for match in heading_pattern.finditer(full_markdown):
            hashes = match.group(1)
            original_level = len(hashes)
            raw_text = match.group(2).strip()

            section_number, clean_text = _parse_section_number(raw_text)

            if section_number:
                computed_level = _section_depth_to_level(section_number)
                level = computed_level if computed_level else original_level
            else:
                level = original_level

            if original_level == 1 and not section_number and document_title is None:
                document_title = clean_text

            headings.append(
                HeadingEntry(
                    text=clean_text,
                    level=level,
                    page_number=1,  # Default to page 1 when no page info
                    section_number=section_number,
                )
            )

    return DocumentHeadingHierarchy(headings=headings, document_title=document_title)


def format_heading_context_for_page(
    page_number: int,
    hierarchy: DocumentHeadingHierarchy,
) -> str:
    """Format heading context for a specific page as a prompt section.

    Returns a concise prompt section listing headings on this page with
    their prescribed levels.

    Args:
        page_number: The page being processed
        hierarchy: The full document heading hierarchy

    Returns:
        Formatted string for agent prompts, or empty string if no headings
    """
    page_headings = hierarchy.get_headings_for_page(page_number)

    if not page_headings:
        return ""

    lines = ["HEADINGS ON THIS PAGE (use exactly these levels):"]
    for h in page_headings:
        if h.section_number:
            lines.append(f"- H{h.level}: {h.section_number} {h.text}")
        else:
            lines.append(f"- H{h.level}: {h.text}")

    return "\n".join(lines)


# =============================================================================
# V4 Pre-computed Document Context
# =============================================================================


@dataclass
class PrecomputedDocumentContext:
    """Pre-computed context extracted once before per-page processing.

    This context helps page agents understand cross-page references like
    "see Figure 3" or "as shown in Table 2" even when processing different pages.
    """

    # Document overview
    summary: str = ""  # 2-3 sentence document summary
    document_type: str = "document"  # "academic paper", "course syllabus", "lecture notes", etc.

    # Document structure
    toc: list[str] = field(default_factory=list)  # Extracted headings/structure
    heading_hierarchy: DocumentHeadingHierarchy = field(
        default_factory=DocumentHeadingHierarchy
    )  # Prescriptive heading levels

    # Cross-page reference maps
    figures: dict[str, str] = field(default_factory=dict)  # "Figure 1" → brief description
    tables: dict[str, str] = field(default_factory=dict)  # "Table 1" → brief description

    # Domain context
    key_terms: list[str] = field(default_factory=list)  # Domain-specific terms


class HeadingAnalysisEntry(BaseModel):
    """LLM-extracted heading with level and reasoning."""

    text: str = Field(..., description="The heading text (e.g., 'Introduction', '1.1 Methods')")
    level: int = Field(..., ge=1, le=6, description="Heading level 1-6 (H1=title, H2=main sections, etc.)")
    page_number: int = Field(default=1, description="Page where heading appears (1-indexed)")
    reasoning: str = Field(
        default="",
        description="Why this level was chosen (e.g., 'Large bold font', 'Numbered section 1.1')"
    )


class ContextExtractionOutput(BaseModel):
    """Output schema for document context extraction."""

    summary: str = Field(..., description="2-3 sentence summary of the document")
    document_type: str = Field(..., description="Type: academic paper, syllabus, lecture notes, etc.")
    headings: list[str] = Field(default_factory=list, description="Main headings/sections found")
    heading_analysis: list[HeadingAnalysisEntry] = Field(
        default_factory=list,
        description="Detailed heading hierarchy with levels. CRITICAL: Include ALL headings with correct H1-H6 levels."
    )
    figures: dict[str, str] = Field(
        default_factory=dict,
        description="Map of figure references to descriptions, e.g., {'Figure 1': 'bar chart of enrollment'}"
    )
    tables: dict[str, str] = Field(
        default_factory=dict,
        description="Map of table references to descriptions, e.g., {'Table 1': 'course schedule'}"
    )
    key_terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific terms (for spell-check whitelist)"
    )


class ContextAnalysisCost(BaseModel):
    """Cost tracking for document context analysis."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


async def analyze_document_context(
    full_markdown: str,
    page_images: dict[int, "Image.Image"],
    page_markdowns: dict[int, str] | None = None,
) -> tuple[PrecomputedDocumentContext, ContextAnalysisCost]:
    """Analyze full document to extract cross-page context.

    Makes ONE LLM call to extract:
    - Document summary and type
    - HEADING HIERARCHY with levels (critical for accessibility)
    - Figure and table descriptions (for cross-references)
    - Key domain terms

    Falls back to regex-based heading extraction if LLM returns empty.

    Args:
        full_markdown: Complete document markdown (all pages)
        page_images: Dict of page images (uses first few for visual context)
        page_markdowns: Optional per-page markdown for page-accurate heading extraction

    Returns:
        Tuple of (PrecomputedDocumentContext, ContextAnalysisCost)
    """
    # Extract heading hierarchy via regex as fallback
    regex_heading_hierarchy = extract_heading_hierarchy(full_markdown, page_markdowns)
    logger.debug(
        f"Regex fallback extracted {len(regex_heading_hierarchy.headings)} headings"
    )

    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    agent: Agent[None, ContextExtractionOutput] = Agent(
        model=model,
        output_type=ContextExtractionOutput,
        system_prompt="""Extract document context for accessibility remediation.

You receive the document markdown AND page images. Extract:

1. SUMMARY: 2-3 sentences describing what this document is about
2. TYPE: academic paper, course syllabus, lecture notes, exam, lab manual, etc.

3. HEADING_ANALYSIS (CRITICAL FOR ACCESSIBILITY):
   Extract EVERY heading with its correct level (H1-H6). This is essential.

   Rules for determining heading levels:
   - H1: Document title only (usually one per document, largest/boldest text)
   - H2: Major sections (Introduction, Methods, Results, Conclusion, or numbered like "1", "2", "3")
   - H3: Subsections (1.1, 1.2, or unnumbered subsections under H2)
   - H4: Sub-subsections (1.1.1, 1.1.2, or deeper nesting)
   - H5/H6: Rarely used, very deep nesting

   Use visual cues from images:
   - Larger/bolder text = higher level (lower number)
   - More indentation or smaller font = lower level (higher number)
   - Section numbers indicate level: "1" = H2, "1.1" = H3, "1.1.1" = H4

   For each heading provide:
   - text: The heading text exactly as written
   - level: 1-6 (H1-H6)
   - page_number: Which page it appears on
   - reasoning: Brief note on why you chose this level

4. FIGURES: For each figure placeholder, describe what it likely is
5. TABLES: For each table placeholder, describe what data it contains
6. KEY TERMS: Domain-specific terms (for spell-check whitelist)

IMPORTANT: The heading_analysis field is the most critical output. Screen readers
depend on correct heading hierarchy. Do not skip any headings.""",
    )

    # Prepare prompt with markdown
    prompt_parts: list[Any] = [
        f"Analyze this document and extract context:\n\n```markdown\n{full_markdown[:15000]}\n```"  # Limit to ~15k chars
    ]

    # Include first 3 page images for visual context (critical for heading level detection)
    for page_num in sorted(page_images.keys())[:3]:
        buffer = BytesIO()
        page_images[page_num].save(buffer, format="PNG")
        prompt_parts.append(
            BinaryContent(data=buffer.getvalue(), media_type="image/png")
        )
    logger.debug(f"Included {min(3, len(page_images))} page images for context analysis")

    try:
        result = await agent.run(prompt_parts)
        output = result.output

        # Extract usage
        usage_info = result.usage()
        cost = ContextAnalysisCost(
            input_tokens=usage_info.input_tokens or 0,
            output_tokens=usage_info.output_tokens or 0,
            cost=calculate_cost(
                usage_info.input_tokens or 0,
                usage_info.output_tokens or 0
            ),
        )

        # Convert LLM heading_analysis to DocumentHeadingHierarchy
        if output.heading_analysis:
            llm_headings = [
                HeadingEntry(
                    text=h.text,
                    level=h.level,
                    page_number=h.page_number,
                    section_number=None,  # LLM doesn't need to parse section numbers
                )
                for h in output.heading_analysis
            ]
            # Determine document title from first H1
            doc_title = None
            for h in llm_headings:
                if h.level == 1:
                    doc_title = h.text
                    break

            heading_hierarchy = DocumentHeadingHierarchy(
                headings=llm_headings,
                document_title=doc_title,
            )
            logger.info(
                f"LLM extracted {len(llm_headings)} headings, "
                f"title={doc_title}"
            )
        else:
            # Fall back to regex extraction
            heading_hierarchy = regex_heading_hierarchy
            logger.warning(
                f"LLM returned no headings, using regex fallback "
                f"({len(regex_heading_hierarchy.headings)} headings)"
            )

        logger.info(
            f"Document context extracted: type={output.document_type}, "
            f"{len(output.figures)} figures, {len(output.tables)} tables, "
            f"{len(heading_hierarchy.headings)} headings, cost=${cost.cost:.6f}"
        )

        return PrecomputedDocumentContext(
            summary=output.summary,
            document_type=output.document_type,
            toc=output.headings,
            heading_hierarchy=heading_hierarchy,
            figures=output.figures,
            tables=output.tables,
            key_terms=output.key_terms,
        ), cost

    except Exception as e:
        logger.warning(f"Context extraction failed: {e}, using regex fallback")
        return PrecomputedDocumentContext(
            summary="Document context extraction failed",
            document_type="document",
            heading_hierarchy=regex_heading_hierarchy,
        ), ContextAnalysisCost()


def format_context_for_prompt(
    ctx: PrecomputedDocumentContext,
    page_number: int | None = None,
) -> str:
    """Format PrecomputedDocumentContext as a prompt section.

    Args:
        ctx: The precomputed document context
        page_number: Optional page number to include page-specific heading context

    Returns a concise context block to inject into agent prompts.
    """
    lines = ["DOCUMENT CONTEXT:"]
    lines.append(f"This is a {ctx.document_type}: {ctx.summary}")

    if ctx.figures:
        fig_list = ", ".join(f"{k}: {v}" for k, v in list(ctx.figures.items())[:5])
        lines.append(f"Figures: {fig_list}")

    if ctx.tables:
        tbl_list = ", ".join(f"{k}: {v}" for k, v in list(ctx.tables.items())[:5])
        lines.append(f"Tables: {tbl_list}")

    if ctx.key_terms:
        lines.append(f"Key terms: {', '.join(ctx.key_terms[:10])}")

    # Add page-specific heading context if page_number provided
    if page_number is not None and ctx.heading_hierarchy.headings:
        heading_context = format_heading_context_for_page(page_number, ctx.heading_hierarchy)
        if heading_context:
            lines.append("")
            lines.append(heading_context)

    return "\n".join(lines)


@dataclass
class IssueSignature:
    """Stable identifier for tracking issues across iterations.

    Normalizes issue descriptions (removes numbers, lowercases) to detect
    when the same conceptual issue reappears in different forms.
    """

    issue_type: str
    content_hash: str  # Hash of normalized description

    @classmethod
    def from_verification_issue(cls, issue: VerificationIssue) -> "IssueSignature":
        """Create a signature from a VerificationIssue."""
        # Normalize: remove numbers, lowercase for stable comparison
        desc_normalized = _re.sub(r'\d+', 'N', issue.description.lower())
        content_hash = _hashlib.sha256(desc_normalized.encode()).hexdigest()[:8]
        return cls(issue_type=issue.issue_type, content_hash=content_hash)

    def __hash__(self) -> int:
        return hash((self.issue_type, self.content_hash))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, IssueSignature)
            and self.issue_type == other.issue_type
            and self.content_hash == other.content_hash
        )


@dataclass
class IssueTracker:
    """Track issue identity across verification iterations.

    Detects oscillation (same issues appearing repeatedly) and tracks
    issue recurrence patterns to inform convergence decisions.
    """

    seen: dict[IssueSignature, int] = field(default_factory=dict)  # signature -> count
    history: list[set[IssueSignature]] = field(default_factory=list)  # list of sets per iteration

    def record(self, issues: list[VerificationIssue]) -> None:
        """Record issues from a verification iteration."""
        current: set[IssueSignature] = set()
        for issue in issues:
            sig = IssueSignature.from_verification_issue(issue)
            current.add(sig)
            self.seen[sig] = self.seen.get(sig, 0) + 1
        self.history.append(current)

    def is_oscillating(self) -> bool:
        """Check if we're oscillating (same issues in last 2 iterations)."""
        if len(self.history) < 2:
            return False
        # Check if any issues appear in both last 2 iterations
        return len(self.history[-1] & self.history[-2]) > 0

    def get_recurring_count(self) -> int:
        """Get count of issues that have appeared more than once."""
        return sum(1 for count in self.seen.values() if count > 1)


def evaluate_convergence(
    verification: VerificationResult,
    tracker: IssueTracker,
    iteration: int,
    max_iterations: int,
) -> tuple[bool, str, str]:
    """Evaluate whether to stop the verification loop.

    Returns (should_stop, reason, quality_level) where:
    - should_stop: True if we should exit the loop
    - reason: Why we're stopping ("passed", "oscillation", "max_iterations", "continue")
    - quality_level: Assessment ("excellent", "acceptable", "in_progress")
    """
    # 1. Perfect pass - no issues
    if verification.passed or len(verification.issues) == 0:
        return True, "passed", "excellent"

    # 2. Oscillation detected - same issues reappearing
    if tracker.is_oscillating():
        logger.info(
            f"Oscillation detected: {tracker.get_recurring_count()} recurring issues"
        )
        return True, "oscillation", "acceptable"

    # 3. Max iterations reached
    if iteration >= max_iterations:
        return True, "max_iterations", "acceptable"

    # Continue iterating
    return False, "continue", "in_progress"


@dataclass
class PageConstraints:
    """Valid element IDs for a page. Used for programmatic pre-validation."""

    page_num: int
    figure_ids: list[int] = field(default_factory=list)
    table_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_bboxes(
        cls,
        page_num: int,
        element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    ) -> "PageConstraints":
        """Build constraints from bbox dict for a specific page."""
        figure_ids = sorted([
            key[2] for key in element_bboxes
            if key[0] == page_num and key[1] == "figure"
        ])
        table_ids = sorted([
            key[2] for key in element_bboxes
            if key[0] == page_num and key[1] == "table"
        ])
        return cls(page_num=page_num, figure_ids=figure_ids, table_ids=table_ids)

    def validate_issue(self, issue_id: str) -> tuple[bool, str]:
        """Check if issue_id references a valid element. Returns (valid, error)."""
        if match := _re.match(r"image:(\d+)", issue_id):
            fig_id = int(match.group(1))
            if fig_id not in self.figure_ids:
                return False, f"figure {fig_id} not in {self.figure_ids}"
            return True, ""
        if match := _re.match(r"table:(\d+)", issue_id):
            tbl_id = int(match.group(1))
            if tbl_id not in self.table_ids:
                return False, f"table {tbl_id} not in {self.table_ids}"
            return True, ""
        # Non-element issues (heading, spelling, line) are always valid
        return True, ""


class IntentionalChange(BaseModel):
    """A change intentionally made by execution."""

    issue_id: str = Field(..., description="Issue ID, e.g., 'heading:1', 'image:1'")
    change_type: str = Field(..., description="Type of change, e.g., 'heading_level_fix', 'alt_text'")
    original: str = Field(..., description="What was there before")
    corrected: str = Field(..., description="What it is now")
    reasoning: str = Field(..., description="Why the change was made")


class ExecutionManifest(BaseModel):
    """Summary of what execution intentionally changed.

    This manifest is passed to verification so it knows what changes
    were intentional and should NOT be reverted unless actually wrong.
    """

    page_num: int = Field(..., description="Page number (1-indexed)")
    changes: list[IntentionalChange] = Field(default_factory=list)

    @classmethod
    def from_execution_result(
        cls,
        result: ExecutionResult,
        issues: list[PageIssue],
    ) -> "ExecutionManifest":
        """Build manifest from execution result.

        Args:
            result: The execution result containing corrections made
            issues: The original issues that were passed to execution

        Returns:
            ExecutionManifest summarizing intentional changes
        """
        changes: list[IntentionalChange] = []
        for correction in result.corrections_made:
            # Find matching issue if possible
            issue_id = "unknown"
            for issue in issues:
                # Check if issue_id appears in reasoning or matches edit type
                if issue.issue_id.lower() in correction.reasoning.lower():
                    issue_id = issue.issue_id
                    break
            changes.append(
                IntentionalChange(
                    issue_id=issue_id,
                    change_type=correction.edit_type,
                    original=correction.original[:100],  # Truncate for prompt size
                    corrected=correction.corrected[:100],
                    reasoning=correction.reasoning[:100],
                )
            )
        return cls(page_num=result.page_num, changes=changes)


def format_manifest_for_prompt(manifest: "ExecutionManifest | None") -> str:
    """Format execution manifest as a prompt section for verification.

    Args:
        manifest: The execution manifest, or None if no changes were made

    Returns:
        Formatted string to include in verification prompt
    """
    if not manifest or not manifest.changes:
        return ""
    lines = ["CHANGES MADE BY EXECUTION (do not revert unless WRONG):"]
    for c in manifest.changes:
        lines.append(f"- [{c.issue_id}] {c.change_type}: `{c.original}` -> `{c.corrected}`")
    return "\n".join(lines)


class LLMCallCost(BaseModel):
    """Cost breakdown for a single LLM call."""

    phase: str = Field(..., description="Pipeline phase: analysis, execution, verification")
    page: int = Field(..., description="Page number (1-indexed)")
    input_tokens: int = Field(default=0, description="Input tokens consumed")
    output_tokens: int = Field(default=0, description="Output tokens generated")
    cost: float = Field(default=0.0, description="Cost in USD")


# AWS Bedrock Claude Haiku pricing (per 1K tokens)
HAIKU_INPUT_COST_PER_1K = 0.00025
HAIKU_OUTPUT_COST_PER_1K = 0.00125


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for Haiku model usage."""
    input_cost = (input_tokens / 1000) * HAIKU_INPUT_COST_PER_1K
    output_cost = (output_tokens / 1000) * HAIKU_OUTPUT_COST_PER_1K
    return input_cost + output_cost


class V4PageResult(BaseModel):
    """Result from processing a single page through the v4 pipeline."""

    page: int
    markdown: str
    analysis: AnalysisResult | None = None
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None
    corrections: list[RefineCorrection] = Field(default_factory=list)
    cost_breakdown: list[LLMCallCost] = Field(
        default_factory=list, description="Cost breakdown for each LLM call on this page"
    )


class PrecomputedContextResponse(BaseModel):
    """Serializable version of PrecomputedDocumentContext for API response."""

    summary: str = ""
    document_type: str = "document"
    toc: list[str] = Field(default_factory=list)
    heading_hierarchy: DocumentHeadingHierarchy = Field(
        default_factory=DocumentHeadingHierarchy
    )
    figures: dict[str, str] = Field(default_factory=dict)
    tables: dict[str, str] = Field(default_factory=dict)
    key_terms: list[str] = Field(default_factory=list)


class V4Response(BaseModel):
    """Response from v4 pipeline."""

    success: bool
    markdown: str = Field(default="", description="Final accessible markdown")

    # Pre-computed document context (new in v4)
    document_context: PrecomputedContextResponse | None = Field(
        default=None, description="Pre-computed context extracted before page processing"
    )
    context_analysis_cost: ContextAnalysisCost | None = Field(
        default=None, description="Cost of document context analysis"
    )

    # Page-level results with all phase outputs
    page_results: dict[int, V4PageResult] = Field(default_factory=dict)

    # Aggregated corrections for human review
    corrections: list[RefineCorrection] = Field(default_factory=list)
    correction_summary: dict[str, int] = Field(
        default_factory=dict, description="Count by correction type"
    )

    # Metrics
    total_pages: int = Field(default=0)
    pages_analyzed: int = Field(default=0)
    pages_verified: int = Field(default=0)
    verification_passed: int = Field(default=0)
    llm_calls: int = Field(default=0)

    # Cost tracking
    total_cost: float = Field(default=0.0, description="Total cost in USD")
    cost_breakdown: list[LLMCallCost] = Field(
        default_factory=list, description="Cost breakdown for each LLM call"
    )

    error: str | None = None


async def process_page_v4(
    page_num: int,
    markdown: str,
    page_image: "Image.Image",
    page_images: dict[int, "Image.Image"],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float = 612.0,
    max_verification_loops: int = 2,
    page_timeout_seconds: float = 120.0,
    document_context: PrecomputedDocumentContext | None = None,
) -> V4PageResult:
    """Process a single page with timeout protection.

    Wraps _process_page_v4_impl with asyncio timeout to prevent runaway pages.

    Args:
        page_num: Page number being processed (1-indexed)
        markdown: Current markdown for this page
        page_image: PIL Image of the page
        page_images: All page images (for cross-page tools)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for coordinate scaling
        max_verification_loops: Max verification retry loops
        page_timeout_seconds: Timeout for page processing
        document_context: Pre-computed document context (optional)
    """
    try:
        return await _asyncio.wait_for(
            _process_page_v4_impl(
                page_num=page_num,
                markdown=markdown,
                page_image=page_image,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                max_verification_loops=max_verification_loops,
                document_context=document_context,
            ),
            timeout=page_timeout_seconds,
        )
    except _asyncio.TimeoutError:
        logger.error(f"Page {page_num} timed out after {page_timeout_seconds}s")
        return V4PageResult(
            page=page_num,
            markdown=markdown,  # Return original markdown
            analysis=None,
            execution=None,
            verification=None,
            corrections=[],
        )


async def _process_page_v4_impl(
    page_num: int,
    markdown: str,
    page_image: "Image.Image",
    page_images: dict[int, "Image.Image"],
    element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]],
    page_width: float = 612.0,
    max_verification_loops: int = 2,
    document_context: PrecomputedDocumentContext | None = None,
) -> V4PageResult:
    """Internal implementation of page processing.

    Pipeline:
        1. ANALYSIS: Detect issues (figures needing alt-text, heading problems, etc.)
        2. EXECUTION: Apply fixes using tools (describe_figure, find_and_replace)
        3. VERIFICATION: "Fresh eyes" check that markdown matches the page image

    Args:
        page_num: Page number being processed (1-indexed)
        markdown: Current markdown for this page
        page_image: PIL Image of the page
        page_images: All page images (for cross-page tools)
        element_bboxes: Bounding boxes for figures/tables
        page_width: Page width for coordinate scaling
        max_verification_loops: Max verification retry loops
        document_context: Pre-computed document context (optional, enhances agent prompts)
    """
    # Track costs for this page
    page_costs: list[LLMCallCost] = []

    # Format context for prompts if available (includes page-specific heading context)
    context_prompt = ""
    if document_context:
        context_prompt = format_context_for_prompt(document_context, page_number=page_num)

    # Phase 1: ANALYSIS - Detect issues
    logger.info(f"Phase 1: Analyzing page {page_num}")
    analysis, analysis_usage = await analyze_page(
        page_num, markdown, page_image, context_prompt=context_prompt
    )
    logger.info(
        f"Analysis found {len(analysis.issues)} issues, quality={analysis.markdown_quality}"
    )

    # Record analysis cost
    analysis_cost = calculate_cost(analysis_usage.input_tokens, analysis_usage.output_tokens)
    page_costs.append(LLMCallCost(
        phase="analysis",
        page=page_num,
        input_tokens=analysis_usage.input_tokens,
        output_tokens=analysis_usage.output_tokens,
        cost=analysis_cost,
    ))

    if not analysis.issues:
        # No issues found, skip execution and verification
        logger.info(f"Page {page_num}: No issues found, skipping execution")
        return V4PageResult(
            page=page_num,
            markdown=markdown,
            analysis=analysis,
            execution=None,
            verification=None,
            corrections=[],
            cost_breakdown=page_costs,
        )

    # PRE-VALIDATION: Filter out issues referencing non-existent elements
    # This is the programmatic guarantee - invalid issues never reach execution
    constraints = PageConstraints.from_bboxes(page_num, element_bboxes)
    valid_issues: list[PageIssue] = []
    for issue in analysis.issues:
        is_valid, error = constraints.validate_issue(issue.issue_id)
        if is_valid:
            valid_issues.append(issue)
        else:
            logger.warning(f"Page {page_num}: Filtering invalid issue {issue.issue_id} - {error}")

    if not valid_issues:
        logger.info(f"Page {page_num}: All {len(analysis.issues)} issues filtered as invalid")
        return V4PageResult(
            page=page_num,
            markdown=markdown,
            analysis=analysis,
            execution=None,
            verification=None,
            corrections=[],
            cost_breakdown=page_costs,
        )

    logger.info(
        f"Page {page_num}: {len(valid_issues)}/{len(analysis.issues)} issues passed validation "
        f"(figures={constraints.figure_ids}, tables={constraints.table_ids})"
    )

    # Phase 2: EXECUTION - Apply fixes (only validated issues)
    logger.info(f"Phase 2: Executing corrections for page {page_num}")
    execution = await execute_corrections(
        page_num=page_num,
        issues=valid_issues,  # Only valid issues
        markdown=markdown,
        page_images=page_images,
        element_bboxes=element_bboxes,
        page_width=page_width,
        context_prompt=context_prompt,
    )
    logger.info(
        f"Execution made {len(execution.corrections_made)} corrections, "
        f"resolved {len(execution.issues_resolved)}/{len(analysis.issues)} issues"
    )

    # Record execution cost
    exec_usage = execution.usage
    exec_cost = calculate_cost(exec_usage.input_tokens, exec_usage.output_tokens)
    page_costs.append(LLMCallCost(
        phase="execution",
        page=page_num,
        input_tokens=exec_usage.input_tokens,
        output_tokens=exec_usage.output_tokens,
        cost=exec_cost,
    ))

    current_markdown = execution.final_markdown
    all_corrections = list(execution.corrections_made)

    # Build execution manifest for verification
    manifest = ExecutionManifest.from_execution_result(execution, valid_issues)
    logger.info(f"Built execution manifest with {len(manifest.changes)} changes")

    # Phase 3: VERIFICATION - Fresh eyes check
    logger.info(f"Phase 3: Verifying page {page_num}")
    verification = await verify_page(
        page_num, current_markdown, page_image,
        execution_manifest=manifest, context_prompt=context_prompt
    )

    # Record verification cost
    verif_usage = verification.usage
    verif_cost = calculate_cost(verif_usage.input_tokens, verif_usage.output_tokens)
    page_costs.append(LLMCallCost(
        phase="verification",
        page=page_num,
        input_tokens=verif_usage.input_tokens,
        output_tokens=verif_usage.output_tokens,
        cost=verif_cost,
    ))

    # Initialize issue tracker for convergence detection
    tracker = IssueTracker()

    if verification.passed:
        logger.info(f"Page {page_num} passed verification")
    else:
        logger.warning(
            f"Page {page_num} failed verification: {len(verification.issues)} issues"
        )

        # Record initial verification issues
        tracker.record(verification.issues)

        # Verification retry loop with convergence tracking
        for loop in range(max_verification_loops):
            # Check convergence BEFORE attempting retry
            should_stop, reason, quality = evaluate_convergence(
                verification, tracker, loop + 1, max_verification_loops
            )

            if should_stop:
                logger.info(
                    f"Page {page_num}: stopping - {reason}, quality={quality}"
                )
                break

            logger.info(
                f"Verification loop {loop + 1}/{max_verification_loops} for page {page_num}"
            )

            # Convert verification issues to PageIssue format for execution
            verification_issues = [
                PageIssue(
                    issue_id=f"verification:{i}",
                    issue_type="other",
                    description=vi.description,
                    context=vi.suggested_action,
                    location=vi.location,
                    severity="major",
                )
                for i, vi in enumerate(verification.issues, 1)
            ]

            # Re-run execution with verification issues
            execution_retry = await execute_corrections(
                page_num=page_num,
                issues=verification_issues,
                markdown=current_markdown,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                context_prompt=context_prompt,
            )

            # Record retry execution cost
            retry_exec_usage = execution_retry.usage
            retry_exec_cost = calculate_cost(retry_exec_usage.input_tokens, retry_exec_usage.output_tokens)
            page_costs.append(LLMCallCost(
                phase=f"execution_retry_{loop + 1}",
                page=page_num,
                input_tokens=retry_exec_usage.input_tokens,
                output_tokens=retry_exec_usage.output_tokens,
                cost=retry_exec_cost,
            ))

            current_markdown = execution_retry.final_markdown
            all_corrections.extend(execution_retry.corrections_made)

            # Build manifest for retry verification (includes original + retry corrections)
            retry_manifest = ExecutionManifest.from_execution_result(
                execution_retry, verification_issues
            )

            # Re-verify
            verification = await verify_page(
                page_num, current_markdown, page_image,
                execution_manifest=retry_manifest, context_prompt=context_prompt
            )

            # Record retry verification cost
            retry_verif_usage = verification.usage
            retry_verif_cost = calculate_cost(retry_verif_usage.input_tokens, retry_verif_usage.output_tokens)
            page_costs.append(LLMCallCost(
                phase=f"verification_retry_{loop + 1}",
                page=page_num,
                input_tokens=retry_verif_usage.input_tokens,
                output_tokens=retry_verif_usage.output_tokens,
                cost=retry_verif_cost,
            ))

            # Track issues for convergence detection
            tracker.record(verification.issues)

            if verification.passed:
                logger.info(f"Page {page_num} passed verification after retry")
                break

    return V4PageResult(
        page=page_num,
        markdown=current_markdown,
        analysis=analysis,
        execution=execution,
        verification=verification,
        corrections=all_corrections,
        cost_breakdown=page_costs,
    )


@router.post(
    "/v4/process",
    response_model=V4Response,
    status_code=status.HTTP_200_OK,
    summary="Process PDF with v4 three-phase pipeline",
    description="""
    Three-phase pipeline: PDF → Docling → Analysis → Execution → Verification → Output

    Features:
    - Analysis phase detects issues (figures, headings, spelling)
    - Execution phase applies fixes using simple tools
    - Verification phase provides "fresh eyes" check
    - Each phase uses a specialized agent

    **WARNING**: Skips PII scanning - for internal testing only.
    """,
)
async def process_v4(
    pdf_file: UploadFile = File(..., description="PDF file to process"),
) -> V4Response:
    """Process PDF with the v4 three-phase pipeline."""
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    llm_calls = 0

    try:
        pdf_content = await pdf_file.read()
        logger.info(f"V4 pipeline: {pdf_file.filename} ({len(pdf_content)} bytes)")

        # =====================================================================
        # Phase 0: DOCLING - Extract semantics, page images, initial markdown
        # =====================================================================
        converter = PDFConverter()
        conversion = await converter.convert_with_page_images(pdf_content)

        logger.info(
            f"Docling complete: {conversion.total_pages} pages, "
            f"{len(conversion.extracted_images)} images, "
            f"is_scanned={conversion.is_scanned}"
        )

        # =====================================================================
        # Prepare per-page markdown and images
        # =====================================================================
        # Split markdown into per-page slices
        page_markdowns: dict[int, str] = {}
        if not conversion.is_scanned and conversion.docling_markdown:
            page_markdowns = _split_markdown_by_page(
                conversion.docling_markdown,
                conversion.total_pages,
            )
            # Renumber placeholders to be 1-indexed per page (fixes bbox mapping)
            page_markdowns = _renumber_placeholders_per_page(page_markdowns)
            logger.info(
                f"Split markdown into {len(page_markdowns)} page slices"
            )
        else:
            for page in conversion.pages:
                page_markdowns[page.page_num] = f"<!-- Page {page.page_num}: OCR needed -->"

        # Prepare page images
        from PIL import Image
        page_images: dict[int, Image.Image] = {}
        for page in conversion.pages:
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                page_images[page.page_num] = Image.open(BytesIO(image_bytes))

        # Build element bboxes (maps (page, element_type, index) -> bbox)
        element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
        figure_counts: dict[int, int] = {}
        table_counts: dict[int, int] = {}
        images_without_bbox = 0

        for img in conversion.extracted_images:
            if img.bbox:
                if img.image_type == "picture":
                    idx = figure_counts.get(img.page_num, 0) + 1
                    figure_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "figure", idx)] = img.bbox
                else:
                    idx = table_counts.get(img.page_num, 0) + 1
                    table_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "table", idx)] = img.bbox
            else:
                images_without_bbox += 1
                logger.warning(
                    f"Image without bbox: page={img.page_num}, type={img.image_type}"
                )

        logger.info(
            f"Element bboxes: {len(element_bboxes)} with bbox, "
            f"{images_without_bbox} without bbox"
        )

        # Get page width for coordinate scaling
        page_width = 612.0  # Default 8.5" at 72 DPI
        if page_images:
            first_img = next(iter(page_images.values()))
            page_width = first_img.width / converter.images_scale

        # =====================================================================
        # Phase 0.5: DOCUMENT CONTEXT - Extract cross-page context ONCE
        # =====================================================================
        full_markdown = conversion.docling_markdown or ""
        document_context, context_cost = await analyze_document_context(
            full_markdown=full_markdown,
            page_images=page_images,
            page_markdowns=page_markdowns,
        )
        llm_calls += 1  # Context analysis call

        logger.info(
            f"Document context: {document_context.document_type}, "
            f"{len(document_context.figures)} figures, {len(document_context.tables)} tables, "
            f"{len(document_context.heading_hierarchy.headings)} headings, "
            f"cost=${context_cost.cost:.6f}"
        )

        # =====================================================================
        # Process each page through the three-phase pipeline
        # =====================================================================
        page_results: dict[int, V4PageResult] = {}
        all_corrections: list[RefineCorrection] = []
        pages_analyzed = 0
        pages_verified = 0
        verification_passed = 0

        for page_num in sorted(page_markdowns.keys()):
            markdown_slice = page_markdowns.get(page_num, "")
            page_image = page_images.get(page_num)

            if not page_image:
                logger.warning(f"No image for page {page_num}, skipping")
                continue

            if not markdown_slice.strip():
                logger.debug(f"Empty markdown for page {page_num}, skipping")
                continue

            # Process through three-phase pipeline
            result = await process_page_v4(
                page_num=page_num,
                markdown=markdown_slice,
                page_image=page_image,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                document_context=document_context,
            )

            page_results[page_num] = result
            all_corrections.extend(result.corrections)

            # Track metrics
            if result.analysis:
                pages_analyzed += 1
                llm_calls += 1  # Analysis call
            if result.execution:
                llm_calls += 1  # Execution call
            if result.verification:
                pages_verified += 1
                llm_calls += 1  # Verification call
                if result.verification.passed:
                    verification_passed += 1

        # =====================================================================
        # Combine final markdown
        # =====================================================================
        final_parts: list[str] = []
        for page_num in sorted(page_results.keys()):
            page_content = page_results[page_num].markdown.strip()
            if page_content:
                final_parts.append(page_content)

        final_markdown = "\n\n".join(final_parts)

        # Build correction summary
        from collections import Counter
        correction_summary = dict(Counter(c.edit_type for c in all_corrections))

        # =====================================================================
        # Aggregate cost breakdown from all pages
        # =====================================================================
        all_costs: list[LLMCallCost] = []

        # Add context analysis cost as first entry
        all_costs.append(LLMCallCost(
            phase="context_analysis",
            page=0,  # Document-level, not page-specific
            input_tokens=context_cost.input_tokens,
            output_tokens=context_cost.output_tokens,
            cost=context_cost.cost,
        ))

        for page_result in page_results.values():
            all_costs.extend(page_result.cost_breakdown)

        total_cost = sum(cost.cost for cost in all_costs)

        logger.info(
            f"V4 pipeline complete: {len(all_corrections)} corrections, "
            f"{verification_passed}/{pages_verified} pages passed verification, "
            f"total_cost=${total_cost:.6f}"
        )

        # Convert document context to response model
        context_response = PrecomputedContextResponse(
            summary=document_context.summary,
            document_type=document_context.document_type,
            toc=document_context.toc,
            heading_hierarchy=document_context.heading_hierarchy,
            figures=document_context.figures,
            tables=document_context.tables,
            key_terms=document_context.key_terms,
        )

        return V4Response(
            success=True,
            markdown=final_markdown,
            document_context=context_response,
            context_analysis_cost=context_cost,
            page_results=page_results,
            corrections=all_corrections,
            correction_summary=correction_summary,
            total_pages=conversion.total_pages,
            pages_analyzed=pages_analyzed,
            pages_verified=pages_verified,
            verification_passed=verification_passed,
            llm_calls=llm_calls,
            total_cost=total_cost,
            cost_breakdown=all_costs,
        )

    except ValueError as e:
        logger.error(f"V4 pipeline failed: {e}")
        return V4Response(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"V4 pipeline unexpected error: {e}")
        return V4Response(success=False, error=f"Unexpected error: {str(e)}")
