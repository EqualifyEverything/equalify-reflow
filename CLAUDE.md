# Equalify PDF Converter

## Project Overview

The Equalify PDF Converter transforms PDF documents into accessible, semantic HTML for University of Illinois Chicago (UIC). While PDFs are ubiquitous for sharing and offline viewing, they're problematic for accessibility, mobile responsiveness, and AI integration.

## Primary Use Case
University of Illinois Chicago (UIC) accessibility enhancement for course materials. System processes **course materials only** - strict architectural boundary against student records or PII.

## Core Architecture

**Pattern:** Monolith with Background Task Queue (single Python application, runs in Docker)

The application runs as **one unified codebase** with:
- **FastAPI REST API** - HTTP endpoints for document submission, status, results
- **Background Workers** - Async task processors monitoring Redis queues
- **Redis Task Queues** - Decouples API responses from long-running processing
- **Shared Services** - S3 storage, job management, queue operations

**Infrastructure:** AWS ECS with Fargate containers, Redis task queues, S3 static hosting
**Processing:** AI pipeline (Phase 1: single agent, Phase 2+: multi-agent) with semantic caching
**Frontend:** Astro application with accessible ShadCN/Radix components (Phase 3)
**Security:** Microsoft Presidio PII scanning, encrypted storage/transmission, ephemeral processing

### ⚠️ CRITICAL: Development Workflow

This project uses a **fully containerized development workflow**. You MUST work inside Docker containers.

#### Development Commands (USE THESE BY DEFAULT):
```bash
make dev          # Start all services (Redis, LocalStack, API in containers)
make logs-api     # View API logs
make shell        # Access container shell for debugging
make test-docker  # Run tests inside container
make down         # Stop all services
```

#### Never Do These:
- ❌ DO NOT run `uv run uvicorn` directly on host
- ❌ DO NOT install dependencies locally with `uv sync`
- ❌ DO NOT try to connect to `localhost:6379` from host code
- ❌ DO NOT run `python` or `pytest` directly on host

#### Why Containerized Development?
- **Unified Networking**: All services communicate via Docker DNS (redis:6379, localstack:4566)
- **Hot Reload**: Code changes auto-reload without rebuild
- **Environment Parity**: Dev matches production exactly
- **No Local Setup**: No need to install Redis, LocalStack, or Python dependencies on host

#### How to Work:
1. `make dev` - Starts everything
2. Edit code in `src/` on your host machine
3. Changes auto-reload in container
4. `make shell` - Debug inside container if needed
5. `make test-docker` - Run tests in container

See [docs/infrastructure-setup.md](docs/infrastructure-setup.md) for details.

## Processing Pipeline
1. **REST API** receives PDF → Store in S3 temp → Queue for PII scanning
2. **PII Worker** (background) → Microsoft Presidio scan → Queue for processing (or approval)
3. **Processing Worker** (background) → Docling PDF→Markdown → AI accessibility enhancement
4. AI processing adds contextual alt texts, fixes heading hierarchy, converts math to MathML
5. Generate semantic MDX → Render to accessible HTML
6. Store versioned results in S3 → API returns URLs

**Note:** All workers run as background threads/processes within the single Python application, not separate microservices.

## Required Integrations
- **Equalify Platform:** Webhook-triggered processing from accessibility scans
- **Canvas LMS:** External URL module items with responsive design
- **AWS ECS:** Infrastructure deployment requirement

## Success Criteria
- WCAG 2.1 AA compliance validation
- Processing cost: ~$0.20 per document target
- Processing time: 2-8 minutes for typical documents
- Structure accuracy: ≥90% proper heading hierarchy preservation
- Faculty review time: ≤10 minutes for 10-page document

## Quality Assurance Architecture
- **Confidence Scoring:** Documents flagged as High (>85%), Medium (60-85%), or Low (<60%) confidence
- **Faculty Review Interface:** Natural language correction workflow with transparent AI reasoning
- **Semantic Caching:** AI decision storage for transparency and future improvements

## Phase 1 Processing Limitations
- Documents >40 pages: Limited optimization
- Mathematical content: Complex LaTeX equations flagged for manual review
- Advanced tables: Merged cells and complex relationships require intervention
- OCR-only content: Poor quality scanned documents have degraded confidence
- Scientific figures: Complex accessible alternatives need manual validation

## Technical Specifications

**Package Management:** ALL Python development uses `uv` for dependency management and virtual environments

**Context7 Library IDs for MCP Integration:**
- **PydanticAI:** `/pydantic/pydantic-ai` (Multi-agent AI framework)
- **FastAPI:** `/tiangolo/fastapi` (Async API framework for Equalify integration)
- **LocalStack:** `/localstack/localstack` (Library that allows for local AWS implementation)
- **Boto3:** `/boto/boto3` (AWS SDK for S3, ECS integration)
- **Microsoft Presidio:** `/microsoft/presidio` (PII detection and de-identification)
- **Docling:** `/docling-project/docling` (Advanced PDF to markdown conversion)
- **Canvas LMS:** `/instructure/canvas-lms` (LMS platform integration)
- **Canvas API:** `/ucfopen/canvasapi` (Python Canvas API wrapper)
- **Canvas Dev Resources:** `/websites/developerdocs_instructure_services_canvas_resources` (API documentation)
- **Astro:** `/withastro/astro` (Frontend framework)
- **Radix UI:** `/radix-ui/primitives` (Accessible UI components)
- **Tailwind CSS:** `/tailwindlabs/tailwindcss.com` (Utility-first CSS)
- **Redis:** `/redis/redis-py` (Queue management and caching)