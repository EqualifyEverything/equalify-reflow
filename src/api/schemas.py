"""API response schemas for structured responses.

This module defines Pydantic models for API responses. Each job status has
its own response model with only the relevant fields - no nulls, no clutter.
"""

from typing import Literal

from pydantic import BaseModel, Field


# Shared components
class PIIFinding(BaseModel):
    """PII detection result."""

    entity_type: str = Field(..., description="Type of PII entity (e.g., EMAIL_ADDRESS, PHONE_NUMBER)")
    text: str = Field(..., description="The detected PII text")
    score: float = Field(..., description="Confidence score (0.0 to 1.0)")


class LLMCallInfo(BaseModel):
    """Individual LLM call information for detailed cost tracking."""

    agent: str = Field(..., description="Agent that made the call (planner, worker, paragraph_agent, etc.)")
    purpose: str = Field(..., description="Purpose of the call (page_1_alt_text, document_structure, etc.)")
    page: int | None = Field(None, description="Page number if applicable")
    input_tokens: int = Field(..., description="Input tokens consumed")
    output_tokens: int = Field(..., description="Output tokens generated")
    cost_cents: float = Field(..., description="Estimated cost in cents")
    timestamp: str = Field(..., description="ISO timestamp of the call")
    duration_ms: int | None = Field(None, description="Duration in milliseconds")


class LLMCostInfo(BaseModel):
    """Aggregate LLM cost information for a job.

    Costs accumulate across all processing phases (structure analysis + transcription).
    """

    input_tokens: int = Field(0, description="Total input tokens consumed")
    output_tokens: int = Field(0, description="Total output tokens generated")
    total_tokens: int = Field(0, description="Total tokens (input + output)")
    estimated_cost_cents: float = Field(..., description="Total estimated LLM cost in cents")
    estimated_cost_dollars: float = Field(..., description="Total estimated LLM cost in dollars")
    calls: list[LLMCallInfo] = Field(default_factory=list, description="Breakdown of individual LLM calls")


# Status-specific response models
class JobStatusBase(BaseModel):
    """Common fields for all job status responses."""

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Current job status")
    filename: str | None = Field(None, description="Original filename")
    created_at: str = Field(..., description="ISO timestamp when job was created")
    updated_at: str = Field(..., description="ISO timestamp of last update")
    debug_bundle_requested: bool = Field(False, description="Whether debug bundle generation was requested")


class PIIScanningResponse(JobStatusBase):
    """Response when job is scanning for PII."""

    status: Literal["pii_scanning"] = "pii_scanning"
    estimated_completion_minutes: int = Field(..., description="Estimated minutes until completion")


class ProcessingResponse(JobStatusBase):
    """Response when job is processing (AI text correction)."""

    status: Literal["processing"] = "processing"
    estimated_completion_minutes: int = Field(..., description="Estimated minutes until completion")
    pii_skipped: bool | None = Field(None, description="Whether PII scan was skipped (true if bypassed)")


class AwaitingPIIApprovalResponse(JobStatusBase):
    """Response when job needs PII approval."""

    status: Literal["awaiting_approval"] = "awaiting_approval"
    pii_findings: list[PIIFinding] = Field(..., description="Detected PII entities")
    approval_token: str = Field(..., description="Token for approval/rejection")
    approval_expires_at: str = Field(..., description="When approval token expires")
    approval_url: str = Field(..., description="URL to submit approval decision")


class CompletedResponse(JobStatusBase):
    """Response when job is completed."""

    status: Literal["completed"] = "completed"
    markdown_url: str = Field(..., description="URL to final markdown")
    confidence_score: float = Field(..., description="Overall confidence score")
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")
    warnings: list[str] = Field(default_factory=list, description="Classification warnings about the document")


class FailedResponse(JobStatusBase):
    """Response when job has failed."""

    status: Literal["failed"] = "failed"
    error: str = Field(..., description="Error message")
    warnings: list[str] = Field(default_factory=list, description="Classification warnings about the document")


