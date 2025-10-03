# Testing Strategy

## Overview

Equalify PDF Converter uses a comprehensive testing strategy with **real infrastructure** for integration tests to catch bugs that mocked tests miss.

## Test Types

### Unit Tests
**Purpose:** Test individual functions/classes in isolation
**Speed:** Fast (< 1 second per test)
**Infrastructure:** None (all mocked)
**Location:** `tests/services/`, `tests/models/`, `tests/api/`

```bash
make test-unit
```

**Characteristics:**
- ✅ All external dependencies mocked
- ✅ Fast feedback loop
- ✅ Tests business logic only
- ❌ Cannot catch integration bugs

### Integration Tests
**Purpose:** Test real infrastructure integration (Redis, S3, queues)
**Speed:** Slower (2-10 seconds per test)
**Infrastructure:** Real Redis + S3 via **testcontainers**
**Location:** `tests/integration/`

```bash
make test-integration
```

**Characteristics:**
- ✅ Real Redis for queue/job operations
- ✅ Real S3 (LocalStack) for file storage
- ✅ Catches serialization bugs, race conditions, network issues
- ✅ AI/ML components still mocked (expensive, tested separately)
- ⚠️ Requires Docker

**What Integration Tests Catch:**
- Queue serialization bugs (JSON encoding issues)
- Redis connection failures
- S3 upload/download errors
- Race conditions in worker coordination
- Timeout tracking failures
- Job state corruption across services

## Test Markers

Tests are marked with pytest markers for selective execution:

| Marker | Description | Example |
|--------|-------------|---------|
| `unit` | Unit tests (fast, no external deps) | `@pytest.mark.unit` |
| `integration` | Integration tests (testcontainers) | `@pytest.mark.integration` |
| `slow` | Slow tests (>5 sec) | `@pytest.mark.slow` |
| `redis` | Requires Redis | `@pytest.mark.redis` |
| `s3` | Requires S3/LocalStack | `@pytest.mark.s3` |
| `workers` | Background worker tests | `@pytest.mark.workers` |
| `api` | API endpoint tests | `@pytest.mark.api` |

### Running Specific Test Types

```bash
# Unit tests only (fast)
pytest -m "not integration"

# Integration tests only
pytest -m integration

# Slow tests (integration + performance)
pytest -m slow

# All tests
pytest
```

## Testcontainers Architecture

Integration tests use **testcontainers** to spin up real infrastructure:

```python
# Session-scoped containers (reused across tests)
@pytest.fixture(scope="session")
def redis_container():
    """Start Redis container once per test session."""
    container = RedisContainer("redis:7-alpine")
    container.start()
    yield container
    container.stop()

# Per-test cleanup
@pytest.fixture
async def real_redis_client(redis_container):
    """Real Redis client with automatic cleanup."""
    client = await aioredis.from_url(redis_container.get_connection_url())
    yield client
    await client.flushall()  # Clean state after each test
    await client.aclose()
```

### Why Testcontainers?

**Problems with Mocked Integration Tests:**
- ❌ Don't catch serialization bugs (JSON encoding)
- ❌ Don't catch network failures
- ❌ Don't catch race conditions
- ❌ Don't validate queue behavior
- ❌ False sense of security

**Benefits of Real Infrastructure:**
- ✅ Catches real bugs before production
- ✅ Tests actual Redis/S3 behavior
- ✅ Validates serialization/deserialization
- ✅ Tests concurrency and race conditions
- ✅ Per-test isolation via cleanup

## What to Mock vs What to Test

### Always Use Real Infrastructure For:
- ✅ **Redis:** Queue operations, job state, timeout tracking
- ✅ **S3 (LocalStack):** File storage, upload/download, versioning

### Always Mock:
- ❌ **AI/ML:** PII analyzer, PDF converter, AI enhancement (expensive)
- ❌ **External APIs:** Third-party services
- ❌ **Time-based operations:** Use `freezegun` for time control

