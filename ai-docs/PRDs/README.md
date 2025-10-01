# Equalify PDF Converter - PRD Index

## Implementation Order

**CRITICAL**: PRDs must be implemented in the exact order listed below. Each PRD depends on the completion of all previous PRDs.

---

## Phase 1: Foundation (COMPLETE ✅)

### PRD-001: Infrastructure Foundation ✅
**Status**: Complete
**File**: [phase-1-foundation/PRD-001-infrastructure-foundation.md](phase-1-foundation/PRD-001-infrastructure-foundation.md)
**Deliverables**: Docker Compose, Redis, LocalStack, health checks

### PRD-002: Shared Data Models ✅
**Status**: Complete
**File**: [phase-1-foundation/PRD-002-shared-data-models.md](phase-1-foundation/PRD-002-shared-data-models.md)
**Deliverables**: Pydantic models, queue schemas, Redis data structures

---

## Phase 2: Core Services (SEQUENTIAL - Follow Order)

### PRD-003: Shared Services Foundation ✅
**Status**: Complete
**File**: [phase-2-services/PRD-003-shared-services.md](phase-2-services/PRD-003-shared-services.md)
**Completed**: 2025-09-30
**Effort**: 5.5 hours (actual vs 4 days estimated)
**Dependencies**: PRD-001 ✅, PRD-002 ✅

**Deliverables** (COMPLETE):
- ✅ `src/services/storage_service.py` - S3 operations with upload, download, delete, presigned URLs
- ✅ `src/services/queue_service.py` - Generic Redis queue operations with enqueue, dequeue, peek
- ✅ `src/services/job_service.py` - Job status management with lifecycle operations
- ✅ `src/config.py` - Configuration management with all queue names and settings
- ✅ `src/dependencies.py` - FastAPI dependency injection with proper resource cleanup
- ✅ `tests/services/test_storage_service.py` - 23 tests passing
- ✅ `tests/services/test_queue_service.py` - 21 tests passing
- ✅ `tests/services/test_job_service.py` - 23 tests passing

**Implementation Notes**:
- All services enhanced with PRD-specified methods
- FastAPI dependency injection refactored for proper async generators
- Configuration expanded with AWS credentials, Redis pool settings, queue names
- 67 service tests passing (100% coverage of new methods)
- All 125 total tests passing after integration
- API endpoints updated to use dependency injection pattern

**Unblocks**: PRD-004, PRD-005, PRD-006, PRD-007, PRD-008

---

### PRD-004: Document API Endpoints ✅
**Status**: Complete
**File**: [phase-2-services/PRD-004-api-endpoints.md](phase-2-services/PRD-004-api-endpoints.md)
**Completed**: 2025-09-30
**Effort**: 6 hours (actual vs 2 days estimated)
**Dependencies**: PRD-003 (Shared Services) ✅

**Deliverables** (COMPLETE):
- ✅ `src/api/documents.py` - POST /submit, GET /status, GET /result
- ✅ `src/api/health.py` - Health and readiness checks
- ✅ `src/middleware/error_handler.py` - Global exception handling
- ✅ `src/middleware/logging_middleware.py` - Request/response logging
- ✅ `src/middleware/rate_limit.py` - Redis-based rate limiting
- ✅ `src/main.py` - FastAPI app with middleware stack
- ✅ `src/services/rate_limit_service.py` - Sliding window rate limiter
- ✅ `tests/test_documents.py` - 6 endpoint tests passing
- ✅ `tests/test_health.py` - 3 health check tests passing
- ✅ `tests/services/test_rate_limit_service.py` - 10 rate limit tests passing
- ✅ `project-docs/rate-limiting.md` - Comprehensive documentation

**Implementation Notes**:
- All API endpoints functional with FastAPI dependency injection
- Rate limiting implemented with Redis sliding window algorithm (BONUS)
- Three-tier rate limiting: per-IP submission (10/hr), per-IP status checks (100/hr), global (1000/day)
- Fail-open design ensures availability over strict enforcement
- Headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
- Fixed datetime.utcnow() deprecation warnings
- 135 total tests passing (9 API tests, 10 rate limit tests, 116 existing tests)
- OpenAPI documentation auto-generated at /docs
- Ready for production deployment

