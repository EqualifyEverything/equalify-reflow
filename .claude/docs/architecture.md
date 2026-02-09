# Architecture

**Pattern:** Monolith with Background Task Queue (single Python application)

## System Design

### Single Application, Multiple Workers

The application runs as one process with:

- FastAPI server (main thread)
- PII worker (background thread)
- Timeout worker (background thread)

Document processing runs inline via FastAPI `BackgroundTasks`, not a separate worker.

All workers start in `src/main.py` via `lifespan` context manager.

## Service Layer

The service layer is split into specialized services for clean separation of concerns:

### Storage Services
- **StorageService** - Core S3 upload/download with circuit breakers for resilience
- **S3URLService** - URL generation (LocalStack vs AWS)
- **S3CleanupService** - Temporary file cleanup (best-effort, no circuit breakers)

### Core Services
- **QueueService** - Redis queue operations (LPUSH/BLPOP)
- **JobService** - Job state management (Redis hashes)
- **RateLimitService** - Rate limiting (Redis sorted sets)

### Processing Services
- **PipelineViewerService** - 7-step versioned document processing (Docling → Structure → Headings → Pages → Code Blocks → Boundaries → Cleanup)
- **DocumentProcessingService** - Pipeline orchestration with S3 storage and Redis job state
- **PIIDetectionService** - Microsoft Presidio PII scanning

### Approval Services
- **ApprovalService** - PII approval workflow
- **TimeoutService** - Approval timeout monitoring

## Redis Data Structures

### Task Queues (Redis Lists)

```python
# Queue a job for PII scanning
LPUSH eq-pdf:queue:pii '{"job_id": "uuid", "s3_key": "temp/uuid.pdf"}'

# Worker blocking pop (60s timeout)
BLPOP eq-pdf:queue:pii 60
```

### Job State (Redis Hash)

```python
# Job metadata stored as hash (top-level fields for O(1) access)
HSET eq-pdf:job:{job_id}
  status "processing"
  s3_key "temp/uuid.pdf"
  created_at "2025-01-01T12:00:00Z"
  confidence_score "0.87"
```

### Timeout Tracking (Redis Sorted Set)

```python
# Track approval deadlines (score = Unix timestamp)
ZADD eq-pdf:timeouts:approval {timestamp} "job_id"

# Get expired jobs
ZRANGEBYSCORE eq-pdf:timeouts:approval 0 {current_time}
```

## Service Communication

All services communicate via Docker DNS:

- Redis: `redis:6379` (NOT `localhost:6379`)
- LocalStack S3: `localstack:4566`
- API: `api-gateway:8080`

## AWS Bedrock Configuration (Hybrid AWS Setup)

The application uses a **hybrid AWS configuration**:

- **LocalStack** for S3 and CloudWatch (development/testing)
- **Real AWS Bedrock** for AI text correction (Claude Haiku via PydanticAI BedrockConverseModel)

### Why Hybrid?

- LocalStack doesn't support AWS Bedrock API
- S3/CloudWatch work perfectly in LocalStack for fast iteration
- Text correction requires real AWS Bedrock with SSO credentials

### Configuration

```bash
# .env
AI_PROVIDER=bedrock
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
BEDROCK_REGION=us-east-1
MAX_CONCURRENT_PAGES=5
PAGE_RETRY_ATTEMPTS=3
CLAUDE_MAX_TOKENS=4096
CLAUDE_TEMPERATURE=0.2
```

### Docker Compose Setup

```yaml
# docker-compose.dev.yml
services:
  api-gateway:
    environment:
      # Service-specific endpoints (no global AWS_ENDPOINT_URL)
      - AWS_ENDPOINT_URL_S3=http://localstack:4566       # S3 → LocalStack
      - AWS_ENDPOINT_URL_CLOUDWATCH=http://localstack:4566  # CloudWatch → LocalStack
      # AWS_ENDPOINT_URL_BEDROCK_RUNTIME not set → uses real AWS

      # Real AWS credentials from host (for Bedrock)
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - AWS_SESSION_TOKEN=${AWS_SESSION_TOKEN:-}
      - AWS_DEFAULT_REGION=us-east-1

      # Disable IMDS to prevent boto3 hang in Docker
      - AWS_EC2_METADATA_DISABLED=true
```

