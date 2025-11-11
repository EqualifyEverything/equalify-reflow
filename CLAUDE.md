# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The Equalify PDF Converter transforms PDF documents into accessible, semantic markup for University of Illinois Chicago (UIC). System processes **course materials only** - strict architectural boundary against student records or PII.

## ⚠️ CRITICAL: Development Workflow

This project uses **fully containerized development**. You MUST work inside Docker containers.

### Essential Commands

```bash
# Development (USE THESE BY DEFAULT)
make dev            # Start all services (Redis, LocalStack, API)
make down           # Stop all services
make logs           # View all service logs
make logs-api       # View API logs only
make health         # Verify infrastructure
make shell          # Access container shell

# Testing (3-tier strategy)
make test-fast      # Unit tests (<30s) - Run before commits
make test-integration # Integration tests (<2min) - Run before PRs
make test-e2e       # E2E tests (<5min) - Run before merges
make test-all       # All tests in Docker

# Coverage
make coverage       # Run tests with coverage
make coverage-html  # Generate and open HTML report

# Debugging
make redis-cli      # Connect to Redis CLI
make shell          # Access container bash shell
```

### Never Do These
- ❌ DO NOT run `uv run uvicorn` directly on host
- ❌ DO NOT install dependencies locally with `uv sync`
- ❌ DO NOT use `localhost:6379` in code (use `redis:6379`)
- ❌ DO NOT run `python` or `pytest` directly on host

### How Development Works
1. `make dev` - Starts all services in Docker
2. Edit code in `src/` on host machine
3. Code auto-reloads in container (hot reload enabled)
4. `make test-fast` - Quick feedback before commit
5. `make shell` - Debug inside container if needed

## Architecture

**Pattern:** Monolith with Background Task Queue (single Python application)

```
src/
├── main.py                    # FastAPI app + worker startup
├── config.py                  # Settings (from env vars)
├── dependencies.py            # Dependency injection
│
├── api/                       # REST API endpoints
│   ├── documents.py           # POST /submit, GET /status, GET /result
│   ├── approval.py            # POST /approve, POST /reject
│   ├── health.py              # GET /health, GET /metrics
│   └── dev_monitoring.py      # Dev-only endpoints (disabled in prod)
│
├── workers/                   # Background task processors
│   ├── pii_worker.py          # Monitors eq-pdf:queue:pii
│   ├── processing_worker.py   # Monitors eq-pdf:queue:processing
│   └── timeout_worker.py      # Scheduled approval timeout checks
│
├── services/                  # Shared business logic
│   ├── storage_service.py     # S3 operations (upload/download/generate URLs)
│   ├── queue_service.py       # Redis queue operations (LPUSH/BLPOP)
│   ├── job_service.py         # Job state management (Redis hashes)
│   ├── pii_service.py         # Microsoft Presidio integration
│   ├── processing_service.py  # AI pipeline orchestration
│   └── rate_limit_service.py  # Rate limiting (Redis)
│
├── middleware/                # FastAPI middleware
│   ├── api_key_auth.py        # API key authentication
│   ├── docs_auth.py           # Swagger/docs HTTP Basic auth
│   ├── error_handler.py       # Global exception handling
│   ├── logging_middleware.py  # Request/response logging
│   ├── rate_limit.py          # Rate limiting middleware
│   ├── cors.py                # CORS configuration
│   └── metrics.py             # Prometheus metrics
│
├── agents/                    # AI agents (PydanticAI)
│   └── accessibility_agent.py # Accessibility enhancement agent
│
├── shared/                    # Data models and constants
│   ├── models/                # Pydantic models
│   │   ├── job.py             # Job, JobCreate, JobUpdate
│   │   ├── queue.py           # QueueMessage, PIIQueuePayload
│   │   └── api.py             # API request/response models
│   └── constants/             # Application constants
│       ├── queues.py          # Queue names (QUEUE_PII, QUEUE_PROCESSING)
│       ├── redis_keys.py      # Redis key patterns
│       └── statuses.py        # Job status constants
│
└── utils/                     # Utility functions
    ├── confidence_scoring.py  # Confidence calculation
    ├── text_cleanup.py        # Text normalization
    ├── token_generator.py     # Secure token generation
    └── retry_helpers.py       # Retry decorators
```

