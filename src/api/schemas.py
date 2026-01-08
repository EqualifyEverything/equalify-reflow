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


class CorrectionSummary(BaseModel):
    """Summary of AI corrections made."""

    total_corrections: int = Field(..., description="Total corrections made")
    auto_applied_count: int = Field(..., description="Corrections auto-applied by AI")
    manual_review_count: int = Field(..., description="Corrections requiring manual review")
    confidence_score: float = Field(..., description="Overall confidence (0.0-1.0)")
    corrections_by_type: dict[str, int] = Field(..., description="Count by correction type")


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
    correction_summary: CorrectionSummary = Field(..., description="Summary of corrections")
    corrections: list[CorrectionItem] = Field(..., description="All correction details")
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
    correction_decision: CorrectionDecision = Field(..., description="How corrections were handled")
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")


class FailedResponse(JobStatusBase):
    """Response when job has failed."""

    status: Literal["failed"] = "failed"
    error: str = Field(..., description="Error message")


class DeniedResponse(JobStatusBase):
    """Response when job was denied (PII not approved)."""

    status: Literal["denied"] = "denied"
    reason: str = Field(..., description="Reason for denial")


class NeedsReviewResponse(JobStatusBase):
    """Response when job needs human review of AI suggestions (PRD-027).

    This status indicates processing is complete but there are review items
    that require human decision before the job can be finalized.
    """

    status: Literal["needs_review"] = "needs_review"
    confidence_score: float = Field(..., description="Overall confidence score")
    review_item_count: int = Field(..., description="Number of items requiring review")
    processing_result_key: str = Field(..., description="S3 key to full ProcessingResult JSON")
    review_url: str = Field(..., description="URL to access review checklist API")
    page_image_urls: list[str] = Field(default_factory=list, description="URLs to page preview images")
    llm_cost: LLMCostInfo = Field(..., description="LLM usage and cost")


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
    | AwaitingCorrectionApprovalResponse
    | CompletedResponse
    | AgenticCompletedResponse
    | FailedResponse
    | DeniedResponse
    | NeedsReviewResponse
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


class VerificationCorrectionSummary(BaseModel):
    """Summary of a single verification correction."""

    search_preview: str = Field(..., description="First 100 chars of search text")
    replace_preview: str = Field(..., description="First 100 chars of replace text")
    issue_type: str = Field(..., description="ocr_error, double_word, missing_text, etc.")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str


class VerificationPageResult(BaseModel):
    """Result of verifying a single page."""

    page_num: int
    is_accurate: bool
    corrections_applied: int = 0
    corrections_failed: int = 0
    issues_count: int = 0
    summary: str


class VerificationPhase(BaseModel):
    """Phase 5: Final verification against source images."""

    status: str = Field(..., description="completed, skipped, or error")
    total_pages: int = Field(default=0, description="Number of pages verified")
    corrections_applied: int = Field(default=0, description="Total corrections successfully applied")
    corrections_failed: int = Field(default=0, description="Corrections that couldn't be applied")
    issues_found: int = Field(default=0, description="Issues flagged for review")
    all_pages_accurate: bool = Field(default=True, description="Whether all pages passed verification")
    page_results: list[VerificationPageResult] = Field(default_factory=list)
    cost_cents: float = Field(default=0.0, description="LLM cost for verification")
    time_seconds: float | None = Field(default=None, description="Verification duration")


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
    verification: VerificationPhase | None = Field(default=None, description="Phase 5: Final verification")

    total_llm_cost: LLMCostInfo | None = None


# =============================================================================
# V2 Pipeline Response Models
# =============================================================================


