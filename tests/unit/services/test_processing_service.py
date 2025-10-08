"""Unit tests for ProcessingService.

Tests the main processing orchestrator with all dependencies mocked.
Validates the 8-step processing pipeline including error handling and retries.
"""

import pytest
from unittest.mock import AsyncMock, call
from datetime import datetime

from src.services.processing_service import ProcessingService
from src.services.ai_enhancement_service import PageProcessingError
from src.services.pdf_converter import PDFConversionResult, PageData
from src.agents.accessibility_agent import PageImprovementResult
from src.shared.models.queue import ProcessingQueuePayload
from src.shared.models.processing import ProcessingResult


pytestmark = pytest.mark.unit


# ============================================================================
# Initialization Tests (2 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_processing_service_init_with_default_dependencies(
    mock_storage_service, mock_queue_service, mock_job_service, mocker
):
    """Test ProcessingService creates default dependencies if not provided."""
    # Mock the AIEnhancementService to avoid needing ANTHROPIC_API_KEY
    mock_ai = mocker.MagicMock()
    mock_pdf = mocker.MagicMock()

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf,
        ai_enhancement=mock_ai,
    )

    # Should have all dependencies
    assert service.pdf_converter is not None
    assert service.ai_enhancement is not None
    assert service.storage == mock_storage_service
    assert service.queue == mock_queue_service
    assert service.job == mock_job_service


@pytest.mark.asyncio
async def test_processing_service_init_with_custom_dependencies(
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test ProcessingService accepts custom dependencies for testing."""
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    # Should use injected dependencies
    assert service.pdf_converter == mock_pdf_converter
    assert service.ai_enhancement == mock_ai_enhancement_service


# ============================================================================
# Success Path Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_happy_path(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
    sample_pdf_conversion_result,
    sample_page_improvement_result,
):
    """Test successful end-to-end document processing.

    NOTE: AI processing is currently disabled for deliverable 1.
    This test validates the Docling conversion pipeline without AI enhancement.
    """
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Verify result
    assert result.job_id == sample_job_payload.job_id
    assert result.markdown_url == "s3://equalify-results/550e8400.../v20250101_120000/output.md"
    # AI processing disabled - expect confidence_score=0.0
    assert result.confidence_score == 0.0
    assert result.processing_time_seconds >= 0  # Can be 0 if very fast
    assert result.error_message is None

    # Verify pipeline steps executed in order
    mock_job_service.update_job_status.assert_any_call(
        sample_job_payload.job_id, "processing"
    )
    mock_storage_service.download_temp_file.assert_called_once_with(
        s3_key=sample_job_payload.s3_key
    )
    mock_pdf_converter.convert_with_page_images.assert_called_once()
    # AI processing disabled - these methods should NOT be called
    mock_ai_enhancement_service.process_pages_concurrently.assert_not_called()
    mock_ai_enhancement_service.combine_page_markdown.assert_not_called()
    mock_storage_service.upload_result.assert_called_once()

    # Verify final status update
    final_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "completed"
    ]
    assert len(final_status_call) == 1
    # AI processing disabled - expect confidence_score=0.0 and confidence_level="raw_docling_output"
    assert final_status_call[0].kwargs["metadata"]["confidence_score"] == 0.0
    assert final_status_call[0].kwargs["metadata"]["confidence_level"] == "raw_docling_output"


@pytest.mark.skip(reason="AI processing disabled for deliverable 1 - will re-enable when AI is active")
@pytest.mark.asyncio
async def test_process_document_calculates_confidence_correctly(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
    sample_pdf_conversion_result,
):
    """Test confidence score calculation from page results.

    SKIPPED: This test validates AI confidence score calculation, which is disabled
    for deliverable 1. Will re-enable when AI processing is restored.
    """
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

    page_improvements = [
        PageImprovementResult(
            improved_markdown="Improved 1", confidence_score=0.9, processing_notes="OK"
        ),
        PageImprovementResult(
            improved_markdown="Improved 2", confidence_score=0.8, processing_notes="OK"
        ),
        PageImprovementResult(
            improved_markdown="Improved 3", confidence_score=0.7, processing_notes="OK"
        ),
    ]

    mock_pdf_converter.convert_with_page_images.return_value = multi_page_result
    mock_ai_enhancement_service.process_pages_concurrently.return_value = (
        page_improvements
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Average: (0.9 + 0.8 + 0.7) / 3 = 0.8
    assert result.confidence_score == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_process_document_tracks_processing_time(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test processing time is measured and stored."""
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Processing time should be > 0 (even if very fast)
    assert result.processing_time_seconds >= 0

    # Metadata should include processing time
    final_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "completed"
    ]
    assert final_status_call[0].kwargs["metadata"]["processing_time_seconds"] >= 0


@pytest.mark.asyncio
async def test_process_document_stores_metadata_correctly(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
    sample_pdf_conversion_result,
):
    """Test all metadata is correctly stored in job status.

    NOTE: AI processing is currently disabled for deliverable 1.
    Validates metadata structure with raw Docling output.
    """
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Get final status update call
    final_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "completed"
    ]

    metadata = final_status_call[0].kwargs["metadata"]
    assert metadata["markdown_url"] == result.markdown_url
    assert metadata["confidence_score"] == result.confidence_score
    # AI processing disabled - expect "raw_docling_output" instead of "high", "medium", "low"
    assert metadata["confidence_level"] == "raw_docling_output"
    assert metadata["processing_time_seconds"] >= 0
    assert metadata["total_pages"] == 1