## Authentication

The API implements two independent authentication layers:

### API Key Authentication

**Purpose:** Secure API endpoints for programmatic access
**Header:** `X-API-Key` (configurable via `API_KEY_HEADER_NAME`)
**Implementation:** `src/middleware/api_key_auth.py`

**Configuration (.env):**
```bash
# Enable/disable API key auth
ENABLE_API_KEY_AUTH=true

# Header name (default: X-API-Key)
API_KEY_HEADER_NAME=X-API-Key

# Comma-separated list of valid keys
API_KEYS=uic-2bd2c716-bc67-4032-ba66-e4f35c441759
```

**Public Endpoints (no API key required):**
- `/health`, `/health/ready`, `/metrics` - Always public for monitoring
- `/api/dev/monitoring/*` - Public in dev environment only

**Usage Example:**
```bash
# With valid API key
curl -H "X-API-Key: uic-2bd2c716-bc67-4032-ba66-e4f35c441759" \
  http://localhost:8080/api/documents/submit

# Missing API key → 401 Unauthorized
curl http://localhost:8080/api/documents/submit
```

**Security Features:**
- Constant-time comparison (`secrets.compare_digest()`) prevents timing attacks
- Multiple keys supported (comma-separated in env var)
- Keys stored as `SecretStr` to prevent accidental logging
- Whitespace automatically stripped from configured keys

**Generating API Keys:**
```bash
# Generate a new UIC-prefixed UUID key
python3 -c "import uuid; print(f'uic-{uuid.uuid4()}')"
```

### Swagger/Docs Authentication (HTTP Basic)

**Purpose:** Protect API documentation in production
**Endpoints:** `/docs`, `/openapi.json`, `/redoc`
**Implementation:** `src/middleware/docs_auth.py`

**Configuration (.env):**
```bash
# Enable/disable docs auth (false in dev, true in prod)
ENABLE_DOCS_AUTH=true

# HTTP Basic credentials
DOCS_USERNAME=dase
DOCS_PASSWORD=a11y
```

**Usage:**
- Browser automatically prompts for username/password
- Credentials: `dase` / `a11y` (configured in .env)
- Environment-dependent: disabled in dev for easy testing, enabled in prod

**Security Features:**
- HTTP Basic Authentication (RFC 7617)
- Constant-time credential comparison
- `WWW-Authenticate` header triggers browser login prompt
- Password stored as `SecretStr`

### Middleware Stack Order

Middleware executes in reverse order of registration (last added = first executed):

```python
# src/main.py
if settings.enable_api_key_auth:
    app.add_middleware(APIKeyAuthMiddleware)    # 1st: API key validation

if settings.enable_docs_auth:
    app.add_middleware(DocsAuthMiddleware)      # 2nd: Docs basic auth

app.add_middleware(ErrorHandlerMiddleware)      # 3rd: Catch errors
app.add_middleware(RateLimitMiddleware)         # 4th: Rate limiting
app.add_middleware(LoggingMiddleware)           # 5th: Logging
add_cors_middleware(app)                        # 6th: CORS headers
```

**Execution Flow:**
1. CORS → 2. Logging → 3. Rate Limit → 4. Error Handler → 5. Docs Auth → 6. API Key Auth → 7. Endpoint

### Testing Authentication

**Unit Tests:**
- `tests/unit/middleware/test_api_key_auth.py` - API key middleware (21 tests)
- `tests/unit/middleware/test_docs_auth.py` - Docs auth middleware (18 tests)

**Integration Tests:**
- `tests/integration/api/test_api_authentication.py` - Full auth flow (10 tests)

