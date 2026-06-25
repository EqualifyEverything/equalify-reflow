"""Document processing endpoints."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..dependencies import (
    get_job_service,
    get_queue_service,
    get_redis_client,
    get_s3_url_service,
    get_storage_service,
)
from ..services import JobService, QueueService, S3URLService, StorageService
from ..services.approval_service import ApprovalService
from ..services.document_processing_service import DocumentProcessingService
from ..services.metrics_service import jobs_submitted_total
from .schemas import (
    AgenticCompletedResponse,
    AgenticProcessingResponse,
    AwaitingPIIApprovalResponse,
    CompletedResponse,
    DeniedResponse,
    DocumentStatusResponse,
    FailedResponse,
    FigureAsset,
    LedgerEntryResponse,
    LedgerPageGroup,
    LedgerResponse,
    LLMCallInfo,
    LLMCostInfo,
    PIIFinding,
    PIIScanningResponse,
    ProcessingResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


def _build_llm_cost(job: dict[str, Any]) -> LLMCostInfo | None:
    """Build LLM cost info from job data.

    Costs accumulate across all processing phases (structure analysis + transcription).
    """
    llm_cost_cents = job.get("llm_cost_cents")
    if llm_cost_cents is None:
        return None

    try:
        total_cents = float(llm_cost_cents)
    except (TypeError, ValueError):
        return None

    # Get aggregate token counts (accumulated across all phases)
    input_tokens = int(job.get("llm_input_tokens", 0) or 0)
    output_tokens = int(job.get("llm_output_tokens", 0) or 0)
    total_tokens = int(job.get("llm_total_tokens", 0) or 0)

    # Parse individual LLM calls from job data
    llm_calls_raw = job.get("llm_calls")
    calls: list[LLMCallInfo] = []

    if llm_calls_raw:
        # llm_calls is stored as JSON string in Redis
        if isinstance(llm_calls_raw, str):
            try:
                llm_calls_raw = json.loads(llm_calls_raw)
            except json.JSONDecodeError:
                llm_calls_raw = []

        if isinstance(llm_calls_raw, list):
            for call in llm_calls_raw:
                if isinstance(call, dict):
                    calls.append(
                        LLMCallInfo(
                            agent=call.get("agent", ""),
                            purpose=call.get("purpose", ""),
                            page=call.get("page"),
                            input_tokens=call.get("input_tokens", 0),
                            output_tokens=call.get("output_tokens", 0),
                            cost_cents=call.get("cost_cents", 0.0),
                            timestamp=call.get("timestamp", ""),
                            duration_ms=call.get("duration_ms"),
                        )
                    )

    return LLMCostInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_cents=total_cents,
        estimated_cost_dollars=total_cents / 100.0,
        calls=calls,
    )


class JobSubmissionResponse(BaseModel):
    """Response for document submission."""

    job_id: str
    status: str
    estimated_completion_minutes: int
    created_at: str
    stream_url: str | None = None


@router.post("/submit", response_model=JobSubmissionResponse, status_code=status.HTTP_201_CREATED)
async def submit_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    skip_pii_scan: bool = Form(default=False, description="Skip PII scanning and use agentic pipeline directly"),
    skip_reason: str | None = Form(default=None, description="Optional reason for skipping PII scan (for audit trail)"),
    review_mode: Literal["auto", "human"] = Form(
        default="auto",
        description="Review mode: 'auto' (immediate completion) or 'human' (ledger available for PR-like review)",
    ),
    generate_debug_bundle: bool = Form(
        default=False, description="Generate debug bundle with all agent prompts and responses"
    ),
    ocr_languages: str | None = Form(
        default=None,
        description="Comma-separated Tesseract OCR language codes for scanned documents (e.g. 'eng,deu'). Defaults to 'eng'.",
    ),
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    job_service: JobService = Depends(get_job_service),
    redis_client: Any = Depends(get_redis_client),
    s3_url_service: S3URLService = Depends(get_s3_url_service),
) -> JobSubmissionResponse:
    """Submit a PDF document for processing.

    Args:
        file: PDF file to process
        skip_pii_scan: If True, bypass PII scanning and use agentic pipeline directly
        skip_reason: Optional justification for skipping PII scan (recorded in audit trail)
        review_mode: 'auto' (immediate completion) or 'human' (ledger available for review)
        generate_debug_bundle: If True, save all agent prompts/responses for debugging
    """
    # Parse and validate OCR languages
    ocr_lang_list = [lang.strip() for lang in ocr_languages.split(",")] if ocr_languages else None
    if ocr_lang_list:
        from ..utils.ocr_languages import validate_ocr_languages

        invalid = validate_ocr_languages(ocr_lang_list)
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid OCR language code(s): {', '.join(invalid)}. Use Tesseract language codes (e.g. 'eng', 'deu', 'fra').",
            )

    job_id, s3_key = await storage.store_document(file)

    if skip_pii_scan:
        # Use agentic pipeline directly (bypass PII scanning)
        await job_service.create_job(
            job_id,
            s3_key,
            status="processing",
            original_filename=file.filename,
            pii_skipped=True,
            pii_skip_reason=skip_reason or "User requested PII scan skip",
            debug_bundle_requested=generate_debug_bundle,
            review_mode=review_mode,
            ocr_languages=ocr_lang_list,
        )

        # Pre-warm docling (best-effort, production only)
        if settings.environment == "production":
            try:
                import boto3

                cw = boto3.client("cloudwatch", region_name=settings.aws_region)
                cw.put_metric_data(
                    Namespace="EqualifyPDF",
                    MetricData=[{"MetricName": "JobsAwaitingProcessing", "Value": 1, "Unit": "Count"}],
                )
            except Exception:
                pass

        # Record job submission metric
        jobs_submitted_total.labels(source="api").inc()

        # Use DocumentProcessingService for agentic pipeline
        processing_service = DocumentProcessingService(
            redis_client=redis_client,
            storage_service=storage,
            s3_url_service=s3_url_service,
        )

        # Run processing in background
        background_tasks.add_task(
            processing_service.process_document,
            job_id=job_id,
            s3_key=s3_key,
            filename=file.filename or "document.pdf",
            review_mode=review_mode,
            ocr_languages=ocr_lang_list,
        )

        return JobSubmissionResponse(
            job_id=job_id,
            status="processing",
            estimated_completion_minutes=settings.estimated_processing_minutes,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            stream_url=f"/api/v1/documents/{job_id}/stream",
        )
    else:
        # Standard flow: PII scanning first
        await job_service.create_job(
            job_id,
            s3_key,
            status="pii_scanning",
            original_filename=file.filename,
            debug_bundle_requested=generate_debug_bundle,
            review_mode=review_mode,
            ocr_languages=ocr_lang_list,
        )
        await queue.queue_pii_job(job_id, s3_key)

        # Pre-warm docling (best-effort, production only)
        if settings.environment == "production":
            try:
                import boto3

                cw = boto3.client("cloudwatch", region_name=settings.aws_region)
                cw.put_metric_data(
                    Namespace="EqualifyPDF",
                    MetricData=[{"MetricName": "JobsAwaitingProcessing", "Value": 1, "Unit": "Count"}],
                )
            except Exception:
                pass

        # Record job submission metric
        jobs_submitted_total.labels(source="api").inc()

        return JobSubmissionResponse(
            job_id=job_id,
            status="pii_scanning",
            estimated_completion_minutes=settings.estimated_processing_minutes,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )


@router.get("/{job_id}", response_model=DocumentStatusResponse)
async def get_job(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    url_service: S3URLService = Depends(get_s3_url_service),
) -> DocumentStatusResponse:
    """
    Get current status of a processing job.

    Returns a clean, status-specific response with only relevant fields.
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    base = {
        "job_id": job["job_id"],
        "status": job["status"],
        "filename": job.get("original_filename"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "debug_bundle_requested": job.get("debug_bundle_requested") == "true",
    }

    match job["status"]:
        case "pii_scanning":
            return PIIScanningResponse(
                **base,
                estimated_completion_minutes=settings.estimated_processing_minutes,
            )

        case "processing":
            # Check if this is an agentic pipeline job (has review_mode set)
            review_mode = job.get("review_mode")
            processing_phase = job.get("processing_phase")

            if review_mode:
                # Agentic pipeline response
                return AgenticProcessingResponse(
                    **base,
                    review_mode=review_mode,
                    processing_phase=processing_phase or "initializing",
                    jobs_total=int(job.get("jobs_total", 0)),
                    jobs_complete=int(job.get("jobs_complete", 0)),
                    stream_url=f"/api/v1/documents/{job_id}/stream",
                    pii_skipped=job.get("pii_skipped") == "true" if job.get("pii_skipped") else None,
                )
            else:
                # Legacy pipeline response
                return ProcessingResponse(
                    **base,
                    estimated_completion_minutes=settings.estimated_processing_minutes,
                    pii_skipped=job.get("pii_skipped") == "true" if job.get("pii_skipped") else None,
                )

        case "awaiting_approval":
            pii_findings = [PIIFinding(**f) for f in (job.get("pii_findings") or [])]
            token = job.get("approval_token", "")
            return AwaitingPIIApprovalResponse(
                **base,
                pii_findings=pii_findings,
                approval_token=token,
                approval_expires_at=job.get("approval_expires_at", ""),
                approval_url=f"/api/v1/approval/{token}/decision",
            )

        case "completed":
            # Check if this is an agentic pipeline job (has review_mode set)
            review_mode = job.get("review_mode")

            if review_mode:
                # Agentic pipeline completed response
                # Get result_url (markdown) - different field name for agentic pipeline
                markdown_key = job.get("result_url") or job.get("markdown_url")
                if not markdown_key:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Job completed but result_url/markdown_url not set. This indicates a bug.",
                    )

                # Ledger is always available for completed agentic pipeline jobs
                ledger_url = f"/api/v1/documents/{job_id}/ledger"

                # Build figure assets from stored_figures
                figure_assets: list[FigureAsset] = []
                stored_figures_raw = job.get("stored_figures")
                if stored_figures_raw:
                    # Parse if stored as JSON string
                    if isinstance(stored_figures_raw, str):
                        try:
                            stored_figures_data = json.loads(stored_figures_raw)
                        except json.JSONDecodeError:
                            stored_figures_data = []
                    elif isinstance(stored_figures_raw, bytes):
                        try:
                            stored_figures_data = json.loads(stored_figures_raw.decode("utf-8"))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            stored_figures_data = []
                    else:
                        stored_figures_data = stored_figures_raw

                    for fig in stored_figures_data:
                        s3_key = fig.get("s3_key", "")
                        if s3_key:
                            figure_url = await url_service.generate_url(
                                s3_key, bucket=url_service.results_bucket
                            )
                            figure_assets.append(
                                FigureAsset(
                                    figure_id=fig.get("figure_id", ""),
                                    url=figure_url,
                                    page=int(fig.get("page_num", 1)),
                                    alt_text=fig.get("alt_text", ""),
                                    caption=fig.get("caption", ""),
                                )
                            )

                # Bundle URL is only available if there are figures
                bundle_url = (
                    f"/api/v1/documents/{job_id}/bundle"
                    if figure_assets
                    else None
                )

                return AgenticCompletedResponse(
                    **base,
                    review_mode=review_mode,
                    markdown_url=await url_service.generate_url(markdown_key, bucket=url_service.results_bucket),
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    llm_cost=_build_llm_cost(job)
                    or LLMCostInfo(
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_cost_cents=0,
                        estimated_cost_dollars=0,
                    ),
                    ledger_url=ledger_url,
                    total_pages=int(job.get("total_pages", 0)),
                    total_edits=int(job.get("total_edits", 0)),
                    figures=figure_assets,
                    bundle_url=bundle_url,
                )
            else:
                # Legacy pipeline completed response
                # PRD-027: markdown_url must be saved in job record by apply_reviews
                markdown_key = job.get("markdown_url")
                if not markdown_key:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Job completed but markdown_url not set. This indicates a bug.",
                    )
                return CompletedResponse(
                    **base,
                    markdown_url=await url_service.generate_url(markdown_key, bucket=url_service.results_bucket),
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    llm_cost=_build_llm_cost(job)
                    or LLMCostInfo(
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_cost_cents=0,
                        estimated_cost_dollars=0,
                    ),
                )

        case "failed":
            return FailedResponse(
                **base,
                error=job.get("error", "Unknown error"),
            )

        case "denied":
            return DeniedResponse(
                **base,
                reason=job.get("denial_reason", "PII not approved"),
            )

        case _:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unknown job status: {job['status']}",
            )



