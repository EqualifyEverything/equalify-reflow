# PRD-004: Document API Endpoints

## Overview
**Epic**: FastAPI REST API Endpoints
**Phase**: 2 - API Layer
**Estimated Effort**: 2 days
**Dependencies**: PRD-003 (Shared Services Foundation) - MUST BE COMPLETE FIRST
**Parallel**: ❌ Cannot start until PRD-003 is complete

## Problem Statement
The monolith application needs FastAPI REST API endpoints that handle document submissions, provide real-time status tracking, and serve processed results. This API layer uses the shared services foundation (PRD-003) to manage document storage, job creation, and status tracking workflows.

**Architecture Note:** This API layer is part of a **single monolith application**, not a separate microservice. It runs in the same process as the background workers, sharing code, configuration, and dependencies. All endpoints use the shared services implemented in PRD-003. See `docs/architecture.md` for detailed monolith architecture explanation.

**Critical Dependency:** PRD-003 MUST be completed and tested before beginning this work. All API endpoints depend on storage_service, queue_service, and job_service being fully functional.

## Success Criteria
- [ ] Document upload endpoint accepts PDF files
- [ ] Job status tracking with real-time updates
- [ ] Result retrieval endpoint serves processed HTML
- [ ] Proper error handling and validation
- [ ] API documentation auto-generated
- [ ] Health checks and monitoring endpoints
- [ ] Endpoints work end-to-end with shared services

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

**Implementation:**
```python
# src/api/documents.py
from fastapi import APIRouter, UploadFile, Depends, HTTPException
from src.dependencies import get_storage_service, get_queue_service, get_job_service
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService

router = APIRouter(prefix="/api/documents", tags=["documents"])

@router.post("/submit")
async def submit_document(
    file: UploadFile,
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service),
    jobs: JobService = Depends(get_job_service)
):
    """Submit PDF document for processing"""
    # 1. Validate file format and size
    if file.content_type != 'application/pdf':
        raise HTTPException(400, "Only PDF files accepted")

    # 2. Upload to S3 temp bucket using shared storage service
    job_id = str(uuid.uuid4())
    s3_key = await storage.upload_temp_file(job_id, file.file, file.filename)

    # 3. Create job using shared job service
    job = await jobs.create_job(
        job_id=job_id,
        initial_status="pii_scanning",
        metadata={"filename": file.filename, "s3_key": s3_key}
    )

    # 4. Enqueue for PII scanning using shared queue service
    await queue.enqueue("pii_scanning_queue", {
        "job_id": job_id,
        "s3_key": s3_key
    })

    return {
        "job_id": job_id,
        "status": "pii_scanning",
        "estimated_completion_minutes": 5,
        "created_at": job.created_at
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

**Implementation:**
```python
@router.get("/{job_id}/status")
async def get_job_status(
    job_id: str,
    jobs: JobService = Depends(get_job_service)
):
    """Get current job status and metadata"""
    # Use shared job service to retrieve status
    job = await jobs.get_job(job_id)

    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    return job.dict()
