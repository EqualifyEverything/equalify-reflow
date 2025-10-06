# Equalify PDF Converter - Architecture Overview

## Architecture Pattern: Monolith with Background Task Queue

This is a **single Python application** (monolith) that uses **Redis task queues** for asynchronous background processing.

## Application Structure

```
src/
├── main.py                    # FastAPI app + worker startup
├── config.py                  # Shared configuration
├── dependencies.py            # Dependency injection
│
├── api/                       # REST API endpoints
│   ├── documents.py           # POST /submit, GET /status, GET /result
│   └── health.py              # GET /health
│
├── workers/                   # Background task processors
│   ├── pii_worker.py          # Monitors eq-pdf:queue:pii
│   ├── processing_worker.py   # Monitors eq-pdf:queue:processing
│   └── timeout_worker.py      # Scheduled approval timeout checks
│
├── services/                  # Shared business logic
│   ├── storage_service.py     # S3 operations
│   ├── queue_service.py       # Redis queue operations
│   ├── job_service.py         # Job state management
│   ├── pii_service.py         # Microsoft Presidio integration
│   └── processing_service.py  # AI pipeline orchestration
│
├── shared/                    # Data models and constants
│   ├── models/                # Pydantic models
│   └── constants/             # Queue names, Redis keys
│
└── middleware/                # FastAPI middleware
    ├── error_handler.py
    └── logging_middleware.py
```

## Request Flow Example

### Document Submission Flow:

```
1. Client → POST /api/documents/submit (PDF file)
   ↓
2. FastAPI endpoint (api/documents.py)
   • Validate PDF format
   • Generate job_id (UUID)
   • Store PDF in S3 temp bucket
   ↓
3. Job Service (services/job_service.py)
   • Create job in Redis: eq-pdf:job:{job_id}
   • Set status: "pii_scanning"
   ↓
4. Queue Service (services/queue_service.py)
   • Push to Redis list: LPUSH eq-pdf:queue:pii
   ↓
5. Return to client immediately (<100ms)
   • Response: {job_id, status: "pii_scanning"}

───────────────────────────────────────────────────

6. PII Worker (workers/pii_worker.py) - Background Thread
   • BLPOP eq-pdf:queue:pii (blocking wait)
   • Download PDF from S3
   • Run Microsoft Presidio scan
   ↓
7. PII Service Decision:
   • If clean → LPUSH eq-pdf:queue:processing
   • If PII found → Set status "awaiting_approval"
   ↓
8. Processing Worker (workers/processing_worker.py) - Background Thread
   • BLPOP eq-pdf:queue:processing
   • Download PDF from S3
   • Docling: PDF → Markdown
   • AI: Markdown → Accessible MDX
   • Store results in S3
   • Update job status: "completed"

───────────────────────────────────────────────────

9. Client → GET /api/documents/{job_id}/result
   ↓
10. FastAPI endpoint returns:
    • {status: "completed", html_url: "...", mdx_url: "..."}
```

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT APPLICATION                        │
└──────┬──────────────────────────────────────────────────────┘
       │
       │ POST /api/documents/submit (PDF)
       ▼
┌─────────────────────────────────────────────────────────────┐
│                  FASTAPI REST API                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  1. Validate PDF                                     │   │
│  │  2. Store in S3: s3://equalify-pdf-temp/{job_id}.pdf │   │
│  │  3. Create job: HSET eq-pdf:job:{job_id}            │   │
│  │  4. Queue: LPUSH eq-pdf:queue:pii                    │   │
│  │  5. Return: {job_id, status: "pii_scanning"}        │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
       │
       │ (Async via Redis)
       ▼
┌─────────────────────────────────────────────────────────────┐
│              BACKGROUND PII WORKER                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  1. BLPOP eq-pdf:queue:pii (blocking wait)           │   │
│  │  2. Download PDF from S3                             │   │
│  │  3. Microsoft Presidio scan                          │   │
│  │  4a. If clean → LPUSH eq-pdf:queue:processing        │   │
│  │  4b. If PII → HSET job status "awaiting_approval"   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
       │
       │ (Async via Redis)
       ▼
