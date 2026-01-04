"""Experimental endpoints for prototyping new processing approaches.

WARNING: These endpoints skip PII scanning and are for internal testing only.
Do not expose in production environments.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier
from ..agents.refine import (
    EditPatch,
    EditType,
    Hotspot,
    ProcessingTrace,
    RefineToolDeps,
    Requirements,
    refine_document,
)
from ..config import settings
from ..services.pdf_converter import PDFConverter, PDFConversionResult

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])


# =============================================================================
# Phase Helpers
# =============================================================================


async def _analyze_document(
    conversion_result: PDFConversionResult,
) -> Requirements:
    """Phase 2: ANALYZE - Generate requirements from document structure.

    Uses Haiku to analyze the first page and identify document characteristics.
    This is a simplified version for the experiment.
    """
    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    agent: Agent[None, Requirements] = Agent(
        model=model,
        output_type=Requirements,
        system_prompt="""You analyze PDF documents to extract requirements for accessibility remediation.

Examine the document and identify:
- Document type (syllabus, lecture notes, exam, etc.)
- Expected heading structure
- Number of figures and tables
- Key domain terms that should be spelled correctly
- Any complex elements that need special attention (hotspots)
- Style conventions used in the document""",
    )

    # Get first page image for analysis
    first_page = conversion_result.pages[0] if conversion_result.pages else None
    if not first_page or not first_page.image_base64:
        # Return minimal requirements if no image
        return Requirements(
            document_type="unknown",
            expected_headings=[],
            expected_figures=len([i for i in conversion_result.extracted_images if i.image_type == "picture"]),
            expected_tables=len([i for i in conversion_result.extracted_images if i.image_type == "table"]),
            key_terms=[],
            hotspots=[],
            style_conventions="",
        )

    # Decode image for analysis
    image_bytes = base64.b64decode(first_page.image_base64)

    # Build hotspots from extracted images
    hotspots = []
    for img in conversion_result.extracted_images:
        if img.bbox:
            hotspots.append(
                Hotspot(
                    page=img.page_num,
                    element_type="figure" if img.image_type == "picture" else "table",
                    bbox=img.bbox,
                    description=img.caption[:100] if img.caption else f"{img.image_type} on page {img.page_num}",
                    verification_hint="Verify transcription matches source",
                )
            )

    try:
        result = await agent.run(
            [
                f"Analyze this {conversion_result.total_pages}-page document and extract requirements.",
                BinaryContent(data=image_bytes, media_type="image/png"),
            ]
        )

        # Merge detected hotspots with any from analysis
        requirements = result.output
        requirements.hotspots.extend(hotspots)
        requirements.expected_figures = len([i for i in conversion_result.extracted_images if i.image_type == "picture"])
        requirements.expected_tables = len([i for i in conversion_result.extracted_images if i.image_type == "table"])

        logger.info(f"Analysis complete: {requirements.document_type}, {len(requirements.hotspots)} hotspots")
        return requirements

    except Exception as e:
        logger.warning(f"Analysis failed, using defaults: {e}")
        return Requirements(
            document_type="document",
            expected_headings=[],
            expected_figures=len([i for i in conversion_result.extracted_images if i.image_type == "picture"]),
            expected_tables=len([i for i in conversion_result.extracted_images if i.image_type == "table"]),
            key_terms=[],
            hotspots=hotspots,
            style_conventions="",
        )


async def _extract_page_markdown(
    page_num: int,
    image_base64: str,
    requirements: Requirements,
) -> str:
    """Phase 3: EXTRACT - Convert a single page image to markdown.

    Uses Haiku vision to transcribe page content.
    """
    model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])

    agent: Agent[None, str] = Agent(
        model=model,
        output_type=str,
        system_prompt="""You transcribe PDF page images to accessible markdown.

