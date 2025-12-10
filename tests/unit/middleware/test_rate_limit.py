"""Unit tests for rate limiting middleware.

Tests focus on critical behavior:
1. Fail-open on Redis errors (availability)
2. Blocking after threshold exceeded (security/cost control)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request, Response
from src.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
def mock_app() -> MagicMock:
    """Create mock FastAPI app."""
    return MagicMock()


@pytest.fixture
def middleware(mock_app: MagicMock) -> RateLimitMiddleware:
    """Create rate limit middleware instance."""
    return RateLimitMiddleware(mock_app)


def create_mock_request(
    path: str,
    method: str = "POST",
    client_host: str = "192.168.1.100",
    rate_limiter: AsyncMock | None = None,
) -> MagicMock:
    """Create mock request with optional rate limiter in app state."""
    request = MagicMock(spec=Request)
    request.url.path = path
    request.method = method
    request.client = MagicMock()
    request.client.host = client_host
    request.headers = MagicMock()
    request.headers.get = MagicMock(return_value=None)

    # Set up app state with rate limiter
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.app.state.rate_limiter = rate_limiter

    return request


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_fails_open_on_redis_error(middleware: RateLimitMiddleware) -> None:
    """If Redis/rate limiter fails, requests should still succeed (fail open).

    Catches: All users blocked when Redis is unavailable.
    This is critical for availability - rate limiting should not take down the service.
    """
    # Setup: rate limiter raises exception (simulating Redis failure)
    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.check_submit_rate_limit = AsyncMock(
        side_effect=Exception("Redis connection failed")
    )

    request = create_mock_request(
        path="/api/documents/submit",
        method="POST",
        rate_limiter=mock_rate_limiter,
    )
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware.dispatch(request, call_next)

    # Assert: request should proceed despite rate limiter failure
    # The middleware catches exceptions and allows the request through
    assert call_next.called, "Request should proceed when rate limiter fails"
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_fails_open_when_not_configured(middleware: RateLimitMiddleware) -> None:
    """If rate limiter not in app state, requests should still succeed.

    Catches: Service startup issues where rate limiter isn't initialized.
    """
    # Setup: no rate limiter configured
    request = create_mock_request(
        path="/api/documents/submit",
        method="POST",
        rate_limiter=None,  # Not configured
    )
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware.dispatch(request, call_next)

    # Assert: request proceeds without rate limiting
    assert call_next.called
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_blocks_after_threshold(middleware: RateLimitMiddleware) -> None:
    """Request after limit exceeded returns 429 with retry_after.

    Catches: Rate limiting not enforcing limits, allowing abuse/cost overruns.
    """
    # Setup: rate limiter returns not allowed
    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.check_submit_rate_limit = AsyncMock(
        return_value=(False, 60)  # Not allowed, retry after 60 seconds
    )

    request = create_mock_request(
        path="/api/documents/submit",
        method="POST",
        rate_limiter=mock_rate_limiter,
    )
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware.dispatch(request, call_next)

    # Assert: 429 response with correct headers
    assert response.status_code == 429, "Should return 429 when rate limited"
    assert not call_next.called, "Request should not proceed when rate limited"
    assert response.headers.get("Retry-After") == "60"
    assert response.headers.get("X-RateLimit-Remaining") == "0"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_allows_request_under_threshold(middleware: RateLimitMiddleware) -> None:
    """Request under limit proceeds normally with rate limit headers.

    Catches: Rate limiter incorrectly blocking valid requests.
    """
    # Setup: rate limiter allows request
    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.check_submit_rate_limit = AsyncMock(
        return_value=(True, 0)  # Allowed
    )
    mock_rate_limiter.get_remaining_quota = AsyncMock(
        return_value={"limit": 25, "remaining": 24, "reset_at": 1234567890}
    )

    request = create_mock_request(
        path="/api/documents/submit",
        method="POST",
        rate_limiter=mock_rate_limiter,
    )

    # Create a real Response object that we can modify
    mock_response = Response(status_code=200)
    call_next = AsyncMock(return_value=mock_response)

    # Execute
    response = await middleware.dispatch(request, call_next)

    # Assert: request proceeds with rate limit headers
    assert call_next.called
    assert response.status_code == 200
    assert response.headers.get("X-RateLimit-Limit") == "25"
    assert response.headers.get("X-RateLimit-Remaining") == "24"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_skips_health_endpoints(middleware: RateLimitMiddleware) -> None:
    """Health check endpoints bypass rate limiting.

    Catches: Health checks being rate limited, breaking load balancer probes.
    """
    # Setup: rate limiter that would block (but shouldn't be called)
    mock_rate_limiter = AsyncMock()
    mock_rate_limiter.check_submit_rate_limit = AsyncMock(
        return_value=(False, 60)
    )

    request = create_mock_request(
        path="/health",
        method="GET",
        rate_limiter=mock_rate_limiter,
    )
    call_next = AsyncMock(return_value=Response(status_code=200))

    # Execute
    response = await middleware.dispatch(request, call_next)

    # Assert: health check bypasses rate limiting
    assert call_next.called
    assert response.status_code == 200
    mock_rate_limiter.check_submit_rate_limit.assert_not_called()
