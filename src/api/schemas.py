"""API response schemas for structured responses.

This module defines Pydantic models for API responses, ensuring consistent
response shapes and type safety across the API.
"""


from pydantic import BaseModel, Field


class PIIFinding(BaseModel):
    """PII detection result."""

    entity_type: str = Field(
        ..., description="Type of PII entity (e.g., EMAIL_ADDRESS, PHONE_NUMBER)"
    )
    text: str = Field(..., description="The detected PII text")
    score: float = Field(..., description="Confidence score (0.0 to 1.0)")


class PageLLMUsage(BaseModel):
    """LLM token usage and cost for a single page."""

    page: int = Field(..., description="Page number (1-indexed)")
    input_tokens: int = Field(..., description="Number of input tokens consumed")
    output_tokens: int = Field(..., description="Number of output tokens generated")
    cost_cents: float = Field(..., description="Cost in cents for this page")


class LLMCostInfo(BaseModel):
    """Aggregate LLM cost information for a job.

    Includes total cost and per-page breakdown for transparency.
    """

    total_cost_cents: float = Field(
        ..., description="Total LLM cost in cents for all pages"
    )
    total_cost_dollars: float = Field(
        ..., description="Total LLM cost in dollars for convenience"
    )
    page_costs: list[PageLLMUsage] = Field(
        default_factory=list, description="Per-page token usage and costs"
    )


class CorrectionApprovalInfo(BaseModel):
    """Correction approval metadata for frontend.

    Provided when job status is awaiting_correction_approval.
    """

    token: str = Field(..., description="Correction approval token for API calls")
    expires_at: str = Field(
        ..., description="ISO timestamp when approval token expires"
    )
    total_corrections: int = Field(..., description="Total number of corrections made")
    confidence_score: float = Field(
        ..., description="Overall confidence score (0.0 to 1.0)"
    )
    corrections_by_type: dict[str, int] = Field(
        ..., description="Count of corrections by type"
    )
    review_url: str = Field(
        ..., description="API endpoint to review corrections in detail"
    )


class CorrectionDecisionInfo(BaseModel):
    """Correction decision metadata after approval/rejection.

    Provided when job status is completed and corrections were reviewed.
    """

    decision: str = Field(..., description="Decision: 'approved' or 'rejected'")
    reviewed_by: str = Field(..., description="Email of person who reviewed")
    reviewed_at: str = Field(..., description="ISO timestamp of review")
    justification: str = Field(..., description="Reason for decision")


class DocumentURLs(BaseModel):
    """Generated URLs for document assets.

    URLs are generated on-demand from S3 keys stored in Redis.
    URL format varies by environment (LocalStack vs AWS).
    """

    original_markdown: str | None = Field(
        None, description="URL to original markdown (before corrections)"
    )
    corrected_markdown: str | None = Field(
        None, description="URL to corrected markdown (awaiting approval)"
    )
    markdown: str | None = Field(
        None, description="URL to final markdown (after completion)"
    )
    page_images: list[str] | None = Field(
        None, description="URLs to page images (for visual comparison)"
    )


class DocumentStatusResponse(BaseModel):
    """Response for GET /api/documents/{job_id}.

    Response shape varies by job status:
    - pii_scanning/processing: Basic status + estimated time
    - awaiting_approval: PII findings + approval token
    - awaiting_correction_approval: Correction approval info + URLs
    - completed: Final markdown URL + correction decision
    """

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Current job status")
    created_at: str = Field(..., description="ISO timestamp when job was created")
    updated_at: str = Field(..., description="ISO timestamp of last update")

    # PII approval (when status = awaiting_approval)
    pii_findings: list[PIIFinding] | None = Field(
        None, description="PII entities detected in document"
    )
    approval_token: str | None = Field(
        None, description="Token to approve/reject PII findings"
    )
    approval_expires_at: str | None = Field(
        None, description="ISO timestamp when approval token expires"
    )

    # Correction approval (when status = awaiting_correction_approval)
    correction_approval: CorrectionApprovalInfo | None = Field(
        None,
        description="Correction approval metadata (only when awaiting correction approval)",
    )

    # Correction decision (when status = completed)
    correction_decision: CorrectionDecisionInfo | None = Field(
        None, description="Correction decision metadata (only when completed)"
    )
    confidence_score: float | None = Field(
        None, description="Overall confidence score (0.0 to 1.0)"
    )

    # Generated URLs (varies by status)
    urls: DocumentURLs | None = Field(
        None, description="Generated URLs to document assets"
    )

    # Estimates (when status = pii_scanning or processing)
    estimated_completion_minutes: int | None = Field(
        None, description="Estimated minutes until completion"
    )

    # LLM cost tracking (when text correction has run)
    llm_cost: LLMCostInfo | None = Field(
        None, description="LLM token usage and cost breakdown"
    )
