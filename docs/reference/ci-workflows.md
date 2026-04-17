# CI workflows reference

GitHub Actions workflows that gate the repo. See [testing strategy](../explanation/testing-strategy.md) for the why.

## Workflows

| File | Trigger | Tests | Duration |
|---|---|---|---|
| `.github/workflows/test-fast.yml` | Every push, every branch | `@pytest.mark.unit` | <2min |
| `.github/workflows/test-integration.yml` | PRs to `main`/`develop` | `@pytest.mark.integration` (real Redis + Floci via testcontainers) | ~5min |
| `.github/workflows/test-e2e.yml` | Merges to `main` | `@pytest.mark.slow` (full stack, real Bedrock) | ~10min |
| `.github/workflows/test-performance.yml` | Weekly (Sun 02:00 UTC) + manual | `@pytest.mark.performance` | Varies |

All workflows upload coverage reports as artifacts. PRs cannot merge until applicable workflows pass.

## Pytest markers

Defined in `pyproject.toml`:

| Marker | Purpose |
|---|---|
| `@pytest.mark.unit` | Pure unit test, mocked dependencies, <100ms |
| `@pytest.mark.integration` | Needs real Redis/S3 via testcontainers |
| `@pytest.mark.slow` | E2E test (>5s) |
| `@pytest.mark.requires_redis` | Redis-dependent |
| `@pytest.mark.requires_s3` | S3/Floci-dependent |
| `@pytest.mark.requires_ai` | Real AI/Bedrock calls |
| `@pytest.mark.performance` | Performance benchmark |
| `@pytest.mark.resilience` | Fault tolerance |
| `@pytest.mark.edge_case` | Edge case coverage |

Select a marker: `uv run pytest -m <marker>`. Exclude: `uv run pytest -m "not slow"`.

## CI environment variables

All workflows set these (values come from GitHub Secrets where sensitive):

```yaml
REDIS_URL: redis://localhost:6379
AWS_ENDPOINT_URL: http://localhost:4566
AWS_ACCESS_KEY_ID: test
AWS_SECRET_ACCESS_KEY: test
AWS_DEFAULT_REGION: us-east-1
S3_TEMP_BUCKET: equalify-pdf-temp
S3_RESULTS_BUCKET: equalify-pdf-results
```

## Service containers

Integration and E2E workflows use GitHub Actions service containers:

- Redis: `redis:7-alpine`
- Floci: `hectorvent/floci:1.5.3` (replaces LocalStack)

Bedrock credentials for E2E come from repo secrets; there is no service container for Bedrock.

## Coverage

Run `make coverage` locally for an HTML report. CI publishes per-tier coverage as workflow artifacts — download from the Actions tab for any run. Overall line coverage targets ~82%; we don't pin a specific passing test count in docs since the number fluctuates per PR.
