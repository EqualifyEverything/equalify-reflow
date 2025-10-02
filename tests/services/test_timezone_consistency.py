"""Comprehensive test suite for timezone consistency across the codebase.

Tests all datetime operations to ensure:
1. All datetimes are timezone-aware (UTC)
2. Datetime serialization preserves timezone information
3. Datetime parsing creates timezone-aware objects
4. Datetime comparisons work correctly across services
5. Edge cases are handled (DST boundaries, UTC midnight, etc.)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.services.approval_service import ApprovalService


class TestDatetimeCreation:
    """Test that all datetime creation uses timezone-aware UTC."""

    @pytest.mark.asyncio
    async def test_job_service_creates_timezone_aware_datetimes(self, mock_redis):
        """Test JobService.create_job stores timezone-aware timestamps."""
        job_service = JobService(mock_redis)

        await job_service.create_job(
            job_id="test-job-123",
            s3_key="temp/test.pdf",
            status="pii_scanning"
        )

        # Get the stored data
        call_args = mock_redis.hset.call_args
        stored_data = call_args.kwargs['mapping']

        # Verify created_at is timezone-aware ISO format
        created_at_str = stored_data['created_at']
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

        # Should be timezone-aware
        assert created_at.tzinfo is not None
        assert created_at.tzinfo == timezone.utc or created_at.tzinfo.utcoffset(None) == timedelta(0)

        # Should be ISO format with timezone info
        assert "+" in created_at_str or created_at_str.endswith("Z")

    @pytest.mark.asyncio
    async def test_job_service_update_uses_timezone_aware_datetimes(self, mock_redis):
        """Test JobService.update_job_status stores timezone-aware timestamps."""
        job_service = JobService(mock_redis)

        await job_service.update_job_status(
            job_id="test-job-123",
            status="processing"
        )

        # Get the stored data
        call_args = mock_redis.hset.call_args
        stored_data = call_args.kwargs['mapping']

        # Verify updated_at is timezone-aware
        updated_at_str = stored_data['updated_at']
        updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))

        assert updated_at.tzinfo is not None
        assert updated_at.tzinfo == timezone.utc or updated_at.tzinfo.utcoffset(None) == timedelta(0)

    @pytest.mark.asyncio
    async def test_queue_service_creates_timezone_aware_datetimes(self, mock_redis):
        """Test QueueService.queue_pii_job stores timezone-aware timestamps."""
        queue_service = QueueService(mock_redis)

        await queue_service.queue_pii_job(
            job_id="test-job-123",
            s3_key="temp/test.pdf"
        )

        # Get the queued payload
        call_args = mock_redis.lpush.call_args
        import json
        payload = json.loads(call_args[0][1])

        # Verify created_at is timezone-aware
        created_at_str = payload['created_at']
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

        assert created_at.tzinfo is not None
        assert created_at.tzinfo == timezone.utc or created_at.tzinfo.utcoffset(None) == timedelta(0)


class TestDatetimeParsing:
    """Test that all datetime parsing creates timezone-aware objects."""

    @pytest.mark.asyncio
    async def test_approval_service_parses_timezone_aware_datetimes(self, mock_redis, mock_s3):
        """Test ApprovalService.validate_approval_token parses timezone-aware expiration."""
        job_service = JobService(mock_redis)
        queue_service = QueueService(mock_redis)
        approval_service = ApprovalService(
            redis_client=mock_redis,
            s3_client=mock_s3,
            job_service=job_service,
            queue_service=queue_service
        )

        # Mock job data with ISO format timestamp
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)
        mock_job_data = {
            "job_id": "test-job-123",
            "approval_token": "test-token-123",
            "approval_expires_at": future_time.isoformat(),
            "status": "awaiting_approval",
            "s3_key": "temp/test.pdf"
        }

        # Mock Redis keys and job retrieval
        mock_redis.keys.return_value = ["eq-pdf:job:test-job-123"]
        job_service.get_job = AsyncMock(return_value=mock_job_data)

        # Validate token
        result = await approval_service.validate_approval_token("test-token-123")

        # Should successfully validate (not expired)
        assert result is not None
        assert result["job_id"] == "test-job-123"

    @pytest.mark.asyncio
    async def test_approval_service_handles_z_suffix_in_timestamps(self, mock_redis, mock_s3):
        """Test ApprovalService handles Z suffix in ISO timestamps."""
        job_service = JobService(mock_redis)
        queue_service = QueueService(mock_redis)
        approval_service = ApprovalService(
            redis_client=mock_redis,
            s3_client=mock_s3,
            job_service=job_service,
            queue_service=queue_service
        )

        # Mock job data with Z suffix timestamp
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)
        mock_job_data = {
            "job_id": "test-job-123",
            "approval_token": "test-token-123",
            "approval_expires_at": future_time.isoformat().replace("+00:00", "Z"),
            "status": "awaiting_approval",
            "s3_key": "temp/test.pdf"
        }

        mock_redis.keys.return_value = ["eq-pdf:job:test-job-123"]
        job_service.get_job = AsyncMock(return_value=mock_job_data)

        result = await approval_service.validate_approval_token("test-token-123")

        # Should handle Z suffix correctly
        assert result is not None

    @pytest.mark.asyncio
    async def test_approval_service_rejects_expired_tokens(self, mock_redis, mock_s3):
        """Test ApprovalService correctly identifies expired tokens."""
        job_service = JobService(mock_redis)
        queue_service = QueueService(mock_redis)
        approval_service = ApprovalService(
            redis_client=mock_redis,
            s3_client=mock_s3,
            job_service=job_service,
            queue_service=queue_service
        )

        # Mock job data with expired timestamp
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_job_data = {
            "job_id": "test-job-123",
            "approval_token": "test-token-123",
            "approval_expires_at": past_time.isoformat(),
            "status": "awaiting_approval",
            "s3_key": "temp/test.pdf"
        }

        mock_redis.keys.return_value = ["eq-pdf:job:test-job-123"]
        job_service.get_job = AsyncMock(return_value=mock_job_data)

        result = await approval_service.validate_approval_token("test-token-123")

        # Should return None for expired token
        assert result is None


class TestDatetimeComparisons:
    """Test that datetime comparisons work correctly across services."""

    @pytest.mark.asyncio
    async def test_approval_expiration_comparison_with_utc_now(self, mock_redis, mock_s3):
        """Test approval expiration comparison uses consistent timezone-aware datetimes."""
        job_service = JobService(mock_redis)
        queue_service = QueueService(mock_redis)
        approval_service = ApprovalService(
            redis_client=mock_redis,
            s3_client=mock_s3,
            job_service=job_service,
            queue_service=queue_service
        )

        # Create datetimes using the same method as the code
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=1)

        # Mock job with future expiration
        mock_job_data = {
            "job_id": "test-job-123",
            "approval_token": "test-token-123",
            "approval_expires_at": future.isoformat(),
            "status": "awaiting_approval",
            "s3_key": "temp/test.pdf"
        }

        mock_redis.keys.return_value = ["eq-pdf:job:test-job-123"]
        job_service.get_job = AsyncMock(return_value=mock_job_data)

        result = await approval_service.validate_approval_token("test-token-123")

        # Should be valid (future expiration)
        assert result is not None

    @pytest.mark.asyncio
    async def test_timeout_tracking_comparison(self, mock_redis):
        """Test timeout tracking uses consistent timestamp comparisons."""
        queue_service = QueueService(mock_redis)

        # Add job to timeout tracking with future expiration
        future_time = datetime.now(timezone.utc) + timedelta(hours=2)
        await queue_service.add_to_timeout_tracking(
            job_id="test-job-123",
            expires_at=future_time,
            timeout_type="approval"
        )

        # Mock Redis response for get_expired_timeouts
        mock_redis.zrangebyscore.return_value = []  # No expired jobs yet

        # Get expired timeouts (should be empty since job expires in future)
        expired = await queue_service.get_expired_timeouts("approval")

        assert len(expired) == 0  # No expired jobs


class TestTimezoneEdgeCases:
    """Test edge cases for timezone handling."""

    @pytest.mark.asyncio
    async def test_utc_midnight_boundary(self, mock_redis):
        """Test datetime handling at UTC midnight boundary."""
        job_service = JobService(mock_redis)

        # Create job at UTC midnight
        with patch('src.services.job_service.datetime') as mock_datetime:
            midnight = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = midnight
            mock_datetime.fromisoformat = datetime.fromisoformat

            await job_service.create_job(
                job_id="midnight-job",
                s3_key="temp/midnight.pdf"
            )

        call_args = mock_redis.hset.call_args
        stored_data = call_args.kwargs['mapping']
        created_at_str = stored_data['created_at']

        # Should handle midnight correctly
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        assert created_at.tzinfo is not None
        assert created_at.hour == 0
        assert created_at.minute == 0

    @pytest.mark.asyncio
    async def test_dst_transition_handling(self, mock_redis):
        """Test datetime handling during DST transitions (UTC is unaffected)."""
        job_service = JobService(mock_redis)

        # DST transition date (March 2024) - UTC is always consistent
        dst_date = datetime(2024, 3, 10, 2, 0, 0, tzinfo=timezone.utc)

        with patch('src.services.job_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = dst_date
            mock_datetime.fromisoformat = datetime.fromisoformat

            await job_service.create_job(
                job_id="dst-job",
                s3_key="temp/dst.pdf"
            )

        call_args = mock_redis.hset.call_args
        stored_data = call_args.kwargs['mapping']
        created_at_str = stored_data['created_at']

        # UTC should be unaffected by DST
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        assert created_at.tzinfo is not None
        assert created_at == dst_date

    @pytest.mark.asyncio
    async def test_year_boundary_handling(self, mock_redis):
        """Test datetime handling at year boundaries."""
        job_service = JobService(mock_redis)

        # New Year's Eve at 23:59:59 UTC
        year_end = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        with patch('src.services.job_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = year_end
            mock_datetime.fromisoformat = datetime.fromisoformat

            await job_service.create_job(
                job_id="year-end-job",
                s3_key="temp/year-end.pdf"
            )

        call_args = mock_redis.hset.call_args
        stored_data = call_args.kwargs['mapping']
        created_at_str = stored_data['created_at']

        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        assert created_at.tzinfo is not None
        assert created_at.year == 2024
        assert created_at.month == 12
        assert created_at.day == 31


class TestJobLifecycleTimezoneConsistency:
    """Test timezone consistency throughout entire job lifecycle."""

    @pytest.mark.asyncio
    async def test_job_creation_to_retrieval_timezone_consistency(self, mock_redis):
        """Test that job timestamps remain timezone-aware from creation to retrieval."""
        job_service = JobService(mock_redis)

        # Create job
        await job_service.create_job(
            job_id="lifecycle-job",
            s3_key="temp/lifecycle.pdf",
            status="pii_scanning"
        )

        # Mock retrieval
        create_call_args = mock_redis.hset.call_args
        stored_data = create_call_args.kwargs['mapping']

        mock_redis.hgetall.return_value = stored_data

        # Retrieve job
        job_data = await job_service.get_job("lifecycle-job")

        # Verify timezone awareness is preserved
        created_at_str = job_data['created_at']
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

        assert created_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_job_update_preserves_timezone_awareness(self, mock_redis):
        """Test that job updates maintain timezone-aware timestamps."""
        job_service = JobService(mock_redis)

        # Create job
        await job_service.create_job(
            job_id="update-job",
            s3_key="temp/update.pdf"
        )

        # Update job
        await job_service.update_job_status(
            job_id="update-job",
            status="processing"
        )

        # Verify updated_at is timezone-aware
        update_call_args = mock_redis.hset.call_args
        update_data = update_call_args.kwargs['mapping']

        updated_at_str = update_data['updated_at']
        updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))

        assert updated_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_approval_expiration_workflow_timezone_consistency(self, mock_redis, mock_s3):
        """Test approval expiration workflow maintains timezone consistency."""
        job_service = JobService(mock_redis)
        queue_service = QueueService(mock_redis)
        approval_service = ApprovalService(
            redis_client=mock_redis,
            s3_client=mock_s3,
            job_service=job_service,
            queue_service=queue_service
        )

        # Create approval with 4-hour expiration
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=4)

        mock_job_data = {
            "job_id": "approval-job",
            "approval_token": "token-123",
            "approval_expires_at": expires_at.isoformat(),
            "status": "awaiting_approval",
            "s3_key": "temp/approval.pdf"
        }

        mock_redis.keys.return_value = ["eq-pdf:job:approval-job"]
        job_service.get_job = AsyncMock(return_value=mock_job_data)

        # Validate token (should be valid)
        result = await approval_service.validate_approval_token("token-123")
        assert result is not None

        # Simulate time passing (5 hours)
        future_now = now + timedelta(hours=5)
        mock_job_data["approval_expires_at"] = expires_at.isoformat()

        with patch('src.services.approval_service.datetime') as mock_datetime:
            mock_datetime.now.return_value = future_now
            mock_datetime.fromisoformat = datetime.fromisoformat

            # Validate token again (should be expired)
            result = await approval_service.validate_approval_token("token-123")
            assert result is None  # Expired


# Pytest fixtures
@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    redis = AsyncMock()
    redis.hset = AsyncMock()
    redis.hgetall = AsyncMock()
    redis.lpush = AsyncMock()
    redis.keys = AsyncMock()
    redis.zadd = AsyncMock()
    redis.zrangebyscore = AsyncMock()
    redis.zrem = AsyncMock()
    return redis


@pytest.fixture
def mock_s3():
    """Mock S3 client for testing."""
    return AsyncMock()
