"""Tests for error handling edge cases (Group G bugs).

Tests for:
- Bug #11: File seek failure handling
- Bug #12: Best-effort cleanup in delete_temp_file
- Bug #13: Rate limit key collision prevention
"""

import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from io import BytesIO
from fastapi import UploadFile, HTTPException

from src.services.storage_service import StorageService
from src.services.rate_limit_service import RateLimitService
from src.config import settings


class TestFileSeekErrorHandling:
    """Tests for Bug #11: File seek failure handling."""

    @pytest.fixture
    def storage_service(self, mock_s3_client):
        """Create storage service with mocked S3 client."""
        return StorageService(
            s3_client=mock_s3_client,
            temp_bucket=settings.s3_temp_bucket,
            results_bucket=settings.s3_results_bucket
        )

    @pytest.mark.parametrize("error_method,error_class,error_msg,expected_detail", [
        ("seek", OSError, "Device not ready", "Device not ready"),
        ("seek", IOError, "File handle closed", "Unable to read file"),
        ("tell", OSError, "Position unavailable", "Unable to read file"),
    ])
    @pytest.mark.asyncio
    async def test_file_operation_errors(
        self, storage_service, error_method, error_class, error_msg, expected_detail
    ):
        """Test handling of various file operation errors (Bug #11).

        Parameterized test covering:
        - OSError during seek (Device not ready)
        - IOError during seek (File handle closed)
        - OSError during tell (Position unavailable)
        """
        mock_file = Mock()

        if error_method == "seek":
            mock_file.seek.side_effect = error_class(error_msg)
        elif error_method == "tell":
            mock_file.seek.return_value = None
            mock_file.tell.side_effect = error_class(error_msg)

        upload_file = Mock(spec=UploadFile)
        upload_file.filename = "test.pdf"
        upload_file.file = mock_file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc_info:
            await storage_service.store_document(upload_file)

        assert exc_info.value.status_code == 400
        assert expected_detail in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_file_seek_attribute_error(self, storage_service):
        """Test handling of AttributeError for objects without seek method."""
        # Create an object without seek method
        mock_file = Mock(spec=[])  # Empty spec means no methods
        delattr(mock_file, 'seek')  # Ensure no seek attribute

        upload_file = Mock(spec=UploadFile)
        upload_file.filename = "test.pdf"
        upload_file.file = mock_file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc_info:
            await storage_service.store_document(upload_file)

        assert exc_info.value.status_code == 400
        assert "Unable to read file" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_file_operations(self, storage_service):
        """Test that successful file operations work as expected."""
        # Create a valid file-like object
        file_content = b"%PDF-1.4\n" + b"%Padding content\n" * 10 + b"%%EOF"
        mock_file = BytesIO(file_content)

        storage_service.s3_client.upload_fileobj = Mock()

        upload_file = Mock(spec=UploadFile)
        upload_file.filename = "test.pdf"
        upload_file.file = mock_file
        upload_file.content_type = "application/pdf"

        job_id, s3_key = await storage_service.store_document(upload_file)

        assert job_id is not None
        assert s3_key.startswith("temp/")
        assert s3_key.endswith(".pdf")
        storage_service.s3_client.upload_fileobj.assert_called_once()


class TestBestEffortCleanup:
    """Tests for Bug #12: Best-effort cleanup in delete_temp_file."""

    @pytest.fixture
    def storage_service(self, mock_s3_client):
        """Create storage service with mocked S3 client."""
        return StorageService(
            s3_client=mock_s3_client,
            temp_bucket=settings.s3_temp_bucket,
            results_bucket=settings.s3_results_bucket
        )

    @pytest.mark.asyncio
    async def test_delete_success_returns_true(self, storage_service):
        """Test that successful deletion returns True."""
        storage_service.s3_client.delete_object = Mock(return_value={})

        result = await storage_service.delete_temp_file("temp/test.pdf")

        assert result is True
        storage_service.s3_client.delete_object.assert_called_once()

    @pytest.mark.parametrize("error_type,error_msg", [
        (Exception, "Unexpected S3 error"),
        (ConnectionError, "Network unreachable"),
        (TimeoutError, "Request timed out"),
    ])
    @pytest.mark.asyncio
    async def test_delete_errors_return_false(self, storage_service, error_type, error_msg):
        """Test that all error types return False instead of raising (Bug #12).

        Parameterized test covering:
        - Generic exceptions
        - Network errors
        - Timeout errors
        """
        storage_service.s3_client.delete_object = Mock(
            side_effect=error_type(error_msg)
        )

        # Should not raise exception
        result = await storage_service.delete_temp_file("temp/test.pdf")

        assert result is False

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
    async def test_delete_logs_warnings_on_error(self, storage_service, caplog):
        """Test that errors are logged as warnings, not errors."""
        import logging

        storage_service.s3_client.delete_object = Mock(
            side_effect=Exception("S3 failure")
        )

        with caplog.at_level(logging.WARNING):
            result = await storage_service.delete_temp_file("temp/test.pdf")

        assert result is False
        assert any("Failed to delete temp file" in record.message or
                   "Unexpected error deleting temp file" in record.message
                   for record in caplog.records)