**Manual Testing:**
```bash
# Start services with auth enabled
make dev

# Test API endpoint without key (should fail)
curl http://localhost:8080/api/documents/test-id
# → 401 Unauthorized

# Test with valid key (should work or 404 if job not found)
curl -H "X-API-Key: uic-2bd2c716-bc67-4032-ba66-e4f35c441759" \
  http://localhost:8080/api/documents/test-id

# Test Swagger UI (should prompt for username/password)
open http://localhost:8080/docs
# → Browser login prompt: dase / a11y

# Test public endpoints still work
curl http://localhost:8080/health
# → 200 OK (no auth required)
```

### Disabling Authentication (Testing)

To disable auth for testing:

```bash
# .env
ENABLE_API_KEY_AUTH=false
ENABLE_DOCS_AUTH=false
```

**Important:** Always re-enable before deploying to production!

## Key Architectural Patterns

### 1. Single Application, Multiple Workers
The application runs as one process with:
- FastAPI server (main thread)
- PII worker (background thread)
- Processing worker (background thread)
- Timeout worker (background thread)

All workers start in `src/main.py` via `lifespan` context manager.

### 2. Redis Data Structures

**Task Queues (Redis Lists):**
```python
# Queue a job for PII scanning
LPUSH eq-pdf:queue:pii '{"job_id": "uuid", "s3_key": "temp/uuid.pdf"}'

# Worker blocking pop (60s timeout)
BLPOP eq-pdf:queue:pii 60
```

**Job State (Redis Hash):**
```python
# Job metadata stored as hash
HSET eq-pdf:job:{job_id}
  status "processing"
  s3_key "temp/uuid.pdf"
  created_at "2025-01-01T12:00:00Z"
  confidence_score "0.87"
```

**Timeout Tracking (Redis Sorted Set):**
```python
# Track approval deadlines (score = Unix timestamp)
ZADD eq-pdf:timeouts:approval {timestamp} "job_id"

# Get expired jobs
ZRANGEBYSCORE eq-pdf:timeouts:approval 0 {current_time}
```

### 3. Service Communication
All services communicate via Docker DNS:
- Redis: `redis:6379` (NOT `localhost:6379`)
- LocalStack S3: `localstack:4566`
- API: `api-gateway:8080`

### 4. Environment Configuration

**Local Development:**
- `.env` contains application config (NO AWS credentials)
- `docker-compose.dev.yml` sets AWS credentials for containers
- LocalStack runs inside Docker network

**AWS Operations:**
- Use AWS profiles from `~/.aws/config` (see `.aws-config-example`)
- Makefile commands handle profiles automatically:
  ```bash
  make aws-health   # Check deployment
  make aws-logs     # View CloudWatch logs
  make aws-status   # ECS service status
  ```

**Never:** Source `.env` manually in your shell - it's for Docker Compose only

## Testing Architecture

### 3-Tier Testing Strategy

**Unit Tests** (`tests/unit/`):
- Fully mocked dependencies
- Fast (<100ms per test)
- No Docker required
- Tests business logic only

**Integration Tests** (`tests/integration/`):
- **Uses:** Python testcontainers library (redis:7-alpine, localstack)
- **Execution:** Host machine via `make test-integration` (not inside Docker)
- **Isolation:** Fresh containers per test session, true test isolation
- **Services:** Real Redis + Real S3 (LocalStack), AI/ML mocked
- **Speed:** ~7 seconds total (19 tests), no shared state issues
- **Benefits:** Catches serialization bugs, race conditions, Redis atomicity

**E2E Tests** (`tests/e2e/`):
- Full workflows with minimal mocking
- Slow (<30s per test)
- Validates complete processing pipeline

### Shared Test Fixtures

**IMPORTANT:** Always use shared fixtures from `tests/conftest_fixtures/`:

```python
# Mock Clients
from tests.conftest_fixtures.clients import (
    mock_redis_client,      # AsyncMock for Redis
    mock_s3_client,         # MagicMock for S3
    mock_ai_service,        # AsyncMock for AI
    mock_presidio_analyzer  # MagicMock for PII
)

# Data Factories
from tests.conftest_fixtures.data_factories import (
    generate_job_id,                # Generate UUID
    create_pii_queue_payload,       # Create queue message
    create_test_pdf_content,        # Generate minimal PDF
    create_test_upload_file         # Create FastAPI UploadFile
)

# Test Helpers
from tests.conftest_fixtures.helpers import (
    assert_job_state,         # Assert job status
    assert_s3_upload,         # Assert S3 called correctly
    setup_redis_error         # Configure error scenarios
)
```