class StreamTokenResponse(BaseModel):
    """Response for stream token generation."""

    token: str = Field(..., description="Single-use token for SSE authentication")
    expires_in_seconds: int = Field(300, description="Token validity period in seconds")
    stream_url: str = Field(..., description="Full URL path with token for EventSource")


@router.post("/{job_id}/stream/token", response_model=StreamTokenResponse)
async def create_stream_token(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> StreamTokenResponse:
    """Generate single-use token for SSE stream authentication.

    Browser EventSource API cannot send custom headers. This endpoint
    generates a short-lived, single-use token that can be passed as
    a query parameter to the stream endpoint.

    The token:
    - Expires in 5 minutes
    - Is consumed on first use (single-use)
    - Is scoped to the specific job_id

    Args:
        job_id: Job identifier

    Returns:
        StreamTokenResponse with token and stream URL

    Raises:
        HTTPException 404: Job not found
    """
    # Verify job exists
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    # Generate single-use token
    token = await job_service.create_stream_token(job_id)

    return StreamTokenResponse(
        token=token,
        expires_in_seconds=300,
        stream_url=f"/api/v1/documents/{job_id}/stream?token={token}",
    )


@router.get("/{job_id}/stream")
async def stream_events(
    job_id: str,
    token: str | None = Query(None, description="Single-use stream token from /stream/token endpoint"),
    job_service: JobService = Depends(get_job_service),
) -> StreamingResponse:
    """Stream processing events via Server-Sent Events (SSE).

    Authentication: Either X-API-Key header OR valid stream token.
    Stream tokens are generated via POST /{job_id}/stream/token.

    Connect to this endpoint to watch processing in real-time.
    Events include: docling progress, planning progress, job creation, edits, etc.

    Args:
        job_id: Job identifier
        token: Optional single-use stream token (for browser EventSource)

    Returns:
        SSE stream with processing events

    Raises:
        HTTPException 401: Invalid or missing authentication
        HTTPException 404: Job not found
    """
    # If token provided, validate and consume it (single-use)
    if token:
        validated_job_id = await job_service.validate_and_consume_stream_token(token)
        if not validated_job_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid, expired, or already-used stream token",
            )
        # Verify token is for this job (job-scoped security)
        if validated_job_id != job_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Stream token is not valid for this job",
            )
    # Note: If no token, middleware already validated API key
    from ..agents.events import get_event_bus

    # Verify job exists
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        """Generate SSE events."""
        try:
            # Get event bus from registry
            event_bus = get_event_bus(job_id)

            # If job is already complete or failed, send final event and close
            if job["status"] in ("completed", "failed"):
                if event_bus:
                    for event in event_bus.events:
                        yield f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"
                yield "event: done\ndata: {}\n\n"
                return

            # Wait for event bus to be available (up to 30 seconds)
            max_wait = 30.0
            waited = 0.0
            while event_bus is None and waited < max_wait:
                await asyncio.sleep(0.5)
                waited += 0.5
                event_bus = get_event_bus(job_id)

            if event_bus is None:
                yield 'event: error\ndata: {"message": "Event bus not available"}\n\n'
                yield "event: done\ndata: {}\n\n"
                return

            # Subscribe to events
            queue = event_bus.subscribe()

            try:
                # First, send any events that already happened
                for event in event_bus.events:
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

                        # Check if job is done by refreshing status
                        refreshed_job = await job_service.get_job(job_id)
                        if refreshed_job and refreshed_job["status"] in ("completed", "failed"):
                            break

            finally:
                event_bus.unsubscribe(queue)

            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            logger.error(f"SSE stream error for job {job_id}: {e}")
            yield f'event: error\ndata: {{"message": "Stream error: {str(e)}"}}\n\n'
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


