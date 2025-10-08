"""Tests for S3 failure scenarios and retry logic.

Tests PII and Processing services to ensure proper retry behavior
when S3 operations fail with various error conditions.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from botocore.exceptions import ClientError
from io import BytesIO
from fastapi import HTTPException

from src.services.pii_service import PIIDetectionService
from src.services.processing_service import ProcessingService
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    return mocker.MagicMock()


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    # Mock common Redis operations
    client.hset = AsyncMock()
    client.lpush = AsyncMock()
    client.zadd = AsyncMock()
    client.setex = AsyncMock()
    return client


@pytest.fixture
def storage_service(mock_s3_client):
    """Create storage service with mock S3 client."""
    return StorageService(
        s3_client=mock_s3_client,
        temp_bucket="test-temp-bucket",
        results_bucket="test-results-bucket"
    )


@pytest.fixture
def queue_service(mock_redis_client):
    """Create queue service with mock Redis client."""
    return QueueService(redis_client=mock_redis_client)


@pytest.fixture
def job_service(mock_redis_client):
    """Create job service with mock Redis client."""
    return JobService(redis_client=mock_redis_client)


@pytest.fixture
def pii_service(storage_service, queue_service, job_service, mocker):
    """Create PII detection service."""
    service = PIIDetectionService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )
    # Mock PII analyzer to avoid loading spaCy (10-30s per worker)
    service.pii_analyzer = mocker.MagicMock()
    service.pii_analyzer.analyze_text.return_value = []  # No PII by default
    return service


@pytest.fixture
def processing_service(storage_service, queue_service, job_service):
    """Create processing service."""
    return ProcessingService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )


class TestPIIServiceS3Failures:
    """Test PII service handling of S3 failures."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_s3_download_timeout_retry_succeeds(
        self, pii_service, mock_s3_client, mocker
    ):
        """Test S3 download retries on timeout and eventually succeeds.

        StorageService now handles retries internally, so we mock S3 client
        to fail twice then succeed. The PII service should see one successful call.
        """
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440001",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"

        # Mock S3 client to fail twice (retryable errors), then succeed
        mock_s3_client.get_object.side_effect = [
            ClientError({'Error': {'Code': 'RequestTimeout'}}, 'GetObject'),
            ClientError({'Error': {'Code': 'ServiceUnavailable'}}, 'GetObject'),
            {'Body': mocker.Mock(read=mocker.Mock(return_value=pdf_content))}
        ]

        # Mock PDF extraction
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Test content"
        )

        # Execute
        await pii_service.process_pii_job(job)

        # Verify S3 was called 3 times (2 failures + 1 success) by StorageService retry logic
        assert mock_s3_client.get_object.call_count == 3

    @pytest.mark.asyncio
    async def test_s3_download_throttling_retry(
        self, pii_service, mock_s3_client, mocker
    ):
        """Test S3 download retries on throttling errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440002",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"
        mock_s3_client.get_object.side_effect = [
            ClientError(
                {
                    'Error': {'Code': 'ThrottlingException'},
                    'ResponseMetadata': {'HTTPStatusCode': 429}
                },
                'GetObject'
            ),
            {'Body': BytesIO(pdf_content)}
        ]

        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Test content"
        )

        await pii_service.process_pii_job(job)

        assert mock_s3_client.get_object.call_count == 2

    @pytest.mark.asyncio
    async def test_s3_download_no_retry_on_nosuchkey(
        self, pii_service, mock_s3_client, mock_redis_client
    ):
        """Test S3 download fails immediately on NoSuchKey (non-retryable)."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440003",
            s3_key="temp/missing.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock S3 to return NoSuchKey error
        mock_s3_client.get_object.side_effect = ClientError(
            {'Error': {'Code': 'NoSuchKey'}},
            'GetObject'
        )

        # Execute - should NOT raise, but catch exception and mark job as failed
        await pii_service.process_pii_job(job)

        # Verify only one attempt (no retries) - non-retryable error
        assert mock_s3_client.get_object.call_count == 1

        # Verify job status was updated to failed (with retries for Redis)
        # The update_job_status for FAILED should have been called
        assert mock_redis_client.hset.called

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)  # Retry logic with backoff can take time
    async def test_s3_download_max_retries_exceeded(
        self, pii_service, mock_s3_client, mock_redis_client
    ):
        """Test S3 download fails after max retries."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440004",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock S3 to always timeout
        mock_s3_client.get_object.side_effect = ClientError(
            {'Error': {'Code': 'RequestTimeout'}},
            'GetObject'
        )

        # Execute - should NOT raise, but catch exception and mark job as failed
        await pii_service.process_pii_job(job)

        # Verify 3 attempts were made (max_attempts=3)
        assert mock_s3_client.get_object.call_count == 3


class TestProcessingServiceS3Failures:
    """Test Processing service handling of S3 failures."""

    @pytest.mark.asyncio
    async def test_pdf_download_retry_on_network_error(
        self, processing_service, mock_s3_client, mocker
    ):
        """Test PDF download retries on network errors."""
        job = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440005",
            s3_key="temp/test.pdf"
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"
        mock_s3_client.get_object.side_effect = [
            ClientError({'Error': {'Code': 'RequestTimeout'}}, 'GetObject'),
            {'Body': BytesIO(pdf_content)}
        ]

        # Mock PDF converter to avoid actual conversion
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(
            side_effect=RuntimeError("No page images")  # Trigger early exit
        )
        processing_service.pdf_converter = mock_converter

        # Execute - should NOT raise, but catch exception and mark job as failed
        result = await processing_service.process_document(job)

        # Verify S3 was retried
        assert mock_s3_client.get_object.call_count == 2

        # Verify result indicates failure (has error_message)
        assert result is not None
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_result_upload_retry_on_service_unavailable(
        self, processing_service, mock_s3_client, mock_redis_client, mocker
    ):
        """Test result upload retries on service unavailable."""
        job = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440006",
            s3_key="temp/test.pdf"
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"

        # Mock download to succeed
        mock_s3_client.get_object.return_value = {'Body': BytesIO(pdf_content)}

        # Mock upload to fail then succeed
        mock_s3_client.put_object.side_effect = [
            ClientError(
                {
                    'Error': {'Code': 'ServiceUnavailable'},
                    'ResponseMetadata': {'HTTPStatusCode': 503}
                },
                'PutObject'
            ),
            None  # Success
        ]

        # Mock PDF converter
        from src.services.pdf_converter import PDFConversionResult, PageData
        mock_result = PDFConversionResult(
            full_markdown="# Test",
            pages=[
                PageData(
                    page_num=1,
                    markdown="# Test",
                    image_base64="base64data"
                )
            ],
            total_pages=1,
            has_page_images=True,
            extracted_images=[]
        )
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(return_value=mock_result)
        processing_service.pdf_converter = mock_converter

        # Mock AI enhancement
        from src.agents.accessibility_agent import PageImprovementResult
        mock_ai = mocker.Mock()
        mock_ai.process_pages_concurrently = AsyncMock(
            return_value=[
                PageImprovementResult(
                    improved_markdown="# Test Improved",
                    confidence_score=0.95,
                    processing_notes="Test notes"
                )
            ]
        )
        mock_ai.combine_page_markdown = Mock(return_value="# Test Improved")
        processing_service.ai_enhancement = mock_ai

        # Execute
        result = await processing_service.process_document(job)

        # Verify upload was retried
        assert mock_s3_client.put_object.call_count == 2
        assert result.markdown_url is not None

    @pytest.mark.asyncio
    async def test_result_upload_no_retry_on_access_denied(
        self, processing_service, mock_s3_client, mock_redis_client, mocker
    ):
        """Test result upload fails immediately on AccessDenied."""
        job = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440007",
            s3_key="temp/test.pdf"
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"
        mock_s3_client.get_object.return_value = {'Body': BytesIO(pdf_content)}

        # Mock upload to fail with access denied
        mock_s3_client.put_object.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied'}},
            'PutObject'
        )

        # Mock converter and AI
        from src.services.pdf_converter import PDFConversionResult, PageData
        from src.agents.accessibility_agent import PageImprovementResult

        mock_result = PDFConversionResult(
            full_markdown="# Test",
            pages=[PageData(page_num=1, markdown="# Test", image_base64="base64")],
            total_pages=1,
            has_page_images=True,
            extracted_images=[]
        )
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(return_value=mock_result)
        processing_service.pdf_converter = mock_converter

        mock_ai = mocker.Mock()
        mock_ai.process_pages_concurrently = AsyncMock(
            return_value=[
                PageImprovementResult(
                    improved_markdown="# Test",
                    confidence_score=0.95,
                    processing_notes="Test notes"
                )
            ]
        )
        mock_ai.combine_page_markdown = Mock(return_value="# Test")
        processing_service.ai_enhancement = mock_ai

        # Execute - should NOT raise, but catch exception and mark job as failed
        result = await processing_service.process_document(job)

        # StorageService retry logic correctly identifies AccessDenied as non-retryable
        # so it fails immediately without retry (call_count = 1)
        assert mock_s3_client.put_object.call_count == 1

        # Verify result indicates failure
        assert result is not None
        assert result.error_message is not None


class TestS3FailureRecovery:
    """Test recovery scenarios after S3 failures."""

    @pytest.mark.asyncio
    async def test_job_status_updated_after_s3_failure(
        self, pii_service, mock_s3_client, mock_redis_client
    ):
        """Test that job status is updated to FAILED after S3 errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440008",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock permanent S3 failure
        mock_s3_client.get_object.side_effect = ClientError(
            {'Error': {'Code': 'NoSuchKey'}},
            'GetObject'
        )

        # Execute - should NOT raise, but catch exception and mark job as failed
        await pii_service.process_pii_job(job)

        # Verify status update was attempted (with retries for Redis)
        # Should have multiple calls due to retry logic
        assert mock_redis_client.hset.call_count >= 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    async def test_transient_s3_error_eventual_success(
        self, pii_service, mock_s3_client, mocker
    ):
        """Test full workflow succeeds after transient S3 errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440009",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        pdf_content = b"%PDF-1.4\nTest PDF\n%%EOF"

        # Simulate transient errors then success
        mock_s3_client.get_object.side_effect = [
            ClientError({'Error': {'Code': 'RequestTimeout'}}, 'GetObject'),
            ClientError({'Error': {'Code': 'ServiceUnavailable'}}, 'GetObject'),
            {'Body': BytesIO(pdf_content)}
        ]

        # Mock PDF extraction (no PII)
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Clean content with no PII"
        )

        # Execute
        await pii_service.process_pii_job(job)

        # Verify job completed successfully after retries
        assert mock_s3_client.get_object.call_count == 3
