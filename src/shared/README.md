# Shared Data Models for Equalify PDF Converter

This package provides type-safe Pydantic models and constants for all microservices in the Equalify PDF Converter system.

## Installation

The package is part of the main project and is installed when you install the project dependencies:

```bash
uv pip install -e .
```

## Package Structure

```
shared/
├── models/              # Pydantic data models
│   ├── job.py          # Job tracking models (JobStatus, JobSubmission)
│   ├── pii.py          # PII detection models (PIIFinding, PIIResult)
│   ├── approval.py     # Approval workflow models
│   ├── processing.py   # Processing result models
│   ├── queue.py        # Queue payload models
│   └── redis_schema.py # Redis key generation functions
└── constants/           # Application constants
    ├── queues.py       # Queue names and keys
    ├── statuses.py     # Job status constants
    └── redis_keys.py   # Redis key utilities
```

## Usage Examples

### Job Submission

```python
from datetime import datetime
from shared.models import JobSubmission, JobStatus

# Create a new job submission
submission = JobSubmission(
    job_id="550e8400-e29b-41d4-a716-446655440000",
    s3_key="temp/550e8400-e29b-41d4-a716-446655440000/syllabus.pdf",
    created_at=datetime.utcnow(),
    file_size_bytes=2456789,
    original_filename="CS101_Syllabus_Fall2024.pdf"
)

# Initialize job status
status = JobStatus(
    job_id=submission.job_id,
    status="pii_scanning",
    created_at=submission.created_at,
    updated_at=submission.created_at
)

# Serialize for Redis storage
json_data = status.model_dump_json()
```

### PII Detection

```python
from shared.models import PIIFinding, PIIResult

# Create PII findings
findings = [
    PIIFinding(
        entity_type="PERSON",
        start=120,
        end=132,
        score=0.85,
        text="John Student"
    ),
    PIIFinding(
        entity_type="EMAIL_ADDRESS",
        start=200,
        end=220,
        score=0.95,
        text="student@uic.edu"
    )
]

# Create PII result
result = PIIResult(
    job_id="550e8400-e29b-41d4-a716-446655440000",
    findings=findings,
    total_findings=len(findings)
)
```

### Queue Operations

```python
from datetime import datetime, timedelta
from shared.models import (
    PIIQueuePayload,
    ApprovalQueuePayload,
    ProcessingQueuePayload
)

# Push to PII scanning queue
pii_payload = PIIQueuePayload(
    job_id="550e8400-e29b-41d4-a716-446655440000",
    s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
    created_at=datetime.utcnow()
)

# If PII detected, create approval payload
approval_payload = ApprovalQueuePayload(
    job_id=pii_payload.job_id,
    s3_key=pii_payload.s3_key,
    pii_findings=findings,
    approval_token="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
    expires_at=datetime.utcnow() + timedelta(days=1)
)

# After approval or clean PII scan, create processing payload
processing_payload = ProcessingQueuePayload(
    job_id=pii_payload.job_id,
    s3_key=pii_payload.s3_key,
    approved_at=datetime.utcnow() if findings else None
)
```

### State Machine Validation

```python
from shared.models import JobStatus, VALID_TRANSITIONS

# Check valid transitions
status = JobStatus(
    job_id="550e8400-e29b-41d4-a716-446655440000",
    status="pii_scanning",
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow()
)

# Validate state transition
if status.can_transition_to("processing"):
    status.status = "processing"
    status.updated_at = datetime.utcnow()

# View all valid transitions
print(VALID_TRANSITIONS)
# {
#     "pii_scanning": ["awaiting_approval", "processing", "failed"],
#     "awaiting_approval": ["processing", "denied", "failed"],
#     "processing": ["completed", "failed"],
#     "completed": [],
#     "failed": [],
#     "denied": []
# }
```

### Redis Key Generation

```python
from shared.models import (
    job_status_key,
    queue_key,
    timeout_key,
    PII_QUEUE,
    APPROVAL_QUEUE,
    PROCESSING_QUEUE
)

# Generate job status key
job_id = "550e8400-e29b-41d4-a716-446655440000"
key = job_status_key(job_id)
# Returns: "eq-pdf:job:550e8400-e29b-41d4-a716-446655440000"

# Use predefined queue constants
redis.lpush(PII_QUEUE, pii_payload.model_dump_json())
redis.lpush(APPROVAL_QUEUE, approval_payload.model_dump_json())
redis.lpush(PROCESSING_QUEUE, processing_payload.model_dump_json())
```

### Constants Usage

```python
from shared.constants import (
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DENIED,
    TERMINAL_STATUSES,
    ACTIVE_STATUSES
)

# Check if status is terminal
if status.status in TERMINAL_STATUSES:
    print("Job is complete (success or failure)")

# Check if status is active
if status.status in ACTIVE_STATUSES:
    print("Job is still processing")
```

