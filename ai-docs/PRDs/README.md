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
**Status**: Complete (Updated 2025-10-02 - Added PRD-008 dependencies)
**File**: [phase-2-services/PRD-003-shared-services.md](phase-2-services/PRD-003-shared-services.md)
**Completed**: 2025-09-30 (Initial), 2025-10-02 (Extensions)
**Effort**: 5.5 hours initial + 4 hours extensions = 9.5 hours total (actual vs 4 days estimated)
**Dependencies**: PRD-001 ✅, PRD-002 ✅

**Deliverables** (COMPLETE):
- ✅ `src/services/storage_service.py` - S3 operations with upload, download, delete, presigned URLs
  - **NEW (10/02)**: `cleanup_temp_files_for_job()` - Batch delete temp files for job
  - **NEW (10/02)**: `list_temp_files()` - List files older than retention period
  - **NEW (10/02)**: `delete_from_s3()` - Generic idempotent delete method
- ✅ `src/services/queue_service.py` - Generic Redis queue operations with enqueue, dequeue, peek
  - **NEW (10/02)**: `add_to_timeout_tracking()` - Add job to sorted set with deadline
  - **NEW (10/02)**: `get_expired_timeouts()` - Query sorted set for expired jobs
  - **NEW (10/02)**: `remove_from_timeout_tracking()` - Remove job from timeout tracking
  - **NEW (10/02)**: `get_timeout_count()` - Count jobs awaiting approval
- ✅ `src/services/job_service.py` - Job status management with lifecycle operations
  - **NEW (10/02)**: `cleanup_old_job()` - Delete old job hash from Redis
- ✅ `src/config.py` - Configuration management with all queue names and settings
  - **NEW (10/02)**: Timeout worker schedules (approval_check_interval_seconds, etc.)
  - **NEW (10/02)**: Retention policies (temp_file_retention_hours, job_retention_days, etc.)
- ✅ `src/dependencies.py` - FastAPI dependency injection with proper resource cleanup
- ✅ `tests/services/test_storage_service.py` - 37 tests passing (+14 new cleanup tests)
- ✅ `tests/services/test_queue_service.py` - 34 tests passing (+13 new timeout tracking tests)
- ✅ `tests/services/test_job_service.py` - 32 tests passing (+9 new cleanup tests)
- ✅ `src/shared/README.md` - Updated with new service methods documentation

**Implementation Notes**:
- All services enhanced with PRD-specified methods
- FastAPI dependency injection refactored for proper async generators
- Configuration expanded with AWS credentials, Redis pool settings, queue names
- **Extension (10/02)**: Added missing methods needed by PRD-008 (Timeout Worker)
  - StorageService: Batch cleanup operations for temp files
  - QueueService: Redis sorted set operations for timeout tracking
  - JobService: Old job cleanup for retention management
  - Config: Cleanup schedules and retention policies
- **129 service tests passing** (100% coverage including new methods)
- All service tests green, PRD-003 fully complete
- API endpoints updated to use dependency injection pattern

**Unblocks**: PRD-004 ✅, PRD-005 ✅, PRD-006 ✅, PRD-007 ✅, **PRD-008 (NOW READY)**

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

### PRD-005: PII Detection Worker ✅
**Status**: Complete
**File**: [phase-2-services/PRD-005-pii-detection-worker.md](phase-2-services/PRD-005-pii-detection-worker.md)
**Completed**: 2025-10-01
**Effort**: 4 hours (actual vs 3 days estimated)
**Dependencies**: PRD-003 (Shared Services) ✅

**Deliverables** (COMPLETE):
- ✅ `src/workers/pii_worker.py` - Background asyncio worker thread
- ✅ `src/services/pii_service.py` - PII detection orchestration
- ✅ `src/services/pii_analyzer.py` - Microsoft Presidio analyzer wrapper
- ✅ `src/services/pdf_extractor.py` - Docling PDF text extraction
- ✅ `src/utils/token_generator.py` - Secure approval token generation
- ✅ `src/main.py` - Worker lifecycle with FastAPI lifespan