@router.get("/{job_id}/ledger", response_model=LedgerResponse)
async def get_ledger(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service),
    url_service: S3URLService = Depends(get_s3_url_service),
) -> LedgerResponse:
    """Get change ledger for PR-like review.

    Returns the complete change ledger with all edits made by the pipeline,
    grouped by page for easy review.

    Args:
        job_id: Job identifier

    Returns:
        LedgerResponse with all changes grouped by page

    Raises:
        HTTPException 404: Job not found or ledger not available
        HTTPException 400: Job not yet complete
    """
    # Verify job exists
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    # Require job to be completed
    if job["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job not yet complete (status: {job['status']})",
        )

    # Get ledger from S3 via DocumentProcessingService
    processing_service = DocumentProcessingService(
        redis_client=None,  # Not needed for get_ledger
        storage_service=storage,
        s3_url_service=url_service,
    )

    ledger_data = await processing_service.get_ledger(job_id)
    if not ledger_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ledger not found for this job",
        )

    # Build grouped ledger response
    entries_by_page: dict[int, list[LedgerEntryResponse]] = {}
    entries_needing_review_count = 0
    for entry in ledger_data.get("entries", []):
        page = entry.get("page", 1)
        if page not in entries_by_page:
            entries_by_page[page] = []

        needs_review = entry.get("needs_review", False)
        if needs_review:
            entries_needing_review_count += 1

        entries_by_page[page].append(
            LedgerEntryResponse(
                entry_id=entry.get("entry_id", ""),
                page=page,
                action=entry.get("action", ""),
                target=entry.get("target", ""),
                before=entry.get("before", ""),
                after=entry.get("after", ""),
                reasoning=entry.get("reasoning", ""),
                confidence=float(entry.get("confidence", 0.0)),
                timestamp=entry.get("timestamp", ""),
                needs_review=needs_review,
            )
        )

    # Build page groups
    pages = []
    for page_num in sorted(entries_by_page.keys()):
        page_entries = entries_by_page[page_num]
        pages.append(
            LedgerPageGroup(
                page=page_num,
                entries=page_entries,
                edit_count=len(page_entries),
            )
        )

    # Generate markdown URL
    markdown_s3_key = job.get("result_url", f"results/{job_id}/result.md")
    final_markdown_url = await url_service.generate_url(
        markdown_s3_key,
        bucket=url_service.results_bucket,
    )

    return LedgerResponse(
        job_id=job_id,
        document_title=job.get("original_filename", ""),
        total_pages=int(job.get("total_pages", 0)),
        pages_with_changes=len(pages),
        total_edits=ledger_data.get("total_edits", 0),
        entries_needing_review=entries_needing_review_count,
        pages=pages,
        processing_duration_ms=0,
        final_markdown_url=final_markdown_url,
    )


