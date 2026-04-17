# How to run the test suite

Three commands cover the day-to-day cases. For the rationale behind the three tiers, see [testing strategy](../explanation/testing-strategy.md). For the full marker list, see [CI workflows reference](../reference/ci-workflows.md).

## Quick commands

```bash
make test-fast          # Unit tests, ~30s — run before every commit
make test-integration   # Integration tests, ~2min — run before PRs
make test-e2e           # End-to-end with real Bedrock, ~5min — run before merges
```

Everything runs in the Docker stack. Don't run `pytest` on the host.

## Run a specific test

```bash
# One file
docker compose exec api-gateway uv run pytest tests/unit/services/test_storage_service.py -v

# One test by name
docker compose exec api-gateway uv run pytest tests/unit/services/test_storage_service.py::test_upload_retries -v

# Integration tests directly (testcontainers will spin up Redis + Floci)
docker compose exec api-gateway uv run pytest tests/integration -m integration -v
```

## Debug a failing test

```bash
# Verbose, show print output
docker compose exec api-gateway uv run pytest tests/path/to/test.py -vvs

# Drop into pdb on first failure
docker compose exec api-gateway uv run pytest tests/path/to/test.py --pdb
```

If it fails in CI but passes locally, see [how to debug a CI failure](debug-ci-failures.md).

## Shared fixtures

Use fixtures from `tests/conftest_fixtures/` — don't re-invent them:

```python
from tests.conftest_fixtures.clients import (
    mock_redis_client,      # AsyncMock
    mock_s3_client,         # MagicMock
    mock_ai_service,        # AsyncMock
    mock_presidio_analyzer, # MagicMock
)
from tests.conftest_fixtures.data_factories import (
    generate_job_id,
    create_pii_queue_payload,
    create_test_pdf_content,
    create_test_upload_file,
)
from tests.conftest_fixtures.helpers import (
    assert_job_state,
    assert_s3_upload,
    setup_redis_error,
)
```

## Coverage

```bash
make coverage        # Run tests with coverage
make coverage-html   # Generate + open HTML report locally
```

CI publishes per-tier coverage as workflow artifacts on every run.
