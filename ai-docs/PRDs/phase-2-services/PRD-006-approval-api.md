# PRD-006: Approval Workflow API

## Overview
**Epic**: MVP PDF Converter Human Approval Workflow Backend
**Phase**: 2 - Core Services
**Estimated Effort**: 2 days
**Dependencies**: PRD-003 (Shared Services)

## Problem Statement
The FastAPI monolith needs backend API endpoints to process manual approval decisions for documents containing PII. When a job requires human review, these endpoints enable approval/denial decisions and route jobs to either the processing queue or cleanup.

**Note**: This is part of the FastAPI monolith and shares services, dependencies, and infrastructure with other components (API Gateway, Processing Worker, etc.). It is NOT an independent module.

## Success Criteria
- [ ] API endpoints accept approval/denial decisions with justification
- [ ] Secure token-based approval URLs with expiration
- [ ] Job routing to processing queue on approval
- [ ] Job cleanup and status updates on denial
- [ ] Integration with shared Redis queue and S3 services from PRD-003

## Technical Requirements

### API Endpoints (Part of Monolith)

```python
# src/api/approval.py - Approval endpoints
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal

router = APIRouter()

class ApprovalDecision(BaseModel):
    decision: Literal["approved", "denied"]
    justification: str

@router.post("/api/approve/{token}")
async def submit_approval(token: str, decision: ApprovalDecision):
    """Submit approval decision for job"""
    job = await validate_approval_token(token)
    if not job:
        raise HTTPException(404, "Invalid or expired approval token")

    await process_approval_decision(job.job_id, decision)
    return {"message": "Decision processed successfully"}

@router.get("/api/review/{token}")
async def get_review_details(token: str):
    """Get job details and PII findings for review"""
    job = await validate_approval_token(token)
    if not job:
        raise HTTPException(404, "Invalid or expired approval token")

    return {
        "job_id": job.job_id,
        "status": job.status,
        "pii_findings": job.pii_findings,
        "created_at": job.created_at,
        "expires_at": job.expires_at
    }
```

### Service Layer (Part of Monolith)

#### Approval Service
```python
# src/services/approval_service.py
from datetime import datetime
from src.dependencies import get_redis_client, get_s3_client
from src.services.cleanup_service import cleanup_job_files

async def process_approval_decision(job_id: str, decision: ApprovalDecision):
    """Process manual approval/denial decision"""
    redis = await get_redis_client()
    job = await get_job_from_id(job_id)

    if decision.decision == "approved":
        # Route to processing queue
        processing_payload = ProcessingQueuePayload(
            job_id=job.job_id,
            s3_key=job.s3_key,
            approved_at=datetime.utcnow()
        )

        await redis.lpush(PROCESSING_QUEUE, processing_payload.json())
        await update_job_status(
            job.job_id,
            "processing",
            approval_decision=decision
        )

    else:
        # Cleanup and mark denied
        await cleanup_job_files(job.s3_key)
        await update_job_status(
            job.job_id,
            "denied",
            approval_decision=decision
        )

    # Remove from timeout tracking
    await redis.zrem(APPROVAL_TIMEOUTS, job.job_id)
```

#### Cleanup Service
```python
# src/services/cleanup_service.py
import logging
from src.dependencies import get_s3_client

logger = logging.getLogger(__name__)

async def cleanup_job_files(s3_key: str):
    """Clean up S3 files for denied jobs"""
    s3 = await get_s3_client()
    try:
        await s3.delete_object(Bucket=S3_TEMP_BUCKET, Key=s3_key)
        logger.info(f"Cleaned up S3 file: {s3_key}")
    except Exception as e:
        logger.error(f"Failed to cleanup S3 file {s3_key}: {e}")
        # Don't fail the whole operation for cleanup errors
```

### Token Security

```python
# src/services/approval_service.py (continued)
import secrets
from datetime import datetime, timedelta
from typing import Optional

def generate_approval_token() -> str:
    """Generate cryptographically secure approval token"""
    return secrets.token_urlsafe(32)

async def validate_approval_token(token: str) -> Optional[JobStatus]:
    """Validate token and return associated job"""
    redis = await get_redis_client()

    # Find job by approval token
    job_keys = await redis.keys("eq-pdf:job:*")

    for job_key in job_keys:
        job_data = await redis.hgetall(job_key)
        if job_data.get("approval_token") == token:
            # Check expiration
            expires_at = datetime.fromisoformat(job_data["expires_at"])
            if datetime.utcnow() < expires_at:
                return JobStatus.parse_obj(job_data)
            else:
                # Clean up expired job
                await cleanup_expired_job(job_data["job_id"])
                return None

    return None
```

