"""API Key authentication middleware for FastAPI."""

import base64
import logging
import secrets
from collections.abc import Callable

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

    def __init__(self, app):
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

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
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
                f"Missing API key for {request.method} {request.url.path} "
                f"from {self._get_client_ip(request)}"
            )
            return self._unauthorized_response(
                detail=f"Missing API key. Provide a valid key in the '{settings.api_key_header_name}' header."
            )

        # Validate API key using constant-time comparison
        if not self._is_valid_key(api_key):
            logger.warning(
                f"Invalid API key for {request.method} {request.url.path} "
                f"from {self._get_client_ip(request)}"
            )
            return self._unauthorized_response(
                detail="Invalid API key"
            )

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
        - Any request with valid HTTP Basic auth (from demo UI)

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

        # Development monitoring endpoints (public only in dev environment)
        if settings.environment == "dev" and path.startswith("/api/dev/monitoring"):
            return True

        # Allow API requests with valid HTTP Basic auth (from demo UI)
        if self._has_valid_basic_auth(request):
            return True

        return False

    def _has_valid_basic_auth(self, request: Request) -> bool:
        """
        Check if request has valid HTTP Basic auth credentials.

        This allows the demo UI (which uses Basic auth) to make API
        requests without needing a separate API key.

        Args:
            request: Incoming request

        Returns:
            True if request has valid Basic auth
        """
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return False

        try:
            encoded_credentials = auth_header[6:]
            decoded_bytes = base64.b64decode(encoded_credentials)
            decoded_str = decoded_bytes.decode("utf-8")

            if ":" not in decoded_str:
                return False

            username, password = decoded_str.split(":", 1)

            # Check against docs credentials
            if not settings.docs_password:
                return False

            expected_username = settings.docs_username
            expected_password = settings.docs_password.get_secret_value()

            username_valid = secrets.compare_digest(username, expected_username)
            password_valid = secrets.compare_digest(password, expected_password)

            return username_valid and password_valid
        except Exception:
            return False

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
            content={
                "detail": detail
            },
            headers={
                "WWW-Authenticate": "ApiKey realm=\"API\", charset=\"UTF-8\""
            }
        )
