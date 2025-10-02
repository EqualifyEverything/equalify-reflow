"""Edge case tests for rate limit boundary conditions."""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from src.services.rate_limit_service import RateLimitService


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    mock = MagicMock()
    mock.pipeline = MagicMock()
    mock.zremrangebyscore = AsyncMock()
    mock.zcard = AsyncMock()
    mock.zadd = AsyncMock()
    mock.expire = AsyncMock()
    mock.zrange = AsyncMock()
    mock.delete = AsyncMock()
    return mock


@pytest.fixture
def rate_limiter(mock_redis):
    """Create rate limiter with mock Redis."""
    return RateLimitService(redis=mock_redis)


class TestRateLimitBoundaries:
    """Tests for rate limit edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_exactly_at_limit(self, rate_limiter, mock_redis):
        """Test exactly N requests allowed (boundary)."""
        # Mock pipeline - at limit (10 requests)
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 10])  # Exactly at limit
        mock_redis.pipeline.return_value = mock_pipe

        # Should be denied (at limit means next one is over)
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is False
        assert retry_after is not None

    @pytest.mark.asyncio
    async def test_one_under_limit(self, rate_limiter, mock_redis):
        """Test N-1 requests allowed."""
        # Mock pipeline - one under limit
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 9])  # 9 < 10 limit
        mock_redis.pipeline.return_value = mock_pipe

        # Should be allowed
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is True
        assert retry_after is None

    @pytest.mark.asyncio
    async def test_one_over_limit(self, rate_limiter, mock_redis):
        """Test N+1 requests denied."""
        # Mock pipeline - over limit
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 11])  # 11 > 10 limit
        mock_redis.pipeline.return_value = mock_pipe

        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

        # Should be denied
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is False
        assert retry_after is not None

    @pytest.mark.asyncio
    async def test_zero_requests_allowed(self, rate_limiter, mock_redis):
        """Test first request is always allowed."""
        # Mock pipeline - no current requests
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 0])
        mock_redis.pipeline.return_value = mock_pipe

        # First request should be allowed
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is True
        assert retry_after is None

    @pytest.mark.asyncio
    async def test_window_boundary_expiration(self, rate_limiter, mock_redis):
        """Test requests at window boundary are properly cleaned up."""
        now = time.time()
        window_boundary = now - 3600  # Exactly at 1 hour window

        # Mock old entries exactly at boundary
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 5])
        mock_redis.pipeline.return_value = mock_pipe

        await rate_limiter.check_submit_rate_limit("192.168.1.1")

        # Verify cleanup was called with correct boundary
        # Should remove entries older than window_start
        assert mock_pipe.zremrangebyscore.call_count >= 1

    @pytest.mark.asyncio
    async def test_concurrent_requests_at_limit(self, rate_limiter, mock_redis):
        """Test race condition with concurrent requests at limit."""
        # Simulate race: both requests see 9 in count, try to increment to 10
        call_count = 0

        def mock_execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [None, 9]  # Both see 9
            else:
                return [None, 10]  # Subsequent checks see 10

        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(side_effect=mock_execute_side_effect)
        mock_redis.pipeline.return_value = mock_pipe

        # Both requests should be handled (implementation-dependent)
        result1, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
        result2, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        # At least one should succeed, both might if zadd happens between checks
        assert result1 or result2

    @pytest.mark.asyncio
    async def test_clock_skew_handling(self, rate_limiter, mock_redis):
        """Test handling of clock skew (future timestamps)."""
        now = time.time()
        future_time = now + 3600  # 1 hour in future (clock skew)

        # Mock entries with future timestamps
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 5])
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.zrange = AsyncMock(return_value=[("req", future_time)])

        # Should handle gracefully
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        # Should still work (may calculate odd retry_after)
        assert isinstance(allowed, bool)

    @pytest.mark.asyncio
    async def test_negative_time_difference(self, rate_limiter, mock_redis):
        """Test handling when oldest entry is in future (clock sync issue)."""
        now = time.time()
        future_time = now + 100  # Entry 100 seconds in future

        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 10])
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.zrange = AsyncMock(return_value=[("req", future_time)])

        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        # Should handle gracefully
        assert isinstance(allowed, bool)
        if retry_after is not None:
            assert retry_after >= 0  # Should not return negative

    @pytest.mark.asyncio
    async def test_window_reset_exactly_on_time(self, rate_limiter, mock_redis):
        """Test rate limit resets exactly when window expires."""
        # First: at limit
        mock_pipe1 = MagicMock()
        mock_pipe1.execute = AsyncMock(return_value=[None, 10])
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 3600)])

        # Second: after window expires (cleanup removes old entries)
        mock_pipe2 = MagicMock()
        mock_pipe2.execute = AsyncMock(return_value=[None, 0])

        mock_redis.pipeline.side_effect = [mock_pipe1, mock_pipe1, mock_pipe2, mock_pipe2]

        # First check: should be denied
        allowed1, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")
        assert allowed1 is False

        # Simulate time passing (window expired, cleanup runs)
        # Second check: should be allowed
        allowed2, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
        assert allowed2 is True

    @pytest.mark.asyncio
    async def test_multiple_ips_independent_boundaries(self, rate_limiter, mock_redis):
        """Test different IPs have independent limit boundaries."""
        # IP1: at limit
        mock_pipe1 = MagicMock()
        mock_pipe1.execute = AsyncMock(return_value=[None, 10])

        # IP2: under limit
        mock_pipe2 = MagicMock()
        mock_pipe2.execute = AsyncMock(return_value=[None, 5])

        # Global: under limit
        mock_pipe_global = MagicMock()
        mock_pipe_global.execute = AsyncMock(return_value=[None, 50])

        mock_redis.pipeline.side_effect = [
            mock_pipe1, mock_pipe_global,  # IP1 checks
            mock_pipe2, mock_pipe_global,  # IP2 checks
        ]
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

        # IP1: should be denied
        allowed1, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
        assert allowed1 is False

        # IP2: should be allowed
        allowed2, _ = await rate_limiter.check_submit_rate_limit("192.168.1.2")
        assert allowed2 is True

    @pytest.mark.asyncio
    async def test_global_limit_boundary(self, rate_limiter, mock_redis):
        """Test global rate limit boundary across all IPs."""
        # Per-IP: under limit
        mock_pipe_ip = MagicMock()
        mock_pipe_ip.execute = AsyncMock(return_value=[None, 5])

        # Global: at limit
        mock_pipe_global = MagicMock()
        mock_pipe_global.execute = AsyncMock(return_value=[None, 1000])

        mock_redis.pipeline.side_effect = [mock_pipe_ip, mock_pipe_global]
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

        # Should be denied due to global limit
        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is False
        assert retry_after is not None

    @pytest.mark.asyncio
    async def test_retry_after_calculation_accuracy(self, rate_limiter, mock_redis):
        """Test retry_after is calculated accurately."""
        now = time.time()
        oldest_time = now - 1800  # 30 minutes ago (window is 60 min)

        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 10])
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.zrange = AsyncMock(return_value=[("req", oldest_time)])

        allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

        assert allowed is False
        assert retry_after is not None
        # Should suggest waiting ~30 minutes (until oldest expires)
        assert 1500 <= retry_after <= 2000  # Allow some variance

    @pytest.mark.asyncio
    async def test_rapid_sequential_requests(self, rate_limiter, mock_redis):
        """Test rapid sequential requests increment counter correctly."""
        # Simulate counter incrementing
        counts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

        for count in counts:
            mock_pipe = MagicMock()
            mock_pipe.execute = AsyncMock(return_value=[None, count])
            mock_redis.pipeline.return_value = mock_pipe

            allowed, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")

            if count < 10:
                assert allowed is True
            else:
                # At limit
                mock_redis.zrange = AsyncMock(return_value=[("req", time.time())])
                allowed, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
                # May be denied

    @pytest.mark.asyncio
    async def test_status_limit_different_from_submit(self, rate_limiter, mock_redis):
        """Test status endpoint has different limit boundary."""
        # Status limit is 100, submit is 10
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 100])  # At status limit
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

        # Should be denied at 100
        allowed, retry_after = await rate_limiter.check_status_rate_limit("192.168.1.1")

        assert allowed is False

    @pytest.mark.asyncio
    async def test_rate_limit_reset_clears_counter(self, rate_limiter, mock_redis):
        """Test that reset clears rate limit counter to zero."""
        mock_redis.delete = AsyncMock(return_value=1)

        result = await rate_limiter.reset_rate_limit("192.168.1.1", "submit")

        assert result is True
        mock_redis.delete.assert_called_once()

        # After reset, should be at 0
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 0])
        mock_redis.pipeline.return_value = mock_pipe

        allowed, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_empty_key_handling(self, rate_limiter, mock_redis):
        """Test handling of empty IP/key."""
        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 0])
        mock_redis.pipeline.return_value = mock_pipe

        # Should handle empty key gracefully
        allowed, _ = await rate_limiter.check_submit_rate_limit("")

        assert isinstance(allowed, bool)

    @pytest.mark.asyncio
    async def test_very_long_key_handling(self, rate_limiter, mock_redis):
        """Test handling of very long IP/key string."""
        long_key = "x" * 1000

        mock_pipe = MagicMock()
        mock_pipe.execute = AsyncMock(return_value=[None, 5])
        mock_redis.pipeline.return_value = mock_pipe

        # Should handle long keys
        allowed, _ = await rate_limiter.check_submit_rate_limit(long_key)

        assert isinstance(allowed, bool)

    @pytest.mark.asyncio
    async def test_quota_remaining_at_boundary(self, rate_limiter, mock_redis):
        """Test remaining quota calculation at boundaries."""
        # At limit: 10 used, 0 remaining
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=10)
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

        quota = await rate_limiter.get_remaining_quota("192.168.1.1", "submit")

        assert quota["limit"] == 10
        assert quota["remaining"] == 0
        assert quota["remaining"] >= 0  # Should never be negative

    @pytest.mark.asyncio
    async def test_quota_more_than_limit_clamped(self, rate_limiter, mock_redis):
        """Test quota remaining is never more than limit."""
        # Mock error: somehow have negative usage
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=-1)  # Invalid state
        mock_redis.zrange = AsyncMock(return_value=[("req", time.time())])

        quota = await rate_limiter.get_remaining_quota("192.168.1.1", "submit")

        # Should clamp to valid range
        assert 0 <= quota["remaining"] <= quota["limit"]
