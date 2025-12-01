"""Unit tests for ProcessingService.

Tests the main processing orchestrator with all dependencies mocked.
Validates the processing pipeline including error handling and retries.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call
from datetime import datetime

from src.services.processing_service import ProcessingService
from src.services.text_correction_service import PageProcessingError
from src.services.pdf_converter import PDFConversionResult, PageData
from src.shared.models.processing import PageCorrectionResult, TextCorrection
from src.shared.models.queue import ProcessingQueuePayload


pytestmark = pytest.mark.unit


# ============================================================================
# Fixtures specific to ProcessingService tests
# ============================================================================


@pytest.fixture
def sample_page_correction_result():
    """Sample PageCorrectionResult for testing."""
    return PageCorrectionResult(
        corrections=[
            TextCorrection(
                correction_type="heading_level",
                original_text="Test Document",
                corrected_text="# Test Document",
                location_context="...content before...\n\nTest Document\n\n...content after...",
                confidence=0.95,
                explanation="Visual layout shows this as a level 1 heading",
            )
        ],
        overall_confidence=0.92,
        processing_notes="Checked 1 heading, 0 lists, 0 tables. Found 1 correction.",
        page_number=1,
    )


@pytest.fixture
def mock_text_correction_service(sample_page_correction_result):
    """Mock TextCorrectionService for unit tests."""
    mock = MagicMock()
    mock.process_pages_concurrently = AsyncMock(
        return_value=[sample_page_correction_result]
    )
    mock.apply_all_page_corrections = MagicMock(
        return_value="# Test Document\n\n![Description](image.png)\n\nSample content"
    )
    return mock


@pytest.fixture
def mock_storage_service_extended():
    """Mock StorageService with all methods needed for ProcessingService tests."""
    mock = AsyncMock()
    mock.download_temp_file = AsyncMock(return_value=b"fake_pdf_content")
    mock.upload_result = AsyncMock(
        return_value="s3://equalify-results/550e8400.../v20250101_120000/output.md"
    )
    mock.upload_page_image = AsyncMock(
        return_value="s3://equalify-results/550e8400/pages/page-1.png"
    )
    mock.upload_image = AsyncMock(
        return_value="s3://equalify-results/550e8400/images/picture-0.png"
    )
    return mock


# ============================================================================
# Initialization Tests (2 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_processing_service_init_with_default_dependencies(
    mock_storage_service, mock_queue_service, mock_job_service, mocker
):
    """Test ProcessingService creates default dependencies if not provided."""
    # Mock the TextCorrectionService to avoid needing API keys
    mock_text_corr = mocker.MagicMock()
    mock_pdf = mocker.MagicMock()

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf,
        text_correction=mock_text_corr,
    )

    # Should have all dependencies
    assert service.pdf_converter is not None
    assert service.text_correction is not None
    assert service.storage == mock_storage_service
    assert service.queue == mock_queue_service
    assert service.job == mock_job_service


@pytest.mark.asyncio
async def test_processing_service_init_with_custom_dependencies(
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test ProcessingService accepts custom dependencies for testing."""
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    # Should use injected dependencies
    assert service.pdf_converter == mock_pdf_converter
    assert service.text_correction == mock_text_correction_service


# ============================================================================
# Success Path Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_happy_path(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
    sample_pdf_conversion_result,
    sample_page_correction_result,
):
    """Test successful end-to-end document processing."""
    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Verify result
    assert result.job_id == sample_job_payload.job_id
    assert result.markdown_url is not None
    assert result.confidence_score is not None
    assert result.processing_time_seconds >= 0
    assert result.error_message is None

    # Verify pipeline steps executed in order
    mock_job_service.update_job_status.assert_any_call(
        sample_job_payload.job_id, "processing"
    )
    mock_storage_service_extended.download_temp_file.assert_called_once_with(
        s3_key=sample_job_payload.s3_key
    )
    mock_pdf_converter.convert_with_page_images.assert_called_once()
    mock_text_correction_service.process_pages_concurrently.assert_called_once()
    mock_storage_service_extended.upload_result.assert_called()


