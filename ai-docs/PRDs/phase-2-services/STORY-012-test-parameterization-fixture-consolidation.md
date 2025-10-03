# STORY-012: Test Parameterization and Fixture Consolidation

**Priority:** MEDIUM
**Type:** Quality Improvement (Test Refactoring)
**Epic:** Phase 2 - Service Layer Testing Infrastructure
**Created:** 2025-10-03
**Status:** PLANNED

---

## Problem Statement

The test suite (14,272 lines across 45 files, 572+ tests) suffers from significant code duplication and lack of parameterization:

### Code Duplication Issues

1. **Zero Parameterization**: No use of `@pytest.mark.parametrize` across entire test suite
2. **Duplicate Test Logic**: 200-300 LOC savings possible through parameterization
3. **Fixture Fragmentation**: 107 fixtures total, 87 (81%) defined in individual test files instead of shared conftest
4. **Mock Configuration Duplication**: Same mock setup repeated across 15+ files
5. **Hardcoded Test Data**: Same UUID pattern "test-job-123" used in 11+ files

### Quantified Impact

**Current State:**
- **test_pii_accuracy.py**: 388 lines, 17 test cases with repeated mock setup (could be 250 lines with parameterization)
- **test_error_handling.py**: 373 lines, multiple error scenarios per service (could be 250 lines)
- **test_invalid_pdfs.py**: 320 lines, 13 PDF validation scenarios (could be 180 lines)
- **Mock fixtures**: `mock_redis_client` defined 15+ times, `mock_s3_client` 15+ times
- **Test data**: 94 uses of pytest-mock `mocker.` fixture, 32 files use AsyncMock/MagicMock

**Maintenance Burden:**
- Changes to mock structure require updates across 15+ files
- Adding new test scenarios requires duplicating 30-50 lines of setup code
- Inconsistent mock configurations lead to flaky tests
- No single source of truth for test data generation

---

## Success Criteria

### Functional Requirements

1. **Parameterization Coverage**: ≥50% of repetitive tests use `@pytest.mark.parametrize`
2. **Fixture Consolidation**: ≥80% of fixtures moved to shared conftest files
3. **LOC Reduction**: Reduce test suite by 250-350 lines (≈2%)
4. **Test Data Factories**: Eliminate all hardcoded UUIDs with factory pattern
5. **Mock Standardization**: Single canonical mock configuration per service

### Quality Requirements

1. **Zero Test Failures**: All tests must pass after refactoring
2. **Coverage Maintenance**: Test coverage remains ≥90%
3. **Readability Improvement**: Parameterized tests more concise and clear
4. **Maintainability**: Adding new test case = 1 line instead of 30-50

---

## Technical Analysis

### Parameterization Opportunities

#### 1. PII Detection Accuracy Tests (test_pii_accuracy.py)

**Current State (Lines 34-62):**
```python
@pytest.mark.asyncio
async def test_ssn_detection_valid_formats(self, pii_analyzer):
    """Test SSN detection with various valid formats."""
    test_cases = [
        ("My SSN is 123-45-6789", True, "Standard format with dashes"),
        ("SSN: 123456789", True, "No dashes"),
        ("Social Security Number 123-45-6789 required", True, "In sentence"),
    ]

    for text, should_detect, description in test_cases:
        # Mock Presidio response
        if should_detect:
            mock_result = Mock()
            mock_result.entity_type = "US_SSN"
            mock_result.start = text.find("123")
            mock_result.end = mock_result.start + 11
            mock_result.score = 0.95
            pii_analyzer.analyzer.analyze.return_value = [mock_result]
        else:
            pii_analyzer.analyzer.analyze.return_value = []

        findings = pii_analyzer.analyze_text(text)

        if should_detect:
            assert len(findings) > 0, f"Failed to detect SSN: {description}"
            assert findings[0].entity_type == "US_SSN"
            assert findings[0].score >= 0.85
        else:
            assert len(findings) == 0, f"False positive: {description}"
```

**After Parameterization (20 lines → 8 lines):**
```python
@pytest.mark.parametrize("text,should_detect,description", [
    ("My SSN is 123-45-6789", True, "Standard format with dashes"),
    ("SSN: 123456789", True, "No dashes"),
    ("Social Security Number 123-45-6789 required", True, "In sentence"),
])
@pytest.mark.asyncio
async def test_ssn_detection_valid_formats(pii_analyzer, text, should_detect, description):
    """Test SSN detection with various valid formats."""
    # Setup mock based on parameters
    configure_pii_mock(pii_analyzer, "US_SSN", text, should_detect)

    findings = pii_analyzer.analyze_text(text)

    # Verify detection
    assert_pii_detection(findings, "US_SSN", should_detect, description)
```

**Impact**:
- 17 test cases in test_pii_accuracy.py can be parameterized
- Estimated reduction: 138 lines (388 → 250 lines)
- Better test discoverability (pytest shows all parameter sets)

#### 2. Error Handling Scenarios (test_error_handling.py)

