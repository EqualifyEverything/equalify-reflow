# Contributing to Equalify Reflow

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Development Workflow

### Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+)
- **Git**

### Getting Started

1. **Fork and clone the repository:**
   ```bash
   git clone <your-fork-url>
   cd equalify-pdf-converter
   ```

2. **Start the development environment:**
   ```bash
   make dev
   ```

3. **Verify everything is running:**
   ```bash
   make health
   curl http://localhost:8080/health
   ```

4. **View API documentation:**
   ```bash
   open http://localhost:8080/docs
   ```

### Making Changes

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** in the `src/` directory
   - Code changes auto-reload via hot reload
   - No need to rebuild containers

3. **Run tests:**
   ```bash
   # Fast feedback
   make test-fast

   # Before opening PR
   make test-integration

   # Before merging
   make test-e2e
   ```

4. **Check coverage:**
   ```bash
   make coverage
   make coverage-html  # View detailed report
   ```

5. **Commit your changes:**
   ```bash
   git add .
   git commit -m "feat: add feature description"
   ```

6. **Push and create PR:**
   ```bash
   git push origin feature/your-feature-name
   ```

## Code Standards

### Python Standards

- **Package Management:** ALL Python development uses `uv`
  ```bash
  # Add dependencies
  docker exec -it equalify-pdf-api-gateway uv add <package>

  # Install dev dependencies
  docker exec -it equalify-pdf-api-gateway uv add --dev <package>
  ```

- **Code Style:**
  - Follow PEP 8 guidelines
  - Use type hints for function signatures
  - Docstrings for public functions and classes
  - Maximum line length: 100 characters

- **Imports:**
  ```python
  # Standard library
  import os
  from typing import Optional

  # Third-party
  from fastapi import FastAPI
  from pydantic import BaseModel

  # Local
  from src.shared.models import Job
  from src.services import storage_service
  ```

### Docker Standards

- **Always use containerized development:**
  ```bash
  # ✅ Correct
  make dev
  make shell
  make test-docker

  # ❌ Incorrect (don't run on host)
  uv run uvicorn ...
  python ...
  pytest ...
  ```

- **Service Communication:**
  - Use Docker DNS names: `redis:6379`, `localstack:4566`
  - Never use `localhost` in application code
  - Use environment variables for configuration

### Testing Standards

#### Test Organization

Place tests in the appropriate tier:

- **Unit Tests** (`tests/unit/`)
  - Pure logic testing
  - Mocked dependencies
  - Fast execution (<100ms per test)
  - No Docker required

- **Integration Tests** (`tests/integration/`)
  - Real Redis/S3 via testcontainers
  - Test service interactions
  - Medium execution (<5s per test)
  - Docker required

- **E2E Tests** (`tests/e2e/`)
  - Full workflow testing
  - Minimal mocking
  - Slow execution (<30s per test)
  - Docker required

#### Writing Tests

```python
import pytest
from src.services import storage_service

@pytest.mark.unit
def test_storage_key_generation():
    """Test S3 key generation for temp storage."""
    job_id = "abc123"
    key = storage_service.generate_temp_key(job_id)
    assert key == f"temp/{job_id}.pdf"

@pytest.mark.integration
@pytest.mark.requires_redis
async def test_redis_queue_operations(redis_client):
    """Test Redis queue push and pop operations."""
    queue_name = "eq-pdf:queue:test"
    message = {"job_id": "test123"}

    await redis_client.lpush(queue_name, json.dumps(message))
    result = await redis_client.brpop(queue_name, timeout=1)

    assert result is not None
    assert json.loads(result[1]) == message
```

#### Shared Test Fixtures

**IMPORTANT:** Always use shared fixtures from `tests/conftest_fixtures/` to avoid duplication.

**Available Fixtures:**

```python
# Mock Clients (tests/conftest_fixtures/clients.py)
from tests.conftest_fixtures import (
    mock_redis_client,      # AsyncMock for Redis operations
    mock_s3_client,         # MagicMock for S3 operations
    mock_ai_service,        # AsyncMock for AI service
    mock_presidio_analyzer, # MagicMock for PII detection
)

# Data Factories (tests/conftest_fixtures/data_factories.py)
from tests.conftest_fixtures import (
    generate_job_id,                # Generate unique UUID
    generate_document_id,           # Generate unique UUID
    create_pii_queue_payload,       # Create PII queue payload
    create_processing_queue_payload,# Create processing queue payload
    create_test_pdf_content,        # Generate minimal valid PDF
    create_test_upload_file,        # Create FastAPI UploadFile mock
)

# Test Helpers (tests/conftest_fixtures/helpers.py)
from tests.conftest_fixtures import (
    assert_job_state,         # Assert job status/confidence
    assert_s3_upload,         # Assert S3 upload called correctly
    assert_redis_set,         # Assert Redis set called correctly
    setup_s3_error,           # Configure S3 to raise errors
    setup_redis_error,        # Configure Redis to raise errors
)
```

**Example Usage:**

```python
import pytest
from tests.conftest_fixtures.data_factories import generate_job_id

@pytest.mark.asyncio
async def test_queue_service(mock_redis_client):
    """Test using shared mock_redis_client fixture."""
    from src.services.queue_service import QueueService

    queue_service = QueueService(redis_client=mock_redis_client)
    job_id = generate_job_id()  # Use factory instead of hardcoded UUID

    await queue_service.queue_pii_job(job_id, f"temp/{job_id}/doc.pdf")

    mock_redis_client.lpush.assert_called_once()
```

**Parameterized Tests:**

