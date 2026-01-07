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
from .model_tiers import MODEL_TIER_MAP, ModelTier

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
from .models import (
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    EditProposal,
    FigureContext,
    HeadingFix,
    IssueCategory,
    Job,
    JobContext,
    JobType,
    Ledger,
    LedgerEntry,
    OutlineEntry,
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
    ValidationResult,
    VerificationReport,
    # Optimized pipeline models
    DocumentContext,
    ExtractedHeading,
    HeadingInference,
    PageSummary,
    StructureInferenceResult,
)
from .orchestrator import (
    process_document_v5,
    process_document_v5_streaming,
    run_recovery_phase,
)
from .planner import plan_document
from .page_chain import (
    PageChainState,
    run_page_chain,
)
from .recovery import (
    attempt_page_recovery,
    calculate_pass_threshold,
    categorize_issues,
    determine_final_status,
    should_attempt_recovery,
)
from .plan_verification import (
    verify_against_plan,
    verify_figure_completeness,
    verify_heading_structure,
    verify_spelling,
    verify_table_completeness,
)
from .validation import auto_fix_minor_issues, validate_edit
from .worker import execute_job, execute_jobs_parallel

# Optimized pipeline (two-phase architecture)
from .context_gatherer import (
    extract_all_headings,
    gather_document_context,
    infer_document_structure,
    summarize_all_pages,
)
from .issue_detector import (
    detect_all_issues,
    detect_page_issues,
    get_critical_issues,
    get_fixable_issues,
    summarize_issues,
)
from .orchestrator_optimized import process_document_v5_optimized

__all__ = [
    # Model tiers
    "MODEL_TIER_MAP",
    "ModelTier",
    # Main entry points
    "process_document_v5",
    "process_document_v5_streaming",
    "process_document_v5_optimized",  # Optimized two-phase pipeline
    "plan_document",
    "execute_job",
    "execute_jobs_parallel",
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
    "verify_spelling",
    # Recovery functions
    "run_recovery_phase",
    "attempt_page_recovery",
    "calculate_pass_threshold",
    "categorize_issues",
    "determine_final_status",
    "should_attempt_recovery",
    # Models
    "DocumentPlan",
    "DocumentStructure",
    "DocumentType",
    "EditProposal",
    "FigureContext",
    "HeadingFix",
    "IssueCategory",
    "Job",
    "JobContext",
    "JobType",
    "Ledger",
    "LedgerEntry",
    "OutlineEntry",
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
    "ValidationResult",
    "VerificationReport",
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
    # Optimized pipeline
    "gather_document_context",
    "extract_all_headings",
    "infer_document_structure",
    "summarize_all_pages",
    "detect_all_issues",
    "detect_page_issues",
    "get_critical_issues",
    "get_fixable_issues",
    "summarize_issues",
    # Optimized pipeline models
    "DocumentContext",
    "ExtractedHeading",
    "HeadingInference",
    "PageSummary",
    "StructureInferenceResult",
]
