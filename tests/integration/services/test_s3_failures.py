"""Tests for S3 failure scenarios and retry logic.

Tests PII and Processing services to ensure proper retry behavior
when S3 operations fail with various error conditions.
"""

from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from src.agents.extraction.models import ExtractionMetrics, ExtractionResult
from src.services.job_service import JobService
from src.services.pii_service import PIIDetectionService
from src.services.processing_service import ProcessingService
from src.services.queue_service import QueueService
from src.services.storage_service import StorageService
from src.shared.models.processing import LLMUsage
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload
from src.shared.models.remediation import DocumentManifest, HeadingTree, PageFeatures


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
def processing_service(storage_service, queue_service, job_service, mocker):
    """Create processing service with mocked PDF converter."""
    # Create a mock PDF converter to avoid loading Docling dependencies
    mock_converter = mocker.Mock()
    return ProcessingService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        pdf_converter=mock_converter
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
            created_at=datetime.now(UTC)
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
            created_at=datetime.now(UTC)
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
            created_at=datetime.now(UTC)
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
            created_at=datetime.now(UTC)
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

        # Mock upload: Pipeline calls put_object for:
        # 1. Manifest upload - succeed
        # 2. v0 markdown upload - fail with ServiceUnavailable
        # 3. v0 markdown retry - succeed
        # 4. Final markdown upload - succeed
        mock_s3_client.put_object.side_effect = [
            None,  # Manifest upload succeeds
            ClientError(
                {
                    'Error': {'Code': 'ServiceUnavailable'},
                    'ResponseMetadata': {'HTTPStatusCode': 503}
                },
                'PutObject'
            ),
            None,  # v0 markdown retry succeeds
            None   # Final markdown succeeds
        ]

        # Mock PDF converter
        from src.services.pdf_converter import PageData, PDFConversionResult
        mock_result = PDFConversionResult(
            pages=[
                PageData(
                    page_num=1,
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

        # Mock analyze_document (chained analysis)
        mock_heading_tree = HeadingTree(
            document_title="Test Document",
            title_page=1,
            sections=[],
            total_pages=1,
            layout_type="single_column",
            confidence=0.9
        )
        mock_analysis_manifest = DocumentManifest(
            job_id=job.job_id,
            document_title="Test Document",
            document_type="lecture_notes",
            total_pages=1,
            heading_tree_json=mock_heading_tree.model_dump_json(),
            page_features=[PageFeatures(page_num=1)],
            required_agents=[],
            skip_agents=[],
            analysis_confidence=0.9,
            analysis_notes="Test",
            analysis_model="us.anthropic.claude-sonnet-4-5-20250514-v1:0"
        )
        mock_analysis_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01
        )
        mocker.patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        )

        # Mock extract_with_validation (new 4-phase architecture)
        mock_extraction_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01
        )
        mock_extraction_result = ExtractionResult(
            markdown="# Test Improved",
            metrics=ExtractionMetrics(
                confidence=0.88,
                heading_count=1,
                total_words=2,
                issues=[],
                critical_issue_count=0,
            ),
            attempt_count=1,
            correction_applied=False,
        )
        mocker.patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            return_value=(mock_extraction_result, mock_extraction_usage),
        )

        # Execute
        result = await processing_service.process_document(job)

        # Verify upload was retried (should have 4 calls: manifest, fail, success, success)
        assert mock_s3_client.put_object.call_count == 4
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

        # Mock upload to fail with AccessDenied (non-retryable)
        mock_s3_client.put_object.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied'}}, 'PutObject'
        )

        # Mock converter
        from src.services.pdf_converter import PageData, PDFConversionResult

        mock_result = PDFConversionResult(
            pages=[PageData(page_num=1, image_base64="base64")],
            total_pages=1,
            has_page_images=True,
            extracted_images=[]
        )
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(return_value=mock_result)
        processing_service.pdf_converter = mock_converter

        # Mock analyze_document (chained analysis)
        mock_heading_tree = HeadingTree(
            document_title="Test Document",
            title_page=1,
            sections=[],
            total_pages=1,
            layout_type="single_column",
            confidence=0.9
        )
        mock_analysis_manifest = DocumentManifest(
            job_id=job.job_id,
            document_title="Test Document",
            document_type="lecture_notes",
            total_pages=1,
            heading_tree_json=mock_heading_tree.model_dump_json(),
            page_features=[PageFeatures(page_num=1)],
            required_agents=[],
            skip_agents=[],
            analysis_confidence=0.9,
            analysis_notes="Test",
            analysis_model="us.anthropic.claude-sonnet-4-5-20250514-v1:0"
        )
        mock_analysis_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01
        )
        mocker.patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        )

        # Mock extract_with_validation (new 4-phase architecture)
        mock_extraction_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01
        )
        mock_extraction_result = ExtractionResult(
            markdown="# Test",
            metrics=ExtractionMetrics(
                confidence=0.88,
                heading_count=1,
                total_words=1,
                issues=[],
                critical_issue_count=0,
            ),
            attempt_count=1,
            correction_applied=False,
        )
        mocker.patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            return_value=(mock_extraction_result, mock_extraction_usage),
        )

        # Execute - should NOT raise, but catch exception and mark job as failed
        result = await processing_service.process_document(job)

        # StorageService retry logic correctly identifies AccessDenied as non-retryable
        # so it fails immediately without retry
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
            created_at=datetime.now(UTC)
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
            created_at=datetime.now(UTC)
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
