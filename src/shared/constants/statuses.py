"""Job status constants and state machine definitions.

Defines all valid job statuses and state transition rules
for the document conversion workflow.
"""

from typing import Literal, Set

# Job status type definition
JobStatusType = Literal[
    "pii_scanning",
    "awaiting_approval",
    "processing",
    "completed",
    "failed",
    "denied"
]

# Status constants
STATUS_PII_SCANNING = "pii_scanning"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DENIED = "denied"

# Status groupings
TERMINAL_STATUSES: Set[str] = {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DENIED
}

ACTIVE_STATUSES: Set[str] = {
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING
}

# All valid statuses
ALL_STATUSES: Set[str] = TERMINAL_STATUSES | ACTIVE_STATUSES