# Equalify Reflow

[![CI](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/ci.yml/badge.svg)](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/ci.yml)

Equalify Reflow frees content trapped inside PDFs. It takes a PDF and produces semantic, reflowable markdown -- content that works on any screen size, with any assistive technology, and with AI tools. Built for the University of Illinois Chicago (UIC) to make course materials work better for everyone.

## What It Is

A server-side pipeline that converts PDFs into semantic markdown using document extraction (IBM Docling) and AI text correction agents (Claude via AWS Bedrock). Upload a PDF, get back structured markdown with proper headings, alt text on images, accessible tables, and extracted figures.

## What It Is Not

- Not a client-side tool or browser extension
- Not a PDF viewer or annotation tool
- Not a general-purpose document editor
- Not a real-time converter -- processing takes ~5 minutes per document depending on length and complexity

## How It Works

```
PDF uploaded via API or Canvas LTI
         |
         v
PII scan (Microsoft Presidio)
  Pass: queue for processing
  Fail: await instructor approval
         |
         v
Docling extracts document structure (headings, tables, figures, text)
         |
         v
AI agents correct and enrich the text (Claude Haiku + Sonnet via Bedrock)
  - Multi-phase pipeline: Plan → Execute → Verify → Recover
  - Parallel page processing with confidence scoring
         |
         v
Semantic markdown + extracted figures stored in S3
         |
         v
Results available via API, SSE streaming, or Canvas Page
```

## What's Implemented

| Feature | Status |
|---|---|
| **PDF processing pipeline** -- Docling extraction, AI text correction, confidence scoring | Complete |
| **REST API** -- Submit documents, poll status, stream events (SSE), retrieve results | Complete |
| **PII detection** -- Microsoft Presidio scans all documents before AI processing | Complete |
| **Approval workflow** -- Token-based PII approval with configurable timeouts | Complete |
| **S3 storage** -- Upload/download with circuit breakers and retry logic | Complete |
| **Redis job management** -- Job state, queuing, rate limiting, event bus | Complete |
| **Authentication** -- API key auth, protected Swagger docs | Complete |
| **Monitoring** -- Prometheus metrics, Grafana dashboards, Jaeger tracing | Complete |
| **Pipeline viewer** -- React UI for upload, status tracking, PII review, diff view | Complete |
| **Testing** -- 984 tests, 83.7% coverage (unit, integration, E2E) | Complete |
| **AWS deployment** -- ECS Fargate, Terraform, CloudWatch, budget alerts | Complete |
| **Canvas LTI 1.3** -- OIDC auth, file menu launch, file download from Canvas | Complete |

## What's In Progress

| Feature | Status |
|---|---|
| **Canvas auto-publishing** -- Automatically convert PDFs to Canvas Pages when uploaded | Design phase ([proposal](docs/features/canvas-auto-publish.md)) |
| **Markdown-to-HTML renderer** -- Convert pipeline output to Canvas-compatible semantic HTML | Not started |
| **Canvas Publisher** -- Upload images to Canvas Files, create Pages, link in Modules | Not started |
| **File discovery worker** -- Poll Canvas for new PDFs in enabled courses | Not started |
| **Instructor dashboard** -- Server-rendered course management UI (Jinja2 + Tailwind) | Not started |
| **Markdown download bundle** -- Zip of markdown + image assets for student download | Not started |

See [Canvas Auto-Publish Proposal](docs/features/canvas-auto-publish.md) for the full design.

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
├── api/                    # REST endpoints (documents, approval, health)
├── agents/                 # AI pipeline (16 modules, multi-phase orchestration)
├── services/               # Business logic (24 services)
│   ├── document_processing_service.py  # Pipeline orchestration
│   ├── storage_service.py              # S3 with circuit breakers
│   ├── job_service.py                  # Redis job state
│   ├── queue_service.py                # Redis queues
│   └── pii_service.py                  # Presidio PII detection
├── workers/                # Background tasks (PII scan, timeout checks)
├── middleware/              # Auth, logging, rate limiting, metrics, CORS
├── lti/                    # Canvas LTI 1.3 integration
├── shared/                 # Pydantic models
└── utils/                  # Retry logic, circuit breakers, tokens

frontend/demo-ui/           # React pipeline viewer (Vite + TypeScript + Tailwind)
tests/                      # Unit, integration, and E2E tests
infrastructure/             # Prometheus, Grafana, LocalStack configs
docs/                       # Architecture, guides, proposals
```

## Technology Stack

**Backend:** Python 3.11+, FastAPI, PydanticAI, IBM Docling, Microsoft Presidio, AWS Bedrock (Claude Haiku + Sonnet)

**Infrastructure:** Docker, Redis, AWS S3, AWS ECS Fargate, LocalStack, Terraform

**Monitoring:** Prometheus, Grafana, Jaeger (OpenTelemetry), CloudWatch

**Canvas Integration:** LTI 1.3 (pylti1p3), Canvas REST API

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
| [Canvas Auto-Publish Proposal](docs/features/canvas-auto-publish.md) | Design for automatic PDF-to-Canvas Page publishing |
| [Canvas LTI Setup](.claude/docs/canvas-lti-setup.md) | Local Canvas + LTI 1.3 development setup |
| [Architecture](docs/architecture.md) | System design, data flows, service layer |
| [Environment Setup](docs/environment-setup.md) | Complete setup guide |
| [Testing](docs/ci-cd.md) | Test tiers, CI/CD, GitHub Actions |
| [AWS Guide](docs/aws-guide.md) | Deployment, operations, cost protection |
| [Contributing](CONTRIBUTING.md) | Development workflow and standards |
| [CLAUDE.md](CLAUDE.md) | Instructions for AI-assisted development |
