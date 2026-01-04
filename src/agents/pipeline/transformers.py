"""LLM-assisted transformers for specific element types.

Each transformer is a focused, bounded LLM call that:
- Takes structured input
- Returns structured output
- Has a single, clear responsibility

These are NOT agents - they're functions that happen to use LLMs.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# Lazy-loaded model to avoid initialization at import time
_model: BedrockConverseModel | None = None


def _get_model() -> BedrockConverseModel:
    """Get or create the shared model instance."""
    global _model
    if _model is None:
        _model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
    return _model


# =============================================================================
# Heading Level Inference
# =============================================================================


class HeadingLevelResult(BaseModel):
    """Result of heading level inference."""

    inferred_level: int = Field(ge=1, le=6, description="Heading level 1-6")
    confidence: float = Field(ge=0, le=1, description="Confidence in inference")
    reasoning: str = Field(description="Brief explanation")


_heading_agent: Agent[None, HeadingLevelResult] | None = None


def _get_heading_agent() -> Agent[None, HeadingLevelResult]:
    global _heading_agent
    if _heading_agent is None:
        _heading_agent = Agent(
            _get_model(),
            output_type=HeadingLevelResult,
            system_prompt="""You infer heading levels from context.

Given a heading and its surrounding context (preceding headings, document type),
determine the appropriate heading level (1-6).

Consider:
- Document structure (H1 for title, H2 for major sections, etc.)
- Preceding heading hierarchy
- Visual/semantic weight of the heading text
- Common patterns for the document type

Return a level 1-6 and your confidence.""",
        )
    return _heading_agent


async def infer_heading_level(
    heading_text: str,
    docling_level: int,
    preceding_headings: list[tuple[str, int]],  # (text, level) pairs
    document_type: str = "document",
) -> HeadingLevelResult:
    """Infer the appropriate heading level.

    Args:
        heading_text: The heading content
        docling_level: Level suggested by Docling's hierarchy
        preceding_headings: Recent headings with their levels for context
        document_type: Type of document (syllabus, paper, etc.)

    Returns:
        HeadingLevelResult with inferred level and confidence
    """
    agent = _get_heading_agent()

    context_str = "\n".join(
        f"  H{level}: {text[:50]}..." if len(text) > 50 else f"  H{level}: {text}"
        for text, level in preceding_headings[-5:]  # Last 5 headings
    )

    prompt = f"""Document type: {document_type}
Docling's suggested level: {docling_level}

Preceding headings:
{context_str if context_str else "  (none)"}

Current heading to classify: "{heading_text}"

What level should this heading be?"""

    try:
        result = await agent.run(prompt)
        logger.debug(f"Heading '{heading_text[:30]}...' -> H{result.output.inferred_level}")
        return result.output
    except Exception as e:
        logger.warning(f"Heading inference failed: {e}")
        return HeadingLevelResult(
            inferred_level=docling_level,
            confidence=0.5,
            reasoning=f"Fallback to Docling level due to error: {e}",
        )


# =============================================================================
# Alt Text Generation
# =============================================================================


class AltTextResult(BaseModel):
    """Result of alt text generation."""

    alt_text: str = Field(description="Generated alt text")
    figure_type: str = Field(description="Type: diagram, photo, chart, etc.")
    confidence: float = Field(ge=0, le=1, description="Confidence in description")


_alt_text_agent: Agent[None, AltTextResult] | None = None


def _get_alt_text_agent() -> Agent[None, AltTextResult]:
    global _alt_text_agent
    if _alt_text_agent is None:
        _alt_text_agent = Agent(
            _get_model(),
            output_type=AltTextResult,
            system_prompt="""You generate accessible alt text for images.

Create concise, descriptive alt text that:
- Conveys the essential information/meaning
- Is appropriate for screen readers
- Follows WCAG guidelines
- Is 1-2 sentences unless complexity requires more