## Test Structure

### Unit Test Example
```python
@pytest.mark.unit
async def test_job_service_create_job(mock_redis_client):
    """Test job creation logic without real Redis."""
    service = JobService(redis_client=mock_redis_client)
    await service.create_job("job-123", "s3://key", "pending")
    mock_redis_client.hset.assert_called_once()
```

### Integration Test Example
```python
@pytest.mark.integration
@pytest.mark.slow
async def test_worker_flow_real_infrastructure(
    real_redis_client,
    real_s3_client,
    storage_service,
    queue_service,
    job_service
):
    """Test full worker flow with REAL Redis/S3."""
    # Upload to REAL S3
    await storage_service.upload_temp_file(key, content)

    # Create job in REAL Redis
    await job_service.create_job(job_id, key, "pending")

    # Enqueue to REAL Redis
    await queue_service.enqueue("pii-queue", payload)

    # Process job (no mocking)
    job_data = await queue_service.dequeue("pii-queue")
    await worker.process(job_data)

    # Verify REAL state
    final_job = await job_service.get_job(job_id)
    assert final_job["status"] == "completed"
```

## Running Tests

### Local Development

```bash
# All tests
make test

# Unit tests only (fast)
make test-unit

# Integration tests (requires Docker)
make test-integration

# With coverage
make coverage
make coverage-html
```

### CI/CD

GitHub Actions runs three test jobs:

1. **Unit Tests** - Fast, no Docker needed
2. **Integration Tests** - Testcontainers (requires Docker)
3. **Docker Full Suite** - All tests in container

## Coverage Requirements

- **Minimum overall coverage:** 80%
- **Critical paths coverage:** 90%
- **New code coverage:** Must not decrease overall coverage

## Test Data and Fixtures

### Shared Fixtures (`tests/integration/conftest.py`)

- `redis_container` - Session-scoped Redis testcontainer
- `localstack_container` - Session-scoped LocalStack testcontainer
- `real_redis_client` - Real Redis client with cleanup
- `real_s3_client` - Real S3 client with cleanup
- `storage_service` - StorageService with real S3
- `queue_service` - QueueService with real Redis
- `job_service` - JobService with real Redis

### Test Data Fixtures

- `sample_job_id` - UUID job ID
- `sample_s3_key` - S3 object key
- `sample_pdf_content` - Minimal valid PDF
- `sample_pii_findings` - PII detection results

## Debugging Tests

### Integration Test Failures

1. **Check Docker is running:**
   ```bash
   docker ps
   ```

2. **View testcontainer logs:**
   ```bash
   # Containers auto-cleanup, so check during test run
   docker logs <container-id>
   ```

3. **Run single test with verbose output:**
   ```bash
   pytest tests/integration/test_worker_flow.py::TestFullWorkflowCleanPDF::test_clean_pdf_full_workflow -vv
   ```

### Common Issues

**"Cannot connect to Docker"**
- Solution: Ensure Docker daemon is running
- Check: `docker info`

**"Testcontainer timeout"**
- Solution: Increase Docker resource limits
- Docker Desktop → Settings → Resources

**"Redis connection refused"**
- Solution: Testcontainer networking issue
- Check: Container is actually started
- Verify: `redis_container.get_connection_url()` returns valid URL

## Best Practices

### DO ✅
- Use real infrastructure for integration tests
- Mock expensive AI/ML components
- Clean up state after each test
- Use descriptive test names
- Verify actual state (not mock calls)

### DON'T ❌
- Mock everything in integration tests
- Leave containers running after tests
- Test implementation details
- Use production credentials
- Skip cleanup fixtures

## Future Improvements

1. **Performance Testing** - Add load tests for high concurrency
2. **End-to-End Tests** - Full API → Worker → Result flow
3. **Chaos Testing** - Simulate infrastructure failures
4. **Contract Testing** - Validate API contracts with consumers