class DeniedResponse(JobStatusBase):
    """Response when job was denied (PII not approved)."""

    status: Literal["denied"] = "denied"
    reason: str = Field(..., description="Reason for denial")


# Agentic Pipeline Response Models
class AgenticProcessingResponse(JobStatusBase):
    """Response when job is processing via agentic pipeline."""

    status: Literal["processing"] = "processing"
    review_mode: Literal["auto", "human"] = Field(..., description="Review mode: auto or human")
    processing_phase: str = Field(..., description="Current phase (docling, planning, executing, verifying)")
    jobs_total: int = Field(default=0, description="Total jobs in plan")
    jobs_complete: int = Field(default=0, description="Jobs completed")
    stream_url: str = Field(..., description="URL for SSE event stream")
    pii_skipped: bool | None = Field(None, description="Whether PII scan was skipped")


class FigureAsset(BaseModel):
    """Figure image stored with the document."""

    figure_id: str = Field(..., description="Figure identifier (e.g., 'figure-1')")
    url: str = Field(..., description="Presigned S3 URL to image")
    page: int = Field(..., ge=1, description="Source page number")
    alt_text: str = Field(default="", description="Generated alt text")
    caption: str = Field(default="", description="Caption from PDF if available")


class AgenticCompletedResponse(JobStatusBase):
    """Response when agentic pipeline job is completed."""

    status: Literal["completed"] = "completed"
    review_mode: Literal["auto", "human"] = Field(..., description="Review mode used")
    markdown_url: str = Field(..., description="URL to final markdown")
    confidence_score: float = Field(..., description="Overall confidence score")
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")
    ledger_url: str | None = Field(None, description="URL to change ledger (human mode only)")
    total_pages: int = Field(default=0, description="Total pages processed")
    total_edits: int = Field(default=0, description="Total edits made")
    figures: list[FigureAsset] = Field(
        default_factory=list,
        description="Extracted figure images with presigned URLs"
    )
    bundle_url: str | None = Field(
        None,
        description="URL to download ZIP bundle with markdown and images"
    )
    warnings: list[str] = Field(default_factory=list, description="Classification warnings about the document")


class LedgerEntryResponse(BaseModel):
    """Single entry in the change ledger."""

    entry_id: str = Field(..., description="Unique entry identifier")
    page: int = Field(..., ge=1, description="Page number")
    action: str = Field(..., description="Action type (add, modify, delete)")
    target: str = Field(..., description="Target element identifier")
    before: str = Field(..., description="Content before change")
    after: str = Field(..., description="Content after change")
    reasoning: str = Field(..., description="LLM reasoning for the change")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    timestamp: str = Field(..., description="ISO timestamp of the change")
    needs_review: bool = Field(default=False, description="True if edit needs human review (low confidence)")


class LedgerPageGroup(BaseModel):
    """Group of ledger entries for a single page."""

    page: int = Field(..., ge=1, description="Page number")
    entries: list[LedgerEntryResponse] = Field(..., description="Entries for this page")
    edit_count: int = Field(..., description="Number of edits on this page")


class LedgerResponse(BaseModel):
    """Complete change ledger for PR-like review."""

    job_id: str = Field(..., description="Job identifier")
    document_title: str = Field(default="", description="Original document title")
    total_pages: int = Field(..., description="Total pages in document")
    pages_with_changes: int = Field(..., description="Pages that have changes")
    total_edits: int = Field(..., description="Total edits across all pages")
    entries_needing_review: int = Field(default=0, description="Count of entries requiring human review")
    pages: list[LedgerPageGroup] = Field(..., description="Ledger entries grouped by page")
    processing_duration_ms: int = Field(default=0, description="Processing duration in milliseconds")
    final_markdown_url: str = Field(..., description="URL to final markdown")


# Union type for OpenAPI documentation
# Includes both legacy and agentic pipeline response types
DocumentStatusResponse = (
    PIIScanningResponse
    | ProcessingResponse
    | AgenticProcessingResponse
    | AwaitingPIIApprovalResponse
    | CompletedResponse
    | AgenticCompletedResponse
    | FailedResponse
    | DeniedResponse
)
