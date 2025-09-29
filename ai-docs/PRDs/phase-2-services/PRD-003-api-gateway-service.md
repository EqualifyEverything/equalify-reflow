# PRD-003: API Gateway Service

## Overview
**Epic**: MVP PDF Converter API Layer
**Phase**: 2 - Core Services
**Estimated Effort**: 2 days
**Dependencies**: PRD-001 (Infrastructure), PRD-002 (Data Models)
**Parallel**: ✅ Independent service

## Problem Statement
The system needs a FastAPI gateway service that handles document submissions, provides real-time status tracking, and serves processed results. This service acts as the primary interface for external clients and manages the initial document storage and job creation workflow.

## Success Criteria
- [ ] Document upload endpoint accepts PDF files
- [ ] Job status tracking with real-time updates
- [ ] Result retrieval endpoint serves processed HTML
- [ ] Proper error handling and validation
- [ ] API documentation auto-generated
- [ ] Health checks and monitoring endpoints

## Technical Requirements

### API Endpoints

#### Document Submission
```python
POST /api/documents/submit
Content-Type: multipart/form-data

Request:
- file: PDF file (max 100MB)
- metadata: Optional JSON with filename, description

Response:
{
    "job_id": "uuid4-...",
    "status": "pii_scanning",
    "estimated_completion_minutes": 5,
    "created_at": "2025-01-01T12:00:00Z"
}
```

#### Status Tracking
```python
GET /api/documents/{job_id}/status
Response:
{
    "job_id": "uuid4-...",
    "status": "awaiting_approval",
    "created_at": "2025-01-01T12:00:00Z",
    "updated_at": "2025-01-01T12:01:30Z",
    "pii_findings": [
        {
            "entity_type": "EMAIL_ADDRESS",
            "text": "john.doe@example.com",
            "score": 0.95
        }
    ],
    "approval_url": "http://localhost:3000/approve/abc123token"
}
```

#### Result Retrieval
```python
GET /api/documents/{job_id}/result
Response (if completed):
{
    "job_id": "uuid4-...",
    "status": "completed",
    "html_url": "http://localhost:4566/equalify-pdf-results/uuid4.html",  # LocalStack S3
    "mdx_url": "http://localhost:4566/equalify-pdf-results/uuid4.mdx",   # LocalStack S3
    "confidence_score": 0.87,
    "processing_time_seconds": 180
}

Response (if still processing):
{
    "job_id": "uuid4-...",
    "status": "processing",
    "estimated_completion_at": "2025-01-01T12:05:00Z"
}
```

### Service Architecture

#### FastAPI Application Structure
```python
app/
├── main.py                    # FastAPI app initialization
├── routers/
│   ├── documents.py          # Document endpoints
│   ├── health.py             # Health checks
│   └── admin.py              # Admin endpoints
├── services/
│   ├── storage.py            # S3 operations
│   ├── queue.py              # Redis queue operations
│   └── job_tracker.py        # Job status management
├── middleware/
│   ├── error_handler.py      # Global error handling
│   ├── logging.py            # Request logging
│   └── cors.py               # CORS configuration
└── dependencies/
    ├── redis.py              # Redis connection
    ├── s3.py                 # S3 client
    └── auth.py               # Authentication (future)
```

## Acceptance Criteria

### 1. Document Upload
- [ ] Accepts PDF files up to 100MB
- [ ] Validates PDF format before storage
- [ ] Stores files in S3 temp bucket with UUID naming
- [ ] Creates job in Redis with "pii_scanning" status
- [ ] Queues job in PII processing queue
- [ ] Returns job ID immediately (<100ms response)

### 2. Status Tracking
- [ ] Returns current job status from Redis
- [ ] Includes relevant metadata based on status
- [ ] Handles non-existent job IDs gracefully
- [ ] Updates include timestamps and progress indicators
- [ ] PII findings displayed when awaiting approval

### 3. Result Retrieval
- [ ] Returns S3 URLs for completed jobs
- [ ] Includes processing metadata and confidence scores
- [ ] Handles incomplete jobs appropriately
- [ ] Provides clear error messages for failed jobs
- [ ] URLs are accessible and serve correct content

### 4. Error Handling
- [ ] Proper HTTP status codes for all scenarios
- [ ] Detailed error messages in development
- [ ] Sanitized error messages in production
- [ ] Request validation with clear field-level errors
- [ ] Rate limiting and abuse prevention

### 5. Documentation
- [ ] OpenAPI spec auto-generated
- [ ] Interactive Swagger UI available
- [ ] Request/response examples provided
- [ ] Error response formats documented

## Deliverables

### Files to Create
```
/services/api-gateway/
├── Dockerfile                     # Container definition
├── requirements.txt               # Dependencies
├── app/
│   ├── main.py                   # FastAPI application
│   ├── config.py                 # Environment configuration
│   ├── routers/
│   │   ├── documents.py          # Document endpoints
│   │   └── health.py             # Health endpoints
│   ├── services/
│   │   ├── storage_service.py    # S3 operations
│   │   ├── queue_service.py      # Redis operations
│   │   └── job_service.py        # Job management
│   └── middleware/
│       ├── error_handler.py      # Error handling
│       └── logging_middleware.py # Request logging
├── tests/
│   ├── test_documents.py         # Endpoint tests
│   ├── test_storage.py           # S3 integration tests
│   └── test_queue.py             # Redis integration tests
└── docs/
    └── api.md                    # API documentation
```

### Container Configuration
```dockerfile
FROM python:3.12-slim

# Install uv
RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY app/ ./app/
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Technical Notes

### Storage Operations
```python
# S3 file upload with validation
async def store_document(file: UploadFile) -> str:
    # Validate PDF format
    if not file.content_type == 'application/pdf':
        raise HTTPException(400, "Only PDF files accepted")

    # Generate unique S3 key
    job_id = str(uuid.uuid4())
    s3_key = f"temp/{job_id}.pdf"

    # Upload to S3
    await s3_client.upload_fileobj(file.file, S3_TEMP_BUCKET, s3_key)
    return job_id, s3_key
```

### Queue Integration
```python
# Queue job for PII scanning
async def queue_pii_job(job_id: str, s3_key: str):
    payload = PIIQueuePayload(
        job_id=job_id,
        s3_key=s3_key,
        created_at=datetime.utcnow()
    )

    await redis.lpush(PII_QUEUE, payload.json())
    await redis.hset(
        f"{JOB_STATUS_PREFIX}{job_id}",
        "status", "pii_scanning",
        "created_at", payload.created_at.isoformat()
    )
```

### Health Checks
```python
@router.get("/health")
async def health_check():
    """Health check endpoint for container orchestration"""
    checks = {
        "redis": await check_redis_connection(),
        "s3": await check_s3_access(),
        "queue": await check_queue_depth()
    }

    if all(checks.values()):
        return {"status": "healthy", "checks": checks}
    else:
        raise HTTPException(503, {"status": "unhealthy", "checks": checks})
```

### Environment Configuration
```python
# Environment variables required
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp
S3_RESULTS_BUCKET=equalify-results
REDIS_URL=redis://redis:6379
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
```

## Definition of Done
- [ ] All API endpoints implemented and tested
- [ ] Container builds and runs successfully
- [ ] Integration tests pass with Redis and S3
- [ ] API documentation generated and complete
- [ ] Error handling covers all edge cases
- [ ] Health checks enable proper orchestration
- [ ] Service ready for integration with workers