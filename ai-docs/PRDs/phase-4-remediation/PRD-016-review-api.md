# PRD-016: Review API & Workflow

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 4 days
**Dependencies**: PRD-011 (Data Models), PRD-015 (Consolidation Service)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Problem Statement

After consolidation generates proposals, humans need to review and approve them before changes are applied. The review workflow supports:

1. **Viewing proposals** - See all pending changes with diffs and justifications
2. **Accepting/Rejecting** - Individual or batch approval decisions
3. **Editing** - Human can modify proposals or submit new observations
4. **Tracking progress** - See what's approved, pending, manual

This PRD covers the API endpoints and business logic for the review workflow.

## Success Criteria

- [ ] API endpoints for listing observations and proposals
- [ ] Approve/reject endpoints with validation
- [ ] Edit endpoint supporting before/after or before/comment patterns
- [ ] Batch approval for auto-routed proposals
- [ ] Apply endpoint triggers application phase
- [ ] Review state persisted correctly

## Technical Requirements

### New API Endpoints

```python
# src/api/review.py

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.dependencies import (
    get_job_service,
    get_remediation_storage,
    get_consolidation_service,
)
from src.services.job_service import JobService
from src.services.remediation_storage_service import RemediationStorageService
from src.services.consolidation_service import ConsolidationService
from src.shared.models.observation import Observation
from src.shared.models.proposal import Proposal

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
    review_notes: str = Field(..., min_length=1, description="Reason for rejection required")


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
        description="Specific proposals to approve, or None for all auto-routed"
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
            detail=f"Cannot approve proposal with status: {proposal.status}"
        )

    # Update proposal
    proposal.status = "approved"
    proposal.reviewed_by = request.reviewed_by
    proposal.reviewed_at = datetime.utcnow()
    proposal.review_notes = request.review_notes

    await storage.save_proposals(job_id, proposals)

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    approved = sum(1 for p in proposals if p.status == "approved")
    await job_service.update_job_status(
        job_id, "processing",
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
            detail=f"Cannot reject proposal with status: {proposal.status}"
        )

    # Update proposal
    proposal.status = "rejected"
    proposal.reviewed_by = request.reviewed_by
    proposal.reviewed_at = datetime.utcnow()
    proposal.review_notes = request.review_notes

    await storage.save_proposals(job_id, proposals)

    # Update observations - they remain open (not resolved)
    observations = await storage.load_observations(job_id)
    for obs in observations:
        if obs.id in proposal.resolves and obs.status == "open":
            # Keep as open or mark as wont_fix based on rejection
            pass

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    rejected = sum(1 for p in proposals if p.status == "rejected")
    await job_service.update_job_status(
        job_id, "processing",
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
            status_code=400,
            detail="'before' text not found in document"
        )

    if request.after is not None:
        # Direct edit: create proposal and auto-approve
        new_proposal = Proposal(
            id=str(uuid.uuid4()),
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
            reviewed_at=datetime.utcnow(),
            review_notes=request.comment,
        )

        # Add to proposals
        proposals = await storage.load_proposals(job_id)
        proposals.append(new_proposal)
        await storage.save_proposals(job_id, proposals)

        return {
            "status": "created_and_approved",
            "proposal_id": new_proposal.id,
            "message": "Human edit approved automatically"
        }

    else:
        # Create observation and reconsolidate
        observation = Observation(
            id=str(uuid.uuid4()),
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
        new_proposal = await consolidation.reconsolidate_observation(
            job_id=job_id,
            observation=observation,
            markdown=markdown,
        )

        if new_proposal:
            return {
                "status": "observation_created",
                "observation_id": observation.id,
                "proposal_id": new_proposal.id,
                "message": "AI generated proposal from your observation"
            }
        else:
            return {
                "status": "observation_created",
                "observation_id": observation.id,
                "proposal_id": None,
                "message": "Observation saved, but AI could not generate proposal"
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
        targets = [p for p in proposals if p.status == "pending" and p.route == "auto"]

    approved_count = 0
    for proposal in targets:
        if proposal.status == "pending":
            proposal.status = "approved"
            proposal.reviewed_by = request.reviewed_by
            proposal.reviewed_at = datetime.utcnow()
            approved_count += 1

    await storage.save_proposals(job_id, proposals)

    # Update job counts
    pending = sum(1 for p in proposals if p.status == "pending")
    approved = sum(1 for p in proposals if p.status == "approved")
    await job_service.update_job_status(
        job_id, "processing",
        pending_proposals=pending,
        approved_proposals=approved,
    )

    return {
        "status": "batch_approved",
        "approved_count": approved_count,
        "remaining_pending": pending,
    }


@router.post("/{job_id}/apply")
async def trigger_application(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
) -> dict[str, Any]:
    """Trigger application of approved proposals.

    This endpoint transitions the job to 'applying' substatus
    and triggers the application phase (PRD-017).
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("substatus") != "awaiting_review":
        raise HTTPException(
            status_code=400,
            detail=f"Job not in review state, current substatus: {job.get('substatus')}"
        )

    # Update substatus to trigger application
    await job_service.update_job_status(
        job_id, "processing", substatus="applying"
    )

    # Application will be handled by processing worker or dedicated worker
    # See PRD-017

    return {
        "status": "applying",
        "message": "Application phase started"
    }
```

