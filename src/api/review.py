"""Review API endpoints for remediation workflow (PRD-016).

Provides human review workflow for accessibility remediation:
- View observations and proposals
- Approve/reject individual proposals
- Batch approve auto-routed proposals
- Edit proposals or submit new observations
- Trigger application of approved changes
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.dependencies import (
    get_application_service,
    get_consolidation_service,
    get_job_service,
    get_remediation_storage,
)
from src.services.application_service import ApplicationService
from src.services.consolidation_service import ConsolidationService
from src.services.job_service import JobService
from src.services.remediation_storage_service import RemediationStorageService
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff

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
    route: str
    manual_reason: str | None


class ProposalResponse(BaseModel):
    """Proposal data for API response."""

    id: str
    resolves: list[str]
    search: str
    replace: str
    justification: str
    page_nums: list[int]
    estimated_impact: str
    route: str
    status: str
    confidence: float


class ReviewSummary(BaseModel):
    """Summary of review state for a job."""

    job_id: str
    status: str
    substatus: str
    observation_count: int
    proposal_count: int
    pending_proposals: int
    approved_proposals: int
    rejected_proposals: int
    applied_proposals: int
    manual_observations: int
    markdown_url: str | None


class ApproveRequest(BaseModel):
    """Request to approve a proposal."""

    reviewed_by: str = Field(..., min_length=1)
    review_notes: str | None = None


class RejectRequest(BaseModel):
    """Request to reject a proposal."""

    reviewed_by: str = Field(..., min_length=1)
    review_notes: str = Field(
        ..., min_length=1, description="Reason for rejection required"
    )


class EditRequest(BaseModel):
    """Request to edit/create observation via human input."""

    before: str = Field(..., min_length=1, description="Text to find in document")
    after: str | None = Field(default=None, description="Replacement text (optional)")
    comment: str = Field(..., min_length=1, description="Explanation for the edit")
    reviewed_by: str = Field(..., min_length=1)


class BatchApproveRequest(BaseModel):
    """Request to batch-approve auto-routed proposals."""

    reviewed_by: str = Field(..., min_length=1)
    proposal_ids: list[str] | None = Field(
        default=None,
        description="Specific proposals to approve, or None for all auto-routed",
    )


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
    proposals = await storage.load_proposals(job_id)

    return ReviewSummary(
        job_id=job_id,
        status=job.get("status", "unknown"),
        substatus=job.get("substatus", ""),
        observation_count=len(observations),
        proposal_count=len(proposals),
        pending_proposals=sum(1 for p in proposals if p.status == "pending"),
        approved_proposals=sum(1 for p in proposals if p.status == "approved"),
        rejected_proposals=sum(1 for p in proposals if p.status == "rejected"),
        applied_proposals=sum(1 for p in proposals if p.status == "applied"),
        manual_observations=sum(1 for o in observations if o.status == "manual"),
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
            route=o.route,
            manual_reason=o.manual_reason,
        )
        for o in observations
    ]


@router.get("/{job_id}/proposals")
async def list_proposals(
    job_id: str,
    status: str | None = None,
    route: str | None = None,
    storage: RemediationStorageService = Depends(get_remediation_storage),
) -> list[ProposalResponse]:
    """List proposals for a job, optionally filtered."""
    proposals = await storage.load_proposals(job_id)

    if status:
        proposals = [p for p in proposals if p.status == status]
    if route:
        proposals = [p for p in proposals if p.route == route]

    return [
        ProposalResponse(
            id=p.id,
            resolves=p.resolves,
            search=p.diff.search,
            replace=p.diff.replace,
            justification=p.justification,
            page_nums=p.page_nums,
            estimated_impact=p.estimated_impact,
            route=p.route,
            status=p.status,
            confidence=0.8,  # Could be calculated from resolved observations
        )
        for p in proposals
    ]


@router.post("/{job_id}/proposals/{proposal_id}/approve")
async def approve_proposal(
    job_id: str,
    proposal_id: str,
    request: ApproveRequest,
    storage: RemediationStorageService = Depends(get_remediation_storage),
    job_service: JobService = Depends(get_job_service),
) -> dict[str, Any]:
    """Approve a single proposal."""
    proposals = await storage.load_proposals(job_id)
    proposal = next((p for p in proposals if p.id == proposal_id), None)

    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if proposal.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve proposal with status: {proposal.status}",
        )

    # Update proposal
    proposal.status = "approved"
    proposal.reviewed_by = request.reviewed_by
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_notes = request.review_notes

    await storage.save_proposals(job_id, proposals)

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    approved = sum(1 for p in proposals if p.status == "approved")
    await job_service.update_job_status(
        job_id,
        "processing",
        pending_proposals=pending,
        approved_proposals=approved,
    )

    return {"status": "approved", "proposal_id": proposal_id}


@router.post("/{job_id}/proposals/{proposal_id}/reject")
async def reject_proposal(
    job_id: str,
    proposal_id: str,
    request: RejectRequest,
    storage: RemediationStorageService = Depends(get_remediation_storage),
    job_service: JobService = Depends(get_job_service),
) -> dict[str, Any]:
    """Reject a single proposal."""
    proposals = await storage.load_proposals(job_id)
    proposal = next((p for p in proposals if p.id == proposal_id), None)

    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    if proposal.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject proposal with status: {proposal.status}",
        )

    # Update proposal
    proposal.status = "rejected"
    proposal.reviewed_by = request.reviewed_by
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_notes = request.review_notes

    await storage.save_proposals(job_id, proposals)

    # Update observations - they remain open (not resolved)
    # Note: Observations associated with rejected proposal stay open
    # so they can be addressed by other proposals or manual fixes

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    rejected = sum(1 for p in proposals if p.status == "rejected")
    await job_service.update_job_status(
        job_id,
        "processing",
        pending_proposals=pending,
        rejected_proposals=rejected,
    )

    return {"status": "rejected", "proposal_id": proposal_id}


@router.post("/{job_id}/proposals/{proposal_id}/edit")
async def edit_proposal(
    job_id: str,
    proposal_id: str,
    request: EditRequest,
    storage: RemediationStorageService = Depends(get_remediation_storage),
    consolidation: ConsolidationService = Depends(get_consolidation_service),
) -> dict[str, Any]:
    """Edit a proposal or submit human observation.

    If 'after' is provided: Create direct proposal, auto-approve
    If 'after' is None: Create observation, run consolidation, return new proposal
    """
    # Load current markdown
    markdown = await storage.load_current_markdown(job_id)
    if not markdown:
        raise HTTPException(status_code=404, detail="Markdown not found")

    # Validate 'before' exists in markdown
    if request.before not in markdown:
        raise HTTPException(
            status_code=400, detail="'before' text not found in document"
        )

    if request.after is not None:
        # Direct edit: create proposal and auto-approve
        new_proposal = Proposal(
            id=str(uuid4()),
            job_id=job_id,
            resolves=[],  # Human-initiated, not resolving agent observations
            diff=SearchReplaceDiff(
                search=request.before,
                replace=request.after,
            ),
            justification=f"Human edit: {request.comment}",
            route="auto",
            status="approved",  # Auto-approved since human provided exact fix
            reviewed_by=request.reviewed_by,
            reviewed_at=datetime.now(UTC),
            review_notes=request.comment,
        )

        # Add to proposals
        proposals = await storage.load_proposals(job_id)
        proposals.append(new_proposal)
        await storage.save_proposals(job_id, proposals)

        return {
            "status": "created_and_approved",
            "proposal_id": new_proposal.id,
            "message": "Human edit approved automatically",
        }

    else:
        # Create observation and reconsolidate
        observation = Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="human",
            source="human",
            visual_description=request.comment,
            markup_description=f"Current text: {request.before[:100]}...",
            location=ObservationLocation(
                location_type="range",
                value=request.before[:50],
                page_num=1,  # TODO: detect page from markdown position
            ),
            confidence=1.0,  # Human observations are high confidence
            severity="major",
            route="auto",
            human_comment=request.comment,
        )

        # Save observation
        observations = await storage.load_observations(job_id)
        observations.append(observation)
        await storage.save_observations(job_id, observations)

        # Reconsolidate
        ai_proposal = await consolidation.reconsolidate_observation(
            job_id=job_id,
            observation=observation,
            markdown=markdown,
        )

        if ai_proposal:
            return {
                "status": "observation_created",
                "observation_id": observation.id,
                "proposal_id": ai_proposal.id,
                "message": "AI generated proposal from your observation",
            }
        else:
            return {
                "status": "observation_created",
                "observation_id": observation.id,
                "proposal_id": None,
                "message": "Observation saved, but AI could not generate proposal",
            }


@router.post("/{job_id}/proposals/batch-approve")
async def batch_approve_proposals(
    job_id: str,
    request: BatchApproveRequest,
    storage: RemediationStorageService = Depends(get_remediation_storage),
    job_service: JobService = Depends(get_job_service),
) -> dict[str, Any]:
    """Batch approve multiple proposals (typically all auto-routed)."""
    proposals = await storage.load_proposals(job_id)

    # Filter to target proposals
    if request.proposal_ids:
        targets = [p for p in proposals if p.id in request.proposal_ids]
    else:
        # Default: all pending auto-routed
        targets = [
            p for p in proposals if p.status == "pending" and p.route == "auto"
        ]

    approved_count = 0
    for proposal in targets:
        if proposal.status == "pending":
            proposal.status = "approved"
            proposal.reviewed_by = request.reviewed_by
            proposal.reviewed_at = datetime.now(UTC)
            approved_count += 1

    await storage.save_proposals(job_id, proposals)

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    approved = sum(1 for p in proposals if p.status == "approved")
    await job_service.update_job_status(
        job_id,
        "processing",
        pending_proposals=pending,
        approved_proposals=approved,
    )

    return {
        "status": "batch_approved",
        "approved_count": approved_count,
        "remaining_pending": pending,
    }


class ApplicationResponse(BaseModel):
    """Response from application endpoint."""

    status: str
    applied_count: int
    failed_count: int
    skipped_count: int
    failed_proposals: list[dict[str, Any]]
    final_markdown_url: str | None
    validation_warnings: list[str]
    manual_observations: int


@router.post("/{job_id}/apply")
async def trigger_application(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    application_service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    """Apply all approved proposals to the markdown document.

    This endpoint:
    1. Loads all approved proposals
    2. Applies each using search-replace (exact match, then whitespace-normalized)
    3. Updates observation status to resolved
    4. Saves final markdown to S3
    5. Updates job status to completed

    Returns:
        Application results including counts and any failures
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Update substatus to 'applying'
    await job_service.update_job_status(job_id, "processing", substatus="applying")

    try:
        # Apply approved proposals
        result = await application_service.apply_approved_proposals(job_id)

        # Count manual observations
        manual_obs = await application_service.count_manual_observations(job_id)

        # Determine final status
        if result.failed_count > 0 and result.applied_count == 0:
            # All failed
            await job_service.update_job_status(
                job_id,
                "failed",
                error=f"All {result.failed_count} proposals failed to apply",
            )
            return ApplicationResponse(
                status="failed",
                applied_count=result.applied_count,
                failed_count=result.failed_count,
                skipped_count=result.skipped_count,
                failed_proposals=result.failed_proposals,
                final_markdown_url=result.final_markdown_url,
                validation_warnings=result.validation_warnings,
                manual_observations=manual_obs,
            )

        # Success (possibly with some failures)
        await job_service.update_job_status(
            job_id,
            "completed",
            substatus="",
            markdown_url=result.final_markdown_url or "",
            applied_proposals=result.applied_count,
            failed_proposals_count=result.failed_count,
            manual_observations=manual_obs,
            validation_warnings=len(result.validation_warnings),
        )

        return ApplicationResponse(
            status="completed",
            applied_count=result.applied_count,
            failed_count=result.failed_count,
            skipped_count=result.skipped_count,
            failed_proposals=result.failed_proposals,
            final_markdown_url=result.final_markdown_url,
            validation_warnings=result.validation_warnings,
            manual_observations=manual_obs,
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
