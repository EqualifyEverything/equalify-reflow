"""Pipeline endpoint for versioned step-by-step PDF processing."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from ..services.feedback_client import feedback_client
from ..services.pdf_classifier import classify_pdf, enrich_classification
from ..services.pipeline_viewer import PipelineViewerService
from ..services.pipeline_viewer_models import PipelineViewerResult, StepResult
from ..services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])

_HEARTBEAT_INTERVAL_SECONDS = 10
_SSE_HEARTBEAT = ": heartbeat\n\n"


def _sse_event(event_type: str, data: Any, event_id: int | None = None) -> str:
    """Format a server-sent event string with optional ``id:`` field."""
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event_type}")
    parts.append(f"data: {json.dumps(data, default=str)}")
    return "\n".join(parts) + "\n\n"


def _slim_init_payload(result: PipelineViewerResult) -> dict[str, Any]:
    """Build an init payload with binary images stripped out.

    Removes page_images and figure image_base64 so the init event stays small.
    Images are streamed individually via page_image / figure_image events.
    """
    data = result.model_dump()
    data["page_images"] = {}
    data["figures"] = [
        {**fig, "image_base64": ""} for fig in data["figures"]
    ]
    return data


async def _heartbeats_until_done(task: asyncio.Task) -> AsyncGenerator[str, None]:
    """Yield SSE heartbeat comments while an asyncio task runs.

    Keeps the connection alive so the ALB does not consider it idle.
    """
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=_HEARTBEAT_INTERVAL_SECONDS)
        if not done:
            yield _SSE_HEARTBEAT


@router.post("/process")
async def process_pdf(
    file: UploadFile = File(...),
    images_scale: float = Form(default=2.0),
    do_table_structure: bool = Form(default=True),
    enable_structure: bool = Form(default=False),
    enable_page_content: bool = Form(default=False),
    enable_boundaries: bool = Form(default=False),
    ocr_languages: str = Form(
        default="eng",
        description="Comma-separated Tesseract OCR language codes (e.g. 'eng,deu')",
    ),
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

    ocr_lang_list = [lang.strip() for lang in ocr_languages.split(",")]

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
        ocr_languages=ocr_lang_list,
    )

    return result.model_dump()


@router.post("/process/stream")
async def process_pdf_stream(
    file: UploadFile = File(...),
    images_scale: float = Form(default=2.0),
    do_table_structure: bool = Form(default=True),
    ocr_languages: str = Form(
        default="eng",
        description="Comma-separated Tesseract OCR language codes (e.g. 'eng,deu')",
    ),
) -> StreamingResponse:
    """Stream pipeline processing results via SSE.

    Always runs all phases (structure, page content, boundaries, cleanup).
    Each step's result is sent as it completes so the UI can render
    incrementally.

    SSE event types:
        init — After Docling extraction. Metadata + markdown (no binary images).
        page_image — Individual page image (one per page, streamed after init).
        figure_image — Individual figure image (one per figure, streamed after init).
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
    ocr_lang_list = [lang.strip() for lang in ocr_languages.split(",")]

    logger.info(f"Pipeline Viewer (stream): processing {filename} ({len(content)} bytes)")

    # Fire-and-forget document upload to feedback service (zero added latency)
    doc_upload_task = asyncio.create_task(
        feedback_client.upload_document(content, filename)
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        pipeline_start = time.time()
        total_steps = 1  # docling always runs

        service = PipelineViewerService()
        result = PipelineViewerResult(filename=filename, total_pages=0)

        # Create session early for SSE reconnect support
        session = session_store.create_for_stream(filename)
        event_counter = 0

        def emit(event_type: str, data: Any) -> str:
            """Build an SSE string, auto-increment event ID, buffer for reconnect."""
            nonlocal event_counter
            eid = event_counter
            event_counter += 1
            sse_text = _sse_event(event_type, data, event_id=eid)
            session.event_buffer.append(sse_text)
            return sse_text

        # First event: tell the client which session to reconnect to
        yield emit("session", {"session_id": session.session_id})

        # Step 0: Pre-flight PDF classification
        classification = classify_pdf(content)

        if classification.has_errors:
            error_msg = "; ".join(classification.error_messages)
            result.warnings = classification.warning_messages
            result.steps.append(
                StepResult(
                    name="classification",
                    display_name="PDF Classification",
                    version_after="v0",
                    elapsed_ms=classification.elapsed_ms,
                    error=error_msg,
                    metadata={
                        "document_type": classification.document_type.value,
                        "findings": [f.model_dump() for f in classification.findings],
                        "pdf_metadata": classification.metadata.model_dump(),
                    },
                )
            )
            yield emit("init", _slim_init_payload(result))
            session.result = result
            session.status = "completed"
            yield emit("done", {"total_steps": 0, "total_elapsed_ms": classification.elapsed_ms})
            return

        result.warnings = classification.warning_messages

        # Step 1: Docling extraction
        try:
            task = asyncio.create_task(
                service._step_docling(result, content, filename, images_scale, do_table_structure)
            )
            async for hb in _heartbeats_until_done(task):
                yield hb
            task.result()
        except Exception as e:
            logger.error(f"Docling extraction failed: {e}")
            yield emit("error", {"step_name": "docling", "message": str(e)})
            session.result = result
            session.status = "error"
            yield emit("done", {"total_steps": 0, "total_elapsed_ms": 0})
            return

        # Enrich classification with post-extraction signals
        enrich_classification(
            classification,
            total_pages=result.total_pages,
            total_chars=result.stats.get("total_chars", 0),
            figure_count=result.stats.get("figure_count", 0),
            layout_hints=result.stats.get("layout_hints", {}),
        )
        result.warnings = classification.warning_messages
        result.stats["classification"] = {
            "document_type": classification.document_type.value,
            "findings_count": len(classification.findings),
            "elapsed_ms": classification.elapsed_ms,
        }

        # OCR re-run for scanned documents
        if result.stats.get("is_likely_scanned", False):
            from src.services.pdf_classifier import FINDING_SCANNED, FINDING_SCAN_PRODUCER

            scanned_codes = {FINDING_SCANNED, FINDING_SCAN_PRODUCER}
            finding_codes = {f.code for f in classification.findings}
            if finding_codes & scanned_codes:
                from src.config import settings as app_settings

                effective_langs = ocr_lang_list
                yield emit("processing", {
                    "step_name": "docling_ocr",
                    "display_name": "Tesseract OCR Re-extraction",
                })
                task = asyncio.create_task(
                    service._step_docling_ocr(
                        result, content, filename, images_scale, do_table_structure,
                        languages=effective_langs,
                    )
                )
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                # Update scanned finding to reflect that OCR was applied
                for finding in classification.findings:
                    if finding.code in scanned_codes:
                        finding.message = (
                            "Document appears to be scanned. Tesseract OCR has been "
                            "applied, which may increase processing time and introduce "
                            "character recognition errors."
                        )
                enrich_classification(
                    classification,
                    total_pages=result.total_pages,
                    total_chars=result.stats.get("total_chars", 0),
                    figure_count=result.stats.get("figure_count", 0),
                    layout_hints=result.stats.get("layout_hints", {}),
                )
                result.warnings = classification.warning_messages

        # Send slim init (metadata + markdown, no binary images)
        yield emit("init", _slim_init_payload(result))

        # Stream page images individually (~200-500KB each, not 5-20MB at once)
        for page_key, page_b64 in result.page_images.items():
            yield emit("page_image", {"page": page_key, "image_base64": page_b64})

        # Stream figure images individually
        for fig in result.figures:
            if fig.image_base64:
                yield emit("figure_image", {"ref_id": fig.ref_id, "image_base64": fig.image_base64})

        # Step 2: Structure analysis
        structure = None
        yield emit("processing", {"step_name": "structure", "display_name": "Structure Analysis"})
        try:
            task = asyncio.create_task(service._step_structure(result))
            async for hb in _heartbeats_until_done(task):
                yield hb
            structure = task.result()
            total_steps += 1
            step = result.steps[-1]
            yield emit("step", {
                "step": step.model_dump(),
                "new_versions": {},
                "new_page_markdowns": {},
            })
        except Exception as e:
            logger.error(f"Structure analysis failed: {e}")
            yield emit("error", {"step_name": "structure", "message": str(e)})

        # Step 2b: Heading reconciliation
        if structure is not None:
            yield emit("processing", {"step_name": "heading_reconciliation", "display_name": "Heading Reconciliation"})
            try:
                task = asyncio.create_task(service._step_heading_reconciliation(result, structure))
                async for hb in _heartbeats_until_done(task):
                    yield hb
                structure = task.result()
                total_steps += 1
                step = result.steps[-1]
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {},
                    "new_page_markdowns": {},
                })
            except Exception as e:
                logger.error(f"Heading reconciliation failed: {e}")
                yield emit("error", {"step_name": "heading_reconciliation", "message": str(e)})

        # Step 2c: Deterministic heading level fix
        if structure is not None:
            yield emit("processing", {"step_name": "heading_levels", "display_name": "Heading Levels"})
            try:
                task = asyncio.create_task(service._step_heading_levels(result, structure))
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                total_steps += 1
                step = result.steps[-1]
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v0": result.versions.get("v0", "")},
                    "new_page_markdowns": {"v0": result.page_markdowns.get("v0", {})},
                })
            except Exception as e:
                logger.error(f"Heading level fix failed: {e}")
                yield emit("error", {"step_name": "heading_levels", "message": str(e)})

        # Step 3: Page content corrections (with programmatic hints)
        section_map = None
        if structure is not None:
            yield emit("processing", {"step_name": "page_content", "display_name": "Page Content Corrections"})
            try:
                # Wrap section map + hints + LLM call in one task so heartbeats
                # flow the entire time (section map building can be slow).
                async def _page_content_with_prep():
                    nonlocal section_map
                    section_map = await asyncio.to_thread(
                        service._build_section_map, result, structure
                    )
                    page_hints = await asyncio.to_thread(
                        service._generate_page_hints, result, structure, section_map
                    )
                    await service._step_page_content(result, structure, page_hints=page_hints)

                task = asyncio.create_task(_page_content_with_prep())
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                total_steps += 1
                step = result.steps[-1]
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v1": result.versions.get("v1", "")},
                    "new_page_markdowns": {"v1": result.page_markdowns.get("v1", {})},
                })
            except Exception as e:
                logger.error(f"Page content corrections failed: {e}")
                yield emit("error", {"step_name": "page_content", "message": str(e)})

        # Step 3b: Deterministic code block language tagging
        if structure is not None:
            yield emit("processing", {"step_name": "code_blocks", "display_name": "Code Block Languages"})
            try:
                task = asyncio.create_task(service._step_code_blocks(result, structure))
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                total_steps += 1
                step = result.steps[-1]
                # Code blocks edit v1 (or v0) in-place — send updated version
                source_ver = "v1" if "v1" in result.page_markdowns else "v0"
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {source_ver: result.versions.get(source_ver, "")},
                    "new_page_markdowns": {source_ver: result.page_markdowns.get(source_ver, {})},
                })
            except Exception as e:
                logger.error(f"Code block language tagging failed: {e}")
                yield emit("error", {"step_name": "code_blocks", "message": str(e)})

        # Step 4: Cross-page fixes (boundaries + footnotes)
        if structure is not None:
            yield emit("processing", {"step_name": "boundaries", "display_name": "Cross-Page Fixes"})
            try:
                async def _boundaries_with_prep():
                    nonlocal section_map
                    if section_map is None:
                        section_map = await asyncio.to_thread(
                            service._build_section_map, result, structure
                        )
                    await service._step_boundaries(result, structure, section_map=section_map)

                task = asyncio.create_task(_boundaries_with_prep())
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                total_steps += 1
                step = result.steps[-1]
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v2": result.versions.get("v2", "")},
                    "new_page_markdowns": {},
                })
            except Exception as e:
                logger.error(f"Cross-page fixes failed: {e}")
                yield emit("error", {"step_name": "boundaries", "message": str(e)})

        # Step 5: Cleanup
        if "v2" in result.versions:
            yield emit("processing", {"step_name": "cleanup", "display_name": "Final Cleanup"})
            try:
                task = asyncio.create_task(service._step_cleanup(result))
                async for hb in _heartbeats_until_done(task):
                    yield hb
                task.result()
                total_steps += 1

                step = result.steps[-1]
                yield emit("step", {
                    "step": step.model_dump(),
                    "new_versions": {"v3": result.versions.get("v3", "")},
                    "new_page_markdowns": {},
                })
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
                yield emit("error", {"step_name": "cleanup", "message": str(e)})

        total_elapsed_ms = int((time.time() - pipeline_start) * 1000)

        # Await document upload (should be done by now — ran concurrently)
        document_ref: str | None = None
        try:
            document_ref = await doc_upload_task
        except Exception:
            logger.warning("Document upload task failed", exc_info=True)

        # Finalize session with results for feedback + reconnect
        session.result = result
        session.structure = structure
        session.section_map = section_map
        session.document_ref = document_ref
        session.status = "completed"

        yield emit("done", {
            "total_steps": total_steps,
            "total_elapsed_ms": total_elapsed_ms,
            "total_input_tokens": sum(s.input_tokens for s in result.steps),
            "total_output_tokens": sum(s.output_tokens for s in result.steps),
            "total_cost_cents": sum(s.cost_cents for s in result.steps),
            "session_id": session.session_id,
            "document_ref": document_ref,
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


_RECONNECT_POLL_INTERVAL = 10  # seconds between polls when still processing


@router.get("/sessions/{session_id}/stream")
async def reconnect_stream(
    session_id: str,
    last_event_id: int = Query(default=-1),
) -> StreamingResponse:
    """Reconnect to a processing session and replay buffered SSE events.

    The client sends the last event ID it received.  This endpoint replays
    all events after that ID.  If the session is still processing, it polls
    for new events with heartbeats until completion.
    """
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    async def replay_generator() -> AsyncGenerator[str, None]:
        cursor = last_event_id + 1

        while True:
            # Replay any buffered events beyond cursor
            buffer_len = len(session.event_buffer)
            while cursor < buffer_len:
                yield session.event_buffer[cursor]
                cursor += 1

            # If session is done, stop
            if session.status in ("completed", "error"):
                return

            # Still processing — wait for more events or timeout
            await asyncio.sleep(_RECONNECT_POLL_INTERVAL)
            yield _SSE_HEARTBEAT

    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
