"""
Shared test fixtures for Equalify PDF Converter test suite.

This package provides canonical fixtures to eliminate duplication across
the test suite. All fixtures use function scope by default unless specified.

Modules:
    clients: Mock clients (Redis, S3, AI services)
    data_factories: Test data generators (UUIDs, jobs, documents)
    helpers: Assertion and setup helpers
    services: Pre-configured service instances with mocked dependencies
"""

# Export all fixtures for pytest discovery
from tests.conftest_fixtures.clients import (
    mock_redis_client,
    mock_s3_client,
    mock_ai_service,
)
from tests.conftest_fixtures.data_factories import (
    generate_job_id,
    generate_document_id,
    create_test_job_dict,
    create_pii_queue_payload,
    create_processing_queue_payload,
    create_s3_key,
    create_test_pdf_content,
    create_test_upload_file,
)
from tests.conftest_fixtures.helpers import (
    assert_job_state,
    assert_s3_upload,
    assert_redis_set,
)

__all__ = [
    # Clients
    "mock_redis_client",
    "mock_s3_client",
    "mock_ai_service",
    # Data factories
    "generate_job_id",
    "generate_document_id",
    "create_test_job_dict",
    "create_pii_queue_payload",
    "create_processing_queue_payload",
    "create_s3_key",
    "create_test_pdf_content",
    "create_test_upload_file",
    # Helpers
    "assert_job_state",
    "assert_s3_upload",
    "assert_redis_set",
]
