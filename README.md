# Equalify PDF Converter

[![Test Suite](https://github.com/dylanisaac/equalify-pdf-converter/actions/workflows/test.yml/badge.svg)](https://github.com/dylanisaac/equalify-pdf-converter/actions/workflows/test.yml)

Transform PDF documents into accessible, semantic HTML for the University of Illinois Chicago (UIC).

## Overview

The Equalify PDF Converter addresses the fundamental accessibility challenges of PDF documents by converting them into responsive, semantic HTML that meets WCAG 2.1 AA compliance standards. Designed specifically for UIC's accessibility enhancement initiative, the system processes course materials through a multi-agent AI pipeline to ensure proper semantic structure, contextual alt text, and mathematical accessibility.

## Key Features

- **Semantic Conversion**: PDF → Markdown → Semantic HTML with proper heading hierarchy
- **AI-Powered Enhancement**: Multi-agent PydanticAI pipeline for semantic analysis
- **Accessibility First**: WCAG 2.1 AA compliance validation
- **PII Protection**: Microsoft Presidio scanning and de-identification
- **Faculty Review**: Natural language correction workflow with confidence scoring
- **Multiple Output Formats**:
  - Accessible Astro application (ShadCN/Radix components)
  - Canvas LMS Pages integration
- **Cost Effective**: ~$0.20 per document processing cost
- **Fast Processing**: 2-8 minutes for typical documents

## Architecture

**Design Pattern**: Monolith with Background Task Queue

The application runs as a **single Python process** with:
- **FastAPI REST API** - Handles HTTP requests
- **Background Workers** - Process tasks from Redis queues asynchronously
- **Redis Task Queues** - Async communication between API and workers

### Infrastructure
- **AWS ECS**: Container orchestration with Fargate (production)
- **Redis**: Task queue for background processing and caching
- **S3**: Static file hosting with versioning
- **LocalStack**: Local AWS emulation for development

### Processing Pipeline
1. PDF upload via REST API → stored in S3 temp bucket
2. Background PII worker → Microsoft Presidio scanning
3. Faculty approval workflow (if PII detected)
4. Background processing worker → PDF to Markdown (IBM Docling)
5. AI semantic enhancement → Accessible MDX/HTML generation
6. Results stored in S3 → Versioned URLs returned via API

### Application Components (Single Codebase)
- **FastAPI API** - Document submission, status tracking, results retrieval
- **PII Worker** - Background thread monitoring `eq-pdf:queue:pii`
- **Processing Worker** - Background thread monitoring `eq-pdf:queue:processing`
- **Timeout Worker** - Background scheduler checking approval deadlines
- **Shared Services** - S3 storage, Redis queue management, job tracking

## Quick Start

### Prerequisites

- Docker (v20.10+)
- Docker Compose (v2.0+)

### Development Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd equalify-pdf-converter

# 2. Start the stack
make dev

# 3. Verify API is running
curl http://localhost:8000/health

# 4. Access API documentation
open http://localhost:8000/docs
```

### Development Features

- 🔥 **Hot Reload**: Code changes auto-reload without container restart
- 🐳 **Unified Networking**: All services communicate via Docker DNS
- 🚀 **Single Command**: `make dev` starts everything
- ✅ **Tests in Container**: Run tests in same environment as production

### Common Commands

```bash
# Essential
make dev            # Start development environment
make down           # Stop all services
make logs           # View all service logs
make logs-api       # View API logs only
make health         # Run health checks
make test           # Run tests

# Testing (Tiered Test Suite)
make test-fast      # Unit tests (<2min, no Docker needed)
make test-integration # Integration tests (~5min, real Redis/S3)
make test-e2e       # E2E tests (~10min, full workflows)
make test-all       # All tests in Docker (most comprehensive)

# Coverage
make coverage       # Run tests with coverage report
make coverage-html  # Generate and open HTML coverage report
make coverage-report # Show coverage summary

# Docker
make build          # Build Docker images
make shell          # Access API container shell
make test-docker    # Run tests inside container

# Utilities
make redis-cli      # Connect to Redis CLI
make clean          # Remove containers and volumes
```

### Running Tests

The test suite is organized into three tiers for optimal developer feedback:

#### Fast Unit Tests (<2min)
```bash
make test-fast    # or: make test-unit
```
- **What**: Pure unit tests with mocked dependencies
- **When**: Every code change, before committing
- **No Docker needed**: Runs directly with `uv run pytest`
- **Coverage**: ~101 tests in `tests/unit/`

#### Integration Tests (~5min)
```bash
make test-integration
```
- **What**: Tests with real Redis/S3 via testcontainers
- **When**: Before opening PR
- **Requires**: Docker for testcontainers
- **Coverage**: ~28 tests in `tests/integration/`

#### E2E Tests (~10min)
```bash
make test-e2e     # or: make test-slow
```
- **What**: Full workflow tests with minimal mocking
- **When**: Before merging to main
- **Requires**: Docker for testcontainers
- **Coverage**: ~63 tests in `tests/e2e/`

#### All Tests (Comprehensive)
```bash
make test-all     # Runs in Docker
```
- **What**: Complete test suite (591 tests)
- **When**: Final verification before deployment
- **Requires**: `make dev` (Docker stack running)

#### Test by Marker
```bash
# Run specific test categories
uv run pytest -m unit            # Unit tests only
uv run pytest -m integration     # Integration tests only
uv run pytest -m slow            # Slow/E2E tests only
uv run pytest -m performance     # Performance tests
uv run pytest -m requires_redis  # Redis-dependent tests
uv run pytest -m requires_s3     # S3-dependent tests
```

### CI/CD

All tests run automatically via GitHub Actions on:
- **Every push**: Fast unit tests (<2min)
- **PRs to main/develop**: Integration + E2E tests (~15min total)
- **Merge to main**: Full test suite
- **Weekly**: Performance tests (Sundays at 2 AM UTC)

**Status:** PRs cannot merge until all tests pass.

**CI Workflows:**
- **[test-fast.yml](.github/workflows/test-fast.yml)** - Unit tests on every push
- **[test-integration.yml](.github/workflows/test-integration.yml)** - Integration tests on PRs
- **[test-e2e.yml](.github/workflows/test-e2e.yml)** - E2E tests on main branch
- **[test-performance.yml](.github/workflows/test-performance.yml)** - Weekly performance benchmarks

### Test Coverage

Test coverage is automatically collected on every CI run and uploaded as artifacts.

**Current Coverage:** 82% (503/503 tests passing ✅)

**Local Coverage:**
```bash
# Run tests with coverage
make coverage

# View HTML coverage report
make coverage-html

# Show coverage summary
make coverage-report
```

**CI Coverage Reports:**
Coverage HTML reports are generated for all test jobs and uploaded as artifacts:
- **Unit Tests:** Coverage for services, models, and API endpoints
- **Integration Tests:** Coverage for Redis/S3 integration paths
- **Docker Tests:** Full end-to-end coverage report

Download coverage reports from GitHub Actions → Workflow run → Artifacts section.

**Coverage Details:**
- Line coverage (% of code lines executed)
- Branch coverage (% of decision branches tested)
- Missing lines highlighted in HTML reports
- Per-file coverage breakdown

## Project Status

**Current Phase**: Phase 1 - Infrastructure Foundation ✅

### Completed
- ✅ Docker Compose orchestration
- ✅ Redis configuration and persistence
- ✅ LocalStack S3 emulation
- ✅ Development/Production environment separation
- ✅ Health check and validation scripts
- ✅ Infrastructure documentation

### In Progress
- 🚧 Phase 2: FastAPI REST endpoints (document submission, status, results)
- 🚧 Phase 2: Background PII worker with Microsoft Presidio
- 🚧 Phase 2: Background processing worker with AI pipeline
- 🚧 Phase 2: Approval workflow and timeout monitoring

### Planned
- 📋 Phase 3: Frontend application (Astro + ShadCN)
- 📋 Phase 3: Canvas LMS integration
- 📋 Phase 4: AWS ECS deployment
- 📋 Phase 4: Monitoring and observability

## Documentation

### Architecture & Planning
- **[Architecture Overview](docs/architecture.md)** - Detailed monolith architecture
- **[Architecture Clarification](docs/ARCHITECTURE_CLARIFICATION.md)** - Monolith vs microservices Q&A
- **[Implementation Order](IMPLEMENTATION_ORDER.md)** - Quick reference for PRD order
- **[Infrastructure Setup Guide](docs/infrastructure-setup.md)** - Complete setup instructions
- **[Program Flow](project-docs/program-flow.md)** - System-wide flow diagrams

### PRD Documentation
- **[PRD Index](ai-docs/PRDs/README.md)** - All PRDs with dependencies
- **[PRD Restructuring Summary](docs/PRD_RESTRUCTURING_SUMMARY.md)** - Recent changes
- **[Using /prd Command](docs/USING_PRD_COMMAND.md)** - Guide for AI agents executing PRDs

### Infrastructure Configuration
- **[Scripts Documentation](scripts/README.md)** - Utility scripts reference
- **[Redis Configuration](infrastructure/redis/README.md)** - Redis setup and operations
- **[LocalStack Configuration](infrastructure/localstack/README.md)** - Local AWS services

### Project Documentation
- **[System Architecture](CLAUDE.md)** - Project instructions and patterns
- **[Project Proposal](project-docs/local/Proposal.md)** - Original technical proposal
- **[Version 1 Buildout](project-docs/local/Version%201%20Buildout.md)** - Contract deliverables

## Development

### Project Structure

```
equalify-pdf-converter/
├── docker-compose.yml              # Base service definitions
├── docker-compose.dev.yml          # Development overrides (LocalStack)
├── docker-compose.prod.yml         # Production overrides
├── .env.example                    # Environment template
├── .env.dev                        # Development configuration
├── .env.prod                       # Production configuration
├── docs/                           # Documentation
│   └── infrastructure-setup.md     # Setup guide
├── infrastructure/                 # Infrastructure configuration
│   ├── localstack/                 # LocalStack init scripts
│   │   ├── init-aws.sh             # S3 bucket creation
│   │   └── README.md               # LocalStack documentation
│   └── redis/                      # Redis configuration
│       ├── redis.conf              # Redis server config
│       └── README.md               # Redis documentation
├── scripts/                        # Utility scripts
│   ├── setup-aws.sh                # AWS resource initialization
│   ├── health-check.sh             # Infrastructure validation
│   └── README.md                   # Scripts documentation
├── src/                            # Source code
│   ├── shared/                     # Shared models and utilities
│   │   ├── models/                 # Pydantic data models
│   │   └── constants/              # Shared constants
│   ├── api-gateway/                # FastAPI application (Phase 2)
│   ├── pii-worker/                 # PII detection worker (Phase 2)
│   ├── processing-worker/          # AI processing worker (Phase 2)
│   ├── approval-service/           # Approval workflow (Phase 2)
│   └── timeout-worker/             # Timeout monitoring (Phase 2)
└── tests/                          # Test suite
    └── models/                     # Model tests
```

### Environment Configuration

#### Development (.env.dev)
```bash
# LocalStack for AWS services
AWS_ENDPOINT_URL=http://localstack:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test

# Local Redis
REDIS_URL=redis://redis:6379

# Debug logging
LOG_LEVEL=DEBUG
```

#### Production (.env.prod)
```bash
# Real AWS services
# AWS_ENDPOINT_URL not set
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}

# AWS ElastiCache
REDIS_URL=${REDIS_URL}

# Production logging
LOG_LEVEL=INFO
```

### Running Services

```bash
# Development
make dev              # Start all services
make logs             # View logs
make down             # Stop services

# Production
make prod             # Start with production config
```

### Testing Infrastructure

```bash
# Health checks
make health           # Validate infrastructure

# Test Redis
make redis-cli        # Connect to Redis CLI

# Direct commands if needed
./scripts/health-check.sh
docker exec -it equalify-pdf-redis redis-cli
```

## Technology Stack

### Backend
- **Python 3.11+**: Primary language (using uv for package management)
- **FastAPI**: Async API framework
- **PydanticAI**: Multi-agent AI framework
- **Docling (IBM)**: PDF to Markdown conversion
- **Microsoft Presidio**: PII detection and de-identification

### Infrastructure
- **Docker & Docker Compose**: Containerization
- **Redis**: Message queue and caching
- **AWS S3**: Object storage
- **AWS ECS**: Container orchestration (production)
- **LocalStack**: Local AWS emulation (development)

### Frontend (Phase 3)
- **Astro**: Static site generation framework
- **ShadCN/Radix**: Accessible UI components
- **Tailwind CSS**: Utility-first styling

### Integrations
- **Canvas LMS**: Learning management system integration
- **Equalify Platform**: Webhook-triggered processing

## Configuration

### S3 Buckets

- **equalify-pdf-temp**: Temporary PDF storage (7-day lifecycle)
- **equalify-pdf-results**: Processed HTML results (versioned, public read)

### Redis Data Structures

- **Lists**: `eq-pdf:queue:pii`, `eq-pdf:queue:approval`, `eq-pdf:queue:processing`
- **Sorted Sets**: `eq-pdf:timeouts:approval`
- **Hashes**: `eq-pdf:job:{job_id}`, `eq-pdf:metrics:daily`

### Environment Variables

See `.env.example` for complete list. Key variables:

- `AWS_*`: AWS configuration
- `REDIS_URL`: Redis connection
- `S3_*_BUCKET`: S3 bucket names
- `OPENAI_API_KEY`: AI processing (Phase 2)
- `CANVAS_*`: Canvas LMS integration (Phase 3)

## Deployment

### Development
```bash
make dev              # Start services
make health           # Verify setup
```

### Production (AWS ECS)
```bash
make prod             # Start with production config
```

Detailed AWS deployment instructions coming in Phase 4. Overview:

1. Build and push Docker images to ECR
2. Create ECS task definitions
3. Configure ECS services with auto-scaling
4. Set up Application Load Balancer
5. Configure AWS ElastiCache for Redis
6. Create S3 buckets with proper policies

## Monitoring

```bash
# Infrastructure health
make health           # Comprehensive validation

# Container status
docker ps

# Service logs
make logs             # All services

# Redis
make redis-cli        # Connect to Redis
# Inside CLI: INFO, PING, MONITOR
# Queue lengths: LLEN eq-pdf:queue:pii

# LocalStack
curl http://localhost:4566/_localstack/health
docker exec -it equalify-pdf-localstack awslocal s3 ls
```

## Troubleshooting

Common issues and solutions:

### Services Won't Start
```bash
make logs             # Check logs
make clean            # Remove containers/volumes
make dev              # Restart
```

### Redis Connection Issues
```bash
docker ps | grep redis
make redis-cli        # Test with PING
```

### LocalStack Issues
```bash
make logs             # Check for errors
docker exec -it equalify-pdf-localstack awslocal s3 ls  # Verify buckets
./scripts/setup-aws.sh dev  # Reinitialize if needed
```

See [Infrastructure Setup Guide](docs/infrastructure-setup.md) for detailed troubleshooting.

## Contributing

This project is in active development. Phase 1 (Infrastructure Foundation) is complete.

### Development Workflow

1. Create feature branch
2. Implement changes
3. Run health checks: `make health`
4. Run tests: `make test`
5. Test locally: `make dev`
6. Submit pull request

### Code Standards

- **Python**: Use `uv` for dependency management
- **Docker**: Follow 12-factor app principles
- **Documentation**: Update docs for any infrastructure changes
- **Testing**: Validate with health check scripts

## Success Criteria

- ✅ WCAG 2.1 AA compliance validation
- ✅ Processing cost: ~$0.20 per document
- ✅ Processing time: 2-8 minutes
- ✅ Structure accuracy: ≥90% heading hierarchy preservation
- ✅ Faculty review time: ≤10 minutes for 10-page document

## License

[License information to be added]

## Support

For questions or issues:

1. Check [Infrastructure Setup Guide](docs/infrastructure-setup.md)
2. Run `make health` to validate infrastructure
3. Review logs: `make logs` or `make logs-<service>`
4. Try `make help` for all available commands
5. Create GitHub issue with details

## Acknowledgments

- **University of Illinois Chicago (UIC)**: Primary use case partner
- **IBM Docling**: PDF conversion technology
- **Microsoft Presidio**: PII detection framework
- **PydanticAI**: Multi-agent AI framework

---

**Project Status**: Phase 1 Complete ✅ | Phase 2 In Progress 🚧

**Last Updated**: 2025-09-29

**Version**: 1.0.0