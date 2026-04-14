"""Shared data models for Equalify Reflow.

This package provides type-safe Pydantic models for all data structures
used across microservices including job tracking, queue payloads, PII
detection, approval workflows, processing results, and remediation pipeline.

4-Phase Architecture Models (PRD-021):
- ProcessingResult (new): Top-level API output with glass box trace
- AgentTrace/AgentResult: Per-agent execution traces
- AutoCorrection: Safe automatic edits (replaces Proposal)
- ReviewChecklist/ReviewItem: Human review interface
- DocumentSummary: Context for downstream agents
"""

from .agent_trace import AgentResult, AgentTrace
from .approval import ApprovalDecision, ApprovalRequest
from .auto_correction import AutoCorrection
from .debug_bundle import DebugArtifact, DebugBundleManifest, DebugPhaseSummary
from .document_context import DocumentSummary, ObservationContext
from .job import VALID_TRANSITIONS, JobStatus, JobSubmission
from .observation import Observation, ObservationLocation
from .pii import PIIFinding, PIIResult
from .processing import LLMUsage, ProcessingJob
from .processing import ProcessingResult as LegacyProcessingResult
from .processing_result import (
    AnalysisSummary,
    ExtractionSummary,
    ProcessingResult,
    ProcessingTrace,
    StructureSummary,
)
from .queue import ApprovalQueuePayload, PIIQueuePayload, ProcessingQueuePayload
from .redis_schema import (
    APPROVAL_QUEUE,
    APPROVAL_TIMEOUTS,
    DAILY_METRICS,
    JOB_STATUS_PREFIX,
    PII_QUEUE,
    PROCESSING_QUEUE,
    job_status_key,
    metrics_key,
    queue_key,
    timeout_key,
)
from .remediation import DocumentManifest, HeadingNode, HeadingTree, PageFeatures
from .remediation_progress import (
    VALID_SUBSTATUSES,
    RemediationProgress,
    SubstatusType,
)
from .review_checklist import ReviewChecklist, ReviewItem, ReviewOption

# Rebuild models with forward references now that all types are imported
# This resolves string-quoted type annotations like "DocumentSummary"
DocumentManifest.model_rebuild()
ObservationContext.model_rebuild()
ReviewChecklist.model_rebuild()

__all__ = [
    # Job models
    "JobStatus",
    "JobSubmission",
    "VALID_TRANSITIONS",
    # PII models
    "PIIFinding",
    "PIIResult",
    # Approval models
    "ApprovalRequest",
    "ApprovalDecision",
    # Legacy processing models (keeping for backward compatibility)
    "LegacyProcessingResult",
    "ProcessingJob",
    "LLMUsage",
    # Queue models
    "PIIQueuePayload",
    "ApprovalQueuePayload",
    "ProcessingQueuePayload",
    # Remediation models
    "PageFeatures",
    "DocumentManifest",
    "HeadingNode",
    "HeadingTree",
    "ObservationLocation",
    "Observation",
    "RemediationProgress",
    "VALID_SUBSTATUSES",
    "SubstatusType",
    # NEW: 4-phase architecture models (PRD-021)
    # Processing result (replaces LegacyProcessingResult for new pipeline)
    "ProcessingResult",
    "ProcessingTrace",
    "AnalysisSummary",
    "ExtractionSummary",
    "StructureSummary",
    # Agent traces (glass box transparency)
    "AgentTrace",
    "AgentResult",
    # Auto-corrections (replaces Proposal)
    "AutoCorrection",
    # Review checklist (human review interface)
    "ReviewChecklist",
    "ReviewItem",
    "ReviewOption",
    # Document context (for downstream agents)
    "DocumentSummary",
    "ObservationContext",
    # Redis schema utilities
    "job_status_key",
    "queue_key",
    "timeout_key",
    "metrics_key",
    "PII_QUEUE",
    "APPROVAL_QUEUE",
    "PROCESSING_QUEUE",
    "APPROVAL_TIMEOUTS",
    "DAILY_METRICS",
    "JOB_STATUS_PREFIX",
    # Debug bundle models
    "DebugArtifact",
    "DebugBundleManifest",
    "DebugPhaseSummary",
]
