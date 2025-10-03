# STORY-007: CI/CD Test Automation with GitHub Actions

**Priority:** P0 - CRITICAL
**Severity:** Blocker - Tests not enforced in development workflow
**Estimated Effort:** 4-6 hours
**Status:** 📋 READY FOR DEVELOPMENT
**Dependencies:** None
**Related:** BUG-006 (test failures must be fixed before CI/CD can pass)

---

## Problem Statement

The test suite currently runs only when developers manually execute `make test-docker`. This leads to:

- ❌ **Untested code reaching main branch** - No automatic verification before merge
- ❌ **Breaking changes undetected** - Tests may fail but PRs still merge
- ❌ **Inconsistent test execution** - Developers may skip tests locally
- ❌ **No quality gates** - No enforcement of test success or coverage thresholds
- ❌ **Delayed feedback** - Bugs discovered in production instead of PR stage

**Current Workflow:**
```
Developer → Code → Manual test → PR → Manual review → Merge → 🤞 Hope it works
```

**Target Workflow:**
```
Developer → Code → Auto test → PR → Auto test + coverage → Review → Merge → ✅ Confidence
```

---

## Success Criteria

### Must Have (P0)
- [ ] Tests run automatically on every push to any branch
- [ ] Tests run automatically on every pull request
- [ ] PR cannot merge if tests fail
- [ ] Test results visible in PR (pass/fail status check)
- [ ] Workflow runs in <5 minutes for fast feedback
- [ ] Caching configured to speed up subsequent runs
- [ ] All 572 tests execute in CI environment

### Should Have (P1)
- [ ] Separate fast (unit) and slow (integration) test jobs
- [ ] Test failures show clear error messages in PR
- [ ] Workflow status badges in README
- [ ] Slack/email notifications for main branch failures
- [ ] Workflow can be manually triggered for debugging

### Nice to Have (P2)
- [ ] Parallel test execution across multiple workers
- [ ] Test result artifacts uploaded for debugging
- [ ] Flaky test detection and reporting
- [ ] Performance regression tracking

---

## Technical Design

### GitHub Actions Workflow Structure

**File:** `.github/workflows/test.yml`

```yaml
name: Test Suite

on:
  push:
    branches: ['**']  # All branches
  pull_request:
    branches: [main, develop]
  workflow_dispatch:  # Manual trigger

env:
  PYTHON_VERSION: '3.11'

jobs:
  # Job 1: Fast unit tests (< 2 min)
  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

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

      - name: Run unit tests
        run: |
          uv run pytest tests/services tests/models tests/api \
            -v \
            --tb=short \
            -m "not integration and not slow" \
            --maxfail=5

      - name: Upload unit test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: unit-test-results
          path: .pytest_cache/

  # Job 2: Integration tests with Docker services (< 5 min)
  integration-tests:
    name: Integration Tests
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

      - name: Run integration tests
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
            --maxfail=3

      - name: Upload integration test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: integration-test-results
          path: .pytest_cache/

  # Job 3: Docker-based full test suite (fallback/verification)
  docker-tests:
    name: Full Test Suite (Docker)
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Checkout code
        uses: actions/checkout@v4

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

      - name: Run tests in Docker
        run: make test-docker

      - name: Upload Docker test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: docker-test-results
          path: .pytest_cache/

  # Job 4: Test result summary
  test-summary:
    name: Test Summary
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests, docker-tests]
    if: always()

    steps:
      - name: Check test results
        run: |
          echo "Unit Tests: ${{ needs.unit-tests.result }}"
          echo "Integration Tests: ${{ needs.integration-tests.result }}"
          echo "Docker Tests: ${{ needs.docker-tests.result }}"

          if [ "${{ needs.unit-tests.result }}" != "success" ] || \
             [ "${{ needs.integration-tests.result }}" != "success" ] || \
             [ "${{ needs.docker-tests.result }}" != "success" ]; then
            echo "❌ Tests failed"
            exit 1
          else
            echo "✅ All tests passed"
          fi
```

---

## Branch Protection Rules

**Configuration:** GitHub Repository Settings → Branches → Branch protection rules

**For `main` branch:**
```yaml
Required status checks:
  - Unit Tests
  - Integration Tests
  - Full Test Suite (Docker)

Required:
  ✅ Require status checks to pass before merging
  ✅ Require branches to be up to date before merging
  ✅ Require conversation resolution before merging

Optional:
  ✅ Require pull request reviews (1 approval)
  ❌ Allow force pushes (disabled)
  ❌ Allow deletions (disabled)
```

**For `develop` branch (if used):**
```yaml
Same as main, but:
  - Optional: Can bypass with admin override
```

---

## Test Markers Configuration

**Update:** `pyproject.toml`

