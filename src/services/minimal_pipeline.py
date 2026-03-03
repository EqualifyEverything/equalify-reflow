"""Minimal step-based pipeline for incremental PDF processing.

Starts with raw Docling extraction and can grow one step at a time.
Each step is a named function that transforms the result in place.

Usage:
    service = MinimalPipelineService()
    result = await service.process(file_content, filename, images_scale=2.0)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

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
        """Run docling-serve extraction + pypdfium2 page rendering."""
        from .docling_response_parser import (
            extract_figures_from_json,
            get_page_info,
            split_markdown_by_page,
        )
        from .docling_serve_client import get_docling_client
        from .page_image_renderer import crop_figure_from_page_image, render_page_images

        step_start = time.time()
        client = get_docling_client()

        # Run docling-serve and page image rendering in parallel
        # Use placeholder mode — no embedded base64, keeps JSON response small
        docling_task = client.convert(
            file_content,
            filename,
            do_ocr=False,
            do_table_structure=do_table_structure,
            include_images=True,
            images_scale=images_scale,
            image_export_mode="placeholder",
            md_page_break_placeholder="<!-- PAGE_BREAK -->",
        )
        images_task = render_page_images(file_content, scale=images_scale)
        response, page_images = await asyncio.gather(docling_task, images_task)

        # Page count from JSON
        page_info = get_page_info(response.json_content)
        result.total_pages = page_info["page_count"]

        # Full document markdown
        result.full_markdown = response.md_content

        # Per-page markdown and images
        total_chars = 0
        page_mds = split_markdown_by_page(response.md_content)
        for page_key, page_md in sorted(page_mds.items(), key=lambda x: int(x[0])):
            total_chars += len(page_md)
            result.pages.append(
                PageResult(
                    page_number=int(page_key),
                    markdown=page_md,
                    image_base64=page_images.get(page_key),
                )
            )

        # Extract figures (crop from page renders using bbox)
        figure_dicts = extract_figures_from_json(response.json_content)
        for fig_dict in figure_dicts:
            page_key = str(fig_dict["page_number"])
            page_b64 = page_images.get(page_key)
            if not page_b64:
                continue
            try:
                cropped_b64 = crop_figure_from_page_image(
                    page_b64,
                    fig_dict["bbox"],
                    fig_dict["page_width"],
                    fig_dict["page_height"],
                )
            except Exception as e:
                logger.warning("Failed to crop figure %s: %s", fig_dict["ref_id"], e)
                continue
            result.figures.append(
                FigureResult(
                    ref_id=fig_dict["ref_id"],
                    caption=fig_dict["caption"],
                    page_number=fig_dict["page_number"],
                    image_base64=cropped_b64,
                )
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps_run.append(
            PipelineStep(
                name="docling",
                description=(
                    "PDF extraction with docling-serve"
                    " (page images via pypdfium2, table structure, figure extraction)"
                ),
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
