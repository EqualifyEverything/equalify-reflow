"""Test configuration and fixtures.

IMPORTANT: Shared fixtures are now available from tests.conftest_fixtures:
- mock_redis_client, mock_s3_client, mock_ai_service (clients.py)
- generate_job_id, create_test_job, create_pii_queue_payload, etc. (data_factories.py)
- assert_job_state, assert_s3_upload, assert_redis_set (helpers.py)

Import these instead of defining duplicates in test files.
"""

# Configure test environment before any imports
# Force disable API key auth for integration tests
import os

os.environ["ENABLE_API_KEY_AUTH"] = "false"

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.services.pdf_converter import PageData, PDFConversionResult
from src.shared.models.queue import ProcessingQueuePayload

# Import shared fixtures to make them available to all tests
# Note: Using string-based plugin registration to avoid import-time side effects
pytest_plugins = ["tests.conftest_fixtures.clients", "tests.conftest_fixtures.data_factories"]


@pytest.fixture
def client():
    """Create test client.

    Note: API key authentication is enabled by default in .env.
    Tests that need to make API requests should use the api_key_headers fixture.
    """
    return TestClient(app)


@pytest.fixture
def api_key_headers():
    """Generate API key headers for authenticated requests.

    Returns headers dict with X-API-Key for use in test requests.
    Uses the first API key from settings to match the configured environment.

    Note: The API key must match what's in the environment when the app was initialized,
    since APIKeyAuthMiddleware caches keys at startup.
    """
    import os

    # Read directly from environment to match what middleware cached at startup
    api_keys_env = os.getenv("API_KEYS", "")
    if api_keys_env:
        keys = api_keys_env.split(",")
        api_key = keys[0].strip() if keys else ""
    else:
        # Fallback for tests that don't require real auth
        api_key = "test-key-fallback"

    return {"X-API-Key": api_key}


@pytest.fixture
def sample_pdf():
    """Create sample PDF file for testing."""
    # Simple PDF content
    pdf_content = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 "
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 44 >>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n"
        b"(Test PDF) Tj\nET\nendstream\nendobj\nxref\n0 5\n"
        b"0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n"
        b"0000000115 00000 n\n0000000317 00000 n\ntrailer\n"
        b"<< /Size 5 /Root 1 0 R >>\nstartxref\n410\n%%EOF"
    )
    return pdf_content


@pytest.fixture(autouse=True)
def reset_agent_singletons():
    """Reset all agent module singletons before and after each test.

    This ensures test isolation by clearing cached agent instances.
    Each test starts with a fresh agent state, preventing test pollution.
    """
    # Import agent modules that use singleton pattern
    from src.agents import (
        extraction_agent,
        figures_agent,
        structure_agent,
        tables_agent,
        typography_agent,
    )

    # Reset before test
    for agent_module in [
        extraction_agent,
        figures_agent,
        structure_agent,
        tables_agent,
        typography_agent,
    ]:
        if hasattr(agent_module, "reset_agent"):
            agent_module.reset_agent()

    yield

    # Reset after test (cleanup)
    for agent_module in [
        extraction_agent,
        figures_agent,
        structure_agent,
        tables_agent,
        typography_agent,
    ]:
        if hasattr(agent_module, "reset_agent"):
            agent_module.reset_agent()


# ============================================================================
# Core Pipeline Test Fixtures
# ============================================================================


@pytest.fixture
def sample_job_payload():
    """Sample processing queue payload for testing."""
    return ProcessingQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
        approved_at=None,
    )


@pytest.fixture
def sample_page_data():
    """Sample PageData for testing AI processing."""
    return PageData(
        page_num=1,
        image_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    )


@pytest.fixture
def sample_pdf_conversion_result(sample_page_data):
    """Sample PDFConversionResult for testing."""
    return PDFConversionResult(
        pages=[sample_page_data],
        total_pages=1,
        has_page_images=True,
        extracted_images=[],
    )


@pytest.fixture
def mock_storage_service():
    """Mock StorageService for unit tests.

    Uses MagicMock as container with AsyncMock for async methods.
    This prevents unawaited coroutine warnings when tests override side_effect.
    """
    mock = MagicMock()
    mock.download_temp_file = AsyncMock(return_value=b"fake_pdf_content")
    mock.upload_result = AsyncMock(
        return_value="s3://equalify-results/550e8400.../v20250101_120000/output.md"
    )
    return mock


@pytest.fixture
def mock_queue_service():
    """Mock QueueService for unit tests."""
    mock = AsyncMock()
    mock.enqueue = AsyncMock()
    mock.dequeue = AsyncMock()
    return mock


@pytest.fixture
def mock_job_service():
    """Mock JobService for unit tests."""
    mock = AsyncMock()
    mock.update_job_status = AsyncMock()
    mock.get_job_status = AsyncMock(return_value="processing")
    return mock


@pytest.fixture
def mock_pdf_converter(sample_pdf_conversion_result):
    """Mock PDFConverter for unit tests."""
    mock = AsyncMock()
    mock.convert_with_page_images = AsyncMock(return_value=sample_pdf_conversion_result)
    return mock