# ============================================================================
# Error Handling Tests (5 tests)
# ============================================================================


@pytest.mark.skip(reason="AI processing disabled for deliverable 1 - will re-enable when AI is active")
@pytest.mark.asyncio
async def test_process_document_handles_page_processing_error(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test PageProcessingError is caught and job marked as failed.

    SKIPPED: This test validates AI error handling, which is not triggered
    when AI processing is disabled. Will re-enable when AI processing is restored.
    """
    # Simulate AI processing failure
    original_error = Exception("Claude API timeout")
    mock_ai_enhancement_service.process_pages_concurrently.side_effect = (
        PageProcessingError(page_num=2, original_error=original_error)
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    # PageProcessingError → ValueError → caught by generic handler → returns failed result
    result = await service.process_document(sample_job_payload)

    # Should return failed result
    assert result.error_message is not None
    assert "page 2" in result.error_message

    # Should update job to failed
    failed_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "failed"
    ]
    assert len(failed_status_call) >= 1  # May be called twice (in PageProcessingError handler + generic handler)


@pytest.mark.asyncio
async def test_process_document_handles_generic_exception(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
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
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_ai_enhancement_service,
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
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_ai_enhancement_service,
):
    """Test error messages are formatted consistently."""
    mock_storage_service.download_temp_file.side_effect = Exception("S3 connection lost")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_ai_enhancement_service,
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
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
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
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test retry logic for S3 download failures."""
    import asyncio

    # Simulate S3 throttling on first attempt (use retryable error)
    mock_storage_service.download_temp_file.side_effect = [
        asyncio.TimeoutError("S3 throttling"),  # Retryable
        b"fake_pdf_content",  # Success on retry
    ]

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should succeed after retry
    assert result.error_message is None
    # Should have attempted download twice
    assert mock_storage_service.download_temp_file.call_count == 2


@pytest.mark.asyncio
async def test_process_document_retries_s3_uploads(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test retry logic for S3 upload failures."""
    # Simulate S3 network error on first attempt (use retryable error)
    mock_storage_service.upload_result.side_effect = [
        ConnectionError("Network error"),  # Retryable
        "s3://equalify-results/550e8400.../v20250101_120000/output.md",  # Success
    ]

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should succeed after retry
    assert result.error_message is None
    assert mock_storage_service.upload_result.call_count == 2


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_process_document_retry_exhaustion_handling(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test handling when all retry attempts are exhausted."""
    import asyncio

    # Simulate persistent S3 failure (use retryable error)
    mock_storage_service.download_temp_file.side_effect = asyncio.TimeoutError(
        "S3 service unavailable"
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail after all retries exhausted
    assert result.error_message is not None
    assert "S3 service unavailable" in result.error_message
    # Should have attempted download 3 times (max_attempts)
    assert mock_storage_service.download_temp_file.call_count == 3


# ============================================================================
# Edge Case Tests (5 tests)
# ============================================================================


@pytest.mark.skip(reason="AI processing disabled for deliverable 1 - will re-enable when AI is active")
@pytest.mark.asyncio
async def test_process_document_validates_page_images_exist(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test validation that page images were generated.

    SKIPPED: This test validates that page images are passed to AI service.
    Since AI processing is disabled, process_pages_concurrently is never called.
    Will re-enable when AI processing is restored.
    """
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
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should complete (inconsistency handled by AI service)
    # AI service will receive empty pages list
    mock_ai_enhancement_service.process_pages_concurrently.assert_called_once_with([])


@pytest.mark.asyncio
async def test_process_document_handles_empty_pdf(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
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
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail due to missing page images
    assert "page images" in result.error_message.lower()


@pytest.mark.asyncio
async def test_process_document_handles_single_page(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
    sample_pdf_conversion_result,
    sample_page_improvement_result,
):
    """Test single-page document processing.

    NOTE: AI processing is currently disabled for deliverable 1.
    Validates single-page Docling conversion.
    """
    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should process successfully
    assert result.error_message is None
    # AI processing disabled - expect confidence_score=0.0
    assert result.confidence_score == 0.0

    # Metadata should show 1 page
    final_status_call = [
        call_args
        for call_args in mock_job_service.update_job_status.call_args_list
        if call_args[0][1] == "completed"
    ]
    assert final_status_call[0].kwargs["metadata"]["total_pages"] == 1


@pytest.mark.asyncio
async def test_process_document_s3_download_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_ai_enhancement_service,
):
    """Test handling of S3 download failures after all retries."""
    mock_storage_service.download_temp_file.side_effect = Exception("S3 AccessDenied")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
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
    mock_ai_enhancement_service,
):
    """Test handling of S3 upload failures after all retries."""
    mock_storage_service.upload_result.side_effect = Exception("S3 bucket full")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement_service,
    )

    result = await service.process_document(sample_job_payload)

    # Should fail with upload error
    assert "S3 bucket full" in result.error_message
    assert result.markdown_url is None