For charts/graphs: describe the trend/conclusion, not every data point.
For diagrams: describe the concept being illustrated.
For photos: describe relevant content and context.""",
        )
    return _alt_text_agent


async def generate_alt_text(
    image: Image.Image,
    caption: str = "",
    document_type: str = "document",
    surrounding_text: str = "",
) -> AltTextResult:
    """Generate alt text for an image.

    Args:
        image: PIL Image of the figure
        caption: Caption text if available
        document_type: Type of document for context
        surrounding_text: Text near the image for context

    Returns:
        AltTextResult with alt text and metadata
    """
    agent = _get_alt_text_agent()

    # Convert image to bytes
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    context_parts = []
    if caption:
        context_parts.append(f"Caption: {caption}")
    if document_type != "document":
        context_parts.append(f"Document type: {document_type}")
    if surrounding_text:
        context_parts.append(f"Context: {surrounding_text[:200]}")

    context = "\n".join(context_parts) if context_parts else "No additional context."

    try:
        result = await agent.run([
            f"Generate alt text for this image.\n\n{context}",
            BinaryContent(data=image_bytes, media_type="image/png"),
        ])
        logger.debug(f"Generated alt text: {result.output.alt_text[:50]}...")
        return result.output
    except Exception as e:
        logger.warning(f"Alt text generation failed: {e}")
        return AltTextResult(
            alt_text="[Image description unavailable]",
            figure_type="unknown",
            confidence=0.0,
        )


# =============================================================================
# Table Semantic Labeling
# =============================================================================


class TableSemanticsResult(BaseModel):
    """Result of table semantic analysis."""

    has_header_row: bool = Field(description="Whether first row is a header")
    has_header_column: bool = Field(description="Whether first column is a header")
    table_type: str = Field(description="Type: data, comparison, schedule, etc.")
    markdown: str = Field(description="Cleaned markdown with proper formatting")
    confidence: float = Field(ge=0, le=1)


_table_agent: Agent[None, TableSemanticsResult] | None = None


def _get_table_agent() -> Agent[None, TableSemanticsResult]:
    global _table_agent
    if _table_agent is None:
        _table_agent = Agent(
            _get_model(),
            output_type=TableSemanticsResult,
            system_prompt="""You analyze tables for semantic structure.

Given a table (as image or markdown), determine:
1. Whether the first row is a header
2. Whether the first column is a header (row headers)
3. The type of table (data, comparison, schedule, matrix, etc.)
4. Clean markdown representation

For accessibility, proper header identification is critical.
Return well-formatted markdown with correct alignment.""",
        )
    return _table_agent


async def analyze_table(
    table_image: Image.Image | None = None,
    table_markdown: str = "",
    caption: str = "",
) -> TableSemanticsResult:
    """Analyze table structure and semantics.

    Args:
        table_image: Optional image of the table
        table_markdown: Docling's markdown extraction
        caption: Table caption if available

    Returns:
        TableSemanticsResult with semantic information
    """
    agent = _get_table_agent()

    messages: list = []

    if table_image:
        buffer = BytesIO()
        table_image.save(buffer, format="PNG")
        messages.append(BinaryContent(data=buffer.getvalue(), media_type="image/png"))

    prompt = "Analyze this table for semantic structure."
    if table_markdown:
        prompt += f"\n\nDocling extracted this markdown:\n```\n{table_markdown}\n```"
    if caption:
        prompt += f"\n\nCaption: {caption}"

    messages.insert(0, prompt)

    try:
        result = await agent.run(messages)
        logger.debug(f"Table analysis: header_row={result.output.has_header_row}")
        return result.output
    except Exception as e:
        logger.warning(f"Table analysis failed: {e}")
        return TableSemanticsResult(
            has_header_row=True,  # Safe default
            has_header_column=False,
            table_type="unknown",
            markdown=table_markdown or "| Error analyzing table |",
            confidence=0.0,
        )


# =============================================================================
# List Type Classification
# =============================================================================


class ListTypeResult(BaseModel):
    """Result of list type classification."""

    list_type: str = Field(description="ordered, unordered, or definition")
    confidence: float = Field(ge=0, le=1)
    reasoning: str


_list_agent: Agent[None, ListTypeResult] | None = None


def _get_list_agent() -> Agent[None, ListTypeResult]:
    global _list_agent
    if _list_agent is None:
        _list_agent = Agent(
            _get_model(),
            output_type=ListTypeResult,
            system_prompt="""You classify list types for accessibility.

Given list items, determine if they form:
- **ordered**: Sequential steps, ranked items, numbered procedures
- **unordered**: Unordered collection, bullet points, features
- **definition**: Term followed by definition (dt/dd pattern)

Definition lists often have a bold/emphasized term followed by explanation.
Ordered lists imply sequence matters.
Unordered lists are collections where order doesn't matter.""",
        )
    return _list_agent


async def classify_list_type(
    items: list[str],
    visual_pattern: str = "",  # e.g., "numbered", "bulleted", "bold-term-colon"
) -> ListTypeResult:
    """Classify the type of a list.

    Args:
        items: List item texts
        visual_pattern: Visual formatting pattern if known

    Returns:
        ListTypeResult with classification
    """
    agent = _get_list_agent()

    items_preview = "\n".join(f"- {item[:100]}" for item in items[:10])
    prompt = f"""Classify this list:

{items_preview}
{"..." if len(items) > 10 else ""}

Visual pattern: {visual_pattern or "unknown"}

Is this an ordered list, unordered list, or definition list?"""

    try:
        result = await agent.run(prompt)
        logger.debug(f"List classified as: {result.output.list_type}")
        return result.output
    except Exception as e:
        logger.warning(f"List classification failed: {e}")
        return ListTypeResult(
            list_type="unordered",
            confidence=0.5,
            reasoning=f"Fallback due to error: {e}",
        )


# =============================================================================
# Formula/Equation Transcription
# =============================================================================


