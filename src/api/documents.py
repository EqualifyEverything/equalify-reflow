"""Document processing endpoints."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..config import settings
from ..dependencies import get_job_service, get_queue_service, get_storage_service
from ..services import JobService, QueueService, StorageService
from .schemas import (
    DocumentStatusResponse,
    LLMCostInfo,
    PageLLMUsage,
)

router = APIRouter(prefix="/api/documents", tags=["Documents"])


def _build_llm_cost_info(job: dict) -> LLMCostInfo | None:
    """Build LLM cost info from job data if available.

    Args:
        job: Job data from Redis

    Returns:
        LLMCostInfo if cost data exists, None otherwise
    """
    # Check if cost data exists
    llm_cost_cents = job.get("llm_cost_cents")
    llm_page_costs = job.get("llm_page_costs")

    if llm_cost_cents is None:
        return None

    # Parse llm_cost_cents to float
    try:
        total_cents = float(llm_cost_cents)
    except (TypeError, ValueError):
        return None

    # Build page costs list
    page_costs = []
    if llm_page_costs:
        # llm_page_costs should already be parsed by job_service.get_job()
        for page_cost in llm_page_costs:
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


# Response models
class JobSubmissionResponse(BaseModel):
    """Response for document submission."""
    job_id: str
    status: str
    estimated_completion_minutes: int
    created_at: str


class JobResultResponse(BaseModel):
    """Response for completed job result."""
    job_id: str
    status: str
    markdown_url: str | None = None
    confidence_score: float | None = None
    processing_time_seconds: int | None = None
    estimated_completion_at: str | None = None
    llm_cost: LLMCostInfo | None = None


@router.post("/submit", response_model=JobSubmissionResponse, status_code=status.HTTP_201_CREATED)
async def submit_document(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    job_service: JobService = Depends(get_job_service)
):
    """
    Submit a PDF document for processing.

    Args:
        file: PDF file (max 100MB)
        storage: Storage service (injected)
        queue: Queue service (injected)
        job_service: Job service (injected)

    Returns:
        Job submission details with job_id
    """

    # Store document in S3
    job_id, s3_key = await storage.store_document(file)

    # Create job in Redis
    await job_service.create_job(job_id, s3_key, status="pii_scanning")

    # Queue for PII processing
    await queue.queue_pii_job(job_id, s3_key)

    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    return JobSubmissionResponse(
        job_id=job_id,
        status="pii_scanning",
        estimated_completion_minutes=settings.estimated_processing_minutes,
        created_at=created_at
    )


@router.get("/{job_id}", response_model=DocumentStatusResponse)
async def get_job(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service)
):
    """
    Get current status of a processing job.

    Returns different response shapes based on job status:
    - pii_scanning/processing: Basic status + estimated time
    - awaiting_approval: PII findings + approval token
    - awaiting_correction_approval: Correction approval info + URLs
    - completed: Final markdown URL + correction decision

    Args:
        job_id: Job identifier
        job_service: Job service (injected)
        storage: Storage service (injected)

    Returns:
        Job status with relevant metadata and generated URLs
    """

    # Get job from Redis
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )

    # Base response
    response_data = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }

    # Add status-specific data
    job_status = job["status"]

    if job_status == "awaiting_approval":
        # PII approval pending
        response_data["pii_findings"] = job.get("pii_findings")
        response_data["approval_token"] = job.get("approval_token")
        response_data["approval_expires_at"] = job.get("approval_expires_at")

    elif job_status == "awaiting_correction_approval":
        # Correction approval pending
        correction_results = job.get("correction_results", [])

        # Count corrections by type
        by_type = {}
        total_corrections = 0
        for page_result in correction_results:
            for correction in page_result.get("corrections", []):
                corr_type = correction.get("type", "other")
                by_type[corr_type] = by_type.get(corr_type, 0) + 1
                total_corrections += 1

        # Build correction approval info
        token = job.get("correction_approval_token")
        response_data["correction_approval"] = {
            "token": token,
            "expires_at": job.get("correction_expires_at"),
            "total_corrections": total_corrections,
            "confidence_score": job.get("confidence_score", 0.0),
            "corrections_by_type": by_type,
            "review_url": f"/api/corrections/{job_id}/review?token={token}",
        }

        # Generate URLs from keys
        page_image_keys = job.get("page_image_keys", [])

        response_data["urls"] = {
            "original_markdown": await storage.generate_url(
                job["original_markdown_key"], bucket=storage.results_bucket
            ),
            "corrected_markdown": await storage.generate_url(
                job["corrected_markdown_key"], bucket=storage.results_bucket
            ),
            "page_images": [
                await storage.generate_url(key, bucket=storage.temp_bucket)
                for key in page_image_keys
            ],
        }

        # Add LLM cost info if available
        llm_cost = _build_llm_cost_info(job)
        if llm_cost:
            response_data["llm_cost"] = llm_cost

    elif job_status == "completed":
        # Job completed with approval decision
        response_data["confidence_score"] = job.get("confidence_score")

        # Correction decision info
        if job.get("correction_decision"):
            response_data["correction_decision"] = {
                "decision": job.get("correction_decision"),
                "reviewed_by": job.get("correction_reviewed_by"),
                "reviewed_at": job.get("correction_reviewed_at"),
                "justification": job.get("correction_justification"),
            }

        # Final markdown URL
        # After approval, markdown is moved to final location (job_id.md)
        final_key = f"{job_id}.md"
        response_data["urls"] = {
            "markdown": await storage.generate_url(
                final_key, bucket=storage.results_bucket
            )
        }

        # Add LLM cost info if available
        llm_cost = _build_llm_cost_info(job)
        if llm_cost:
            response_data["llm_cost"] = llm_cost

    elif job_status in ["pii_scanning", "processing"]:
        # In-progress status
        response_data["estimated_completion_minutes"] = (
            settings.estimated_processing_minutes
        )

    return DocumentStatusResponse(**response_data)


@router.get("/{job_id}/result", response_model=JobResultResponse)
async def get_job_result(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service)
):
    """
    Get result of a completed job.

    Args:
        job_id: Job identifier
        job_service: Job service (injected)
        storage: Storage service (injected)

    Returns:
        Job result with download URLs or processing status
    """

    # Get job from Redis
    job_data = await job_service.get_job(job_id)

    if not job_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )

    # If completed, return result URLs
    if job_data["status"] == "completed":
        return JobResultResponse(
            job_id=job_id,
            status="completed",
            markdown_url=storage.get_result_url(job_id, "md"),
            confidence_score=float(job_data.get("confidence_score", 0.0)),
            processing_time_seconds=int(job_data.get("processing_time_seconds", 0)),
            llm_cost=_build_llm_cost_info(job_data)
        )

    # If still processing, return estimated completion
    elif job_data["status"] in ["pii_scanning", "processing", "awaiting_approval"]:
        # Parse ISO format datetime and ensure timezone awareness
        # Replace Z suffix with +00:00 for proper parsing
        created_at_str = job_data["created_at"].replace("Z", "+00:00")
        created_at = datetime.fromisoformat(created_at_str)

        # If somehow still naive, add UTC timezone
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        estimated_completion = created_at + timedelta(minutes=settings.estimated_processing_minutes)

        return JobResultResponse(
            job_id=job_id,
            status=job_data["status"],
            estimated_completion_at=estimated_completion.isoformat() + "Z"
        )

    # If failed
    elif job_data["status"] == "failed":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Job {job_id} failed: {job_data.get('error', 'Unknown error')}"
        )

    # Unknown status
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Job {job_id} has unknown status: {job_data['status']}"
        )


@router.get("/{job_id}/pages/{page_num}/image")
async def get_page_image(
    job_id: str,
    page_num: int,
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service)
):
    """
    Get page preview image for correction review.

    Returns a redirect to the S3 URL where the page image is stored.
    Page images are PNG screenshots of each PDF page used by AI for
    text correction analysis.

    Args:
        job_id: Job identifier
        page_num: Page number (1-indexed)
        job_service: Job service (injected)
        storage: Storage service (injected)

    Returns:
        Redirect to page image URL

    Raises:
        404: Job not found or page doesn't exist
    """
    # Get job from Redis
    job_data = await job_service.get_job(job_id)

    if not job_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )

    # Get page image URLs from metadata
    metadata = job_data.get("metadata", {})
    if isinstance(metadata, str):
        import json
        metadata = json.loads(metadata)

    page_image_urls = metadata.get("page_image_urls", {})

    # Convert page_num to string for dict lookup (JSON stores keys as strings)
    page_key = str(page_num)

    if page_key not in page_image_urls:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Page {page_num} image not found for job {job_id}"
        )

    # Redirect to S3 URL
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=page_image_urls[page_key])
