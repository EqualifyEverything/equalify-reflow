"""Approval workflow models for PII-flagged documents."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    """Faculty approval decision for PII-flagged document.

    Captures the manual review decision when PII is detected in course materials.
    Includes accountability tracking via reviewer identification and timestamp.

    Attributes:
        job_id: Unique job identifier (UUID)
        decision: Approval or denial of processing
        justification: Required explanation for decision
        reviewed_by: Identifier of person making decision (email or user ID)
        reviewed_at: UTC timestamp of decision

    Example:
        >>> approval = ApprovalRequest(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     decision="approved",
        ...     justification="PII is instructor name in course syllabus - acceptable",
        ...     reviewed_by="faculty@uic.edu",
        ...     reviewed_at=datetime.utcnow()
        ... )
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    decision: Literal["approved", "denied"] = Field(
        ...,
        description="Binary approval decision"
    )
    justification: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Required explanation for decision"
    )
    reviewed_by: str = Field(
        ...,
        min_length=3,
        description="Reviewer identifier (email or user ID)"
    )
    reviewed_at: datetime = Field(
        ...,
        description="UTC timestamp of review"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "decision": "approved",
                "justification": "PII detected is instructor contact information",
                "reviewed_by": "professor@uic.edu",
                "reviewed_at": "2024-01-15T10:30:00Z"
            }
        }


class ApprovalDecision(BaseModel):
    """Internal tracking model for approval workflow state.

    Extended approval information stored in Redis for workflow management.
    Includes expiration tracking for approval tokens.

    Attributes:
        approval_token: Unique token for approval URL
        expires_at: UTC timestamp when approval link expires
        decision: Optional approval request once submitted
        created_at: UTC timestamp when approval was requested
    """
    approval_token: str = Field(
        ...,
        min_length=32,
        max_length=64,
        description="Secure approval token"
    )
    expires_at: datetime = Field(
        ...,
        description="UTC expiration timestamp"
    )
    decision: ApprovalRequest | None = Field(
        default=None,
        description="Approval decision once submitted"
    )
    created_at: datetime = Field(
        ...,
        description="UTC creation timestamp"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "approval_token": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
                "expires_at": "2024-01-16T10:30:00Z",
                "decision": None,
                "created_at": "2024-01-15T10:30:00Z"
            }
        }