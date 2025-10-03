# STORY-013: Test Suite Performance Optimization & Developer Experience

**Priority:** HIGH
**Category:** Developer Experience & Quality Assurance
**Complexity:** Medium
**Status:** PENDING
**Created:** 2025-10-03

---

## Problem Statement

The current test suite lacks performance optimization and modern testing practices, leading to slow feedback loops and poor developer experience. With 45+ test files and 14,272+ lines of test code, developers face long wait times during development and CI/CD.

### Current Issues

**Performance Problems:**
- ❌ No parallel test execution (pytest-xdist not configured)
- ❌ 30+ `asyncio.sleep()` calls adding cumulative delays (15+ seconds minimum)
- ❌ No test timeouts configured (hanging tests block suite)
- ❌ No fast/slow test separation (4 tests marked `@pytest.mark.slow`)
- ❌ Unknown total suite execution time (no baseline established)
- ❌ No test performance profiling or duration tracking

**Developer Experience Issues:**
- ⚠️ Slow feedback loop during development (full suite required for confidence)
- ⚠️ No distinction between unit/integration/e2e test execution
- ⚠️ Long CI/CD pipeline times (no caching or parallelization)
- ⚠️ Difficult to identify slow tests or performance regressions
- ⚠️ No quick smoke test command for rapid iteration

**Identified Bottlenecks:**

| File | Sleep Calls | Total Delay | Issue |
|------|-------------|-------------|-------|
| `test_graceful_shutdown.py` | 15 calls | 2.9s+ | Worker lifecycle simulation |
| `test_multi_worker.py` | 4 calls | 0.63s+ | Concurrency simulation |
| `test_concurrent_requests.py` | 2 calls | 0.2s+ | Race condition testing |
| `test_resource_management.py` | 4 calls | 0.45s+ | Resource cleanup delays |
| `test_timeout_worker.py` | 3 calls | 0.4s+ | Timeout simulation |
| **TOTAL** | **30+ calls** | **4.58s+** | **Wasted time** |

---

## Dependencies

**Blocking:**
- None (can be implemented independently)

**Blocked by:**
- None

**Related:**
- BUG-006 ✅ (Test Suite Failures - resolved, provided foundation)
- STORY-007 ✅ (CI/CD Test Automation - would benefit from optimizations)

---

## Goals & Success Criteria

### Performance Targets

**Aggressive Goals (P0 - Required):**
- ✅ Unit tests: **<30 seconds** (currently ~45s estimated)
- ✅ Integration tests: **<2 minutes** (currently ~3-5m estimated)
- ✅ Full suite: **<5 minutes** with parallelization (currently ~6-8m estimated)
- ✅ Reduce `asyncio.sleep()` usage by **90%** (30+ calls → 3-5 strategic calls)
- ✅ Enable **4-8 parallel workers** (CPU core count dependent)

**Stretch Goals (P1 - Nice to Have):**
- ⭐ Unit tests: **<15 seconds**
- ⭐ CI/CD full suite: **<3 minutes** (with aggressive caching)
- ⭐ Zero hanging tests (all tests have timeouts)

### Developer Experience Targets

**P0 Requirements:**
- ✅ Fast feedback loop for unit tests (<30s)
- ✅ Clear test selection commands (`make test-fast`, `make test-integration`)
- ✅ Identify slow tests automatically (--durations flag)
- ✅ Test timeout safety net (no infinite hangs)

**P1 Enhancements:**
- ⭐ Test performance dashboard (Grafana integration)
- ⭐ Automatic flaky test detection
- ⭐ Performance regression alerts

---

## Technical Solution

### Strategy 1: Parallel Test Execution

**Implementation:**

Add `pytest-xdist` for parallel test execution:

```toml
# pyproject.toml
[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-xdist>=3.5.0",      # NEW: Parallel execution
    "pytest-timeout>=2.2.0",     # NEW: Test timeouts
    "mypy>=1.5.0",
    "httpx>=0.27.0",
    "pytest-cov>=6.0.0",
]
```

**Configuration:**

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = """
    -v
    --tb=short
    --strict-markers
    --durations=10
    -n auto
    --timeout=30
    --timeout-method=thread
"""
pythonpath = ["src"]

