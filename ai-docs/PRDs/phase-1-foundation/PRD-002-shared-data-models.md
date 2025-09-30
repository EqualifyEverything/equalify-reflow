# PRD-002: Shared Data Models

## Overview
**Epic**: MVP PDF Converter Data Architecture
**Phase**: 1 - Foundation
**Estimated Effort**: 1 day
**Dependencies**: None
**Parallel**: ✅ Can start immediately

## Problem Statement
The monolith application requires standardized data models for queue payloads, job status tracking, and internal communication between modules. These models must be type-safe, version-compatible, and shared across all application modules to ensure data consistency.

## Success Criteria
- [ ] Pydantic models for all data structures defined
- [ ] Redis data schema documented and implemented
- [ ] Queue payload formats standardized
- [ ] Job status state machine clearly defined
- [ ] Type validation working across all models
- [ ] Models organized in src/shared/ for reuse across modules

## Technical Requirements

### Core Data Models

#### Job Processing Models
```python
class JobSubmission(BaseModel):
    job_id: str
    s3_key: str
    created_at: datetime
    file_size_bytes: int
    original_filename: str

class PIIFinding(BaseModel):
    entity_type: str      # "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"
    start: int           # character position
    end: int             # character position
    score: float         # 0.0-1.0 confidence
    text: str            # actual detected text

class ApprovalRequest(BaseModel):
    job_id: str
    decision: Literal["approved", "denied"]
    justification: str
    reviewed_by: str
    reviewed_at: datetime

class ProcessingResult(BaseModel):
    job_id: str
    html_url: Optional[str]
    mdx_url: Optional[str]
    confidence_score: Optional[float]
    processing_time_seconds: int
    error_message: Optional[str]
```
#### Job Status Models
```python
class JobStatus(BaseModel):
    job_id: str
    status: Literal[
        "pii_scanning",
        "awaiting_approval",
        "processing",
        "completed",
        "failed",
        "denied"
    ]
    created_at: datetime
    updated_at: datetime

    # Optional fields (only when relevant)
    pii_findings: Optional[List[PIIFinding]] = None
    approval_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    html_url: Optional[str] = None
    mdx_url: Optional[str] = None
    confidence_score: Optional[float] = None
    error_message: Optional[str] = None
    approval_decision: Optional[ApprovalRequest] = None
```

### Redis Data Schema

#### Queue Structures
```python
# Queue Names (Redis Lists)
PII_QUEUE = "eq-pdf:queue:pii"
APPROVAL_QUEUE = "eq-pdf:queue:approval"
PROCESSING_QUEUE = "eq-pdf:queue:processing"

# Timeout Management (Redis Sorted Set)
APPROVAL_TIMEOUTS = "eq-pdf:timeouts:approval"

# Job Status (Redis Hashes)
JOB_STATUS_PREFIX = "eq-pdf:job:"

# Metrics (Redis Hashes)
DAILY_METRICS = "eq-pdf:metrics:daily"
```

#### Queue Payload Models
```python
class PIIQueuePayload(BaseModel):
    job_id: str
    s3_key: str
    created_at: datetime

class ApprovalQueuePayload(BaseModel):
    job_id: str
    s3_key: str
    pii_findings: List[PIIFinding]
    approval_token: str
    expires_at: datetime

class ProcessingQueuePayload(BaseModel):
    job_id: str
    s3_key: str
    approved_at: Optional[datetime] = None
```

## Acceptance Criteria

### 1. Model Validation
- [ ] All models validate required fields
- [ ] Optional fields properly typed with defaults
- [ ] Enum values strictly enforced
- [ ] DateTime fields use UTC timezone
- [ ] Model serialization/deserialization works correctly

### 2. Redis Integration
- [ ] Models serialize to/from Redis JSON
- [ ] Queue operations preserve type information
- [ ] Job status updates maintain data integrity
- [ ] Timeout operations work with datetime fields

### 3. Type Safety
- [ ] MyPy type checking passes
- [ ] IDE autocomplete works for all fields
- [ ] Invalid data raises clear validation errors
- [ ] Model inheritance works correctly

### 4. Documentation
- [ ] All models have docstrings
- [ ] Field descriptions explain business logic
- [ ] Examples provided for complex models
- [ ] State transitions documented

## Deliverables

### Files to Create
```
/src/shared/models/
├── __init__.py                    # Package exports
├── job.py                        # Job-related models
├── queue.py                      # Queue payload models
├── pii.py                        # PII detection models
├── approval.py                   # Approval workflow models
├── processing.py                 # Processing result models
└── redis_schema.py               # Redis key patterns

/src/shared/constants/
├── __init__.py
├── queues.py                     # Queue names and keys
├── statuses.py                   # Job status constants
└── redis_keys.py                 # Redis key patterns

/tests/shared/
├── test_job_models.py
├── test_queue_models.py
└── test_redis_integration.py
```

### Model Package Structure
```python
# src/shared/models/__init__.py
from .job import JobStatus, JobSubmission
from .pii import PIIFinding, PIIResult
from .approval import ApprovalRequest, ApprovalDecision
from .processing import ProcessingResult, ProcessingJob
from .queue import PIIQueuePayload, ApprovalQueuePayload, ProcessingQueuePayload

__all__ = [
    "JobStatus", "JobSubmission",
    "PIIFinding", "PIIResult",
    "ApprovalRequest", "ApprovalDecision",
    "ProcessingResult", "ProcessingJob",
    "PIIQueuePayload", "ApprovalQueuePayload", "ProcessingQueuePayload"
]
```

## Technical Notes

### State Machine
```python
# Valid status transitions
VALID_TRANSITIONS = {
    "pii_scanning": ["awaiting_approval", "processing", "failed"],
    "awaiting_approval": ["processing", "denied", "failed"],
    "processing": ["completed", "failed"],
    "completed": [],  # Terminal state
    "failed": [],     # Terminal state
    "denied": []      # Terminal state
}
```

### Redis Key Patterns
```python
# Consistent key naming
def job_status_key(job_id: str) -> str:
    return f"eq-pdf:job:{job_id}"

def queue_key(queue_type: str) -> str:
    return f"eq-pdf:queue:{queue_type}"

def timeout_key(timeout_type: str) -> str:
    return f"eq-pdf:timeouts:{timeout_type}"
```

### Validation Rules
```python
# Business logic validation
class JobSubmission(BaseModel):
    job_id: str = Field(..., regex=r'^[0-9a-f-]{36}$')  # UUID format
    file_size_bytes: int = Field(..., gt=0, le=100_000_000)  # Max 100MB
    s3_key: str = Field(..., min_length=1)

    @validator('s3_key')
    def validate_s3_key(cls, v):
        if not v.startswith('temp/'):
            raise ValueError('Temporary files must be in temp/ prefix')
        return v
```

## Definition of Done
- [ ] All data models defined with proper types
- [ ] Redis integration functions working
- [ ] Model validation tests passing
- [ ] Documentation generated and reviewed
- [ ] Models can be imported by all application modules
- [ ] No circular dependencies between models
- [ ] Models ready for Phase 2 module implementation