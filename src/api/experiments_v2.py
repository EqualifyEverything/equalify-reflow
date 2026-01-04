"""Experimental endpoint v2: Pipeline-based processing.

This implements the architectural pivot from agentic refinement
to deterministic traversal with LLM-assisted decision points.

Key differences from v1:
- No agent loops
- Deterministic element traversal
- Focused, bounded LLM calls
- Rich provenance tracking
- Structured transformations for human review
- Image assets with S3 URLs
- Footnote linking
"""

from __future__ import annotations

import logging
import uuid
from io import BytesIO

from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import DocumentStream
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from PIL import Image
from pydantic import BaseModel, Field

from src.agents.pipeline import (
    ProcessingResult,
    build_transformations,
    link_footnotes,
    process_document,
)
from src.agents.pipeline.analyzer import DocumentAnalysis, PageAnalysis, analyze_document
from src.api.schemas import ImageAsset, PipelineV2Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments/v2", tags=["Experiments V2"])


class PipelineResponse(BaseModel):
    """Response from pipeline processing endpoint."""

    success: bool
    markdown: str = Field(default="", description="Final markdown with provenance")
    markdown_clean: str = Field(default="", description="Markdown without provenance comments")

    # Stats
    total_elements: int = 0
    figures_processed: int = 0
    tables_processed: int = 0
    headings_validated: int = 0
    formulas_transcribed: int = 0
    code_blocks_transcribed: int = 0
    llm_calls: int = 0
    llm_failures: int = 0  # Failed LLM calls that used fallbacks

    # Analysis results
    document_type: str = ""
    pages_with_code: list[int] = Field(default_factory=list)
    pages_with_equations: list[int] = Field(default_factory=list)

    # Transformation summary
    transformations: dict[str, int] = Field(default_factory=dict)

    # Errors
    errors: list[str] = Field(default_factory=list)
    error: str | None = None