**Unblocks**: PRD-004.5, PRD-005, PRD-006, PRD-007, PRD-008

---

### PRD-004.5: Docker Containerization ✅
**Status**: Complete
**File**: [phase-2-services/PRD-004.5-docker-containerization.md](phase-2-services/PRD-004.5-docker-containerization.md)
**Completed**: 2025-10-01
**Effort**: 2.5 hours (actual vs 4 hours estimated)
**Dependencies**: PRD-004 (API Endpoints) ✅

**Deliverables** (COMPLETE):
- ✅ Enhanced `Dockerfile` - Multi-stage build (dev + production targets)
- ✅ Updated `docker-compose.yml` - Real api-gateway service with health checks
- ✅ Updated `docker-compose.dev.yml` - Volume mounts for hot-reload, tests mounted
- ✅ Updated `Makefile` - build, shell, test-docker, logs-api commands
- ✅ Updated `README.md` - Containerized Quick Start workflow

**Implementation Notes**:
- Fixed environment fragmentation - entire stack now runs in Docker
- Zero application code changes - infrastructure only
- Enabled unified networking with Docker DNS resolution
- Hot-reload working with volume mounts (./src and ./tests)
- All 135 tests passing in containerized environment
- Health endpoint accessible: http://localhost:8000/health
- API docs at http://localhost:8000/docs
- Development dependencies (httpx, pytest) included in development stage
- Matches production AWS ECS deployment pattern

**Verification**:
- ✅ `make dev` starts all services (Redis, LocalStack, FastAPI)
- ✅ Health check returns healthy status with Redis + S3 connectivity
- ✅ Hot-reload verified - code changes trigger automatic reload
- ✅ All 135 tests pass in container: `make test-docker`
- ✅ API accessible at localhost:8000
- ✅ Zero application code changes

**Unblocks**: PRD-005, PRD-006, PRD-007, PRD-008

---

### PRD-005: PII Detection Worker
**Status**: Not Started
**File**: [phase-2-services/PRD-005-pii-detection-worker.md](phase-2-services/PRD-005-pii-detection-worker.md)
**Effort**: 3 days
**Dependencies**: PRD-003 (Shared Services) - MUST BE COMPLETE

**Deliverables**:
- `src/workers/pii_worker.py` (Background PII scanning thread)
- Microsoft Presidio integration
- PDF text extraction with Docling

**Shared Services Used**: storage_service, queue_service, job_service

---

### PRD-006: Approval Workflow API
**Status**: Not Started
**File**: [phase-2-services/PRD-006-approval-api.md](phase-2-services/PRD-006-approval-api.md)
**Effort**: 2 days
**Dependencies**: PRD-003 (Shared Services) - MUST BE COMPLETE

**Deliverables**:
- `src/api/approval.py` (POST /approve/{token}, GET /review/{token})
- `src/services/approval_service.py` (Decision processing)
- `src/services/cleanup_service.py` (Denied job cleanup)

**Shared Services Used**: queue_service, job_service, storage_service

---

### PRD-007: Processing Worker
**Status**: Not Started
**File**: [phase-2-services/PRD-007-processing-worker.md](phase-2-services/PRD-007-processing-worker.md)
**Effort**: 4 days
**Dependencies**: PRD-003 (Shared Services) - MUST BE COMPLETE

**Deliverables**:
- `src/workers/processing_worker.py` (Background AI processing thread)
- Docling PDF→Markdown conversion
- Claude AI accessibility enhancement pipeline
- MDX/HTML generation

**Shared Services Used**: storage_service, queue_service, job_service

---

### PRD-008: Timeout Cleanup Worker
**Status**: Not Started
**File**: [phase-2-services/PRD-008-timeout-cleanup-worker.md](phase-2-services/PRD-008-timeout-cleanup-worker.md)
**Effort**: 2 days
**Dependencies**: PRD-003 (Shared Services) - MUST BE COMPLETE

**Deliverables**:
- `src/workers/timeout_worker.py` (Background maintenance scheduler)
- Approval timeout monitoring
- S3 temp file cleanup
- Orphaned job cleanup