┌─────────────────────────────────────────────────────────────┐
│         BACKGROUND PROCESSING WORKER                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  1. BLPOP eq-pdf:queue:processing                    │   │
│  │  2. Download PDF from S3                             │   │
│  │  3. Docling: PDF → Markdown                          │   │
│  │  4. AI Pipeline: Markdown → Accessible MDX           │   │
│  │  5. Store: s3://equalify-pdf-results/{job_id}/       │   │
│  │  6. HSET job status "completed", html_url, mdx_url   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
       │
       │ GET /api/documents/{job_id}/result
       ▼
┌─────────────────────────────────────────────────────────────┐
│                  CLIENT RETRIEVES RESULT                     │
│  Response: {                                                 │
│    job_id, status: "completed",                              │
│    html_url: "https://s3.../result.html",                    │
│    mdx_url: "https://s3.../result.mdx"                       │
│  }                                                           │
└─────────────────────────────────────────────────────────────┘
```

## Infrastructure Components

### Docker Compose Services (Infrastructure Only):
```yaml
services:
  redis:
    # Task queue and cache
    # NOT a separate microservice - just shared infrastructure

  localstack:  # Development only
    # Local S3 emulation
```

### Python Application (Run via uv):
```bash
# Start infrastructure
docker-compose up -d

# Run the monolith application
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

The application `src/main.py` starts:
1. FastAPI server (main thread)
2. PII worker thread
3. Processing worker thread
4. Timeout scheduler thread

## Redis Data Structures

### Task Queues (Redis Lists):
```python
# Job enters PII scanning
LPUSH eq-pdf:queue:pii '{"job_id": "abc123", "s3_key": "temp/abc123.pdf"}'

# Worker gets next job (blocking)
job = BLPOP eq-pdf:queue:pii 60  # Wait up to 60 seconds

# Job enters processing
LPUSH eq-pdf:queue:processing '{"job_id": "abc123", "s3_key": "temp/abc123.pdf"}'
```

### Job State (Redis Hash):
```python
# Create job
HSET eq-pdf:job:abc123
  status "pii_scanning"
  created_at "2025-01-01T12:00:00Z"
  s3_key "temp/abc123.pdf"

# Update after PII scan
HSET eq-pdf:job:abc123
  status "processing"
  updated_at "2025-01-01T12:01:00Z"

# Complete job
HSET eq-pdf:job:abc123
  status "completed"
  html_url "https://s3.../result.html"
  confidence_score "0.87"
```

### Timeout Tracking (Redis Sorted Set):
```python
# Add approval deadline (score = Unix timestamp)
ZADD eq-pdf:timeouts:approval 1704124800 "abc123"

# Get expired approvals
expired = ZRANGEBYSCORE eq-pdf:timeouts:approval 0 {current_timestamp}
```

## Project Structure

