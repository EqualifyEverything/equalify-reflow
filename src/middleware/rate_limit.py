"""Rate limiting middleware for FastAPI."""

import logging
from typing import Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..dependencies import get_rate_limit_service

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware for rate limiting API requests.

    Applies different rate limits based on endpoint:
    - POST /api/documents/submit: Per-IP + global limits
    - GET /api/documents/*/status: Per-IP limits
    - Other endpoints: No rate limiting
    """

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Check rate limits before processing request.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object (429 if rate limited, otherwise normal response)
        """
        # Skip rate limiting for health checks and docs
        if request.url.path in ["/health", "/health/ready", "/docs", "/redoc", "/openapi.json", "/"]:
            return await call_next(request)

        # Get client IP
        client_ip = self._get_client_ip(request)

        # Get rate limit service from app state
        rate_limiter = getattr(request.app.state, "rate_limiter", None)

        # If rate limiter not available, fail open
        if rate_limiter is None:
            logger.warning("Rate limiter not available in app state, allowing request")
            return await call_next(request)

        # Check rate limits based on endpoint
        if request.url.path == "/api/documents/submit" and request.method == "POST":
            # Submission endpoint - strict limits
            allowed, retry_after = await rate_limiter.check_submit_rate_limit(client_ip)

            if not allowed:
                return self._rate_limit_response(
                    request=request,
                    retry_after=retry_after,
                    limit_type="submission"
                )

        elif "/status" in request.url.path and request.method == "GET":
            # Status check endpoint - prevent polling storms
            allowed, retry_after = await rate_limiter.check_status_rate_limit(client_ip)

            if not allowed:
                return self._rate_limit_response(
                    request=request,
                    retry_after=retry_after,
                    limit_type="status_check"
                )

        # Get quota info for response headers
        try:
            if request.url.path == "/api/documents/submit":
                quota = await rate_limiter.get_remaining_quota(client_ip, "submit")
            elif "/status" in request.url.path:
                quota = await rate_limiter.get_remaining_quota(client_ip, "status")
            else:
                quota = None
        except Exception:
            quota = None

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        if quota:
            response.headers["X-RateLimit-Limit"] = str(quota["limit"])
            response.headers["X-RateLimit-Remaining"] = str(quota["remaining"])
            response.headers["X-RateLimit-Reset"] = str(quota["reset_at"])

        return response

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP from request.

        Handles X-Forwarded-For header for reverse proxy setups.

        Args:
            request: Incoming request

        Returns:
            Client IP address
        """
        # Check X-Forwarded-For header (for reverse proxies)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take first IP in chain (original client)
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP header (nginx)
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct connection IP
        if request.client:
            return request.client.host

        # Default if no IP available
        return "unknown"

    def _rate_limit_response(
        self,
        request: Request,
        retry_after: int,
        limit_type: str
    ) -> JSONResponse:
        """
        Create rate limit error response.

        Args:
            request: Original request
            retry_after: Seconds until limit resets
            limit_type: Type of limit that was exceeded

        Returns:
            429 Too Many Requests response
        """
        logger.warning(
            f"Rate limit exceeded: {limit_type} for {self._get_client_ip(request)} "
            f"on {request.url.path}"
        )

        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "detail": f"Rate limit exceeded for {limit_type}",
                "retry_after": retry_after,
                "limit_type": limit_type
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining": "0"
            }
        )
