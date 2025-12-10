"""Shared data models for Equalify PDF Converter.

This package provides type-safe Pydantic models for all data structures
used across microservices including job tracking, queue payloads, PII
detection, approval workflows, processing results, and remediation pipeline.
"""

from .approval import ApprovalDecision, ApprovalRequest
from .job import VALID_TRANSITIONS, JobStatus, JobSubmission
from .observation import (
    VALID_OBSERVATION_TRANSITIONS,
    Observation,
    ObservationLocation,
)
from .pii import PIIFinding, PIIResult
from .processing import LLMUsage, ProcessingJob, ProcessingResult
from .proposal import VALID_PROPOSAL_TRANSITIONS, Proposal, SearchReplaceDiff
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
from .remediation import DocumentManifest, PageFeatures
from .remediation_progress import (
    VALID_SUBSTATUSES,
    RemediationProgress,
    SubstatusType,
)

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

    # Processing models
    "ProcessingResult",
    "ProcessingJob",
    "LLMUsage",

    # Queue models
    "PIIQueuePayload",
    "ApprovalQueuePayload",
    "ProcessingQueuePayload",

    # Remediation models
    "PageFeatures",
    "DocumentManifest",
    "ObservationLocation",
    "Observation",
    "VALID_OBSERVATION_TRANSITIONS",
    "SearchReplaceDiff",
    "Proposal",
    "VALID_PROPOSAL_TRANSITIONS",
    "RemediationProgress",
    "VALID_SUBSTATUSES",
    "SubstatusType",

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
    "JOB_STATUS_PREFIX"
]