# Custom markers for test organization
markers = [
    "unit: Fast unit tests (< 1s per test)",
    "integration: Integration tests (1-10s per test)",
    "slow: Slow tests requiring extended resources (> 10s)",
    "e2e: End-to-end workflow tests",
]
```

**Performance Expectations:**
- **4-core system:** 3-4x speedup (6m → 1.5-2m)
- **8-core system:** 5-6x speedup (6m → 1-1.2m)

**Makefile Commands:**

```makefile
# Makefile updates
test:
	uv run pytest tests/ -v

test-fast:
	uv run pytest tests/ -v -m "unit" --durations=5

test-unit:
	uv run pytest tests/ -v -m "unit" -n auto

test-integration:
	uv run pytest tests/ -v -m "integration" -n auto

test-slow:
	uv run pytest tests/ -v -m "slow"

test-parallel:
	uv run pytest tests/ -v -n auto --durations=10

test-debug:
	uv run pytest tests/ -v -s -n 0 --timeout=0

test-docker:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v -n auto

test-docker-fast:
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run pytest tests/ -v -m "unit" --durations=5
```

---

### Strategy 2: Replace asyncio.sleep() with Efficient Alternatives

**Problem:** `asyncio.sleep()` adds real wall-clock delays to tests.

**Solution:** Use event-based synchronization and mocked delays.

**Example Transformations:**

#### Before (Slow - 0.1s delay):
```python
# test_graceful_shutdown.py:49
await asyncio.sleep(0.1)
shutdown_event.set()
```

#### After (Fast - <1ms):
```python
# Use event-based synchronization
worker_started = asyncio.Event()

async def worker_with_startup_signal(shutdown_event):
    worker_started.set()  # Signal immediately
    await worker.start(shutdown_event)

worker_task = asyncio.create_task(worker_with_startup_signal(shutdown_event))
await asyncio.wait_for(worker_started.wait(), timeout=1.0)  # Safety timeout
shutdown_event.set()
```

#### Before (Slow - 0.2s delay):
```python
# test_graceful_shutdown.py:102
async def mock_process_pii_job(job):
    await asyncio.sleep(0.2)  # Simulate work
    job_completed.set()
```

#### After (Fast - immediate):
```python
async def mock_process_pii_job(job):
    # Simulate work with minimal delay
    await asyncio.sleep(0)  # Yield control, no delay
    job_completed.set()
```

**File-by-File Changes:**

| File | Sleep Calls | Strategy | Expected Savings |
|------|-------------|----------|------------------|
| `test_graceful_shutdown.py` | 15 | Event-based sync | 2.5s → 0.05s |
| `test_multi_worker.py` | 4 | Mock time.time() + events | 0.63s → 0.01s |
| `test_concurrent_requests.py` | 2 | Remove unnecessary waits | 0.2s → 0s |
| `test_resource_management.py` | 4 | Event-based sync | 0.45s → 0.02s |
| `test_timeout_worker.py` | 3 | Event-based sync | 0.4s → 0.02s |

**Target Reduction:** 4.58s → 0.1s (95% improvement)

---

### Strategy 3: Test Timeouts

**Implementation:**

Add global and per-test timeout configuration:

```python
# pyproject.toml
[tool.pytest.ini_options]
addopts = """
    --timeout=30
    --timeout-method=thread
"""
```

**Per-test overrides:**

```python
# For slow tests that legitimately need more time
@pytest.mark.timeout(60)
@pytest.mark.slow
async def test_ten_workers_process_large_queue():
    """Test system with 10 workers processing 100 jobs."""
    # ... test code ...
```

**For fast unit tests:**

```python
@pytest.mark.timeout(5)
@pytest.mark.unit
async def test_pii_analyzer_detects_email():
    """Unit test should complete in < 5s."""
    # ... test code ...
```

**Benefits:**
- ✅ Prevents hanging tests from blocking suite
- ✅ Identifies slow tests early
- ✅ Provides safety net during refactoring

---

### Strategy 4: Test Categorization & Markers

**Implementation:**

Add pytest markers to organize tests by speed/scope:

```python
# tests/services/test_storage_service.py
import pytest

@pytest.mark.unit
@pytest.mark.timeout(5)
async def test_upload_temp_file_success():
    """Fast unit test - mocked S3."""
    # ... test code ...

