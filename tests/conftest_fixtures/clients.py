"""
Canonical mock clients for test suite.

Provides standardized mocks for:
- Redis (AsyncMock for async operations)
- S3/boto3 (MagicMock for sync operations)
- AI services (AsyncMock for async operations)

All fixtures use function scope (default) unless specified.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_redis_client():
    """Create mock Redis client with common async operations.

    Pre-configured with:
    - lpush, rpush, lrange, llen (queue operations)
    - set, get, delete, exists (key-value operations)
    - flushall, aclose (cleanup operations)

    Returns:
        AsyncMock: Mock Redis client for unit tests
    """
    client = AsyncMock()

    # Configure common return values
    client.lpush.return_value = 1
    client.rpush.return_value = 1
    client.llen.return_value = 0
    client.lrange.return_value = []
    client.get.return_value = None
    client.exists.return_value = 0
    client.flushall.return_value = None
    client.aclose.return_value = None

    return client


@pytest.fixture
def mock_s3_client():
    """Create mock S3 client with common sync operations.

    Pre-configured with:
    - upload_fileobj (document uploads)
    - put_object (content uploads)
    - get_object (downloads)
    - delete_object (cleanup)
    - list_objects_v2 (listing)

    Returns:
        MagicMock: Mock S3 client for unit tests
    """
    client = MagicMock()

    # Configure common return values
    client.upload_fileobj.return_value = None
    client.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=b"test content")),
        "ContentLength": 12,
    }
    client.delete_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 204}}
    client.list_objects_v2.return_value = {"Contents": []}

    return client


@pytest.fixture
def mock_ai_service():
    """Create mock AI service with common async operations.

    Pre-configured with:
    - enhance_accessibility (returns enhanced markdown)
    - generate_alt_text (returns descriptive alt text)
    - fix_heading_hierarchy (returns corrected structure)

    Returns:
        AsyncMock: Mock AI service for unit tests
    """
    service = AsyncMock()

    # Configure common return values
    service.enhance_accessibility.return_value = "# Enhanced Markdown\n\nAccessible content."
    service.generate_alt_text.return_value = "A descriptive alt text for the image"
    service.fix_heading_hierarchy.return_value = "# Corrected Heading Structure"

    return service


@pytest.fixture
def mock_presidio_analyzer():
    """Create mock Microsoft Presidio analyzer for PII detection.

    Pre-configured with:
    - analyze (returns empty list by default, no PII)

    Returns:
        MagicMock: Mock Presidio AnalyzerEngine
    """
    analyzer = MagicMock()
    analyzer.analyze.return_value = []  # No PII by default
    return analyzer


@pytest.fixture
def mock_presidio_anonymizer():
    """Create mock Microsoft Presidio anonymizer for PII redaction.

    Pre-configured with:
    - anonymize (returns anonymized text)

    Returns:
        MagicMock: Mock Presidio AnonymizerEngine
    """
    anonymizer = MagicMock()

    # Configure default return
    anonymizer.anonymize.return_value = MagicMock(
        text="[REDACTED]",
        items=[]
    )

    return anonymizer
