"""PII detection models for Presidio integration."""

from typing import List
from pydantic import BaseModel, Field


class PIIFinding(BaseModel):
    """Represents a single PII entity detected in document text.

    Used by Microsoft Presidio analyzer to flag sensitive information
    that requires manual review before processing.

    Attributes:
        entity_type: Type of PII detected (e.g., "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER")
        start: Character position where entity starts in text
        end: Character position where entity ends in text
        score: Confidence score from 0.0 to 1.0
        text: The actual detected text snippet

    Example:
        >>> finding = PIIFinding(
        ...     entity_type="EMAIL_ADDRESS",
        ...     start=45,
        ...     end=63,
        ...     score=0.95,
        ...     text="student@example.com"
        ... )
    """
    entity_type: str = Field(
        ...,
        description="PII entity type from Presidio",
        min_length=1
    )
    start: int = Field(
        ...,
        ge=0,
        description="Start character position"
    )
    end: int = Field(
        ...,
        ge=0,
        description="End character position"
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Detection confidence score"
    )
    text: str = Field(
        ...,
        min_length=1,
        description="Detected text snippet"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "entity_type": "PERSON",
                "start": 120,
                "end": 132,
                "score": 0.85,
                "text": "John Student"
            }
        }


class PIIResult(BaseModel):
    """Complete PII scan results for a document.

    Aggregates all PII findings from a single document scan,
    including metadata about the scan operation itself.

    Attributes:
        job_id: Unique job identifier (UUID)
        findings: List of detected PII entities
        scan_completed_at: UTC timestamp when scan finished
        total_findings: Count of PII entities found
    """
    job_id: str = Field(
        ...,
        pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        description="UUID format job identifier"
    )
    findings: List[PIIFinding] = Field(
        default_factory=list,
        description="List of PII entities detected"
    )
    total_findings: int = Field(
        ...,
        ge=0,
        description="Total count of findings"
    )

    class Config:
        """Pydantic model configuration."""
        json_schema_extra = {
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "findings": [],
                "total_findings": 0
            }
        }