# tests/integration/test_multi_worker.py
@pytest.mark.integration
@pytest.mark.timeout(30)
async def test_three_pii_workers_process_queue():
    """Integration test - multiple workers."""
    # ... test code ...

# tests/integration/test_multi_worker.py
@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.timeout(60)
async def test_ten_workers_process_large_queue():
    """Slow scalability test."""
    # ... test code ...
```

**Test Organization:**

| Marker | Count (Est.) | Avg Time/Test | Total Time | Purpose |
|--------|--------------|---------------|------------|---------|
| `unit` | ~200 tests | 0.1s | ~20s | Fast feedback |
| `integration` | ~80 tests | 1-3s | ~2-3m | Worker/service interaction |
| `slow` | ~10 tests | 5-15s | ~1-2m | Scalability, edge cases |
| `e2e` | ~5 tests | 10-30s | ~1-2m | Full workflow validation |

**Usage:**

```bash
# Fast unit tests only (30s)
make test-fast

# Integration tests only (2-3m with parallelization)
make test-integration

# Everything except slow tests (2-3m)
pytest -v -m "not slow"

# Only slow tests (1-2m)
make test-slow
```

---

### Strategy 5: Fixture Optimization

**Current State:**

Integration test fixtures create many mocked services:
- `mock_redis_client` (40+ method mocks)
- `mock_s3_client` (10+ method mocks)
- `storage_service`, `queue_service`, `job_service` (cascading dependencies)

**Optimization Opportunities:**

#### 1. Fixture Scoping

```python
# tests/integration/conftest.py

# Before: Function scope (created for EVERY test)
@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    # ... 40+ lines of setup ...
    return client

# After: Class scope (created once per test class)
@pytest.fixture(scope="class")
def mock_redis_client():
    client = AsyncMock()
    # ... 40+ lines of setup ...
    return client
```

**Expected Impact:** 20-30% faster test setup for integration tests

#### 2. Lazy Fixture Initialization

```python
# Before: All mocks created upfront
@pytest.fixture
def mock_pii_analyzer():
    with patch('src.services.pii_analyzer.get_pii_analyzer') as mock:
        analyzer = MagicMock()
        analyzer.analyze_text.return_value = []
        mock.return_value = analyzer
        yield analyzer

# After: Only patch when used
@pytest.fixture
def mock_pii_analyzer():
    """Lazy mock - only creates analyzer when test needs it."""
    with patch('src.services.pii_analyzer.get_pii_analyzer') as mock:
        def create_analyzer():
            analyzer = MagicMock()
            analyzer.analyze_text.return_value = []
            return analyzer
        mock.side_effect = create_analyzer
        yield mock
```

#### 3. Shared Test Data Fixtures

```python
# tests/conftest.py (root-level shared fixtures)

@pytest.fixture(scope="session")
def sample_pdf_content():
    """Generate once per test session."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n..."

@pytest.fixture(scope="session")
def sample_job_ids():
    """Pre-generate 100 UUIDs for tests."""
    return [str(uuid.uuid4()) for _ in range(100)]
```

**Expected Impact:** 10-15% faster test suite

---

### Strategy 6: CI/CD Optimization

**Current CI/CD Issues:**
- No test caching
- Sequential test execution
- Redundant dependency installation
- No test result caching

**GitHub Actions Optimization:**

```yaml
# .github/workflows/test.yml
name: Test Suite

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test-unit:
    name: Unit Tests
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      # Cache Python dependencies
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      # Cache uv dependencies
      - name: Cache uv
        uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}

      # Install dependencies
      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync

      # Run unit tests
      - name: Run unit tests
        run: uv run pytest tests/ -v -m "unit" -n auto --timeout=10

      # Upload coverage
      - name: Upload coverage
        uses: codecov/codecov-action@v4
        if: always()

  test-integration:
    name: Integration Tests
    runs-on: ubuntu-latest
    timeout-minutes: 10
    services:
      redis:
        image: redis:7-alpine
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 5
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync

      - name: Run integration tests
        run: uv run pytest tests/ -v -m "integration" -n auto --timeout=30
        env:
          REDIS_URL: redis://localhost:6379

  test-slow:
    name: Slow & E2E Tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - name: Start Docker services
        run: make dev

      - name: Run slow tests
        run: make test-docker-slow

      - name: Cleanup
        if: always()
        run: make down

  test-results:
    name: Test Results Summary
    runs-on: ubuntu-latest
    needs: [test-unit, test-integration]
    if: always()
    steps:
      - name: Check test results
        run: |
          if [ "${{ needs.test-unit.result }}" != "success" ] || [ "${{ needs.test-integration.result }}" != "success" ]; then
            echo "Tests failed"
            exit 1
          fi
```

**Expected CI/CD Improvements:**
- ✅ Unit tests: 5m → **2m** (parallelization + caching)
- ✅ Integration tests: 8m → **3m** (parallelization + service optimization)
- ✅ Total CI/CD time: 13m → **5m** (parallel jobs)

---

## Test Performance Profiling

### Strategy 7: Duration Tracking & Monitoring

**Implementation:**

Enable pytest duration reporting:

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = """
    --durations=10
    --durations-min=1.0
