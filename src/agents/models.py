"""V5 Pipeline Data Models.

This module defines all the data structures for the hierarchical
document processing pipeline with streaming ledger support.

Architecture Overview:
    - DocumentPlan: Output from Planner phase (structure + jobs)
    - Job: A scoped unit of work for a Worker agent
    - LedgerEntry: Immutable record of a change
    - ValidationResult: Feedback from validation gate
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# =============================================================================
# Enums
# =============================================================================


class DocumentType(str, Enum):
    """Types of documents we can process."""

    PAPER = "paper"
    SYLLABUS = "syllabus"
    SLIDES = "slides"
    MANUAL = "manual"
    REPORT = "report"
    ARTICLE = "article"
    OTHER = "other"


class TaskType(str, Enum):
    """Types of tasks that workers can perform."""

    ALT_TEXT = "alt_text"
    TABLE_TRANSCRIPTION = "table_transcription"
    HEADING_FIX = "heading_fix"
    OCR_FIX = "ocr_fix"
    FORMAT_FIX = "format_fix"
    SPELLING_FIX = "spelling_fix"
    PAGELESS_OPTIMIZATION = "pageless_optimization"  # Final pass: page break removal, paragraph merging


class JobType(str, Enum):
    """Job categories for prioritization."""

    STRUCTURE = "structure"  # Heading fixes - run first
    CONTENT = "content"  # Alt-text, tables - run after structure


class PageType(str, Enum):
    """Types of pages detected in quick scan."""

    TITLE = "title"
    TOC = "toc"
    CONTENT = "content"
    REFERENCES = "references"
    APPENDIX = "appendix"
    BLANK = "blank"


# =============================================================================
# Stage 1: Quick Scan Models
# =============================================================================


class PageSkeleton(BaseModel):
    """Cheap extraction from a single page (no LLM required).

    Extracted via regex/heuristics from the initial markdown.
    Used to build the document structure before detailed analysis.
    """

    page_num: int = Field(..., description="1-indexed page number")

    # Extracted via regex from markdown
    headings: list[str] = Field(
        default_factory=list,
        description="Raw heading text found on this page",
    )
    heading_levels: list[int] = Field(
        default_factory=list,
        description="# count for each heading (1-6)",
    )

    # Counted from placeholders
    figure_count: int = Field(default=0, description="Number of figures on page")
    table_count: int = Field(default=0, description="Number of tables on page")

    # Heuristics
    word_count: int = Field(default=0, description="Approximate word count")
    has_citations: bool = Field(
        default=False,
        description="Whether [1], [2] citation patterns found",
    )
    page_type: PageType = Field(
        default=PageType.CONTENT,
        description="Detected page type",
    )


# =============================================================================
# Stage 2: Structure Inference Models
# =============================================================================


class OutlineEntry(BaseModel):
    """A node in the document outline (TOC structure)."""

    heading: str = Field(..., description="The heading text")
    level: int = Field(..., ge=1, le=6, description="Heading level (1=H1, 2=H2, etc.)")
    page_start: int = Field(..., description="First page of this section")
    page_end: int = Field(..., description="Last page of this section")
    children: list[OutlineEntry] = Field(
        default_factory=list,
        description="Child sections",
    )

    class Config:
        """Allow self-referential model."""

        arbitrary_types_allowed = True


class HeadingFix(BaseModel):
    """A heading that needs level adjustment."""

    page: int = Field(..., description="Page number")
    line: int = Field(default=0, description="Line number in markdown (0 if unknown)")
    current_text: str = Field(..., description="Current heading text")
    current_level: int = Field(..., description="Current heading level")
    should_be_level: int = Field(..., description="Correct heading level")
    reason: str = Field(..., description="Why this fix is needed")


class DocumentStructure(BaseModel):
    """LLM-inferred document structure from Stage 2.

    This is the single source of truth for document organization.
    Workers follow this structure, they don't question it.
    """

    title: str = Field(..., description="Document title")
    document_type: DocumentType = Field(
        default=DocumentType.OTHER,
        description="Type of document",
    )

    outline: list[OutlineEntry] = Field(
        default_factory=list,
        description="Hierarchical table of contents",
    )

    # Heading corrections needed
    heading_fixes: list[HeadingFix] = Field(
        default_factory=list,
        description="Headings that need level adjustment",
    )

    # Dictionary for spell-checking
    key_terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific terms (e.g., 'Docling', 'TableFormer')",
    )
    acronyms: dict[str, str] = Field(
        default_factory=dict,
        description="Acronym expansions (e.g., {'LLM': 'Large Language Model'})",
    )


# =============================================================================
# Stage 3: Page Summary Models
# =============================================================================


class FigureContext(BaseModel):
    """Context for a figure that needs alt-text."""

    figure_index: int = Field(..., description="1-indexed figure number on page")
    location: str = Field(
        default="",
        description="Where in document (e.g., 'after heading 2.1')",
    )
    appears_to_be: str = Field(
        default="figure",
        description="What the figure looks like (e.g., 'architecture diagram')",
    )
    surrounding_text: str = Field(
        default="",
        description="Caption or nearby text for context",
    )
    is_decorative: bool = Field(
        default=False,
        description="Whether this appears to be decorative (no alt needed)",
    )


class TableContext(BaseModel):
    """Context for a table that needs transcription."""

    table_index: int = Field(..., description="1-indexed table number on page")
    location: str = Field(default="", description="Where in document")
    appears_to_be: str = Field(
        default="data table",
        description="Table type (e.g., 'comparison table', 'data table')",
    )
    # Aliases for compatibility with page chain output
    appears_to_contain: str = Field(
        default="",
        description="What data the table contains",
    )
    caption: str | None = Field(default=None, description="Table caption if visible")
    estimated_rows: int = Field(default=0, description="Approximate row count")
    estimated_cols: int = Field(default=0, description="Approximate column count")
    row_count: int = Field(default=0, description="Row count (alias for estimated_rows)")
    has_header: bool = Field(default=True, description="Whether table has header row")


class PagePlan(BaseModel):
    """Detailed analysis of a single page from Stage 3."""

    page_num: int = Field(..., description="1-indexed page number")

    # From Stage 3 LLM call
    summary: str = Field(..., description="1-2 sentence page summary")
    keywords: list[str] = Field(
        default_factory=list,
        description="Page-specific terms to add to dictionary",
    )
    section_context: str = Field(
        ...,
        description="Where this page fits (e.g., 'Part of 2.1 Architecture')",
    )

    # Work items detected
    figures: list[FigureContext] = Field(
        default_factory=list,
        description="Figures needing alt-text",
    )
    tables: list[TableContext] = Field(
        default_factory=list,
        description="Tables needing transcription",
    )

    # Issues detected
    ocr_errors: list[str] = Field(
        default_factory=list,
        description="Suspected typos/OCR errors",
    )
    formatting_issues: list[str] = Field(
        default_factory=list,
        description="Markdown formatting problems",
    )


# =============================================================================
# Stage 4: Job Generation Models
# =============================================================================


class Task(BaseModel):
    """A single unit of work within a job."""

    task_id: str = Field(
        default_factory=lambda: str(uuid4())[:8],
        description="Unique task identifier",
    )
    task_type: TaskType = Field(..., description="Type of task")
    target: str = Field(..., description="Target identifier (e.g., 'fig:1', 'table:2')")
    context: str = Field(..., description="Relevant context for this task")
    priority: int = Field(default=1, ge=1, le=3, description="Priority (1=highest)")


class JobContext(BaseModel):
    """Slim context slice passed to a Worker agent.

    Contains only what the worker needs - keeps token usage bounded.
    """

    document_title: str = Field(..., description="Document title")
    document_type: DocumentType = Field(..., description="Document type")
    section_context: str = Field(
        ...,
        description="Where this page fits in document",
    )
    dictionary: list[str] = Field(
        default_factory=list,
        description="Terms for spell-checking",
    )

    # Only the outline entries relevant to this page
    relevant_outline: list[OutlineEntry] = Field(
        default_factory=list,
        description="Outline entries for this section",
    )


class Job(BaseModel):
    """A scoped unit of work for a Worker agent.

    Jobs are independent and can run in parallel.
    Each job handles one page's worth of work.
    """

    job_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique job identifier",
    )
    job_type: JobType = Field(..., description="Job category for prioritization")
    priority: int = Field(default=1, ge=1, description="Lower = higher priority")

    page: int = Field(..., description="Page number this job handles")
    tasks: list[Task] = Field(..., description="Tasks to complete")
    context: JobContext = Field(..., description="Context for the worker")

    # The markdown content the worker will edit
    page_markdown: str = Field(..., description="Current markdown for this page")

    # Status tracking
    status: Literal["pending", "running", "completed", "failed"] = Field(default="pending")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


# =============================================================================
# Document Plan (Complete Planner Output)
# =============================================================================


class DocumentPlan(BaseModel):
    """Complete output from the Planner phase.

    This is the single source of truth that workers follow.
    Contains structure, page plans, and generated jobs.
    """

    document_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique document identifier",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Source info
    filename: str = Field(..., description="Original filename")
    total_pages: int = Field(..., description="Total page count")

    # From Stage 2: Structure
    structure: DocumentStructure = Field(..., description="Inferred document structure")

    # From Stage 3: Page details
    pages: dict[int, PagePlan] = Field(
        default_factory=dict,
        description="Page plans indexed by page number",
    )

    # From Stage 4: Jobs
    jobs: list[Job] = Field(default_factory=list, description="Generated jobs")

    # Aggregated dictionary (structure terms + page terms)
    full_dictionary: list[str] = Field(
        default_factory=list,
        description="Complete dictionary for spell-checking",
    )

    # Planning phase stats
    planning_duration_ms: int = Field(default=0)
    planning_tokens_input: int = Field(default=0)
    planning_tokens_output: int = Field(default=0)


# =============================================================================
# Ledger Models
# =============================================================================


class LedgerEntry(BaseModel):
    """Immutable record of a change made by a worker.

    The ledger provides a complete audit trail of all modifications.
    Entries are append-only and streamed to the UI in real-time.
    """

    entry_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique entry identifier",
    )
    job_id: str = Field(..., description="Job that made this change")
    page: int = Field(..., description="Page number affected")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # What changed
    action: TaskType = Field(..., description="Type of change")
    target: str = Field(..., description="Target identifier (e.g., 'fig:1')")

    before: str = Field(..., description="Original text/placeholder")
    after: str = Field(..., description="New content")

    reasoning: str = Field(..., description="Why this change was made")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    # Validation status
    validated: bool = Field(default=False, description="Passed validation gate")
    validation_feedback: str | None = Field(
        default=None,
        description="Feedback if validation failed",
    )


class Ledger(BaseModel):
    """Append-only log of all document changes.

    Provides complete audit trail and supports real-time streaming.
    """

    document_id: str = Field(..., description="Document being processed")
    entries: list[LedgerEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def append(self, entry: LedgerEntry) -> None:
        """Add an entry to the ledger."""
        self.entries.append(entry)

    def get_page_entries(self, page: int) -> list[LedgerEntry]:
        """Get all entries for a specific page."""
        return [e for e in self.entries if e.page == page]

    def get_job_entries(self, job_id: str) -> list[LedgerEntry]:
        """Get all entries for a specific job."""
        return [e for e in self.entries if e.job_id == job_id]

    @property
    def total_edits(self) -> int:
        """Count of validated edits."""
        return sum(1 for e in self.entries if e.validated)


# =============================================================================
# Validation Models
# =============================================================================


class EditProposal(BaseModel):
    """A proposed edit from a Worker agent (not yet applied)."""

    target: str = Field(..., description="Target identifier")
    task_type: TaskType = Field(..., description="Type of edit")
    before: str = Field(..., description="Original text to replace")
    after: str = Field(..., description="New text")
    reasoning: str = Field(..., description="Why this edit is needed")


class SpellIssue(BaseModel):
    """A spelling issue found during validation."""

    word: str = Field(..., description="The flagged word")
    suggestion: str = Field(default="", description="Suggested correction")
    in_dictionary: bool = Field(
        default=False,
        description="Whether word is in document dictionary",
    )


class ValidationResult(BaseModel):
    """Result of validating a proposed edit."""

    approved: bool = Field(..., description="Whether edit passed validation")
    edit: EditProposal = Field(..., description="The edit that was validated")

    # If not approved, why?
    spell_issues: list[SpellIssue] = Field(default_factory=list)
    lint_issues: list[str] = Field(default_factory=list)
    consistency_issues: list[str] = Field(default_factory=list)

    # Message to return to agent for revision
    feedback: str | None = Field(
        default=None,
        description="Feedback for agent if validation failed",
    )


# =============================================================================
# Verification Models
# =============================================================================


class PageVerification(BaseModel):
    """Verification result for a single page."""

    page_num: int = Field(..., description="Page number")
    passed: bool = Field(..., description="Whether page passed verification")
    issues: list[str] = Field(
        default_factory=list,
        description="Issues found during verification",
    )
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class VerificationReport(BaseModel):
    """Final verification report for the complete document."""

    document_id: str = Field(..., description="Document ID")
    passed: bool = Field(..., description="Whether document passed overall")

    pages: list[PageVerification] = Field(
        default_factory=list,
        description="Per-page verification results",
    )

    # Aggregated issues for human review
    total_issues: int = Field(default=0)
    critical_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    # Stats
    pages_passed: int = Field(default=0)
    pages_failed: int = Field(default=0)
    verification_duration_ms: int = Field(default=0)


# =============================================================================
# Recovery Phase Models
# =============================================================================


class RecoveryAttemptStatus(str, Enum):
    """Status of a recovery attempt on a single page."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ACCEPTED_WITH_CAVEATS = "accepted_with_caveats"