### Running Tests

```bash
# Fast feedback (before commit)
make test-fast          # ~30s with parallelization

# Before opening PR (requires Docker Desktop running)
make test-integration   # ~7s with testcontainers

# Before merging
make test-e2e           # ~5min full workflows

# Run specific test
uv run pytest tests/unit/services/test_storage_service.py::test_name -v

# Run integration tests directly
uv run pytest tests/integration -m integration -v

# Debug with verbose output
uv run pytest tests/path/to/test.py -vvs
```

### Test Markers

Use pytest markers for selective execution:

```python
@pytest.mark.unit                # Unit test (fast, mocked)
@pytest.mark.integration         # Integration test (testcontainers)
@pytest.mark.slow                # E2E test (>5s)
@pytest.mark.requires_redis      # Needs Redis
@pytest.mark.requires_s3         # Needs S3/LocalStack
```

Run specific markers:
```bash
pytest -m unit                   # Unit tests only
pytest -m integration            # Integration tests only
pytest -m "not slow"             # Skip slow tests
```

## Adding New Features

### Adding an API Endpoint

1. **Define route** in `src/api/documents.py`:
```python
@router.post("/documents/submit")
async def submit_document(
    file: UploadFile,
    storage: StorageService = Depends(get_storage_service)
):
    # Implementation
```

2. **Add business logic** in `src/services/`:
```python
# src/services/document_service.py
class DocumentService:
    def __init__(self, storage: StorageService, queue: QueueService):
        self.storage = storage
        self.queue = queue
```

3. **Create Pydantic models** in `src/shared/models/`:
```python
# src/shared/models/document.py
class DocumentSubmitRequest(BaseModel):
    filename: str
    content_type: str
```

4. **Write tests**:
- Unit: `tests/unit/api/test_documents.py`
- Integration: `tests/integration/test_document_flow.py`

5. **Update dependency injection** in `src/dependencies.py` if needed

### Adding a Background Worker

1. **Create worker** in `src/workers/new_worker.py`:
```python
async def start_new_worker(shutdown_event: asyncio.Event):
    """Worker that processes new queue."""
    while not shutdown_event.is_set():
        try:
            # BLPOP with timeout for graceful shutdown
            job = await queue_service.dequeue("new-queue", timeout=5)
            if job:
                await process_job(job)
        except Exception as e:
            logger.error(f"Worker error: {e}")
```

2. **Start worker in** `src/main.py` lifespan:
```python
new_worker_task = asyncio.create_task(start_new_worker(shutdown_event))
```

3. **Add queue constant** in `src/shared/constants/queues.py`:
```python
QUEUE_NEW = "eq-pdf:queue:new"
```

4. **Test worker** in `tests/integration/test_new_worker.py` with real Redis

### Adding a Service

1. **Create service** in `src/services/new_service.py`:
```python
class NewService:
    def __init__(self, redis: Redis, s3_client):
        self.redis = redis
        self.s3_client = s3_client
```

2. **Add dependency injection** in `src/dependencies.py`:
```python
async def get_new_service(
    redis: Redis = Depends(get_redis_client),
    s3_client = Depends(get_s3_client)
) -> NewService:
    return NewService(redis=redis, s3_client=s3_client)
```

3. **Write tests** in `tests/unit/services/test_new_service.py`

## S3 Operations Resilience

All S3 operations use exponential backoff retry and circuit breakers to handle transient failures gracefully.

### Retry Behavior

S3 operations automatically retry on transient errors:
- **Retriable errors**: 503 (Service Unavailable), SlowDown, RequestTimeout, InternalError, ThrottlingException
- **Non-retriable errors**: 403 (Forbidden), 404 (NotFound), NoSuchKey, AccessDenied
- **Max attempts**: 3 (with exponential backoff: 1s, 2s, 4s)
- **Boto3 adaptive retry**: Enabled (intelligent client-side rate limiting)

