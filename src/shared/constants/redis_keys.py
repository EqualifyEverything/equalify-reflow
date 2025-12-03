"""Redis key generation utilities.

Provides consistent key generation functions for all Redis operations.
Ensures proper namespacing and key patterns across services.
"""

# Redis namespace prefix
REDIS_PREFIX = "eq-pdf"


def job_key(job_id: str) -> str:
    """Generate Redis key for job status hash.

    Args:
        job_id: UUID format job identifier

    Returns:
        Redis key string: eq-pdf:job:{job_id}

    Example:
        >>> job_key("550e8400-e29b-41d4-a716-446655440000")
        'eq-pdf:job:550e8400-e29b-41d4-a716-446655440000'
    """
    return f"{REDIS_PREFIX}:job:{job_id}"


def queue_name(queue_type: str) -> str:
    """Generate Redis key for processing queue list.

    Args:
        queue_type: Type of queue (pii, approval, processing)

    Returns:
        Redis key string: eq-pdf:queue:{queue_type}

    Example:
        >>> queue_name("pii")
        'eq-pdf:queue:pii'
    """
    return f"{REDIS_PREFIX}:queue:{queue_type}"


def timeout_key(timeout_type: str) -> str:
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


def metrics_key(metric_type: str) -> str:
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