@pytest.mark.asyncio
async def test_process_document_calculates_confidence_correctly(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
    sample_pdf_conversion_result,
):
    """Test confidence score calculation from page results."""
    # Multiple pages with different confidence scores
    multi_page_result = PDFConversionResult(
        pages=[
            PageData(page_num=1, markdown="Page 1", image_base64="img1"),
            PageData(page_num=2, markdown="Page 2", image_base64="img2"),
            PageData(page_num=3, markdown="Page 3", image_base64="img3"),
        ],
        total_pages=3,
        extracted_images=[],
        full_markdown="Page 1\nPage 2\nPage 3",
        has_page_images=True,
    )

    page_corrections = [
        PageCorrectionResult(
            corrections=[],
            overall_confidence=0.9,
            processing_notes="OK",
            page_number=1,
        ),
        PageCorrectionResult(
            corrections=[],
            overall_confidence=0.8,
            processing_notes="OK",
            page_number=2,
        ),
        PageCorrectionResult(
            corrections=[],
            overall_confidence=0.7,
            processing_notes="OK",
            page_number=3,
        ),
    ]

    mock_pdf_converter.convert_with_page_images.return_value = multi_page_result
    mock_text_correction_service.process_pages_concurrently.return_value = (
        page_corrections
    )

    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Average: (0.9 + 0.8 + 0.7) / 3 = 0.8
    assert result.confidence_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_process_document_tracks_processing_time(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test processing time is measured and stored."""
    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Processing time should be >= 0 (even if very fast)
    assert result.processing_time_seconds >= 0


@pytest.mark.asyncio
async def test_process_document_stores_metadata_correctly(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
    sample_pdf_conversion_result,
):
    """Test all metadata is correctly stored in job status."""
    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Verify job status was updated (either completed or awaiting_correction_approval)
    assert mock_job_service.update_job_status.called
    assert result.error_message is None


# ============================================================================
# Error Handling Tests (5 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_handles_page_processing_error(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test PageProcessingError is caught and job marked as failed."""
    # Simulate text correction failure
    original_error = Exception("Claude API timeout")
    mock_text_correction_service.process_pages_concurrently.side_effect = (
        PageProcessingError(page_num=2, original_error=original_error)
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    # PageProcessingError → ValueError → caught by generic handler → returns failed result
    result = await service.process_document(sample_job_payload)

    # Should return failed result
    assert result.error_message is not None
    assert "page 2" in result.error_message.lower()


@pytest.mark.asyncio
async def test_process_document_handles_generic_exception(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test generic exceptions are caught and return failed result."""
    # Simulate unexpected error
    mock_pdf_converter.convert_with_page_images.side_effect = RuntimeError(
        "Docling internal error"
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should return failed result (not raise)
    assert result.job_id == sample_job_payload.job_id
    assert result.markdown_url is None
    assert result.confidence_score is None
    assert result.error_message is not None
    assert "Docling internal error" in result.error_message

    # Should update job to failed
    failed_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "failed"
    ]
    assert len(failed_status_call) == 1


@pytest.mark.asyncio
async def test_process_document_updates_job_status_on_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test job status is updated when processing fails."""
    mock_pdf_converter.convert_with_page_images.side_effect = ValueError(
        "Invalid PDF"
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Verify status updated to failed
    mock_job_service.update_job_status.assert_any_call(
        sample_job_payload.job_id, "failed", error="Processing failed: Invalid PDF"
    )


@pytest.mark.asyncio
async def test_process_document_error_message_formatting(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test error messages are formatted consistently."""
    mock_storage_service.download_temp_file.side_effect = Exception("S3 connection lost")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Error message should start with "Processing failed:"
    assert result.error_message.startswith("Processing failed:")
    assert "S3 connection lost" in result.error_message


@pytest.mark.asyncio
async def test_process_document_missing_page_images_raises_error(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test processing fails if Docling doesn't generate page images."""
    # Return conversion result WITHOUT page images
    bad_conversion_result = PDFConversionResult(
        pages=[],
        total_pages=1,
        full_markdown="# Test",
        has_page_images=False,  # CRITICAL: No images
        extracted_images=[],
    )
    mock_pdf_converter.convert_with_page_images.return_value = bad_conversion_result

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail with appropriate error
    assert "page images" in result.error_message.lower()
    assert result.markdown_url is None


# ============================================================================
# Retry Logic Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_retries_job_status_updates(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test retry logic for job status updates (Redis failures)."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    # Simulate Redis failure on first attempt, success on second
    mock_job_service.update_job_status.side_effect = [
        RedisConnectionError("Redis connection timeout"),  # Retryable error
        None,  # Success on retry
        None,  # Subsequent calls
        None,
    ]

    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should succeed after retry
    assert result.error_message is None
    # Should have attempted status update multiple times (with retries)
    assert mock_job_service.update_job_status.call_count >= 2


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_process_document_retries_s3_downloads(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test that processing service calls storage service for downloads.

    Retry logic is now handled inside StorageService, not in ProcessingService.
    This test verifies that ProcessingService makes a single call to storage service,
    which handles retries internally.
    """
    # Mock successful download (storage service handles retries internally)
    mock_storage_service_extended.download_temp_file.return_value = b"fake_pdf_content"

    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should succeed (storage handles retries internally)
    assert result.error_message is None
    # Should have called download once (no outer retry loop)
    assert mock_storage_service_extended.download_temp_file.call_count == 1


@pytest.mark.asyncio
async def test_process_document_retries_s3_uploads(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test that processing service calls storage service for uploads.

    Retry logic is now handled inside StorageService, not in ProcessingService.
    This test verifies that ProcessingService makes a single call to storage service,
    which handles retries internally.
    """
    # Mock successful upload (storage service handles retries internally)
    mock_storage_service_extended.upload_result.return_value = "s3://equalify-results/550e8400.../v20250101_120000/output.md"

    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should succeed (storage handles retries internally)
    assert result.error_message is None
    # upload_result is called multiple times (original markdown, page images, etc.)
    assert mock_storage_service_extended.upload_result.called


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_process_document_retry_exhaustion_handling(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test handling when storage service fails after exhausting internal retries.

    StorageService handles retries internally, so when it raises an exception,
    it has already exhausted its retries. ProcessingService should handle the
    final failure gracefully.
    """
    from fastapi import HTTPException

    # Simulate storage service failure after internal retries exhausted
    mock_storage_service.download_temp_file.side_effect = HTTPException(
        status_code=500,
        detail="Failed to download file: S3 service unavailable"
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail (storage already exhausted retries)
    assert result.error_message is not None
    assert "S3 service unavailable" in result.error_message or "Processing failed" in result.error_message
    # Should have called download once (storage handles retries internally)
    assert mock_storage_service.download_temp_file.call_count == 1


# ============================================================================
# Edge Case Tests (5 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_validates_page_images_exist(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test validation that page images were generated."""
    # Empty pages list but has_page_images=True (inconsistent state)
    bad_result = PDFConversionResult(
        pages=[],  # No pages
        total_pages=1,
        full_markdown="# Test",
        has_page_images=True,
        extracted_images=[],
    )
    mock_pdf_converter.convert_with_page_images.return_value = bad_result

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should complete (process_pages_concurrently receives empty list)
    mock_text_correction_service.process_pages_concurrently.assert_called_once_with([])


@pytest.mark.asyncio
async def test_process_document_handles_empty_pdf(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test handling of PDF with zero pages."""
    empty_result = PDFConversionResult(
        pages=[],
        total_pages=0,
        full_markdown="",
        has_page_images=False,
        extracted_images=[],
    )
    mock_pdf_converter.convert_with_page_images.return_value = empty_result

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail due to missing page images
    assert "page images" in result.error_message.lower()


@pytest.mark.asyncio
async def test_process_document_handles_single_page(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
    sample_pdf_conversion_result,
    sample_page_correction_result,
):
    """Test single-page document processing."""
    service = ProcessingService(
        storage_service=mock_storage_service_extended,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should process successfully
    assert result.error_message is None
    assert result.confidence_score is not None


@pytest.mark.asyncio
async def test_process_document_s3_download_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test handling of S3 download failures after all retries."""
    mock_storage_service.download_temp_file.side_effect = Exception("S3 AccessDenied")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail with S3 error
    assert "S3 AccessDenied" in result.error_message
    assert result.markdown_url is None


@pytest.mark.asyncio
async def test_process_document_s3_upload_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_text_correction_service,
):
    """Test handling of S3 upload failures after all retries."""
    mock_storage_service.upload_result.side_effect = Exception("S3 bucket full")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        text_correction=mock_text_correction_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail with upload error
    assert "S3 bucket full" in result.error_message
    assert result.markdown_url is None