### Circuit Breakers

Circuit breakers prevent cascading failures when S3 is degraded:
- **Failure threshold**: 5 consecutive failures → circuit opens
- **Timeout**: 60 seconds before testing recovery
- **Recovery**: 2 successes in half-open → circuit closes
- **Separate circuits**: Upload, download, and delete operations have independent circuit breakers

**Circuit States:**
- **CLOSED**: Normal operation, requests pass through
- **OPEN**: Service failing, requests blocked immediately (raises `CircuitBreakerOpen`)
- **HALF_OPEN**: Testing recovery, limited requests allowed

### Monitoring S3 Health

View S3 health in Prometheus metrics:

```bash
# Check S3 operation success/failure rates
curl http://localhost:8080/metrics | grep s3_operations_total

# Monitor circuit breaker states (0=closed, 1=half-open, 2=open)
curl http://localhost:8080/metrics | grep s3_circuit_breaker_state

# Track operation latency
curl http://localhost:8080/metrics | grep s3_operation_duration_seconds
```

**Available metrics:**
- `s3_operations_total{operation, bucket, result}` - Total operations (result: success/error/circuit_open)
- `s3_operation_duration_seconds{operation, bucket}` - Operation latency histogram
- `s3_circuit_breaker_state{circuit_name}` - Circuit state (s3-upload, s3-download, s3-delete)
- `s3_retry_attempts_total{operation, attempt}` - Retry attempt counters

### Adding New S3 Operations

When adding new StorageService methods that call S3:

1. **Choose appropriate circuit breaker**:
   - `self.upload_circuit` - For put_object, upload_fileobj
   - `self.download_circuit` - For get_object, head_object, list_objects
   - `self.delete_circuit` - For delete_object, delete_objects

2. **Check circuit before operation**:
   ```python
   self.upload_circuit.check_state()
   if self.upload_circuit.is_open:
       raise CircuitBreakerOpen("S3 upload circuit breaker is open")
   ```

3. **Wrap S3 call with retry**:
   ```python
   try:
       result = await retry_with_backoff_sync(
           lambda: self.s3_client.put_object(...),
           max_attempts=3,
           base_delay=1.0,
           operation_name="upload result"
       )
       self.upload_circuit.record_success()
       return result
   except CircuitBreakerOpen:
       raise
   except Exception:
       self.upload_circuit.record_failure()
       raise
   ```

4. **Add metrics** (optional but recommended):
   ```python
   from ..services.metrics_service import s3_operations_total, s3_operation_duration_seconds
   import time

   start_time = time.time()
   try:
       # ... S3 operation ...
       s3_operations_total.labels(operation='put_object', bucket=bucket, result='success').inc()
       s3_operation_duration_seconds.labels(operation='put_object', bucket=bucket).observe(time.time() - start_time)
   except CircuitBreakerOpen:
       s3_operations_total.labels(operation='put_object', bucket=bucket, result='circuit_open').inc()
       raise
   except Exception:
       s3_operations_total.labels(operation='put_object', bucket=bucket, result='error').inc()
       raise
   ```

### Testing S3 Resilience

**Unit tests**: Mock S3 errors to test retry and circuit breaker logic
```python
from src.utils.circuit_breaker import CircuitBreakerOpen
from botocore.exceptions import ClientError

# Test retry on transient error
mock_s3.put_object.side_effect = [
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    {'ETag': 'success'}  # Succeeds on 3rd attempt
]

# Test circuit breaker opens after failures
for _ in range(5):
    storage.upload_circuit.record_failure()
with pytest.raises(CircuitBreakerOpen):
    await storage.upload_result(job_id, content, 'md')
```

**Integration tests**: Use testcontainers with LocalStack to simulate real S3 throttling

See [tests/integration/services/test_storage_resilience.py](tests/integration/services/test_storage_resilience.py) for examples.

## Package Management

ALL Python development uses `uv`:

