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
    auto_applied_count: int = Field(..., description="Corrections auto-applied by AI")
    manual_review_count: int = Field(..., description="Corrections requiring manual review")
    confidence_score: float = Field(..., description="Overall confidence (0.0-1.0)")
    corrections_by_type: dict[str, int] = Field(
        ..., description="Count by correction type"
    )


class CorrectionItem(BaseModel):
    """Individual correction detail."""

    page: int = Field(..., ge=1, description="Page number (1-indexed)")
    type: str = Field(..., description="Correction type")
    original_snippet: str = Field(..., description="Original text (first 200 chars)")
    corrected_snippet: str = Field(..., description="Corrected text (first 200 chars)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="AI confidence score")
    explanation: str = Field(..., description="Explanation of the correction")
    is_auto_applied: bool = Field(..., description="Whether auto-applied by AI")


class AwaitingCorrectionApprovalResponse(JobStatusBase):
    """Response when job needs correction approval."""

    status: Literal["awaiting_correction_approval"] = "awaiting_correction_approval"
    correction_summary: CorrectionSummary = Field(
        ..., description="Summary of corrections"
    )
    corrections: list[CorrectionItem] = Field(
        ..., description="All correction details"
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


# Processing Phases Response
class PageFeatureSummary(BaseModel):
    """Summary of page features from analysis."""

    page_num: int
    has_images: bool
    image_count: int
    has_tables: bool
    table_count: int
    has_lists: bool
    complexity_score: float


class AnalysisPhase(BaseModel):
    """Phase 1: Document Analysis output."""

    status: str = Field(..., description="completed, skipped, or error")
    document_title: str | None = Field(default=None)
    document_type: str | None = Field(default=None)
    total_pages: int | None = Field(default=None)
    layout_type: str | None = Field(default=None)
    required_agents: list[str] = Field(default_factory=list)
    analysis_confidence: float | None = Field(default=None)
    page_features: list[PageFeatureSummary] = Field(default_factory=list)
    heading_tree: dict | None = Field(default=None, description="Document structure as heading tree")
    raw_manifest: dict | None = Field(default=None, description="Full manifest JSON when show_raw=true")


class ExtractionPhase(BaseModel):
    """Phase 2: Markdown Extraction output."""

    status: str = Field(..., description="completed, skipped, or error")
    markdown_url: str | None = Field(default=None, description="URL to v0 (original) markdown")
    confidence_score: float | None = Field(default=None)
    extraction_model: str | None = Field(default=None)


class ObservationSummary(BaseModel):
    """Summary of a single observation."""

    id: str
    agent: str
    severity: str
    confidence: float
    category: str
    status: str
    resolution: str | None = Field(default=None)
    visual_description: str | None = Field(default=None)
    markup_description: str | None = Field(default=None)
    page_num: int | None = Field(default=None)


class AgentsPhase(BaseModel):
    """Phase 3: Specialized Agents output."""

    status: str = Field(..., description="completed, skipped, or error")
    agents_run: list[str] = Field(default_factory=list)
    observation_count: int = Field(default=0)
    observations: list[ObservationSummary] = Field(default_factory=list)
    raw_observations: list[dict] | None = Field(default=None, description="Full observations when show_raw=true")


class AutoCorrectionSummary(BaseModel):
    """Summary of a single auto correction."""

    id: str
    observation_id: str
    applied: bool
    page_num: int | None
    search_preview: str = Field(..., description="First 100 chars of search text")
    replace_preview: str = Field(..., description="First 100 chars of replace text")
    justification: str
    confidence: float
    agent: str


class RemediationPhase(BaseModel):
    """Phase 4: Remediation output (auto corrections + review items)."""

    status: str = Field(..., description="completed, skipped, or error")
    auto_correction_count: int = Field(default=0)
    applied_count: int = Field(default=0)
    pending_count: int = Field(default=0)
    auto_corrections: list[AutoCorrectionSummary] = Field(default_factory=list)
    raw_corrections: list[dict] | None = Field(default=None, description="Full corrections when show_raw=true")


class ProcessingPhasesResponse(BaseModel):
    """Response containing all processing phase outputs."""

    job_id: str
    filename: str
    status: str
    created_at: str
    updated_at: str

    analysis: AnalysisPhase
    extraction: ExtractionPhase
    agents: AgentsPhase
    remediation: RemediationPhase

    total_llm_cost: LLMCostInfo | None = None
