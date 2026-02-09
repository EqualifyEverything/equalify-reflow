# Equalify Reflow

[![CI](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/ci.yml)

Equalify Reflow frees content trapped inside PDFs. It takes a PDF and produces semantic, reflowable markdown -- content that works on any screen size, with any assistive technology, and with AI tools. Built for the University of Illinois Chicago (UIC) to make course materials work better for everyone.

## What It Is

A server-side pipeline that converts PDFs into semantic markdown using document extraction (IBM Docling) and AI text correction (Claude Haiku via AWS Bedrock). Upload a PDF, get back structured markdown with proper headings, alt text on images, accessible tables, and extracted figures.

## What It Is Not

- Not a client-side tool or browser extension
- Not a PDF viewer or annotation tool
- Not a general-purpose document editor
- Not a real-time converter -- processing takes ~5 minutes per document depending on length and complexity

## How It Works

```
PDF uploaded via API
         |
         v
PII scan (Microsoft Presidio)
  Pass: queue for processing
  Fail: await instructor approval
         |
         v
Versioned pipeline (7 steps):
  1. Docling extraction (v0) -- PDF → markdown + page images
  2. Structure analysis -- AI identifies headings, footnotes, page types
  3. Heading level fix -- normalize heading hierarchy
  4. Page content corrections (v1) -- AI fixes OCR errors per-page
  5. Code block tagging -- identify programming languages
  6. Cross-page boundary fixes (v2) -- rejoin split content, relocate footnotes
  7. Final cleanup (v3) -- normalize whitespace and formatting
         |
         v
Semantic markdown + extracted figures stored in S3
         |
         v
Results available via API or pipeline viewer UI
```

## What's Implemented

| Feature | Status |
|---|---|
| **Versioned processing pipeline** -- Docling extraction, AI structure analysis, page corrections, boundary fixes | Complete |
| **REST API** -- Submit documents, poll status, stream events (SSE), retrieve results | Complete |
| **PII detection** -- Microsoft Presidio scans all documents before AI processing | Complete |
| **Approval workflow** -- Token-based PII approval with configurable timeouts | Complete |
| **S3 storage** -- Upload/download with circuit breakers and retry logic | Complete |
| **Redis job management** -- Job state, queuing, rate limiting, event bus | Complete |
| **Authentication** -- API key auth, protected Swagger docs | Complete |
| **Monitoring** -- Prometheus metrics, Grafana dashboards, Jaeger tracing | Complete |
| **Pipeline viewer** -- React UI for upload, step-by-step review, version diff comparison | Complete |
| **Testing** -- 1133 tests (unit, integration, E2E) | Complete |
| **AWS deployment** -- ECS Fargate, Terraform, CloudWatch, budget alerts | Complete |

## Quick Start

### Prerequisites

- Docker (v20.10+)
- Docker Compose (v2.0+)

### Get Running

```bash
# Start all services
make dev

# Verify
curl http://localhost:8080/health

# View API docs (username: dase, password: a11y)
open http://localhost:8080/docs
```

The API runs at http://localhost:8080 with hot reload enabled. Edit code in `src/` and changes reload automatically inside the container.

### Essential Commands

```bash
make dev               # Start development environment
make down              # Stop all services
make logs-api          # View API logs
make test-fast         # Unit tests (<30s) -- run before commits
make test-integration  # Integration tests (<2min) -- run before PRs
make shell             # Access container bash
make health            # Verify infrastructure
```

Run `make help` for all commands.

## Project Structure

```
src/
├── main.py                 # FastAPI app entry point
├── config.py               # Settings from environment variables
├── dependencies.py         # Dependency injection
├── api/                    # REST endpoints (documents, approval, pipeline, health)
├── agents/                 # AI prompt modules (structure, boundary, footnote)
├── services/               # Business logic (20 services)
│   ├── pipeline_viewer.py             # Core versioned processing pipeline
│   ├── document_processing_service.py # Pipeline orchestration + S3/Redis
│   ├── storage_service.py             # S3 with circuit breakers
│   ├── job_service.py                 # Redis job state (Lua scripts)
│   ├── queue_service.py               # Redis queues
│   └── pii_service.py                 # Presidio PII detection
├── workers/                # Background tasks (PII scan, timeout checks)
├── middleware/              # Auth, logging, rate limiting, metrics, CORS
├── shared/                 # Constants and shared utilities
└── utils/                  # Retry logic, circuit breakers, tokens

clients/viewer/             # React pipeline viewer (Vite + TypeScript + Tailwind)
tests/                      # Unit, integration, and E2E tests
infrastructure/             # Prometheus, Grafana, LocalStack configs
docs/                       # Architecture, guides
```

## Technology Stack

**Backend:** Python 3.11+, FastAPI, PydanticAI, IBM Docling, Microsoft Presidio, AWS Bedrock (Claude Haiku)

**Infrastructure:** Docker, Redis, AWS S3, AWS ECS Fargate, LocalStack, Terraform

**Monitoring:** Prometheus, Grafana, Jaeger (OpenTelemetry), CloudWatch

**Pipeline Viewer:** React 18, TypeScript, Vite, ShadCN/Radix, Tailwind CSS

**Testing:** pytest, pytest-asyncio, pytest-xdist, pytest-cov, testcontainers

## Services (Development)

| Service | Port | Purpose |
|---|---|---|
| API Gateway | http://localhost:8080 | Main application, API docs, pipeline viewer |
| Redis | localhost:6379 | Job state, queues, rate limiting |
| LocalStack | localhost:4566 | S3 and CloudWatch emulation |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3001 | Metrics dashboards (admin/admin) |
| Jaeger | http://localhost:16686 | Distributed tracing |

## Documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/architecture.md) | System design, data flows, service layer |
| [Environment Setup](docs/environment-setup.md) | Complete setup guide |
| [Testing](docs/ci-cd.md) | Test tiers, CI/CD, GitHub Actions |
| [AWS Guide](docs/aws-guide.md) | Deployment, operations, cost protection |
| [Contributing](CONTRIBUTING.md) | Development workflow and standards |
| [CLAUDE.md](CLAUDE.md) | Instructions for AI-assisted development |
