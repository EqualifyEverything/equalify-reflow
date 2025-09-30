# PRD-003A: Shared Services Foundation

## Overview
**Epic**: Core Shared Services Layer
**Phase**: 2 - Core Services
**Estimated Effort**: 4 days
**Dependencies**: PRD-001 (Infrastructure), PRD-002 (Data Models)
**Blocks**: PRD-003B (API Endpoints), PRD-004 (PII Worker), PRD-006 (Processing Worker), PRD-007 (Timeout Worker)

## Problem Statement
The monolith application requires foundational shared services that ALL other components depend on. These services handle S3 storage operations, Redis queue management, job status tracking, configuration management, and dependency injection. This MUST be completed before any API endpoints or background workers can be implemented.

**Architecture Note:** These are shared services within a **single monolith application**. All API endpoints and workers will import and use these services. See `docs/architecture.md` for detailed monolith architecture explanation.

## Success Criteria
- [ ] S3 storage service with upload/download/delete operations
- [ ] Redis queue service with enqueue/dequeue/monitoring
- [ ] Job status management service with Redis persistence
- [ ] Configuration management with environment variables
- [ ] FastAPI dependency injection setup
- [ ] All services work with LocalStack and Redis
- [ ] Integration tests pass for each service
- [ ] Services can be imported by other modules

## Technical Requirements

### Service Layer Architecture

#### Monolith Shared Services Structure
```python
src/
├── main.py                    # FastAPI app + worker startup (created later)
├── config.py                  # Configuration management (THIS PRD)
├── dependencies.py            # Dependency injection (THIS PRD)
│
├── services/                  # Shared business logic (THIS PRD)
│   ├── storage_service.py     # S3 operations
│   ├── queue_service.py       # Redis queue operations
│   └── job_service.py         # Job status management
│
└── shared/                    # Data models (PRD-002)
    ├── models/                # Pydantic models
    └── constants/             # Queue names, Redis keys
```

**Key Point:** These services are the foundation layer. NO other implementation work (API endpoints, workers) can proceed until this is complete and tested.

## Core Services

### 1. Storage Service (S3 Operations)

#### Interface
```python
# src/services/storage_service.py
from typing import BinaryIO
import boto3
from botocore.exceptions import ClientError

class StorageService:
    """S3 storage operations for PDF and HTML documents"""

    def __init__(self, s3_client: boto3.client, temp_bucket: str, results_bucket: str):
        self.s3 = s3_client
        self.temp_bucket = temp_bucket
        self.results_bucket = results_bucket

    async def upload_temp_file(self, job_id: str, file_data: BinaryIO, filename: str) -> str:
        """Upload PDF to temp bucket"""
        s3_key = f"temp/{job_id}/{filename}"
        # Validate file size, type
        # Upload to S3
        # Return S3 key
        pass

    async def download_temp_file(self, s3_key: str) -> bytes:
        """Download PDF from temp bucket"""
        pass

    async def upload_result(self, job_id: str, content: str, format: str) -> str:
        """Upload processed HTML/MDX to results bucket"""
        s3_key = f"results/{job_id}.{format}"
        # Upload with correct Content-Type
        # Return public URL
        pass

    async def delete_temp_file(self, s3_key: str) -> None:
        """Delete temporary file after processing"""
        pass

    async def file_exists(self, bucket: str, key: str) -> bool:
        """Check if file exists in S3"""
        pass

    async def get_presigned_url(self, bucket: str, key: str, expiration: int = 3600) -> str:
        """Generate presigned URL for file access"""
        pass
```

#### Implementation Requirements
- Async operations using aioboto3
- LocalStack endpoint configuration
- File validation (PDF format, size limits)
- Error handling for network failures
- Presigned URL generation for secure access
- Bucket existence verification on startup

### 2. Queue Service (Redis Operations)

#### Interface
```python
# src/services/queue_service.py
from typing import Optional
import redis.asyncio as redis
from src.shared.models.queue import QueuePayload

class QueueService:
    """Redis queue operations for background job processing"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def enqueue(self, queue_name: str, payload: QueuePayload) -> None:
        """Add job to queue"""
        # Serialize payload
        # LPUSH to Redis list
        pass

    async def dequeue(self, queue_name: str, timeout: int = 5) -> Optional[QueuePayload]:
        """Pop job from queue (blocking)"""
        # BRPOP from Redis list
        # Deserialize payload
        pass

    async def queue_depth(self, queue_name: str) -> int:
        """Get current queue depth"""
        # LLEN Redis list
        pass

    async def peek_queue(self, queue_name: str, count: int = 10) -> list[QueuePayload]:
        """View queued jobs without removing"""
        # LRANGE Redis list
        pass

    async def health_check(self) -> bool:
        """Check Redis connectivity"""
        # PING command
        pass
```

