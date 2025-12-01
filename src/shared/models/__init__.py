"""Shared data models for Equalify PDF Converter.

This package provides type-safe Pydantic models for all data structures
used across microservices including job tracking, queue payloads, PII
detection, approval workflows, and processing results.
"""

from .approval import ApprovalDecision, ApprovalRequest
from .job import VALID_TRANSITIONS, JobStatus, JobSubmission
from .pii import PIIFinding, PIIResult
from .processing import PageCorrectionResult, ProcessingJob, ProcessingResult, TextCorrection
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
    "TextCorrection",
    "PageCorrectionResult",

    # Queue models
    "PIIQueuePayload",
    "ApprovalQueuePayload",
    "ProcessingQueuePayload",

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
