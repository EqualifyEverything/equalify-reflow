"""API response schemas for structured responses.

This module defines Pydantic models for API responses. Each job status has
its own response model with only the relevant fields - no nulls, no clutter.
"""

from typing import Literal

from pydantic import BaseModel, Field


# Shared components
class PIIFinding(BaseModel):
    """PII detection result."""

    entity_type: str = Field(
        ..., description="Type of PII entity (e.g., EMAIL_ADDRESS, PHONE_NUMBER)"
    )
    text: str = Field(..., description="The detected PII text")
    score: float = Field(..., description="Confidence score (0.0 to 1.0)")


class LLMCostInfo(BaseModel):
    """Aggregate LLM cost information for a job.

    Costs accumulate across all processing phases (structure analysis + transcription).
    """

    input_tokens: int = Field(0, description="Total input tokens consumed")
    output_tokens: int = Field(0, description="Total output tokens generated")
    total_tokens: int = Field(0, description="Total tokens (input + output)")
    estimated_cost_cents: float = Field(..., description="Total estimated LLM cost in cents")
    estimated_cost_dollars: float = Field(..., description="Total estimated LLM cost in dollars")


# Status-specific response models
class JobStatusBase(BaseModel):
    """Common fields for all job status responses."""

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Current job status")
    filename: str | None = Field(None, description="Original filename")
    created_at: str = Field(..., description="ISO timestamp when job was created")
    updated_at: str = Field(..., description="ISO timestamp of last update")


class PIIScanningResponse(JobStatusBase):
    """Response when job is scanning for PII."""

    status: Literal["pii_scanning"] = "pii_scanning"
    estimated_completion_minutes: int = Field(
        ..., description="Estimated minutes until completion"
    )


class ProcessingResponse(JobStatusBase):
    """Response when job is processing (AI text correction)."""

    status: Literal["processing"] = "processing"
    estimated_completion_minutes: int = Field(
        ..., description="Estimated minutes until completion"
    )
    pii_skipped: bool | None = Field(
        None, description="Whether PII scan was skipped (true if bypassed)"
    )


class AwaitingPIIApprovalResponse(JobStatusBase):
    """Response when job needs PII approval."""

    status: Literal["awaiting_approval"] = "awaiting_approval"
    pii_findings: list[PIIFinding] = Field(..., description="Detected PII entities")
    approval_token: str = Field(..., description="Token for approval/rejection")
    approval_expires_at: str = Field(..., description="When approval token expires")
    approval_url: str = Field(..., description="URL to submit approval decision")


class CorrectionSummary(BaseModel):
    """Summary of AI corrections made."""

    total_corrections: int = Field(..., description="Total corrections made")
    confidence_score: float = Field(..., description="Overall confidence (0.0-1.0)")
    corrections_by_type: dict[str, int] = Field(
        ..., description="Count by correction type"
    )


class AwaitingCorrectionApprovalResponse(JobStatusBase):
    """Response when job needs correction approval."""

    status: Literal["awaiting_correction_approval"] = "awaiting_correction_approval"
    correction_summary: CorrectionSummary = Field(
        ..., description="Summary of corrections"
    )
    approval_token: str = Field(..., description="Token for approval/rejection")
    approval_expires_at: str = Field(..., description="When approval token expires")
    review_url: str = Field(..., description="URL to review corrections in detail")
    original_markdown_url: str = Field(..., description="URL to original markdown")
    corrected_markdown_url: str = Field(..., description="URL to corrected markdown")
    page_image_urls: list[str] = Field(..., description="URLs to page preview images")
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")


class CorrectionDecision(BaseModel):
    """Record of correction approval/rejection.

    Decision types:
    - approved: Human reviewed and approved corrections
    - rejected: Human reviewed and rejected corrections (original used)
    - auto_completed: No correction review was needed (direct processing)
    """

    decision: Literal["approved", "rejected", "auto_completed"] = Field(
        ..., description="The decision: approved/rejected (human review) or auto_completed (no review needed)"
    )
    reviewed_by: str = Field("", description="Email of reviewer (empty for auto_completed)")
    reviewed_at: str = Field("", description="When the decision was made (empty for auto_completed)")
    justification: str = Field("", description="Reason for decision (empty for auto_completed)")


class CompletedResponse(JobStatusBase):
    """Response when job is completed."""

    status: Literal["completed"] = "completed"
    markdown_url: str = Field(..., description="URL to final markdown")
    confidence_score: float = Field(..., description="Overall confidence score")
    correction_decision: CorrectionDecision = Field(
        ..., description="How corrections were handled"
    )
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")


class FailedResponse(JobStatusBase):
    """Response when job has failed."""

    status: Literal["failed"] = "failed"
    error: str = Field(..., description="Error message")


class DeniedResponse(JobStatusBase):
    """Response when job was denied (PII not approved)."""

    status: Literal["denied"] = "denied"
    reason: str = Field(..., description="Reason for denial")


# Union type for OpenAPI documentation
DocumentStatusResponse = (
    PIIScanningResponse
    | ProcessingResponse
    | AwaitingPIIApprovalResponse
    | AwaitingCorrectionApprovalResponse
    | CompletedResponse
    | FailedResponse
    | DeniedResponse
)