#### Queue Names (from shared/constants)
```python
PII_QUEUE = "pii_scanning_queue"
PROCESSING_QUEUE = "processing_queue"
TIMEOUT_QUEUE = "timeout_check_queue"
```

#### Implementation Requirements
- Async Redis client using redis-py
- Blocking pop with timeout for worker efficiency
- Serialization/deserialization of Pydantic models
- Connection pooling and error recovery
- Queue monitoring and depth tracking

### 3. Job Service (Status Management)

#### Interface
```python
# src/services/job_service.py
from datetime import datetime
from typing import Optional
import redis.asyncio as redis
from src.shared.models.job import JobStatus, JobMetadata

class JobService:
    """Job status tracking and metadata management"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.key_prefix = "job:"

    async def create_job(self, job_id: str, initial_status: str, metadata: dict) -> JobMetadata:
        """Create new job with initial status"""
        # Create Redis hash with job metadata
        # Set timestamps
        pass

    async def get_job(self, job_id: str) -> Optional[JobMetadata]:
        """Retrieve job metadata"""
        # HGETALL Redis hash
        # Deserialize to Pydantic model
        pass

    async def update_status(self, job_id: str, new_status: str, metadata: Optional[dict] = None) -> None:
        """Update job status with optional metadata"""
        # HSET Redis hash
        # Update timestamps
        pass

    async def add_pii_findings(self, job_id: str, findings: list[dict]) -> None:
        """Store PII scan results"""
        pass

    async def add_processing_result(self, job_id: str, result_url: str, confidence: float) -> None:
        """Store processing completion data"""
        pass

    async def job_exists(self, job_id: str) -> bool:
        """Check if job exists"""
        # EXISTS command
        pass

    async def delete_job(self, job_id: str) -> None:
        """Delete job and all metadata"""
        # DEL command
        pass

    async def set_expiration(self, job_id: str, ttl_seconds: int) -> None:
        """Set TTL for job cleanup"""
        # EXPIRE command
        pass
```

#### Job Status Flow
```
pending → pii_scanning → awaiting_approval → processing → completed
                ↓              ↓                  ↓           ↓
              failed         failed            failed      failed
```

#### Implementation Requirements
- Redis hash storage for structured metadata
- Atomic status transitions
- Timestamp tracking (created_at, updated_at)
- TTL-based automatic cleanup
- Efficient existence checks

### 4. Configuration Management

#### Interface
```python
# src/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """Application configuration from environment variables"""

    # AWS Configuration
    aws_endpoint_url: str = "http://localhost:4566"
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"

    # S3 Buckets
    s3_temp_bucket: str = "equalify-temp"
    s3_results_bucket: str = "equalify-results"

    # Redis Configuration
    redis_url: str = "redis://localhost:6379"
    redis_max_connections: int = 10

    # Queue Configuration
    pii_queue_name: str = "pii_scanning_queue"
    processing_queue_name: str = "processing_queue"
    timeout_queue_name: str = "timeout_check_queue"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Application Settings
    log_level: str = "INFO"
    max_file_size_mb: int = 100
    job_ttl_days: int = 30

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Singleton instance
settings = Settings()
```

#### Implementation Requirements
- Pydantic Settings for validation
- Environment variable loading with .env support
- Type validation and defaults
- Singleton pattern for global access

### 5. Dependency Injection

#### Interface
```python
# src/dependencies.py
from typing import Generator
import boto3
import redis.asyncio as redis
from fastapi import Depends
from src.config import settings
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService

# Client dependencies
async def get_s3_client() -> Generator[boto3.client, None, None]:
    """Get S3 client (LocalStack or AWS)"""
    client = boto3.client(
        's3',
        endpoint_url=settings.aws_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key
    )
    try:
        yield client
    finally:
        client.close()

async def get_redis_client() -> Generator[redis.Redis, None, None]:
    """Get Redis client"""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.close()

# Service dependencies
def get_storage_service(s3_client = Depends(get_s3_client)) -> StorageService:
    """Get storage service instance"""
    return StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket
    )

def get_queue_service(redis_client = Depends(get_redis_client)) -> QueueService:
    """Get queue service instance"""
    return QueueService(redis_client=redis_client)

def get_job_service(redis_client = Depends(get_redis_client)) -> JobService:
    """Get job service instance"""
    return JobService(redis_client=redis_client)
```

#### Implementation Requirements
- FastAPI dependency injection pattern
- Proper resource cleanup (client connections)
- Service instance creation with configured clients
- Support for testing with mock clients

## Acceptance Criteria