**Current State (Lines 33-103):**
```python
@pytest.mark.asyncio
async def test_file_seek_os_error(self, storage_service):
    """Test handling of OSError during file seek operation."""
    mock_file = Mock()
    mock_file.seek.side_effect = OSError("Device not ready")

    upload_file = Mock(spec=UploadFile)
    upload_file.filename = "test.pdf"
    upload_file.file = mock_file
    upload_file.content_type = "application/pdf"

    with pytest.raises(HTTPException) as exc_info:
        await storage_service.store_document(upload_file)

    assert exc_info.value.status_code == 400
    assert "Unable to read file" in exc_info.value.detail
    assert "Device not ready" in exc_info.value.detail

@pytest.mark.asyncio
async def test_file_seek_io_error(self, storage_service):
    """Test handling of IOError during file seek operation."""
    mock_file = Mock()
    mock_file.seek.side_effect = IOError("File handle closed")
    # ... 15 more lines of duplicated setup
```

**After Parameterization:**
```python
@pytest.mark.parametrize("exception_type,error_message,expected_status", [
    (OSError, "Device not ready", 400),
    (IOError, "File handle closed", 400),
    (AttributeError, "No seek method", 400),
])
@pytest.mark.asyncio
async def test_file_seek_errors(storage_service, exception_type, error_message, expected_status):
    """Test handling of various file seek operation errors."""
    upload_file = create_mock_upload_file(seek_error=exception_type(error_message))

    with pytest.raises(HTTPException) as exc_info:
        await storage_service.store_document(upload_file)

    assert exc_info.value.status_code == expected_status
    assert "Unable to read file" in exc_info.value.detail
```

**Impact**:
- 5 file operation error tests consolidated
- 70 lines → 25 lines (45 line reduction)
- Easier to add new error scenarios (1 line vs 20 lines)

#### 3. PDF Validation Cases (test_invalid_pdfs.py)

**Current State (Lines 298-320):**
```python
@pytest.mark.asyncio
async def test_multiple_pdf_versions(self, storage_service, mocker):
    """Test handling of different PDF versions."""
    pdf_versions = [
        b"%PDF-1.0\n" + b"%Test line\n" * 10 + b"%%EOF",  # Very old
        b"%PDF-1.4\n" + b"%Test line\n" * 10 + b"%%EOF",  # Common
        b"%PDF-1.7\n" + b"%Test line\n" * 10 + b"%%EOF",  # Modern
        b"%PDF-2.0\n" + b"%Test line\n" * 10 + b"%%EOF",  # Latest
    ]

    mock_s3_client = mocker.MagicMock()
    mock_s3_client.upload_fileobj.return_value = None
    storage_service.s3_client = mock_s3_client

    for idx, pdf_content in enumerate(pdf_versions):
        file = BytesIO(pdf_content)
        upload_file = mocker.Mock(spec=UploadFile)
        upload_file.filename = f"version_{idx}.pdf"
        upload_file.file = file
        upload_file.content_type = "application/pdf"

        # All versions should be accepted
        job_id, s3_key = await storage_service.store_document(upload_file)
        assert job_id is not None
```

**After Parameterization:**
```python
@pytest.mark.parametrize("pdf_version,description", [
    ("1.0", "Very old"),
    ("1.4", "Common"),
    ("1.7", "Modern"),
    ("2.0", "Latest ISO 32000-2"),
])
@pytest.mark.asyncio
async def test_pdf_version_support(storage_service, pdf_version, description):
    """Test handling of different PDF versions."""
    upload_file = create_pdf_upload(version=pdf_version)

    job_id, s3_key = await storage_service.store_document(upload_file)

    assert job_id is not None
    assert s3_key.endswith(".pdf")
```

**Impact**:
- 13 PDF validation scenarios can be parameterized
- 320 lines → 180 lines (140 line reduction)
- Clear test naming in pytest output

#### 4. Rate Limit Scenarios

**Current Pattern (test_rate_limit_service.py):**
```python
@pytest.mark.asyncio
async def test_check_submit_rate_limit_allowed(rate_limiter, mock_redis):
    """Test submission rate limit when under limit."""
    # 15 lines of setup...

@pytest.mark.asyncio
async def test_check_submit_rate_limit_exceeded(rate_limiter, mock_redis):
    """Test submission rate limit when limit exceeded."""
    # 15 lines of similar setup...
```

**After Parameterization:**
```python
@pytest.mark.parametrize("current_count,limit,expected_allowed", [
    (5, 10, True),   # Under limit
    (10, 10, False), # At limit
    (15, 10, False), # Over limit
    (0, 10, True),   # Empty
])
@pytest.mark.asyncio
async def test_rate_limit_thresholds(rate_limiter, mock_redis, current_count, limit, expected_allowed):
    """Test rate limiting at various thresholds."""
    configure_rate_limit_mock(mock_redis, current_count)

    allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

    assert allowed == expected_allowed
```

---

### Fixture Consolidation Strategy

#### Current Fragmentation Problem

**15+ files define identical mock_redis_client:**
```python
# In test_job_service.py
@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    return client

# In test_queue_service.py
@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    return client

# In test_storage_service.py
@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    client = mocker.MagicMock()
    return client

# ... duplicated 15+ more times
```

#### Proposed Directory Structure

