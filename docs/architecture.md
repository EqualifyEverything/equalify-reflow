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

## Deployment

### Development:
```bash
# 1. Start infrastructure (Redis, LocalStack)
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# 2. Run application locally
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
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
