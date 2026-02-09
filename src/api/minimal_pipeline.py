"""Dev-only minimal pipeline endpoint for incremental PDF processing.

SECURITY: Only available when ENVIRONMENT=dev
"""

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import settings
from ..services.minimal_pipeline import MinimalPipelineService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dev/minimal", tags=["Development - Minimal Pipeline"])


def _require_dev_mode() -> None:
    """Ensure endpoint only accessible in development."""
    if settings.environment != "dev":
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/process")
async def process_pdf(
    file: UploadFile = File(...),
    images_scale: float = Form(default=2.0),
    do_table_structure: bool = Form(default=True),
) -> dict[str, Any]:
    """Process a PDF through the minimal pipeline (Docling only).

    Synchronous dev endpoint: upload PDF, wait for extraction, get JSON back.
    Typically takes 10-30s depending on document size.

    Args:
        file: PDF file upload.
        images_scale: Scale factor for page images (1.0-3.0).
        do_table_structure: Run Docling table structure recognition.

    Returns:
        JSON with per-page markdown, images, figures, stats, and step timings.
    """
    _require_dev_mode()

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Clamp images_scale to valid range
    images_scale = max(1.0, min(3.0, images_scale))

    logger.info(f"Minimal pipeline: processing {file.filename} ({len(content)} bytes)")

    service = MinimalPipelineService()
    result = await service.process(
        file_content=content,
        filename=file.filename,
        images_scale=images_scale,
        do_table_structure=do_table_structure,
    )

    return {
        "filename": result.filename,
        "total_pages": result.total_pages,
        "pages": [
            {
                "page_number": p.page_number,
                "markdown": p.markdown,
                "image_base64": p.image_base64,
            }
            for p in result.pages
        ],
        "figures": [
            {
                "ref_id": f.ref_id,
                "caption": f.caption,
                "page_number": f.page_number,
                "image_base64": f.image_base64,
            }
            for f in result.figures
        ],
        "full_markdown": result.full_markdown,
        "steps_run": [
            {
                "name": s.name,
                "description": s.description,
                "elapsed_ms": s.elapsed_ms,
            }
            for s in result.steps_run
        ],
        "stats": result.stats,
    }
