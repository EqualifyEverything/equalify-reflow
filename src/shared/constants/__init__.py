"""Shared constants for Equalify PDF Converter.

This package provides application-wide constants including queue names,
status enums, and Redis key patterns.
"""

from .queues import (
    PII_QUEUE_NAME,
    APPROVAL_QUEUE_NAME,
    PROCESSING_QUEUE_NAME,
    APPROVAL_TIMEOUT_KEY,
    DAILY_METRICS_KEY
)
from .statuses import (
    JobStatusType,
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DENIED,
    TERMINAL_STATUSES,
    ACTIVE_STATUSES
)
from .redis_keys import (
    REDIS_PREFIX,
    job_key,
    queue_name,
    timeout_key,
    metrics_key
)

__all__ = [
    # Queue constants
    "PII_QUEUE_NAME",
    "APPROVAL_QUEUE_NAME",
    "PROCESSING_QUEUE_NAME",
    "APPROVAL_TIMEOUT_KEY",
    "DAILY_METRICS_KEY",

    # Status constants
    "JobStatusType",
    "STATUS_PII_SCANNING",
    "STATUS_AWAITING_APPROVAL",
    "STATUS_PROCESSING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_DENIED",
    "TERMINAL_STATUSES",
    "ACTIVE_STATUSES",

    # Redis key utilities
    "REDIS_PREFIX",
    "job_key",
    "queue_name",
    "timeout_key",
    "metrics_key"
]