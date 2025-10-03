# STORY-011: Test Separation and Marker Organization

**Priority:** MEDIUM
**Type:** Technical Debt / Developer Experience
**Discovered:** 2025-10-03 (Test Suite Review)
**Status:** PLANNED
**Effort:** Medium (8-12 hours)
**Phase:** Phase 2 - Services

---

## Problem Statement

The Equalify PDF Converter test suite has grown to **572 tests across 45 files** with no systematic organization or categorization. This creates several development workflow problems:

### Current State Issues

1. **No Test Categorization**
   - Unit, integration, and E2E tests are mixed together
   - No markers configured in `pyproject.toml`
   - Only `@pytest.mark.asyncio` and ad-hoc `@pytest.mark.integration` and `@pytest.mark.slow` used
   - Cannot run fast tests separately from slow tests

2. **Slow Development Cycle**
   - All 572 tests run on every execution
   - Integration tests requiring Redis/S3 always execute
   - No way to run "quick feedback" tests during development
   - CI/CD runs all tests on every push (inefficient)

3. **Resource Dependency Confusion**
   - No clear indication which tests need Redis
   - No clear indication which tests need S3/LocalStack
   - No clear indication which tests require AI API calls
   - Difficult to run tests in resource-constrained environments

4. **Directory Structure Misalignment**
   - `tests/integration/` contains some unit tests
   - `tests/edge_cases/` contains mixed test types
   - `tests/services/` contains integration tests
   - No clear separation by execution speed

5. **CI/CD Inefficiency**
   - Every push triggers full test suite (~5-10 minutes)
   - No fast feedback loop for simple changes
   - Integration tests run even for documentation updates
   - No progressive test execution strategy

### Business Impact

- **Developer Velocity:** Slow test feedback loop reduces iteration speed
- **CI/CD Costs:** Running all tests on every push wastes compute resources
- **Onboarding Friction:** New developers can't easily understand test organization
- **Test Reliability:** Mixing unit and integration tests reduces determinism
- **Development Experience:** Cannot quickly validate changes with fast tests

---

## Success Criteria

### Functional Requirements

1. **Test Markers Configured**
   - All tests categorized with appropriate markers
   - Marker definitions in `pyproject.toml`
   - Clear documentation for each marker type

2. **Directory Structure Reorganized**
   - Tests organized by speed and isolation level
   - Clear separation between unit/integration/E2E tests
   - Logical grouping by functionality

3. **Makefile Commands Available**
   - `make test-fast` runs unit tests only (<30s)
   - `make test-integration` runs integration tests (<2min)
   - `make test-all` runs complete suite
   - `make test-requires-redis`, `make test-requires-s3`, etc.

4. **CI/CD Workflow Optimized**
   - Fast tests run on every push
   - Integration tests run on PR creation
   - E2E tests run on merge to main
   - Separate workflows for each test tier

5. **Performance Targets Met**
   - Unit tests complete in <30 seconds
   - Integration tests complete in <2 minutes
   - Full suite completes in <10 minutes
   - 90% of common development workflows use fast tests

### Quality Requirements

- All 572 tests continue to pass
- No test behavior changes (only organization)
- Clear migration path with minimal disruption
- Documentation updated with new workflow

---

## Test Categorization Strategy

### Marker Definitions

#### @pytest.mark.unit
**Characteristics:**
- **Execution Time:** <100ms per test
- **Isolation:** Completely isolated, no external dependencies
- **Mocking:** All external services mocked (Redis, S3, AI)
- **Determinism:** 100% deterministic results
- **Purpose:** Validate business logic, algorithms, data transformations

**Examples:**
```python
@pytest.mark.unit
async def test_job_model_validation():
    """Test job model field validation."""
    # Pure Pydantic model testing, no I/O
```

```python
@pytest.mark.unit
async def test_timeout_calculation():
    """Test timeout timestamp calculation logic."""
    # Pure datetime logic, no external calls
```

**Target Count:** ~350 tests (60% of suite)
**Expected Runtime:** 20-30 seconds total

---

#### @pytest.mark.integration
**Characteristics:**
- **Execution Time:** <5 seconds per test
- **Isolation:** Uses real services (Redis, LocalStack S3)
- **Mocking:** External APIs mocked (Anthropic, Presidio)
- **Determinism:** High (uses containerized services)
- **Purpose:** Validate service interactions, queue operations, storage

**Examples:**
```python
@pytest.mark.integration
@pytest.mark.requires_redis
async def test_queue_pii_job_with_real_redis():
    """Test queuing PII job to real Redis."""
    # Uses actual Redis container
```

```python
@pytest.mark.integration
@pytest.mark.requires_s3
async def test_s3_upload_and_retrieve():
    """Test S3 upload and download flow."""
    # Uses LocalStack S3
```

**Target Count:** ~180 tests (31% of suite)
**Expected Runtime:** 60-90 seconds total

---

