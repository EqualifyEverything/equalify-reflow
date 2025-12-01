"""Job status constants and state machine definitions.

Defines all valid job statuses and state transition rules
for the document conversion workflow.
"""

from typing import Literal

# Job status type definition
# Status flow:
# 1. pii_scanning - Scanning document for PII
# 2. awaiting_approval - PII found, awaiting human approval to proceed
# 3. processing - Converting PDF and applying AI text corrections
# 4. awaiting_correction_approval - AI corrections complete, awaiting human review
# 5. completed/failed/denied - Terminal states
JobStatusType = Literal[
    "pii_scanning",
    "awaiting_approval",
    "processing",
    "awaiting_correction_approval",
    "completed",
    "failed",
    "denied"
]

# Status constants
STATUS_PII_SCANNING = "pii_scanning"
STATUS_AWAITING_APPROVAL = "awaiting_approval"  # PII approval
STATUS_PROCESSING = "processing"
STATUS_AWAITING_CORRECTION_APPROVAL = "awaiting_correction_approval"  # Text correction approval
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DENIED = "denied"

# Status groupings
TERMINAL_STATUSES: set[str] = {
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DENIED
}

ACTIVE_STATUSES: set[str] = {
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
    STATUS_AWAITING_CORRECTION_APPROVAL
}

# All valid statuses
ALL_STATUSES: set[str] = TERMINAL_STATUSES | ACTIVE_STATUSES