Use `@pytest.mark.parametrize` to reduce duplicate test code:

```python
@pytest.mark.parametrize("error_type,error_msg,expected", [
    (OSError, "Device not ready", 400),
    (IOError, "File closed", 400),
    (ConnectionError, "Network error", 500),
])
async def test_error_handling(storage_service, error_type, error_msg, expected):
    """Test multiple error scenarios with single test function."""
    storage_service.s3_client.upload_fileobj.side_effect = error_type(error_msg)

    with pytest.raises(HTTPException) as exc:
        await storage_service.store_document(upload_file)

    assert exc.value.status_code == expected
```

**Benefits:**
- **No duplication:** One canonical implementation per fixture
- **Consistency:** All tests use same mock behavior
- **Maintainability:** Change once, apply everywhere
- **Parameterization:** Test multiple scenarios without duplication

#### Test Coverage

- **Target:** >80% overall coverage
- **Current:** 1133 tests passing
- **New Code:** All new code should have tests
- **Bug Fixes:** Add regression tests
- **Critical Paths:** Aim for 100% coverage on critical business logic

#### Running Tests Locally

```bash
# Fast feedback loop
make test-fast

# Before PR
make test-integration

# Before merge
make test-e2e

# All tests
make test-all
```

### Documentation Standards

- **Code Documentation:**
  - Docstrings for all public functions/classes
  - Inline comments for complex logic
  - Type hints for function signatures

- **Markdown Files:**
  - Clear headings and structure
  - Code examples with proper formatting
  - Links to related documentation

- **Update Documentation:**
  - README.md for user-facing changes
  - Architecture docs for system changes
  - API docs via OpenAPI/Swagger

## Git Workflow

### Branching Strategy

- `main` - Production-ready code
- `develop` - Integration branch (if needed)
- `feature/*` - New features
- `fix/*` - Bug fixes
- `docs/*` - Documentation updates

### Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test additions/changes
- `refactor:` Code refactoring
- `chore:` Build process/tooling changes
- `ci:` CI/CD changes

**Examples:**
```bash
feat(api): add document submission endpoint

Implements POST /api/documents/submit with PDF validation,
S3 storage, and Redis queue integration.

Closes #123

fix(worker): handle Redis connection failures

Add retry logic and exponential backoff for Redis
connection errors in PII worker.

docs(readme): update quick start instructions

test(services): add integration tests for storage service
```

### Pull Request Guidelines

1. **PR Title:** Use conventional commit format
2. **Description:** Explain what, why, and how
3. **Tests:** All tests must pass
4. **Coverage:** Maintain or improve coverage
5. **Documentation:** Update relevant docs
6. **Size:** Keep PRs focused and reviewable

**PR Template:**
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] E2E tests added/updated
- [ ] All tests passing locally

## Checklist
- [ ] Code follows project standards
- [ ] Documentation updated
- [ ] No new warnings or errors
- [ ] Coverage maintained/improved
```

## CI/CD

### Automated Checks

All PRs trigger automated checks:

1. **Fast Unit Tests** (<2min)
   - Runs on every push
   - Must pass before merge

2. **Integration Tests** (~5min)
   - Runs on PRs to main/develop
   - Tests Redis/S3 integration

3. **E2E Tests** (~10min)
   - Runs on merge to main
   - Full workflow validation

4. **Coverage Report**
   - Generated for all test runs
   - Available as workflow artifacts

### CI Status

- PRs cannot merge until all tests pass
- Coverage must not decrease significantly
- All workflows must be green

See [CI/CD Documentation](docs/ci-cd.md) for detailed information.

## Common Development Tasks

### Adding a New API Endpoint

1. Define route in `src/api/`
2. Add business logic in `src/services/`
3. Create Pydantic models in `src/shared/models/`
4. Write unit tests in `tests/unit/api/`
5. Add integration tests in `tests/integration/`
6. Update API documentation

### Adding a New Service

1. Create service file in `src/services/`
2. Add tests in `tests/unit/services/`
3. Update dependency injection in `src/dependencies.py`
4. Document in architecture docs

### Adding a Background Worker

1. Create worker in `src/workers/`
2. Add to startup in `src/main.py`
3. Test with integration tests
4. Document queue structure in Redis docs

### Updating Dependencies

```bash
# Add new dependency
docker exec -it equalify-pdf-api-gateway uv add <package>

# Update existing dependency
docker exec -it equalify-pdf-api-gateway uv add <package>@latest

# Remove dependency
docker exec -it equalify-pdf-api-gateway uv remove <package>
```

## Troubleshooting

### Tests Failing

```bash
# View detailed test output
uv run pytest -vv

# Run specific test
uv run pytest tests/unit/services/test_storage_service.py::test_name

# Debug with prints
uv run pytest -s tests/path/to/test.py
```

### Container Issues

```bash
# View logs
make logs
make logs-api

# Restart services
make down
make dev

# Clean everything
make clean
make dev
```

### Coverage Not Updating

```bash
# Clear coverage data
rm -rf .coverage htmlcov/

# Regenerate coverage
make coverage-html
```

## Getting Help

- **Documentation:** Check [docs/](docs/) directory
- **Issues:** Search existing GitHub issues
- **Questions:** Open a GitHub Discussion
- **PRD Context:** See [ai-docs/PRDs/](ai-docs/PRDs/)

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on what is best for the project
- Show empathy towards others

## Recognition

Contributors are recognized in:
- GitHub contributor graphs
- Release notes for significant contributions
- Project documentation credits

Thank you for contributing to Equalify Reflow! 🎉
