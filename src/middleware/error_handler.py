"""Global error handling middleware."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware for handling uncaught exceptions."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Handle request and catch any unhandled exceptions.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object
        """
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            logger.error(
                f"Unhandled exception: {exc}",
                exc_info=True,
                extra={"path": request.url.path}
            )

            # Detailed error in development, sanitized in production
            if settings.log_level == "DEBUG":
                detail = f"Internal server error: {str(exc)}"
            else:
                detail = "An internal error occurred. Please try again later."

            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": detail}
            )
