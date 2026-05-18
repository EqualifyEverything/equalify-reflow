"""API Key authentication middleware for FastAPI."""

import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..auth.base import Identity
from ..config import settings

logger = logging.getLogger(__name__)


def _fingerprint(api_key: str) -> str:
    """Stable, non-reversible identifier for an unlabelled key.

    Lets two unlabelled keys still be told apart in logs without ever
    logging the key itself.
    """
    return "key-" + hashlib.sha256(api_key.encode()).hexdigest()[:8]


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
        # Cache API keys at initialization to avoid reloading on every request.
        # Maps secret key -> human label (for usage attribution in logs).
        self._cached_keys: dict[str, str] = self._load_api_keys()

    def _load_api_keys(self) -> dict[str, str]:
        """
        Load valid API keys from settings into a {key: label} map.

        ``API_KEYS`` is comma-separated. Each entry is either a bare key or a
        ``label:key`` pair (split on the first colon, mirroring how
        ``AUTH_BASIC_USERS`` encodes ``username:hash``). The label is logged
        on every authenticated request so per-key usage is attributable; the
        key itself is never logged. A bare key gets a derived
        ``key-<fingerprint>`` label so even unlabelled keys stay distinct.

        Returns:
            Mapping of valid API key string -> label.
        """
        if not settings.api_keys:
            logger.warning("No API keys configured! All authenticated requests will be rejected.")
            return {}

        keys: dict[str, str] = {}
        for entry in settings.api_keys.get_secret_value().split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                label, _, key = entry.partition(":")
                label = label.strip()
                key = key.strip()
            else:
                key = entry
                label = ""
            if not key:
                logger.warning("API key entry skipped: empty key after parsing")
                continue
            keys[key] = label or _fingerprint(key)

        if not keys:
            logger.warning("API keys configured but empty after parsing!")
            return {}

        logger.info("Loaded %d API key(s) for authentication", len(keys))
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
        label = self._match_key(api_key)
        if label is None:
            logger.warning(
                f"Invalid API key for {request.method} {request.url.path} from {self._get_client_ip(request)}"
            )
            return self._unauthorized_response(detail="Invalid API key")

        # API key is valid. Stash the key for handlers and the label for
        # request logging (LoggingMiddleware reads request.state.api_key_label
        # so per-key usage is attributable without ever logging the key).
        request.state.api_key = api_key
        request.state.api_key_label = label

        # Process request
        return await call_next(request)

    def _is_public_endpoint(self, request: Request) -> bool:
        """
        Check if endpoint is exempt from API key auth.

        Only `/api/*` paths require an API key — everything else is served by
        the (public) viewer, the LTI JWT flow, or is a public health check.
        API routes themselves have three carve-outs:

        - Development-only endpoints under /api/dev/monitoring, /api/dev/minimal,
          /api/dev/pipeline-viewer are public when settings.environment == "dev"
        - Same-origin requests from the viewer are exempt via
          `_is_demo_ui_request` (checks Referer, Origin, and Sec-Fetch-Site)
          so the viewer's JS can call the API without the browser having to
          inject an X-API-Key header
        - SSE stream endpoints with a `?token=` query parameter bypass API
          key auth and validate the token in the endpoint handler instead

        Args:
            request: Incoming request

        Returns:
            True if the request is exempt from API key auth
        """
        path = request.url.path

        # API routes are the only ones gated by API key auth.
        if path.startswith("/api/"):
            # Session-authenticated requests (set by SessionAuthMiddleware
            # when AUTH_MODE != 'none') short-circuit. Both auth paths
            # coexist: an API key still works in parallel for programmatic
            # clients regardless of AUTH_MODE.
            #
            # We isinstance-check rather than truthiness-check because mock
            # tests upstream sometimes use MagicMock for request.state, and a
            # MagicMock attribute is always truthy. Only a real Identity
            # object should short-circuit.
            if isinstance(getattr(request.state, "identity", None), Identity):
                return True
            # /api/v1/auth/* endpoints handle their own auth (config is
            # always public; login/logout/me self-validate). Exempt the
            # subtree here so an unauthenticated user can hit /auth/login.
            if path.startswith("/api/v1/auth/"):
                return True
            # Dev-only endpoints are exempt when running in dev
            if settings.environment == "dev" and (
                path.startswith("/api/dev/monitoring")
                or path.startswith("/api/dev/minimal")
                or path.startswith("/api/dev/pipeline-viewer")
            ):
                return True
            # Same-origin requests from the viewer or demo UI.
            # When AUTH_MODE != 'none', the SPA must establish a session
            # before it can call the API — the same-origin shortcut is only
            # safe in the open-default mode. Otherwise an unauthenticated
            # browser session would defeat the new auth layer.
            if settings.auth_mode == "none" and self._is_demo_ui_request(request):
                return True
            # SSE stream endpoints authenticate via ?token= query param
            if self._is_stream_token_request(request):
                return True
            return False

        # Everything outside /api/ is exempt from API key auth:
        #   - / and every SPA deep link → viewer HTML (public)
        #   - /viewer, /viewer/* → legacy 301 redirects to /
        #   - /health, /health/ready → public health checks
        #   - /docs, /openapi.json, /redoc → public API documentation
        #   - /lti/* → Canvas JWT auth
        #   - /static/canvas/* → dashboard assets
        #   - /metrics → Prometheus
        #   - /assets/*, favicons, fonts, etc. → public viewer static files
        return True

    def _is_demo_ui_request(self, request: Request) -> bool:
        """
        Check if request is a same-origin browser fetch from the viewer SPA.

        The viewer is served at `/` and is now publicly accessible. Its JS
        calls /api/v1/* from the same origin without an X-API-Key header.
        We identify these requests by the combination of:

        - No X-API-Key header (external clients always send one)
        - Sec-Fetch-Site: same-origin (set by the browser for genuinely
          same-origin fetches, unforgeable by external callers)

        This is safe because:
        1. CORS prevents external sites from forging the Origin or reading
           the response
        2. External API clients must send X-API-Key, which takes the
           normal-auth branch in dispatch() above before this code runs
        3. Sec-Fetch-Site is a browser-controlled header — page scripts
           cannot set or spoof it

        Args:
            request: Incoming request

        Returns:
            True if request appears to be a same-origin viewer fetch
        """
        # If request has an API key, it's an external client - use normal auth
        if request.headers.get(settings.api_key_header_name):
            return False

        # Same-origin fetch from the viewer SPA — trust the browser-set
        # Sec-Fetch-Site header combined with absence of API key.
        sec_fetch_site = request.headers.get("Sec-Fetch-Site", "")
        if sec_fetch_site == "same-origin":
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

    def _match_key(self, provided_key: str) -> str | None:
        """
        Validate an API key and return its label.

        Uses secrets.compare_digest() per key to prevent timing attacks on
        the comparison itself. Uses cached keys loaded at initialization.

        Args:
            provided_key: API key from request header

        Returns:
            The matching key's label, or None if no key matches.
        """
        for valid_key, label in self._cached_keys.items():
            if secrets.compare_digest(provided_key, valid_key):
                return label

        return None

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