#### @pytest.mark.slow
**Characteristics:**
- **Execution Time:** >5 seconds per test
- **Isolation:** Full workflows across multiple services
- **Mocking:** Minimal mocking, mostly real services
- **Determinism:** Moderate (timing-sensitive)
- **Purpose:** Validate complete workflows, race conditions, timeouts

**Examples:**
```python
@pytest.mark.slow
@pytest.mark.requires_redis
@pytest.mark.requires_s3
async def test_full_worker_pipeline():
    """Test complete PDF processing pipeline."""
    # Submit → PII Worker → Processing Worker → Complete
```

```python
@pytest.mark.slow
async def test_concurrent_job_processing():
    """Test 100 concurrent job submissions."""
    # High-load scenario testing
```

**Target Count:** ~42 tests (7% of suite)
**Expected Runtime:** 3-5 minutes total

---

#### Resource Dependency Markers

##### @pytest.mark.requires_redis
Tests that require a running Redis instance (Docker or remote).

**Use Cases:**
- Queue operations (lpush, rpop, zadd)
- Job state management
- Rate limiting
- Timeout tracking

**Examples:**
- `test_queue_service.py` tests
- `test_redis_ttl.py` tests
- `test_timeout_monitoring.py` tests

**Target Count:** ~200 tests

---

##### @pytest.mark.requires_s3
Tests that require S3-compatible storage (LocalStack or AWS).

**Use Cases:**
- File upload/download
- Temp bucket operations
- Results bucket operations
- Storage service testing

**Examples:**
- `test_storage_service.py` tests
- `test_s3_cleanup.py` tests
- `test_s3_failures.py` tests

**Target Count:** ~120 tests

---

##### @pytest.mark.requires_ai
Tests that require AI API access (Anthropic Claude API).

**Use Cases:**
- AI enhancement testing
- Prompt engineering validation
- Response parsing
- Confidence scoring

**Examples:**
- PII false positive detection tests
- AI processing workflow tests
- Confidence threshold tests

**Target Count:** ~30 tests (mostly mocked in Phase 1)

**Note:** Most AI tests will be mocked until Phase 2+ when AI budget allows real API testing.

---

### Marker Combinations

Tests can have multiple markers to indicate all requirements:

```python
@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
async def test_worker_flow_with_storage():
    """Test worker processing with Redis and S3."""
    # Requires both Redis and S3
```

```python
@pytest.mark.slow
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.requires_ai
async def test_full_pipeline_with_real_ai():
    """Test complete pipeline with real AI processing."""
    # Most comprehensive test - requires all services
```

---

## Directory Restructuring Plan

### Current Structure (Problematic)
```
tests/
├── __init__.py
├── conftest.py                    # Root fixtures
├── test_documents.py              # Mixed unit/integration
├── test_health.py                 # API tests
├── api/                           # API endpoint tests
│   ├── test_status.py
│   └── test_submit.py
├── edge_cases/                    # Mixed unit/integration/slow
│   ├── test_config_validation.py
│   ├── test_invalid_pdfs.py
│   ├── test_large_files.py        # Slow tests!
│   ├── test_pii_accuracy.py
│   └── test_rate_limit_boundaries.py
├── integration/                   # E2E and integration mixed
│   ├── conftest.py
│   ├── test_concurrent_requests.py # Slow!
│   ├── test_malformed_payloads.py
│   ├── test_multi_worker.py       # Very slow!
│   ├── test_pii_false_positives.py
│   └── test_worker_flow.py
├── models/                        # Unit tests (good!)
│   ├── test_job_models.py
│   ├── test_queue_models.py
│   └── test_redis_integration.py  # Actually integration!
├── services/                      # Mixed unit/integration
│   ├── test_approval_service.py
│   ├── test_approval_workflow.py
│   ├── test_cleanup_service.py
│   ├── test_error_handling.py
│   ├── test_job_service.py
│   ├── test_orphan_detection.py
│   ├── test_production_environment.py
│   ├── test_queue_service.py
│   ├── test_rate_limit_service.py
│   ├── test_redis_failures.py
│   ├── test_redis_ttl.py
│   ├── test_resource_management.py
│   ├── test_retry_logic.py
│   ├── test_s3_cleanup.py
│   ├── test_s3_failures.py
│   ├── test_storage_service.py
│   ├── test_timeout_monitoring.py
│   └── test_timezone_consistency.py
└── workers/                       # Worker tests
    ├── test_pii_worker.py
    └── test_processing_worker.py
```

**Problems:**
- "Integration" directory contains E2E tests
- "Edge cases" contains slow tests (not edge cases)
- "Services" directory has 18 files with mixed test types
- No way to identify fast vs. slow tests by directory

---

### Proposed Structure (Organized by Speed & Type)

