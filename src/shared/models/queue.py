"""Queue payload models for Redis queue communication."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from .pii import PIIFinding


class PIIQueuePayload(BaseModel):
    """Payload for PII scanning queue.

    Pushed to eq-pdf:queue:pii when new job is submitted.
    PII worker consumes this to scan PDF for sensitive data.

    Attributes:
        job_id: Unique job identifier (UUID)
        s3_key: S3 object key for PDF to scan
        created_at: UTC timestamp of job creation

    Example:
        >>> payload = PIIQueuePayload(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     s3_key="temp/550e8400.../document.pdf",
        ...     created_at=datetime.utcnow()
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
        description="UTC creation timestamp"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "s3_key": "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
                "created_at": "2024-01-15T10:30:00Z"
            }
        }


class ApprovalQueuePayload(BaseModel):
    """Payload for approval workflow queue.

    Pushed to eq-pdf:queue:approval when PII is detected.
    Approval worker sends notification and waits for decision.

    Attributes:
        job_id: Unique job identifier (UUID)
        s3_key: S3 object key for flagged PDF
        pii_findings: List of detected PII entities
        approval_token: Secure token for approval URL
        expires_at: UTC expiration timestamp for approval

    Example:
        >>> payload = ApprovalQueuePayload(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     s3_key="temp/550e8400.../document.pdf",
        ...     pii_findings=[...],
        ...     approval_token="abc123...",
        ...     expires_at=datetime.utcnow() + timedelta(days=1)
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
    pii_findings: List[PIIFinding] = Field(
        ...,
        min_length=1,
        description="Detected PII entities"
    )
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

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "s3_key": "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
                "pii_findings": [
                    {
                        "entity_type": "PERSON",
                        "start": 45,
                        "end": 57,
                        "score": 0.85,
                        "text": "John Student"
                    }
                ],
                "approval_token": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
                "expires_at": "2024-01-16T10:30:00Z"
            }
        }


class ProcessingQueuePayload(BaseModel):
    """Payload for document processing queue.

    Pushed to eq-pdf:queue:processing after PII scan passes or approval granted.
    Processing worker performs Docling conversion and AI enhancement.

    Attributes:
        job_id: Unique job identifier (UUID)
        s3_key: S3 object key for PDF to process
        approved_at: Optional UTC timestamp if approval was required

    Example:
        >>> payload = ProcessingQueuePayload(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     s3_key="temp/550e8400.../document.pdf",
        ...     approved_at=None  # No PII detected
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
    approved_at: Optional[datetime] = Field(
        default=None,
        description="UTC approval timestamp if required"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "s3_key": "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
                "approved_at": "2024-01-15T11:00:00Z"
            }
        }