## Validation Rules

### Job ID
- Must be valid UUID format: `550e8400-e29b-41d4-a716-446655440000`

### S3 Keys
- Temporary uploads must use `temp/` prefix
- Example: `temp/550e8400-e29b-41d4-a716-446655440000/document.pdf`

### File Size
- Minimum: 1 byte
- Maximum: 100MB (100,000,000 bytes)

### Confidence Score
- Range: 0.0 to 1.0
- Example: 0.87 = 87% confidence

### Approval Token
- Length: 32-64 characters
- Example: `a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`

## State Machine

The job workflow follows this state machine:

```
pii_scanning
├── awaiting_approval → processing → completed
├── processing → completed
└── failed (terminal)

awaiting_approval
├── processing → completed
├── denied (terminal)
└── failed (terminal)

processing
├── completed (terminal)
└── failed (terminal)
```

## Redis Schema

All Redis keys use the `eq-pdf:` prefix for namespacing:

- **Job Status**: `eq-pdf:job:{job_id}` (Hash)
- **PII Queue**: `eq-pdf:queue:pii` (List)
- **Approval Queue**: `eq-pdf:queue:approval` (List)
- **Processing Queue**: `eq-pdf:queue:processing` (List)
- **Approval Timeouts**: `eq-pdf:timeouts:approval` (Sorted Set)
- **Daily Metrics**: `eq-pdf:metrics:daily` (Hash)

## Testing

Run the test suite:

```bash
uv run pytest tests/models/ -v
```

All models include comprehensive tests for:
- Validation rules
- State machine transitions
- JSON serialization/deserialization
- Redis compatibility
- Field constraints

## Type Safety

All models are fully typed and compatible with:
- MyPy static type checking
- IDE autocomplete
- Pydantic validation

Example type checking:

```python
from shared.models import JobStatus

def update_job_status(status: JobStatus) -> None:
    # IDE will provide autocomplete for all fields
    print(status.job_id)
    print(status.status)
    # Type checker will catch errors
    # status.status = "invalid"  # Error: Literal type violation
```

## Shared Services (PRD-003 Extensions)

### StorageService - Cleanup Operations

Added in PRD-003 completion for timeout worker support:

```python
from src.services.storage_service import StorageService

storage = StorageService(s3_client, temp_bucket, results_bucket)

# Cleanup all temp files for a specific job
deleted_count = await storage.cleanup_temp_files_for_job("job-123")
print(f"Deleted {deleted_count} temp files")

# List temp files older than 24 hours
old_files = await storage.list_temp_files(older_than_hours=24)
for file in old_files:
    print(f"{file['key']} - {file['age_hours']:.1f} hours old, {file['size']} bytes")

# Delete specific S3 object (idempotent)
success = await storage.delete_from_s3("bucket", "key")
```

### QueueService - Timeout Tracking

Added in PRD-003 completion for approval timeout management:

```python
from datetime import datetime, timedelta, timezone
from src.services.queue_service import QueueService

queue = QueueService(redis_client)

# Add job to timeout tracking (sorted set)
expires_at = datetime.now(timezone.utc) + timedelta(hours=4)
await queue.add_to_timeout_tracking("job-123", expires_at)

# Get all expired approvals
expired_jobs = await queue.get_expired_timeouts()
for job_id, timestamp in expired_jobs:
    print(f"Job {job_id} expired at {datetime.fromtimestamp(timestamp)}")

# Remove job from timeout tracking (after approval/denial)
removed = await queue.remove_from_timeout_tracking("job-123")

# Get count of jobs awaiting approval
count = await queue.get_timeout_count()
print(f"{count} jobs awaiting approval")
```

### JobService - Job Cleanup

Added in PRD-003 completion for job retention management:

```python
from src.services.job_service import JobService

job_service = JobService(redis_client)

# Clean up old completed/failed job
deleted = await job_service.cleanup_old_job("job-123")
if deleted:
    print("Job removed from Redis")
else:
    print("Job didn't exist or cleanup failed")
```

### Configuration - Cleanup & Retention Policies

Added in PRD-003 completion:

```python
from src.config import settings

# Timeout worker schedules
settings.approval_check_interval_seconds  # 30 - Check timeouts every 30s
settings.temp_cleanup_interval_hours      # 1 - Clean temp files hourly
settings.orphan_cleanup_interval_hours    # 4 - Check for orphans every 4 hours

# Retention policies
settings.approval_timeout_hours           # 4 - Approval deadline
settings.temp_file_retention_hours        # 24 - Delete temp files after 24h
settings.job_retention_days               # 30 - Keep jobs for 30 days
settings.max_processing_hours             # 2 - Mark stuck jobs after 2h
```

## Version

Current version: 0.1.0

## License

Part of the Equalify PDF Converter project.