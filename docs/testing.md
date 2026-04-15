# Testing Strategy

## 3-Tier Testing Strategy

### Unit Tests (`tests/unit/`)

- Fully mocked dependencies
- Fast (<100ms per test)
- No Docker required
- Tests business logic only

### Integration Tests (`tests/integration/`)

- **Uses:** Python testcontainers library (redis:7-alpine, floci)
- **Execution:** Host machine via `make test-integration` (not inside Docker)
- **Isolation:** Fresh containers per test session, true test isolation
- **Services:** Real Redis + Real S3 (Floci), AI/ML mocked
- **Speed:** ~2 minutes total
- **Benefits:** Catches serialization bugs, race conditions, Redis atomicity

### E2E Tests (`tests/e2e/`)

- Full workflows with minimal mocking
- Slow (<30s per test)
- Validates complete processing pipeline

## Shared Test Fixtures

**IMPORTANT:** Always use shared fixtures from `tests/conftest_fixtures/`:

```python
# Mock Clients
from tests.conftest_fixtures.clients import (
    mock_redis_client,      # AsyncMock for Redis
    mock_s3_client,         # MagicMock for S3
    mock_ai_service,        # AsyncMock for AI
    mock_presidio_analyzer  # MagicMock for PII
)

# Data Factories
from tests.conftest_fixtures.data_factories import (
    generate_job_id,                # Generate UUID
    create_pii_queue_payload,       # Create queue message
    create_test_pdf_content,        # Generate minimal PDF
    create_test_upload_file         # Create FastAPI UploadFile
)

# Test Helpers
from tests.conftest_fixtures.helpers import (
    assert_job_state,         # Assert job status
    assert_s3_upload,         # Assert S3 called correctly
    setup_redis_error         # Configure error scenarios
)
```

## Running Tests

```bash
# Fast feedback (before commit)
make test-fast          # ~30s with parallelization

# Before opening PR (requires Docker Desktop running)
make test-integration   # ~2min with testcontainers

# Before merging
make test-e2e           # ~5min full workflows

# Run specific test
uv run pytest tests/unit/services/test_storage_service.py::test_name -v

# Run integration tests directly
uv run pytest tests/integration -m integration -v

# Debug with verbose output
uv run pytest tests/path/to/test.py -vvs
```

## Test Markers

Use pytest markers for selective execution:

```python
@pytest.mark.unit                # Unit test (fast, mocked)
@pytest.mark.integration         # Integration test (testcontainers)
@pytest.mark.slow                # E2E test (>5s)
@pytest.mark.requires_redis      # Needs Redis
@pytest.mark.requires_s3         # Needs S3/Floci
@pytest.mark.requires_ai         # Needs AI/Bedrock
@pytest.mark.performance         # Performance tests
@pytest.mark.resilience          # Resilience/fault tolerance tests
@pytest.mark.edge_case           # Edge case coverage
```

Run specific markers:

```bash
pytest -m unit                   # Unit tests only
pytest -m integration            # Integration tests only
pytest -m "not slow"             # Skip slow tests
```

## Coverage

```bash
make coverage       # Run tests with coverage
make coverage-html  # Generate and open HTML report
```
