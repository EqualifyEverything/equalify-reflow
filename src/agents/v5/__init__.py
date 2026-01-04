"""V5 Pipeline - Hierarchical Document Processing with Streaming Ledger.

This module implements a three-phase document processing pipeline:

Phase 1: Planning
    - Stage 1: Quick Scan (code) - Extract structure from markdown
    - Stage 2: Structure Inference (LLM) - Determine document type, outline
    - Stage 3: Page Summaries (LLM, batched) - Detailed page analysis
    - Stage 4: Job Generation (code) - Create worker jobs

Phase 2: Execution
    - Workers run in parallel with bounded concurrency
    - Each worker has tools: view_page, view_figure, propose_edit
    - Validation gate checks all edits before commit
    - Ledger tracks all changes with full provenance

Phase 3: Verification
    - Single pass quality check
    - Compares final output to document plan
    - Returns issues for human review

Key Features:
    - Planner establishes structure once (no agent fighting)
    - Workers get scoped context (bounded token usage)
    - Validation gate catches errors before commit
    - Streaming ledger for real-time UI
    - Parallel execution where possible

Usage:
    from src.agents.v5 import process_document_v5

    result, event_bus = await process_document_v5(
        filename="doc.pdf",
        page_markdowns={1: "# Title...", 2: "## Chapter 1..."},
        page_images={1: pil_image_1, 2: pil_image_2},
        element_bboxes={},
        page_width=612.0,
    )

    # Access results
    print(result.final_markdown)
    print(result.ledger.entries)
    print(result.verification.passed)

    # Stream events
    for event in event_bus.events:
        print(event.event_type, event.model_dump())
"""

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
from .models import (
    DocumentContext,
    ExtractedHeading,
    HeadingInference,
    PageSummary,
    StructureInferenceResult,
)

__all__ = [
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
    # Plan verification (V3.1-V3.4)
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