```
tests/
├── conftest.py                      # Existing: FastAPI test client
├── conftest_fixtures/               # NEW: Shared fixture library
│   ├── __init__.py
│   ├── clients.py                   # Redis, S3, AI service mocks
│   ├── services.py                  # Service layer fixtures
│   ├── data_factories.py            # Test data generators
│   └── helpers.py                   # Assertion and setup helpers
├── integration/
│   └── conftest.py                  # Integration-specific fixtures
├── services/
│   ├── test_job_service.py
│   └── test_storage_service.py
└── edge_cases/
    └── test_pii_accuracy.py
```

#### Implementation Plan

**1. tests/conftest_fixtures/clients.py**
```python
"""Shared client mock fixtures.

Provides canonical mock implementations for:
- Redis client (AsyncMock with common operations)
- S3 client (MagicMock with boto3 operations)
- AI service clients (for PII, Docling, PydanticAI)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_redis_client():
    """Create mock Redis client with standard async operations.

    Pre-configured with:
    - All async methods return AsyncMock
    - Common operations: hset, hgetall, lpush, brpop, zadd, etc.
    - Default return values for common queries

    Usage:
        def test_something(mock_redis_client):
            mock_redis_client.hset.return_value = 3
            # Use in service...
    """
    client = AsyncMock()

    # Common Redis operations
    client.hset = AsyncMock(return_value=1)
    client.hgetall = AsyncMock(return_value={})
    client.lpush = AsyncMock(return_value=1)
    client.brpop = AsyncMock(return_value=None)
    client.llen = AsyncMock(return_value=0)
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.setex = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.expire = AsyncMock(return_value=1)
    client.exists = AsyncMock(return_value=0)
    client.zadd = AsyncMock(return_value=1)
    client.zrem = AsyncMock(return_value=1)
    client.zrangebyscore = AsyncMock(return_value=[])
    client.zremrangebyscore = AsyncMock(return_value=0)
    client.zcard = AsyncMock(return_value=0)
    client.zrange = AsyncMock(return_value=[])
    client.ping = AsyncMock(return_value=True)
    client.scan = AsyncMock(return_value=(0, []))
    client.keys = AsyncMock(return_value=[])

    # Pipeline support
    mock_pipeline = AsyncMock()
    mock_pipeline.execute = AsyncMock(return_value=[])
    mock_pipeline.hset = MagicMock()
    mock_pipeline.zadd = MagicMock()
    mock_pipeline.zremrangebyscore = MagicMock()
    mock_pipeline.zcard = MagicMock()
    client.pipeline = MagicMock(return_value=mock_pipeline)

    return client


@pytest.fixture
def mock_s3_client():
    """Create mock S3 client with standard boto3 operations.

    Pre-configured with:
    - All boto3 S3 operations (put_object, get_object, etc.)
    - Standard exception classes
    - Default successful responses

    Usage:
        def test_upload(mock_s3_client, storage_service):
            mock_s3_client.put_object.return_value = {'ETag': '...'}
            # Use storage_service...
    """
    from botocore.exceptions import ClientError

    client = MagicMock()

    # Add S3 exception classes
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = type('NoSuchKey', (ClientError,), {})
    client.exceptions.NoSuchBucket = type('NoSuchBucket', (ClientError,), {})

    # Common S3 operations
    client.put_object = MagicMock(return_value={'ETag': 'test-etag'})
    client.get_object = MagicMock()
    client.delete_object = MagicMock(return_value={})
    client.head_object = MagicMock(return_value={'ContentLength': 1024})
    client.head_bucket = MagicMock(return_value={'ResponseMetadata': {'HTTPStatusCode': 200}})
    client.list_objects_v2 = MagicMock(return_value={'Contents': []})
    client.upload_fileobj = MagicMock(return_value=None)
    client.generate_presigned_url = MagicMock(return_value="https://s3.example.com/signed-url")

    # Paginator support
    mock_paginator = MagicMock()
    mock_paginator.paginate = MagicMock(return_value=[])
    client.get_paginator = MagicMock(return_value=mock_paginator)

    return client


@pytest.fixture
def mock_pii_analyzer():
    """Create mock PII analyzer with Presidio interface.

    Default behavior: No PII detected

    Usage:
        def test_pii_detection(mock_pii_analyzer):
            from unittest.mock import Mock
            finding = Mock(entity_type="EMAIL", score=0.95)
            mock_pii_analyzer.analyze_text.return_value = [finding]
    """
    from unittest.mock import Mock

    analyzer = Mock()
    analyzer.analyze_text = Mock(return_value=[])
    return analyzer
```

**2. tests/conftest_fixtures/services.py**
```python
"""Shared service layer fixtures.

Provides pre-configured service instances with mocked dependencies.
"""

import pytest
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.services.rate_limit_service import RateLimitService
from src.config import settings


@pytest.fixture
def storage_service(mock_s3_client):
    """Create StorageService with mocked S3 client."""
    return StorageService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest.fixture
def queue_service(mock_redis_client):
    """Create QueueService with mocked Redis client."""
    return QueueService(redis_client=mock_redis_client)


@pytest.fixture
def job_service(mock_redis_client):
    """Create JobService with mocked Redis client."""
    return JobService(redis_client=mock_redis_client)


@pytest.fixture
def rate_limit_service(mock_redis_client):
    """Create RateLimitService with mocked Redis client."""
    return RateLimitService(redis=mock_redis_client)
```

