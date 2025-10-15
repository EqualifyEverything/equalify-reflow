# Equalify PDF Converter

[![Test Suite](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/test.yml/badge.svg)](https://github.com/EqualifyEverything/equalify-pdf-converter/actions/workflows/test.yml)

> ⚠️ **ACTIVE DEVELOPMENT** - This project is currently in Phase 2 development. Core infrastructure is complete, but API endpoints and processing workers are still being implemented. See [Project Status](docs/project-status.md) for current progress.

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

That's it! The API is running at http://localhost:8080 with hot reload enabled.

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

**Frontend (Phase 3):**
- Astro (static site generation)
- ShadCN/Radix (accessible UI components)
- Tailwind CSS

## Documentation

### Getting Started
- **[Infrastructure Setup](docs/infrastructure-setup.md)** - Complete setup guide with troubleshooting
- **[Contributing Guidelines](CONTRIBUTING.md)** - Development workflow and code standards
- **[Project Status](docs/project-status.md)** - Current phase and roadmap

### Technical Documentation
- **[Architecture Overview](docs/architecture.md)** - Detailed system design and data flows
- **[CI/CD Pipeline](docs/ci-cd.md)** - Testing strategy and GitHub Actions workflows
- **[Testing Strategy](docs/testing-strategy.md)** - Test organization and best practices

### AWS Deployment
- **[AWS Guide](docs/aws-guide.md)** - Complete deployment, testing, and operations guide
- **[AWS Bedrock Integration](docs/aws-bedrock-summary.md)** - Using Claude via AWS Bedrock
- **[Bedrock Migration Guide](docs/aws-bedrock-migration.md)** - Detailed migration from Anthropic API

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

1. Check [Infrastructure Setup Guide](docs/infrastructure-setup.md)
2. Run `make health` to validate infrastructure
3. Review logs: `make logs` or `make logs-api`
4. Try `make help` for all available commands
5. Create a [GitHub issue](https://github.com/EqualifyEverything/equalify-pdf-converter/issues)

## Project Status

**Current Phase:** Phase 2 - Services & Background Workers (60% complete)

**Completed:**
- ✅ Phase 1: Infrastructure Foundation
- ✅ Docker Compose orchestration
- ✅ Redis and LocalStack integration
- ✅ Tiered test suite with 82% coverage
- ✅ CI/CD pipeline with GitHub Actions

**In Progress:**
- 🚧 FastAPI REST endpoints
- 🚧 Background PII worker (Microsoft Presidio)
- 🚧 Background processing worker (AI pipeline)
- 🚧 Approval workflow service

**Planned:**
- 📋 Phase 3: Frontend application (Astro + ShadCN)
- 📋 Phase 4: AWS ECS deployment

See [Project Status](docs/project-status.md) for detailed roadmap.

---

**Built for** University of Illinois Chicago (UIC)

**Last Updated:** 2025-10-06 | **Version:** 1.0.0