class Transformation(BaseModel):
    """Single LLM transformation for human review.

    Tracks what the AI changed and why, enabling humans to:
    - Review specific changes before approval
    - Provide feedback on AI decisions
    - Approve/reject individual transformations
    """

    element_id: str = Field(..., description="Unique ID e.g. 'heading-3', 'figure-p5-1'")
    element_type: str = Field(..., description="heading, figure, table, formula, code, list")
    transformation_type: str = Field(
        ..., description="heading_level_adjusted, alt_text_generated, table_semantics_added, etc."
    )
    page: int = Field(..., ge=1, description="Page number (1-indexed)")

    # Before/after for review
    original: dict = Field(..., description="Original values, e.g. {'level': 1, 'text': 'Abstract'}")
    transformed: dict = Field(..., description="New values, e.g. {'level': 2, 'text': 'Abstract'}")

    # LLM reasoning
    reason: str = Field(..., description="LLM's explanation for the change")
    confidence: float = Field(..., ge=0.0, le=1.0, description="LLM confidence in the change")

    # Review status (for future human-in-the-loop workflow)
    status: str = Field(default="pending", description="pending, approved, rejected")


class ImageAsset(BaseModel):
    """Extracted image asset with S3 URL.

    Images are cropped from page images using bounding boxes,
    uploaded to S3, and referenced by URL in the markdown output.
    """

    asset_id: str = Field(..., description="Unique ID e.g. 'figure-p5-1', 'table-p8-2'")
    s3_url: str = Field(..., description="Public S3 URL to the image")
    page: int = Field(..., ge=1, description="Source page number (1-indexed)")
    element_type: str = Field(..., description="figure or table")
    alt_text: str | None = Field(default=None, description="Generated alt text for figures")
    caption: str | None = Field(default=None, description="Caption text if available")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Bounding box (x0, y0, x1, y1) in page coordinates"
    )


class FootnoteLink(BaseModel):
    """Linked footnote reference.

    Connects footnote markers in the text to their definitions,
    enabling proper footnote rendering and navigation.
    """

    marker: str = Field(..., description="Footnote marker e.g. '1', '2', '*', '†'")
    reference_text: str = Field(..., description="Text containing the marker (truncated)")
    reference_page: int = Field(..., ge=1, description="Page where marker appears")
    definition_page: int = Field(..., ge=1, description="Page where definition is")
    definition_text: str = Field(..., description="The footnote definition text")


class PipelineV2Response(BaseModel):
    """Enhanced pipeline response with structured transformations and assets.

    This response model supports human-in-the-loop review by exposing:
    - All LLM transformations with before/after for review
    - Image assets with S3 URLs for proper rendering
    - Linked footnotes for document navigation
    """

    success: bool = Field(..., description="True if processing completed without failures")
    markdown: str = Field(default="", description="Final markdown with S3 image URLs")
    markdown_clean: str = Field(default="", description="Markdown without provenance comments")

    # Stats
    total_elements: int = Field(default=0, description="Total document elements processed")
    figures_processed: int = Field(default=0, description="Figures with generated alt text")
    tables_processed: int = Field(default=0, description="Tables with semantic analysis")
    headings_validated: int = Field(default=0, description="Headings with validated levels")
    formulas_transcribed: int = Field(default=0, description="Formulas transcribed to LaTeX")
    code_blocks_transcribed: int = Field(default=0, description="Code blocks transcribed")
    llm_calls: int = Field(default=0, description="Total LLM API calls made")
    llm_failures: int = Field(default=0, description="Failed LLM calls (used fallbacks)")

    # Analysis results
    document_type: str = Field(default="", description="Detected document type")
    pages_with_code: list[int] = Field(default_factory=list, description="Pages with code blocks")
    pages_with_equations: list[int] = Field(default_factory=list, description="Pages with equations")

    # NEW: Structured transformations for human review
    transformations: list[Transformation] = Field(
        default_factory=list, description="All LLM transformations with before/after"
    )

    # NEW: Image assets with S3 URLs
    assets: dict[str, ImageAsset] = Field(
        default_factory=dict, description="Mapping of asset_id to ImageAsset"
    )

    # NEW: Linked footnotes
    footnotes: list[FootnoteLink] = Field(
        default_factory=list, description="Footnote markers linked to definitions"
    )

    # Citation/footnote style from analysis
    citation_style: str = Field(default="unknown", description="Detected citation style (APA, IEEE, etc.)")
    footnote_style: str = Field(default="none", description="Detected footnote style")

    # Errors
    errors: list[str] = Field(default_factory=list, description="Non-fatal errors during processing")
    error: str | None = Field(default=None, description="Fatal error message if success=False")
