"""Request logging middleware."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def _identity_fields(request: Request) -> dict[str, str | None]:
    """Pull caller-identity attributes off ``request.state`` for logging.

    Two independent auth paths can populate identity, and a request may use
    either:

    * SessionAuthMiddleware sets ``request.state.identity`` (an ``Identity``)
      for viewer logins — surfaced as ``user_sub`` / ``user_email`` /
      ``user_provider``.
    * APIKeyAuthMiddleware sets ``request.state.api_key_label`` for
      programmatic clients — surfaced as ``api_key_label``. The key itself
      is never logged.

    Returns ``{}`` for fully anonymous requests so log lines from
    AUTH_MODE=none deployments stay shape-identical to today's.

    The ``isinstance`` rather than truthiness checks matter: existing mock
    tests sometimes use a MagicMock for ``request.state`` whose attribute
    access always returns a truthy child mock.
    """
    from ..auth.base import Identity  # lazy to keep middleware import-light

    fields: dict[str, str | None] = {}

    identity = getattr(request.state, "identity", None)
    if isinstance(identity, Identity):
        fields["user_sub"] = identity.sub
        fields["user_email"] = identity.email
        fields["user_provider"] = identity.provider_id

    api_key_label = getattr(request.state, "api_key_label", None)
    if isinstance(api_key_label, str):
        fields["api_key_label"] = api_key_label

    return fields


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Log request and response details.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object
        """
        start_time = time.time()

        # Log request — identity isn't set yet (SessionAuthMiddleware runs
        # inner to us per main.py middleware ordering), so user_* fields
        # only appear on the Response line below. Keeping the Request line
        # *before* call_next preserves crash/hang forensics.
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else None,
            },
        )

        response = await call_next(request)

        process_time = time.time() - start_time
        logger.info(
            f"Response: {response.status_code} ({process_time:.3f}s)",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "process_time": process_time,
                **_identity_fields(request),
            },
        )

        response.headers["X-Process-Time"] = str(process_time)

        return response