```
equalify-pdf-converter/
├── docker-compose.yml              # Base service definitions
├── docker-compose.dev.yml          # Development overrides (LocalStack)
├── docker-compose.prod.yml         # Production overrides
├── Dockerfile                      # Application container
├── Makefile                        # Development commands
├── .env.example                    # Environment template
├── .env.dev                        # Development configuration
├── .env.prod                       # Production configuration
│
├── docs/                           # Documentation
│   ├── architecture.md             # This file
│   ├── infrastructure-setup.md     # Setup guide
│   ├── ci-cd.md                    # CI/CD and testing
│   ├── project-status.md           # Current status and phases
│   └── testing-strategy.md         # Test organization
│
├── infrastructure/                 # Infrastructure configuration
│   ├── localstack/                 # LocalStack init scripts
│   │   ├── init-aws.sh             # S3 bucket creation
│   │   └── README.md               # LocalStack documentation
│   └── redis/                      # Redis configuration
│       ├── redis.conf              # Redis server config
│       └── README.md               # Redis documentation
│
├── scripts/                        # Utility scripts
│   ├── setup-aws.sh                # AWS resource initialization
│   ├── health-check.sh             # Infrastructure validation
│   └── README.md                   # Scripts documentation
│
├── src/                            # Source code (monolith)
│   ├── main.py                     # FastAPI app + worker startup
│   ├── config.py                   # Shared configuration
│   ├── dependencies.py             # Dependency injection
│   │
│   ├── api/                        # REST API endpoints
│   │   ├── documents.py            # POST /submit, GET /status, GET /result
│   │   └── health.py               # GET /health
│   │
│   ├── workers/                    # Background task processors
│   │   ├── pii_worker.py           # Monitors eq-pdf:queue:pii
│   │   ├── processing_worker.py    # Monitors eq-pdf:queue:processing
│   │   └── timeout_worker.py       # Scheduled approval timeout checks
│   │
│   ├── services/                   # Shared business logic
│   │   ├── storage_service.py      # S3 operations
│   │   ├── queue_service.py        # Redis queue operations
│   │   ├── job_service.py          # Job state management
│   │   ├── pii_service.py          # Microsoft Presidio integration
│   │   └── processing_service.py   # AI pipeline orchestration
│   │
│   ├── shared/                     # Data models and constants
│   │   ├── models/                 # Pydantic models
│   │   │   ├── job.py              # Job model
│   │   │   └── queue.py            # Queue message models
│   │   └── constants/              # Queue names, Redis keys
│   │       ├── queues.py           # Queue name constants
│   │       └── redis_keys.py       # Redis key patterns
│   │
│   └── middleware/                 # FastAPI middleware
│       ├── error_handler.py        # Global error handling
│       └── logging_middleware.py   # Request/response logging
│
└── tests/                          # Test suite (tiered)
    ├── unit/                       # Fast unit tests (<2min)
    │   ├── services/               # Service layer tests
    │   ├── models/                 # Model tests
    │   └── api/                    # API endpoint tests
    │
    ├── integration/                # Integration tests (~5min)
    │   ├── redis/                  # Redis integration tests
    │   ├── s3/                     # S3 integration tests
    │   └── services/               # Service integration tests
    │
    └── e2e/                        # End-to-end tests (~10min)
        ├── workflows/              # Complete workflow tests
        └── api/                    # API endpoint E2E tests
```

## Environment Configuration

### Development (.env.dev)

```bash
# LocalStack for AWS services
AWS_ENDPOINT_URL=http://localstack:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1

# Local Redis
REDIS_URL=redis://redis:6379

# S3 Buckets
S3_TEMP_BUCKET=equalify-pdf-temp
S3_RESULTS_BUCKET=equalify-pdf-results

# Application
LOG_LEVEL=DEBUG
ENVIRONMENT=development

# AI Processing (Phase 2)
# OPENAI_API_KEY=your-key-here

# Canvas Integration (Phase 3)
# CANVAS_API_URL=https://canvas.example.edu
# CANVAS_API_KEY=your-key-here
```

### Production (.env.prod)

```bash
# Real AWS services (no endpoint URL)
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_DEFAULT_REGION=us-east-1

# AWS ElastiCache
REDIS_URL=${REDIS_URL}

# S3 Buckets
S3_TEMP_BUCKET=equalify-pdf-temp-prod
S3_RESULTS_BUCKET=equalify-pdf-results-prod

# Application
LOG_LEVEL=INFO
ENVIRONMENT=production

# AI Processing
OPENAI_API_KEY=${OPENAI_API_KEY}

# Canvas Integration
CANVAS_API_URL=${CANVAS_API_URL}
CANVAS_API_KEY=${CANVAS_API_KEY}
```

## Deployment

### Development:
```bash
# 1. Start all services (recommended)
make dev

# 2. Verify infrastructure
make health

# 3. View logs
make logs-api

# Alternative: Manual startup
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### Production (AWS ECS):
```bash
# 1. Build Docker image with application
docker build -t equalify-pdf-converter .

# 2. Push to ECR
docker push <ecr-repo>/equalify-pdf-converter:latest

# 3. Deploy to ECS Fargate
# - Single task definition with API + workers
# - Environment variables for Redis (ElastiCache) and S3
# - Health check on /health endpoint
```

See [Infrastructure Setup Guide](infrastructure-setup.md) for detailed deployment instructions.