**3. tests/conftest_fixtures/data_factories.py**
```python
"""Test data factory functions.

Replaces hardcoded UUIDs and test data with factory functions.
"""

import uuid
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import Mock
from fastapi import UploadFile


def generate_job_id() -> str:
    """Generate a valid UUID job ID.

    Returns:
        UUID v4 string

    Usage:
        job_id = generate_job_id()
        # Returns: "550e8400-e29b-41d4-a716-446655440000"
    """
    return str(uuid.uuid4())


def generate_s3_key(job_id: str = None, prefix: str = "temp") -> str:
    """Generate S3 key for test file.

    Args:
        job_id: Optional job ID (generates new UUID if None)
        prefix: S3 prefix (default: "temp")

    Returns:
        S3 key path

    Usage:
        s3_key = generate_s3_key()
        # Returns: "temp/550e8400-e29b-41d4-a716-446655440000/test.pdf"
    """
    if job_id is None:
        job_id = generate_job_id()
    return f"{prefix}/{job_id}/test.pdf"


def create_pdf_content(version: str = "1.4", pages: int = 1) -> bytes:
    """Generate minimal valid PDF binary content.

    Args:
        version: PDF version (1.0, 1.4, 1.7, 2.0)
        pages: Number of pages (default: 1)

    Returns:
        PDF binary content

    Usage:
        pdf = create_pdf_content(version="1.7", pages=3)
    """
    header = f"%PDF-{version}\n".encode()
    content = b"%Test content line\n" * (pages * 10)
    footer = b"%%EOF"
    return header + content + footer


def create_mock_upload_file(
    filename: str = "test.pdf",
    content: bytes = None,
    content_type: str = "application/pdf",
    seek_error: Exception = None
) -> Mock:
    """Create mock UploadFile for testing.

    Args:
        filename: File name
        content: File content (generates valid PDF if None)
        content_type: MIME type
        seek_error: Optional exception to raise on seek()

    Returns:
        Mock UploadFile instance

    Usage:
        # Valid upload
        upload = create_mock_upload_file()

        # Upload that fails seek
        upload = create_mock_upload_file(seek_error=OSError("Device not ready"))
    """
    if content is None:
        content = create_pdf_content()

    file = BytesIO(content)

    upload_file = Mock(spec=UploadFile)
    upload_file.filename = filename
    upload_file.file = file
    upload_file.content_type = content_type

    if seek_error:
        upload_file.file.seek = Mock(side_effect=seek_error)

    return upload_file


def create_pii_finding(
    entity_type: str = "EMAIL_ADDRESS",
    text: str = "test@example.com",
    score: float = 0.95,
    start: int = 0,
    end: int = 16
):
    """Create mock PII finding.

    Args:
        entity_type: Type of PII (EMAIL_ADDRESS, US_SSN, etc.)
        text: Detected text
        score: Confidence score (0.0-1.0)
        start: Start position in text
        end: End position in text

    Returns:
        Mock PII finding object

    Usage:
        finding = create_pii_finding(entity_type="US_SSN", score=0.99)
    """
    from src.shared.models.pii import PIIFinding

    return PIIFinding(
        entity_type=entity_type,
        text=text,
        score=score,
        start=start,
        end=end
    )
```

**4. tests/conftest_fixtures/helpers.py**
```python
"""Test helper functions for common assertions and mock setup."""

from unittest.mock import Mock


def configure_pii_mock(
    pii_analyzer,
    entity_type: str,
    text: str,
    should_detect: bool,
    score: float = 0.95
):
    """Configure PII analyzer mock for test case.

    Args:
        pii_analyzer: Mock PII analyzer fixture
        entity_type: PII entity type to detect
        text: Text to analyze
        should_detect: Whether detection should occur
        score: Confidence score for detection

    Usage:
        configure_pii_mock(pii_analyzer, "US_SSN", "SSN: 123-45-6789", True)
    """
    if should_detect:
        mock_result = Mock()
        mock_result.entity_type = entity_type
        mock_result.start = 0
        mock_result.end = len(text)
        mock_result.score = score
        pii_analyzer.analyzer.analyze.return_value = [mock_result]
    else:
        pii_analyzer.analyzer.analyze.return_value = []


def assert_pii_detection(findings, entity_type: str, should_detect: bool, description: str):
    """Assert PII detection results match expectations.

    Args:
        findings: List of PII findings from analyzer
        entity_type: Expected entity type
        should_detect: Whether detection was expected
        description: Test case description for error messages

    Usage:
        assert_pii_detection(findings, "US_SSN", True, "Standard SSN format")
    """
    if should_detect:
        assert len(findings) > 0, f"Failed to detect {entity_type}: {description}"
        assert findings[0].entity_type == entity_type
        assert findings[0].score >= 0.85
    else:
        entity_findings = [f for f in findings if f.entity_type == entity_type]
        assert len(entity_findings) == 0, f"False positive {entity_type}: {description}"


def configure_rate_limit_mock(mock_redis, current_count: int):
    """Configure Redis mock for rate limit testing.

    Args:
        mock_redis: Mock Redis client
        current_count: Current request count to simulate

    Usage:
        configure_rate_limit_mock(mock_redis, current_count=5)
    """
    from unittest.mock import MagicMock, AsyncMock

    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock(return_value=[None, current_count])
    mock_redis.pipeline.return_value = mock_pipeline
```