@router.post(
    "/process",
    response_model=PipelineResponse,
    status_code=status.HTTP_200_OK,
    summary="Process PDF with pipeline approach",
    description="""
    Pipeline-based processing with deterministic traversal.

    Key features:
    - Analysis phase identifies document type and hotspots (code, equations)
    - Walks Docling's document tree (no markdown flattening)
    - Focused LLM calls for specific tasks
    - Formula transcription to LaTeX with accessible plain text
    - Code block detection and proper formatting
    - Rich provenance tracking
    - No agent loops or "decide what to do" patterns

    Returns markdown with embedded provenance comments.
    """,
)
async def process_pipeline(
    pdf_file: UploadFile = File(..., description="PDF file to process"),
    validate_headings: bool = True,
    generate_alt_texts: bool = True,
    analyze_tables: bool = True,
    classify_lists: bool = False,  # Off by default (noisy for many docs)
    transcribe_formulas: bool = True,
    transcribe_code: bool = True,
    run_analysis: bool = True,  # Whether to run analysis phase first
) -> PipelineResponse:
    """Process PDF with pipeline-based approach."""

    # Validate file type
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    try:
        # Read PDF content
        pdf_content = await pdf_file.read()
        logger.info(
            f"Pipeline processing: {pdf_file.filename} ({len(pdf_content)} bytes)"
        )

        # Configure Docling
        pipeline_options = PdfPipelineOptions(
            generate_page_images=True,
            generate_picture_images=True,
            images_scale=1.5,
            do_ocr=False,
            do_table_structure=True,  # Enable for better table extraction
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        # Convert PDF
        pdf_stream = BytesIO(pdf_content)
        doc_stream = DocumentStream(name="document.pdf", stream=pdf_stream)
        result = converter.convert(source=doc_stream)
        doc = result.document

        logger.info(f"Docling conversion: {len(doc.pages)} pages")

        # Extract page images
        page_images: dict[int, Image.Image] = {}
        for page_no in sorted(doc.pages.keys()):
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                page_images[page_no] = page.image.pil_image

        logger.info(f"Extracted {len(page_images)} page images")

        # Run analysis phase to identify document type and hotspots
        analysis: DocumentAnalysis | None = None
        if run_analysis and page_images:
            logger.info("Running analysis phase...")
            analysis = await analyze_document(page_images)
            logger.info(
                f"Analysis complete: type={analysis.document_type}, "
                f"code_pages={analysis.pages_with_code}, "
                f"equation_pages={analysis.pages_with_equations}"
            )

        # Get document type from analysis or use default
        document_type = analysis.document_type if analysis else "document"

        # Process with pipeline
        processing_result: ProcessingResult = await process_document(
            doc=doc,
            page_images=page_images,
            document_type=document_type,
            validate_headings=validate_headings,
            generate_alt_texts=generate_alt_texts,
            analyze_tables=analyze_tables,
            classify_lists=classify_lists,
            transcribe_formulas=transcribe_formulas,
            transcribe_code=transcribe_code,
            analysis=analysis,
        )

        # Generate outputs
        markdown_with_provenance = processing_result.to_markdown(include_provenance=True)
        markdown_clean = processing_result.to_markdown(include_provenance=False)

        return PipelineResponse(
            success=processing_result.llm_failures == 0,  # Only success if no failures
            markdown=markdown_with_provenance,
            markdown_clean=markdown_clean,
            total_elements=processing_result.total_elements,
            figures_processed=processing_result.figures_processed,
            tables_processed=processing_result.tables_processed,
            headings_validated=processing_result.headings_validated,
            formulas_transcribed=processing_result.formulas_transcribed,
            code_blocks_transcribed=processing_result.code_blocks_transcribed,
            llm_calls=processing_result.llm_calls,
            llm_failures=processing_result.llm_failures,
            document_type=document_type,
            pages_with_code=analysis.pages_with_code if analysis else [],
            pages_with_equations=analysis.pages_with_equations if analysis else [],
            transformations=processing_result.transformation_summary,
            errors=processing_result.errors,
        )

    except Exception as e:
        logger.exception(f"Pipeline processing failed: {e}")
        return PipelineResponse(
            success=False,
            error=str(e),
            errors=[str(e)],
        )


# =============================================================================
# Analysis Endpoint (for testing the analysis phase)
# =============================================================================


class AnalyzeResponse(BaseModel):
    """Response from analysis endpoint."""

    success: bool
    document_type: str = ""
    document_type_confidence: float = 0.0
    total_pages: int = 0
    pages_analyzed: int = 0

    # Hotspot summary
    pages_with_code: list[int] = Field(default_factory=list)
    pages_with_equations: list[int] = Field(default_factory=list)
    pages_with_complex_diagrams: list[int] = Field(default_factory=list)

    # Conventions
    footnote_style: str = "none"
    citation_style: str = "unknown"
    expected_sections: list[str] = Field(default_factory=list)

    # Detailed per-page analysis
    page_analyses: dict[int, PageAnalysis] = Field(default_factory=dict)

    # Errors
    error: str | None = None


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze PDF to identify hotspots",
    description="""
    Analyzes a PDF to identify:
    - Document type (academic paper, syllabus, etc.)
    - Code blocks, equations, complex diagrams
    - Footnote and citation styles
    - Expected sections

    This is the "router" phase that tells us what needs LLM transcription
    vs what Docling can handle.
    """,
)
async def analyze_pdf(
    pdf_file: UploadFile = File(..., description="PDF file to analyze"),
) -> AnalyzeResponse:
    """Analyze PDF to identify hotspots and conventions."""

    # Validate file type
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    try:
        # Read PDF content
        pdf_content = await pdf_file.read()
        logger.info(f"Analyzing: {pdf_file.filename} ({len(pdf_content)} bytes)")

        # Configure Docling (just for page images)
        pipeline_options = PdfPipelineOptions(
            generate_page_images=True,
            generate_picture_images=False,  # Don't need these for analysis
            images_scale=1.0,  # Lower res is fine for analysis
            do_ocr=False,
            do_table_structure=False,
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        # Convert PDF
        pdf_stream = BytesIO(pdf_content)
        doc_stream = DocumentStream(name="document.pdf", stream=pdf_stream)
        result = converter.convert(source=doc_stream)
        doc = result.document

        logger.info(f"Docling conversion: {len(doc.pages)} pages")

        # Extract page images
        page_images: dict[int, Image.Image] = {}
        for page_no in sorted(doc.pages.keys()):
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                page_images[page_no] = page.image.pil_image

        logger.info(f"Extracted {len(page_images)} page images")

        # Run analysis
        analysis: DocumentAnalysis = await analyze_document(page_images)

        return AnalyzeResponse(
            success=True,
            document_type=analysis.document_type,
            document_type_confidence=analysis.document_type_confidence,
            total_pages=analysis.total_pages,
            pages_analyzed=len(analysis.page_analyses),
            pages_with_code=analysis.pages_with_code,
            pages_with_equations=analysis.pages_with_equations,
            pages_with_complex_diagrams=analysis.pages_with_complex_diagrams,
            footnote_style=analysis.footnote_style,
            citation_style=analysis.citation_style,
            expected_sections=analysis.expected_sections,
            page_analyses=analysis.page_analyses,
        )

    except Exception as e:
        logger.exception(f"Analysis failed: {e}")
        return AnalyzeResponse(
            success=False,
            error=str(e),
        )


# =============================================================================
# Enhanced Pipeline Endpoint with Structured Transformations
# =============================================================================


@router.post(
    "/process-enhanced",
    response_model=PipelineV2Response,
    status_code=status.HTTP_200_OK,
    summary="Process PDF with structured transformations",
    description="""
    Enhanced pipeline processing with structured output for human review.

    Key enhancements over /process:
    - Structured transformations list with before/after for each AI change
    - Footnote linking (markers to definitions)
    - Citation and footnote style detection
    - Ready for asset extraction with S3 URLs (future)

    Returns structured data suitable for human-in-the-loop review workflow.
    """,
)
async def process_pipeline_enhanced(
    pdf_file: UploadFile = File(..., description="PDF file to process"),
    validate_headings: bool = True,
    generate_alt_texts: bool = True,
    analyze_tables: bool = True,
    classify_lists: bool = False,
    transcribe_formulas: bool = True,
    transcribe_code: bool = True,
    run_analysis: bool = True,
) -> PipelineV2Response:
    """Process PDF with enhanced structured output for review."""

    # Validate file type
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    try:
        # Generate job ID for this processing run
        job_id = str(uuid.uuid4())

        # Read PDF content
        pdf_content = await pdf_file.read()
        logger.info(
            f"Enhanced pipeline processing: {pdf_file.filename} "
            f"({len(pdf_content)} bytes) job_id={job_id}"
        )

        # Configure Docling
        pipeline_options = PdfPipelineOptions(
            generate_page_images=True,
            generate_picture_images=True,
            images_scale=1.5,
            do_ocr=False,
            do_table_structure=True,
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        # Convert PDF
        pdf_stream = BytesIO(pdf_content)
        doc_stream = DocumentStream(name="document.pdf", stream=pdf_stream)
        result = converter.convert(source=doc_stream)
        doc = result.document

        logger.info(f"Docling conversion: {len(doc.pages)} pages")

        # Extract page images
        page_images: dict[int, Image.Image] = {}
        for page_no in sorted(doc.pages.keys()):
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                page_images[page_no] = page.image.pil_image

        logger.info(f"Extracted {len(page_images)} page images")

        # Run analysis phase
        analysis: DocumentAnalysis | None = None
        if run_analysis and page_images:
            logger.info("Running analysis phase...")
            analysis = await analyze_document(page_images)
            logger.info(
                f"Analysis complete: type={analysis.document_type}, "
                f"code_pages={analysis.pages_with_code}, "
                f"equation_pages={analysis.pages_with_equations}, "
                f"footnote_style={analysis.footnote_style}"
            )

        # Get document type and styles from analysis
        document_type = analysis.document_type if analysis else "document"
        footnote_style = analysis.footnote_style if analysis else "none"
        citation_style = analysis.citation_style if analysis else "unknown"

        # Process with pipeline
        processing_result: ProcessingResult = await process_document(
            doc=doc,
            page_images=page_images,
            document_type=document_type,
            validate_headings=validate_headings,
            generate_alt_texts=generate_alt_texts,
            analyze_tables=analyze_tables,
            classify_lists=classify_lists,
            transcribe_formulas=transcribe_formulas,
            transcribe_code=transcribe_code,
            analysis=analysis,
        )

        # Build structured transformations for human review
        transformations = build_transformations(processing_result.elements)
        logger.info(f"Built {len(transformations)} structured transformations")

        # Link footnotes
        footnote_links = link_footnotes(
            processing_result.elements,
            footnote_style=footnote_style,
        )
        logger.info(f"Linked {len(footnote_links)} footnotes")

        # Store footnote links on result for potential later use
        processing_result.footnote_links = footnote_links

        # Generate markdown outputs
        markdown_with_provenance = processing_result.to_markdown(include_provenance=True)
        markdown_clean = processing_result.to_markdown(include_provenance=False)

        return PipelineV2Response(
            success=processing_result.llm_failures == 0,
            markdown=markdown_with_provenance,
            markdown_clean=markdown_clean,
            total_elements=processing_result.total_elements,
            figures_processed=processing_result.figures_processed,
            tables_processed=processing_result.tables_processed,
            headings_validated=processing_result.headings_validated,
            formulas_transcribed=processing_result.formulas_transcribed,
            code_blocks_transcribed=processing_result.code_blocks_transcribed,
            llm_calls=processing_result.llm_calls,
            llm_failures=processing_result.llm_failures,
            document_type=document_type,
            pages_with_code=analysis.pages_with_code if analysis else [],
            pages_with_equations=analysis.pages_with_equations if analysis else [],
            transformations=transformations,
            assets={},  # TODO: Add asset extraction with S3 integration
            footnotes=footnote_links,
            citation_style=citation_style,
            footnote_style=footnote_style,
            errors=processing_result.errors,
        )

    except Exception as e:
        logger.exception(f"Enhanced pipeline processing failed: {e}")
        return PipelineV2Response(
            success=False,
            error=str(e),
            errors=[str(e)],
        )
