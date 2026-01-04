"""Page analysis phase - identifies what Docling will miss.

This runs BEFORE processing to identify:
- Document type (academic, syllabus, brochure, etc.)
- Hotspots that need LLM transcription (code, equations)
- Semantic conventions (footnotes, citations)

One LLM call per page, outputs structured analysis.
"""

from __future__ import annotations

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

# Lazy-loaded model
_model: BedrockConverseModel | None = None


def _get_model() -> BedrockConverseModel:
    """Get or create the shared model instance."""
    global _model
    if _model is None:
        _model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
    return _model


# =============================================================================
# Output Models
# =============================================================================


class BoundingBoxEstimate(BaseModel):
    """Estimated location of an element on the page."""

    description: str = Field(description="What this element contains")
    location: str = Field(description="Where on page: top/middle/bottom, left/center/right")
    approximate_lines: int = Field(default=0, description="Roughly how many lines of content")


class PageAnalysis(BaseModel):
    """Analysis of a single page."""

    # Document classification (same across all pages, but we ask each time for robustness)
    document_type: str = Field(
        description="Type: academic_paper, syllabus, technical_manual, brochure, form, other"
    )
    document_type_confidence: float = Field(ge=0, le=1)

    # Hotspots - things Docling will likely mangle
    code_blocks: list[BoundingBoxEstimate] = Field(
        default_factory=list,
        description="Code snippets, pseudocode, or algorithm blocks"
    )
    equations: list[BoundingBoxEstimate] = Field(
        default_factory=list,
        description="Mathematical equations or formulas"
    )
    complex_diagrams: list[BoundingBoxEstimate] = Field(
        default_factory=list,
        description="Flowcharts, architecture diagrams, or complex figures with text"
    )

    # Semantic conventions
    has_footnotes: bool = Field(default=False)
    footnote_style: str = Field(
        default="none",
        description="none, numbered_superscript, symbols, endnotes"
    )
    has_citations: bool = Field(default=False)
    citation_style: str = Field(
        default="unknown",
        description="APA, IEEE, numbered, author-year, or unknown"
    )

    # Structure hints
    expected_sections: list[str] = Field(
        default_factory=list,
        description="Expected section headings based on document type"
    )

    # Page-specific notes
    page_notes: str = Field(
        default="",
        description="Any other observations about this page"
    )


class DocumentAnalysis(BaseModel):
    """Aggregated analysis across all pages."""

    document_type: str
    document_type_confidence: float
    total_pages: int

    # Aggregated hotspots by page
    pages_with_code: list[int] = Field(default_factory=list)
    pages_with_equations: list[int] = Field(default_factory=list)
    pages_with_complex_diagrams: list[int] = Field(default_factory=list)

    # Document-wide conventions
    footnote_style: str = "none"
    citation_style: str = "unknown"
    expected_sections: list[str] = Field(default_factory=list)

    # Per-page analysis for detailed processing
    page_analyses: dict[int, PageAnalysis] = Field(default_factory=dict)


# =============================================================================
# Analyzer Agent
# =============================================================================

_analyzer_agent: Agent[None, PageAnalysis] | None = None


def _get_analyzer_agent() -> Agent[None, PageAnalysis]:
    """Get or create the analyzer agent."""
    global _analyzer_agent
    if _analyzer_agent is None:
        _analyzer_agent = Agent(
            _get_model(),
            output_type=PageAnalysis,
            system_prompt="""You analyze PDF page images to identify content that automated extraction tools struggle with.

Your job is to scan the page and identify:

1. **Document Type**: What kind of document is this?
   - academic_paper: Research papers, journal articles, conference papers
   - syllabus: Course syllabi, class schedules
   - technical_manual: Software docs, API references, user guides
   - brochure: Marketing materials, flyers
   - form: Applications, surveys, fillable documents
   - other: Anything else

2. **Code Blocks**: Look for:
   - Syntax-highlighted code
   - Monospace font sections
   - Pseudocode or algorithms
   - Command-line examples
   - These often get mangled into plain text by extraction tools

3. **Equations**: Look for:
   - Mathematical formulas with symbols (∑, ∫, √, etc.)
   - Inline or display equations
   - These often become garbled text like "glyph[epsilon]"

4. **Complex Diagrams**: Look for:
   - Flowcharts with text labels
   - Architecture diagrams
   - Figures that contain important text annotations

5. **Footnotes & Citations**:
   - Are there superscript numbers or symbols?
   - Is there a references section?
   - What citation style (APA, IEEE, numbered)?

6. **Expected Sections**: Based on document type, what sections would you expect?
   - Academic: Abstract, Introduction, Methods, Results, Discussion, References
   - Syllabus: Course Info, Schedule, Grading, Policies
   - Technical: Overview, Installation, API Reference, Examples

Be specific about WHERE elements are located (top/middle/bottom, left/center/right).
If you're unsure, say so - don't make things up.""",
        )
    return _analyzer_agent