**5. Update tests/conftest.py to import shared fixtures:**
```python
"""Test configuration and fixtures."""

import pytest
from fastapi.testclient import TestClient

from src.main import app

# Import all shared fixtures (makes them available to all tests)
pytest_plugins = [
    "tests.conftest_fixtures.clients",
    "tests.conftest_fixtures.services",
    "tests.conftest_fixtures.data_factories",
    "tests.conftest_fixtures.helpers",
]


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_pdf():
    """Create sample PDF file for testing."""
    # Import from data_factories instead of duplicating
    from tests.conftest_fixtures.data_factories import create_pdf_content
    return create_pdf_content()
```

---

## Mock Standardization

### Current Problem: AsyncMock vs MagicMock Inconsistency

**Analysis of 32 files using mocks:**
- **AsyncMock**: 18 files (for Redis async operations)
- **MagicMock**: 14 files (for S3 sync operations)
- **Mixed usage**: 8 files use both without clear pattern
- **Pytest-mock mocker**: 94 uses across files (duplicated fixture injection)

### Standardization Rules

**Rule 1: Use AsyncMock for async service methods**
```python
# ✅ CORRECT: Redis operations are async
@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    client.hset = AsyncMock(return_value=1)
    return client

# ❌ WRONG: Using MagicMock for async operations
@pytest.fixture
def mock_redis_client():
    client = MagicMock()  # Will fail with "object is not callable"
    return client
```

**Rule 2: Use MagicMock for sync service methods**
```python
# ✅ CORRECT: boto3 S3 operations are synchronous
@pytest.fixture
def mock_s3_client():
    client = MagicMock()
    client.put_object = MagicMock(return_value={'ETag': 'abc'})
    return client

# ❌ WRONG: Using AsyncMock for sync operations
@pytest.fixture
def mock_s3_client():
    client = AsyncMock()  # Adds unnecessary async overhead
    return client
```

**Rule 3: Eliminate pytest-mock mocker fixture dependency**
```python
# ❌ BEFORE: Requires mocker fixture
@pytest.fixture
def mock_redis_client(mocker):
    return mocker.AsyncMock()

# ✅ AFTER: Direct import, no mocker needed
from unittest.mock import AsyncMock

@pytest.fixture
def mock_redis_client():
    return AsyncMock()
```

**Impact**:
- Consistent mock types across all 32 files
- Remove 94 instances of `mocker.` calls
- Clearer test code (mock type indicates sync/async)

---

## Migration Plan

### Phase 1: Foundation (Week 1)

**Day 1: Create Fixture Library**
- Create `tests/conftest_fixtures/` directory structure
- Implement `clients.py` with canonical mock fixtures
- Implement `data_factories.py` with UUID/data generators
- Update root `conftest.py` to import shared fixtures

**Day 2-3: Migrate High-Impact Files**
- Remove local fixture definitions from 15 files
- Update imports to use shared fixtures
- Run full test suite, verify zero failures
- Commit: "refactor: consolidate Redis/S3 mock fixtures"

**Files to migrate:**
1. tests/services/test_job_service.py
2. tests/services/test_storage_service.py
3. tests/services/test_queue_service.py
4. tests/services/test_rate_limit_service.py
5. tests/services/test_redis_failures.py
6. tests/services/test_s3_failures.py
7. tests/services/test_error_handling.py
8. tests/edge_cases/test_invalid_pdfs.py
9. tests/edge_cases/test_large_files.py
10. tests/integration/conftest.py (keep integration-specific fixtures)

### Phase 2: Parameterization (Week 2)

**Day 1: Parameterize PII Tests**
- Refactor `tests/edge_cases/test_pii_accuracy.py`
- Implement helper functions in `conftest_fixtures/helpers.py`
- Target: 17 test cases → 5 parameterized tests
- Estimated reduction: 138 lines

**Day 2: Parameterize Error Handling**
- Refactor `tests/services/test_error_handling.py`
- Consolidate file operation error tests
- Consolidate cleanup error tests
- Consolidate rate limit error tests
- Target: 373 lines → 250 lines (123 line reduction)

**Day 3: Parameterize PDF Validation**
- Refactor `tests/edge_cases/test_invalid_pdfs.py`
- Consolidate PDF version tests
- Consolidate filename validation tests
- Consolidate content validation tests
- Target: 320 lines → 180 lines (140 line reduction)

**Day 4: Parameterize Rate Limit Tests**
- Refactor `tests/services/test_rate_limit_service.py`
- Consolidate threshold tests
- Consolidate quota tests
- Target: 15-20% line reduction

### Phase 3: Test Data Factories (Week 2)

**Day 5: Eliminate Hardcoded UUIDs**
- Replace all instances of "test-job-123" pattern (11 files)
- Replace all instances of hardcoded UUIDs (10+ files)
- Use `generate_job_id()` factory function
- Run full test suite, verify deterministic behavior

**Files with hardcoded test data:**
1. tests/api/test_approval_flow.py
2. tests/workers/test_graceful_shutdown.py
3. tests/services/test_timeout_monitoring.py
4. tests/services/test_orphan_detection.py
5. tests/integration/test_malformed_payloads.py
6. tests/models/test_job_models.py
7. tests/models/test_queue_models.py

