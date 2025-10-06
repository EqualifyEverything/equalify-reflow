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
    def storage_service(self):
        """Create storage service with mocked S3 client."""
        mock_s3 = Mock()
        return StorageService(
            s3_client=mock_s3,
            temp_bucket=settings.s3_temp_bucket,
            results_bucket=settings.s3_results_bucket
        )

    @pytest.mark.asyncio
    async def test_file_seek_os_error(self, storage_service):
        """Test handling of OSError during file seek operation."""
        # Create a file object that raises OSError on seek
        mock_file = Mock()
        mock_file.seek.side_effect = OSError("Device not ready")

        upload_file = Mock(spec=UploadFile)
        upload_file.filename = "test.pdf"
        upload_file.file = mock_file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc_info:
            await storage_service.store_document(upload_file)

        assert exc_info.value.status_code == 400
        assert "Unable to read file" in exc_info.value.detail
        assert "Device not ready" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_file_seek_io_error(self, storage_service):
        """Test handling of IOError during file seek operation."""
        mock_file = Mock()
        mock_file.seek.side_effect = IOError("File handle closed")

        upload_file = Mock(spec=UploadFile)
        upload_file.filename = "test.pdf"
        upload_file.file = mock_file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc_info:
            await storage_service.store_document(upload_file)

        assert exc_info.value.status_code == 400
        assert "Unable to read file" in exc_info.value.detail

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
    async def test_file_tell_failure(self, storage_service):
        """Test handling of failure in tell() method."""
        mock_file = Mock()
        mock_file.seek.return_value = None
        mock_file.tell.side_effect = OSError("Position unavailable")

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
    def storage_service(self):
        """Create storage service with mocked S3 client."""
        mock_s3 = Mock()
        return StorageService(
            s3_client=mock_s3,
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
    async def test_delete_generic_error_returns_false(self, storage_service):
        """Test that generic exceptions return False instead of raising."""
        storage_service.s3_client.delete_object = Mock(
            side_effect=Exception("Unexpected S3 error")
        )

        # Should not raise exception
        result = await storage_service.delete_temp_file("temp/test.pdf")

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_network_error_returns_false(self, storage_service):
        """Test that network errors return False instead of raising."""
        storage_service.s3_client.delete_object = Mock(
            side_effect=ConnectionError("Network unreachable")
        )

        # Should not raise exception
        result = await storage_service.delete_temp_file("temp/test.pdf")

        assert result is False

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
        mock_redis = AsyncMock()
        # Pipeline methods are sync, only execute() is async
        mock_pipeline = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[None, 0])
        mock_redis.pipeline.return_value = mock_pipeline
        mock_redis.zadd = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        service = RateLimitService(mock_redis)

        # Simulate 100 concurrent requests
        members_used = []

        async def track_zadd(key, mapping):
            members_used.extend(mapping.keys())
            return len(mapping)

        mock_redis.zadd = track_zadd

        for _ in range(100):
            await service.check_submit_rate_limit("192.168.1.1")

        # All members should be unique despite high concurrency
        assert len(members_used) == len(set(members_used))
