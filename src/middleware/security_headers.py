"""Security headers middleware for iframe embedding support.

Sets Content-Security-Policy frame-ancestors and X-Frame-Options headers
to allow the Canvas LMS to embed dashboard pages in an iframe while
blocking other origins from framing the application.
"""

import logging
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Dashboard routes that Canvas embeds in an iframe
_LTI_DASHBOARD_PREFIX = "/lti/dashboard"
_LTI_STATIC_PREFIX = "/static/canvas"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses.

    For LTI dashboard routes, sets frame-ancestors to allow the
    configured Canvas issuer domain. For all other routes, sets
    X-Frame-Options: DENY to prevent clickjacking.
    """

    def __init__(self, app, canvas_origin: str = "") -> None:
        super().__init__(app)
        self.canvas_origin = self._normalize_origin(canvas_origin)

    @staticmethod
    def _normalize_origin(issuer_url: str) -> str:
        """Extract scheme + host from an issuer URL.

        Canvas LTI_ISSUER is typically something like
        ``https://canvas.instructure.com``. We only need the origin
        (scheme + host) for the frame-ancestors directive.
        """
        if not issuer_url:
            return ""
        parsed = urlparse(issuer_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return issuer_url.rstrip("/")

    def _is_dashboard_route(self, path: str) -> bool:
        """Check if the request path is a dashboard or dashboard-static route."""
        return path.startswith(_LTI_DASHBOARD_PREFIX) or path.startswith(_LTI_STATIC_PREFIX)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        if self._is_dashboard_route(request.url.path):
            # Allow Canvas to embed dashboard pages in an iframe
            ancestors = "'self'"
            if self.canvas_origin:
                ancestors = f"'self' {self.canvas_origin}"
            response.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
            # Remove X-Frame-Options if somehow set by another layer,
            # since CSP frame-ancestors takes precedence in modern browsers
            if "X-Frame-Options" in response.headers:
                del response.headers["X-Frame-Options"]
        else:
            # Block framing for all non-dashboard routes
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"

        return response