```
tests/
├── __init__.py
├── conftest.py                    # Root fixtures (shared)
│
├── unit/                          # FAST: <100ms per test, fully mocked
│   ├── __init__.py
│   ├── conftest.py                # Unit-specific fixtures
│   ├── models/                    # Pydantic model validation
│   │   ├── test_job_models.py
│   │   ├── test_queue_models.py
│   │   └── test_approval_models.py
│   ├── services/                  # Service logic (mocked dependencies)
│   │   ├── test_job_service_logic.py
│   │   ├── test_queue_service_logic.py
│   │   ├── test_approval_service_logic.py
│   │   ├── test_cleanup_service_logic.py
│   │   ├── test_rate_limit_logic.py
│   │   ├── test_timeout_calculations.py
│   │   └── test_timezone_consistency.py
│   ├── utils/                     # Helper functions, utilities
│   │   ├── test_config_validation.py
│   │   ├── test_redis_key_helpers.py
│   │   └── test_s3_key_helpers.py
│   └── workers/                   # Worker logic (mocked I/O)
│       ├── test_pii_worker_logic.py
│       └── test_processing_worker_logic.py
│
├── integration/                   # MEDIUM: <5s per test, real services
│   ├── __init__.py
│   ├── conftest.py                # Integration fixtures (Redis/S3 setup)
│   ├── api/                       # API endpoint integration
│   │   ├── test_health_endpoint.py
│   │   ├── test_status_endpoint.py
│   │   └── test_submit_endpoint.py
│   ├── redis/                     # Redis-specific integration
│   │   ├── test_queue_operations.py
│   │   ├── test_redis_ttl.py
│   │   ├── test_redis_failures.py
│   │   └── test_timeout_tracking.py
│   ├── s3/                        # S3-specific integration
│   │   ├── test_storage_operations.py
│   │   ├── test_s3_cleanup.py
│   │   └── test_s3_failures.py
│   ├── services/                  # Service integration (real Redis/S3)
│   │   ├── test_approval_workflow.py
│   │   ├── test_orphan_detection.py
│   │   ├── test_resource_management.py
│   │   └── test_retry_logic.py
│   └── workers/                   # Worker integration
│       ├── test_pii_worker_integration.py
│       └── test_processing_worker_integration.py
│
├── e2e/                           # SLOW: >5s per test, full workflows
│   ├── __init__.py
│   ├── conftest.py                # E2E fixtures (full stack)
│   ├── workflows/                 # Complete user workflows
│   │   ├── test_clean_pdf_workflow.py
│   │   ├── test_pii_approval_workflow.py
│   │   ├── test_pii_denial_workflow.py
│   │   └── test_timeout_workflow.py
│   ├── performance/               # Load and stress tests
│   │   ├── test_concurrent_requests.py
│   │   ├── test_large_files.py
│   │   └── test_rate_limit_boundaries.py
│   ├── resilience/                # Failure and recovery scenarios
│   │   ├── test_multi_worker_failures.py
│   │   ├── test_malformed_payloads.py
│   │   └── test_error_recovery.py
│   └── edge_cases/                # Rare but important scenarios
│       ├── test_invalid_pdfs.py
│       ├── test_pii_accuracy.py
│       └── test_pii_false_positives.py
│
└── fixtures/                      # Shared test data and fixtures
    ├── __init__.py
    ├── sample_pdfs/               # Test PDF files
    ├── sample_jobs.py             # Job test data generators
    └── sample_payloads.py         # Queue payload generators
```

**Benefits:**
- **Clear Speed Indicators:** Directory name indicates execution time
- **Logical Grouping:** Tests grouped by what they test (not what they need)
- **Easy Filtering:** Can run entire directories with `pytest tests/unit/`
- **Fixture Isolation:** Unit tests can't accidentally use integration fixtures
- **Discoverability:** New developers understand structure immediately

---

## Marker Configuration in pyproject.toml

### Updated pyproject.toml

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
pythonpath = ["src"]

# Test execution defaults
addopts = "-v --tb=short --strict-markers"

# Custom marker definitions
markers = [
    # Test type markers (execution speed and isolation)
    "unit: Fast unit tests (<100ms) with full mocking and no external dependencies",
    "integration: Medium integration tests (<5s) using real Redis/S3 but mocked external APIs",
    "slow: Slow end-to-end tests (>5s) with full workflows and minimal mocking",

    # Resource dependency markers
    "requires_redis: Tests requiring Redis container or instance",
    "requires_s3: Tests requiring S3-compatible storage (LocalStack or AWS)",
    "requires_ai: Tests requiring AI API access (Anthropic Claude)",

    # Special category markers
    "performance: Load testing, concurrency, and performance benchmarks",
    "resilience: Failure recovery and error handling scenarios",
    "edge_case: Rare scenarios and boundary conditions",
]

# Asyncio mode for all tests
asyncio_mode = "auto"