**Shared Services Used**: storage_service, queue_service, job_service

---

## Phase 3: Integration & Demo

### PRD-009: End-to-End Integration
**Status**: Not Started
**File**: [phase-3-integration/PRD-009-end-to-end-integration.md](phase-3-integration/PRD-009-end-to-end-integration.md)
**Effort**: 3 days
**Dependencies**: ALL Phase 2 PRDs complete

**Deliverables**:
- End-to-end integration tests
- Performance benchmarking
- Error handling validation
- Documentation updates

---

### PRD-010: Demo Frontend Application
**Status**: Not Started
**File**: [phase-3-integration/PRD-010-demo-frontend.md](phase-3-integration/PRD-010-demo-frontend.md)
**Effort**: 3 days
**Dependencies**: PRD-004, PRD-006

**Deliverables**:
- `frontend/demo-ui/` (Vite + React + TypeScript)
- ShadCN UI components
- Document upload interface
- Job status tracking
- PII review interface

**⚠️ NOTE**: This is a DEMO frontend for testing and stakeholder presentations, NOT the production UIC interface.

---

## Implementation Timeline

### Week 1: Foundation
```
Days 1-4: PRD-003 Shared Services (CRITICAL PATH)
Days 5-6: PRD-004 API Endpoints
```

### Week 2: Workers
```
After PRD-003 complete, can work on workers:
- PRD-005: PII Worker (3 days)
- PRD-006: Approval API (2 days)
- PRD-007: Processing Worker (4 days)
- PRD-008: Timeout Worker (2 days)

With 1 developer: Sequential
With 2+ developers: Can parallelize after PRD-003 done
```

### Week 3: Integration
```
Days 1-2: PRD-009 End-to-end integration
Days 3-5: PRD-010 Demo frontend (optional)
```

---

## Quick Reference

### Phase 2 Dependencies Graph
```
PRD-003: Shared Services
    ├─► PRD-004: API Endpoints
    ├─► PRD-005: PII Worker
    ├─► PRD-006: Approval API
    ├─► PRD-007: Processing Worker
    └─► PRD-008: Timeout Worker
```

### File Locations
```
src/
├── main.py                    # FastAPI app (PRD-004)
├── config.py                  # Configuration (PRD-003)
├── dependencies.py            # DI (PRD-003)
├── api/
│   ├── documents.py           # Document endpoints (PRD-004)
│   ├── health.py              # Health checks (PRD-004)
│   └── approval.py            # Approval endpoints (PRD-006)
├── services/
│   ├── storage_service.py     # S3 operations (PRD-003)
│   ├── queue_service.py       # Redis queues (PRD-003)
│   ├── job_service.py         # Job management (PRD-003)
│   ├── approval_service.py    # Approval logic (PRD-006)
│   └── cleanup_service.py     # Cleanup logic (PRD-006)
├── workers/
│   ├── pii_worker.py          # PII scanning (PRD-005)
│   ├── processing_worker.py   # AI processing (PRD-007)
│   └── timeout_worker.py      # Maintenance (PRD-008)
├── middleware/
│   ├── error_handler.py       # Error handling (PRD-004)
│   └── logging_middleware.py  # Logging (PRD-004)
└── shared/                    # Shared models (PRD-002)
```

---

## Architecture Reference

- **Pattern**: Monolith with Background Task Queue
- **NOT**: Microservices (despite having multiple workers)
- **Deployment**: Single Python application via `uv run uvicorn src.main:app`
- **Workers**: Background asyncio tasks started by `src/main.py`
- **Shared Services**: All components import from `src/services/`

See [docs/architecture.md](../../docs/architecture.md) for detailed architecture explanation.

---

## For AI Agents

When implementing a PRD:
1. Verify all dependencies are complete
2. Read the specific PRD file for detailed requirements
3. Implement deliverables listed in PRD
4. All workers import shared services from PRD-003
5. All components run in same Python process
6. Test integration with existing components

**Critical**: Do not start a PRD until all dependencies are complete. PRD-003 must be finished before any other Phase 2 work.