class IssueCategory(str, Enum):
    """Categories of issues for recovery prioritization."""

    UNFILLED_PLACEHOLDER = "unfilled_placeholder"
    MISSING_ALT_TEXT = "missing_alt_text"
    HEADING_HIERARCHY = "heading_hierarchy"
    FORMATTING = "formatting"
    COSMETIC = "cosmetic"


class RecoveryAction(str, Enum):
    """Actions the recovery agent can take."""

    CLEANUP_EDIT = "cleanup_edit"
    REMOVE_PLACEHOLDER = "remove_placeholder"
    ACCEPT_WITH_CAVEAT = "accept_with_caveat"
    ESCALATE = "escalate"


class ProcessingStatus(str, Enum):
    """Final processing status after recovery attempts."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


class RecoveryEdit(BaseModel):
    """A single recovery edit made to fix an issue."""

    edit_id: str = Field(
        default_factory=lambda: str(uuid4())[:8],
        description="Unique edit identifier",
    )
    page: int = Field(..., description="Page number")
    action: RecoveryAction = Field(..., description="Type of recovery action")
    target: str = Field(..., description="Target identifier (e.g., 'fig:1', 'heading')")
    before: str = Field(..., description="Original text")
    after: str = Field(..., description="Corrected text")
    reasoning: str = Field(..., description="Why this recovery edit was made")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class RecoveryAttempt(BaseModel):
    """Record of a recovery attempt on a single page."""

    attempt_id: str = Field(
        default_factory=lambda: str(uuid4())[:8],
        description="Unique attempt identifier",
    )
    page_num: int = Field(..., description="Page number being recovered")
    attempt_number: int = Field(..., ge=1, le=2, description="Attempt number (1 or 2)")
    status: RecoveryAttemptStatus = Field(
        default=RecoveryAttemptStatus.PENDING,
        description="Status of this attempt",
    )
    original_issues: list[str] = Field(
        default_factory=list,
        description="Issues that triggered recovery",
    )
    edits_proposed: int = Field(default=0, description="Number of edits proposed")
    edits_applied: int = Field(default=0, description="Number of edits successfully applied")
    caveats: list[str] = Field(
        default_factory=list,
        description="Caveats for accepted-with-caveats status",
    )
    duration_ms: int = Field(default=0, description="Duration of recovery attempt")


class RecoveryReport(BaseModel):
    """Complete report of the recovery phase."""

    document_id: str = Field(..., description="Document being recovered")
    recovery_attempted: bool = Field(
        default=False,
        description="Whether recovery was attempted",
    )
    pages_recovered: list[int] = Field(
        default_factory=list,
        description="Pages successfully recovered",
    )
    pages_accepted_with_caveats: list[int] = Field(
        default_factory=list,
        description="Pages accepted with caveats",
    )
    pages_unrecoverable: list[int] = Field(
        default_factory=list,
        description="Pages that could not be recovered",
    )
    attempts: list[RecoveryAttempt] = Field(
        default_factory=list,
        description="All recovery attempts made",
    )
    total_recovery_edits: int = Field(
        default=0,
        description="Total edits applied during recovery",
    )
    recovery_duration_ms: int = Field(
        default=0,
        description="Total recovery phase duration",
    )


# =============================================================================
# Processing Result
# =============================================================================


class ProcessingResult(BaseModel):
    """Final result of V5 pipeline processing."""

    document_id: str = Field(..., description="Document ID")
    success: bool = Field(..., description="Whether processing succeeded")

    # Outputs
    final_markdown: str = Field(..., description="Complete corrected markdown")
    ledger: Ledger = Field(..., description="Complete change ledger")
    verification: VerificationReport = Field(..., description="Verification report")

    # Confidence
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Document confidence score aggregated from verification",
    )

    # Stats
    total_pages: int = Field(default=0)
    total_edits: int = Field(default=0)
    total_jobs: int = Field(default=0)

    # Cost tracking
    total_input_tokens: int = Field(default=0)
    total_output_tokens: int = Field(default=0)
    total_cost: float = Field(default=0.0)

    # Timing
    total_duration_ms: int = Field(default=0)
    planning_duration_ms: int = Field(default=0)
    execution_duration_ms: int = Field(default=0)
    verification_duration_ms: int = Field(default=0)

    # Recovery phase (optional)
    recovery_report: RecoveryReport | None = Field(
        default=None,
        description="Recovery phase report if recovery was attempted",
    )
