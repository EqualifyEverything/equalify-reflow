# Equalify PDF Converter

[![Test Suite](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/test.yml/badge.svg)](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/test.yml)

> ⚠️ **ACTIVE DEVELOPMENT** - Core infrastructure is complete. AI document remediation features are being implemented.

Transform PDF documents into accessible, semantic HTML for the University of Illinois Chicago (UIC).

## What It Does

Converts PDFs into Semantic Markup with:

- Semantic structure with proper heading hierarchy
- AI-generated contextual alt text for images
- Accessible mathematical content (MathML)
- PII detection and protection
- Faculty review workflow with confidence scoring

## Quick Start

### Prerequisites

- Docker (v20.10+)
- Docker Compose (v2.0+)

### Get Started in 3 Commands

```bash
# 1. Start everything
make dev

# 2. Verify it's running
curl http://localhost:8080/health

# 3. View API docs
open http://localhost:8080/docs
```

That's it! The API is running at <http://localhost:8080> with hot reload enabled.

## Authentication

The API includes two authentication layers for security:

### API Key Authentication

All API endpoints (except `/health` and `/metrics`) require an API key for access:

```bash
# Example: Submit document with example API key
curl -X POST http://localhost:8080/api/documents/submit \
  -H "X-API-Key: uic-2bd2c716-bc67-4032-ba66-e4f35c441759" \
  -F "file=@document.pdf"
```

**Your API key:** `uic-2bd2c716-bc67-4032-ba66-e4f35c441759` (configured in `.env`)

### Swagger UI Authentication

The API documentation at `/docs` uses HTTP Basic Authentication:

- **URL:** <http://localhost:8080/docs>
- **Username:** `dase`
- **Password:** `a11y`

Your browser will prompt for these credentials when accessing the documentation.

### Disabling Authentication (Development)

To disable authentication for local testing, update `.env`:

```bash
ENABLE_API_KEY_AUTH=false
ENABLE_DOCS_AUTH=false
```

Then restart: `make down && make dev`

**Note:** Re-enable authentication before deploying to production!

## Essential Commands

```bash
make dev            # Start development environment
make down           # Stop all services
make logs           # View all service logs
make logs-api       # View API logs only
make health         # Run health checks
make test           # Run fast unit tests
```

See `make help` for all available commands.

## Demo UI

A developer testing interface is available at <http://localhost:8080/demo> when running `make dev`.

**Features:**

- Document upload with drag-and-drop
- Real-time job status tracking with workflow visualization
- PII review modal for approval workflow testing
- AI correction review with side-by-side diff view
- Raw API response viewer for debugging

**Rebuilding:** After frontend changes, run `make build-demo-ui` then `make down && make dev`.

## Running Tests

The test suite is organized into three tiers for optimal feedback:

```bash
make test-fast         # Unit tests (<30s with parallelization) - Run before every commit
make test-integration  # Integration tests (<2min with parallelization) - Run before PR
make test-e2e          # E2E tests (<5min with parallelization) - Run before merge
```

**Current Coverage:** 83.70% (536/536 tests passing ✅)

**Performance:** Full test suite runs in **~30 seconds** with parallelization (`pytest-xdist`)

See [CI/CD Documentation](docs/ci-cd.md) for detailed testing information.

## AWS Deployment

The application is deployed on AWS ECS. Quick commands:

```bash
make aws-health   # Check deployment health
make aws-logs     # View CloudWatch logs
make aws-status   # Show ECS service status
```

**Auto-login:** All AWS commands automatically detect expired SSO tokens and prompt you to login. No need to manually run `aws sso login` first!

**Prerequisites:** Install AWS Session Manager plugin for `make aws-shell`:

```bash
brew install --cask session-manager-plugin  # macOS
```

**For full deployment guide, troubleshooting, and operations:** See [AWS Guide](docs/aws-guide.md)

## Architecture

**Pattern:** Monolith with Background Task Queue (single Python application)

```
FastAPI REST API → Redis Queues → Background Workers
     ↓                                    ↓
  S3 Temp Storage              S3 Results Storage
```

**Key Features:**

- Single codebase with FastAPI + background workers
- Redis task queues for async processing
- Docker Compose orchestration (LocalStack for local AWS)
- Hot reload development workflow
- Tiered test suite (unit → integration → E2E)

See [Architecture Overview](docs/architecture.md) for detailed system design.

## Technology Stack

**Backend:**

- Python 3.11+ (using `uv` for package management)
- FastAPI (async API framework)
- PydanticAI (multi-agent AI framework)
- IBM Docling (PDF to Markdown conversion)
- Microsoft Presidio (PII detection)

**Infrastructure:**

- Docker & Docker Compose
- Redis (task queues and caching)
- AWS S3 (object storage)
- AWS ECS (production deployment)
- LocalStack (local AWS emulation)

**Frontend (Demo UI):**

- React 18 + TypeScript + Vite
- ShadCN/Radix (accessible UI components)
- Tailwind CSS
- React Query (async state management)

## Documentation

### Getting Started

- **[Environment Setup](docs/environment-setup.md)** - Complete setup guide with troubleshooting
- **[Contributing Guidelines](CONTRIBUTING.md)** - Development workflow and code standards

### Technical Documentation

- **[Architecture Overview](docs/architecture.md)** - Detailed system design, data flows, and AWS Bedrock integration
- **[CI/CD Pipeline](docs/ci-cd.md)** - Testing strategy, test tiers, and GitHub Actions workflows

### AWS Deployment

- **[AWS Guide](docs/aws-guide.md)** - Complete deployment, testing, and operations guide

### Infrastructure Configuration

- **[Redis Configuration](infrastructure/redis/README.md)** - Redis setup and operations
- **[LocalStack Configuration](infrastructure/localstack/README.md)** - Local AWS services
- **[Scripts Documentation](scripts/README.md)** - Utility scripts reference

### Project Planning

- **[PRD Index](ai-docs/PRDs/README.md)** - All Product Requirement Documents with implementation order
- **[Project Instructions](CLAUDE.md)** - Development patterns and guidelines

## Development Workflow

This project uses **fully containerized development**. All development happens inside Docker containers with hot reload:

```bash
# Start services
make dev

# Edit code in src/ - changes auto-reload
# No rebuild needed!

# Access container shell if needed
make shell

# Run tests in container
make test-docker
```

See [Contributing Guidelines](CONTRIBUTING.md) for detailed workflow.

## Integrations

- **Canvas LMS** - External URL module items for course materials
- **Equalify Platform** - Webhook-triggered processing from accessibility scans
- **AWS ECS** - Production container orchestration

## Support

Having issues?

1. Check [Environment Setup Guide](docs/environment-setup.md)
2. Run `make health` to validate infrastructure
3. Review logs: `make logs` or `make logs-api`
4. Try `make help` for all available commands
5. Create a [GitHub issue](https://github.com/EqualifyEverything/equalify-pdf-converter/issues)

## Project Status

This project is under active development. Core infrastructure (API, workers, queues, storage) is complete. AI document remediation features are being implemented.

See [PRD Index](ai-docs/PRDs/README.md) for detailed project roadmap.