```toml
[tool.pytest.ini_options]
markers = [
    "unit: Unit tests (fast, isolated)",
    "integration: Integration tests (with Redis/S3)",
    "slow: Slow-running tests (>1s)",
    "requires_redis: Needs Redis service",
    "requires_s3: Needs S3/LocalStack service",
    "requires_ai: Needs Anthropic API (mocked in CI)",
]

# Default test options for CI
addopts = """
    -v
    --tb=short
    --strict-markers
    --maxfail=10
    --disable-warnings
"""
```

---

## Caching Strategy

### 1. **Python Dependencies Cache**
```yaml
- uses: actions/cache@v4
  with:
    path: |
      ~/.cache/uv
      ~/.cache/pip
    key: ${{ runner.os }}-python-${{ hashFiles('**/pyproject.toml') }}
```

**Benefit:** Reduces dependency install time from 60s → 10s

### 2. **Docker Layer Cache**
```yaml
- uses: docker/build-push-action@v5
  with:
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Benefit:** Reduces Docker build time from 5min → 30s

### 3. **Pytest Cache**
```yaml
- uses: actions/cache@v4
  with:
    path: .pytest_cache
    key: ${{ runner.os }}-pytest-${{ hashFiles('tests/**/*.py') }}
```

**Benefit:** Faster test collection and fixture setup

---

## Notification Strategy

### PR Status Checks
- ✅ Inline status in PR conversation
- ✅ Commit status badges
- ✅ Workflow run links

### Main Branch Failures
```yaml
# Add to workflow
- name: Notify on failure
  if: failure() && github.ref == 'refs/heads/main'
  uses: 8398a7/action-slack@v3
  with:
    status: ${{ job.status }}
    text: 'Tests failed on main branch!'
    webhook_url: ${{ secrets.SLACK_WEBHOOK }}
```

---

## Migration Plan

### Phase 1: Setup (Week 1)
**Owner:** DevOps/Tech Lead
**Duration:** 4 hours

1. Create `.github/workflows/test.yml` workflow file
2. Configure GitHub Actions secrets (if needed)
3. Test workflow on feature branch
4. Verify all 572 tests pass in CI
5. Fix any CI-specific failures

### Phase 2: Branch Protection (Week 1)
**Owner:** Tech Lead
**Duration:** 30 minutes

1. Enable branch protection on `main`
2. Require status checks
3. Test with draft PR
4. Document workflow in CONTRIBUTING.md

### Phase 3: Optimization (Week 2)
**Owner:** Team
**Duration:** 2 hours

1. Add test markers (`@pytest.mark.unit`, etc.)
2. Split slow tests into separate job
3. Optimize caching
4. Add parallel execution if needed

### Phase 4: Monitoring (Ongoing)
**Owner:** Team
**Duration:** Ongoing

1. Monitor workflow execution times
2. Identify and fix flaky tests
3. Optimize slow tests
4. Update as dependencies change

---

## Cost Analysis

### GitHub Actions Free Tier
- **2,000 minutes/month** for private repos
- **Unlimited** for public repos

### Expected Usage
```
Workflow duration: 8 minutes (unit + integration + docker)
PRs per month: ~40
Pushes per month: ~100

Total: (40 + 100) × 8 = 1,120 minutes/month
```

**Verdict:** ✅ Well within free tier

### Optimization Opportunities
- Skip Docker job if unit + integration pass
- Use matrix for parallel execution
- Cache more aggressively

---

## Rollback Plan

If CI/CD causes issues:

1. **Disable branch protection** (1 minute)
   - Remove required status checks
   - Allow merging without CI

2. **Disable workflow** (1 minute)
   - Add `if: false` to workflow
   - Or delete `.github/workflows/test.yml`

3. **Revert to manual testing** (immediate)
   - Continue with `make test-docker`
   - Fix issues offline

---

## Testing the CI/CD Workflow

### Before Enabling Branch Protection

**Test Workflow:**
1. Create feature branch: `git checkout -b test/ci-cd-workflow`
2. Add workflow file: `.github/workflows/test.yml`
3. Push branch: `git push origin test/ci-cd-workflow`
4. Create draft PR
5. Verify workflow runs automatically
6. Check all jobs pass
7. Review logs for any issues

**Expected Results:**
- ✅ Workflow triggers on push
- ✅ All jobs execute
- ✅ Tests pass (after fixing BUG-006)
- ✅ Status check appears in PR
- ✅ Execution time < 10 minutes

**If Tests Fail:**
- Fix issues identified in BUG-006 first
- Verify tests pass locally: `make test-docker`
- Re-run workflow after fixes

---

## Documentation Updates

### 1. **README.md**
Add workflow status badge:
```markdown
# Equalify PDF Converter

