"""Minimal step-based pipeline for incremental PDF processing.

Starts with raw Docling extraction and can grow one step at a time.
Each step is a named function that transforms the result in place.

Usage:
    service = MinimalPipelineService()
    result = await service.process(file_content, filename, images_scale=2.0)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from io import BytesIO

logger = logging.getLogger(__name__)


@dataclass
class PipelineStep:
    """Record of a single pipeline step that ran."""

    name: str
    description: str
    elapsed_ms: int


@dataclass
class PageResult:
    """Extraction result for a single page."""

    page_number: int
    markdown: str
    image_base64: str | None = None  # PNG encoded as base64


@dataclass
class FigureResult:
    """An extracted figure/picture from the document."""

    ref_id: str
    caption: str
    page_number: int
    image_base64: str  # PNG encoded as base64


@dataclass
class MinimalPipelineResult:
    """Complete result from the minimal pipeline."""

    filename: str
    total_pages: int
    pages: list[PageResult] = field(default_factory=list)
    figures: list[FigureResult] = field(default_factory=list)
    full_markdown: str = ""
    steps_run: list[PipelineStep] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _pil_to_base64(image: object) -> str:
    """Convert a PIL Image to base64-encoded PNG string."""
    buf = BytesIO()
    image.save(buf, format="PNG")  # type: ignore[union-attr]
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class MinimalPipelineService:
    """Step-based pipeline starting with Docling extraction.

    Phase 1: Docling only.
    Future: add run_table_agent(), run_heading_agent(), etc.
    """

    async def process(
        self,
        file_content: bytes,
        filename: str,
        *,
        images_scale: float = 2.0,
        do_table_structure: bool = True,
    ) -> MinimalPipelineResult:
        """Run the minimal pipeline on a PDF.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename.
            images_scale: Scale factor for page image generation.
            do_table_structure: Whether to run Docling table structure recognition.

        Returns:
            MinimalPipelineResult with per-page markdown, images, figures, and stats.
        """
        result = MinimalPipelineResult(filename=filename, total_pages=0)

        # Step 1: Docling extraction (always runs)
        await self._step_docling(result, file_content, filename, images_scale, do_table_structure)

        # Future steps would go here, gated by request params:
        # if enable_table_agent:
        #     await self._step_table_agent(result)

        return result

    async def _step_docling(
        self,
        result: MinimalPipelineResult,
        file_content: bytes,
        filename: str,
        images_scale: float,
        do_table_structure: bool,
    ) -> None:
        """Run Docling PDF extraction."""
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.document import DocumentStream  # type: ignore[attr-defined]
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        step_start = time.time()

        pipeline_options = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=do_table_structure,
            generate_page_images=True,
            generate_picture_images=True,
            images_scale=images_scale,
        )

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

        pdf_stream = BytesIO(file_content)
        doc_stream = DocumentStream(name=filename, stream=pdf_stream)
        conv_result = await asyncio.to_thread(converter.convert, source=doc_stream)
        doc = conv_result.document

        result.total_pages = len(doc.pages)

        # Full document markdown
        try:
            result.full_markdown = doc.export_to_markdown()
        except Exception as e:
            logger.warning(f"Failed to export full markdown: {e}")
            result.full_markdown = ""

        # Per-page markdown and images
        total_chars = 0
        for page_no in sorted(doc.pages.keys()):
            page_md = doc.export_to_markdown(page_no=page_no)
            total_chars += len(page_md)

            # Extract page image
            page_image_b64 = None
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                page_image_b64 = _pil_to_base64(page.image.pil_image)

            result.pages.append(
                PageResult(
                    page_number=page_no,
                    markdown=page_md,
                    image_base64=page_image_b64,
                )
            )

        # Extract figures
        for pic in doc.pictures:
            if pic.image and hasattr(pic.image, "pil_image") and pic.image.pil_image:
                result.figures.append(
                    FigureResult(
                        ref_id=str(pic.self_ref) if pic.self_ref else "",
                        caption=pic.caption_text(doc=doc) or "",
                        page_number=pic.prov[0].page_no if pic.prov else 1,
                        image_base64=_pil_to_base64(pic.image.pil_image),
                    )
                )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps_run.append(
            PipelineStep(
                name="docling",
                description="PDF extraction with Docling (page images, table structure, figure extraction)",
                elapsed_ms=elapsed_ms,
            )
        )

        # Compute stats
        chars_per_page = total_chars / result.total_pages if result.total_pages > 0 else 0
        is_likely_scanned = chars_per_page < 50 and result.total_pages > 0

        result.stats = {
            "total_chars": total_chars,
            "chars_per_page": round(chars_per_page, 1),
            "is_likely_scanned": is_likely_scanned,
            "figure_count": len(result.figures),
            "images_scale": images_scale,
            "do_table_structure": do_table_structure,
            "total_elapsed_ms": elapsed_ms,
        }
