# STORY-008: Test Coverage Reporting & Enforcement

**Priority:** P1 - HIGH
**Severity:** Important - Quality visibility and enforcement gap
**Estimated Effort:** 6-8 hours
**Status:** 📋 READY FOR DEVELOPMENT
**Dependencies:** STORY-007 (CI/CD Test Automation)
**Related:** BUG-006 (test failures), STORY-009 (core pipeline testing)

---

## Problem Statement

The project has pytest-cov installed but **no coverage measurement, reporting, or enforcement** configured. This creates significant quality visibility gaps:

- ❌ **No visibility into test coverage** - Unknown which code paths are tested
- ❌ **No coverage trends** - Can't track if coverage is improving or degrading
- ❌ **No enforcement** - Nothing prevents merging untested code
- ❌ **No local feedback** - Developers don't see coverage impact during development
- ❌ **No CI integration** - Coverage not part of automated quality gates
- ❌ **No PR context** - Reviewers can't see coverage delta for changes

**Current Situation:**
```
Developer → Write code → Write tests → ? (no idea if adequate) → Merge
```

**Target Situation:**
```
Developer → Write code → Write tests → See coverage locally (80%+) → CI validates → PR shows delta → Merge
```

**Risk Impact:**
- 🔴 **Critical paths untested** - Production failures in uncovered code
- 🔴 **False confidence** - Tests exist but don't cover edge cases
- 🔴 **Technical debt growth** - Coverage decreases over time without visibility
- 🔴 **Slow debugging** - Unclear which tests exercise failing code

---

## Success Criteria

### Must Have (P0)
- [ ] Coverage measured for all test runs (local + CI)
- [ ] Terminal reports show coverage percentage + missing lines
- [ ] HTML reports generated for local development (browsable)
- [ ] XML reports generated for CI/CD (machine-readable)
- [ ] Coverage threshold enforced: **minimum 80%** overall
- [ ] CI fails if coverage drops below threshold
- [ ] Codecov.io integration with PR comments showing coverage delta
- [ ] Coverage badge in README.md

### Should Have (P1)
- [ ] Per-module coverage breakdown (services, models, api, workers)
- [ ] Coverage diff in PR comments (lines added/removed from coverage)
- [ ] Branch coverage measurement (not just line coverage)
- [ ] Coverage reports uploaded as CI artifacts (downloadable)
- [ ] Local pre-commit hook warns if coverage drops
- [ ] Coverage trends tracked over time (Codecov graphs)