```

#### Result Retrieval
```python
GET /api/documents/{job_id}/result
Response (if completed):
{
    "job_id": "uuid4-...",
    "status": "completed",
    "html_url": "http://localhost:4566/equalify-pdf-results/uuid4.html",
    "mdx_url": "http://localhost:4566/equalify-pdf-results/uuid4.mdx",
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

**Implementation:**
```python
@router.get("/{job_id}/result")
async def get_job_result(
    job_id: str,
    jobs: JobService = Depends(get_job_service)
):
    """Get processed document result"""
    job = await jobs.get_job(job_id)

    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    if job.status != "completed":
        return {
            "job_id": job_id,
            "status": job.status,
            "estimated_completion_at": job.estimated_completion_at
        }

    # Return result URLs from job metadata
    return {
        "job_id": job_id,
        "status": "completed",
        "html_url": job.html_url,
        "mdx_url": job.mdx_url,
        "confidence_score": job.confidence_score,
        "processing_time_seconds": job.processing_time_seconds
    }
```

### API Layer Architecture

#### Monolith Application Structure
```python
src/
├── main.py                    # FastAPI app entry point (THIS PRD)
├── config.py                  # Configuration (PRD-003A)
├── dependencies.py            # Dependency injection (PRD-003A)
│
├── api/                       # REST API endpoints (THIS PRD)
│   ├── __init__.py
│   ├── documents.py           # Document submission/status/results
│   └── health.py              # Health checks
│
├── middleware/                # FastAPI middleware (THIS PRD)
│   ├── __init__.py
│   ├── error_handler.py       # Global error handling
│   └── logging_middleware.py  # Request logging
│
├── services/                  # Shared services (PRD-003A - COMPLETE)
│   ├── storage_service.py     # S3 operations
│   ├── queue_service.py       # Redis queue operations
│   └── job_service.py         # Job status management
│
└── shared/                    # Data models (PRD-002)
    ├── models/                # Pydantic models
    └── constants/             # Queue names, Redis keys
```

**Key Point:** API endpoints ONLY handle HTTP request/response logic. All business logic is in shared services from PRD-003A.

## Acceptance Criteria

### 1. Document Upload
- [ ] Accepts PDF files up to 100MB
- [ ] Validates PDF format before storage
- [ ] Uses storage_service to upload to S3 temp bucket
- [ ] Uses job_service to create job with "pii_scanning" status
- [ ] Uses queue_service to enqueue job in PII processing queue
- [ ] Returns job ID immediately (<100ms response)

### 2. Status Tracking
- [ ] Uses job_service to retrieve current job status
- [ ] Returns relevant metadata based on status
- [ ] Handles non-existent job IDs gracefully
- [ ] Updates include timestamps and progress indicators
- [ ] PII findings displayed when awaiting approval

### 3. Result Retrieval
- [ ] Uses job_service to get job metadata
- [ ] Returns S3 URLs for completed jobs
- [ ] Includes processing metadata and confidence scores
- [ ] Handles incomplete jobs appropriately
- [ ] Provides clear error messages for failed jobs

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
/src/
├── main.py                        # FastAPI application entry point
├── api/
│   ├── __init__.py
│   ├── documents.py               # Document endpoints
│   └── health.py                  # Health endpoints
├── middleware/
│   ├── __init__.py
│   ├── error_handler.py           # Error handling
│   └── logging_middleware.py      # Request logging
└── tests/
    └── api/
        ├── test_documents.py      # Endpoint tests
        └── test_health.py         # Health check tests
```

**Note:** Files in `src/services/` are NOT created here - they are completed in PRD-003A.

### FastAPI Application Entry Point
```python
# src/main.py
from fastapi import FastAPI
from src.api import documents, health
from src.middleware.error_handler import error_handler_middleware
from src.middleware.logging_middleware import logging_middleware
from src.config import settings

app = FastAPI(
    title="Equalify PDF Converter API",
    description="REST API for converting PDFs to accessible HTML",
    version="1.0.0"
)

# Add middleware
app.middleware("http")(logging_middleware)
app.middleware("http")(error_handler_middleware)

# Include routers
app.include_router(documents.router)
app.include_router(health.router)

@app.on_event("startup")
async def startup_event():
    """Verify infrastructure connectivity"""
    # Test Redis connection
    # Test S3 access
    # Log startup confirmation
    pass
```

### Local Development
```bash
# Start infrastructure services (Redis, LocalStack)
docker-compose up -d localstack redis

# Run the FastAPI application
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# API available at http://localhost:8080
# Swagger docs at http://localhost:8080/docs
```

## Technical Notes

### Health Checks
```python
# src/api/health.py
from fastapi import APIRouter, Depends, HTTPException
from src.dependencies import get_queue_service, get_storage_service

router = APIRouter(prefix="/health", tags=["health"])

@router.get("/")
async def health_check(
    queue: QueueService = Depends(get_queue_service),
    storage: StorageService = Depends(get_storage_service)
):
    """Health check endpoint for container orchestration"""
    checks = {
        "redis": await queue.health_check(),
        "s3": await storage.health_check()
    }

    if all(checks.values()):
        return {"status": "healthy", "checks": checks}
    else:
        raise HTTPException(503, {"status": "unhealthy", "checks": checks})
```

### Error Handling Middleware
```python
# src/middleware/error_handler.py
from fastapi import Request, status
from fastapi.responses import JSONResponse
from src.shared.exceptions import StorageError, QueueError, JobNotFoundError

async def error_handler_middleware(request: Request, call_next):
    """Global error handling for API"""
    try:
        return await call_next(request)
    except JobNotFoundError as e:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Job not found", "detail": str(e)}
        )
    except StorageError as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Storage operation failed", "detail": str(e)}
        )
    except QueueError as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Queue operation failed", "detail": str(e)}
        )
    except Exception as e:
        # Log unexpected errors
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error"}
        )
```

### Request Logging Middleware
```python
# src/middleware/logging_middleware.py
import logging
import time
from fastapi import Request

logger = logging.getLogger(__name__)

async def logging_middleware(request: Request, call_next):
    """Log all API requests"""
    start_time = time.time()

    # Log request
    logger.info(f"Request: {request.method} {request.url.path}")

    response = await call_next(request)

    # Log response
    duration = time.time() - start_time
    logger.info(
        f"Response: {response.status_code} "
        f"Duration: {duration:.3f}s "
        f"Path: {request.url.path}"
    )

    return response
```

### Environment Configuration
```python
# Required environment variables (from PRD-003A config.py)
AWS_ENDPOINT_URL=http://localhost:4566
S3_TEMP_BUCKET=equalify-temp
S3_RESULTS_BUCKET=equalify-results
REDIS_URL=redis://localhost:6379
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO
MAX_FILE_SIZE_MB=100
```

## Testing

### API Integration Tests
```python
# tests/api/test_documents.py
import pytest
from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

def test_submit_document():
    """Test document submission endpoint"""
    with open("tests/fixtures/sample.pdf", "rb") as f:
        response = client.post(
            "/api/documents/submit",
            files={"file": ("sample.pdf", f, "application/pdf")}
        )

    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "pii_scanning"

def test_get_job_status():
    """Test status tracking endpoint"""
    # Submit document first
    job_id = submit_test_document()

    # Get status
    response = client.get(f"/api/documents/{job_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job_id

def test_get_job_result_not_found():
    """Test result endpoint with non-existent job"""
    response = client.get("/api/documents/invalid-job-id/result")
    assert response.status_code == 404
```

## Definition of Done
- [ ] All API endpoints implemented
- [ ] FastAPI application runs and serves requests
- [ ] Endpoints use shared services from PRD-003A
- [ ] Middleware for error handling and logging working
- [ ] Health checks respond correctly
- [ ] API integration tests pass
- [ ] OpenAPI documentation generated
- [ ] Application can be run with `uvicorn src.main:app`
- [ ] Swagger UI accessible at /docs
- [ ] Ready for worker integration (PRD-004, PRD-006, PRD-007)

## References
- See `docs/architecture.md` for monolith architecture details
- See PRD-003A for shared services implementation (MUST BE COMPLETE)
- See PRD-001 for infrastructure setup (Redis, LocalStack)
- See PRD-002 for data models used in request/response