**Implementation Notes**:
- Presidio successfully integrated with spaCy en_core_web_sm model
- Docling extracts text from PDFs (handles complex layouts, tables, OCR)
- PII detection with configurable confidence threshold (0.7 default)
- Worker runs as background asyncio task in monolith application
- Automatic routing: clean docs → processing queue, PII docs → approval queue
- Retry logic: 1 retry on PDF extraction failures
- Successfully tested: 604KB security paper → 55K chars extracted → 526 PII entities detected
- Dependencies: presidio-analyzer, spacy, docling, en-core-web-sm model

**Shared Services Used**: storage_service, queue_service, job_service

**Unblocks**: PRD-006 (Approval API)

---

### PRD-006: Approval Workflow API ✅
**Status**: Complete
**File**: [phase-2-services/PRD-006-approval-api.md](phase-2-services/PRD-006-approval-api.md)
**Completed**: 2025-10-01
**Effort**: 3.5 hours (actual vs 2 days estimated)
**Dependencies**: PRD-003 (Shared Services) ✅, PRD-005 (PII Worker) ✅

**Deliverables** (COMPLETE):
- ✅ `src/api/approval.py` - GET /api/review/{token}, POST /api/approve/{token}
- ✅ `src/services/approval_service.py` - Token validation, decision processing
- ✅ `src/services/cleanup_service.py` - S3 file cleanup for denied jobs
- ✅ `src/main.py` - Approval router registered
- ✅ `tests/api/test_approval_flow.py` - 7 integration tests passing
- ✅ `tests/api/test_approval_security.py` - 7 security tests passing
- ✅ `tests/services/test_approval_service.py` - 11 service tests passing
- ✅ `tests/services/test_cleanup_service.py` - 5 cleanup tests passing

**Implementation Notes**:
- Approval workflow endpoints functional with token-based security
- Token validation via Redis KEYS scan (O(N) - documented for optimization)
- Approved jobs route to processing queue with `ProcessingQueuePayload`
- Denied jobs trigger S3 cleanup and update status to "denied"
- Timeout tracking removal prevents race conditions with timeout worker (PRD-008)
- Idempotent S3 cleanup design (safe for double-cleanup scenarios)
- Input validation: justification 10-1000 chars, reviewed_by min 3 chars
- All 30 new tests passing (165 total tests passing)
- Security: Tokens not leaked in error messages, PII not in URLs
- OpenAPI docs auto-generated at /docs with approval endpoints

**Shared Services Used**: queue_service, job_service, storage_service (via cleanup_service)

**Unblocks**: PRD-007 (Processing Worker), PRD-008 (Timeout Worker), PRD-009 (Integration)

---

### PRD-007: Processing Worker ✅
**Status**: Complete
**File**: [phase-2-services/PRD-007-processing-worker.md](phase-2-services/PRD-007-processing-worker.md)
**Completed**: 2025-10-01
**Effort**: ~8 hours (actual vs 4 days estimated)
**Dependencies**: PRD-003 (Shared Services) ✅

**Deliverables** (COMPLETE):
- ✅ `src/workers/processing_worker.py` - Background worker with queue polling
- ✅ `src/services/processing_service.py` - Main orchestration service
- ✅ `src/services/pdf_converter.py` - Docling PDF→MD with page images
- ✅ `src/services/ai_enhancement_service.py` - Concurrent page processing (max 5)
- ✅ `src/agents/accessibility_agent.py` - Claude Haiku 3.5 agent via PydanticAI
- ✅ `src/utils/confidence_scoring.py` - Confidence aggregation utilities
- ✅ `config/accessibility_prompts.yaml` - Claude system prompts
- ✅ Updated `src/main.py` - Processing worker integrated into lifespan
- ✅ Updated `src/shared/models/processing.py` - Markdown-only output model
- ✅ Updated `src/config.py` - Claude AI configuration with SecretStr
- ✅ Updated `.env.dev` - Anthropic API key and processing settings
- ✅ Updated `pyproject.toml` - pydantic-ai-slim[anthropic]>=1.0.12

