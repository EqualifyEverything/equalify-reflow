"""Test configuration and fixtures."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from src.main import app
from src.shared.models.queue import ProcessingQueuePayload
from src.services.pdf_converter import PageData, PDFConversionResult
from src.agents.accessibility_agent import PageImprovementResult


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_pdf():
    """Create sample PDF file for testing."""
    # Simple PDF content
    pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length 44 >>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n(Test PDF) Tj\nET\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n0000000317 00000 n\ntrailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n410\n%%EOF"
    return pdf_content


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
        markdown="# Test Document\n\nSample content",
        image_base64="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    )


@pytest.fixture
def sample_pdf_conversion_result(sample_page_data):
    """Sample PDFConversionResult for testing."""
    return PDFConversionResult(
        pages=[sample_page_data],
        total_pages=1,
        full_markdown="# Test Document\n\nSample content",
        has_page_images=True,
    )


@pytest.fixture
def sample_page_improvement_result():
    """Sample PageImprovementResult for testing."""
    return PageImprovementResult(
        improved_markdown="# Test Document\n\n![Description](image.png)\n\nSample content",
        confidence_score=0.92,
        processing_notes="Added alt text to image",
    )


@pytest.fixture
def mock_storage_service():
    """Mock StorageService for unit tests."""
    mock = AsyncMock()
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


@pytest.fixture
def mock_ai_enhancement_service(sample_page_improvement_result):
    """Mock AIEnhancementService for unit tests."""
    mock = AsyncMock()
    mock.process_pages_concurrently = AsyncMock(
        return_value=[sample_page_improvement_result]
    )
    mock.combine_page_markdown = MagicMock(
        return_value="# Test Document\n\n![Description](image.png)\n\nSample content"
    )
    return mock


@pytest.fixture
def mock_accessibility_agent(sample_page_improvement_result):
    """Mock AccessibilityAgent for unit tests."""
    mock = AsyncMock()
    mock.process_page = AsyncMock(return_value=sample_page_improvement_result)
    return mock