Rules:
- Transcribe text EXACTLY as written (verbatim)
- Use proper heading hierarchy (# for title, ## for sections, etc.)
- For images: use placeholder ![TODO: describe](image-page-N-M.png)
- For tables: use markdown table syntax with | delimiters
- Mark unclear text with [?]
- Output ONLY the markdown, no explanations""",
    )

    image_bytes = base64.b64decode(image_base64)

    try:
        result = await agent.run(
            [
                f"Transcribe page {page_num} of this {requirements.document_type}. "
                f"Expected elements: {requirements.expected_figures} figures, {requirements.expected_tables} tables.",
                BinaryContent(data=image_bytes, media_type="image/png"),
            ]
        )

        markdown = result.output
        logger.debug(f"Extracted page {page_num}: {len(markdown)} chars")
        return markdown

    except Exception as e:
        logger.error(f"Extraction failed for page {page_num}: {e}")
        return f"<!-- Page {page_num} extraction failed: {e} -->"


class ExperimentRequest(BaseModel):
    """Request configuration for experimental processing."""

    use_docling_extraction: bool = Field(
        default=True,
        description="If True, use Docling for clean PDFs; if False, use LLM vision",
    )
    max_refine_iterations: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum refinement iterations per page",
    )


class ExperimentResponse(BaseModel):
    """Response from experimental processing endpoint."""

    success: bool
    markdown: str = Field(default="", description="Final processed markdown")
    requirements: Requirements | None = Field(
        default=None, description="Extracted requirements from analysis phase"
    )
    trace: ProcessingTrace | None = Field(
        default=None, description="Processing trace with cost breakdown"
    )
    edit_history: list[EditPatch] = Field(
        default_factory=list,
        description="Complete history of all edits with reasoning",
    )
    edit_summary: dict[str, int] = Field(
        default_factory=dict,
        description="Count of edits by type (lint_fix, spell_fix, figure_alt, etc.)",
    )
    error: str | None = Field(default=None, description="Error message if failed")


@router.post(
    "/process",
    response_model=ExperimentResponse,
    status_code=status.HTTP_200_OK,
    summary="Process PDF with experimental pipeline",
    description="""
    Experimental endpoint for testing unified refine agent approach.

    **WARNING**: This endpoint:
    - Skips PII scanning (for experimentation only)
    - Uses Docling-first extraction for clean PDFs
    - Uses single refine agent with tool use

    Do not use with sensitive documents.
    """,
)
async def process_experiment(
    pdf_file: UploadFile = File(..., description="PDF file to process"),
) -> ExperimentResponse:
    """Process PDF with experimental unified refine agent pipeline.

    This endpoint implements the new architecture:
    1. INGEST: Docling extracts page images + bboxes
    2. ANALYZE: Generate requirements and identify hotspots
    3. EXTRACT: LLM vision transcription per page
    4. REFINE: Single agent with tool calls for figures/tables/verification
    5. OUTPUT: Final markdown with processing trace
    """
    # Validate file type
    if not pdf_file.filename or not pdf_file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF",
        )

    trace = ProcessingTrace(phases=[])

    try:
        # Read PDF content
        pdf_content = await pdf_file.read()
        logger.info(
            f"Received PDF for experimental processing: {pdf_file.filename} "
            f"({len(pdf_content)} bytes)"
        )

        # =====================================================================
        # Phase 1: INGEST - Extract page images and element bboxes
        # =====================================================================
        trace.phases.append("ingest")
        converter = PDFConverter()
        conversion_result = await converter.convert_with_page_images(pdf_content)

        logger.info(
            f"Phase 1 INGEST complete: {conversion_result.total_pages} pages, "
            f"{len(conversion_result.extracted_images)} images with bboxes, "
            f"is_scanned={conversion_result.is_scanned}, "
            f"docling_markdown={len(conversion_result.docling_markdown)} chars"
        )

        # =====================================================================
        # Phase 2: ANALYZE - Generate requirements from document
        # =====================================================================
        trace.phases.append("analyze")
        requirements = await _analyze_document(conversion_result)
        trace.total_llm_calls += 1

        logger.info(
            f"Phase 2 ANALYZE complete: {requirements.document_type}, "
            f"{len(requirements.hotspots)} hotspots identified"
        )

        # =====================================================================
        # Phase 3: EXTRACT - Docling-first for born-digital, LLM for scanned
        # =====================================================================
        trace.phases.append("extract")
        page_markdowns: dict[int, str] = {}

        if not conversion_result.is_scanned and conversion_result.docling_markdown:
            # Born-digital PDF: Use Docling markdown directly (FREE - no LLM calls)
            logger.info("Using Docling markdown extraction (born-digital PDF detected)")

            # Split Docling markdown by page breaks or distribute evenly
            # For now, we'll use the full document markdown for each page's refinement
            # The refine agent will handle page-specific content via images
            full_markdown = conversion_result.docling_markdown

            # Simple page split: assign full doc to page 1, empty to others
            # In practice, Docling doesn't provide per-page markdown easily
            # So we'll give the refine agent full context on page 1
            for page in conversion_result.pages:
                if page.page_num == 1:
                    page_markdowns[page.page_num] = full_markdown
                else:
                    # Placeholder - refine agent will use image verification
                    page_markdowns[page.page_num] = f"<!-- Page {page.page_num} - see full document -->"

            logger.info(
                f"Phase 3 EXTRACT complete: {len(page_markdowns)} pages from Docling "
                f"({len(full_markdown)} chars total, 0 LLM calls)"
            )
        else:
            # Scanned PDF: Use LLM vision transcription per page
            logger.info("Using LLM vision extraction (scanned PDF detected)")

            for page in conversion_result.pages:
                if page.image_base64:
                    markdown = await _extract_page_markdown(
                        page_num=page.page_num,
                        image_base64=page.image_base64,
                        requirements=requirements,
                    )
                    page_markdowns[page.page_num] = markdown
                    trace.total_llm_calls += 1

            logger.info(
                f"Phase 3 EXTRACT complete: {len(page_markdowns)} pages via LLM vision"
            )

        # =====================================================================
        # Phase 4: REFINE - Unified agent with tool use
        # =====================================================================
        trace.phases.append("refine")

        # Build page images dict (decode from base64 to PIL)
        from PIL import Image

        page_images: dict[int, Image.Image] = {}
        for page in conversion_result.pages:
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)
                page_images[page.page_num] = Image.open(BytesIO(image_bytes))

        # Build element bboxes dict from extracted images
        element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
        figure_counts: dict[int, int] = {}
        table_counts: dict[int, int] = {}

        for img in conversion_result.extracted_images:
            if img.bbox:
                if img.image_type == "picture":
                    idx = figure_counts.get(img.page_num, 0) + 1
                    figure_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "figure", idx)] = img.bbox
                else:
                    idx = table_counts.get(img.page_num, 0) + 1
                    table_counts[img.page_num] = idx
                    element_bboxes[(img.page_num, "table", idx)] = img.bbox

        # Get page width from first page (assumes all pages same width)
        # Default to 612 points (8.5" at 72 DPI) if not available
        page_width = 612.0
        if page_images:
            first_img = next(iter(page_images.values()))
            # Estimate based on typical PDF scaling
            page_width = first_img.width / converter.images_scale

        # Run refinement
        final_markdown, refine_trace = await refine_document(
            page_markdowns=page_markdowns,
            requirements=requirements,
            page_images=page_images,
            page_width=page_width,
            element_bboxes=element_bboxes,
        )

        # Merge refine trace into main trace
        trace.total_llm_calls += refine_trace.total_llm_calls
        trace.total_input_tokens += refine_trace.total_input_tokens
        trace.total_output_tokens += refine_trace.total_output_tokens
        trace.page_results = refine_trace.page_results
        trace.edit_history = refine_trace.edit_history

        logger.info(
            f"Phase 4 REFINE complete: {len(trace.edit_history)} total edits, "
            f"summary: {trace.edit_summary}"
        )

        # =====================================================================
        # Phase 5: OUTPUT - Return results
        # =====================================================================
        trace.phases.append("output")

        return ExperimentResponse(
            success=True,
            markdown=final_markdown,
            requirements=requirements,
            trace=trace,
            edit_history=trace.edit_history,
            edit_summary=trace.edit_summary,
        )

    except ValueError as e:
        logger.error(f"PDF processing failed: {e}")
        trace.errors.append(str(e))
        return ExperimentResponse(
            success=False,
            error=str(e),
            trace=trace,
        )
    except Exception as e:
        logger.exception(f"Unexpected error in experimental processing: {e}")
        trace.errors.append(f"Unexpected error: {str(e)}")
        return ExperimentResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            trace=trace,
        )
