"""Queue name constants for Redis operations.

Defines all queue and key names used for job processing workflow.
All keys use the eq-pdf namespace prefix.
"""

# Redis namespace prefix
REDIS_PREFIX = "eq-pdf"

# Processing queues (Redis Lists)
PII_QUEUE_NAME = f"{REDIS_PREFIX}:queue:pii"
APPROVAL_QUEUE_NAME = f"{REDIS_PREFIX}:queue:approval"
PROCESSING_QUEUE_NAME = f"{REDIS_PREFIX}:queue:processing"

# Timeout tracking (Redis Sorted Sets)
APPROVAL_TIMEOUT_KEY = f"{REDIS_PREFIX}:timeouts:approval"

# Metrics tracking (Redis Hashes)
DAILY_METRICS_KEY = f"{REDIS_PREFIX}:metrics:daily"

# Job status key prefix (Redis Hashes)
JOB_STATUS_KEY_PREFIX = f"{REDIS_PREFIX}:job:"