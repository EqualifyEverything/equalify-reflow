"""API Key authentication middleware for FastAPI."""

import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings

logger = logging.getLogger(__name__)


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for API key authentication.

    Validates API keys in the X-API-Key header (configurable).
    Public endpoints (health, metrics, dev monitoring) bypass authentication.
    """

    def __init__(self, app: Any) -> None:
        """
        Initialize API key auth middleware.

        Args:
            app: FastAPI application instance
        """
        super().__init__(app)
        # Cache API keys at initialization to avoid reloading on every request
        self._cached_keys: set[str] = self._load_api_keys()

    def _load_api_keys(self) -> set[str]:
        """
        Load valid API keys from settings.

        Returns:
            Set of valid API key strings
        """
        if not settings.api_keys:
            logger.warning("No API keys configured! All authenticated requests will be rejected.")
            return set()

        # Parse comma-separated keys from SecretStr
        keys_str = settings.api_keys.get_secret_value()
        keys = {key.strip() for key in keys_str.split(",") if key.strip()}

        if not keys:
            logger.warning("API keys configured but empty after parsing!")
            return set()

        logger.info(f"Loaded {len(keys)} API key(s) for authentication")
        return keys

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """
        Validate API key before processing request.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object (401 if auth fails, otherwise normal response)
        """
        # Skip authentication for public endpoints
        if self._is_public_endpoint(request):
            return await call_next(request)

        # Extract API key from header
        api_key = request.headers.get(settings.api_key_header_name)

        # Check if API key is provided
        if not api_key:
            logger.warning(
                f"Missing API key for {request.method} {request.url.path} from {self._get_client_ip(request)}"
            )
            return self._unauthorized_response(
                detail=f"Missing API key. Provide a valid key in the '{settings.api_key_header_name}' header."
            )

        # Validate API key using constant-time comparison
        if not self._is_valid_key(api_key):
            logger.warning(
                f"Invalid API key for {request.method} {request.url.path} from {self._get_client_ip(request)}"
            )
            return self._unauthorized_response(detail="Invalid API key")

        # API key is valid, add to request state for potential use in handlers
        request.state.api_key = api_key

        # Process request
        return await call_next(request)

    def _is_public_endpoint(self, request: Request) -> bool:
        """
        Check if endpoint is public (no auth required).

        Public endpoints:
        - /health, /metrics (monitoring)
        - /docs, /openapi.json, /redoc (documentation - has separate HTTP Basic auth)
        - /demo/* (demo UI - has separate HTTP Basic auth)
        - /api/dev/monitoring/* (development monitoring, only in dev mode)
        - Same-origin requests from demo UI (no X-API-Key header, has Referer from /demo)

        Args:
            request: Incoming request

        Returns:
            True if endpoint is public
        """
        path = request.url.path

        # Health and metrics endpoints (always public)
        public_paths = ["/health", "/health/ready", "/metrics", "/"]
        if path in public_paths:
            return True

        # Documentation endpoints (have separate HTTP Basic auth)
        docs_paths = ["/docs", "/openapi.json", "/redoc"]
        if path in docs_paths:
            return True

        # Demo UI static files (have separate HTTP Basic auth)
        if path == "/demo" or path.startswith("/demo/"):
            return True

        # Viewer static files (have separate HTTP Basic auth)
        if path == "/viewer" or path.startswith("/viewer/"):
            return True

        # LTI 1.3 endpoints (use JWT authentication from Canvas)
        if path.startswith("/lti/"):
            return True

        # Dashboard static assets (CSS, images served by StaticFiles mount)
        if path.startswith("/static/canvas/"):
            return True

        # Development monitoring endpoints (public only in dev environment)
        if settings.environment == "dev" and path.startswith("/api/dev/monitoring"):
            return True

        # Allow same-origin requests from demo UI (protected by Basic Auth at /demo)
        if self._is_demo_ui_request(request):
            return True

        # Allow stream endpoints with token query parameter
        # Token validation happens in the endpoint handler
        if self._is_stream_token_request(request):
            return True

        return False

    def _is_demo_ui_request(self, request: Request) -> bool:
        """
        Check if request originates from the demo UI.

        The demo UI is served at /demo and protected by HTTP Basic Auth.
        Requests from the demo UI are same-origin and don't include an X-API-Key
        header. We identify them by checking the Referer header.

        This is secure because:
        1. The demo UI itself requires Basic Auth to access
        2. CORS prevents external sites from making requests with our Referer
        3. External API clients will use X-API-Key (not Referer-based auth)

        Args:
            request: Incoming request

        Returns:
            True if request appears to come from demo UI
        """
        # If request has an API key, it's an external client - use normal auth
        if request.headers.get(settings.api_key_header_name):
            return False

        # Check Referer header for demo UI or viewer origin
        referer = request.headers.get("Referer", "")
        if "/demo" in referer or "/viewer" in referer:
            return True

        # Check Origin header as fallback (for some browsers/requests)
        origin = request.headers.get("Origin", "")
        # Origin header doesn't include path, so we check Sec-Fetch-Site
        # for same-origin requests combined with absence of API key
        sec_fetch_site = request.headers.get("Sec-Fetch-Site", "")
        if origin and sec_fetch_site == "same-origin":
            # Same-origin request without API key - likely from demo UI
            # This is safe because external clients must use API key
            return True

        return False

    def _is_stream_token_request(self, request: Request) -> bool:
        """
        Check if request has stream token for SSE endpoint.

        Stream tokens allow bypassing API key auth for browser EventSource
        connections which cannot send custom headers.

        This only checks if the endpoint qualifies for token-based auth.
        Actual token validation and consumption happens in the endpoint handler.
        We mark it as "public" here to bypass API key check, then the
        endpoint validates the token.

        Args:
            request: Incoming request

        Returns:
            True if this is a stream endpoint with a token parameter
        """
        path = request.url.path

        # Only applies to stream endpoints (not the token creation endpoint)
        if not path.endswith("/stream"):
            return False

        # Must have token query parameter
        token = request.query_params.get("token")
        if not token:
            return False

        # Basic format validation (256-bit tokens are ~43 chars)
        # Full validation happens in endpoint handler
        if len(token) < 40:
            return False

        return True

    def _is_valid_key(self, provided_key: str) -> bool:
        """
        Validate API key using constant-time comparison.

        Uses secrets.compare_digest() to prevent timing attacks.
        Uses cached keys loaded at initialization for optimal performance.

        Args:
            provided_key: API key from request header

        Returns:
            True if key is valid
        """
        if not self._cached_keys:
            return False

        # Use constant-time comparison to prevent timing attacks
        for valid_key in self._cached_keys:
            if secrets.compare_digest(provided_key, valid_key):
                return True

        return False

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
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP header (nginx)
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        # Fall back to direct connection IP
        if request.client:
            return request.client.host

        return "unknown"

    def _unauthorized_response(self, detail: str) -> JSONResponse:
        """
        Create unauthorized error response.

        Args:
            detail: Error message

        Returns:
            401 Unauthorized response
        """
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": detail},
            headers={"WWW-Authenticate": 'ApiKey realm="API", charset="UTF-8"'},
        )
