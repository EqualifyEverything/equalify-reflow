# PRD-001: Infrastructure Foundation

## Overview
**Epic**: MVP PDF Converter Infrastructure
**Phase**: 1 - Foundation
**Estimated Effort**: 2 days
**Dependencies**: None
**Parallel**: ✅ Can start immediately

## Problem Statement
The Equalify PDF Converter requires a complete local development infrastructure that mirrors production AWS services. This foundation must support containerized microservices with Redis queuing, S3 storage, and proper networking between services.

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
Use Docker Compose override files for environment-specific configurations:
- **Base**: `docker-compose.yml` with core services (Redis, API services)
- **Development**: `docker-compose.dev.yml` adds LocalStack + dev environment variables
- **Production**: `docker-compose.prod.yml` has production environment variables for real AWS

```yaml
# Base services (docker-compose.yml)
services:
  redis:           # Message broker
  api-gateway:     # Placeholder for Phase 2
  pii-worker:      # Placeholder for Phase 2
  approval-service: # Placeholder for Phase 2
  processing-worker: # Placeholder for Phase 2
  timeout-worker:   # Placeholder for Phase 2

# Development override (docker-compose.dev.yml)
services:
  localstack:      # AWS services (dev only)
```

**Usage Examples:**
```bash
# Development
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

# Production
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up
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

### 3. Container Orchestration
- [ ] All services start in correct dependency order
- [ ] Network communication between containers
- [ ] Environment variables properly injected
- [ ] Services restart on failure

### 4. Development Tools
- [ ] Setup scripts for initializing AWS resources
- [ ] Docker Compose commands documented
- [ ] Health check endpoints for all infrastructure
- [ ] Local testing utilities provided

## Deliverables

### Files to Create
```
/docker-compose.yml                    # Main orchestration
/docker-compose.dev.yml               # Development overrides
/docker-compose.prod.yml              # Production overrides
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

### Service Dependencies
```yaml
# Startup Order
1. Redis + LocalStack (parallel)
2. All other services (depend on infrastructure)
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
- [ ] `docker-compose up` starts all infrastructure services
- [ ] Health check script passes all validations
- [ ] Documentation allows new developer to setup locally
- [ ] No hardcoded values, all environment-driven
- [ ] Services restart automatically on failure
- [ ] Infrastructure ready for Phase 2 service integration