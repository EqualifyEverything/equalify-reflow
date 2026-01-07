# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The Equalify PDF Converter transforms PDF documents into accessible, semantic markup for University of Illinois Chicago (UIC). System processes **course materials only** - strict architectural boundary against student records or PII.

## Essential Commands

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `make dev` | Start all services | Default for development |
| `make down` | Stop all services | End of session |
| `make test-fast` | Run unit tests (~30s) | Before commits |
| `make test-integration` | Run integration tests (~2min) | Before PRs |
| `make test-e2e` | Run E2E tests (~5min) | Before merges |
| `make logs` / `make logs-api` | View service logs | Debugging |
| `make shell` | Access container bash | Interactive debugging |
| `make redis-cli` | Connect to Redis CLI | Redis operations |
| `make health` | Verify infrastructure | Health checks |
| `make coverage` | Run tests with coverage | Coverage reports |

## Never Do These

- ❌ DO NOT run `uv run uvicorn` directly on host
- ❌ DO NOT install dependencies locally with `uv sync`
- ❌ DO NOT use `localhost:6379` in code (use `redis:6379`)
- ❌ DO NOT run `python` or `pytest` directly on host

## Default Ports

**API Gateway:** `http://localhost:8080`
- FastAPI docs: `http://localhost:8080/docs`
- Demo UI: `http://localhost:8080/demo`
- V5 Viewer: `http://localhost:8080/viewer`

**Other Services:**
- Redis: `localhost:6379`
- LocalStack: `localhost:4566`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`
- Jaeger: `http://localhost:16686`

## Quick Architecture

**Pattern:** Monolith with Inline Agentic Pipeline

```
├── API Layer (FastAPI) - All endpoints prefixed with /api/v1/
│   ├── POST /api/v1/documents/submit     → PII scan + inline processing
│   ├── GET /api/v1/documents/{job_id}    → Job status + results
│   ├── GET /api/v1/documents/{job_id}/stream → SSE event stream
│   ├── GET /api/v1/documents/{job_id}/ledger → Change ledger for review
│   └── POST /api/v1/approval/{token}/decision → PII approval
│
├── Workers (Background threads)
│   ├── PII Worker         → Microsoft Presidio PII detection
│   └── Timeout Worker     → Approval timeout checks
│
├── Services (Business logic)
│   ├── StorageService     → S3 upload/download (circuit breakers)
│   ├── S3URLService       → URL generation (LocalStack vs AWS)
│   ├── S3CleanupService   → File cleanup (best-effort, no circuit breakers)
│   ├── QueueService       → Redis queue operations
│   ├── JobService         → Redis job state management
│   ├── PIIDetectionService → Presidio-based PII scanning
│   ├── DocumentProcessingService → Inline agentic pipeline orchestration
│   └── AssemblyService    → Correction application and confidence scoring
│
└── Infrastructure
    ├── Redis              → Job state, rate limiting, event bus
    ├── LocalStack (dev)   → S3 + CloudWatch emulation
    └── AWS Bedrock        → Claude models for extraction and analysis
```

**Data Flow:**

1. PDF uploaded → S3 temp bucket
2. PII scan (Presidio) → Pass: queue processing | Fail: await approval
3. Processing (Docling + Bedrock) → Markdown + confidence score
4. Correction approval → Final markdown to results bucket

## Detailed Documentation

- [Architecture](.claude/docs/architecture.md) - System design, data flow, service layer, AWS Bedrock setup
- [Authentication](.claude/docs/authentication.md) - API key auth, docs auth, middleware stack
- [Testing](.claude/docs/testing.md) - 3-tier strategy, fixtures, markers, running tests
- [S3 Resilience](.claude/docs/s3-resilience.md) - Circuit breakers, retry logic, metrics
- [Development](.claude/docs/development.md) - Adding features, debugging, common issues

## Development Workflow

1. `make dev` - Starts all services in Docker
2. Edit code in `src/` on host machine
3. Code auto-reloads in container (hot reload enabled)
4. `make test-fast` - Quick feedback before commit
5. `make shell` - Debug inside container if needed

## Project Structure

```
src/
├── main.py                    # FastAPI app + worker startup
├── config.py                  # Settings (from env vars)
├── dependencies.py            # Dependency injection
├── api/                       # REST API endpoints
├── workers/                   # Background task processors
├── services/                  # Business logic (storage, queue, job, PII, processing)
├── middleware/                # Auth, logging, rate limiting, CORS, metrics
├── agents/                    # PydanticAI agents (text correction)
├── shared/                    # Pydantic models and constants
└── utils/                     # Helpers (retry, circuit breaker, tokens)
```

## Technology Stack

**Backend:** Python 3.11+, FastAPI, PydanticAI, IBM Docling, Microsoft Presidio, AWS Bedrock
**Infrastructure:** Docker, Redis, AWS S3, AWS ECS, LocalStack, Prometheus + Grafana
**Testing:** pytest, pytest-asyncio, pytest-xdist, pytest-cov, testcontainers

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

## Additional Documentation

For broader project documentation in `docs/`:

- [Environment Setup](docs/environment-setup.md) - Complete setup guide
- [CI/CD](docs/ci-cd.md) - GitHub Actions workflows
- [Contributing](CONTRIBUTING.md) - Development workflow and code standards
- [AWS Guide](docs/aws-guide.md) - AWS deployment and operations
