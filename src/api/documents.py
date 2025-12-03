"""Document processing endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..config import settings
from ..dependencies import (
    get_job_service,
    get_queue_service,
    get_s3_url_service,
    get_storage_service,
)
from ..services import JobService, QueueService, S3URLService, StorageService
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
    PageLLMUsage,
    PIIFinding,
    PIIScanningResponse,
    ProcessingResponse,
)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _build_llm_cost(job: dict) -> LLMCostInfo | None:
    """Build LLM cost info from job data."""
    llm_cost_cents = job.get("llm_cost_cents")
    if llm_cost_cents is None:
        return None

    try:
        total_cents = float(llm_cost_cents)
    except (TypeError, ValueError):
        return None

    page_costs = []
    for page_cost in job.get("llm_page_costs", []):
        page_costs.append(
            PageLLMUsage(
                page=page_cost.get("page", 0),
                input_tokens=page_cost.get("input_tokens", 0),
                output_tokens=page_cost.get("output_tokens", 0),
                cost_cents=page_cost.get("cost_cents", 0.0),
            )
        )

    return LLMCostInfo(
        total_cost_cents=total_cents,
        total_cost_dollars=total_cents / 100.0,
        page_costs=page_costs,
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
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    job_service: JobService = Depends(get_job_service),
):
    """Submit a PDF document for processing."""
    job_id, s3_key = await storage.store_document(file)
    await job_service.create_job(job_id, s3_key, status="pii_scanning")
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
                    total_cost_cents=0, total_cost_dollars=0, page_costs=[]
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
                    decision=job.get("correction_decision", "approved"),
                    reviewed_by=job.get("correction_reviewed_by", ""),
                    reviewed_at=job.get("correction_reviewed_at", ""),
                    justification=job.get("correction_justification", ""),
                ),
                llm_cost=_build_llm_cost(job) or LLMCostInfo(
                    total_cost_cents=0, total_cost_dollars=0, page_costs=[]
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