# Coverage settings
[tool.coverage.run]
source = ["src"]
omit = ["*/tests/*", "*/migrations/*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

### Marker Usage Examples

```python
# Unit test - fast and isolated
@pytest.mark.unit
async def test_job_model_validation():
    """Test job model field validation."""
    job = JobModel(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        status="pending"
    )
    assert job.job_id.version == 4


# Integration test - requires Redis
@pytest.mark.integration
@pytest.mark.requires_redis
async def test_queue_pii_job_redis(redis_client):
    """Test queuing PII job to real Redis."""
    queue_service = QueueService(redis_client)
    await queue_service.queue_pii_job(payload)
    # Verify job in Redis queue


# Slow E2E test - full workflow
@pytest.mark.slow
@pytest.mark.requires_redis
@pytest.mark.requires_s3
async def test_full_pdf_processing_workflow(
    api_client, redis_client, s3_client
):
    """Test complete PDF processing from upload to completion."""
    # Submit PDF
    response = await api_client.post("/api/convert", files={"file": pdf})
    job_id = response.json()["job_id"]

    # Wait for processing to complete
    await wait_for_job_completion(job_id, timeout=60)

    # Verify results in S3
    result = await s3_client.get_object(
        Bucket="results",
        Key=f"{job_id}/index.html"
    )
    assert result is not None


# Performance test
@pytest.mark.slow
@pytest.mark.performance
@pytest.mark.requires_redis
async def test_concurrent_job_submissions(api_client):
    """Test 100 concurrent job submissions."""
    async with asyncio.TaskGroup() as tg:
        for _ in range(100):
            tg.create_task(submit_job(api_client))
```

---

## CI/CD Workflow Updates

### Current GitHub Actions Workflow (Inefficient)

```yaml
# .github/workflows/test.yml (CURRENT - ALL TESTS ON EVERY PUSH)
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run all tests
        run: make test-docker  # Runs all 572 tests (~10 minutes)
```

**Problems:**
- Documentation changes trigger full test suite
- No fast feedback loop
- Wastes GitHub Actions minutes
- Slow PR review cycle

---

### Proposed Multi-Tier CI/CD Workflow

#### Tier 1: Fast Unit Tests (Every Push)

```yaml
# .github/workflows/test-fast.yml
name: Fast Tests
on: [push, pull_request]

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install uv
          uv sync

      - name: Run unit tests
        run: uv run pytest tests/unit/ -m unit -v
        # Expected: ~350 tests in <30 seconds

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        if: always()
        with:
          file: ./coverage.xml
          flags: unit
```

**Triggers:** Every push to any branch
**Expected Duration:** <2 minutes total
**Purpose:** Fast feedback for code changes

---

#### Tier 2: Integration Tests (Pull Requests)

```yaml
# .github/workflows/test-integration.yml
name: Integration Tests
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  integration-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    services:
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      localstack:
        image: localstack/localstack:latest
        ports:
          - 4566:4566
        env:
          SERVICES: s3
          DEBUG: 1

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install uv
          uv sync

      - name: Wait for services
        run: |
          ./scripts/wait-for-redis.sh
          ./scripts/wait-for-localstack.sh

      - name: Run integration tests
        env:
          REDIS_HOST: localhost
          REDIS_PORT: 6379
          AWS_ENDPOINT_URL: http://localhost:4566
        run: uv run pytest tests/integration/ -m integration -v
        # Expected: ~180 tests in 60-90 seconds

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        if: always()
        with:
          file: ./coverage.xml
          flags: integration
```

**Triggers:** PR opened, updated, or reopened
**Expected Duration:** <5 minutes total
**Purpose:** Validate service interactions before merge

---

#### Tier 3: E2E Tests (Main Branch Merges)

```yaml
# .github/workflows/test-e2e.yml
name: E2E Tests
on:
  push:
    branches: [main]
  workflow_dispatch:  # Allow manual trigger

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Start Docker Compose stack
        run: make dev

      - name: Wait for services
        run: |
          ./scripts/wait-for-api.sh
          ./scripts/wait-for-workers.sh

      - name: Run E2E tests
        run: make test-docker-e2e
        # Runs tests/e2e/ directory
        # Expected: ~42 tests in 3-5 minutes

      - name: Collect logs on failure
        if: failure()
        run: |
          docker-compose logs > docker-logs.txt

      - name: Upload logs
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: docker-logs
          path: docker-logs.txt

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        if: always()
        with:
          file: ./coverage.xml
          flags: e2e
```

**Triggers:**
- Push to `main` branch (after PR merge)
- Manual workflow dispatch

**Expected Duration:** <10 minutes total
**Purpose:** Full system validation before deployment

---

#### Tier 4: Performance Tests (Weekly)

```yaml
# .github/workflows/test-performance.yml
name: Performance Tests
on:
  schedule:
    - cron: '0 2 * * 0'  # Every Sunday at 2 AM UTC
  workflow_dispatch:

jobs:
  performance-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Start Docker Compose stack
        run: make dev

      - name: Run performance tests
        run: make test-performance
        # Runs tests marked with @pytest.mark.performance

      - name: Generate performance report
        run: |
          python scripts/generate_performance_report.py

      - name: Upload performance report
        uses: actions/upload-artifact@v4
        with:
          name: performance-report
          path: performance-report.html

      - name: Comment on degradation
        if: failure()
        uses: peter-evans/create-issue-from-file@v5
        with:
          title: Performance Degradation Detected
          content-filepath: performance-issues.md
          labels: performance, bug
```

**Triggers:**
- Weekly schedule (Sunday 2 AM UTC)
- Manual workflow dispatch

**Expected Duration:** <20 minutes total
**Purpose:** Track performance trends and catch regressions

---

## Developer Workflow Commands

### Updated Makefile

Add new test commands to existing Makefile:

```makefile
# Test commands (add to existing Makefile after line 72)

# Fast unit tests only (no Docker required for most)
test-fast:
	uv run pytest tests/unit/ -m unit -v --tb=short
	@echo "✅ Fast tests complete (~30s)"

# Integration tests (requires Docker services)
test-integration:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest tests/integration/ -m integration -v
	@echo "✅ Integration tests complete (~2min)"

# E2E tests (requires full stack)
test-e2e:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest tests/e2e/ -m slow -v
	@echo "✅ E2E tests complete (~5min)"

# Performance tests
test-performance:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest tests/e2e/performance/ -m performance -v
	@echo "✅ Performance tests complete"

# Run all tests (replaces existing `make test-docker`)
test-all:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest tests/ -v
	@echo "✅ All tests complete (~10min)"

# Run tests by resource requirement
test-requires-redis:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest -m requires_redis -v

test-requires-s3:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest -m requires_s3 -v

test-requires-ai:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest -m requires_ai -v

# Coverage report with test type breakdown
test-coverage:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway \
		uv run pytest tests/ --cov=src --cov-report=html --cov-report=term
	@echo "📊 Coverage report: htmlcov/index.html"

# List all available test markers
test-markers:
	@echo "Available test markers:"
	@uv run pytest --markers | grep "mark\." | grep -E "(unit|integration|slow|requires_)"
```

### Developer Workflow Examples

#### Scenario 1: Fixing a Bug in JobService

```bash
# 1. Make code changes in src/services/job_service.py

# 2. Run fast unit tests for quick feedback
make test-fast  # ~30 seconds
# OR target specific file:
uv run pytest tests/unit/services/test_job_service_logic.py -v

# 3. Run integration tests to verify Redis interaction
make test-integration  # ~2 minutes
# OR target specific tests:
uv run pytest tests/integration/services/ -m "integration and requires_redis" -v

# 4. If all pass, commit and push
git add . && git commit -m "fix: job service null handling"
git push  # Triggers fast CI tests
```

**Time Saved:** 30s fast tests vs 10min full suite = 9.5min saved per iteration

---

#### Scenario 2: Adding New API Endpoint

```bash
# 1. Implement endpoint in src/api/routes.py
# 2. Add unit tests in tests/unit/api/test_new_endpoint.py

# 3. Run unit tests
make test-fast  # Verify logic

# 4. Add integration tests in tests/integration/api/test_new_endpoint.py

# 5. Run integration tests
make test-integration  # Verify with real services

# 6. Add E2E test in tests/e2e/workflows/test_new_endpoint_workflow.py

# 7. Run E2E tests
make test-e2e  # Full workflow validation

# 8. Open PR - CI runs appropriate tier tests
```

---

#### Scenario 3: Refactoring Queue Service

```bash
# 1. Make refactoring changes

# 2. Run Redis-related tests specifically
make test-requires-redis

# 3. If pass, run full integration suite
make test-integration

# 4. Run all tests before pushing
make test-all
```

---

## Migration Plan

### Phase 1: Add Markers (No File Movement) - 2 hours

**Goal:** Configure markers and add to existing tests without moving files.

1. **Update pyproject.toml**
   - Add marker definitions (as shown above)
   - Update `addopts` with `--strict-markers`
   - Test configuration: `uv run pytest --markers`

2. **Audit Existing Tests**
   - Create inventory of 572 tests by type:
     ```bash
     grep -r "def test_" tests/ --include="*.py" | wc -l  # Count tests
     grep -r "AsyncMock\|mocker" tests/ --include="*.py" | wc -l  # Count mocked
     grep -r "redis_client\|RedisClient" tests/ --include="*.py" | wc -l  # Count Redis
     ```

3. **Add Markers Progressively**
   - Start with `tests/models/` (easiest - mostly unit tests)
   - Then `tests/services/` (mixed - requires analysis)
   - Then `tests/integration/` (mostly integration/slow)
   - Finally `tests/edge_cases/` (mixed types)

4. **Marker Addition Script**
   Create `scripts/add_test_markers.py` to help automate:
   ```python
   """
   Script to suggest markers for test files based on:
   - Presence of AsyncMock/mocker → likely unit test
   - Redis/S3 fixtures → integration test
   - asyncio.sleep() or long timeouts → slow test
   """
   ```

5. **Verify Markers Work**
   ```bash
   # Test marker filtering
   uv run pytest -m unit --collect-only  # Should find ~350 tests
   uv run pytest -m integration --collect-only  # Should find ~180 tests
   uv run pytest -m slow --collect-only  # Should find ~42 tests
   ```

**Deliverable:** All tests have appropriate markers, no file moves yet.

---

### Phase 2: Update CI/CD Workflows - 1 hour

**Goal:** Implement multi-tier CI/CD with existing directory structure.

1. **Create Fast Test Workflow**
   - `.github/workflows/test-fast.yml`
   - Runs on every push
   - Target: `pytest -m unit`

2. **Create Integration Workflow**
   - `.github/workflows/test-integration.yml`
   - Runs on PR open/update
   - Spins up Redis and LocalStack services
   - Target: `pytest -m integration`

3. **Update Existing Workflow**
   - Rename `.github/workflows/test.yml` → `test-e2e.yml`
   - Run only on merge to main
   - Target: `pytest -m slow`

4. **Add Performance Workflow**
   - `.github/workflows/test-performance.yml`
   - Weekly schedule
   - Target: `pytest -m performance`

5. **Test CI Workflows**
   - Create test PR to verify workflows trigger correctly
   - Verify each tier runs expected number of tests
   - Check execution times meet targets

**Deliverable:** Multi-tier CI/CD running, faster feedback loop.

---

### Phase 3: Update Makefile Commands - 30 minutes

**Goal:** Add developer workflow commands.

1. **Add Test Commands**
   - `make test-fast`
   - `make test-integration`
   - `make test-e2e`
   - `make test-all`
   - Resource-specific commands

2. **Update Help Text**
   - Document new commands in `make help`

3. **Update README**
   - Add "Running Tests" section
   - Document marker usage
   - Show workflow examples

**Deliverable:** Developers can run test subsets easily.

---

### Phase 4: Restructure Directories (Optional) - 4-6 hours

**Goal:** Reorganize test files into new directory structure.

**Note:** This phase is OPTIONAL and can be deferred. Markers alone provide 80% of the benefit.

1. **Create New Directory Structure**
   ```bash
   mkdir -p tests/unit/{models,services,utils,workers}
   mkdir -p tests/integration/{api,redis,s3,services,workers}
   mkdir -p tests/e2e/{workflows,performance,resilience,edge_cases}
   mkdir -p tests/fixtures
   ```

2. **Move Tests Progressively**
   - Start with clear cases (models → unit/)
   - Move integration tests requiring Redis to integration/redis/
   - Move slow E2E tests to e2e/workflows/
   - Update imports in moved files

3. **Update conftest.py Files**
   - Create `tests/unit/conftest.py` with mock fixtures
   - Create `tests/integration/conftest.py` with real service fixtures
   - Create `tests/e2e/conftest.py` with full stack fixtures
   - Update root `tests/conftest.py` for shared fixtures

4. **Run Tests After Each Move**
   ```bash
   # After moving each file/directory:
   make test-all  # Verify no breaks
   ```

5. **Update CI/CD Paths**
   - Update workflow files to use new paths if needed
   - Update coverage configuration

**Deliverable:** Tests organized by speed and type in clear directory structure.

---

### Phase 5: Documentation and Training - 1 hour

**Goal:** Ensure team understands new test organization.

1. **Update Documentation**
   - `README.md` - Add "Running Tests" section
   - `CONTRIBUTING.md` - Add test categorization guidelines
   - `docs/testing.md` - Detailed testing strategy document

2. **Create Test Writing Guidelines**
   ```markdown
   # When to Use Each Test Type

   ## Write Unit Tests When:
   - Testing pure functions or business logic
   - No I/O operations needed
   - Can fully mock dependencies
   - Want fast feedback (<100ms)

   ## Write Integration Tests When:
   - Testing service interactions
   - Need real Redis or S3
   - Testing queue operations
   - Can accept 1-5s execution time

   ## Write E2E Tests When:
   - Testing complete workflows
   - Need multiple workers coordinating
   - Testing race conditions or timing
   - Can accept >5s execution time
   ```

3. **Add Examples to Codebase**
   - Create `tests/examples/` directory
   - Add example test files for each type
   - Show marker usage patterns

4. **Team Communication**
   - Announce new test workflow in team meeting
   - Share this PRD with team
   - Demo new `make test-*` commands

**Deliverable:** Team trained on new test workflow, documentation complete.

---

## Performance Targets

### Test Execution Time Targets

| Test Category | Target Time | Max Time | Current Baseline |
|---------------|-------------|----------|------------------|
| Unit Tests (350 tests) | 20s | 30s | TBD (currently mixed) |
| Integration Tests (180 tests) | 60s | 120s | TBD (currently mixed) |
| E2E Tests (42 tests) | 180s | 300s | TBD (currently mixed) |
| Full Suite (572 tests) | 300s | 600s | ~600s (10min) |

### CI/CD Pipeline Targets

| Pipeline | Trigger | Target Time | Max Time |
|----------|---------|-------------|----------|
| Fast Tests | Every push | 2min | 5min |
| Integration Tests | PR open/update | 5min | 10min |
| E2E Tests | Merge to main | 10min | 15min |
| Performance Tests | Weekly | 20min | 30min |

### Developer Workflow Targets

| Workflow | Target Time | Improvement |
|----------|-------------|-------------|
| Quick validation | 30s | 95% faster (vs 10min full suite) |
| Service change validation | 2min | 80% faster |
| Full validation before push | 10min | Same (but optional) |

---

## Edge Cases and Considerations

### Edge Case 1: Tests with Mixed Requirements

**Scenario:** Test validates unit logic but also needs Redis for fixture data.

**Solution:** Split into two tests:
```python
# Unit test - pure logic
@pytest.mark.unit
async def test_job_validation_logic():
    """Test job validation without Redis."""
    job_data = {"job_id": "...", "status": "pending"}
    result = validate_job(job_data)
    assert result.is_valid

# Integration test - with Redis
@pytest.mark.integration
@pytest.mark.requires_redis
async def test_job_validation_with_redis_lookup(redis_client):
    """Test job validation with Redis state lookup."""
    # Create job in Redis
    await redis_client.hset("eq-pdf:job:123", mapping={"status": "pending"})

    # Validate with Redis lookup
    result = await validate_job_with_state("123", redis_client)
    assert result.is_valid
```

---

### Edge Case 2: Flaky Tests Due to Timing

**Scenario:** Test sometimes fails due to timing (worker not ready).

**Solution:** Mark as `@pytest.mark.slow` and add robust waiting:
```python
@pytest.mark.slow
@pytest.mark.requires_redis
async def test_worker_processes_job(worker, redis_client):
    """Test worker processes job (with timeout)."""
    # Queue job
    await redis_client.lpush("eq-pdf:pii_queue", json.dumps(payload))

    # Wait for processing with timeout
    async def wait_for_completion():
        for _ in range(30):  # 30 seconds max
            job = await get_job(job_id)
            if job["status"] == "completed":
                return True
            await asyncio.sleep(1)
        return False

    completed = await wait_for_completion()
    assert completed, "Job did not complete within 30 seconds"
```

**Alternative:** Move to E2E test category if truly workflow-oriented.

---

### Edge Case 3: Tests Requiring AI API (Expensive)

**Scenario:** Test needs real AI API calls but API calls are expensive.

**Solution:** Mock by default, allow override with environment variable:
```python
@pytest.mark.integration
@pytest.mark.requires_ai
async def test_ai_enhancement(mock_ai_client):
    """Test AI enhancement (mocked by default)."""
    if os.getenv("USE_REAL_AI") == "true":
        # Use real AI client (only in special CI runs)
        client = AnthropicAIClient(api_key=os.getenv("ANTHROPIC_API_KEY"))
    else:
        # Use mock (default)
        client = mock_ai_client

    result = await client.enhance_accessibility(text)
    assert result.confidence > 0.8
```

**CI Strategy:**
- Regular CI: Uses mock (fast, free)
- Weekly AI validation: Sets `USE_REAL_AI=true` (slow, costs money)

---

### Edge Case 4: Local Development Without Docker

**Scenario:** Developer wants to run unit tests without starting Docker.

**Solution:** Unit tests should work without Docker:
```bash
# Install dependencies locally
uv sync

# Run unit tests directly (no Docker needed)
uv run pytest tests/unit/ -m unit -v

# Integration/E2E tests require Docker
make dev  # Start Docker stack
make test-integration  # Run integration tests
```

**Requirement:** Unit tests must not require Redis/S3/external services.

---

## Rollback Plan

If test reorganization causes problems:

### Immediate Rollback (< 5 minutes)

1. **Revert Commits**
   ```bash
   git revert HEAD~N  # Revert last N commits
   git push
   ```

2. **Restore CI Workflows**
   - Revert workflow file changes
   - Restore original `test.yml`

3. **Restore Makefile**
   - Revert Makefile changes
   - Keep only `make test-docker` command

### Partial Rollback (Keep Markers, Undo Directory Changes)

If directory restructuring causes issues but markers work:

1. **Keep Marker Configuration**
   - Keep `pyproject.toml` changes
   - Keep marker decorators on tests

2. **Revert Directory Changes**
   - Move files back to original locations
   - Update imports
   - Keep old directory structure

3. **Keep CI Improvements**
   - Keep multi-tier workflows
   - Use markers with old directory structure

**Advantage:** Still get 80% of benefit (fast CI) without directory churn.

---

## Success Metrics

### Quantitative Metrics

1. **Test Execution Time**
   - Fast tests complete in <30s (vs current ~600s for subset)
   - Integration tests complete in <2min
   - Full suite completes in <10min

2. **CI/CD Performance**
   - Average PR CI time: <5min (vs current ~10min)
   - Fast test feedback: <2min from push
   - CI compute cost reduction: 60% (fewer full suite runs)

3. **Developer Productivity**
   - Development cycle iterations: 10x faster (30s vs 10min)
   - PRs merged faster: 40% improvement (faster CI)
   - Developer satisfaction: >80% positive feedback

### Qualitative Metrics

1. **Code Quality**
   - More tests written (lower barrier with fast feedback)
   - Better test coverage (easier to target gaps)
   - Fewer flaky tests (clear categorization)

2. **Developer Experience**
   - Onboarding time reduced (clear test structure)
   - Confidence in changes (quick validation)
   - Better debugging (can isolate test types)

3. **Maintainability**
   - Clear test organization
   - Easy to find relevant tests
   - Logical fixture management

---

## Dependencies and Blockers

### Dependencies
- None - can implement immediately

### Blockers
- None identified

### Related Work
- BUG-006: Test Suite Failures - Should be fixed first
- STORY-010: Observability improvements - Complements test categorization

---

## Acceptance Criteria

### Phase 1: Markers Added
- [ ] `pyproject.toml` updated with marker definitions
- [ ] All 572 tests have appropriate markers
- [ ] `pytest -m unit --collect-only` finds ~350 tests
- [ ] `pytest -m integration --collect-only` finds ~180 tests
- [ ] `pytest -m slow --collect-only` finds ~42 tests
- [ ] All tests still pass with markers added

### Phase 2: CI/CD Updated
- [ ] Fast test workflow runs on every push
- [ ] Integration test workflow runs on PR open/update
- [ ] E2E test workflow runs on merge to main
- [ ] Performance test workflow runs weekly
- [ ] Each workflow completes within target time
- [ ] Test PR validates all workflows work

### Phase 3: Makefile Updated
- [ ] `make test-fast` runs unit tests (<30s)
- [ ] `make test-integration` runs integration tests (<2min)
- [ ] `make test-e2e` runs E2E tests (<5min)
- [ ] `make test-all` runs full suite (<10min)
- [ ] `make test-requires-redis` filters Redis tests
- [ ] `make help` documents all new commands
- [ ] README updated with test workflow section

### Phase 4: Directory Restructure (Optional)
- [ ] New directory structure created
- [ ] Tests moved to appropriate directories
- [ ] conftest.py files updated with directory-specific fixtures
- [ ] All 572 tests still pass after moves
- [ ] Imports updated correctly
- [ ] CI workflows updated for new paths

### Phase 5: Documentation Complete
- [ ] `README.md` updated with testing section
- [ ] `CONTRIBUTING.md` has test categorization guidelines
- [ ] `docs/testing.md` created with detailed strategy
- [ ] Example tests created in `tests/examples/`
- [ ] Team trained on new workflow
- [ ] Migration guide published

---

## Definition of Done

- [x] PRD reviewed and approved by team
- [ ] All 5 migration phases completed
- [ ] All 572 tests pass with new organization
- [ ] CI/CD workflows operational and meeting time targets
- [ ] Developer commands documented and working
- [ ] Team trained on new test workflow
- [ ] Documentation complete and published
- [ ] Success metrics tracked and meeting targets
- [ ] No regressions in test coverage or reliability

---

## Timeline Estimate

| Phase | Description | Estimated Time | Dependencies |
|-------|-------------|----------------|--------------|
| Phase 1 | Add markers to existing tests | 2 hours | None |
| Phase 2 | Update CI/CD workflows | 1 hour | Phase 1 complete |
| Phase 3 | Update Makefile commands | 30 minutes | Phase 1 complete |
| Phase 4 | Restructure directories (optional) | 4-6 hours | Phase 1-3 complete |
| Phase 5 | Documentation and training | 1 hour | All phases complete |
| **Total** | **Complete implementation** | **8-12 hours** | |

**Recommended Approach:** Implement Phases 1-3 first (3.5 hours) to get immediate benefits, defer Phase 4 (directory restructure) until team validates marker-based workflow.

---

## Related Documentation

- [BUG-006: Test Suite Failures](./BUG-006-test-suite-failures.md) - Fix before reorganization
- [CLAUDE.md](../../CLAUDE.md) - Project overview and containerized development workflow
- [Makefile](../../Makefile) - Current test commands
- [pyproject.toml](../../pyproject.toml) - Current pytest configuration

---

## Appendix: Test Categorization Decision Tree

```
Is this test testing business logic/pure functions?
├─ YES → Can you fully mock all dependencies?
│  ├─ YES → @pytest.mark.unit
│  └─ NO → Does it need Redis or S3?
│     ├─ Redis → @pytest.mark.integration + @pytest.mark.requires_redis
│     └─ S3 → @pytest.mark.integration + @pytest.mark.requires_s3
└─ NO → Is this testing a complete workflow?
   ├─ YES → Does it take >5 seconds?
   │  ├─ YES → @pytest.mark.slow + resource markers
   │  └─ NO → @pytest.mark.integration + resource markers
   └─ NO → Is this testing service interactions?
      └─ YES → @pytest.mark.integration + resource markers
```

**Special Cases:**
- **Performance tests:** Always `@pytest.mark.performance` + `@pytest.mark.slow`
- **Failure recovery:** `@pytest.mark.resilience` + appropriate speed marker
- **Boundary conditions:** `@pytest.mark.edge_case` + appropriate speed marker