class TestRateLimitKeyCollision:
    """Tests for Bug #13: Rate limit key collision prevention."""

    @pytest.fixture
    def redis_client(self):
        """Create mock Redis client."""
        mock_redis = MagicMock()
        # Default pipeline behavior - pipeline is synchronous, only execute() is async
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[None, 0])  # [zremrangebyscore result, zcard result]
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)
        mock_redis.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        return mock_redis

    @pytest.fixture
    def rate_limit_service(self, redis_client):
        """Create rate limit service with mocked Redis."""
        return RateLimitService(redis_client)

    @pytest.mark.asyncio
    async def test_unique_members_for_concurrent_requests(self, redis_client, rate_limit_service):
        """Test that concurrent requests at same microsecond have unique members."""
        captured_members = []

        async def capture_zadd(key, mapping):
            """Capture the member keys used in zadd calls."""
            for member in mapping.keys():
                captured_members.append(member)
            return 1

        redis_client.zadd.side_effect = capture_zadd

        # Simulate multiple concurrent requests
        for _ in range(5):
            allowed, _ = await rate_limit_service.check_submit_rate_limit("192.168.1.1")
            assert allowed is True

        # All members should be unique
        assert len(captured_members) == len(set(captured_members))

        # Members should contain UUID suffix
        for member in captured_members:
            assert "-" in member  # Format: "timestamp-uuid"
            parts = member.split("-")
            assert len(parts) >= 2  # At minimum: timestamp and first uuid part

    @pytest.mark.asyncio
    async def test_member_format_includes_uuid(self, redis_client, rate_limit_service):
        """Test that members have UUID suffix to prevent collisions."""
        captured_member = None

        async def capture_zadd(key, mapping):
            nonlocal captured_member
            captured_member = list(mapping.keys())[0]
            return 1

        redis_client.zadd.side_effect = capture_zadd

        allowed, _ = await rate_limit_service.check_submit_rate_limit("192.168.1.1")
        assert allowed is True
        assert captured_member is not None

        # Verify format: timestamp-uuid (uuid is 8 hex chars)
        parts = captured_member.split("-", 1)
        assert len(parts) == 2
        timestamp_part, uuid_part = parts

        # Timestamp part should be a float-like string
        assert float(timestamp_part) > 0

        # UUID part should be hexadecimal
        assert all(c in "0123456789abcdef" for c in uuid_part[:8])

    @pytest.mark.asyncio
    async def test_score_remains_timestamp(self, redis_client, rate_limit_service):
        """Test that score value is still the timestamp for proper ordering."""
        import time

        captured_score = None

        async def capture_zadd(key, mapping):
            nonlocal captured_score
            # mapping is {member: score}
            captured_score = list(mapping.values())[0]
            return 1

        redis_client.zadd.side_effect = capture_zadd

        before_time = time.time()
        allowed, _ = await rate_limit_service.check_submit_rate_limit("192.168.1.1")
        after_time = time.time()

        assert allowed is True
        assert captured_score is not None
        assert before_time <= captured_score <= after_time

    @pytest.mark.asyncio
    async def test_cleanup_still_works_with_uuid_members(self, redis_client, rate_limit_service):
        """Test that window cleanup still functions with UUID-based members."""
        # Mock pipeline to simulate existing entries
        # Pipeline methods (zremrangebyscore, zcard) are sync, only execute() is async
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[5, 3])  # Removed 5 old, 3 remaining
        redis_client.pipeline.return_value = mock_pipeline

        allowed, _ = await rate_limit_service.check_submit_rate_limit("192.168.1.1")

        # Should have called zremrangebyscore to clean old entries
        assert mock_pipeline.zremrangebyscore.called


class TestIntegrationErrorHandling:
    """Integration tests for error handling across multiple bugs."""

    @pytest.mark.asyncio
    async def test_storage_service_resilience(self):
        """Test that storage service handles multiple error types gracefully."""
        mock_s3 = Mock()
        storage_service = StorageService(
            s3_client=mock_s3,
            temp_bucket="test-bucket",
            results_bucket="results-bucket"
        )

        # Test 1: File seek error
        bad_file = Mock()
        bad_file.seek.side_effect = OSError("Seek failed")
        upload = Mock(spec=UploadFile)
        upload.filename = "test.pdf"
        upload.file = bad_file
        upload.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(upload)
        assert exc.value.status_code == 400

        # Test 2: Best-effort deletion (should not raise)
        mock_s3.delete_object = Mock(side_effect=Exception("S3 error"))
        result = await storage_service.delete_temp_file("temp/file.pdf")
        assert result is False  # Returns False, doesn't crash

    @pytest.mark.asyncio
    async def test_rate_limit_collision_resistance(self):
        """Test that rate limiting handles high concurrency without collisions."""
        # Track all zadd calls to verify uniqueness
        members_used = []

        async def track_zadd(key, mapping):
            """Async function to track zadd calls."""
            members_used.extend(mapping.keys())
            return len(mapping)

        # Create properly configured mocks
        mock_redis = MagicMock()

        # Create a single pipeline instance to reuse (avoids AsyncMock proliferation)
        mock_pipeline = MagicMock()
        mock_pipeline.zremrangebyscore = MagicMock(return_value=None)
        mock_pipeline.zcard = MagicMock(return_value=None)
        mock_pipeline.execute = AsyncMock(return_value=[None, 0])  # Always under limit

        # Return same pipeline instance every time
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        # Configure async methods with proper awaitable returns
        mock_redis.zadd = AsyncMock(side_effect=track_zadd)
        mock_redis.expire = AsyncMock(return_value=True)
        mock_redis.zrange = AsyncMock(return_value=[])

        service = RateLimitService(mock_redis)

        # Simulate 100 concurrent requests
        for _ in range(100):
            await service.check_submit_rate_limit("192.168.1.1")

        # All members should be unique despite high concurrency
        # Note: check_submit_rate_limit makes 2 calls (per-IP and global)
        assert len(members_used) == len(set(members_used)), \
            f"Found {len(members_used)} total members but {len(set(members_used))} unique"
