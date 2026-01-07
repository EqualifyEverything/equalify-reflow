"""HTTP Basic authentication middleware for Swagger/OpenAPI documentation and demo UI."""

import base64
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings

logger = logging.getLogger(__name__)


class DocsAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for HTTP Basic authentication on documentation and demo endpoints.

    Protects /docs, /openapi.json, /redoc, and /demo with username/password.
    Triggers browser login prompt via WWW-Authenticate header.
    """

    def __init__(self, app: Any) -> None:
        """
        Initialize docs auth middleware.

        Args:
            app: FastAPI application instance
        """
        super().__init__(app)

        # Validate configuration
        if not settings.docs_password:
            logger.warning(
                "Docs authentication enabled but no password configured! "
                "Documentation endpoints will be inaccessible."
            )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Validate HTTP Basic auth for documentation endpoints.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object (401 if auth fails, otherwise normal response)
        """
        # Only protect documentation endpoints
        if not self._is_docs_endpoint(request):
            return await call_next(request)

        # Extract credentials from Authorization header
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            logger.info(f"Missing authorization for docs access from {self._get_client_ip(request)}")
            return self._unauthorized_response()

        # Validate Basic auth scheme
        if not auth_header.startswith("Basic "):
            logger.warning(f"Invalid auth scheme for docs access from {self._get_client_ip(request)}")
            return self._unauthorized_response()

        # Decode and validate credentials
        try:
            credentials = self._decode_credentials(auth_header)
            if not credentials:
                return self._unauthorized_response()

            username, password = credentials

            # Validate against configured credentials using constant-time comparison
            if not self._is_valid_credentials(username, password):
                logger.warning(
                    f"Invalid credentials for docs access from {self._get_client_ip(request)}: "
                    f"username={username}"
                )
                return self._unauthorized_response()

        except Exception as e:
            logger.error(f"Error validating docs credentials: {e}")
            return self._unauthorized_response()

        # Credentials are valid, process request
        logger.info(f"Successful docs authentication for user: {username}")
        return await call_next(request)

    def _is_docs_endpoint(self, request: Request) -> bool:
        """
        Check if endpoint is a protected endpoint.

        Protected endpoints:
        - /docs (Swagger UI)
        - /openapi.json (OpenAPI schema)
        - /redoc (ReDoc alternative UI)
        - /demo/* (Demo UI)

        Args:
            request: Incoming request

        Returns:
            True if endpoint requires docs auth
        """
        path = request.url.path
        # Exact match paths
        exact_paths = ["/docs", "/openapi.json", "/redoc"]
        if path in exact_paths:
            return True
        # Prefix match for demo UI (includes /demo, /demo/, /demo/assets/*, etc.)
        if path == "/demo" or path.startswith("/demo/"):
            return True
        # Prefix match for viewer - but allow static assets without auth
        # (favicon, JS, CSS files should load without requiring credentials)
        if path == "/viewer" or path.startswith("/viewer/"):
            # Allow static assets without auth (files with common extensions)
            if any(path.endswith(ext) for ext in ['.js', '.css', '.svg', '.ico', '.png', '.jpg', '.woff', '.woff2']):
                return False
            return True
        return False

    def _decode_credentials(self, auth_header: str) -> tuple[str, str] | None:
        """
        Decode HTTP Basic auth credentials.

        Args:
            auth_header: Authorization header value (e.g., "Basic dXNlcjpwYXNz")

        Returns:
            Tuple of (username, password) or None if invalid
        """
        try:
            # Remove "Basic " prefix
            encoded_credentials = auth_header[6:]

            # Decode base64
            decoded_bytes = base64.b64decode(encoded_credentials)
            decoded_str = decoded_bytes.decode("utf-8")

            # Split on first colon (password may contain colons)
            if ":" not in decoded_str:
                return None

            username, password = decoded_str.split(":", 1)
            return username, password

        except Exception as e:
            logger.error(f"Failed to decode credentials: {e}")
            return None

    def _is_valid_credentials(self, username: str, password: str) -> bool:
        """
        Validate username and password using constant-time comparison.

        Uses secrets.compare_digest() to prevent timing attacks.

        Args:
            username: Provided username
            password: Provided password

        Returns:
            True if credentials are valid
        """
        # Check if password is configured
        if not settings.docs_password:
            return False

        # Get configured credentials
        expected_username = settings.docs_username
        expected_password = settings.docs_password.get_secret_value()

        # Use constant-time comparison to prevent timing attacks
        username_valid = secrets.compare_digest(username, expected_username)
        password_valid = secrets.compare_digest(password, expected_password)

        return username_valid and password_valid

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP from request.

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

    def _unauthorized_response(self) -> JSONResponse:
        """
        Create unauthorized error response with WWW-Authenticate header.

        This triggers the browser's built-in login prompt.

        Returns:
            401 Unauthorized response
        """
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": "Authentication required to access documentation"
            },
            headers={
                "WWW-Authenticate": 'Basic realm="Equalify PDF API Documentation", charset="UTF-8"'
            }
        )