**Implementation Notes**:
- **Architecture**: Markdown-only output (no HTML/MDX rendering - handled by client)
- **AI Model**: Claude 3.5 Haiku via PydanticAI (cost-effective, fast)
- **Concurrent Processing**: Max 5 pages at once using asyncio.Semaphore
- **Retry Logic**: Up to 3 attempts per page with exponential backoff
- **Page Images**: Docling `generate_page_images=True` verified working (2x scale = 144 DPI)
- **Multimodal Input**: BinaryContent for base64 PNG images to Claude vision API
- **Versioned Output**: S3 storage with `results/{job_id}/v{timestamp}/output.md`
- **Confidence Scoring**: Per-page aggregation with high/medium/low classification
- **Error Handling**: Page-level failures reported with page number and error
- **Dependencies**: pydantic-ai-slim 1.0.12, PyYAML 6.0.3 (already transitive)
- **Both workers running**: PII worker + Processing worker in single monolith app

**Shared Services Used**: storage_service, queue_service, job_service

**Unblocks**: PRD-008 (Timeout Worker), PRD-009 (End-to-End Integration)

---

### PRD-008: Timeout Cleanup Worker ✅
**Status**: Complete
**File**: [phase-2-services/PRD-008-timeout-cleanup-worker.md](phase-2-services/PRD-008-timeout-cleanup-worker.md)
**Completed**: 2025-10-02
**Effort**: ~8 hours (actual vs 2 days estimated)
**Dependencies**: PRD-003 (Shared Services) ✅

**Deliverables** (COMPLETE):
- ✅ `src/workers/timeout_worker.py` - Background scheduler with asyncio task coordination
- ✅ `src/services/timeout_service.py` - Approval timeout monitoring (30s intervals)
- ✅ `src/services/s3_cleanup_service.py` - S3 temp file cleanup (hourly)
- ✅ `src/services/orphan_service.py` - Orphaned job detection (4h intervals)
- ✅ `src/services/metrics_service.py` - Daily metrics tracking and cleanup
- ✅ `src/main.py` - Timeout worker integrated into lifespan (3 workers running)
- ✅ `tests/services/test_timeout_monitoring.py` - 9 tests passing
- ✅ `tests/services/test_s3_cleanup.py` - 8 tests passing
- ✅ `tests/services/test_orphan_detection.py` - 18 tests passing
- ✅ `tests/workers/test_timeout_worker.py` - 14 tests passing

**Implementation Notes**:
- **Architecture**: Single worker with time-based task scheduling (not separate workers per task)
- **Scheduled Tasks**:
  - Approval timeouts: 30 seconds (updates job to "failed", removes temp files)
  - Temp file cleanup: 1 hour (deletes files older than 24h retention)
  - Orphaned jobs: 4 hours (cleans up old completed/failed jobs beyond 30 day retention)
  - Stuck jobs: 4 hours (fails jobs stuck in processing >2 hours)
  - Metrics cleanup: Daily (removes metrics older than 90 days)
- **Metrics Storage**: Redis hashes with date keys (eq-pdf:metrics:daily:YYYYMMDD)
- **Error Handling**: Task-level try/except with metric tracking, worker continues on failures
- **Timezone Handling**: Fixed naive datetime comparison issues in orphan detection
- **Integration**: Uses same dependency injection pattern as PII/Processing workers
- **All 49 new tests passing** (237 total tests passing)
- **Three workers running**: PII worker, Processing worker, Timeout worker
- **Zero regressions**: Existing tests remain green

**Shared Services Used**: storage_service, queue_service, job_service, metrics_service

**Unblocks**: PRD-009 (End-to-End Integration) - **ALL PHASE 2 PRDs NOW COMPLETE**

---

## Phase 3: Integration & Demo

### PRD-009A: Grafana Observability Stack
**Status**: Not Started
**File**: [phase-3-integration/PRD-009A-grafana-observability.md](phase-3-integration/PRD-009A-grafana-observability.md)
**Effort**: 6-8 hours
**Dependencies**: PRD-008 (Timeout Worker - all workers complete)
**Can run in parallel with**: PRD-009B

