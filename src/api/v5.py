"""V5 Pipeline API Endpoints.

Provides REST and SSE endpoints for the V5 document processing pipeline.

Endpoints:
    POST /api/v5/process - Start processing a PDF
    GET /api/v5/jobs/{job_id} - Get job status
    GET /api/v5/jobs/{job_id}/stream - SSE stream of events
    GET /api/v5/jobs/{job_id}/ledger - Get the change ledger
    GET /api/v5/jobs/{job_id}/result - Get final result
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v5", tags=["v5-pipeline"])


# =============================================================================
# In-Memory Job Storage (would be Redis in production)
# =============================================================================


class JobState(BaseModel):
    """State of a processing job."""

    job_id: str
    filename: str
    status: str  # pending, planning, executing, verifying, complete, failed
    progress: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Stats (populated as processing proceeds)
    total_pages: int = 0
    jobs_total: int = 0
    jobs_complete: int = 0
    ledger_entries: int = 0

    # Result (populated when complete)
    result: Any | None = None
    event_bus: Any | None = None
    error: str | None = None


# Simple in-memory store (use Redis in production)
_job_store: dict[str, JobState] = {}


def get_job(job_id: str) -> JobState | None:
    """Get job state by ID."""
    return _job_store.get(job_id)


def save_job(state: JobState) -> None:
    """Save job state."""
    _job_store[state.job_id] = state


# =============================================================================
# Request/Response Models
# =============================================================================


class ProcessingStarted(BaseModel):
    """Response when processing starts."""

    job_id: str
    status: str = "pending"
    stream_url: str
    status_url: str


class JobStatus(BaseModel):
    """Current job status."""

    job_id: str
    status: str
    progress: float
    total_pages: int
    jobs_total: int
    jobs_complete: int
    ledger_entries: int
    created_at: datetime
    error: str | None = None


class LedgerResponse(BaseModel):
    """Ledger response."""

    document_id: str
    entries: list[dict[str, Any]]
    total_entries: int


class ProcessingResultResponse(BaseModel):
    """Final processing result."""

    document_id: str
    success: bool
    final_markdown: str
    total_pages: int
    total_edits: int
    total_cost: float
    total_duration_ms: int
    verification_passed: bool
    verification_issues: list[str]


# =============================================================================
# Background Processing
# =============================================================================


async def process_in_background(
    job_id: str,
    file_content: bytes,
    filename: str,
    use_optimized: bool = False,
):
    """Run V5 processing in background.

    This is called by BackgroundTasks to run processing async.
    Updates job state and stores result when complete.

    For scanned PDFs (detected by low text extraction from Docling),
    automatically falls back to LLM vision extraction.

    Args:
        job_id: Unique job identifier
        file_content: PDF file bytes
        filename: Original filename
        use_optimized: If True, use optimized two-phase pipeline
    """
    import base64
    import time
    from io import BytesIO

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import DocumentStream
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from PIL import Image

    from src.agents.v5.events import (
        DoclingCompleteEvent,
        DoclingStartedEvent,
        EventBus,
        VisionExtractionCompleteEvent,
        VisionExtractionStartedEvent,
        VisionPageExtractedEvent,
    )
    from src.agents.v5.orchestrator import process_document_v5
    from src.agents.v5.orchestrator_optimized import process_document_v5_optimized
    from src.services.vision_extraction_service import (
        extract_all_pages_vision,
        is_scanned_pdf,
    )

    state = get_job(job_id)
    if not state:
        logger.error(f"Job {job_id} not found")
        return

    # Create event bus EARLY so streaming can connect immediately
    event_bus = EventBus(job_id)
    state.event_bus = event_bus
    state.status = "docling"
    save_job(state)

    try:
        # Emit Docling started event
        event_bus.emit(
            DoclingStartedEvent(
                document_id=job_id,
                filename=filename,
            )
        )

        # Convert PDF with Docling
        docling_start = time.time()
        logger.info(f"Converting PDF: {filename}")

        # Configure Docling
        pipeline_options = PdfPipelineOptions(
            do_ocr=False,  # We use LLM vision for scanned PDFs instead
            do_table_structure=True,
            generate_page_images=True,
            images_scale=2.0,
        )

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        # Convert from bytes using DocumentStream
        # Run in thread pool to avoid blocking event loop (Docling is synchronous)
        pdf_stream = BytesIO(file_content)
        doc_stream = DocumentStream(name=filename, stream=pdf_stream)
        result = await asyncio.to_thread(converter.convert, source=doc_stream)
        doc = result.document

        logger.info(f"Docling conversion: {len(doc.pages)} pages")

        # Extract full document markdown for scanned detection
        full_docling_markdown = ""
        try:
            full_docling_markdown = doc.export_to_markdown()
        except Exception as e:
            logger.warning(f"Failed to export full Docling markdown: {e}")

        # Extract page markdowns (from Docling)
        page_markdowns: dict[int, str] = {}
        for page_no in sorted(doc.pages.keys()):
            # Get markdown for this page
            page_md = doc.export_to_markdown(page_no=page_no)
            page_markdowns[page_no] = page_md

        # Extract page images (PIL)
        page_images: dict[int, Image.Image] = {}
        for page_no in sorted(doc.pages.keys()):
            page = doc.pages[page_no]
            if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                page_images[page_no] = page.image.pil_image

        logger.info(f"Extracted {len(page_images)} page images")

        # Detect if PDF is scanned
        total_pages = len(doc.pages)
        scanned = is_scanned_pdf(full_docling_markdown, total_pages)
        chars_per_page = len(full_docling_markdown) / max(total_pages, 1)

        # Detect if this is a visual document type (poster, infographic, etc.)
        # For single-page documents with mostly images, we should use vision extraction
        # even if they have some extractable text
        from src.services.vision_extraction_service import (
            VisualDocumentType,
            detect_visual_document_type,
        )

        is_visual_document = False
        visual_doc_type = VisualDocumentType.STANDARD

        # Check for visual document types on single-page documents
        if total_pages == 1 and len(page_images) > 0:
            # Convert first page to base64 for detection
            first_page_image = page_images.get(1) or next(iter(page_images.values()))
            buffer = BytesIO()
            first_page_image.save(buffer, format="PNG")
            first_page_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            visual_doc_type = await detect_visual_document_type(
                image_base64=first_page_base64,
                filename=filename,
                total_pages=total_pages,
            )

            is_visual_document = visual_doc_type != VisualDocumentType.STANDARD
            if is_visual_document:
                logger.info(f"Visual document detected: {visual_doc_type.value}")

        # Emit Docling complete event
        docling_duration_ms = int((time.time() - docling_start) * 1000)
        event_bus.emit(
            DoclingCompleteEvent(
                document_id=job_id,
                pages_extracted=len(page_markdowns),
                images_extracted=len(page_images),
                duration_ms=docling_duration_ms,
                is_scanned=scanned,
                chars_per_page=chars_per_page,
            )
        )

        # Use vision extraction for:
        # 1. Scanned PDFs (minimal text extracted)
        # 2. Visual documents (posters, infographics, etc.)
        use_vision_extraction = scanned or is_visual_document

        if use_vision_extraction:
            reason = (
                f"visual_document ({visual_doc_type.value})"
                if is_visual_document
                else f"scanned_pdf ({chars_per_page:.1f} chars/page)"
            )
            logger.info(f"Using LLM vision extraction: {reason}")

            # Emit vision extraction started
            event_bus.emit(
                VisionExtractionStartedEvent(
                    document_id=job_id,
                    total_pages=len(page_images),
                    reason=reason,
                )
            )

            state.status = "vision_extraction"
            save_job(state)

            # Convert PIL images to base64 for vision extraction
            page_images_base64: dict[int, str] = {}
            for page_no, pil_image in page_images.items():
                buffer = BytesIO()
                pil_image.save(buffer, format="PNG")
                page_images_base64[page_no] = base64.b64encode(buffer.getvalue()).decode("utf-8")

            # Callback for progress events
            def on_page_complete(page_num: int, markdown: str, success: bool):
                event_bus.emit(
                    VisionPageExtractedEvent(
                        document_id=job_id,
                        page_num=page_num,
                        chars_extracted=len(markdown),
                        success=success,
                    )
                )

            # Run vision extraction (with automatic visual document type detection)
            page_markdowns, vision_stats = await extract_all_pages_vision(
                page_images_base64=page_images_base64,
                document_type="course material",
                max_concurrent=3,
                on_page_complete=on_page_complete,
                filename=filename,
                auto_detect_visual_type=True,
            )

            # Emit vision extraction complete
            total_chars = sum(len(md) for md in page_markdowns.values())
            event_bus.emit(
                VisionExtractionCompleteEvent(
                    document_id=job_id,
                    pages_extracted=vision_stats.pages_extracted,
                    total_chars=total_chars,
                    total_tokens_input=vision_stats.total_tokens_input,
                    total_tokens_output=vision_stats.total_tokens_output,
                    estimated_cost=vision_stats.estimated_cost,
                    duration_ms=vision_stats.total_duration_ms,
                )
            )

            logger.info(
                f"Vision extraction complete: {vision_stats.pages_extracted} pages, "
                f"{total_chars} chars, ${vision_stats.estimated_cost:.4f}"
            )

        # Extract element bboxes
        element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
        figure_counts: dict[int, int] = {}
        table_counts: dict[int, int] = {}

        for item, level in doc.iterate_items():
            page_no = getattr(item, "page_no", 1)

            if hasattr(item, "label"):
                if item.label == "picture":
                    figure_counts[page_no] = figure_counts.get(page_no, 0) + 1
                    idx = figure_counts[page_no]
                    if hasattr(item, "prov") and item.prov:
                        bbox = item.prov[0].bbox
                        element_bboxes[(page_no, "figure", idx)] = (
                            bbox.l, bbox.t, bbox.r, bbox.b
                        )
                elif item.label == "table":
                    table_counts[page_no] = table_counts.get(page_no, 0) + 1
                    idx = table_counts[page_no]
                    if hasattr(item, "prov") and item.prov:
                        bbox = item.prov[0].bbox
                        element_bboxes[(page_no, "table", idx)] = (
                            bbox.l, bbox.t, bbox.r, bbox.b
                        )

        # Get page width
        page_width = 612.0  # Default letter size
        if doc.pages:
            first_page = doc.pages.get(1) or next(iter(doc.pages.values()), None)
            if first_page and hasattr(first_page, "size") and first_page.size:
                page_width = first_page.size.width

        state.total_pages = len(page_markdowns)
        state.status = "planning"
        save_job(state)

        # Run V5 pipeline (pass pre-created event_bus for streaming)
        if use_optimized:
            logger.info(f"Using OPTIMIZED pipeline for {filename}")
            processing_result, event_bus = await process_document_v5_optimized(
                filename=filename,
                page_markdowns=page_markdowns,
                page_images=page_images,
                document_id=job_id,
                event_bus=event_bus,
            )
        else:
            logger.info(f"Using ORIGINAL pipeline for {filename}")
            processing_result, event_bus = await process_document_v5(
                filename=filename,
                page_markdowns=page_markdowns,
                page_images=page_images,
                element_bboxes=element_bboxes,
                page_width=page_width,
                document_id=job_id,
                event_bus=event_bus,
            )

        # Update final state
        state.status = "complete" if processing_result.success else "failed"
        state.progress = 1.0
        state.jobs_total = processing_result.total_jobs
        state.jobs_complete = processing_result.total_jobs
        state.ledger_entries = processing_result.total_edits
        state.result = processing_result
        state.event_bus = event_bus
        save_job(state)

        logger.info(f"Job {job_id} complete: {processing_result.total_edits} edits")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        state.status = "failed"
        state.error = str(e)
        save_job(state)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/process", response_model=ProcessingStarted)
async def start_processing(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    optimized: bool = False,
):
    """Start processing a PDF document.

    Returns immediately with job_id. Use the stream endpoint
    to watch progress in real-time.

    Args:
        file: PDF file to process
        optimized: If True, use the optimized two-phase pipeline
                   (faster for multi-page documents)
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    job_id = str(uuid4())

    # Create job state
    state = JobState(
        job_id=job_id,
        filename=file.filename,
        status="pending",
    )
    save_job(state)

    # Read file content
    content = await file.read()

    # Start background processing
    background_tasks.add_task(
        process_in_background,
        job_id,
        content,
        file.filename,
        optimized,  # Pass optimized flag
    )

    return ProcessingStarted(
        job_id=job_id,
        stream_url=f"/api/v5/jobs/{job_id}/stream",
        status_url=f"/api/v5/jobs/{job_id}",
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    """Get current status of a processing job."""
    state = get_job(job_id)
    if not state:
        raise HTTPException(404, f"Job {job_id} not found")

    return JobStatus(
        job_id=state.job_id,
        status=state.status,
        progress=state.progress,
        total_pages=state.total_pages,
        jobs_total=state.jobs_total,
        jobs_complete=state.jobs_complete,
        ledger_entries=state.ledger_entries,
        created_at=state.created_at,
        error=state.error,
    )


@router.get("/jobs/{job_id}/stream")
async def stream_events(job_id: str):
    """Stream events for a job via Server-Sent Events.

    Connect to this endpoint to watch processing in real-time.
    Events include: planning progress, job creation, edits, etc.
    """
    state = get_job(job_id)
    if not state:
        raise HTTPException(404, f"Job {job_id} not found")

    async def event_generator():
        """Generate SSE events."""
        # If job is already complete, send final event
        if state.status in ("complete", "failed"):
            if state.event_bus:
                for event in state.event_bus.events:
                    yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"
            yield "event: done\ndata: {}\n\n"
            return

        # Wait for event bus to be available
        max_wait = 30  # seconds
        waited = 0
        while state.event_bus is None and waited < max_wait:
            await asyncio.sleep(0.5)
            waited += 0.5
            # Refresh state
            new_state = get_job(job_id)
            if new_state:
                state.event_bus = new_state.event_bus
                state.status = new_state.status

        if state.event_bus is None:
            yield "event: error\ndata: {\"message\": \"Event bus not available\"}\n\n"
            return

        # Subscribe to events
        queue = state.event_bus.subscribe()

        try:
            # First, send any events that already happened
            for event in state.event_bus.events:
                yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

            # Then stream new events
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

                    # Check if processing is complete
                    if event.event_type in ("processing:complete", "processing:error"):
                        break

                except TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"

                    # Check if job is done
                    new_state = get_job(job_id)
                    if new_state and new_state.status in ("complete", "failed"):
                        break

        finally:
            state.event_bus.unsubscribe(queue)

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs/{job_id}/ledger", response_model=LedgerResponse)
async def get_ledger(job_id: str):
    """Get the change ledger for a job."""
    state = get_job(job_id)
    if not state:
        raise HTTPException(404, f"Job {job_id} not found")

    if not state.result:
        raise HTTPException(400, "Job not yet complete")

    ledger = state.result.ledger

    return LedgerResponse(
        document_id=job_id,
        entries=[e.model_dump() for e in ledger.entries],
        total_entries=len(ledger.entries),
    )


@router.get("/jobs/{job_id}/result", response_model=ProcessingResultResponse)
async def get_result(job_id: str):
    """Get the final processing result.

    Only available after processing completes.
    """
    state = get_job(job_id)
    if not state:
        raise HTTPException(404, f"Job {job_id} not found")

    if state.status not in ("complete", "failed"):
        raise HTTPException(400, f"Job is still {state.status}")

    if not state.result:
        raise HTTPException(500, "Result not available")

    result = state.result

    return ProcessingResultResponse(
        document_id=job_id,
        success=result.success,
        final_markdown=result.final_markdown,
        total_pages=result.total_pages,
        total_edits=result.total_edits,
        total_cost=result.total_cost,
        total_duration_ms=result.total_duration_ms,
        verification_passed=result.verification.passed,
        verification_issues=result.verification.critical_issues,
    )


@router.get("/jobs/{job_id}/markdown")
async def get_markdown(job_id: str):
    """Get just the final markdown as plain text."""
    from urllib.parse import quote

    state = get_job(job_id)
    if not state:
        raise HTTPException(404, f"Job {job_id} not found")

    if state.status not in ("complete", "failed"):
        raise HTTPException(400, f"Job is still {state.status}")

    if not state.result:
        raise HTTPException(500, "Result not available")

    # Create ASCII-safe filename for Content-Disposition header
    # HTTP headers use latin-1 encoding, so non-ASCII chars cause errors
    safe_filename = state.filename.encode("ascii", "ignore").decode("ascii")
    if not safe_filename:
        safe_filename = "document"

    # Use RFC 5987 encoding for non-ASCII filenames (filename* parameter)
    # This provides both ASCII fallback and UTF-8 encoded version
    encoded_filename = quote(state.filename, safe="")

    return StreamingResponse(
        iter([state.result.final_markdown]),
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_filename}.md"; '
                f"filename*=UTF-8''{encoded_filename}.md"
            )
        },
    )