### Nice to Have (P2)
- [ ] Coverage annotations in PR file diffs (line-by-line)
- [ ] Uncovered line highlighting in HTML reports
- [ ] Coverage exceptions for known untestable code (via # pragma: no cover)
- [ ] Differential coverage: 100% for new code (even if overall is 80%)
- [ ] Coverage by test type (unit vs integration)
- [ ] Parallel coverage collection for speed

---

## Technical Design

### 1. pytest-cov Configuration

**File:** `pyproject.toml`

Add comprehensive coverage configuration:

```toml
[tool.coverage.run]
# What to measure
source = ["src"]
branch = true  # Measure branch coverage, not just line coverage
omit = [
    "*/tests/*",
    "*/test_*.py",
    "*/__pycache__/*",
    "*/migrations/*",
    "*/venv/*",
    "*/.venv/*",
]

# Parallel mode for future use (multi-worker testing)
parallel = false

[tool.coverage.report]
# Terminal output configuration
precision = 2
show_missing = true  # Show line numbers of missing coverage
skip_covered = false
skip_empty = false

# Fail if coverage below threshold
fail_under = 80.0

# Exclude patterns from coverage
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "class .*\\(Protocol\\):",
    "@(abc\\.)?abstractmethod",
]

# Sort results for consistent output
sort = "Cover"

[tool.coverage.html]
# HTML report configuration
directory = "htmlcov"
show_contexts = true

[tool.coverage.xml]
# XML report for CI/CD (Codecov)
output = "coverage.xml"

[tool.pytest.ini_options]
# Existing config...
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
pythonpath = ["src"]

# Updated addopts with coverage
addopts = """
    -v
    --tb=short
    --strict-markers
    --maxfail=10
    --disable-warnings
    --cov=src
    --cov-report=term-missing:skip-covered
    --cov-report=html
    --cov-report=xml
    --cov-fail-under=80
"""

# Test markers (existing from STORY-007)
markers = [
    "unit: Unit tests (fast, isolated)",
    "integration: Integration tests (with Redis/S3)",
    "slow: Slow-running tests (>1s)",
    "requires_redis: Needs Redis service",
    "requires_s3: Needs S3/LocalStack service",
    "requires_ai: Needs Anthropic API (mocked in CI)",
]
```

### 2. Local Development Workflow

**Terminal Report (always shown):**
```bash
$ make test-docker

---------- coverage: platform linux, python 3.11.9-final-0 -----------
Name                              Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------
src/__init__.py                       0      0      0      0   100%
src/api/__init__.py                   3      0      0      0   100%
src/api/documents.py                 87     12     24      3    84%   45-47, 92-95, 187-190
src/api/health.py                    15      0      2      0   100%
src/models/job.py                    45      2      8      1    94%   78-79
src/services/job_service.py         156      8     42      2    93%   234-235, 389-392
src/services/pii_analyzer.py         62      0     12      0   100%
src/services/queue_service.py        98      5     28      1    94%   145-147, 223-224
src/services/storage_service.py     124     18     34      4    83%   67-72, 189-195, 301-303
src/workers/pii_worker.py            89      7     22      2    90%   123-125, 201-204
-----------------------------------------------------------------------------
TOTAL                               679     52    172     13    89%

Coverage HTML written to dir htmlcov
Coverage XML written to file coverage.xml

✅ Coverage: 89.2% (threshold: 80%)
```

**HTML Report (browsable locally):**
```bash
$ open htmlcov/index.html
# Opens interactive report showing:
# - File-by-file coverage
# - Highlighted missing lines in source code
# - Branch coverage visualization
# - Drill-down to specific modules
```

### 3. CI/CD Integration

**Update:** `.github/workflows/test.yml` (from STORY-007)

Add coverage collection and reporting to existing workflow:

```yaml
name: Test Suite

on:
  push:
    branches: ['**']
  pull_request:
    branches: [main, develop]
  workflow_dispatch:

env:
  PYTHON_VERSION: '3.11'
  COVERAGE_THRESHOLD: '80'

jobs:
  # Job 1: Unit Tests with Coverage
  unit-tests:
    name: Unit Tests + Coverage
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history for Codecov

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: Cache uv dependencies
        uses: actions/cache@v4
        with:
          path: ~/.cache/uv
          key: ${{ runner.os }}-uv-${{ hashFiles('**/pyproject.toml') }}
          restore-keys: |
            ${{ runner.os }}-uv-

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync --all-extras

      - name: Run unit tests with coverage
        run: |
          uv run pytest tests/services tests/models tests/api \
            -v \
            --tb=short \
            -m "not integration and not slow" \
            --cov=src \
            --cov-report=term-missing:skip-covered \
            --cov-report=html \
            --cov-report=xml \
            --cov-fail-under=${{ env.COVERAGE_THRESHOLD }} \
            --maxfail=5

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: ./coverage.xml
          flags: unit-tests
          name: unit-test-coverage
          fail_ci_if_error: true

      - name: Upload HTML coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: unit-coverage-report
          path: htmlcov/

      - name: Upload XML coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: unit-coverage-xml
          path: coverage.xml

  # Job 2: Integration Tests with Coverage
  integration-tests:
    name: Integration Tests + Coverage
    runs-on: ubuntu-latest
    timeout-minutes: 15

    services:
      redis:
        image: redis:7-alpine
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 6379:6379

      localstack:
        image: localstack/localstack:latest
        env:
          SERVICES: s3
          DEFAULT_REGION: us-east-1
        options: >-
          --health-cmd "awslocal s3 ls"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 4566:4566

    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: 'pip'

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: uv sync --all-extras

      - name: Configure AWS CLI for LocalStack
        run: |
          pip install awscli-local
          awslocal s3 mb s3://equalify-pdf-temp
          awslocal s3 mb s3://equalify-pdf-results

      - name: Run integration tests with coverage
        env:
          REDIS_URL: redis://localhost:6379/0
          AWS_ENDPOINT_URL: http://localhost:4566
          AWS_ACCESS_KEY_ID: test
          AWS_SECRET_ACCESS_KEY: test
          AWS_REGION: us-east-1
        run: |
          uv run pytest tests/integration tests/edge_cases \
            -v \
            --tb=short \
            -m "integration or slow" \
            --cov=src \
            --cov-append \
            --cov-report=term-missing \
            --cov-report=xml \
            --maxfail=3

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: ./coverage.xml
          flags: integration-tests
          name: integration-test-coverage
          fail_ci_if_error: true

      - name: Upload coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: integration-coverage-xml
          path: coverage.xml

  # Job 3: Docker Tests with Coverage
  docker-tests:
    name: Full Test Suite (Docker) + Coverage
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Cache Docker layers
        uses: actions/cache@v4
        with:
          path: /tmp/.buildx-cache
          key: ${{ runner.os }}-buildx-${{ github.sha }}
          restore-keys: |
            ${{ runner.os }}-buildx-

      - name: Build Docker images
        run: make build

      - name: Run tests in Docker with coverage
        run: |
          docker compose run --rm api pytest \
            --cov=src \
            --cov-report=term-missing \
            --cov-report=html \
            --cov-report=xml \
            --cov-fail-under=${{ env.COVERAGE_THRESHOLD }}

      - name: Copy coverage reports from container
        if: always()
        run: |
          docker compose cp api:/app/coverage.xml ./coverage.xml
          docker compose cp api:/app/htmlcov ./htmlcov

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: ./coverage.xml
          flags: docker-tests
          name: docker-test-coverage
          fail_ci_if_error: false  # Optional for Docker tests

      - name: Upload coverage artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: docker-coverage-report
          path: |
            coverage.xml
            htmlcov/

  # Job 4: Coverage Summary & Enforcement
  coverage-summary:
    name: Coverage Summary & Enforcement
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests, docker-tests]
    if: always()

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Download all coverage reports
        uses: actions/download-artifact@v4
        with:
          pattern: '*-coverage-*'
          path: coverage-reports/

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install coverage tools
        run: pip install coverage

      - name: Combine coverage reports
        run: |
          cd coverage-reports
          coverage combine */coverage.xml
          coverage report --fail-under=${{ env.COVERAGE_THRESHOLD }}
          coverage html

      - name: Upload combined coverage
        uses: codecov/codecov-action@v4
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          files: coverage-reports/.coverage
          flags: combined
          name: combined-coverage
          fail_ci_if_error: true

      - name: Comment PR with coverage
        if: github.event_name == 'pull_request'
        uses: py-cov-action/python-coverage-comment-action@v3
        with:
          GITHUB_TOKEN: ${{ github.token }}
          MINIMUM_GREEN: 90
          MINIMUM_ORANGE: 80

      - name: Check coverage threshold
        run: |
          COVERAGE=$(coverage report | grep TOTAL | awk '{print $4}' | sed 's/%//')
          echo "Total Coverage: $COVERAGE%"

          if (( $(echo "$COVERAGE < ${{ env.COVERAGE_THRESHOLD }}" | bc -l) )); then
            echo "❌ Coverage ($COVERAGE%) is below threshold (${{ env.COVERAGE_THRESHOLD }}%)"
            exit 1
          else
            echo "✅ Coverage ($COVERAGE%) meets threshold (${{ env.COVERAGE_THRESHOLD }}%)"
          fi
```

### 4. Codecov.io Setup

**Step 1: Create Codecov Account**
```bash
# 1. Go to https://codecov.io/
# 2. Sign in with GitHub
# 3. Select "equalify-pdf-converter" repository
# 4. Copy CODECOV_TOKEN
```

**Step 2: Add GitHub Secret**
```bash
# GitHub Repository Settings → Secrets → Actions → New secret
# Name: CODECOV_TOKEN
# Value: <paste token from Codecov>
```

**Step 3: Configure Codecov Behavior**

**File:** `codecov.yml` (new file in repository root)

```yaml
# Codecov configuration
# https://docs.codecov.com/docs/codecov-yaml

coverage:
  status:
    project:
      default:
        target: 80%        # Overall project target
        threshold: 1%      # Allow 1% decrease without failing
        base: auto         # Compare to parent commit

    patch:
      default:
        target: 100%       # New code must be 100% covered
        threshold: 0%      # No tolerance for uncovered new code

  # Coverage precision
  precision: 2
  round: down
  range: "70...100"

  # What to ignore
  ignore:
    - "tests/**/*"
    - "**/__pycache__/**/*"
    - "**/migrations/**/*"

# PR comments
comment:
  layout: "reach,diff,flags,tree,footer"
  behavior: default
  require_changes: false
  require_base: no
  require_head: yes

# GitHub status checks
github_checks:
  annotations: true

# Flags for different test types
flag_management:
  default_rules:
    carryforward: true

  individual_flags:
    - name: unit-tests
      paths:
        - src/
      carryforward: false

    - name: integration-tests
      paths:
        - src/
      carryforward: false

    - name: docker-tests
      paths:
        - src/
      carryforward: false
```

**Expected PR Comment from Codecov:**
```markdown
## Codecov Report
Merging #42 into main will **increase** coverage by `2.34%`.
The diff coverage is `95.12%`.

@@            Coverage Diff            @@
##             main      #42     +/-   ##
=========================================
+ Coverage   86.78%   89.12%   +2.34%
=========================================
  Files          18       19      +1
  Lines         679      734     +55
  Branches      172      184     +12
=========================================
+ Hits          589      654     +65
+ Misses         52       48      -4
+ Partials       38       32      -6

| Flag | Coverage Δ |
|------|------------|
| unit-tests | `88.2% <95.1%> (+1.2%)` |
| integration-tests | `91.4% <100.0%> (+0.8%)` |
| docker-tests | `89.5% <96.2%> (+2.1%)` |

Files Changed Coverage Δ
src/api/documents.py 84.2% <95.0%> (+3.2%)
src/services/storage_service.py 83.1% <100.0%> (+1.4%)
```

### 5. README Badge Configuration

**Update:** `README.md`

Add coverage badge:

```markdown
# Equalify PDF Converter

[![Test Suite](https://github.com/org/equalify-pdf-converter/actions/workflows/test.yml/badge.svg)](https://github.com/org/equalify-pdf-converter/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/org/equalify-pdf-converter/branch/main/graph/badge.svg)](https://codecov.io/gh/org/equalify-pdf-converter)
[![Code Coverage](https://img.shields.io/codecov/c/github/org/equalify-pdf-converter?label=coverage)](https://codecov.io/gh/org/equalify-pdf-converter)

## Overview
Transforms PDF documents into accessible, semantic HTML for University of Illinois Chicago (UIC).

**Coverage Target:** 80% minimum (enforced in CI/CD)
```

### 6. Makefile Updates

**Update:** `Makefile`

Add coverage-specific commands:

```makefile
# Existing commands...

.PHONY: test-coverage
test-coverage: ## Run tests with coverage report
	docker compose run --rm api pytest \
		--cov=src \
		--cov-report=term-missing:skip-covered \
		--cov-report=html \
		--cov-report=xml \
		--cov-fail-under=80

.PHONY: coverage-report
coverage-report: ## Open HTML coverage report in browser
	@echo "Opening coverage report..."
	@python -m webbrowser htmlcov/index.html || open htmlcov/index.html

.PHONY: coverage-check
coverage-check: ## Check if coverage meets threshold (80%)
	docker compose run --rm api coverage report --fail-under=80

.PHONY: coverage-clean
coverage-clean: ## Remove coverage reports
	rm -rf htmlcov/ coverage.xml .coverage .coverage.*
```

---

## Migration Plan

### Phase 1: Baseline Measurement (Day 1)

**Goal:** Establish current coverage baseline

1. **Add pytest-cov configuration to pyproject.toml**
2. **Run full test suite with coverage:**
   ```bash
   make test-coverage
   ```
3. **Document baseline coverage percentage:**
   - Overall: ?%
   - Per module: services/?, models/?, api/?, workers/?
4. **Identify uncovered critical paths**
5. **Set realistic initial threshold** (if <80%, start lower and increment)

**Example Baseline Report:**
```
Current Coverage: 67.4%
- src/api/: 72%
- src/services/: 65%
- src/models/: 88%
- src/workers/: 54%

Critical gaps:
- Error handling paths: 45%
- Edge case validation: 38%
- Worker retry logic: 50%
```

### Phase 2: Coverage Configuration (Day 1-2)

**Goal:** Set up local and CI coverage reporting

1. **Configure pyproject.toml** (terminal, HTML, XML reports)
2. **Test locally:**
   ```bash
   make test-coverage
   open htmlcov/index.html
   ```
3. **Add .gitignore entries:**
   ```
   htmlcov/
   coverage.xml
   .coverage
   .coverage.*
   ```
4. **Update Makefile** with coverage commands
5. **Document in CONTRIBUTING.md**

### Phase 3: CI/CD Integration (Day 2-3)

**Goal:** Automate coverage in GitHub Actions

1. **Update `.github/workflows/test.yml`** with coverage steps
2. **Add coverage threshold enforcement** (--cov-fail-under)
3. **Test on feature branch:**
   ```bash
   git checkout -b test/coverage-reporting
   git push origin test/coverage-reporting
   # Verify workflow runs and reports coverage
   ```
4. **Review CI artifacts** (downloadable coverage.xml and htmlcov/)

### Phase 4: Codecov Setup (Day 3-4)

**Goal:** Enable PR coverage comments and trends

1. **Create Codecov account** (https://codecov.io/)
2. **Add CODECOV_TOKEN** to GitHub Secrets
3. **Create `codecov.yml`** configuration
4. **Add Codecov action** to workflow
5. **Test with sample PR:**
   - Create PR with coverage change
   - Verify Codecov comment appears
   - Verify coverage status check
6. **Add coverage badges** to README.md

### Phase 5: Threshold Enforcement (Day 4-5)

**Goal:** Block PRs below coverage threshold

1. **Set initial threshold** in `pyproject.toml`:
   ```toml
   fail_under = 80.0  # Or lower if baseline is <80%
   ```
2. **Update GitHub branch protection:**
   - Require Codecov status check
   - Require minimum 80% coverage
3. **Test enforcement:**
   - Create PR that reduces coverage
   - Verify CI fails
   - Verify cannot merge
4. **Document in CONTRIBUTING.md**

### Phase 6: Coverage Improvement (Ongoing)

**Goal:** Reach and maintain 80%+ coverage

**Week 1-2: Low-hanging fruit**
- Add tests for uncovered error paths
- Test edge cases in validation logic
- Cover worker retry scenarios

**Week 3-4: Systematic improvement**
- Prioritize critical paths (PII detection, storage, queue)
- Aim for 90%+ in services/ and models/
- 100% coverage for new code

**Month 2+: Maintenance**
- Monitor coverage trends in Codecov
- Review coverage reports in PRs
- Prevent coverage regression

---

## Coverage Thresholds Strategy

### Option A: Aggressive (Recommended)

**Immediate 80% threshold:**
```toml
fail_under = 80.0
```

**Pros:**
- Forces immediate test improvement
- Clear quality bar from day 1
- No technical debt accumulation

**Cons:**
- May require significant upfront work
- Could block PRs initially

**When to use:** If current coverage is >75%

### Option B: Incremental (If baseline <75%)

**Gradual threshold increases:**

```toml
# Week 1: Start at current baseline
fail_under = 67.0

# Week 2: +3%
fail_under = 70.0

# Week 3: +3%
fail_under = 73.0

# Week 4: +3%
fail_under = 76.0

# Week 5: +2%
fail_under = 78.0

# Week 6: +2%
fail_under = 80.0
```

**Pros:**
- Less disruptive to workflow
- Steady, achievable progress
- Team learns coverage practices gradually

**Cons:**
- Allows some uncovered code in interim
- Requires discipline to increment

**When to use:** If current coverage is <75%

### Option C: Differential (Advanced)

**High bar for new code, lower for existing:**

```yaml
# codecov.yml
coverage:
  status:
    project:
      default:
        target: 70%      # Legacy code
    patch:
      default:
        target: 100%     # New code must be fully covered
```

**Pros:**
- New code always well-tested
- Legacy code improved over time
- Balances quality and velocity

**Cons:**
- More complex to configure
- Requires Codecov.io

**When to use:** Large existing codebase with <70% coverage

---

## Per-Module Coverage Targets

### Critical Modules (90%+ target)

**Why:** Core business logic, high risk

- `src/services/pii_analyzer.py` - PII detection (security-critical)
- `src/services/job_service.py` - Job state management
- `src/services/queue_service.py` - Task queue orchestration
- `src/models/job.py` - Data models
- `src/api/documents.py` - API endpoints

### Important Modules (80%+ target)

**Why:** Key functionality, moderate risk

- `src/services/storage_service.py` - S3 operations
- `src/workers/pii_worker.py` - Background processing
- `src/api/health.py` - Health checks
- `src/config.py` - Configuration

### Lower Priority (70%+ target)

**Why:** Utility code, lower risk

- `src/utils/` - Helper functions
- `src/monitoring/` - Metrics collection
- `src/__init__.py` - Package initialization

### Exempted (No threshold)

**Why:** Not testable or low value

- `src/migrations/` - Database migrations
- Entry point scripts (`if __name__ == "__main__"`)
- Type stubs and protocols

---

## Coverage Metrics to Track

### 1. Overall Coverage

**Metric:** Total percentage of covered lines/branches

**Target:** ≥80%

**Dashboard:** Codecov main page

**Trend:** Should increase or stay stable over time

### 2. Diff Coverage (PR-specific)

**Metric:** Percentage of new/changed lines covered

**Target:** 100% for new code

**Dashboard:** Codecov PR comments

**Enforcement:** Require 100% patch coverage

### 3. Per-Module Coverage

**Metric:** Coverage breakdown by file/module

**Target:** See "Per-Module Coverage Targets" above

**Dashboard:** Codecov file browser

**Review:** Weekly in team meetings

### 4. Branch Coverage

**Metric:** Percentage of conditional branches covered

**Target:** ≥75% (harder than line coverage)

**Configuration:**
```toml
[tool.coverage.run]
branch = true
```

**Benefit:** Catches untested if/else paths

### 5. Coverage Trend

**Metric:** Coverage change over time (graph)

**Target:** Upward or flat (no regression)

**Dashboard:** Codecov graphs page

**Alert:** If coverage drops >2% in a week

### 6. Uncovered Critical Paths

**Metric:** Number of uncovered lines in critical modules

**Target:** 0 uncovered error handlers in PII detection

**Manual Review:** Monthly audit of coverage reports

**Action:** File tickets for gaps

---

## Local Development Experience

### Before Commit (Pre-commit Hook)

**File:** `.pre-commit-config.yaml` (future enhancement)

```yaml
repos:
  - repo: local
    hooks:
      - id: coverage-check
        name: Check test coverage
        entry: bash -c 'pytest --cov=src --cov-fail-under=80 -q'
        language: system
        pass_filenames: false
        always_run: false  # Optional: only on test file changes
```

**Behavior:**
```bash
$ git commit -m "Add new feature"

Running coverage check...
Coverage: 78.4% (below 80% threshold)
❌ Coverage check failed

# Fix by adding tests, then:
$ git commit -m "Add new feature + tests"

Running coverage check...
Coverage: 82.1% (meets 80% threshold)
✅ Coverage check passed
```

### During Development

**Quick coverage check:**
```bash
$ make test-coverage

# Review terminal output for quick feedback
# Or open HTML report for detailed analysis
$ make coverage-report  # Opens htmlcov/index.html
```

**Expected workflow:**
1. Write new code in `src/services/foo.py`
2. Run `make test-coverage`
3. See `src/services/foo.py` at 45% coverage
4. Add tests until 90%+
5. Commit code + tests together

### IDE Integration (Future Enhancement)

**VSCode Coverage Gutters:**
- Install: `Coverage Gutters` extension
- Run tests with coverage
- See green/red indicators in editor gutter
- Instantly see which lines are covered

**PyCharm Coverage:**
- Run → Run with Coverage
- IDE highlights uncovered lines in red
- Click to jump to missing tests

---

## Acceptance Criteria

### Functional Requirements

- [ ] Coverage measured for all test runs (local and CI)
- [ ] Terminal reports show percentage + missing line numbers
- [ ] HTML reports generated with browsable interface
- [ ] XML reports generated for Codecov parsing
- [ ] Coverage threshold enforced at 80% minimum
- [ ] CI fails if coverage drops below 80%
- [ ] Branch coverage measured (not just line coverage)
- [ ] Codecov.io integrated with PR comments
- [ ] Coverage badges in README.md
- [ ] Coverage reports uploaded as CI artifacts

### Configuration Requirements

- [ ] `pyproject.toml` has `[tool.coverage.*]` sections
- [ ] pytest `addopts` includes `--cov` flags
- [ ] `codecov.yml` exists with thresholds configured
- [ ] `.gitignore` excludes coverage artifacts
- [ ] `CODECOV_TOKEN` secret added to GitHub

### Workflow Requirements

- [ ] Coverage collected in all 3 test jobs (unit, integration, docker)
- [ ] Coverage uploaded to Codecov from each job
- [ ] Combined coverage report generated in summary job
- [ ] PR comments show coverage delta
- [ ] Status checks block merge if below threshold

### Documentation Requirements

- [ ] README.md explains coverage target (80%)
- [ ] README.md shows coverage badge
- [ ] CONTRIBUTING.md documents coverage workflow
- [ ] Makefile includes coverage commands
- [ ] Inline comments explain coverage configuration

---

## Testing Strategy

### Test the Coverage System Itself

**Test 1: Verify Coverage Measurement**
```bash
# Run tests with coverage
make test-coverage

# Expected: Coverage report generated
# Expected: htmlcov/ directory created
# Expected: coverage.xml file created
# Expected: Terminal shows coverage percentage
```

**Test 2: Verify Threshold Enforcement**
```bash
# Temporarily set high threshold
# In pyproject.toml: fail_under = 99.0
make test-coverage

# Expected: Tests fail with coverage error
# Expected: Clear message about threshold violation

# Restore threshold to 80
```

**Test 3: Verify CI Coverage Collection**
```bash
# Create test PR
git checkout -b test/coverage-ci
git push origin test/coverage-ci

# Expected: Workflow runs
# Expected: Coverage steps execute
# Expected: Codecov upload succeeds
# Expected: PR comment appears with coverage
```

**Test 4: Verify Codecov PR Comments**
```bash
# Create PR that reduces coverage
# - Remove tests from existing file
# - Push to PR

# Expected: Codecov comment shows decreased coverage
# Expected: Status check fails
# Expected: Cannot merge PR
```

**Test 5: Verify Coverage Artifacts**
```bash
# After CI run, go to Actions → Test Suite → Artifacts
# Download coverage reports

# Expected: htmlcov/ folder downloadable
# Expected: coverage.xml downloadable
# Expected: Can open index.html locally
```

---

## Edge Cases & Considerations

### Case 1: Coverage Drops Due to Refactoring

**Scenario:** Developer refactors code, coverage temporarily drops

**Solution:**
```yaml
# codecov.yml
coverage:
  status:
    project:
      default:
        threshold: 2%  # Allow 2% temporary decrease
```

**Benefit:** Allows refactoring without immediate test burden

**Risk:** Must ensure coverage recovers in subsequent PRs

### Case 2: Untestable Code

**Scenario:** Code that can't be easily tested (complex I/O, external dependencies)

**Solution:**
```python
def complex_io_operation():
    """Complex I/O that's hard to test."""
    # pragma: no cover
    ...
```

**Guidelines:**
- Use sparingly (only for genuinely untestable code)
- Requires code review justification
- Document why coverage excluded

### Case 3: Flaky Coverage in CI

**Scenario:** Coverage varies between CI runs due to timing/concurrency

**Solution:**
```toml
[tool.coverage.run]
parallel = true  # Enable parallel mode
concurrency = ["thread", "multiprocessing"]
```

**Additional:**
- Use `coverage combine` to merge results
- Ensure consistent test execution order

### Case 4: Coverage for Async Code

**Scenario:** Async functions not covered properly

**Solution:**
```python
# Use pytest-asyncio properly
@pytest.mark.asyncio
async def test_async_function():
    result = await async_function()
    assert result == expected
```

**Configuration:**
```toml
[tool.coverage.run]
concurrency = ["thread", "greenlet"]  # For asyncio
```

### Case 5: Coverage Differs Local vs. CI

**Scenario:** 82% locally, 78% in CI

**Debugging:**
```bash
# Compare .coverage files
coverage report --show-missing > local.txt

# In CI, download coverage.xml artifact
# Compare missing lines
diff local.txt ci.txt
```

**Common causes:**
- Different test execution (CI runs more/fewer tests)
- Environment-specific code paths
- Mock differences

---

## Performance Implications

### Test Execution Time

**Before (no coverage):**
```
Unit tests: 45 seconds
Integration tests: 90 seconds
Total: 135 seconds (2.25 minutes)
```

**After (with coverage):**
```
Unit tests: 52 seconds (+7s, +15%)
Integration tests: 98 seconds (+8s, +9%)
Coverage report generation: 3 seconds
Total: 153 seconds (2.55 minutes)
```

**Impact:** +18 seconds total (+13% overhead)

**Mitigation:**
- Coverage overhead is acceptable for quality gain
- Can disable coverage for quick local runs: `pytest --no-cov`
- Use `--cov-report=term` only for fastest CI runs

### CI/CD Time

**Current CI/CD duration (STORY-007):**
```
Unit tests: 3 minutes
Integration tests: 5 minutes
Docker tests: 8 minutes
Total: ~8 minutes (parallel jobs)
```

**With coverage added:**
```
Unit tests: 3.5 minutes (+0.5 min for coverage)
Integration tests: 5.5 minutes (+0.5 min for coverage)
Docker tests: 8.5 minutes (+0.5 min for coverage)
Coverage summary job: 2 minutes (combine + upload)
Total: ~10 minutes (parallel jobs)
```

**Impact:** +2 minutes total workflow time (+25%)

**Benefit:** Worth it for coverage visibility and enforcement

### Storage Impact

**Coverage artifacts per CI run:**
```
coverage.xml: ~50 KB
htmlcov/: ~500 KB (zipped)
Total per run: ~550 KB
```

**Monthly storage (140 runs/month):**
```
550 KB × 140 = ~77 MB/month
```

**GitHub Actions artifact retention:** 90 days default

**Cost:** Negligible (well within free tier)

---

## Rollback Plan

If coverage causes critical issues:

### Immediate (5 minutes)

**Disable coverage in CI:**
```yaml
# .github/workflows/test.yml
# Comment out coverage steps:

# - name: Run unit tests with coverage
#   run: |
#     uv run pytest tests/services tests/models tests/api \
#       --cov=src \
#       --cov-report=term-missing \
#       --cov-report=html \
#       --cov-report=xml \
#       --cov-fail-under=80
```

**Or set threshold to 0:**
```yaml
env:
  COVERAGE_THRESHOLD: '0'  # Temporarily disable enforcement
```

### Short-term (30 minutes)

**Revert pyproject.toml changes:**
```bash
git revert <commit-hash>  # Revert coverage configuration commit
git push origin main
```

**Remove Codecov integration:**
```yaml
# Comment out Codecov upload steps in workflow
```

### Long-term (if persistent issues)

**Remove coverage entirely:**
1. Remove `--cov*` flags from pytest commands
2. Remove `[tool.coverage.*]` from pyproject.toml
3. Remove Codecov steps from workflow
4. Remove coverage badges from README
5. Document decision in ARCHITECTURE.md

**Reason to remove:**
- Coverage slows CI by >50%
- Frequent false failures
- Team unable to maintain 80% threshold
- Coverage tools incompatible with dependencies

---

## Monitoring & Validation

### Weekly Metrics Review

**Metrics to track:**
1. **Overall coverage trend** (should be stable or increasing)
2. **PR coverage delta** (new code should be 100% covered)
3. **Failed PRs due to coverage** (should decrease over time)
4. **Coverage by module** (identify gaps)

**Dashboard:** Codecov.io → Graphs → Coverage over time

**Action items:**
- If coverage drops >2%: Investigate and file tickets
- If module coverage <70%: Schedule refactoring sprint
- If PR failures >30%: Adjust threshold or improve docs

### Automated Alerts

**GitHub Actions:**
```yaml
- name: Notify on coverage drop
  if: failure() && contains(github.event.head_commit.message, 'coverage')
  uses: 8398a7/action-slack@v3
  with:
    status: ${{ job.status }}
    text: 'Coverage check failed on ${{ github.ref }}'
    webhook_url: ${{ secrets.SLACK_WEBHOOK }}
```

**Codecov:**
- Enable email notifications for coverage drops
- Configure Slack integration for weekly summaries

### Manual Review (Monthly)

**Coverage audit checklist:**
- [ ] Review uncovered lines in critical modules
- [ ] Identify patterns in missing coverage
- [ ] File tickets for untested edge cases
- [ ] Update coverage exclusions if justified
- [ ] Review coverage trend (should be improving)
- [ ] Celebrate coverage milestones (85%, 90%, 95%)

---

## Future Enhancements

### After Initial Implementation

**Phase 2: Advanced Coverage (Month 2-3)**
1. **Mutation testing** - Verify tests actually catch bugs (pytest-mutpy)
2. **Coverage exceptions** - Formalize `# pragma: no cover` policy
3. **Differential coverage** - 100% for new code, 80% overall
4. **Coverage by test type** - Separate unit vs integration coverage

**Phase 3: Coverage Optimization (Month 4-6)**
1. **Parallel coverage** - Speed up collection with pytest-xdist
2. **Incremental coverage** - Only measure changed files
3. **Coverage caching** - Reuse coverage for unchanged code
4. **Smart test selection** - Run only tests affected by changes

**Phase 4: Coverage Culture (Ongoing)**
1. **Coverage leaderboard** - Gamify coverage improvements
2. **Coverage training** - Team workshops on effective testing
3. **Coverage retrospectives** - Learn from coverage gaps
4. **Coverage milestones** - Celebrate 85%, 90%, 95% achievements

---

## Dependencies

### Before This Story

**Required:**
- ✅ STORY-007: CI/CD Test Automation (must have GitHub Actions workflow)
- ✅ BUG-006: Test Suite Failures (all tests must pass before enforcing coverage)

**Optional:**
- Test markers configured (helps split coverage by test type)

### After This Story

**Enables:**
- STORY-009: Core Pipeline Testing (coverage guides where to add tests)
- STORY-010: Test Optimization (coverage shows which tests are redundant)
- Quality reviews (coverage delta visible in PRs)

**Blocks:**
- None (this is a quality enhancement, not a blocker)

---

## Definition of Done

### Code
- [ ] `pyproject.toml` has complete `[tool.coverage.*]` configuration
- [ ] Coverage reports configured: terminal, HTML, XML
- [ ] Coverage threshold set to 80% (`fail_under = 80.0`)
- [ ] Branch coverage enabled (`branch = true`)
- [ ] `.gitignore` updated with coverage artifacts

### CI/CD
- [ ] Coverage steps added to all 3 test jobs (unit, integration, docker)
- [ ] Coverage threshold enforced in CI (`--cov-fail-under=80`)
- [ ] Coverage reports uploaded as CI artifacts
- [ ] Codecov action configured and tested
- [ ] `CODECOV_TOKEN` secret added to GitHub
- [ ] Coverage summary job combines and validates coverage

### Codecov Integration
- [ ] `codecov.yml` created with project/patch thresholds
- [ ] Codecov.io account connected to repository
- [ ] PR comments enabled and tested
- [ ] Coverage status checks appear in PRs
- [ ] Coverage badges added to README.md
- [ ] Coverage trends visible in Codecov dashboard

### Documentation
- [ ] README.md explains 80% coverage requirement
- [ ] README.md shows test and coverage badges
- [ ] CONTRIBUTING.md documents coverage workflow
- [ ] Makefile has `test-coverage`, `coverage-report`, `coverage-check` commands
- [ ] Inline comments explain coverage configuration

### Testing
- [ ] Baseline coverage measured and documented
- [ ] Coverage reports generated locally
- [ ] Coverage reports generated in CI
- [ ] Codecov PR comment tested with sample PR
- [ ] Coverage threshold enforcement tested (fails below 80%)
- [ ] Coverage artifacts downloadable from GitHub Actions

### Verification
- [ ] First PR with coverage reporting merged
- [ ] Coverage badge visible in README
- [ ] Codecov dashboard shows coverage trends
- [ ] Team trained on coverage workflow
- [ ] Coverage reports reviewed in PR process

---

## References

- **pytest-cov documentation:** https://pytest-cov.readthedocs.io/
- **Coverage.py documentation:** https://coverage.readthedocs.io/
- **Codecov documentation:** https://docs.codecov.com/
- **GitHub Actions with Coverage:** https://docs.github.com/en/actions/automating-builds-and-tests/building-and-testing-python
- **pytest-cov GitHub:** https://github.com/pytest-dev/pytest-cov

---

## Related Stories

- **STORY-007:** CI/CD Test Automation (prerequisite)
- **BUG-006:** Test Suite Failures (must fix first)
- **STORY-009:** Core Pipeline Testing (uses coverage to guide tests)
- **STORY-010:** Test Optimization (coverage shows redundant tests)
- **STORY-011:** Test Parameterization (coverage shows edge cases)
