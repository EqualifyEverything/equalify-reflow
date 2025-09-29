"""Shared data models for Equalify PDF Converter.

This package provides type-safe Pydantic models for all data structures
used across microservices including job tracking, queue payloads, PII
detection, approval workflows, and processing results.
"""

from .job import JobStatus, JobSubmission, VALID_TRANSITIONS
from .pii import PIIFinding, PIIResult
from .approval import ApprovalRequest, ApprovalDecision
from .processing import ProcessingResult, ProcessingJob
from .queue import PIIQueuePayload, ApprovalQueuePayload, ProcessingQueuePayload
from .redis_schema import (
    job_status_key,
    queue_key,
    timeout_key,
    metrics_key,
    PII_QUEUE,
    APPROVAL_QUEUE,
    PROCESSING_QUEUE,
    APPROVAL_TIMEOUTS,
    DAILY_METRICS,
    JOB_STATUS_PREFIX
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