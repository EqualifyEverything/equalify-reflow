"""Document processing endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..config import settings
from ..dependencies import (
    get_job_service,
    get_queue_service,
    get_s3_url_service,
    get_storage_service,
)
from ..services import JobService, QueueService, S3URLService, StorageService
from ..shared.constants.queues import PROCESSING_QUEUE
from ..shared.models.queue import ProcessingQueuePayload
from .schemas import (
    AwaitingCorrectionApprovalResponse,
    AwaitingPIIApprovalResponse,
    CompletedResponse,
    CorrectionDecision,
    CorrectionSummary,
    DeniedResponse,
    DocumentStatusResponse,
    FailedResponse,
    LLMCostInfo,
    PIIFinding,
    PIIScanningResponse,
    ProcessingResponse,
)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _build_llm_cost(job: dict) -> LLMCostInfo | None:
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


@router.post(
    "/submit", response_model=JobSubmissionResponse, status_code=status.HTTP_201_CREATED
)
async def submit_document(
    file: UploadFile = File(...),
    skip_pii_scan: bool = Form(default=False, description="Skip PII scanning and queue directly for processing"),
    skip_reason: str | None = Form(default=None, description="Optional reason for skipping PII scan (for audit trail)"),
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    job_service: JobService = Depends(get_job_service),
):
    """Submit a PDF document for processing.

    Args:
        file: PDF file to process
        skip_pii_scan: If True, bypass PII scanning and queue directly for processing
        skip_reason: Optional justification for skipping PII scan (recorded in audit trail)
    """
    job_id, s3_key = await storage.store_document(file)

    if skip_pii_scan:
        # Direct to processing queue (bypass PII scanning)
        await job_service.create_job(
            job_id,
            s3_key,
            status="processing",
            original_filename=file.filename,
            pii_skipped=True,
            pii_skip_reason=skip_reason or "User requested PII scan skip"
        )
        processing_payload = ProcessingQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            approved_at=None  # No approval needed - PII scan skipped
        )
        await queue.enqueue(PROCESSING_QUEUE, processing_payload)

        return JobSubmissionResponse(
            job_id=job_id,
            status="processing",
            estimated_completion_minutes=settings.estimated_processing_minutes,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    else:
        # Standard flow: PII scanning first
        await job_service.create_job(
            job_id, s3_key, status="pii_scanning", original_filename=file.filename
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
):
    """
    Get current status of a processing job.

    Returns a clean, status-specific response with only relevant fields.
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found"
        )

    base = {
        "job_id": job["job_id"],
        "status": job["status"],
        "filename": job.get("original_filename"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }

    match job["status"]:
        case "pii_scanning":
            return PIIScanningResponse(
                **base,
                estimated_completion_minutes=settings.estimated_processing_minutes,
            )

        case "processing":
            return ProcessingResponse(
                **base,
                estimated_completion_minutes=settings.estimated_processing_minutes,
                pii_skipped=job.get("pii_skipped") == "true" if job.get("pii_skipped") else None,
            )

        case "awaiting_approval":
            pii_findings = [
                PIIFinding(**f) for f in (job.get("pii_findings") or [])
            ]
            token = job.get("approval_token", "")
            return AwaitingPIIApprovalResponse(
                **base,
                pii_findings=pii_findings,
                approval_token=token,
                approval_expires_at=job.get("approval_expires_at", ""),
                approval_url=f"/api/approval/{token}/decision",
            )

        case "awaiting_correction_approval":
            # Build correction summary
            correction_results = job.get("correction_results", [])
            by_type: dict[str, int] = {}
            total = 0
            for page in correction_results:
                for c in page.get("corrections", []):
                    ctype = c.get("type", "other")
                    by_type[ctype] = by_type.get(ctype, 0) + 1
                    total += 1

            token = job.get("correction_approval_token", "")
            page_keys = job.get("page_image_keys", [])

            return AwaitingCorrectionApprovalResponse(
                **base,
                correction_summary=CorrectionSummary(
                    total_corrections=total,
                    confidence_score=float(job.get("confidence_score", 0.0)),
                    corrections_by_type=by_type,
                ),
                approval_token=token,
                approval_expires_at=job.get("correction_expires_at", ""),
                review_url=f"/api/corrections/{job_id}/review?token={token}",
                original_markdown_url=await url_service.generate_url(
                    job["original_markdown_key"], bucket=url_service.results_bucket
                ),
                corrected_markdown_url=await url_service.generate_url(
                    job["corrected_markdown_key"], bucket=url_service.results_bucket
                ),
                page_image_urls=[
                    await url_service.generate_url(k, bucket=url_service.temp_bucket)
                    for k in page_keys
                ],
                llm_cost=_build_llm_cost(job) or LLMCostInfo(
                    estimated_cost_cents=0, estimated_cost_dollars=0
                ),
            )

        case "completed":
            return CompletedResponse(
                **base,
                markdown_url=await url_service.generate_url(
                    f"{job_id}.md", bucket=url_service.results_bucket
                ),
                confidence_score=float(job.get("confidence_score", 0.0)),
                correction_decision=CorrectionDecision(
                    # Default to "auto_completed" when no manual review was performed
                    decision=job.get("correction_decision", "auto_completed"),
                    reviewed_by=job.get("correction_reviewed_by", ""),
                    reviewed_at=job.get("correction_reviewed_at", ""),
                    justification=job.get("correction_justification", ""),
                ),
                llm_cost=_build_llm_cost(job) or LLMCostInfo(
                    estimated_cost_cents=0, estimated_cost_dollars=0
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