class FormulaResult(BaseModel):
    """Result of formula transcription."""

    latex: str = Field(description="LaTeX representation of the formula")
    plain_text: str = Field(description="Plain text fallback for accessibility")
    formula_type: str = Field(description="Type: inline, display, equation_block")
    confidence: float = Field(ge=0, le=1)


_formula_agent: Agent[None, FormulaResult] | None = None


def _get_formula_agent() -> Agent[None, FormulaResult]:
    global _formula_agent
    if _formula_agent is None:
        _formula_agent = Agent(
            _get_model(),
            output_type=FormulaResult,
            system_prompt="""You transcribe mathematical formulas to LaTeX.

Given an image of a formula or equation, produce:
1. Accurate LaTeX representation
2. Plain text fallback for screen readers

Guidelines:
- Use standard LaTeX notation (\\frac, \\sum, \\int, etc.)
- For Greek letters, use proper commands (\\alpha, \\beta, etc.)
- Include proper spacing and grouping
- The plain text should be readable when spoken aloud

Examples of good plain text:
- "x squared plus y squared equals r squared"
- "the sum from i equals 1 to n of x sub i"
- "the integral from 0 to infinity of e to the negative x dx"

Be precise with mathematical notation.""",
        )
    return _formula_agent


async def transcribe_formula(
    image: "Image.Image",
    docling_text: str = "",
    context: str = "",
) -> FormulaResult:
    """Transcribe a formula from image to LaTeX.

    Args:
        image: PIL Image of the formula region
        docling_text: What Docling extracted (often garbled)
        context: Surrounding text for disambiguation

    Returns:
        FormulaResult with LaTeX and plain text
    """
    agent = _get_formula_agent()

    # Convert image to bytes
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    prompt = "Transcribe this mathematical formula to LaTeX."
    if docling_text:
        prompt += f"\n\nDocling extracted (likely garbled): {docling_text}"
    if context:
        prompt += f"\n\nContext: {context[:200]}"

    try:
        result = await agent.run([
            prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ])
        logger.debug(f"Formula transcribed: {result.output.latex[:50]}...")
        return result.output
    except Exception as e:
        logger.warning(f"Formula transcription failed: {e}")
        return FormulaResult(
            latex="[Formula transcription failed]",
            plain_text=docling_text or "[Formula description unavailable]",
            formula_type="unknown",
            confidence=0.0,
        )


# =============================================================================
# Code Block Transcription
# =============================================================================


class CodeBlockResult(BaseModel):
    """Result of code block transcription."""

    code: str = Field(description="Transcribed code")
    language: str = Field(description="Programming language (python, java, etc.)")
    is_pseudocode: bool = Field(description="Whether this is pseudocode vs real code")
    confidence: float = Field(ge=0, le=1)


_code_agent: Agent[None, CodeBlockResult] | None = None


def _get_code_agent() -> Agent[None, CodeBlockResult]:
    global _code_agent
    if _code_agent is None:
        _code_agent = Agent(
            _get_model(),
            output_type=CodeBlockResult,
            system_prompt="""You transcribe code blocks from document page images.

Your task:
1. Find the code block(s) in the image (may be the whole page or a region)
2. Transcribe the code exactly as shown
3. Identify the programming language
4. Indicate if it's pseudocode vs real executable code

What counts as code:
- Programming language code (Python, Java, C++, JavaScript, etc.)
- Pseudocode or algorithm descriptions with code-like structure
- Command-line examples or shell scripts
- SQL queries, config files, or markup languages
- Mathematical algorithms written in code notation

Guidelines:
- Preserve indentation exactly - this is critical for Python and pseudocode
- Include all comments (// or # style)
- Preserve line numbers if shown (but put them as comments)
- Use proper syntax for the identified language
- For pseudocode, use consistent formatting
- Don't try to "fix" or optimize the code
- If multiple code blocks exist, transcribe all of them separated by blank lines

Be precise with syntax, variable names, and operators.""",
        )
    return _code_agent


async def transcribe_code_block(
    image: "Image.Image",
    docling_text: str = "",
    context: str = "",
) -> CodeBlockResult:
    """Transcribe a code block from image.

    Args:
        image: PIL Image of the code region
        docling_text: What Docling extracted (may have formatting issues)
        context: Surrounding text for context

    Returns:
        CodeBlockResult with transcribed code
    """
    agent = _get_code_agent()

    # Convert image to bytes
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    prompt = "Transcribe this code block exactly as shown."
    if docling_text:
        prompt += f"\n\nDocling extracted (may have issues): {docling_text[:500]}"
    if context:
        prompt += f"\n\nContext: {context[:200]}"

    try:
        result = await agent.run([
            prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ])
        logger.debug(f"Code transcribed: {result.output.language}, {len(result.output.code)} chars")
        return result.output
    except Exception as e:
        logger.warning(f"Code transcription failed: {e}")
        return CodeBlockResult(
            code=docling_text or "[Code transcription failed]",
            language="unknown",
            is_pseudocode=False,
            confidence=0.0,
        )