**Deliverables**:
- Prometheus metrics collection (OpenTelemetry)
- Grafana dashboards (System, Queues, Jobs, Workers)
- Redis exporter integration
- Docker Compose integration (Prometheus, Grafana, Redis exporter)
- Metrics middleware and instrumentation
- Production-ready observability stack

**Purpose**: Industry-standard monitoring for system health, queue depths, job processing, and worker status. Essential for debugging and production operations.

---

### PRD-009B: Demo REST API Testing UI ✅
**Status**: Complete
**File**: [phase-3-integration/PRD-009B-demo-rest-ui.md](phase-3-integration/PRD-009B-demo-rest-ui.md)
**Completed**: 2025-10-02
**Effort**: ~8 hours (actual vs 8-12 hours estimated)
**Dependencies**: PRD-004 (API Endpoints) ✅, PRD-006 (Approval API) ✅

**Deliverables** (COMPLETE):
- ✅ `frontend/demo-ui/` - Vite + React + TypeScript setup
- ✅ ShadCN UI components with UIC branding (navy #001e62, red #d50032)
- ✅ Document upload interface (file picker)
- ✅ Real-time job status tracking (React Query polling every 2s)
- ✅ PII review and approval interface (token-based routing)
- ✅ System monitoring dashboard (health, workers, optional queue metrics)
- ✅ Docker integration with hot reload (docker-compose.dev.yml)
- ✅ Mobile-responsive design (tested 375px, 768px, 1024px+)
- ✅ `src/api/dev_monitoring.py` - Optional dev-only queue metrics endpoint
- ✅ Backend integration - Dev monitoring router conditionally enabled
- ✅ Comprehensive README with demo script

**Implementation Notes**:
- Full React application with 4 pages: Dashboard, Job Detail, Approval Review, Monitoring
- Typed API client matching FastAPI OpenAPI spec
- React Query hooks for intelligent polling (stop on terminal states)
- Layout components: Header with "DEMO ONLY" badge, Sidebar navigation
- Document components: Upload, JobList, JobCard, JobDetail
- Monitoring components: SystemHealth, QueueMonitor, WorkerStatus
- Accessible UI with WCAG 2.1 AA compliance (Radix primitives)
- UIC branding throughout (OKLCH color tokens)
- Docker hot-reload verified working
- Dev monitoring endpoint `/api/dev/monitoring/queues` (dev-only)
- Zero regressions in existing 237 backend tests

**⚠️ NOTE**: This is a DEMO/DEVELOPER TOOL for testing and stakeholder presentations, NOT the production UIC interface. Production will use Canvas LMS integration.

**Purpose**: Better than Postman for API testing, stakeholder demos, and visual debugging of the document processing pipeline.

**Unblocks**: PRD-010 (End-to-End Integration) - UI ready for validation

---

### PRD-010: End-to-End Integration & Testing
**Status**: Not Started
**File**: [phase-3-integration/PRD-010-end-to-end-integration.md](phase-3-integration/PRD-010-end-to-end-integration.md)
**Effort**: 2 days
**Dependencies**: ALL Phase 2 PRDs complete (PRD-001 through PRD-008), PRD-009A (Grafana), PRD-009B (Demo UI)

**Deliverables**:
- End-to-end integration tests
- Performance benchmarking (2-8 min processing, $0.20/doc)
- Load testing (10+ concurrent documents)
- Error handling validation
- Production readiness checklist
- Documentation updates
- Final validation with Grafana metrics

**Purpose**: Final validation that entire system meets success criteria and is ready for AWS ECS deployment.

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

### Week 3: Integration & Demo
```
PRD-009A: Grafana Observability (6-8 hours)
PRD-009B: Demo REST UI (8-12 hours)
  → Can run in parallel (independent implementations)

PRD-010: End-to-End Integration (2 days)
  → Must run after 009A and 009B complete
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

### Phase 3 Dependencies Graph
```
PRD-008 (All workers complete)
    └─► PRD-009A: Grafana Observability

PRD-004 + PRD-006 (API complete)
    └─► PRD-009B: Demo REST UI

PRD-009A + PRD-009B
    └─► PRD-010: End-to-End Integration

Note: PRD-009A and PRD-009B can run in parallel
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
