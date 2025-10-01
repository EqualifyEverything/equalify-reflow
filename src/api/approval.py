"""Approval workflow API endpoints.

Provides endpoints for reviewing PII-flagged documents and
submitting approval/denial decisions.
"""

from datetime import datetime
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from ..dependencies import get_redis_client, get_s3_client
from ..services.approval_service import ApprovalService
from ..services.job_service import JobService
from ..services.queue_service import QueueService


router = APIRouter(prefix="/api/approval", tags=["Approval"])


class ApprovalDecisionInput(BaseModel):
    """Input model for approval/denial decisions.

    Attributes:
        decision: Binary approval decision
        justification: Optional explanation (10-1000 chars)
        reviewed_by: Reviewer identifier (email or user ID)

    Example:
        >>> decision = ApprovalDecisionInput(
        ...     decision="approved",
        ...     justification="Instructor contact info in syllabus is acceptable",
        ...     reviewed_by="faculty@uic.edu"
        ... )
    """
    decision: Literal["approved", "denied"] = Field(
        ...,
        description="Approval or denial of processing"
    )
    justification: str | None = Field(
        None,
        min_length=10,
        max_length=1000,
        description="Optional explanation for decision"
    )
    reviewed_by: str = Field(
        ...,
        min_length=3,
        description="Reviewer email or user ID"
    )


class ReviewDetailsResponse(BaseModel):
    """Response model for review endpoint.

    Provides all information needed for human review of PII findings.

    Attributes:
        job_id: Unique job identifier
        status: Current job status
        pii_findings: List of detected PII entities
        created_at: UTC timestamp when job created
        expires_at: UTC timestamp when approval link expires
        s3_key: S3 object key (for reference)
    """
    job_id: str
    status: str
    pii_findings: list
    created_at: str
    expires_at: str
    s3_key: str


class ApprovalResponse(BaseModel):
    """Response model for approval submission.

    Attributes:
        message: Success message
        job_id: Job identifier
        decision: Decision that was recorded
    """
    message: str
    job_id: str
    decision: str


@router.get(
    "/review/{token}",
    response_model=ReviewDetailsResponse,
    summary="Get job details for review",
    description="Retrieve job details and PII findings for human review interface"
)
async def get_review_details(
    token: str,
    redis_client=Depends(get_redis_client),
    s3_client=Depends(get_s3_client)
) -> ReviewDetailsResponse:
    """Get job details and PII findings for review.

    Validates approval token and returns job information needed
    for human review of PII-flagged documents.

    Args:
        token: Approval token from review URL
        redis_client: Redis client (injected)
        s3_client: S3 client (injected)

    Returns:
        ReviewDetailsResponse with job details and PII findings

    Raises:
        HTTPException 404: Invalid or expired token
        HTTPException 500: Server error

    Example:
        GET /api/review/abc123def456...
        Returns:
        {
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "awaiting_approval",
            "pii_findings": [...],
            "created_at": "2024-01-15T10:00:00Z",
            "expires_at": "2024-01-15T14:00:00Z",
            "s3_key": "temp/upload-123.pdf"
        }
    """
    try:
        # Initialize services
        job_service = JobService(redis_client)
        queue_service = QueueService(redis_client)
        approval_service = ApprovalService(
            redis_client=redis_client,
            s3_client=s3_client,
            job_service=job_service,
            queue_service=queue_service
        )

        # Validate token and get job
        job = await approval_service.validate_approval_token(token)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="Invalid or expired approval token"
            )

        # Return review details
        return ReviewDetailsResponse(
            job_id=job["job_id"],
            status=job["status"],
            pii_findings=job.get("pii_findings", []),
            created_at=job["created_at"],
            expires_at=job.get("approval_expires_at", ""),
            s3_key=job["s3_key"]
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve review details: {str(e)}"
        )


@router.post(
    "/{token}/approve",
    response_model=ApprovalResponse,
    summary="Submit approval decision",
    description="Submit approval or denial decision for PII-flagged document"
)
async def submit_approval(
    token: str,
    decision_input: ApprovalDecisionInput,
    redis_client=Depends(get_redis_client),
    s3_client=Depends(get_s3_client)
) -> ApprovalResponse:
    """Submit approval or denial decision.

    Validates approval token and processes decision:
    - Approved: Routes to processing queue
    - Denied: Cleans up S3 files, marks as denied

    Args:
        token: Approval token from review URL
        decision_input: Approval decision details
        redis_client: Redis client (injected)
        s3_client: S3 client (injected)

    Returns:
        ApprovalResponse with success message

    Raises:
        HTTPException 404: Invalid or expired token
        HTTPException 500: Server error processing decision

    Example:
        POST /api/approve/abc123def456...
        Body:
        {
            "decision": "approved",
            "justification": "Instructor name in syllabus is acceptable",
            "reviewed_by": "faculty@uic.edu"
        }
        Returns:
        {
            "message": "Approval decision recorded successfully",
            "job_id": "550e8400-e29b-41d4-a716-446655440000",
            "decision": "approved"
        }
    """
    try:
        # Initialize services
        job_service = JobService(redis_client)
        queue_service = QueueService(redis_client)
        approval_service = ApprovalService(
            redis_client=redis_client,
            s3_client=s3_client,
            job_service=job_service,
            queue_service=queue_service
        )

        # Validate token and get job
        job = await approval_service.validate_approval_token(token)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="Invalid or expired approval token"
            )

        job_id = job["job_id"]

        # Process decision
        await approval_service.process_approval_decision(
            job_id=job_id,
            decision=decision_input.decision,
            justification=decision_input.justification,
            reviewed_by=decision_input.reviewed_by
        )

        # Return success response
        action = "approved for processing" if decision_input.decision == "approved" else "denied and cleaned up"
        return ApprovalResponse(
            message=f"Job {action} successfully",
            job_id=job_id,
            decision=decision_input.decision
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process approval decision: {str(e)}"
        )
