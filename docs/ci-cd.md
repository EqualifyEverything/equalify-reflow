# CI/CD and Testing Infrastructure

## Overview

All tests run automatically via GitHub Actions on every push, PR, and merge. The test suite is organized into three tiers for optimal developer feedback and comprehensive coverage.

## Test Tiers

### Fast Unit Tests (<2min)
```bash
make test-fast    # or: make test-unit
```
- **What**: Pure unit tests with mocked dependencies
- **When**: Every code change, before committing
- **No Docker needed**: Runs directly with `uv run pytest`
- **Coverage**: ~101 tests in `tests/unit/`
- **CI Trigger**: Every push to any branch

### Integration Tests (~5min)
```bash
make test-integration
```
- **What**: Tests with real Redis/S3 via testcontainers
- **When**: Before opening PR
- **Requires**: Docker for testcontainers
- **Coverage**: ~28 tests in `tests/integration/`
- **CI Trigger**: PRs to main/develop branches

### E2E Tests (~10min)
```bash
make test-e2e     # or: make test-slow
```
- **What**: Full workflow tests with minimal mocking
- **When**: Before merging to main
- **Requires**: Docker for testcontainers
- **Coverage**: ~63 tests in `tests/e2e/`
- **CI Trigger**: Merges to main branch

### All Tests (Comprehensive)
```bash
make test-all     # Runs in Docker
```
- **What**: Complete test suite (591 tests)
- **When**: Final verification before merging to main
- **Requires**: `make dev` (Docker stack running)
- **CI Trigger**: Manual or scheduled

## Test by Marker

Run specific test categories using pytest markers:

```bash
# Run specific test categories
uv run pytest -m unit            # Unit tests only
uv run pytest -m integration     # Integration tests only
uv run pytest -m slow            # Slow/E2E tests only
uv run pytest -m performance     # Performance tests
uv run pytest -m requires_redis  # Redis-dependent tests
uv run pytest -m requires_s3     # S3-dependent tests
```

## GitHub Actions Workflows

### [test-fast.yml](.github/workflows/test-fast.yml)
**Trigger:** Every push to any branch

**Purpose:** Fast feedback loop for developers

**Tests:** Unit tests only (~101 tests)

**Duration:** <2 minutes

**Artifacts:** Unit test coverage report

### [test-integration.yml](.github/workflows/test-integration.yml)
**Trigger:** PRs to main/develop branches

**Purpose:** Validate integration points before review

**Tests:** Integration tests with real Redis/S3 (~28 tests)

**Duration:** ~5 minutes

**Requires:** Docker, testcontainers

**Artifacts:** Integration test coverage report

### [test-e2e.yml](.github/workflows/test-e2e.yml)
**Trigger:** Merges to main branch

**Purpose:** Comprehensive validation of merges to main

**Tests:** E2E tests with full workflows (~63 tests)

**Duration:** ~10 minutes

**Requires:** Docker, testcontainers

**Artifacts:** E2E test coverage report

### [test-performance.yml](.github/workflows/test-performance.yml)
**Trigger:** Weekly (Sundays at 2 AM UTC) or manual

**Purpose:** Performance regression detection

**Tests:** Performance-marked tests

**Duration:** Varies

**Artifacts:** Performance benchmark results

## CI Status

**PR Merge Policy:** PRs cannot merge until all applicable tests pass.

**Status Badges:** Test status badges are displayed in the main README.

## Test Coverage

### Current Coverage
**Overall:** 82% (503/503 tests passing ✅)

### Local Coverage Commands

```bash
# Run tests with coverage
make coverage

# View HTML coverage report
make coverage-html

# Show coverage summary
make coverage-report
```

### CI Coverage Reports

Coverage HTML reports are generated for all test jobs and uploaded as artifacts:

- **Unit Tests:** Coverage for services, models, and API endpoints
- **Integration Tests:** Coverage for Redis/S3 integration paths
- **Docker Tests:** Full end-to-end coverage report

**Download Reports:** GitHub Actions → Workflow run → Artifacts section

### Coverage Details

- **Line coverage**: % of code lines executed
- **Branch coverage**: % of decision branches tested
- **Missing lines**: Highlighted in HTML reports
- **Per-file breakdown**: Detailed coverage by module

## Environment Configuration

### CI Environment Variables

All workflows use GitHub Secrets for sensitive values:

```yaml
env:
  REDIS_URL: redis://localhost:6379
  AWS_ENDPOINT_URL: http://localhost:4566
  AWS_ACCESS_KEY_ID: test
  AWS_SECRET_ACCESS_KEY: test
  AWS_DEFAULT_REGION: us-east-1
  S3_TEMP_BUCKET: equalify-pdf-temp
  S3_RESULTS_BUCKET: equalify-pdf-results
```

### Service Containers

Integration and E2E workflows use GitHub Actions service containers:

- **Redis:** `redis:7-alpine`
- **Floci:** `hectorvent/floci:1.5.3` (replaces LocalStack)

## Local CI Simulation

To replicate CI environment locally:

```bash
# Run same commands as CI
make test-fast        # Matches test-fast.yml
make test-integration # Matches test-integration.yml
make test-e2e         # Matches test-e2e.yml

# Full CI simulation
make test-all
```

## Debugging CI Failures

1. **Check workflow logs** in GitHub Actions
2. **Download artifacts** for detailed coverage reports
3. **Replicate locally** using matching test tier command
4. **Run with verbose output**: `uv run pytest -vv -m <marker>`
5. **Check service logs** if integration/E2E failures

## Test Development Guidelines

### Writing New Tests

1. **Choose appropriate tier:**
   - Unit: Pure logic, mocked dependencies → `tests/unit/`
   - Integration: Real Redis/S3 → `tests/integration/`
   - E2E: Full workflows → `tests/e2e/`

2. **Add pytest markers:**
   ```python
   @pytest.mark.unit
   def test_something():
       ...

   @pytest.mark.integration
   @pytest.mark.requires_redis
   def test_redis_integration():
       ...
   ```

3. **Run locally before pushing:**
   ```bash
   make test-fast  # Always run this first
   ```