```bash
# Add dependency (from inside container)
docker exec -it equalify-pdf-api-gateway uv add <package>

# Add dev dependency
docker exec -it equalify-pdf-api-gateway uv add --dev <package>

# Remove dependency
docker exec -it equalify-pdf-api-gateway uv remove <package>

# Update dependency
docker exec -it equalify-pdf-api-gateway uv add <package>@latest
```

## Debugging

### View Logs
```bash
make logs           # All services
make logs-api       # API only
docker logs equalify-pdf-redis -f
```

### Access Container Shell
```bash
make shell          # API container bash
make redis-cli      # Redis CLI
```

### Check Infrastructure Health
```bash
make health         # Run health checks
curl http://localhost:8080/health
curl http://localhost:8080/metrics
```

### Common Issues

**Tests failing with Redis connection refused:**
- Check Docker is running: `docker ps`
- Restart services: `make down && make dev`
- Verify Redis is healthy: `make redis-cli` then `PING`

**Hot reload not working:**
- Check logs: `make logs-api`
- Verify volume mounts in `docker-compose.dev.yml`
- Restart: `make down && make dev`

**Container not starting:**
- Check logs: `docker logs equalify-pdf-api-gateway`
- Verify `.env` file exists
- Clean and rebuild: `make clean && make build && make dev`

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

## Processing Pipeline Flow

1. **POST /api/documents/submit** (API)
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

3. **Processing Worker** (Background)
   - BLPOP from `eq-pdf:queue:processing`
   - Download PDF from S3
   - Docling: PDF → Markdown
   - AI: Enhance accessibility (alt text, headings, structure)
   - Store Markdown in S3 results bucket
   - Update job: `{status: "completed", markdown_url}`

4. **GET /api/documents/{job_id}/result** (API)
   - Return `{job_id, status, markdown_url, confidence_score}`

## Technology Stack

**Backend:**
- Python 3.11+ (using `uv` package manager)
- FastAPI (async API framework)
- PydanticAI (multi-agent AI framework)
- IBM Docling (PDF to Markdown)
- Microsoft Presidio (PII detection)
- AWS Bedrock (Claude AI via Bedrock)

**Infrastructure:**
- Docker & Docker Compose
- Redis (task queues, caching, rate limiting)
- AWS S3 (object storage)
- AWS ECS (production deployment)
- LocalStack (local AWS emulation)
- Prometheus + Grafana (metrics)

**Testing:**
- pytest (test framework)
- pytest-asyncio (async test support)
- pytest-xdist (parallel test execution)
- pytest-cov (coverage reporting)
- testcontainers (real Redis/S3 for integration tests)

## Context7 Library IDs

For MCP integration, use these library IDs:
- **PydanticAI:** `/pydantic/pydantic-ai`
- **FastAPI:** `/tiangolo/fastapi`
- **LocalStack:** `/localstack/localstack`
- **Boto3:** `/boto/boto3`
- **Microsoft Presidio:** `/microsoft/presidio`
- **Docling:** `/docling-project/docling`
- **Redis:** `/redis/redis-py`
- **Canvas LMS:** `/instructure/canvas-lms`
- **Canvas API:** `/ucfopen/canvasapi`

## Documentation

Key docs in `docs/`:
- [Environment Setup](docs/environment-setup.md) - Complete setup guide with local dev and AWS operations
- [Architecture](docs/architecture.md) - Detailed system design with AWS Bedrock integration
- [CI/CD](docs/ci-cd.md) - Testing strategy, test tiers, and GitHub Actions workflows
- [Contributing](CONTRIBUTING.md) - Development workflow and code standards
- [AWS Guide](docs/aws-guide.md) - AWS deployment and operations

## Project Status

**Current Phase:** Phase 2 - Services & Background Workers (60% complete)
- ✅ Phase 1: Infrastructure foundation complete
- 🚧 Phase 2: API endpoints and workers in progress
- 📋 Phase 3: Frontend (Astro + ShadCN) planned
- 📋 Phase 4: AWS ECS deployment planned

**Test Coverage:** 83.70% (536/536 tests passing)