## Acceptance Criteria

### 1. Decision Processing
- [ ] Validates approval tokens and expiration
- [ ] Processes approved jobs to processing queue
- [ ] Cleans up denied jobs and temp files
- [ ] Updates job status with decision details
- [ ] Removes jobs from timeout tracking
- [ ] Handles invalid/expired tokens gracefully

### 2. Security
- [ ] Approval URLs use secure random tokens
- [ ] Tokens expire after configured timeout
- [ ] No sensitive data in URLs or logs
- [ ] Input validation and sanitization on all endpoints

### 3. Integration with Shared Services (PRD-003A)
- [ ] Uses shared Redis client from dependencies
- [ ] Uses shared S3 client from dependencies
- [ ] Communicates with shared queue infrastructure
- [ ] Updates job status using shared status service
- [ ] Works with Docker networking setup

## Deliverables

### Files to Create
```
/src/api/
└── approval.py                       # Approval API endpoints

/src/services/
├── approval_service.py              # Approval workflow logic
└── cleanup_service.py               # File cleanup service

/tests/api/
├── test_approval_flow.py            # Approval workflow tests
└── test_approval_security.py        # Token security tests

/tests/services/
├── test_approval_service.py         # Service layer tests
└── test_cleanup_service.py          # Cleanup logic tests
```

### Integration Points
- **src/main.py**: Register approval router
- **src/dependencies.py**: Use shared Redis/S3 clients
- **src/services/job_service.py**: Shared job status updates
- **PRD-003A services**: Queue management, status tracking

## Environment Configuration
```python
# Required environment variables (shared with monolith)
REDIS_URL=redis://redis:6379
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp

# Queue names
APPROVAL_QUEUE_NAME=eq-pdf:queue:approval
PROCESSING_QUEUE_NAME=eq-pdf:queue:processing

# Approval settings
APPROVAL_TIMEOUT_HOURS=4
```

## Testing Strategy

### Unit Tests
```python
# tests/services/test_approval_service.py
async def test_process_approval_approved(mock_redis, mock_s3):
    """Test approved decision routes to processing queue"""
    decision = ApprovalDecision(decision="approved", justification="Looks good")
    await process_approval_decision("job123", decision)

    # Verify job added to processing queue
    assert await mock_redis.llen(PROCESSING_QUEUE) == 1

    # Verify status updated
    job = await get_job_from_id("job123")
    assert job.status == "processing"

async def test_process_approval_denied(mock_redis, mock_s3):
    """Test denied decision cleans up files"""
    decision = ApprovalDecision(decision="denied", justification="Contains SSN")
    await process_approval_decision("job123", decision)

    # Verify S3 cleanup called
    mock_s3.delete_object.assert_called_once()

    # Verify status updated
    job = await get_job_from_id("job123")
    assert job.status == "denied"
```

### Integration Tests
```python
# tests/api/test_approval_flow.py
async def test_approval_flow_end_to_end(client, redis_client, s3_client):
    """Test complete approval flow with real Redis/S3"""
    # Create job with approval token
    token = generate_approval_token()

    # Submit approval decision
    response = await client.post(
        f"/api/approve/{token}",
        json={"decision": "approved", "justification": "Safe to process"}
    )
    assert response.status_code == 200

    # Verify job in processing queue
    payload = await redis_client.rpop(PROCESSING_QUEUE)
    assert payload is not None
```

## Definition of Done
- [ ] Approval/denial API endpoints implemented
- [ ] Approval service processes decisions correctly
- [ ] Jobs route to correct queues based on decisions
- [ ] Security tokens work and expire properly
- [ ] Cleanup service removes S3 files for denied jobs
- [ ] Unit tests pass with >80% coverage
- [ ] Integration tests with Redis and S3 pass
- [ ] Code integrated into monolith main.py
- [ ] Ready for PRD-006 (Processing Worker) integration