### Phase 4: Verification (Week 3)

**Day 1: Test Coverage Analysis**
```bash
pytest --cov=src --cov-report=term-missing
```
- Verify coverage remains ≥90%
- Identify any coverage regressions
- Add missing tests if needed

**Day 2: Performance Testing**
```bash
pytest --durations=10
pytest --benchmark-only  # If benchmarks exist
```
- Verify test suite runtime unchanged or improved
- Identify slow parameterized tests
- Optimize if needed

**Day 3: Documentation**
- Update CONTRIBUTING.md with fixture usage guide
- Document parameterization patterns
- Create examples for common test scenarios
- Add docstrings to all shared fixtures

---

## Before/After Examples

### Example 1: Multiple Error Scenarios

**BEFORE (test_error_handling.py, Lines 150-204):**
```python
@pytest.mark.asyncio
async def test_delete_client_error_returns_false(self, storage_service):
    """Test that ClientError returns False instead of raising."""
    from botocore.exceptions import ClientError

    error_response = {'Error': {'Code': 'AccessDenied', 'Message': 'Access denied'}}
    storage_service.s3_client.delete_object = Mock(
        side_effect=ClientError(error_response, 'DeleteObject')
    )

    # Should not raise exception
    result = await storage_service.delete_temp_file("temp/test.pdf")

    assert result is False  # Returns False, doesn't raise

@pytest.mark.asyncio
async def test_delete_generic_error_returns_false(self, storage_service):
    """Test that generic exceptions return False instead of raising."""
    storage_service.s3_client.delete_object = Mock(
        side_effect=Exception("Unexpected S3 error")
    )

    # Should not raise exception
    result = await storage_service.delete_temp_file("temp/test.pdf")

    assert result is False

@pytest.mark.asyncio
async def test_delete_network_error_returns_false(self, storage_service):
    """Test that network errors return False instead of raising."""
    storage_service.s3_client.delete_object = Mock(
        side_effect=ConnectionError("Network unreachable")
    )

    # Should not raise exception
    result = await storage_service.delete_temp_file("temp/test.pdf")

    assert result is False
```

**AFTER (55 lines → 18 lines):**
```python
@pytest.mark.parametrize("error_type,error_args,description", [
    (ClientError, ({'Error': {'Code': 'AccessDenied'}}, 'DeleteObject'), "Client error"),
    (Exception, ("Unexpected S3 error",), "Generic exception"),
    (ConnectionError, ("Network unreachable",), "Network error"),
])
@pytest.mark.asyncio
async def test_delete_errors_return_false(
    storage_service, mock_s3_client, error_type, error_args, description
):
    """Test that deletion errors return False instead of raising (best-effort cleanup)."""
    mock_s3_client.delete_object.side_effect = error_type(*error_args)

    result = await storage_service.delete_temp_file("temp/test.pdf")

    assert result is False, f"Should return False on {description}"
```

### Example 2: PII Detection Patterns

**BEFORE (test_pii_accuracy.py, Lines 84-120):**
```python
@pytest.mark.asyncio
async def test_email_detection_accuracy(self, pii_analyzer):
    """Test email detection with various formats."""
    test_cases = [
        ("Contact: user@example.com", True, "Simple email"),
        ("Email: first.last@company.co.uk", True, "Subdomain and TLD"),
        ("user+tag@example.com", True, "Plus addressing"),
        ("user_name@example.com", True, "Underscore"),
        ("user@", False, "Incomplete email"),
        ("@example.com", False, "Missing user"),
        ("not-an-email", False, "No @ symbol"),
    ]

    for text, should_detect, description in test_cases:
        if should_detect:
            # Find email in text
            at_pos = text.find("@")
            start = max(0, text[:at_pos].rfind(" ") + 1)
            end = text.find(" ", at_pos) if " " in text[at_pos:] else len(text)

            mock_result = Mock()
            mock_result.entity_type = "EMAIL_ADDRESS"
            mock_result.start = start
            mock_result.end = end
            mock_result.score = 0.90
            pii_analyzer.analyzer.analyze.return_value = [mock_result]
        else:
            pii_analyzer.analyzer.analyze.return_value = []

        findings = pii_analyzer.analyze_text(text)

        if should_detect:
            assert len(findings) > 0, f"Failed to detect: {description}"
            assert any(f.entity_type == "EMAIL_ADDRESS" for f in findings)
        else:
            email_findings = [f for f in findings if f.entity_type == "EMAIL_ADDRESS"]
            assert len(email_findings) == 0, f"False positive: {description}"
```

**AFTER (37 lines → 15 lines):**
```python
@pytest.mark.parametrize("text,should_detect,description", [
    ("Contact: user@example.com", True, "Simple email"),
    ("Email: first.last@company.co.uk", True, "Subdomain and TLD"),
    ("user+tag@example.com", True, "Plus addressing"),
    ("user_name@example.com", True, "Underscore"),
    ("user@", False, "Incomplete email"),
    ("@example.com", False, "Missing user"),
    ("not-an-email", False, "No @ symbol"),
])
@pytest.mark.asyncio
async def test_email_detection_accuracy(pii_analyzer, text, should_detect, description):
    """Test email detection with various formats."""
    configure_pii_mock(pii_analyzer, "EMAIL_ADDRESS", text, should_detect, score=0.90)

    findings = pii_analyzer.analyze_text(text)

    assert_pii_detection(findings, "EMAIL_ADDRESS", should_detect, description)
```

