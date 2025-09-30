"""Document processing endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..config import settings
from ..services import JobService, QueueService, StorageService
from ..dependencies import get_job_service, get_queue_service, get_storage_service

router = APIRouter(prefix="/api/documents", tags=["Documents"])


# Response models
class JobSubmissionResponse(BaseModel):
    """Response for document submission."""
    job_id: str
    status: str
    estimated_completion_minutes: int
    created_at: str


class PIIFinding(BaseModel):
    """PII finding structure."""
    entity_type: str
    text: str
    score: float


class JobStatusResponse(BaseModel):
    """Response for job status."""
    job_id: str
    status: str
    created_at: str
    updated_at: str
    pii_findings: Optional[list[PIIFinding]] = None
    approval_url: Optional[str] = None


class JobResultResponse(BaseModel):
    """Response for completed job result."""
    job_id: str
    status: str
    html_url: Optional[str] = None
    mdx_url: Optional[str] = None
    confidence_score: Optional[float] = None
    processing_time_seconds: Optional[int] = None
    estimated_completion_at: Optional[str] = None


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

    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return JobSubmissionResponse(
        job_id=job_id,
        status="pii_scanning",
        estimated_completion_minutes=settings.estimated_processing_minutes,
        created_at=created_at
    )


@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    job_service: JobService = Depends(get_job_service)
):
    """
    Get current status of a processing job.

    Args:
        job_id: Job identifier
        job_service: Job service (injected)

    Returns:
        Job status with relevant metadata
    """

    # Get job from Redis
    job_data = await job_service.get_job(job_id)

    if not job_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )

    # Build response based on status
    response = JobStatusResponse(
        job_id=job_data["job_id"],
        status=job_data["status"],
        created_at=job_data["created_at"],
        updated_at=job_data["updated_at"]
    )

    # Add PII findings if awaiting approval
    if job_data["status"] == "awaiting_approval" and "pii_findings" in job_data:
        response.pii_findings = [
            PIIFinding(**finding) for finding in job_data["pii_findings"]
        ]
        if "approval_url" in job_data:
            response.approval_url = job_data["approval_url"]

    return response


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
            html_url=storage.get_result_url(job_id, "html"),
            mdx_url=storage.get_result_url(job_id, "mdx"),
            confidence_score=float(job_data.get("confidence_score", 0.0)),
            processing_time_seconds=int(job_data.get("processing_time_seconds", 0))
        )

    # If still processing, return estimated completion
    elif job_data["status"] in ["pii_scanning", "processing", "awaiting_approval"]:
        created_at = datetime.fromisoformat(job_data["created_at"].replace("Z", ""))
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