# ---------------------------------------------------------------------------
# PII approval / denial — by-job-id endpoints.
#
# The token-based equivalent lives at /api/v1/approval/{token}/decision in
# api.approval. These by-job-id variants exist so machine clients (e.g. the
# reflow-canvas-lti connector) can forward a faculty decision without having
# to juggle an approval token — they already authenticated with an API key
# and they already know the job_id from a prior status poll.
# ---------------------------------------------------------------------------


class PIIDecisionInput(BaseModel):
    """Input for the by-job-id PII decision endpoints."""

    justification: str | None = Field(
        None,
        min_length=10,
        max_length=1000,
        description="Optional explanation for the decision (10–1000 chars when provided).",
    )
    reviewed_by: str = Field(
        ...,
        min_length=3,
        description="Reviewer identifier (email or stable user id).",
    )


class PIIDecisionResponse(BaseModel):
    """Response for the by-job-id PII decision endpoints."""

    message: str
    job_id: str
    decision: Literal["approved", "denied"]


@router.post(
    "/{job_id}/pii/approve",
    response_model=PIIDecisionResponse,
    summary="Approve PII gate for a document (by job_id)",
    description=(
        "Records a faculty approval of the PII findings on a job that is in "
        "``awaiting_approval`` status, then resumes processing. Symmetric "
        "counterpart of ``POST /{job_id}/pii/deny``. Intended for connector "
        "consumption (the Canvas LTI connector forwards a faculty decision "
        "here after the panorama PII gate)."
    ),
)
async def approve_pii(
    job_id: str,
    body: PIIDecisionInput,
    background_tasks: BackgroundTasks,
    redis_client: Any = Depends(get_redis_client),
    storage_service: StorageService = Depends(get_storage_service),
    s3_url_service: S3URLService = Depends(get_s3_url_service),
) -> PIIDecisionResponse:
    return await _process_pii_decision(
        job_id=job_id,
        decision="approved",
        body=body,
        background_tasks=background_tasks,
        redis_client=redis_client,
        storage_service=storage_service,
        s3_url_service=s3_url_service,
    )