### Example 3: Hardcoded UUID Elimination

**BEFORE (test_approval_flow.py):**
```python
async def test_pending_approvals_list(client, mock_redis):
    """Test listing pending approvals."""
    mock_redis.keys.return_value = [b"eq-pdf:job:test-job-123"]
    mock_redis.hgetall.return_value = {
        "job_id": "test-job-123",
        "status": "awaiting_approval",
        "created_at": "2024-01-01T00:00:00Z"
    }

    response = client.get("/api/v1/admin/approvals/pending")

    assert response.status_code == 200
```

**AFTER:**
```python
async def test_pending_approvals_list(client, mock_redis):
    """Test listing pending approvals."""
    job_id = generate_job_id()

    mock_redis.keys.return_value = [f"eq-pdf:job:{job_id}".encode()]
    mock_redis.hgetall.return_value = {
        "job_id": job_id,
        "status": "awaiting_approval",
        "created_at": "2024-01-01T00:00:00Z"
    }

    response = client.get("/api/v1/admin/approvals/pending")

    assert response.status_code == 200
```

---

## Expected Outcomes

### Quantitative Metrics

**Code Reduction:**
- test_pii_accuracy.py: 388 → 250 lines (-138, -35.6%)
- test_error_handling.py: 373 → 250 lines (-123, -33.0%)
- test_invalid_pdfs.py: 320 → 180 lines (-140, -43.8%)
- Fixture definitions: -200 lines (consolidation)
- **Total estimated reduction: ~600 lines (4.2% of test suite)**

**Fixture Consolidation:**
- Before: 87 fixtures in test files
- After: ~20 fixtures in test files (integration-specific only)
- Shared: 15-20 fixtures in conftest_fixtures/
- **Reduction: 67 duplicate fixtures eliminated (77%)**

**Test Maintainability:**
- Adding new PII test case: 30 lines → 1 line (97% reduction)
- Adding new error scenario: 20 lines → 1 line (95% reduction)
- Updating mock configuration: 15 files → 1 file (93% reduction)

### Qualitative Improvements

**Developer Experience:**
- New developers find canonical fixtures in one location
- Parameterized tests are self-documenting (clear test cases in pytest output)
- Consistent mock patterns reduce cognitive load
- Test data factories eliminate UUID typos

**Test Output Clarity:**
```bash
# BEFORE
test_ssn_detection_valid_formats PASSED
test_ssn_detection_invalid_formats PASSED

# AFTER (pytest shows each parameter)
test_ssn_detection[My SSN is 123-45-6789-True-Standard format] PASSED
test_ssn_detection[SSN: 123456789-True-No dashes] PASSED
test_ssn_detection[111-11-1111-False-Invalid sequence] PASSED
```

**Maintenance:**
- Changing Redis mock structure: Update 1 file instead of 15
- Adding new S3 operation: Update clients.py, available everywhere
- Standardizing error assertions: Create helper function once

---

## Testing Strategy

### Verification Steps

**Step 1: Incremental Migration**
```bash
# After each file migration
pytest tests/services/test_job_service.py -v
pytest tests/edge_cases/test_pii_accuracy.py -v

# Verify zero test failures
# Verify test count unchanged
```

**Step 2: Full Test Suite Validation**
```bash
# After fixture consolidation phase
pytest tests/ --tb=short

# Expected: All tests pass
# Expected: Test count unchanged (572 tests)
```

**Step 3: Coverage Analysis**
```bash
# Before refactoring
pytest --cov=src --cov-report=term > coverage_before.txt

# After refactoring
pytest --cov=src --cov-report=term > coverage_after.txt

# Compare: Coverage should remain ≥90%
diff coverage_before.txt coverage_after.txt
```

**Step 4: Performance Benchmark**
```bash
# Before
pytest tests/ --durations=0 > durations_before.txt

# After
pytest tests/ --durations=0 > durations_after.txt

# Verify: No significant slowdown (parameterization may speed up fixtures)
```

### Rollback Plan

If issues are discovered during migration:

1. **Per-file rollback**: Each file migration is a separate commit
2. **Feature flag approach**: Use `pytest.mark.skip` to disable problematic parameterized tests
3. **Fixture override**: Tests can override shared fixtures with local versions if needed

```python
# Local override if needed
@pytest.fixture
def mock_redis_client():
    """Local override with special behavior."""
    from tests.conftest_fixtures.clients import mock_redis_client as base_fixture
    client = base_fixture()
    # Add special configuration
    client.special_operation = AsyncMock()
    return client
```

---

## Definition of Done

### Code Quality Checklist

- [ ] **Fixture Consolidation**: ≥80% of fixtures moved to conftest_fixtures/
- [ ] **Parameterization**: ≥50% of repetitive tests use @pytest.mark.parametrize
- [ ] **LOC Reduction**: Test suite reduced by 250-350 lines
- [ ] **Mock Standardization**: All Redis mocks use AsyncMock, all S3 mocks use MagicMock
- [ ] **UUID Elimination**: Zero hardcoded UUIDs remain (all use factories)

