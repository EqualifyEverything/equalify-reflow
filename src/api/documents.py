"""Document processing endpoints."""

import asyncio
import json
import logging
from collections import Counter
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings


from ..dependencies import (
    get_job_service,
    get_queue_service,
    get_redis_client,
    get_remediation_storage,
    get_s3_url_service,
    get_storage_service,
)
from ..services import JobService, QueueService, S3URLService, StorageService
from ..services.document_processing_service import DocumentProcessingService
from ..services.remediation_storage_service import RemediationStorageService
from ..shared.constants.queues import PROCESSING_QUEUE
from ..shared.models.queue import ProcessingQueuePayload
from .schemas import (
    AgenticCompletedResponse,
    AgenticProcessingResponse,
    AgentsPhase,
    AnalysisPhase,
    AutoCorrectionSummary,
    AwaitingCorrectionApprovalResponse,
    AwaitingPIIApprovalResponse,
    CompletedResponse,
    CorrectionDecision,
    CorrectionItem,
    CorrectionSummary,
    DeniedResponse,
    DocumentStatusResponse,
    ExtractionPhase,
    FailedResponse,
    LedgerEntryResponse,
    LedgerPageGroup,
    LedgerResponse,
    LLMCostInfo,
    NeedsReviewResponse,
    ObservationSummary,
    PageFeatureSummary,
    PIIFinding,
    PIIScanningResponse,
    ProcessingPhasesResponse,
    ProcessingResponse,
    RemediationPhase,
    VerificationPageResult,
    VerificationPhase,
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

    return LLMCostInfo(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_cents=total_cents,
        estimated_cost_dollars=total_cents / 100.0,
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
        )

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
        )
        await queue.queue_pii_job(job_id, s3_key)

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

        case "awaiting_correction_approval":
            # Build correction summary and full correction list
            correction_results = job.get("correction_results", [])
            by_type: dict[str, int] = {}
            total = 0
            auto_applied = 0
            manual_review = 0
            corrections_list: list[CorrectionItem] = []

            for page_result in correction_results:
                page_num = page_result.get("page", 1)
                for c in page_result.get("corrections", []):
                    ctype = c.get("type", "other")
                    is_auto = c.get("is_auto_applied", False)
                    by_type[ctype] = by_type.get(ctype, 0) + 1
                    total += 1
                    if is_auto:
                        auto_applied += 1
                    else:
                        manual_review += 1

                    corrections_list.append(
                        CorrectionItem(
                            page=page_num,
                            type=ctype,
                            original_snippet=c.get("original", "")[:200],
                            corrected_snippet=c.get("corrected", "")[:200],
                            confidence=c.get("confidence", 0.0),
                            explanation=c.get("explanation", ""),
                            is_auto_applied=is_auto,
                        )
                    )

            token = job.get("correction_approval_token", "")
            # page_image_urls is stored as comma-separated string in Redis
            page_keys_raw = job.get("page_image_urls", "")
            page_keys = page_keys_raw.split(",") if page_keys_raw else []

            return AwaitingCorrectionApprovalResponse(
                **base,
                correction_summary=CorrectionSummary(
                    total_corrections=total,
                    auto_applied_count=auto_applied,
                    manual_review_count=manual_review,
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    corrections_by_type=by_type,
                ),
                corrections=corrections_list,
                approval_token=token,
                approval_expires_at=job.get("correction_expires_at", ""),
                review_url=f"/api/v1/corrections/{job_id}/review?token={token}",
                original_markdown_url=await url_service.generate_url(
                    job["original_markdown_key"], bucket=url_service.results_bucket
                ),
                corrected_markdown_url=await url_service.generate_url(
                    job["corrected_markdown_key"], bucket=url_service.results_bucket
                ),
                page_image_urls=[await url_service.generate_url(k, bucket=url_service.temp_bucket) for k in page_keys],
                llm_cost=_build_llm_cost(job)
                or LLMCostInfo(
                    input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0, estimated_cost_dollars=0
                ),
            )

        case "needs_review":
            # PRD-027: New review checklist workflow
            # page_image_urls is stored as comma-separated string in Redis
            page_keys_raw = job.get("page_image_urls", "")
            page_keys = page_keys_raw.split(",") if page_keys_raw else []
            return NeedsReviewResponse(
                **base,
                confidence_score=float(job.get("confidence_score", 0.0)),
                review_item_count=int(job.get("review_item_count", 0)),
                processing_result_key=job.get("processing_result_key", ""),
                review_url=f"/api/v1/documents/{job_id}/result/checklist",
                page_image_urls=[
                    await url_service.generate_url(k, bucket=url_service.temp_bucket)
                    for k in page_keys
                    if k  # Skip empty strings
                ],
                llm_cost=_build_llm_cost(job)
                or LLMCostInfo(
                    input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0, estimated_cost_dollars=0
                ),
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

                # Build ledger URL only for human review mode
                ledger_url = None
                if review_mode == "human":
                    ledger_url = f"/api/v1/documents/{job_id}/ledger"

                return AgenticCompletedResponse(
                    **base,
                    review_mode=review_mode,
                    markdown_url=await url_service.generate_url(markdown_key, bucket=url_service.results_bucket),
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    llm_cost=_build_llm_cost(job)
                    or LLMCostInfo(
                        input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0, estimated_cost_dollars=0
                    ),
                    ledger_url=ledger_url,
                    total_pages=int(job.get("total_pages", 0)),
                    total_edits=int(job.get("total_edits", 0)),
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
                    correction_decision=CorrectionDecision(
                        # Default to "auto_completed" when no manual review was performed
                        decision=job.get("correction_decision", "auto_completed"),
                        reviewed_by=job.get("correction_reviewed_by", ""),
                        reviewed_at=job.get("correction_reviewed_at", ""),
                        justification=job.get("correction_justification", ""),
                    ),
                    llm_cost=_build_llm_cost(job)
                    or LLMCostInfo(
                        input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0, estimated_cost_dollars=0
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


@router.get("/{job_id}/phases", response_model=ProcessingPhasesResponse)
async def get_job_phases(
    job_id: str,
    show_raw: bool = False,
    job_service: JobService = Depends(get_job_service),
    url_service: S3URLService = Depends(get_s3_url_service),
    remediation_storage: RemediationStorageService = Depends(get_remediation_storage),
) -> ProcessingPhasesResponse:
    """
    Get detailed processing phase outputs for a job.

    Returns structured data from each phase of the processing pipeline:
    - Analysis: Document structure, page features, heading tree
    - Extraction: Original markdown (v0), extraction confidence
    - Agents: Observations from specialized agents
    - Remediation: Auto corrections and review items

    Query params:
        show_raw: Include full raw JSON from each phase artifact
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    # Load artifacts from S3
    manifest = await remediation_storage.load_manifest(job_id)
    observations = await remediation_storage.load_observations(job_id)

    # Build Analysis Phase
    analysis_phase = AnalysisPhase(status="skipped")
    if manifest:
        page_features_list = []
        for pf in manifest.page_features:
            page_features_list.append(
                PageFeatureSummary(
                    page_num=pf.page_num,
                    has_images=pf.has_images,
                    image_count=pf.image_count,
                    has_tables=pf.has_tables,
                    table_count=pf.table_count,
                    has_lists=pf.has_lists,
                    complexity_score=pf.complexity_score,
                )
            )

        # Build heading tree from manifest's heading_tree_json
        heading_tree = None
        layout_type = None
        if manifest.heading_tree_json:
            try:
                tree_data = json.loads(manifest.heading_tree_json)
                heading_tree = tree_data
                # HeadingTree model has layout_type at document level
                layout_type = tree_data.get("layout_type")
            except json.JSONDecodeError:
                pass

        # If no layout from heading tree, derive from page features
        if not layout_type and manifest.page_features:
            layouts = [pf.layout_type for pf in manifest.page_features]
            # Use most common layout as the document layout
            layout_counts = Counter(layouts)
            layout_type = layout_counts.most_common(1)[0][0] if layout_counts else None

        analysis_phase = AnalysisPhase(
            status="completed",
            document_title=manifest.document_title,
            document_type=manifest.document_type,
            total_pages=manifest.total_pages,
            layout_type=layout_type,
            required_agents=manifest.required_agents,
            analysis_confidence=manifest.analysis_confidence,
            page_features=page_features_list,
            heading_tree=heading_tree,
            raw_manifest=manifest.model_dump(mode="json") if show_raw else None,
        )

    # Build Extraction Phase
    extraction_phase = ExtractionPhase(status="skipped")
    v0_key = f"{job_id}-v0.md"
    try:
        v0_url = await url_service.generate_url(v0_key, bucket=url_service.results_bucket)
        extraction_phase = ExtractionPhase(
            status="completed",
            markdown_url=v0_url,
            confidence_score=float(job.get("confidence_score", 0.0)),
            extraction_model=job.get("extraction_model"),
        )
    except Exception:
        pass  # v0 may not exist yet

    # Build Agents Phase
    agents_phase = AgentsPhase(status="skipped")
    if observations:
        agents_run = list(set(obs.agent for obs in observations))
        obs_summaries = [
            ObservationSummary(
                id=obs.id,
                agent=obs.agent,
                severity=obs.severity,
                confidence=obs.confidence,
                category=obs.category,
                status=obs.status,
                resolution=obs.resolution,
                visual_description=obs.visual_description[:200] if obs.visual_description else None,
                markup_description=obs.markup_description[:200] if obs.markup_description else None,
                page_num=obs.location.page_num if obs.location else None,
            )
            for obs in observations
        ]
        agents_phase = AgentsPhase(
            status="completed",
            agents_run=agents_run,
            observation_count=len(observations),
            observations=obs_summaries,
            raw_observations=[obs.model_dump(mode="json") for obs in observations] if show_raw else None,
        )

    # Build Remediation Phase (auto corrections)
    auto_corrections = await remediation_storage.load_auto_corrections(job_id)
    remediation_phase = RemediationPhase(status="skipped")
    if auto_corrections:
        applied_count = sum(1 for c in auto_corrections if c.applied)
        pending_count = sum(1 for c in auto_corrections if not c.applied)

        correction_summaries = [
            AutoCorrectionSummary(
                id=c.id,
                observation_id=c.observation_id,
                applied=c.applied,
                page_num=c.page_num,
                search_preview=c.search[:100] if c.search else "",
                replace_preview=c.replace[:100] if c.replace else "",
                justification=c.justification,
                confidence=c.confidence,
                agent=c.agent,
            )
            for c in auto_corrections
        ]
        remediation_phase = RemediationPhase(
            status="completed",
            auto_correction_count=len(auto_corrections),
            applied_count=applied_count,
            pending_count=pending_count,
            auto_corrections=correction_summaries,
            raw_corrections=[c.model_dump(mode="json") for c in auto_corrections] if show_raw else None,
        )

    # Build Verification Phase
    verification_phase: VerificationPhase | None = None
    verification_summary = job.get("verification_summary")
    if verification_summary:
        page_results = [
            VerificationPageResult(
                page_num=pr["page_num"],
                is_accurate=pr["is_accurate"],
                corrections_applied=pr.get("corrections_applied", 0),
                corrections_failed=pr.get("corrections_failed", 0),
                issues_count=pr.get("issues_count", 0),
                summary=pr.get("summary", ""),
            )
            for pr in verification_summary.get("page_results", [])
        ]
        verification_phase = VerificationPhase(
            status="completed",
            total_pages=verification_summary.get("total_pages", 0),
            corrections_applied=verification_summary.get("corrections_applied", 0),
            corrections_failed=verification_summary.get("corrections_failed", 0),
            issues_found=verification_summary.get("issues_found", 0),
            all_pages_accurate=verification_summary.get("all_pages_accurate", True),
            page_results=page_results,
            cost_cents=verification_summary.get("cost_cents", 0.0),
        )

    return ProcessingPhasesResponse(
        job_id=job["job_id"],
        filename=job.get("original_filename", ""),
        status=job["status"],
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        analysis=analysis_phase,
        extraction=extraction_phase,
        agents=agents_phase,
        remediation=remediation_phase,
        verification=verification_phase,
        total_llm_cost=_build_llm_cost(job),
    )


@router.get("/{job_id}/debug-bundle")
async def download_debug_bundle(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service),
) -> StreamingResponse:
    """Download debug bundle as a zip file.

    Only available if job was submitted with generate_debug_bundle=true.
    Contains all agent prompts, responses, page images, and outputs.

    The bundle includes:
    - README.md with analysis instructions
    - input/original.pdf and input/pages/*.png
    - phase_*/agent_name.json with prompts and responses
    - output/manifest.json, observations.json, final_markdown.md

    Args:
        job_id: Job identifier

    Returns:
        Streaming zip file download

    Raises:
        HTTPException 404: Job not found or debug bundle not requested
    """
    from ..services.debug_bundle_service import DebugBundleService

    # Get job and verify debug bundle was requested
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    if job.get("debug_bundle_requested") != "true":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Debug bundle was not requested for this job. Submit with generate_debug_bundle=true to enable.",
        )

    # Generate bundle
    debug_service = DebugBundleService(storage)
    try:
        zip_bytes = await debug_service.generate_bundle(job_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate debug bundle: {str(e)}"
        )

    # Return as streaming download
    filename = f"debug_{job_id}.zip"
    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.get("/{job_id}/stream")
async def stream_events(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> StreamingResponse:
    """Stream processing events via Server-Sent Events (SSE).

    Connect to this endpoint to watch processing in real-time.
    Events include: docling progress, planning progress, job creation, edits, etc.

    Args:
        job_id: Job identifier

    Returns:
        SSE stream with processing events

    Raises:
        HTTPException 404: Job not found
    """
    from ..agents.v5.events import get_event_bus

    # Verify job exists
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    async def event_generator():
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
            max_wait = 30
            waited = 0
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
    for entry in ledger_data.get("entries", []):
        page = entry.get("page", 1)
        if page not in entries_by_page:
            entries_by_page[page] = []

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
        pages=pages,
        processing_duration_ms=0,
        final_markdown_url=final_markdown_url,
    )