### 1. Storage Service
- [ ] Upload PDF to temp bucket successfully
- [ ] Download PDF from temp bucket
- [ ] Upload HTML/MDX to results bucket
- [ ] Delete temporary files
- [ ] Generate presigned URLs
- [ ] Handle S3 errors gracefully
- [ ] Validate file formats and sizes
- [ ] Works with LocalStack endpoint

### 2. Queue Service
- [ ] Enqueue jobs to named queues
- [ ] Dequeue jobs with blocking timeout
- [ ] Serialize/deserialize Pydantic payloads
- [ ] Get queue depth accurately
- [ ] Peek queued jobs without consuming
- [ ] Health check returns Redis connectivity
- [ ] Handle connection failures gracefully

### 3. Job Service
- [ ] Create jobs with initial status
- [ ] Retrieve job metadata by ID
- [ ] Update job status atomically
- [ ] Store PII findings
- [ ] Store processing results
- [ ] Check job existence efficiently
- [ ] Delete jobs and cleanup
- [ ] Set TTL for automatic expiration

### 4. Configuration
- [ ] Load from environment variables
- [ ] Support .env file
- [ ] Validate configuration on startup
- [ ] Provide sensible defaults
- [ ] Type-safe configuration access

### 5. Dependency Injection
- [ ] FastAPI integration works
- [ ] Service instances created correctly
- [ ] Client connections managed properly
- [ ] Resource cleanup on request completion
- [ ] Support for test dependency overrides

## Deliverables

### Files to Create
```
/src/
├── config.py                      # Configuration management
├── dependencies.py                # FastAPI dependency injection
└── services/
    ├── __init__.py
    ├── storage_service.py         # S3 operations
    ├── queue_service.py           # Redis queue operations
    └── job_service.py             # Job status management

/tests/
├── services/
│   ├── test_storage_service.py   # S3 integration tests
│   ├── test_queue_service.py     # Redis integration tests
│   └── test_job_service.py       # Job management tests
└── test_config.py                 # Configuration tests
```

### Integration Tests
```bash
# Run service integration tests
uv run pytest tests/services/ -v

# Test with LocalStack and Redis running
docker-compose up -d localstack redis
uv run pytest tests/services/ --integration
```

## Technical Notes

### Testing with LocalStack
```python
# tests/services/test_storage_service.py
import pytest
from src.services.storage_service import StorageService
from src.config import settings

@pytest.fixture
async def storage_service():
    """Create storage service with LocalStack"""
    # Setup S3 client
    # Create test buckets
    service = StorageService(...)
    yield service
    # Cleanup test buckets

@pytest.mark.asyncio
async def test_upload_temp_file(storage_service):
    """Test PDF upload to temp bucket"""
    job_id = "test-job-123"
    with open("tests/fixtures/sample.pdf", "rb") as f:
        s3_key = await storage_service.upload_temp_file(job_id, f, "sample.pdf")

    assert s3_key.startswith("temp/")
    exists = await storage_service.file_exists(settings.s3_temp_bucket, s3_key)
    assert exists is True
```

### Testing with Redis
```python
# tests/services/test_queue_service.py
import pytest
from src.services.queue_service import QueueService
from src.shared.models.queue import PIIQueuePayload

@pytest.mark.asyncio
async def test_enqueue_dequeue(queue_service):
    """Test queue operations"""
    payload = PIIQueuePayload(
        job_id="test-123",
        s3_key="temp/test.pdf",
        created_at=datetime.utcnow()
    )

    await queue_service.enqueue("test_queue", payload)
    depth = await queue_service.queue_depth("test_queue")
    assert depth == 1

    dequeued = await queue_service.dequeue("test_queue")
    assert dequeued.job_id == "test-123"
```

### Error Handling Patterns
```python
# All services should implement consistent error handling
from src.shared.exceptions import StorageError, QueueError, JobNotFoundError

# Example in storage_service.py
async def upload_temp_file(self, job_id: str, file_data: BinaryIO, filename: str) -> str:
    try:
        # Upload logic
        return s3_key
    except ClientError as e:
        raise StorageError(f"Failed to upload file: {e}")
    except Exception as e:
        # Log unexpected errors
        raise StorageError(f"Unexpected error: {e}")
```

## Definition of Done
- [ ] All service files created and implemented
- [ ] Configuration management working
- [ ] Dependency injection setup complete
- [ ] Integration tests pass with Redis and LocalStack
- [ ] Services can be imported by other modules
- [ ] Error handling comprehensive
- [ ] Documentation complete with examples
- [ ] PRD-003B (API Endpoints) can begin implementation
- [ ] PRD-004, PRD-006, PRD-007 (Workers) can begin implementation

## References
- See `docs/architecture.md` for monolith architecture details
- See PRD-001 for infrastructure setup (Redis, LocalStack)
- See PRD-002 for data models used by services
