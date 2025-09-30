"""Tests for rate limit service."""

import pytest
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


@pytest.mark.asyncio
async def test_check_submit_rate_limit_allowed(rate_limiter, mock_redis):
    """Test submission rate limit when under limit."""
    # Mock pipeline for checking rate limit
    mock_pipe = MagicMock()
    mock_pipe.zremrangebyscore = MagicMock()
    mock_pipe.zcard = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[None, 5])  # 5 current requests
    mock_redis.pipeline.return_value = mock_pipe

    # Test
    allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

    # Should be allowed (5 < 10 limit)
    assert allowed is True
    assert retry_after is None

    # Should add entry to sorted set
    mock_redis.zadd.assert_called()
    mock_redis.expire.assert_called()


@pytest.mark.asyncio
async def test_check_submit_rate_limit_exceeded(rate_limiter, mock_redis):
    """Test submission rate limit when limit exceeded."""
    # Mock pipeline - at limit
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[None, 10])  # 10 requests (at limit)
    mock_redis.pipeline.return_value = mock_pipe

    # Mock oldest entry for retry calculation
    now = time.time()
    mock_redis.zrange = AsyncMock(return_value=[("request_id", now - 3000)])

    # Test
    allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

    # Should be denied
    assert allowed is False
    assert retry_after is not None
    assert isinstance(retry_after, int)

    # Should NOT add entry
    mock_redis.zadd.assert_not_called()


@pytest.mark.asyncio
async def test_check_submit_global_limit(rate_limiter, mock_redis):
    """Test global submission rate limit."""
    # Mock per-IP check passes
    mock_pipe1 = MagicMock()
    mock_pipe1.execute = AsyncMock(return_value=[None, 5])  # Under IP limit

    # Mock global check fails
    mock_pipe2 = MagicMock()
    mock_pipe2.execute = AsyncMock(return_value=[None, 1000])  # At global limit

    mock_redis.pipeline.side_effect = [mock_pipe1, mock_pipe2]
    mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 3000)])

    # Test
    allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

    # Should be denied due to global limit
    assert allowed is False
    assert retry_after is not None


@pytest.mark.asyncio
async def test_check_status_rate_limit(rate_limiter, mock_redis):
    """Test status check rate limit."""
    # Mock under limit
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[None, 50])  # 50 < 100 limit
    mock_redis.pipeline.return_value = mock_pipe

    # Test
    allowed, retry_after = await rate_limiter.check_status_rate_limit("192.168.1.1")

    # Should be allowed
    assert allowed is True
    assert retry_after is None


@pytest.mark.asyncio
async def test_get_remaining_quota_submit(rate_limiter, mock_redis):
    """Test getting remaining quota for submissions."""
    # Mock current usage
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=7)  # 7 requests used
    mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

    # Test
    quota = await rate_limiter.get_remaining_quota("192.168.1.1", "submit")

    # Verify quota info
    assert quota["limit"] == 10
    assert quota["remaining"] == 3  # 10 - 7
    assert "reset_at" in quota
    assert quota["window_seconds"] == 3600


@pytest.mark.asyncio
async def test_get_remaining_quota_status(rate_limiter, mock_redis):
    """Test getting remaining quota for status checks."""
    # Mock current usage
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.zcard = AsyncMock(return_value=20)  # 20 requests used
    mock_redis.zrange = AsyncMock(return_value=[("req", time.time() - 1000)])

    # Test
    quota = await rate_limiter.get_remaining_quota("192.168.1.1", "status")

    # Verify quota info
    assert quota["limit"] == 100
    assert quota["remaining"] == 80  # 100 - 20
    assert "reset_at" in quota
    assert quota["window_seconds"] == 3600


@pytest.mark.asyncio
async def test_reset_rate_limit(rate_limiter, mock_redis):
    """Test resetting rate limit for a client."""
    # Test
    result = await rate_limiter.reset_rate_limit("192.168.1.1", "submit")

    # Should delete the key
    assert result is True
    mock_redis.delete.assert_called_once_with("eq-pdf:ratelimit:submit:ip:192.168.1.1")


@pytest.mark.asyncio
async def test_rate_limit_fails_open_on_redis_error(rate_limiter, mock_redis):
    """Test rate limiter fails open when Redis is unavailable."""
    # Mock Redis error
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(side_effect=Exception("Redis connection failed"))
    mock_redis.pipeline.return_value = mock_pipe

    # Test - should fail open (allow request)
    allowed, retry_after = await rate_limiter.check_submit_rate_limit("192.168.1.1")

    assert allowed is True  # Fails open
    assert retry_after is None


@pytest.mark.asyncio
async def test_sliding_window_cleanup(rate_limiter, mock_redis):
    """Test that old entries outside window are removed."""
    # Mock pipeline
    mock_pipe = MagicMock()
    mock_pipe.zremrangebyscore = MagicMock()
    mock_pipe.zcard = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[None, 3])
    mock_redis.pipeline.return_value = mock_pipe

    # Test
    await rate_limiter.check_submit_rate_limit("192.168.1.1")

    # Verify old entries were removed (called twice: per-IP + global)
    assert mock_pipe.zremrangebyscore.call_count == 2


@pytest.mark.asyncio
async def test_different_ips_independent_limits(rate_limiter, mock_redis):
    """Test that different IPs have independent rate limits."""
    # Mock for first IP
    mock_pipe1 = MagicMock()
    mock_pipe1.execute = AsyncMock(return_value=[None, 9])  # Near limit

    # Mock for second IP
    mock_pipe2 = MagicMock()
    mock_pipe2.execute = AsyncMock(return_value=[None, 1])  # Low usage

    # Mock for global (both checks)
    mock_pipe_global = MagicMock()
    mock_pipe_global.execute = AsyncMock(return_value=[None, 50])

    mock_redis.pipeline.side_effect = [
        mock_pipe1, mock_pipe_global,  # First IP checks
        mock_pipe2, mock_pipe_global   # Second IP checks
    ]

    # Test both IPs
    allowed1, _ = await rate_limiter.check_submit_rate_limit("192.168.1.1")
    allowed2, _ = await rate_limiter.check_submit_rate_limit("192.168.1.2")

    # Both should be allowed (independent limits)
    assert allowed1 is True
    assert allowed2 is True