### Router Registration

```python
# src/api/__init__.py or src/main.py

from src.api.review import router as review_router

app.include_router(review_router)
```

### Review State Tracking

```python
# Additional fields in job hash (Redis)

{
    "substatus": "awaiting_review",
    "observation_count": 12,
    "proposal_count": 8,
    "pending_proposals": 6,
    "approved_proposals": 2,
    "rejected_proposals": 0,
    "applied_proposals": 0,
    "manual_observations": 2,
    "review_started_at": "2024-12-10T10:30:00Z",
}
```

## Acceptance Criteria

### 1. Review Summary Endpoint
- [ ] Returns accurate counts
- [ ] Includes markdown URL
- [ ] Shows current substatus

### 2. List Endpoints
- [ ] Observations filterable by status, agent
- [ ] Proposals filterable by status, route
- [ ] Returns all required fields

### 3. Approve/Reject
- [ ] Only pending proposals can be approved/rejected
- [ ] Reviewer info recorded
- [ ] Review notes captured
- [ ] Job counts updated

### 4. Edit Endpoint
- [ ] before+after creates auto-approved proposal
- [ ] before+comment creates observation and reconsolidates
- [ ] Validates 'before' exists in document
- [ ] Returns appropriate response

### 5. Batch Approve
- [ ] Approves all auto-routed by default
- [ ] Can specify specific proposal IDs
- [ ] Counts updated correctly

### 6. Apply Trigger
- [ ] Validates job is in awaiting_review
- [ ] Updates substatus to applying
- [ ] Returns confirmation

## Deliverables

### Files to Create

```
src/api/
└── review.py                   # Review API endpoints

tests/api/
└── test_review_api.py
```

### Files to Modify

```
src/main.py                     # Register review router
src/dependencies.py             # Add review-related dependencies
```

## Technical Notes

### Authentication

Review endpoints should require authentication. Consider:
- API key authentication (existing)
- Job-specific tokens (like PII approval tokens)
- Role-based access for batch operations

### Concurrency

Multiple reviewers could approve/reject simultaneously:
- Use optimistic locking on proposal status
- Return conflict error if status already changed
- Consider WebSocket for real-time updates

### Audit Trail

All review actions should be logged:
- Who reviewed
- When
- What decision
- Any notes

This is captured in proposal fields but consider dedicated audit log for compliance.

## Definition of Done

- [ ] All review endpoints implemented
- [ ] Request/response models defined
- [ ] Validation works correctly
- [ ] Job state updates correctly
- [ ] Edit workflow (both paths) works
- [ ] Batch approve works
- [ ] Integration tests pass
- [ ] API documentation generated
- [ ] Authentication integrated
