"""Pipeline endpoint for versioned step-by-step PDF processing."""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..services.pipeline_viewer import PipelineViewerService
from ..services.pipeline_viewer_models import PipelineViewerResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])


def _sse_event(event_type: str, data: Any) -> str:
    """Format a server-sent event string."""
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


@router.post("/process")
async def process_pdf(
    file: UploadFile = File(...),
    images_scale: float = Form(default=2.0),
    do_table_structure: bool = Form(default=True),
    enable_structure: bool = Form(default=False),
    enable_page_content: bool = Form(default=False),
    enable_boundaries: bool = Form(default=False),
) -> dict[str, Any]:
    """Process a PDF through the versioned pipeline viewer.

    Synchronous dev endpoint: upload PDF, wait for extraction, get JSON back.
    Returns versioned markdown snapshots for each pipeline step.
    """


    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    images_scale = max(1.0, min(3.0, images_scale))

    logger.info(f"Pipeline Viewer: processing {file.filename} ({len(content)} bytes)")

    service = PipelineViewerService()
    result = await service.process(
        file_content=content,
        filename=file.filename,
        images_scale=images_scale,
        do_table_structure=do_table_structure,
        enable_structure=enable_structure,
        enable_page_content=enable_page_content,
        enable_boundaries=enable_boundaries,
    )

    return result.model_dump()


@router.post("/process/stream")
async def process_pdf_stream(
    file: UploadFile = File(...),
    images_scale: float = Form(default=2.0),
    do_table_structure: bool = Form(default=True),
) -> StreamingResponse:
    """Stream pipeline processing results via SSE.

    Always runs all phases (structure, page content, boundaries, cleanup).
    Each step's result is sent as it completes so the UI can render
    incrementally.

    SSE event types:
        init — After Docling extraction. Full result with page_images, figures.
        processing — Before each subsequent step starts.
        step — After each subsequent step completes (incremental data).
        error — If a step fails (non-fatal).
        done — Stream complete.
    """


    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    images_scale = max(1.0, min(3.0, images_scale))
    filename = file.filename

    logger.info(f"Pipeline Viewer (stream): processing {filename} ({len(content)} bytes)")

    async def event_generator() -> AsyncGenerator[str, None]:
        pipeline_start = time.time()
        total_steps = 1  # docling always runs

        service = PipelineViewerService()
        result = PipelineViewerResult(filename=filename, total_pages=0)

        # Step 1: Docling extraction
        try:
            await service._step_docling(result, content, filename, images_scale, do_table_structure)
        except Exception as e:
            logger.error(f"Docling extraction failed: {e}")
            yield _sse_event("error", {"step_name": "docling", "message": str(e)})
            yield _sse_event("done", {"total_steps": 0, "total_elapsed_ms": 0})
            return

        # Send full result after Docling (includes page_images, figures)
        yield _sse_event("init", result.model_dump())

        # Step 2: Structure analysis
        structure = None
        yield _sse_event("processing", {"step_name": "structure", "display_name": "Structure Analysis"})
        try:
            structure = await service._step_structure(result)
            total_steps += 1
            step = result.steps[-1]
            yield _sse_event("step", {
                "step": step.model_dump(),
                "new_versions": {},
                "new_page_markdowns": {},
            })
        except Exception as e:
            logger.error(f"Structure analysis failed: {e}")
            yield _sse_event("error", {"step_name": "structure", "message": str(e)})

        # Step 2b: Deterministic heading level fix
        if structure is not None:
            yield _sse_event("processing", {"step_name": "heading_levels", "display_name": "Heading Levels"})
            try:
                await service._step_heading_levels(result, structure)
                total_steps += 1
                step = result.steps[-1]
                yield _sse_event("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v0": result.versions.get("v0", "")},
                    "new_page_markdowns": {"v0": result.page_markdowns.get("v0", {})},
                })
            except Exception as e:
                logger.error(f"Heading level fix failed: {e}")
                yield _sse_event("error", {"step_name": "heading_levels", "message": str(e)})

        # Step 3: Page content corrections
        if structure is not None:
            yield _sse_event("processing", {"step_name": "page_content", "display_name": "Page Content Corrections"})
            try:
                await service._step_page_content(result, structure)
                total_steps += 1
                step = result.steps[-1]
                yield _sse_event("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v1": result.versions.get("v1", "")},
                    "new_page_markdowns": {"v1": result.page_markdowns.get("v1", {})},
                })
            except Exception as e:
                logger.error(f"Page content corrections failed: {e}")
                yield _sse_event("error", {"step_name": "page_content", "message": str(e)})

        # Step 3b: Deterministic code block language tagging
        if structure is not None:
            yield _sse_event("processing", {"step_name": "code_blocks", "display_name": "Code Block Languages"})
            try:
                await service._step_code_blocks(result, structure)
                total_steps += 1
                step = result.steps[-1]
                # Code blocks edit v1 (or v0) in-place — send updated version
                source_ver = "v1" if "v1" in result.page_markdowns else "v0"
                yield _sse_event("step", {
                    "step": step.model_dump(),
                    "new_versions": {source_ver: result.versions.get(source_ver, "")},
                    "new_page_markdowns": {source_ver: result.page_markdowns.get(source_ver, {})},
                })
            except Exception as e:
                logger.error(f"Code block language tagging failed: {e}")
                yield _sse_event("error", {"step_name": "code_blocks", "message": str(e)})

        # Step 4: Cross-page fixes (boundaries + footnotes)
        if structure is not None:
            yield _sse_event("processing", {"step_name": "boundaries", "display_name": "Cross-Page Fixes"})
            try:
                await service._step_boundaries(result, structure)
                total_steps += 1
                step = result.steps[-1]
                yield _sse_event("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v2": result.versions.get("v2", "")},
                    "new_page_markdowns": {},
                })
            except Exception as e:
                logger.error(f"Cross-page fixes failed: {e}")
                yield _sse_event("error", {"step_name": "boundaries", "message": str(e)})

        # Step 5: Cleanup
        if "v2" in result.versions:
            yield _sse_event("processing", {"step_name": "cleanup", "display_name": "Final Cleanup"})
            try:
                await service._step_cleanup(result)
                total_steps += 1
                step = result.steps[-1]
                yield _sse_event("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v3": result.versions.get("v3", "")},
                    "new_page_markdowns": {},
                })
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
                yield _sse_event("error", {"step_name": "cleanup", "message": str(e)})

        total_elapsed_ms = int((time.time() - pipeline_start) * 1000)
        yield _sse_event("done", {
            "total_steps": total_steps,
            "total_elapsed_ms": total_elapsed_ms,
            "total_input_tokens": sum(s.input_tokens for s in result.steps),
            "total_output_tokens": sum(s.output_tokens for s in result.steps),
            "total_cost_cents": sum(s.cost_cents for s in result.steps),
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
