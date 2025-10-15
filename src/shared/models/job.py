"""Core job models for document conversion tracking."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


from .pii import PIIFinding
from .approval import ApprovalRequest


# Valid state transitions for job status state machine
VALID_TRANSITIONS = {
    "pii_scanning": ["awaiting_approval", "processing", "failed"],
    "awaiting_approval": ["processing", "denied", "failed"],
    "processing": ["completed", "failed"],
    "completed": [],  # Terminal state
    "failed": [],     # Terminal state
    "denied": []      # Terminal state
}


class JobSubmission(BaseModel):
    """Initial job submission data for PDF conversion.

    Created when API receives a new PDF upload. Captures all
    metadata needed to track and process the document.

    Attributes:
        job_id: Unique job identifier (UUID format)
        s3_key: S3 object key for uploaded PDF
        created_at: UTC timestamp of submission
        file_size_bytes: PDF file size in bytes
        original_filename: User-provided filename

    Validation Rules:
        - job_id must be valid UUID format
        - file_size_bytes between 1 byte and 100MB
        - s3_key must start with 'temp/' prefix

    Example:
        >>> submission = JobSubmission(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     s3_key="temp/550e8400.../syllabus.pdf",
        ...     created_at=datetime.now(timezone.utc),
        ...     file_size_bytes=2456789,
        ...     original_filename="CS101_Syllabus_Fall2024.pdf"
        ... )
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    s3_key: str = Field(
        ...,
        min_length=1,
        description="S3 object key for PDF"
    )
    created_at: datetime = Field(
        ...,
        description="UTC submission timestamp"
    )
    file_size_bytes: int = Field(
        ...,
        gt=0,
        le=100_000_000,  # 100MB max
        description="File size in bytes"
    )
    original_filename: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original uploaded filename"
    )

    @field_validator('s3_key')
    @classmethod
    def validate_s3_key(cls, v: str) -> str:
        """Validate S3 key starts with temp/ prefix."""
        if not v.startswith('temp/'):
            raise ValueError('Temporary uploads must use temp/ prefix')
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "s3_key": "temp/550e8400-e29b-41d4-a716-446655440000/document.pdf",
                "created_at": "2024-01-15T10:30:00Z",
                "file_size_bytes": 2456789,
                "original_filename": "Course_Syllabus.pdf"
            }
        }
    )


class JobStatus(BaseModel):
    """Complete job status with workflow state and results.

    Central data model for tracking conversion jobs through the entire
    pipeline. Stored in Redis and updated as job progresses through states.

    State Machine:
        - pii_scanning → awaiting_approval | processing | failed
        - awaiting_approval → processing | denied | failed
        - processing → completed | failed
        - completed (terminal)
        - failed (terminal)
        - denied (terminal)

    Attributes:
        job_id: Unique job identifier (UUID)
        status: Current workflow state
        created_at: UTC timestamp of initial submission
        updated_at: UTC timestamp of last status change
        pii_findings: Optional list of detected PII entities
        approval_token: Optional token for approval workflow
        expires_at: Optional expiration for approval token
        markdown_url: Optional S3 URL for Markdown output
        confidence_score: Optional AI confidence score
        error_message: Optional error details
        approval_decision: Optional approval request details

    Example:
        >>> status = JobStatus(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     status="processing",
        ...     created_at=datetime.now(timezone.utc),
        ...     updated_at=datetime.now(timezone.utc),
        ...     pii_findings=None,
        ...     approval_token=None
        ... )
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    status: Literal[
        "pii_scanning",
        "awaiting_approval",
        "processing",
        "completed",
        "failed",
        "denied"
    ] = Field(
        ...,
        description="Current workflow state"
    )
    created_at: datetime = Field(
        ...,
        description="UTC submission timestamp"
    )
    updated_at: datetime = Field(
        ...,
        description="UTC last update timestamp"
    )

    # Optional fields populated at different stages
    pii_findings: Optional[List[PIIFinding]] = Field(
        default=None,
        description="PII entities detected (if any)"
    )
    approval_token: Optional[str] = Field(
        default=None,
        description="Approval workflow token"
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Approval expiration timestamp"
    )
    markdown_url: Optional[str] = Field(
        default=None,
        description="S3 URL for Markdown output"
    )
    confidence_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="AI confidence score"
    )
    error_message: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Error details if failed"
    )
    approval_decision: Optional[ApprovalRequest] = Field(
        default=None,
        description="Approval decision details"
    )

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transition to new status is valid.

        Args:
            new_status: Target status to transition to

        Returns:
            True if transition is allowed by state machine
        """
        valid_next = VALID_TRANSITIONS.get(self.status, [])
        return new_status in valid_next

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "completed",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:35:00Z",
                "pii_findings": None,
                "approval_token": None,
                "expires_at": None,
                "markdown_url": "https://equalify-output.s3.amazonaws.com/...",
                "confidence_score": 0.87,
                "error_message": None,
                "approval_decision": None
            }
        }
    )