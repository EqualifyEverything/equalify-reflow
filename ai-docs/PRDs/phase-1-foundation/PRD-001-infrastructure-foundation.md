# PRD-001: Infrastructure Foundation

## Overview
**Epic**: MVP PDF Converter Infrastructure
**Phase**: 1 - Foundation
**Estimated Effort**: 2 days
**Dependencies**: None
**Parallel**: ✅ Can start immediately

## Problem Statement
The Equalify PDF Converter requires a complete local development infrastructure that mirrors production AWS services. This foundation must support the **monolith Python application** (single codebase with FastAPI + background workers) with Redis task queues, S3 storage, and proper local development tooling.

## Success Criteria
- [ ] LocalStack running with S3 buckets configured
- [ ] Redis instance with persistent storage
- [ ] Docker Compose orchestration for all services
- [ ] Health checks and service dependencies
- [ ] Local AWS CLI configured and tested
- [ ] Container networking allowing inter-service communication

## Technical Requirements

### LocalStack Configuration
```yaml
# S3 Buckets Required
- equalify-pdf-temp       # Temporary PDF storage
- equalify-pdf-results    # Processed HTML storage

# Services Required
- S3 (primary)
- CloudWatch (logging)
```

### Redis Configuration
```yaml
# Data Structures Required
- Lists: eq-pdf:queue:pii, eq-pdf:queue:approval, eq-pdf:queue:processing
- Sorted Sets: eq-pdf:timeouts:approval
- Hashes: eq-pdf:job:{job_id}, eq-pdf:metrics:daily
```

### Docker Compose Structure
Use Docker Compose **only for infrastructure services** (Redis, LocalStack):
- **Base**: `docker-compose.yml` with infrastructure services only
- The **monolith application runs via `uv`** during development, NOT in Docker
- Application includes: FastAPI API + background worker threads in single process

```yaml
# Infrastructure services (docker-compose.yml)
services:
  redis:           # Task queue and caching
  localstack:      # AWS services for local development (S3, etc.)
```

**Architecture Note:**
This is a **monolith with background task queue** pattern, not microservices.
- Single Python application codebase
- FastAPI REST API + background workers in one process
- Redis provides async task distribution (not service-to-service communication)

**Usage Examples:**
```bash
# Start infrastructure services only
docker-compose up -d

# Run the monolith application (FastAPI + workers)
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8080
```

## Acceptance Criteria

### 1. LocalStack Setup
- [ ] S3 buckets auto-created on startup
- [ ] Bucket policies configured for public read on results bucket
- [ ] AWS CLI commands work: `awslocal s3 ls`
- [ ] Container persists data between restarts

### 2. Redis Setup
- [ ] Redis accepts connections on internal network
- [ ] Persistent storage configured
- [ ] Basic queue operations tested: `LPUSH`, `BLPOP`
- [ ] Health check endpoint responsive

### 3. Infrastructure Orchestration
- [ ] Infrastructure services start in correct dependency order
- [ ] Network communication between infrastructure services
- [ ] Environment variables properly configured for application
- [ ] Infrastructure services restart on failure

### 4. Development Tools
- [ ] Setup scripts for initializing AWS resources
- [ ] Docker Compose commands documented
- [ ] Health check endpoints for all infrastructure
- [ ] Local testing utilities provided

## Deliverables

### Files to Create
```
/docker-compose.yml                    # Infrastructure orchestration
/scripts/setup-aws.sh                 # LocalStack initialization
/scripts/health-check.sh              # Infrastructure validation
/.env.example                         # Environment template
/infrastructure/localstack/           # LocalStack config
/infrastructure/redis/                # Redis config
/docs/infrastructure-setup.md         # Setup documentation
```

### Configuration Files
- Docker Compose with proper networking and volumes
- Environment variable templates
- LocalStack initialization scripts
- Redis persistence configuration
- Health check endpoints

## Technical Notes

### Infrastructure Dependencies
```yaml
# Startup Order
1. Infrastructure: Redis + LocalStack (via docker-compose)
2. Application: Python monolith (via uv run - FastAPI + background workers)
```

**Application Structure:**
```
src/main.py:
  - Starts FastAPI server (main thread)
  - Starts PII worker thread (monitors eq-pdf:queue:pii)
  - Starts processing worker thread (monitors eq-pdf:queue:processing)
  - Starts timeout scheduler thread (checks approval deadlines)
```

### Environment Variables

**Development (.env.dev):**
```bash
# AWS Configuration (LocalStack)
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
AWS_ENDPOINT_URL=http://localstack:4566

# Redis Configuration
REDIS_URL=redis://redis:6379

# S3 Buckets
S3_TEMP_BUCKET=equalify-temp
S3_RESULTS_BUCKET=equalify-results
```

**Production (.env.prod):**
```bash
# AWS Configuration (Real AWS)
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
AWS_DEFAULT_REGION=us-east-1
# AWS_ENDPOINT_URL not set (uses real AWS)

# Redis Configuration
REDIS_URL=${REDIS_URL}

# S3 Buckets
S3_TEMP_BUCKET=equalify-pdf-temp
S3_RESULTS_BUCKET=equalify-pdf-results
```

### Validation Tests
**Development Validation Script**: `./scripts/health-check.sh`
- Manual script run after `docker-compose up`
- Validates infrastructure setup for developers
- Tests Redis connectivity, S3 bucket creation, container networking

```bash
# Infrastructure Validation Script
./scripts/health-check.sh
# Verifies:
# - Redis connectivity: redis-cli ping
# - S3 bucket creation: awslocal s3 ls
# - Container networking: curl service endpoints
# - Service health endpoints: curl localhost:8080/health
```

## Definition of Done
- [ ] `docker-compose up` starts all infrastructure services (Redis, LocalStack)
- [ ] Health check script passes all validations
- [ ] Documentation allows new developer to setup locally
- [ ] No hardcoded values, all environment-driven
- [ ] Infrastructure services restart automatically on failure
- [ ] Infrastructure ready for monolith application development