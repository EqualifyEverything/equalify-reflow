"""AI agents for document processing.

This module provides the agentic pipeline for document processing.

The pipeline uses:
- Orchestrator: Main processing coordinator
- Planner: Structure inference and job generation
- Worker: Page-by-page issue detection and fixing
- Recovery: Error recovery and retry logic
- Events: Event bus for SSE streaming

See individual modules in src/agents/ for implementation details.
"""

# Re-export model tiers for convenience
# Import all pipeline components
from .events import (
    EditCommittedEvent,
    EditProposedEvent,
    EventBus,
    JobCompletedEvent,
    JobCreatedEvent,
    JobStartedEvent,
    PageScannedEvent,
    PageSummarizedEvent,
    PageVerifiedEvent,
    PlanningCompleteEvent,
    PlanningStartedEvent,
    ProcessingCompleteEvent,
    RecoveryCompleteEvent,
    RecoveryEditAppliedEvent,
    RecoveryPhaseCompleteEvent,
    RecoveryPhaseStartedEvent,
    RecoveryStartedEvent,
    StreamEvent,
    StructureInferredEvent,
    VerificationCompleteEvent,
    VerificationStartedEvent,
)
from .model_tiers import MODEL_TIER_MAP, ModelTier
from .models import (
    # Issue detection models
    CitationIssue,
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    EditProposal,
    FigureContext,
    FootnoteIssue,
    HeadingFix,
    IssueCategory,
    Job,
    JobContext,
    JobType,
    Ledger,
    LedgerEntry,
    ListIssue,
    OutlineEntry,
    PageArtifactIssue,
    PagePlan,
    PageSkeleton,
    PageType,
    PageVerification,
    ProcessingResult,
    ProcessingStatus,
    RecoveryAction,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryEdit,
    RecoveryReport,
    TableContext,
    Task,
    TaskType,
    TypographyIssue,
    ValidationResult,
    VerificationReport,
)
from .orchestrator import (
    merge_cross_page_paragraphs,
    run_agentic_pipeline,
    run_agentic_pipeline_streaming,
    run_recovery_phase,
)
from .page_chain import (
    PageChainState,
    run_page_chain,
)
from .paragraph_agent import execute_with_paragraph_agent
from .plan_verification import (
    verify_against_plan,
    verify_figure_completeness,
    verify_heading_structure,
    verify_spelling,
    verify_table_accuracy_vision,
    verify_table_completeness,
)
from .planner import plan_document
from .recovery import (
    attempt_page_recovery,
    calculate_pass_threshold,
    categorize_issues,
    determine_final_status,
    should_attempt_recovery,
)
from .subagents import (
    CITATION_SYSTEM_PROMPT,
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_SKIP,
    FOOTNOTE_SYSTEM_PROMPT,
    LIST_SEMANTICS_SYSTEM_PROMPT,
    PAGE_ARTIFACT_SYSTEM_PROMPT,
    PARAGRAPH_MERGE_SYSTEM_PROMPT,
    TYPOGRAPHY_SYSTEM_PROMPT,
    SubagentResult,
    invoke_citation_subagent,
    invoke_footnote_subagent,
    invoke_list_subagent,
    invoke_page_artifact_subagent,
    invoke_paragraph_merge_subagent,
    invoke_typography_subagent,
)
from .subagents.types import (
    CitationResult,
    FootnoteResult,
    ListResult,
    PageArtifactResult,
    ParagraphMergeResult,
    TypographyResult,
)
from .validation import auto_fix_minor_issues, validate_edit
from .worker import execute_job, execute_jobs_parallel

__all__ = [
    # Model tiers
    "MODEL_TIER_MAP",
    "ModelTier",
    # Main entry points
    "run_agentic_pipeline",
    "run_agentic_pipeline_streaming",
    "plan_document",
    "execute_job",
    "execute_jobs_parallel",
    "execute_with_paragraph_agent",
    "merge_cross_page_paragraphs",
    # Page chain
    "PageChainState",
    "run_page_chain",
    # Validation
    "validate_edit",
    "auto_fix_minor_issues",
    # Plan verification
    "verify_against_plan",
    "verify_heading_structure",
    "verify_figure_completeness",
    "verify_table_completeness",
    "verify_table_accuracy_vision",
    "verify_spelling",
    # Recovery functions
    "run_recovery_phase",
    "attempt_page_recovery",
    "calculate_pass_threshold",
    "categorize_issues",
    "determine_final_status",
    "should_attempt_recovery",
    # Models
    "CitationIssue",
    "DocumentPlan",
    "DocumentStructure",
    "DocumentType",
    "EditProposal",
    "FigureContext",
    "FootnoteIssue",
    "HeadingFix",
    "IssueCategory",
    "Job",
    "JobContext",
    "JobType",
    "Ledger",
    "LedgerEntry",
    "ListIssue",
    "OutlineEntry",
    "PageArtifactIssue",
    "PagePlan",
    "PageSkeleton",
    "PageType",
    "PageVerification",
    "ProcessingResult",
    "ProcessingStatus",
    "RecoveryAction",
    "RecoveryAttempt",
    "RecoveryAttemptStatus",
    "RecoveryEdit",
    "RecoveryReport",
    "TableContext",
    "Task",
    "TaskType",
    "TypographyIssue",
    "ValidationResult",
    "VerificationReport",
    # Subagent types and constants
    "CONFIDENCE_AUTO_APPLY",
    "CONFIDENCE_APPLY_WITH_REVIEW",
    "CONFIDENCE_SKIP",
    "SubagentResult",
    "CitationResult",
    "FootnoteResult",
    "ListResult",
    "PageArtifactResult",
    "ParagraphMergeResult",
    "TypographyResult",
    # Subagent invoke functions
    "invoke_citation_subagent",
    "invoke_footnote_subagent",
    "invoke_list_subagent",
    "invoke_page_artifact_subagent",
    "invoke_paragraph_merge_subagent",
    "invoke_typography_subagent",
    # Subagent system prompts
    "CITATION_SYSTEM_PROMPT",
    "FOOTNOTE_SYSTEM_PROMPT",
    "LIST_SEMANTICS_SYSTEM_PROMPT",
    "PAGE_ARTIFACT_SYSTEM_PROMPT",
    "PARAGRAPH_MERGE_SYSTEM_PROMPT",
    "TYPOGRAPHY_SYSTEM_PROMPT",
    # Events
    "EventBus",
    "StreamEvent",
    "PlanningStartedEvent",
    "PageScannedEvent",
    "StructureInferredEvent",
    "PageSummarizedEvent",
    "JobCreatedEvent",
    "PlanningCompleteEvent",
    "JobStartedEvent",
    "EditProposedEvent",
    "EditCommittedEvent",
    "JobCompletedEvent",
    "VerificationStartedEvent",
    "PageVerifiedEvent",
    "VerificationCompleteEvent",
    "ProcessingCompleteEvent",
    # Recovery events
    "RecoveryPhaseStartedEvent",
    "RecoveryStartedEvent",
    "RecoveryEditAppliedEvent",
    "RecoveryCompleteEvent",
    "RecoveryPhaseCompleteEvent",
]
