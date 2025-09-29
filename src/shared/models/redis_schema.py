"""Redis key patterns and schema utilities.

Provides consistent key naming functions for all Redis operations
across microservices. Ensures proper namespacing with eq-pdf prefix.
"""

from typing import Literal


# Redis key prefixes
REDIS_PREFIX = "eq-pdf"

# Queue type literals
QueueType = Literal["pii", "approval", "processing"]
TimeoutType = Literal["approval"]
MetricType = Literal["daily"]


def job_status_key(job_id: str) -> str:
    """Generate Redis key for job status hash.

    Args:
        job_id: UUID format job identifier

    Returns:
        Redis key string: eq-pdf:job:{job_id}

    Example:
        >>> job_status_key("550e8400-e29b-41d4-a716-446655440000")
        'eq-pdf:job:550e8400-e29b-41d4-a716-446655440000'
    """
    return f"{REDIS_PREFIX}:job:{job_id}"


def queue_key(queue_type: QueueType) -> str:
    """Generate Redis key for processing queue list.

    Args:
        queue_type: Type of queue (pii, approval, processing)

    Returns:
        Redis key string: eq-pdf:queue:{queue_type}

    Example:
        >>> queue_key("pii")
        'eq-pdf:queue:pii'
    """
    return f"{REDIS_PREFIX}:queue:{queue_type}"


def timeout_key(timeout_type: TimeoutType) -> str:
    """Generate Redis key for timeout sorted set.

    Args:
        timeout_type: Type of timeout tracking (approval)

    Returns:
        Redis key string: eq-pdf:timeouts:{timeout_type}

    Example:
        >>> timeout_key("approval")
        'eq-pdf:timeouts:approval'
    """
    return f"{REDIS_PREFIX}:timeouts:{timeout_type}"


def metrics_key(metric_type: MetricType) -> str:
    """Generate Redis key for metrics hash.

    Args:
        metric_type: Type of metrics (daily)

    Returns:
        Redis key string: eq-pdf:metrics:{metric_type}

    Example:
        >>> metrics_key("daily")
        'eq-pdf:metrics:daily'
    """
    return f"{REDIS_PREFIX}:metrics:{metric_type}"


# Constant queue keys for direct import
PII_QUEUE = queue_key("pii")
APPROVAL_QUEUE = queue_key("approval")
PROCESSING_QUEUE = queue_key("processing")

# Constant timeout keys
APPROVAL_TIMEOUTS = timeout_key("approval")

# Constant metric keys
DAILY_METRICS = metrics_key("daily")

# Job status key prefix for pattern matching
JOB_STATUS_PREFIX = f"{REDIS_PREFIX}:job:"