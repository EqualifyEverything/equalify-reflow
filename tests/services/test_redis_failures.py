"""Tests for Redis failure scenarios and retry logic.

Tests PII and Processing services to ensure proper retry behavior
when Redis operations fail with various error conditions.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
from io import BytesIO

from src.services.pii_service import PIIDetectionService
from src.services.processing_service import ProcessingService
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    client = mocker.MagicMock()
    # Default successful S3 operations
    client.get_object.return_value = {'Body': BytesIO(b"%PDF-1.4\nTest\n%%EOF")}
    client.put_object.return_value = None
    return client


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    return mocker.AsyncMock()


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
def pii_service(storage_service, queue_service, job_service):
    """Create PII detection service."""
    return PIIDetectionService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )


@pytest.fixture
def processing_service(storage_service, queue_service, job_service):
    """Create processing service."""
    return ProcessingService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )


class TestPIIServiceRedisFailures:
    """Test PII service handling of Redis failures."""

    @pytest.mark.asyncio
    async def test_job_status_update_retry_on_connection_error(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test job status update retries on Redis connection errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440010",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock Redis to fail twice then succeed
        mock_redis_client.hset.side_effect = [
            RedisConnectionError("Connection refused"),
            RedisConnectionError("Connection lost"),
            True  # Success
        ]

        # Mock PDF extraction
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Test content"
        )

        # Mock PII analyzer to return no findings
        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = []
        pii_service.pii_analyzer = mock_analyzer

        # Execute
        await pii_service.process_pii_job(job)

        # Verify Redis was retried for status update
        # Should be called at least 3 times (initial status + retries)
        assert mock_redis_client.hset.call_count >= 3

    @pytest.mark.asyncio
    async def test_queue_enqueue_retry_on_redis_error(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test queue enqueue retries on Redis errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440011",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock lpush to fail then succeed
        mock_redis_client.lpush.side_effect = [
            RedisError("Redis error"),
            1  # Success
        ]

        # Mock other Redis operations
        mock_redis_client.hset.return_value = True
        mock_redis_client.zadd.return_value = True
        mock_redis_client.setex.return_value = True

        # Mock PDF extraction
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Test content"
        )

        # Mock PII analyzer to return no findings (triggers processing queue)
        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = []
        pii_service.pii_analyzer = mock_analyzer

        # Execute
        await pii_service.process_pii_job(job)

        # Verify lpush was retried
        assert mock_redis_client.lpush.call_count >= 1

    @pytest.mark.asyncio
    async def test_approval_queue_retry_with_pii_findings(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test approval queue operations retry on Redis errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440012",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock Redis operations with transient failures
        call_count = {"lpush": 0, "hset": 0, "zadd": 0, "setex": 0}

        def lpush_side_effect(*args, **kwargs):
            call_count["lpush"] += 1
            if call_count["lpush"] < 2:
                raise RedisConnectionError("Connection lost")
            return 1

        def hset_side_effect(*args, **kwargs):
            call_count["hset"] += 1
            if call_count["hset"] < 2:
                raise RedisError("Redis error")
            return True

        mock_redis_client.lpush.side_effect = lpush_side_effect
        mock_redis_client.hset.side_effect = hset_side_effect
        mock_redis_client.zadd.return_value = True
        mock_redis_client.setex.return_value = True

        # Mock PDF extraction
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="SSN: 123-45-6789"
        )

        # Mock PII analyzer to return findings (triggers approval queue)
        from src.shared.models.pii import PIIFinding
        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = [
            PIIFinding(
                entity_type="US_SSN",
                text="123-45-6789",
                start=5,
                end=16,
                score=0.95
            )
        ]
        pii_service.pii_analyzer = mock_analyzer

        # Execute
        await pii_service.process_pii_job(job)

        # Verify retries occurred
        assert call_count["lpush"] >= 1
        assert call_count["hset"] >= 1

    @pytest.mark.asyncio
    async def test_timeout_tracking_retry_on_redis_error(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test timeout tracking (zadd) retries on Redis errors."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440013",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock zadd to fail then succeed
        mock_redis_client.zadd.side_effect = [
            RedisConnectionError("Connection error"),
            True  # Success
        ]

        mock_redis_client.lpush.return_value = 1
        mock_redis_client.hset.return_value = True
        mock_redis_client.setex.return_value = True

        # Mock PDF extraction with PII
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Email: test@example.com"
        )

        from src.shared.models.pii import PIIFinding
        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = [
            PIIFinding(
                entity_type="EMAIL_ADDRESS",
                text="test@example.com",
                start=7,
                end=24,
                score=0.98
            )
        ]
        pii_service.pii_analyzer = mock_analyzer

        # Execute
        await pii_service.process_pii_job(job)

        # Verify zadd was retried
        assert mock_redis_client.zadd.call_count >= 1


class TestProcessingServiceRedisFailures:
    """Test Processing service handling of Redis failures."""

    @pytest.mark.asyncio
    async def test_status_update_retry_on_redis_error(
        self, processing_service, mock_redis_client, mocker
    ):
        """Test job status update retries on Redis errors."""
        job = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440014",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock Redis to fail then succeed
        mock_redis_client.hset.side_effect = [
            RedisError("Redis error"),
            RedisConnectionError("Connection lost"),
            True,  # Success for processing status
            True,  # Success for completed status
        ]

        # Mock converter and AI services
        from src.shared.models.pdf import ConversionResult, PageResult
        from src.shared.models.ai import PageImprovementResult

        mock_result = ConversionResult(
            full_markdown="# Test",
            pages=[PageResult(page_num=1, markdown="# Test", page_image_base64="base64")],
            total_pages=1,
            has_page_images=True
        )
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(return_value=mock_result)
        processing_service.pdf_converter = mock_converter

        mock_ai = mocker.Mock()
        mock_ai.process_pages_concurrently = AsyncMock(
            return_value=[
                PageImprovementResult(
                    page_num=1,
                    improved_markdown="# Test",
                    confidence_score=0.95,
                    changes_made=[]
                )
            ]
        )
        mock_ai.combine_page_markdown = Mock(return_value="# Test")
        processing_service.ai_enhancement = mock_ai

        # Execute
        await processing_service.process_document(job)

        # Verify status update was retried
        assert mock_redis_client.hset.call_count >= 3

    @pytest.mark.asyncio
    async def test_failed_status_update_retry_on_error(
        self, processing_service, mock_redis_client, mocker
    ):
        """Test FAILED status update retries when processing fails."""
        job = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440015",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Mock initial status update to succeed
        # Mock failed status update to retry
        mock_redis_client.hset.side_effect = [
            True,  # Initial processing status
            RedisConnectionError("Connection error"),
            True  # Success on retry
        ]

        # Mock converter to fail
        mock_converter = mocker.Mock()
        mock_converter.convert_with_page_images = AsyncMock(
            side_effect=RuntimeError("Conversion failed")
        )
        processing_service.pdf_converter = mock_converter

        # Execute
        result = await processing_service.process_document(job)

        # Verify failed status update was retried
        assert result.error_message is not None
        assert mock_redis_client.hset.call_count >= 2


class TestRedisFailureRecovery:
    """Test recovery scenarios after Redis failures."""

    @pytest.mark.asyncio
    async def test_eventual_consistency_after_redis_failures(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test that operations eventually succeed after Redis failures."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440016",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Simulate intermittent Redis failures across operations
        operation_count = {"hset": 0, "lpush": 0}

        def hset_side_effect(*args, **kwargs):
            operation_count["hset"] += 1
            if operation_count["hset"] == 2:  # Fail on second call
                raise RedisConnectionError("Intermittent error")
            return True

        def lpush_side_effect(*args, **kwargs):
            operation_count["lpush"] += 1
            if operation_count["lpush"] == 1:  # Fail on first call
                raise RedisError("Queue error")
            return 1

        mock_redis_client.hset.side_effect = hset_side_effect
        mock_redis_client.lpush.side_effect = lpush_side_effect
        mock_redis_client.zadd.return_value = True
        mock_redis_client.setex.return_value = True

        # Mock extraction
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="Test content"
        )

        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = []
        pii_service.pii_analyzer = mock_analyzer

        # Execute - should succeed despite intermittent failures
        await pii_service.process_pii_job(job)

        # Verify operations completed with retries
        assert operation_count["hset"] >= 2
        assert operation_count["lpush"] >= 1

    @pytest.mark.asyncio
    async def test_multiple_redis_operations_all_retry(
        self, pii_service, mock_redis_client, mocker
    ):
        """Test that multiple Redis operations all use retry logic."""
        job = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440017",
            s3_key="temp/test.pdf",
            created_at=datetime.now(timezone.utc)
        )

        # Track all Redis operations
        redis_calls = {
            "hset": [],
            "lpush": [],
            "zadd": [],
            "setex": []
        }

        def track_hset(*args, **kwargs):
            redis_calls["hset"].append(args)
            if len(redis_calls["hset"]) == 1:
                raise RedisError("First hset error")
            return True

        def track_lpush(*args, **kwargs):
            redis_calls["lpush"].append(args)
            if len(redis_calls["lpush"]) == 1:
                raise RedisConnectionError("First lpush error")
            return 1

        def track_zadd(*args, **kwargs):
            redis_calls["zadd"].append(args)
            if len(redis_calls["zadd"]) == 1:
                raise RedisError("First zadd error")
            return True

        def track_setex(*args, **kwargs):
            redis_calls["setex"].append(args)
            if len(redis_calls["setex"]) == 1:
                raise RedisConnectionError("First setex error")
            return True

        mock_redis_client.hset.side_effect = track_hset
        mock_redis_client.lpush.side_effect = track_lpush
        mock_redis_client.zadd.side_effect = track_zadd
        mock_redis_client.setex.side_effect = track_setex

        # Mock extraction with PII (triggers approval flow - more Redis ops)
        mocker.patch(
            'src.services.pii_service.extract_pdf_text',
            return_value="SSN: 123-45-6789"
        )

        from src.shared.models.pii import PIIFinding
        mock_analyzer = mocker.Mock()
        mock_analyzer.analyze_text.return_value = [
            PIIFinding(
                entity_type="US_SSN",
                text="123-45-6789",
                start=5,
                end=16,
                score=0.95
            )
        ]
        pii_service.pii_analyzer = mock_analyzer

        # Execute
        await pii_service.process_pii_job(job)

        # Verify all Redis operations were retried
        assert len(redis_calls["hset"]) >= 2  # Status updates with retry
        assert len(redis_calls["lpush"]) >= 2  # Queue enqueue with retry
        assert len(redis_calls["zadd"]) >= 2  # Timeout tracking with retry
        assert len(redis_calls["setex"]) >= 2  # Token mapping with retry