@router.post(
    "/{job_id}/pii/deny",
    response_model=PIIDecisionResponse,
    summary="Deny PII gate for a document (by job_id)",
    description=(
        "Records a faculty denial of the PII findings on a job that is in "
        "``awaiting_approval`` status, then cleans up the document. Symmetric "
        "counterpart of ``POST /{job_id}/pii/approve``."
    ),
)
async def deny_pii(
    job_id: str,
    body: PIIDecisionInput,
    background_tasks: BackgroundTasks,
    redis_client: Any = Depends(get_redis_client),
    storage_service: StorageService = Depends(get_storage_service),
    s3_url_service: S3URLService = Depends(get_s3_url_service),
) -> PIIDecisionResponse:
    return await _process_pii_decision(
        job_id=job_id,
        decision="denied",
        body=body,
        background_tasks=background_tasks,
        redis_client=redis_client,
        storage_service=storage_service,
        s3_url_service=s3_url_service,
    )


async def _process_pii_decision(
    *,
    job_id: str,
    decision: Literal["approved", "denied"],
    body: PIIDecisionInput,
    background_tasks: BackgroundTasks,
    redis_client: Any,
    storage_service: StorageService,
    s3_url_service: S3URLService,
) -> PIIDecisionResponse:
    """Shared body for approve_pii + deny_pii.

    Pre-validates the job's current status (404 if missing, 409 if not
    awaiting_approval) before invoking the approval_service. That contract is
    what the Canvas LTI connector relies on to surface "another instructor
    decided in a parallel tab" as a 409 to the operator. The token-based
    sibling endpoint in api.approval lets quick_approve / quick_deny set the
    status unconditionally; that's safe there because the token already
    proves the job is in awaiting_approval, but it isn't safe by job_id.

    Connector contract pinned by:
        https://github.com/oshrizak/reflow-canvas-lti — the Canvas LTI
        connector tries this endpoint first when forwarding a faculty PII
        decision; on 404/405 it falls back to
        ``POST /api/v1/approval/{token}/decision`` so the connector keeps
        working against Core deployments that pre-date this PR. Both
        branches are covered in the connector's
        ``tests/integration/test_pii_decision.py``
        (``test_pii_approve_prefers_by_job_id_endpoint`` +
        ``test_pii_approve_falls_back_to_token_endpoint_on_405``).
        Net effect once this PR merges + Core ships: connector drops the
        approval-token round-trip and submits decisions in one POST.
    """
    job_service = JobService(redis_client)
    queue_service = QueueService(redis_client)
    approval_service = ApprovalService(
        redis_client=redis_client,
        s3_client=None,  # Lazy-loaded inside the background task on the denial path.
        job_service=job_service,
        queue_service=queue_service,
        storage_service=storage_service,
        s3_url_service=s3_url_service,
    )

    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    current_status = str(job.get("status") or "")
    if current_status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {job_id} is in {current_status!r}, not 'awaiting_approval' "
                f"— a decision was already recorded."
            ),
        )

    s3_key = job.get("s3_key", "")
    justification = body.justification or ""

    try:
        if decision == "approved":
            await approval_service.quick_approve(job_id)
            background_tasks.add_task(
                approval_service.process_approval_background,
                job_id=job_id,
                s3_key=s3_key,
                justification=justification,
                reviewed_by=body.reviewed_by,
            )
            return PIIDecisionResponse(
                message="Job approved - processing started",
                job_id=job_id,
                decision="approved",
            )

        await approval_service.quick_deny(job_id)
        background_tasks.add_task(
            approval_service.process_denial_background,
            job_id=job_id,
            s3_key=s3_key,
            justification=justification,
            reviewed_by=body.reviewed_by,
        )
        return PIIDecisionResponse(
            message="Job denied - cleanup started",
            job_id=job_id,
            decision="denied",
        )
    except ValueError as exc:
        # Defensive: approval_service can raise ValueError on data checks
        # (e.g. missing s3_key). The job exists but isn't in a decidable
        # state — surface as 409 not 500.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("PII decision (%s) failed for job=%s", decision, job_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process PII decision: {exc}",
        ) from exc
