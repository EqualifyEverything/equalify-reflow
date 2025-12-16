"""Debug middleware for capturing HTTP request and response bodies.

This middleware captures request bodies and response bodies when debug mode is enabled.
It works in conjunction with the DebugLoggingService to provide comprehensive logging.
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Any

from fastapi import Request, Response
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse

from src.config import settings
from src.services.debug_logging_service import debug_logger

logger = logging.getLogger(__name__)


class DebugMiddleware(BaseHTTPMiddleware):
    """Middleware for capturing request/response bodies in debug mode.

    Only active when settings.debug_mode is True. Captures:
    - Request headers (with sensitive values masked)
    - Request body (for POST/PATCH/PUT)
    - Response body
    - Query parameters
    - Request timing

    Note: File uploads are logged as metadata only (filename, size),
    not the actual file contents.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request and capture debug information.

        Args:
            request: Incoming request
            call_next: Next middleware/handler

        Returns:
            Response object
        """
        # Skip if debug mode is disabled
        if not settings.debug_mode:
            return await call_next(request)

        start_time = time.time()

        # Extract job_id from path if available
        job_id = self._extract_job_id(request.url.path)

        # Capture request details
        request_body = await self._capture_request_body(request)

        # Log the request
        debug_logger.log_api_request(
            job_id=job_id,
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            body=request_body,
            query_params=dict(request.query_params),
            client_ip=request.client.host if request.client else None,
        )

        # Call the next handler and capture response
        response = await call_next(request)

        # Capture response body
        response_body = await self._capture_response_body(response)

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Log the response
        debug_logger.log_api_response(
            job_id=job_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            response_body=response_body,
            duration_ms=duration_ms,
        )

        return response

    def _extract_job_id(self, path: str) -> str | None:
        """Extract job_id from URL path if present.

        Args:
            path: Request URL path

        Returns:
            Job ID if found, None otherwise
        """
        # Common patterns: /api/documents/{job_id}, /api/corrections/{job_id}
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part in ("documents", "corrections") and i + 1 < len(parts):
                potential_id = parts[i + 1]
                # Check if it looks like a UUID or job ID
                if len(potential_id) >= 8 and potential_id not in ("submit",):
                    return potential_id
        return None

    async def _capture_request_body(self, request: Request) -> dict[str, Any] | str | None:
        """Capture request body for debug logging.

        Handles JSON bodies and form data (including file uploads).
        File contents are not captured - only metadata.

        Args:
            request: FastAPI request object

        Returns:
            Captured body as dict/string, or None if no body
        """
        if request.method not in ("POST", "PATCH", "PUT"):
            return None

        content_type = request.headers.get("content-type", "")

        try:
            if "application/json" in content_type:
                # JSON body
                body_bytes = await request.body()
                # Reset body for downstream handlers
                request._body = body_bytes
                return json.loads(body_bytes.decode("utf-8"))

            elif "multipart/form-data" in content_type:
                # Form with file upload - capture metadata only
                form = await request.form()
                form_data: dict[str, Any] = {}

                for key, value in form.multi_items():
                    if isinstance(value, UploadFile):
                        # Only log file metadata, not contents
                        form_data[key] = {
                            "_type": "file",
                            "filename": value.filename,
                            "content_type": value.content_type,
                            "size_bytes": value.size if hasattr(value, "size") else "unknown",
                        }
                        # Reset file position for downstream handlers
                        await value.seek(0)
                    else:
                        form_data[key] = value

                return form_data

            elif "application/x-www-form-urlencoded" in content_type:
                # URL-encoded form
                form = await request.form()
                return dict(form)

            else:
                # Unknown content type - just note it
                return {"_note": f"Unknown content-type: {content_type}"}

        except Exception as e:
            logger.debug(f"Failed to capture request body: {e}")
            return {"_error": str(e)}

    async def _capture_response_body(self, response: Response) -> dict[str, Any] | str | None:
        """Capture response body for debug logging.

        Args:
            response: Starlette response object

        Returns:
            Captured body as dict/string, or None if streaming/empty
        """
        content_type = response.headers.get("content-type", "")

        # Skip binary/streaming responses
        if isinstance(response, StreamingResponse):
            # For streaming responses, we need to buffer and re-stream
            body_parts: list[bytes] = []
            original_body_iterator = response.body_iterator

            async def buffer_and_iterate():
                async for chunk in original_body_iterator:
                    body_parts.append(chunk)
                    yield chunk

            response.body_iterator = buffer_and_iterate()

            # We can't easily get the body here without consuming it
            # Return a placeholder
            return {"_type": "streaming_response", "content_type": content_type}

        try:
            # Access body if available
            if hasattr(response, "body"):
                body_bytes = response.body

                if "application/json" in content_type:
                    return json.loads(body_bytes.decode("utf-8"))
                elif "text/" in content_type:
                    return body_bytes.decode("utf-8")[:1000]  # Truncate text
                else:
                    return {"_type": "binary", "size_bytes": len(body_bytes)}

            return None

        except Exception as e:
            logger.debug(f"Failed to capture response body: {e}")
            return {"_error": str(e)}


__all__ = ["DebugMiddleware"]