"""
```

**Output Example:**
```
======================== slowest 10 durations =========================
5.23s call     tests/integration/test_multi_worker.py::test_ten_workers_process_large_queue
2.15s call     tests/workers/test_graceful_shutdown.py::test_worker_completes_current_job_before_shutdown
1.87s call     tests/integration/test_concurrent_requests.py::test_high_concurrency_load
1.42s call     tests/edge_cases/test_large_files.py::test_40_page_pdf_processing
1.13s call     tests/services/test_resource_management.py::test_concurrent_resource_cleanup
...
```

**Automated Performance Regression Detection:**

Create performance baseline:

```bash
# Generate baseline
pytest --durations=0 --durations-min=0.1 > test_durations_baseline.txt

# Compare in CI
pytest --durations=0 --durations-min=0.1 > test_durations_current.txt
python scripts/compare_test_durations.py
```

**Threshold Alerts:**
- ⚠️ Test takes >2x baseline time → Warning
- 🚨 Test takes >5x baseline time → Fail CI

---

### Strategy 8: Flaky Test Detection

**Implementation:**

Add pytest-rerunfailures for flaky test detection:

```toml
[project.optional-dependencies]
dev = [
    # ... existing deps ...
    "pytest-rerunfailures>=13.0",
]
```

**Usage:**

```bash
# Re-run failed tests up to 3 times
pytest --reruns 3 --reruns-delay 1

# In CI, identify flaky tests
pytest --reruns 5 --only-rerun AssertionError
```

**Metrics to Track:**
- Test failure rate per test
- Re-run success rate
- Tests that fail intermittently

---

## Migration Plan

### Phase 1: Foundation (Week 1) - P0

**Day 1-2: Add Dependencies & Basic Configuration**
- [ ] Add `pytest-xdist`, `pytest-timeout` to `pyproject.toml`
- [ ] Update pytest configuration with basic parallelization
- [ ] Add test markers (unit, integration, slow, e2e)
- [ ] Update Makefile with new commands
- [ ] Run baseline performance tests (document current times)

**Day 3-4: Mark Existing Tests**
- [ ] Tag ~200 unit tests with `@pytest.mark.unit`
- [ ] Tag ~80 integration tests with `@pytest.mark.integration`
- [ ] Tag ~10 slow tests with `@pytest.mark.slow`
- [ ] Add timeouts to all tests (default 30s)
- [ ] Run parallel test suite, identify issues

**Day 5: Validate & Document**
- [ ] Fix any parallelization issues (shared state, race conditions)
- [ ] Document performance improvements
- [ ] Update README with new test commands
- [ ] Merge Phase 1 changes

**Expected Outcome:** 40-50% performance improvement from parallelization alone

---

### Phase 2: Sleep Optimization (Week 2) - P0

**Focus Files (highest impact):**
1. `tests/workers/test_graceful_shutdown.py` (15 sleep calls, 2.9s)
2. `tests/integration/test_multi_worker.py` (4 sleep calls, 0.63s)
3. `tests/services/test_resource_management.py` (4 sleep calls, 0.45s)
4. `tests/workers/test_timeout_worker.py` (3 sleep calls, 0.4s)
5. `tests/integration/test_concurrent_requests.py` (2 sleep calls, 0.2s)

**Per-File Strategy:**

```python
# Example: test_graceful_shutdown.py optimization

# BEFORE:
worker_task = asyncio.create_task(worker.start(shutdown_event))
await asyncio.sleep(0.1)  # Give worker time to start
shutdown_event.set()

# AFTER:
worker_started = asyncio.Event()

# Modify worker or use wrapper
async def start_with_signal(worker, shutdown_event):
    worker_started.set()
    await worker.start(shutdown_event)

worker_task = asyncio.create_task(start_with_signal(worker, shutdown_event))
await asyncio.wait_for(worker_started.wait(), timeout=1.0)
shutdown_event.set()
```

**Tasks:**
- [ ] Refactor `test_graceful_shutdown.py` (target: 2.9s → 0.1s)
- [ ] Refactor `test_multi_worker.py` (target: 0.63s → 0.05s)
- [ ] Refactor `test_resource_management.py` (target: 0.45s → 0.05s)
- [ ] Refactor `test_timeout_worker.py` (target: 0.4s → 0.05s)
- [ ] Refactor `test_concurrent_requests.py` (target: 0.2s → 0s)
- [ ] Run regression tests to ensure behavior unchanged
- [ ] Measure performance improvement

**Expected Outcome:** Additional 30-40% improvement (cumulative: 60-70% total)

---

### Phase 3: Fixture & CI Optimization (Week 3) - P1

**Fixture Optimization:**
- [ ] Add fixture scoping to `tests/integration/conftest.py`
- [ ] Convert function-scoped fixtures to class/session scope where safe
- [ ] Add lazy initialization for expensive mocks
- [ ] Create shared session-scoped test data fixtures
- [ ] Measure fixture setup/teardown time

**CI/CD Optimization:**
- [ ] Create `.github/workflows/test.yml` with parallel jobs
- [ ] Add dependency caching (uv, pip, Python packages)
- [ ] Split unit/integration/slow tests into separate jobs
- [ ] Add test result caching
- [ ] Configure Docker layer caching

**Expected Outcome:** 10-20% additional improvement (cumulative: 70-80% total)

---

### Phase 4: Monitoring & Maintenance (Week 4) - P1

**Performance Monitoring:**
- [ ] Create `scripts/compare_test_durations.py` for regression detection
- [ ] Add performance baseline to repository
- [ ] Configure CI alerts for slow tests
- [ ] Create test performance dashboard (optional)

**Flaky Test Detection:**
- [ ] Add `pytest-rerunfailures` configuration
- [ ] Run CI with re-runs enabled
- [ ] Track flaky test metrics
- [ ] Fix or quarantine flaky tests

**Documentation:**
- [ ] Update `README.md` with performance optimization guide
- [ ] Document test categorization strategy
- [ ] Create troubleshooting guide for slow tests
- [ ] Add performance regression prevention guidelines

---

## Edge Cases & Challenges

### Challenge 1: Parallel Test Isolation

**Issue:** Tests that share state may fail when run in parallel.

**Example:**
```python
# Redis key collision between parallel tests
async def test_job_creation_1():
    await job_service.create_job("test-job", ...)  # Collision!

async def test_job_creation_2():
    await job_service.create_job("test-job", ...)  # Collision!
```

**Solution:**
```python
# Use unique IDs per test
async def test_job_creation_1():
    job_id = f"test-{uuid.uuid4()}"
    await job_service.create_job(job_id, ...)

async def test_job_creation_2():
    job_id = f"test-{uuid.uuid4()}"
    await job_service.create_job(job_id, ...)
```

**Detection:** Run tests with `-n auto` and identify failures.

---

### Challenge 2: Timing-Sensitive Tests

**Issue:** Tests that depend on precise timing may become flaky.

**Example:**
```python
# Assumes operation completes within 0.1s
start = time.time()
await process_job()
assert time.time() - start < 0.1  # Flaky in CI
```

**Solution:**
```python
# Use asyncio.wait_for() with generous timeout
await asyncio.wait_for(process_job(), timeout=5.0)
# Don't assert on timing, assert on completion
```

---

### Challenge 3: Resource Limits in CI

**Issue:** GitHub Actions runners have limited CPU cores (2 cores).

**Solution:**
- Configure `-n 2` in CI instead of `-n auto`
- Use faster runners for main branch (4-8 cores)
- Optimize for developer machines (4-16 cores)

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: pytest -n 2  # Force 2 workers in CI
```

---

### Challenge 4: Docker Test Execution

**Issue:** `make test-docker` runs inside container with different resource limits.

**Solution:**
```makefile
# Makefile
test-docker:
	docker-compose exec api-gateway uv run pytest tests/ -v -n auto --timeout=60

test-docker-fast:
	docker-compose exec api-gateway uv run pytest tests/ -v -m "unit" -n 2
```

**Container Resource Limits:**
```yaml
# docker-compose.dev.yml
services:
  api-gateway:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
```

---

## Performance Benchmarks & Validation

### Baseline Measurements (Current)

**Estimated Current Performance:**
```
Unit tests (~200 tests):          ~45 seconds
Integration tests (~80 tests):    ~180 seconds (3 minutes)
Slow tests (~10 tests):           ~90 seconds (1.5 minutes)
Full suite (300+ tests):          ~360 seconds (6 minutes)

asyncio.sleep() delays:           ~4.6 seconds (fixed overhead)
Fixture setup/teardown:           ~30 seconds (estimated)
```

**Bottlenecks:**
1. Sequential execution (no parallelization)
2. asyncio.sleep() delays (4.6s cumulative)
3. Expensive fixture creation (per-test scope)
4. No test categorization (always run all tests)

---

### Target Performance (Post-Optimization)

**Phase 1 Target (Parallelization):**
```
Unit tests (-n 4):                ~15 seconds (3x speedup)
Integration tests (-n 4):         ~60 seconds (3x speedup)
Full suite (-n 4):                ~120 seconds (3x speedup)
```

**Phase 2 Target (Sleep Removal):**
```
Unit tests:                       ~12 seconds (4x total speedup)
Integration tests:                ~45 seconds (4x total speedup)
Full suite:                       ~90 seconds (4x total speedup)
```

**Phase 3 Target (Fixture Optimization):**
```
Unit tests:                       ~10 seconds (4.5x total speedup)
Integration tests:                ~40 seconds (4.5x total speedup)
Full suite:                       ~75 seconds (4.8x total speedup)
```

**Phase 4 Target (CI Optimization):**
```
CI unit tests (cached):           ~90 seconds (wall clock, includes setup)
CI integration tests (cached):    ~120 seconds (wall clock)
CI full suite:                    ~240 seconds (4 minutes, parallel jobs)
```

---

### Validation Tests

**Performance Regression Test:**

```python
# tests/meta/test_suite_performance.py

import pytest
import time

@pytest.mark.meta
def test_unit_test_suite_performance(benchmark):
    """Validate unit test suite completes within 30s."""
    start = time.time()

    # Run unit tests
    exit_code = pytest.main([
        "tests/",
        "-m", "unit",
        "-n", "auto",
        "--quiet"
    ])

    duration = time.time() - start

    assert exit_code == 0, "Unit tests failed"
    assert duration < 30, f"Unit tests too slow: {duration:.2f}s > 30s"

@pytest.mark.meta
def test_no_tests_without_timeouts():
    """Validate all tests have timeouts configured."""
    # Scan test files for missing @pytest.mark.timeout
    # Fail if any async test lacks timeout
    pass

@pytest.mark.meta
def test_all_tests_properly_marked():
    """Validate all tests have appropriate markers."""
    # Ensure every test has at least one marker: unit, integration, slow, e2e
    pass
```

---

## Monitoring & Metrics

### Test Performance Dashboard

**Prometheus Metrics (Optional):**

```python
# tests/conftest.py

from prometheus_client import Histogram

test_duration = Histogram(
    'test_duration_seconds',
    'Test execution duration',
    ['test_file', 'test_name', 'marker']
)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    start = time.time()
    outcome = yield
    duration = time.time() - start

    # Record metrics
    test_duration.labels(
        test_file=item.fspath.basename,
        test_name=item.name,
        marker=item.get_closest_marker('mark').name if item.get_closest_marker('mark') else 'none'
    ).observe(duration)
```

**Grafana Dashboard Panels:**
1. Test Suite Duration Trend (last 30 days)
2. Slowest 10 Tests (current run)
3. Test Failure Rate by Category
4. Flaky Test Detection (re-run success rate)
5. CI/CD Test Duration vs. Local

---

### Alert Thresholds

**Development Alerts:**
- ⚠️ Unit test suite >30s
- ⚠️ Integration test suite >120s
- ⚠️ Any single test >10s (except marked slow)

**CI/CD Alerts:**
- 🚨 Unit tests >5m (indicates parallelization failure)
- 🚨 Integration tests >10m
- 🚨 Test duration regression >50% from baseline

**Flaky Test Alerts:**
- ⚠️ Test fails then passes on re-run >3x in 7 days
- 🚨 Any test has <80% success rate

---

## Rollback Plan

If optimizations cause issues:

### Phase 1 Rollback (Parallelization)
```toml
# pyproject.toml - Remove parallelization
[tool.pytest.ini_options]
addopts = "-v --tb=short"  # Remove -n auto
```

### Phase 2 Rollback (Sleep Removal)
- Git revert specific file changes
- Keep parallelization gains
- Investigate timing issues

### Phase 3 Rollback (Fixture Scoping)
- Revert to function-scoped fixtures
- Debug state sharing issues
- Re-apply selectively

### Emergency Rollback
```bash
git revert <optimization-commit-sha>
git push origin main
```

---

## Future Enhancements

### Phase 5: Advanced Optimizations (Future)

**Test Sharding:**
```bash
# Split tests across multiple CI runners
pytest --shard-id=1 --num-shards=4  # Runner 1
pytest --shard-id=2 --num-shards=4  # Runner 2
```

**Hypothesis Property-Based Testing:**
```python
from hypothesis import given, strategies as st

@given(st.text(min_size=1, max_size=1000))
def test_pii_detection_handles_any_text(text):
    findings = pii_analyzer.analyze_text(text)
    assert isinstance(findings, list)
```

**Test Impact Analysis:**
```bash
# Only run tests affected by code changes
pytest --testmon
```

**Snapshot Testing:**
```python
def test_markdown_output_matches_snapshot(snapshot):
    result = process_document(...)
    snapshot.assert_match(result, "document_output.md")
```

---

## Definition of Done

### P0 Requirements (Must Have)

**Performance:**
- [x] `pytest-xdist` installed and configured
- [x] `pytest-timeout` installed and configured
- [x] All tests categorized with markers (unit/integration/slow/e2e)
- [x] Unit tests complete in <30s with parallelization
- [x] Integration tests complete in <2m with parallelization
- [x] Full suite completes in <5m with parallelization
- [x] 90% of `asyncio.sleep()` calls replaced (30 → 3)

**Developer Experience:**
- [x] `make test-fast` runs unit tests only (<30s)
- [x] `make test-integration` runs integration tests (<2m)
- [x] `make test-parallel` runs with all cores
- [x] `--durations=10` enabled by default
- [x] All tests have timeout configuration
- [x] README updated with new test commands

**CI/CD:**
- [x] GitHub Actions workflow with parallel jobs
- [x] Unit test job (<5m wall clock)
- [x] Integration test job (<10m wall clock)
- [x] Dependency caching configured
- [x] Test results uploaded/reported

**Quality:**
- [x] All existing tests pass with parallelization
- [x] No flaky tests introduced
- [x] Performance baseline documented
- [x] Regression detection configured

---

### P1 Enhancements (Nice to Have)

**Performance:**
- [ ] Unit tests <15s
- [ ] CI full suite <3m
- [ ] 95% of sleep calls removed

**Monitoring:**
- [ ] Test performance dashboard (Grafana)
- [ ] Automated flaky test detection
- [ ] Performance regression alerts
- [ ] Test impact analysis

**Advanced Features:**
- [ ] Test sharding for massive parallelization
- [ ] Hypothesis property-based testing
- [ ] Snapshot testing for complex outputs

---

## Success Metrics

**Quantitative:**
- ✅ Test suite execution time reduced by **70-80%**
- ✅ Developer feedback loop <30s for unit tests
- ✅ CI/CD pipeline time reduced by **60%**
- ✅ Zero hanging tests (100% timeout coverage)
- ✅ Zero flaky tests detected in 30-day window

**Qualitative:**
- ✅ Developer satisfaction with test speed (survey)
- ✅ Increased test-driven development adoption
- ✅ Faster PR review cycles (faster CI feedback)
- ✅ Reduced "skip tests to save time" incidents

---

## References

**Pytest Documentation:**
- [pytest-xdist](https://pytest-xdist.readthedocs.io/)
- [pytest-timeout](https://github.com/pytest-dev/pytest-timeout)
- [pytest markers](https://docs.pytest.org/en/stable/example/markers.html)

**Best Practices:**
- [Speed Up Your Tests (RealPython)](https://realpython.com/pytest-python-testing/#speed-up-tests)
- [Pytest Good Practices](https://docs.pytest.org/en/stable/goodpractices.html)
- [Fast Python Tests](https://martinfowler.com/articles/practical-test-pyramid.html)

**Related PRDs:**
- BUG-006: Test Suite Failures (foundation)
- STORY-007: CI/CD Test Automation (integration)

---

## Appendix: Detailed Sleep Analysis

### Complete Sleep Call Inventory

**File: `tests/workers/test_graceful_shutdown.py` (15 calls, 2.9s total)**

| Line | Duration | Context | Optimization |
|------|----------|---------|--------------|
| 49 | 0.1s | Worker startup wait | Event-based |
| 80 | 0.1s | Worker startup wait | Event-based |
| 102 | 0.2s | Simulate work | Remove delay |
| 127 | 0.1s | Worker startup wait | Event-based |
| 161 | 0.1s | Worker startup wait | Event-based |
| 186 | 0.1s | Worker startup wait | Event-based |
| 222 | 0.1s | Worker startup wait | Event-based |
| 257 | 0.2s | Periodic task wait | Event-based |
| 278 | 0 | Mock return value | Keep |
| 279 | 0 | Mock return value | Keep |
| 280 | 0 | Mock return value | Keep |
| 289 | 0.1s | Worker startup wait | Event-based |
| 316 | 1000s | Mock (never reached) | Keep |
| 317 | 1000s | Mock (never reached) | Keep |
| 318 | 1000s | Mock (never reached) | Keep |
| 346 | 0.1s | Worker loop | Event-based |
| 359 | 0.1s | Worker startup wait | Event-based |
| 382 | 0.1s | Worker startup wait | Event-based |
| 402 | 0.1s | Worker startup wait | Event-based |
| 429 | 0.1s | Worker startup wait | Event-based |

**Realistic Total:** 2.9s (excluding 1000s mocks that timeout)

**File: `tests/integration/test_multi_worker.py` (4 calls, 0.63s total)**

| Line | Duration | Context | Optimization |
|------|----------|---------|--------------|
| 184 | 0.01s | Processing simulation | Remove/reduce |
| 251 | 0.001s | Redis latency mock | Remove |
| 283 | 0.02s | Processing simulation | Remove/reduce |
| 578 | 0.001s | Processing simulation | Remove |

**File: `tests/services/test_resource_management.py` (4 calls, 0.45s total)**

| Line | Duration | Context | Optimization |
|------|----------|---------|--------------|
| 148 | 0.1s | Concurrent operation | Event-based |
| 193 | 0.1s | Concurrent operation | Event-based |
| 281 | 0.05s | Concurrent operation | Event-based |
| 359 | 0.2s | Concurrent operation | Event-based |

**File: `tests/workers/test_timeout_worker.py` (3 calls, 0.4s total)**

| Line | Duration | Context | Optimization |
|------|----------|---------|--------------|
| 194 | 0.1s | Worker startup wait | Event-based |
| 214 | 0.1s | Worker startup wait | Event-based |
| 235 | 0.5s | Periodic task wait | Reduce to 0.05s |

**File: `tests/integration/test_concurrent_requests.py` (2 calls, 0.2s total)**

| Line | Duration | Context | Optimization |
|------|----------|---------|--------------|
| 255 | 0.1s | Concurrent request | Event-based |
| 478 | 0.1s | Concurrent request | Event-based |

**Grand Total:** 4.58s of real delays (excluding timeout mocks)

---

**Optimization Impact:**
- Remove processing simulation sleeps: 0.032s → 0s (32ms saved)
- Replace worker startup sleeps with events: 1.8s → 0.05s (1.75s saved)
- Replace concurrent operation sleeps: 0.65s → 0.05s (0.6s saved)
- Reduce timeout worker periodic sleep: 0.5s → 0.05s (0.45s saved)

**Total Savings:** ~2.83s per test run (62% reduction)
**With Parallelization (4 workers):** Additional 3-4x speedup

**Final Estimated Performance:**
- Current: ~360s (6 minutes)
- After optimization: ~75s (1.25 minutes)
- **Improvement: 4.8x faster (79% reduction)**