### Getting AWS Credentials

```bash
# 1. Configure AWS SSO profile (one-time setup)
#    See .aws-config-example for UIC profile configuration

# 2. Login via AWS SSO
aws sso login --profile uic

# 3. Start services with credentials
./restart-and-test.sh  # Loads credentials from SSO and restarts services
```

### How It Works

1. boto3 SDK reads service-specific endpoint variables
2. S3/CloudWatch requests → `http://localstack:4566`
3. Bedrock requests → Real AWS (no endpoint override)
4. Credentials from `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` environment
5. Region from `AWS_DEFAULT_REGION=us-east-1`
6. `AWS_EC2_METADATA_DISABLED=true` skips IMDS entirely (prevents 169.254.169.254 hang)

### Lazy Initialization

- PydanticAI agents are created on-demand during pipeline processing
- BedrockConverseModel initialized per-step, not at startup
- All workers start in <1 second

## Processing Pipeline Flow

1. **POST /api/v1/documents/submit** (API)
   - Validate PDF
   - Store in S3 temp bucket
   - Create job in Redis
   - Queue for PII scanning
   - Return `{job_id, status: "pii_scanning"}`

2. **PII Worker** (Background)
   - BLPOP from `eq-pdf:queue:pii`
   - Download PDF from S3
   - Microsoft Presidio scan
   - If clean → Queue for processing
   - If PII found → Set status "awaiting_approval"

3. **Processing** (BackgroundTasks)
   - `DocumentProcessingService.process_document()` runs as a FastAPI background task
   - Downloads PDF from S3
   - `PipelineViewerService` runs 7-step versioned pipeline:
     1. Docling extraction (v0) — PDF → markdown + page images
     2. Structure analysis — AI identifies headings, footnotes, page types
     3. Heading level fix — normalize heading hierarchy
     4. Page content corrections (v1) — AI fixes OCR errors per-page (parallel, semaphore-limited)
     5. Code block tagging — identify programming languages
     6. Cross-page boundary fixes (v2) — rejoin split content, relocate footnotes
     7. Final cleanup (v3) — normalize whitespace and formatting
   - Stores final markdown + figures to S3 results bucket
   - Updates job state to "completed" with cost/token metadata

4. **GET /api/v1/documents/{job_id}** (API)
   - Return status-specific response with URLs generated on-demand

## API Design Principles

1. **Top-level fields**: All job data stored as top-level Redis hash fields (not nested metadata blob) for O(1) access
2. **S3 keys, not URLs**: System stores S3 keys internally; URLs generated on-demand based on environment (LocalStack vs AWS)
3. **RESTful endpoints**: Resource-oriented URLs (`/api/v1/corrections/{job_id}`) with tokens in query/body for authentication
4. **Structured responses**: Pydantic models ensure type-safe, status-specific response structures
5. **Temporary data cleanup**: Page images and intermediate files removed after job completion

### URL Generation

- LocalStack: `http://localstack:4566/{bucket}/{key}`
- AWS Production: `https://{bucket}.s3.{region}.amazonaws.com/{key}`

### Token Security

- Approval tokens stored in Redis with 4-hour TTL
- Token validation includes job_id match verification (PATCH endpoint)
- Tokens automatically deleted after decision submission

## Important Patterns

### Error Handling

All errors caught by `ErrorHandlerMiddleware`:

```python
# Raise HTTPException in endpoints/services
raise HTTPException(status_code=404, detail="Job not found")
```

### Async Operations

All services use async/await:

```python
async def process_document(job_id: str):
    job = await job_service.get_job(job_id)
    content = await storage_service.download(job.s3_key)
    await queue_service.enqueue(QUEUE_PROCESSING, payload)
```

### Dependency Injection

Use FastAPI's Depends() for service injection:

```python
@router.get("/documents/{job_id}/status")
async def get_status(
    job_id: str,
    job_service: JobService = Depends(get_job_service)
):
    return await job_service.get_job(job_id)
```

### Configuration

All settings via Pydantic Settings (loaded from `.env`):

```python
from src.config import settings

redis_url = settings.redis_url  # From REDIS_URL env var
s3_bucket = settings.s3_temp_bucket
```