### Test Quality Checklist

- [ ] **Zero Failures**: All 572 tests pass
- [ ] **Coverage Maintained**: Test coverage ≥90% (same as before)
- [ ] **Test Count**: Test count unchanged (parameterization may increase count slightly)
- [ ] **No Flaky Tests**: 3 consecutive full test runs pass

### Documentation Checklist

- [ ] **Fixture Documentation**: All shared fixtures have docstrings with usage examples
- [ ] **Helper Documentation**: All helper functions documented
- [ ] **Migration Guide**: CONTRIBUTING.md updated with fixture usage patterns
- [ ] **Parameterization Guide**: Examples of when/how to use parametrize

### Performance Checklist

- [ ] **Runtime**: Test suite runtime ≤105% of baseline (max 5% slowdown)
- [ ] **Memory**: No memory leaks in parameterized tests
- [ ] **CI/CD**: All GitHub Actions workflows pass

---

## Success Metrics (Post-Implementation)

**Measure after 2 weeks:**

1. **Developer Velocity**
   - Track: Time to add new test case (before: ~15 min, target: ~5 min)
   - Track: Test failures per PR (target: 20% reduction)

2. **Code Maintainability**
   - Count: PRs that touch >5 test files (target: 50% reduction)
   - Count: Test-related bug fixes (target: 30% reduction)

3. **Test Suite Health**
   - Coverage: Maintain ≥90%
   - Flakiness: <1% flaky test rate
   - Runtime: ≤2 minutes for full suite

---

## Related Work

**Blocked By:**
- None (can start immediately)

**Blocks:**
- Future test additions (will use new patterns)
- Test coverage improvements (easier to add tests with factories)

**Related PRDs:**
- PRD-009: Cleanup Service (will benefit from shared fixtures)
- PRD-010: Timeout Worker (will benefit from test data factories)
- BUG-006: Test Suite Failures (partially addresses root causes)

---

## Appendix A: Fixture Inventory

### Current Fixture Distribution (107 total)

**Root conftest.py (2 fixtures):**
- `client` - FastAPI TestClient
- `sample_pdf` - Basic PDF content

**Integration conftest.py (18 fixtures):**
- `mock_redis_client`, `mock_s3_client` (will move to shared)
- `storage_service`, `queue_service`, `job_service` (will move to shared)
- `pii_worker`, `processing_worker` (integration-specific, keep)
- `sample_job_id`, `sample_s3_key`, `sample_pii_findings` (will move to factories)
- `cleanup_redis`, `cleanup_s3` (integration-specific, keep)

**Service test files (87 fixtures across 26 files):**

Most common duplicates:
- `mock_redis_client`: 15 files
- `mock_s3_client`: 15 files
- `storage_service`: 10 files
- `job_service`: 8 files
- `queue_service`: 7 files

**Estimated consolidation:**
- 67 fixtures can be moved to shared conftest_fixtures/
- 20 fixtures remain (integration-specific or test-specific)
- 20 new factory functions replace hardcoded data

---

## Appendix B: Parameterization Opportunities Inventory

### High-Value Targets (17+ cases each)

**test_pii_accuracy.py (17 test methods, many can be parameterized):**
- `test_ssn_detection_valid_formats` - 3 cases
- `test_ssn_detection_invalid_formats` - 5 cases
- `test_email_detection_accuracy` - 7 cases
- `test_phone_number_detection` - 6 cases
- `test_person_name_not_detected` - 4 cases
- `test_location_not_detected` - 4 cases
- `test_date_time_not_detected` - 5 cases
- `test_credit_card_detection` - 3 cases
- `test_international_phone_formats` - 4 cases

**test_error_handling.py (3 test classes):**
- `TestFileSeekErrorHandling` - 5 error types
- `TestBestEffortCleanup` - 4 error scenarios
- `TestRateLimitKeyCollision` - 3 collision scenarios

**test_invalid_pdfs.py (13 test methods):**
- `test_multiple_pdf_versions` - 4 versions
- `test_special_characters_in_filename` - Can be parameterized with character sets
- Various validation scenarios - Can consolidate into parameterized tests

### Medium-Value Targets (5-10 cases each)

**test_rate_limit_service.py:**
- Threshold tests - 3 scenarios
- Quota tests - 3 scenarios
- Different IPs - Can be parameterized

**test_storage_service.py:**
- Error handling - Multiple error types
- File existence checks - Multiple scenarios

---

## Appendix C: Mock Type Standards Reference

```python
# ASYNC SERVICES (use AsyncMock)
# - Redis operations
# - Queue operations
# - Job service methods
# - Rate limit service methods

from unittest.mock import AsyncMock

mock_redis = AsyncMock()
mock_redis.hset = AsyncMock(return_value=1)

# SYNC SERVICES (use MagicMock)
# - S3/boto3 operations
# - File I/O
# - HTTP clients (httpx, requests)

from unittest.mock import MagicMock

mock_s3 = MagicMock()
mock_s3.put_object = MagicMock(return_value={'ETag': 'abc'})

# HYBRID (use both)
# - Pipeline operations (sync construction, async execution)

mock_pipeline = MagicMock()
mock_pipeline.execute = AsyncMock(return_value=[])
mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
```

---

**End of PRD**
