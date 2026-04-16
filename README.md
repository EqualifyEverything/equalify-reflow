# Equalify Reflow

[![CI](https://github.com/EqualifyEverything/equalify-reflow/actions/workflows/ci.yml/badge.svg)](https://github.com/EqualifyEverything/equalify-reflow/actions/workflows/ci.yml)
[![Licence: AGPL-3.0-or-later](https://img.shields.io/badge/licence-AGPL--3.0--or--later-blue.svg)](https://www.gnu.org/licenses/agpl-3.0.html)

Equalify Reflow is an open source project that frees content trapped inside PDFs. It takes a PDF and produces semantic, reflowable markdown — content that works on any screen size, with any assistive technology, and with AI tools. Originally built with the University of Illinois Chicago (UIC) to make course materials work better for everyone, the project is now available for any organisation that needs accessible document conversion.

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
Versioned pipeline (5 phases):
  1. Extraction  -- PDF → markdown + page images (Docling, with OCR fallback for scanned PDFs)
  2. Analysis    -- AI classifies pages and identifies headings, footnotes, code blocks
  3. Headings    -- AI reconciles heading hierarchy and normalises levels
  4. Translation -- AI fixes per-page content and tags code block languages
  5. Assembly    -- Cross-page boundary fixes and final cleanup
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
| **Testing** -- Three-tier suite (unit, integration, E2E) with coverage reports in CI | Complete |

## Quick Start

### Prerequisites

- Docker (v20.10+)
- Docker Compose (v2.0+)
- An Anthropic API key — get one at [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys). Alternatively, bring your own AWS credentials with Bedrock access.

### Get Running

```bash
# 1. Copy the env template and add your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 2. Start all services
make dev

# 3. Verify
curl http://localhost:8080/health

# 4. View API docs (publicly accessible)
open http://localhost:8080/docs
```

The API runs at http://localhost:8080 with hot reload enabled. Edit code in `src/` and changes reload automatically inside the container.

The pipeline's AI backend auto-detects from your environment: if `ANTHROPIC_API_KEY` is set, it uses Anthropic direct; otherwise it falls back to AWS Bedrock. Set `AI_PROVIDER=anthropic` or `AI_PROVIDER=bedrock` to force a specific backend. See [`.env.example`](.env.example) for the full list of settings.

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

## API Documentation

The FastAPI app exposes interactive API documentation at `/docs` (Swagger UI) and `/redoc` (ReDoc).

- **Local development:** http://localhost:8080/docs (public — no auth)
- **OpenAPI schema:** http://localhost:8080/openapi.json (public)
- **Endpoints overview:** all application endpoints are prefixed with `/api/v1/`. See the Swagger UI for the complete list, request/response shapes, and authentication requirements.

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
infrastructure/             # Prometheus, Grafana, Floci configs
docs/                       # Architecture, guides
```

## Technology Stack

**Backend:** Python 3.11+, FastAPI, PydanticAI, IBM Docling, Microsoft Presidio. AI model backend is pluggable — currently AWS Bedrock (Claude Haiku); Anthropic direct and other providers planned.

**Infrastructure (local dev):** Docker, Redis, AWS S3 (via [Floci](https://github.com/floci-io/floci) — a lightweight MIT-licensed emulator), Prometheus + Grafana

**Monitoring:** Prometheus, Grafana, Jaeger (OpenTelemetry)

**Pipeline Viewer:** React 18, TypeScript, Vite, ShadCN/Radix, Tailwind CSS

**Testing:** pytest, pytest-asyncio, pytest-xdist, pytest-cov, testcontainers

## Services (Development)

| Service | Port | Purpose |
|---|---|---|
| API Gateway | http://localhost:8080 | Main application, API docs, pipeline viewer |
| Redis | localhost:6379 | Job state, queues, rate limiting |
| Floci | localhost:4566 | S3 + CloudWatch emulation (replaces LocalStack) |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3001 | Metrics dashboards (admin/admin) |
| Jaeger | http://localhost:16686 | Distributed tracing |

## Documentation

Docs live under [`docs/`](docs/), organised in [Diátaxis](https://diataxis.fr/) modes:

| Mode | What's there |
|---|---|
| [Tutorials](docs/tutorials/) | Guided walkthroughs for first-time contributors |
| [How-to](docs/how-to/) | Task recipes: [set up dev environment](docs/how-to/set-up-dev-environment.md), [run tests](docs/how-to/run-tests.md), [iterate on a prompt](docs/how-to/iterate-on-a-prompt.md), [add a new agent](docs/how-to/add-a-new-agent.md), [add S3 operations](docs/how-to/add-s3-operations.md), [test rate limits](docs/how-to/test-rate-limits.md), [debug a CI failure](docs/how-to/debug-ci-failures.md), [add features](docs/how-to/add-features.md) |
| [Reference](docs/reference/) | Authoritative lookups: [pipeline phases](docs/reference/pipeline-phases.md), [model tiers](docs/reference/model-tiers.md), [authentication](docs/reference/authentication.md), [rate limits](docs/reference/rate-limits.md), [CI workflows](docs/reference/ci-workflows.md) |
| [Explanation](docs/explanation/) | Design rationale: [architecture](docs/explanation/architecture.md), [authentication design](docs/explanation/authentication-design.md), [rate limiting](docs/explanation/rate-limiting.md), [S3 resilience](docs/explanation/s3-resilience.md), [testing strategy](docs/explanation/testing-strategy.md) |

Also:

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to submit a change
- [AGENTS.md](AGENTS.md) — orientation file for AI and human agents working on the repo (symlinked as `CLAUDE.md`)
- **API reference** — runtime Swagger at `http://localhost:8080/docs` locally, or `https://reflow.equalify.uic.edu/docs` for the deployed instance