[![Test Suite](https://github.com/org/equalify-pdf-converter/actions/workflows/test.yml/badge.svg)](https://github.com/org/equalify-pdf-converter/actions/workflows/test.yml)

## Development

### Running Tests
```bash
# Local (Docker)
make test-docker

# Local (fast unit tests only)
uv run pytest tests/services -m "not integration"
```

### CI/CD
All tests run automatically on:
- Every push to any branch
- Every pull request to main/develop
- Manual workflow dispatch

PRs cannot merge until all tests pass.
```

### 2. **CONTRIBUTING.md**
```markdown
## Testing Requirements

Before submitting a PR:

1. **Run tests locally:**
   ```bash
   make test-docker
   ```

2. **Verify CI passes:**
   - GitHub Actions will run automatically
   - Check status in PR
   - Fix any failures before requesting review

3. **Test markers:**
   - `@pytest.mark.unit` - Fast, isolated tests
   - `@pytest.mark.integration` - Requires Redis/S3
   - `@pytest.mark.slow` - Long-running tests (>1s)

4. **Branch protection:**
   - PRs to `main` require all tests to pass
   - Must be up-to-date with main branch
```

---

## Success Metrics

### Immediate (Week 1)
- [ ] Workflow file created and tested
- [ ] All tests pass in CI environment
- [ ] Branch protection enabled
- [ ] First PR merged using CI/CD

### Short-term (Month 1)
- [ ] 100% of PRs blocked if tests fail
- [ ] Average workflow duration < 8 minutes
- [ ] Zero manual "trust me, tests pass" merges
- [ ] Team comfortable with workflow

### Long-term (Quarter 1)
- [ ] Reduced production bugs by 50%
- [ ] Faster PR review cycle (tests give confidence)
- [ ] No test-related incidents in production
- [ ] Test suite runs 500+ times/month automatically

---

## Known Limitations & Mitigations

### Limitation 1: AI API Rate Limits
**Issue:** Anthropic API may rate limit in CI
**Mitigation:** Mock AI calls in CI tests (already done)

### Limitation 2: Docker Build Time
**Issue:** Full Docker build takes 5+ minutes
**Mitigation:**
- Use aggressive layer caching
- Make Docker job optional (run only on main)

### Limitation 3: Flaky Tests
**Issue:** Some integration tests may be flaky
**Mitigation:**
- Add retry logic: `pytest --retries 3`
- Track flaky tests and fix root causes

### Limitation 4: Secret Management
**Issue:** Tests need API keys/secrets
**Mitigation:**
- Use GitHub secrets for sensitive values
- Mock external services in most tests

---

## Dependencies

### Before This Story
1. **BUG-006 fixes** - All 27 failing tests must pass
2. **Test markers** - Add unit/integration/slow markers (can be done in parallel)

### After This Story
1. **STORY-008: Coverage Reporting** - Add coverage to CI workflow
2. **STORY-010: Test Optimization** - Speed up slow tests
3. **STORY-011: Test Separation** - Split integration tests further

---

## Definition of Done

### Code
- [ ] `.github/workflows/test.yml` created with all 3 jobs
- [ ] Test markers added to `pyproject.toml`
- [ ] Caching configured for dependencies and Docker
- [ ] Workflow tested on feature branch

### Configuration
- [ ] Branch protection enabled on `main`
- [ ] Required status checks configured
- [ ] GitHub Actions secrets set (if needed)

### Documentation
- [ ] README.md updated with badge and test instructions
- [ ] CONTRIBUTING.md updated with CI/CD workflow
- [ ] Workflow inline comments explain each step

### Verification
- [ ] Workflow runs successfully on push
- [ ] Workflow runs successfully on PR
- [ ] All 572 tests pass in CI
- [ ] PR cannot merge if tests fail
- [ ] Workflow execution time < 10 minutes
- [ ] Team trained on new workflow

### Monitoring
- [ ] First successful PR merged via CI/CD
- [ ] First failed PR blocked by CI/CD
- [ ] Workflow execution times logged
- [ ] No false failures in first week

---

## Future Enhancements

### After Initial Implementation
1. **Parallel test execution** - Use `pytest-xdist` with `-n auto`
2. **Test sharding** - Split tests across multiple runners
3. **Conditional workflows** - Skip jobs based on changed files
4. **Nightly full test runs** - Comprehensive testing off critical path
5. **Performance benchmarks** - Track test execution trends
6. **Custom GitHub Action** - Package workflow as reusable action

---

## References

- **GitHub Actions Documentation:** https://docs.github.com/en/actions
- **pytest in CI:** https://docs.pytest.org/en/stable/how-to/usage.html#ci-cd
- **Docker in GitHub Actions:** https://docs.docker.com/build/ci/github-actions/
- **Branch Protection:** https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches

---

## Related Stories
- **BUG-006:** Test Suite Failures (must fix first)
- **STORY-008:** Test Coverage Reporting
- **STORY-009:** Core Pipeline Testing
- **STORY-010:** Integration Test Separation
- **STORY-011:** Test Parameterization & Fixture Consolidation
