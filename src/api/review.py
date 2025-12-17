"""Review API endpoints for remediation workflow.

Provides human review workflow for accessibility remediation:
- View observations and auto corrections
- Approve/reject individual review items
- Apply auto corrections to markdown
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.dependencies import (
    get_application_service,
    get_job_service,
    get_remediation_storage,
)
from src.services.application_service import ApplicationService
from src.services.job_service import JobService
from src.services.remediation_storage_service import RemediationStorageService

router = APIRouter(prefix="/api/documents", tags=["review"])


# --- Request/Response Models ---


class ObservationResponse(BaseModel):
    """Observation data for API response."""

    id: str
    agent: str
    page_num: int
    visual_description: str
    markup_description: str
    severity: str
    confidence: float
    status: str
    resolution: str | None
    category: str


class AutoCorrectionResponse(BaseModel):
    """Auto correction data for API response."""

    id: str
    observation_id: str
    search: str
    replace: str
    justification: str
    confidence: float
    applied: bool
    agent: str
    page_num: int | None


class ReviewSummary(BaseModel):
    """Summary of review state for a job."""

    job_id: str
    status: str
    substatus: str
    observation_count: int
    open_observations: int
    closed_observations: int
    auto_correction_count: int
    applied_corrections: int
    pending_corrections: int
    markdown_url: str | None


class CloseObservationRequest(BaseModel):
    """Request to close an observation."""

    resolution: str = Field(
        ..., description="Resolution: 'fixed', 'kept_original', or 'skipped'"
    )
    reviewed_by: str = Field(..., min_length=1)


# --- Endpoints ---


@router.get("/{job_id}/review")
async def get_review_summary(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    storage: RemediationStorageService = Depends(get_remediation_storage),
) -> ReviewSummary:
    """Get review summary and status for a job."""
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    observations = await storage.load_observations(job_id)
    corrections = await storage.load_auto_corrections(job_id)

    return ReviewSummary(
        job_id=job_id,
        status=job.get("status", "unknown"),
        substatus=job.get("substatus", ""),
        observation_count=len(observations),
        open_observations=sum(1 for o in observations if o.status == "open"),
        closed_observations=sum(1 for o in observations if o.status == "closed"),
        auto_correction_count=len(corrections),
        applied_corrections=sum(1 for c in corrections if c.applied),
        pending_corrections=sum(1 for c in corrections if not c.applied),
        markdown_url=job.get("markdown_url"),
    )


@router.get("/{job_id}/observations")
async def list_observations(
    job_id: str,
    status: str | None = None,
    agent: str | None = None,
    storage: RemediationStorageService = Depends(get_remediation_storage),
) -> list[ObservationResponse]:
    """List observations for a job, optionally filtered."""
    observations = await storage.load_observations(job_id)

    if status:
        observations = [o for o in observations if o.status == status]
    if agent:
        observations = [o for o in observations if o.agent == agent]

    return [
        ObservationResponse(
            id=o.id,
            agent=o.agent,
            page_num=o.location.page_num,
            visual_description=o.visual_description,
            markup_description=o.markup_description,
            severity=o.severity,
            confidence=o.confidence,
            status=o.status,
            resolution=o.resolution,
            category=o.category,
        )
        for o in observations
    ]


@router.get("/{job_id}/corrections")
async def list_corrections(
    job_id: str,
    applied: bool | None = None,
    storage: RemediationStorageService = Depends(get_remediation_storage),
) -> list[AutoCorrectionResponse]:
    """List auto corrections for a job, optionally filtered."""
    corrections = await storage.load_auto_corrections(job_id)

    if applied is not None:
        corrections = [c for c in corrections if c.applied == applied]

    return [
        AutoCorrectionResponse(
            id=c.id,
            observation_id=c.observation_id,
            search=c.search,
            replace=c.replace,
            justification=c.justification,
            confidence=c.confidence,
            applied=c.applied,
            agent=c.agent,
            page_num=c.page_num,
        )
        for c in corrections
    ]


@router.post("/{job_id}/observations/{observation_id}/close")
async def close_observation(
    job_id: str,
    observation_id: str,
    request: CloseObservationRequest,
    storage: RemediationStorageService = Depends(get_remediation_storage),
) -> dict[str, Any]:
    """Close an observation with a resolution."""
    valid_resolutions = ["fixed", "kept_original", "skipped"]
    if request.resolution not in valid_resolutions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolution. Must be one of: {valid_resolutions}",
        )

    success = await storage.close_observation(
        job_id=job_id,
        observation_id=observation_id,
        resolution=request.resolution,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Observation not found or already closed",
        )

    return {
        "status": "closed",
        "observation_id": observation_id,
        "resolution": request.resolution,
    }


class ApplicationResponse(BaseModel):
    """Response from application endpoint."""

    status: str
    applied_count: int
    failed_count: int
    skipped_count: int
    failed_corrections: list[dict[str, Any]]
    final_markdown_url: str | None
    validation_warnings: list[str]
    open_observations: int


@router.post("/{job_id}/apply")
async def trigger_application(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    application_service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    """Apply all unapplied auto corrections to the markdown document.

    This endpoint:
    1. Loads all unapplied auto corrections
    2. Applies each using search-replace (exact match, then whitespace-normalized)
    3. Closes associated observations as fixed
    4. Saves final markdown to S3
    5. Updates job status to completed

    Returns:
        Application results including counts and any failures
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check job is in review state before applying
    current_substatus = job.get("substatus", "")
    if current_substatus != "awaiting_review":
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state (current substatus: {current_substatus or 'none'})"
        )

    # Update substatus to 'applying'
    await job_service.update_job_status(job_id, "processing", substatus="applying")

    try:
        # Apply auto corrections
        result = await application_service.apply_auto_corrections(job_id)

        # Count open observations
        open_obs = await application_service.count_open_observations(job_id)

        # Determine final status
        if result.failed_count > 0 and result.applied_count == 0:
            # All failed
            await job_service.update_job_status(
                job_id,
                "failed",
                error=f"All {result.failed_count} corrections failed to apply",
            )
            return ApplicationResponse(
                status="failed",
                applied_count=result.applied_count,
                failed_count=result.failed_count,
                skipped_count=result.skipped_count,
                failed_corrections=result.failed_corrections,
                final_markdown_url=result.final_markdown_url,
                validation_warnings=result.validation_warnings,
                open_observations=open_obs,
            )

        # Success (possibly with some failures)
        await job_service.update_job_status(
            job_id,
            "completed",
            substatus="",
            markdown_url=result.final_markdown_url or "",
            applied_corrections=result.applied_count,
            failed_corrections_count=result.failed_count,
            open_observations=open_obs,
            validation_warnings=len(result.validation_warnings),
        )

        return ApplicationResponse(
            status="completed",
            applied_count=result.applied_count,
            failed_count=result.failed_count,
            skipped_count=result.skipped_count,
            failed_corrections=result.failed_corrections,
            final_markdown_url=result.final_markdown_url,
            validation_warnings=result.validation_warnings,
            open_observations=open_obs,
        )

    except ValueError as e:
        # No markdown found or other validation error
        await job_service.update_job_status(
            job_id,
            "failed",
            error=str(e),
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Unexpected error
        await job_service.update_job_status(
            job_id,
            "failed",
            error=f"Application error: {str(e)}",
        )
        raise HTTPException(status_code=500, detail=f"Application failed: {str(e)}")