async def analyze_page(
    page_image: "Image.Image",
    page_number: int,
    total_pages: int,
) -> PageAnalysis:
    """Analyze a single page to identify hotspots and conventions.

    Args:
        page_image: PIL Image of the page
        page_number: 1-indexed page number
        total_pages: Total pages in document

    Returns:
        PageAnalysis with identified hotspots and conventions
    """
    agent = _get_analyzer_agent()

    # Convert image to bytes
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    prompt = f"""Analyze this page image (page {page_number} of {total_pages}).

Identify:
1. Document type (if this is page 1, classify the document; otherwise confirm)
2. Any code blocks, equations, or complex diagrams on THIS page
3. Footnote and citation conventions you observe
4. Expected sections based on document type

Be thorough but concise."""

    try:
        result = await agent.run([
            prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ])
        logger.info(
            f"Page {page_number} analysis: type={result.output.document_type}, "
            f"code={len(result.output.code_blocks)}, "
            f"equations={len(result.output.equations)}"
        )
        return result.output
    except Exception as e:
        logger.warning(f"Page {page_number} analysis failed: {e}")
        return PageAnalysis(
            document_type="unknown",
            document_type_confidence=0.0,
            page_notes=f"Analysis failed: {e}",
        )


async def analyze_document(
    page_images: dict[int, "Image.Image"],
    analyze_all: bool = True,
) -> DocumentAnalysis:
    """Analyze a document to identify hotspots.

    Args:
        page_images: Dict of page_number -> PIL Image
        analyze_all: If True, analyze every page. If False, sample.

    Returns:
        DocumentAnalysis with aggregated results
    """
    total_pages = len(page_images)
    pages = sorted(page_images.keys())

    if analyze_all:
        sample_pages = pages
    else:
        # Fallback sampling for very large docs (50+ pages)
        if total_pages <= 10:
            sample_pages = pages
        else:
            # First 3, every 5th, last 3
            sample_pages = pages[:3]
            sample_pages.extend(pages[4::5])
            sample_pages.extend(pages[-3:])
            sample_pages = sorted(set(sample_pages))

    logger.info(f"Analyzing {len(sample_pages)} of {total_pages} pages")

    # Analyze sampled pages
    page_analyses: dict[int, PageAnalysis] = {}
    for page_no in sample_pages:
        if page_no in page_images:
            analysis = await analyze_page(page_images[page_no], page_no, total_pages)
            page_analyses[page_no] = analysis

    # Aggregate results
    doc_types = [pa.document_type for pa in page_analyses.values()]
    most_common_type = max(set(doc_types), key=doc_types.count) if doc_types else "unknown"

    avg_confidence = sum(pa.document_type_confidence for pa in page_analyses.values()) / len(page_analyses) if page_analyses else 0.0

    pages_with_code = [p for p, pa in page_analyses.items() if pa.code_blocks]
    pages_with_equations = [p for p, pa in page_analyses.items() if pa.equations]
    pages_with_diagrams = [p for p, pa in page_analyses.items() if pa.complex_diagrams]

    # Get conventions from first page (usually most reliable)
    first_analysis = page_analyses.get(min(page_analyses.keys())) if page_analyses else None

    return DocumentAnalysis(
        document_type=most_common_type,
        document_type_confidence=avg_confidence,
        total_pages=total_pages,
        pages_with_code=pages_with_code,
        pages_with_equations=pages_with_equations,
        pages_with_complex_diagrams=pages_with_diagrams,
        footnote_style=first_analysis.footnote_style if first_analysis else "none",
        citation_style=first_analysis.citation_style if first_analysis else "unknown",
        expected_sections=first_analysis.expected_sections if first_analysis else [],
        page_analyses=page_analyses,
    )
