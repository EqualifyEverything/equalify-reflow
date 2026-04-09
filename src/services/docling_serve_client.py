"""HTTP client for docling-serve.

Wraps docling-serve's ``/v1/convert/file`` endpoint with connection pooling,
circuit breaker, and retry logic.  The client is created at app startup and
closed at shutdown via the module-level lifecycle helpers.

When docling runs behind an internal ALB (PRD-1), cold starts produce 503s
and ConnectErrors while EC2 instances spin up.  The circuit breaker is tuned
with a high threshold (10) and long timeout (360s) to tolerate this.

Usage::

    from src.services.docling_serve_client import get_docling_client

    client = get_docling_client()
    response = await client.convert(file_content, "syllabus.pdf")
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.utils.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DoclingServeResponse(BaseModel):
    """Parsed response from docling-serve ``/v1/convert/file``."""

    md_content: str = ""
    json_content: dict[str, Any] = Field(default_factory=dict)
    status: str = "unknown"
    processing_time: float = 0.0
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

# Circuit breaker tuned for decoupled docling behind ALB:
# - threshold=10: tolerate cold-start 503s before tripping
# - timeout=360s: EC2 Spot + model loading can take ~3-5 min
_CIRCUIT_BREAKER = CircuitBreaker(
    "docling-serve",
    failure_threshold=10,
    timeout=360.0,
)

# Limit concurrent docling-serve calls to prevent OOM (~1-2GB per PDF)
_DOCLING_SEMAPHORE = asyncio.Semaphore(int(__import__("os").environ.get("DOCLING_MAX_CONCURRENT", "2")))


class DoclingServeClient:
    """Async HTTP client for docling-serve."""

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    # -- conversion ---------------------------------------------------------

    async def convert(
        self,
        file_content: bytes,
        filename: str,
        *,
        do_ocr: bool = False,
        force_ocr: bool = False,
        ocr_engine: str = "easyocr",
        ocr_lang: list[str] | None = None,
        do_table_structure: bool = True,
        include_images: bool = True,
        images_scale: float = 2.0,
        md_page_break_placeholder: str = "<!-- PAGE_BREAK -->",
        image_export_mode: str = "placeholder",
        max_retries: int = 2,
        timeout: float | None = None,
        on_cold_start: Callable[[], None] | None = None,
    ) -> DoclingServeResponse:
        """Convert a PDF via docling-serve with retry and circuit breaker.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename (sent as multipart name).
            do_ocr: Enable OCR processing.
            force_ocr: Force OCR even when text is detected.
            ocr_engine: OCR engine name (``easyocr``, ``tesseract``, etc.).
            ocr_lang: OCR language codes (EasyOCR format, e.g. ``["en"]``).
            do_table_structure: Extract table structure.
            include_images: Extract images from the document.
            images_scale: Scale factor for extracted images.
            md_page_break_placeholder: Marker inserted between pages in MD.
            image_export_mode: How images appear in output (``embedded`` / ``placeholder``).
            max_retries: Maximum retry attempts on transient errors.
            timeout: Per-request timeout override (seconds). When set,
                overrides the client-level timeout for this conversion only.
                Useful for OCR which is significantly slower than text extraction.

        Returns:
            Parsed :class:`DoclingServeResponse`.

        Raises:
            CircuitBreakerOpenError: If the circuit breaker is open.
            httpx.HTTPStatusError: On non-retryable HTTP errors.
            RuntimeError: If all retries are exhausted.
        """
        # Fail fast if circuit breaker is open (before acquiring semaphore)
        _CIRCUIT_BREAKER.check_state()

        async with _DOCLING_SEMAPHORE:
            return await self._convert_with_retry(
                file_content,
                filename,
                do_ocr=do_ocr,
                force_ocr=force_ocr,
                ocr_engine=ocr_engine,
                ocr_lang=ocr_lang or [],
                do_table_structure=do_table_structure,
                include_images=include_images,
                images_scale=images_scale,
                md_page_break_placeholder=md_page_break_placeholder,
                image_export_mode=image_export_mode,
                max_retries=max_retries,
                timeout=timeout,
                on_cold_start=on_cold_start,
            )

    async def _convert_with_retry(
        self,
        file_content: bytes,
        filename: str,
        *,
        do_ocr: bool,
        force_ocr: bool,
        ocr_engine: str,
        ocr_lang: list[str],
        do_table_structure: bool,
        include_images: bool,
        images_scale: float,
        md_page_break_placeholder: str,
        image_export_mode: str,
        max_retries: int,
        timeout: float | None = None,
        on_cold_start: Callable[[], None] | None = None,
    ) -> DoclingServeResponse:
        """Inner retry loop (runs inside the concurrency semaphore)."""
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 2):  # +2 because first attempt is not a retry
            try:
                response = await self._do_convert(
                    file_content,
                    filename,
                    do_ocr=do_ocr,
                    force_ocr=force_ocr,
                    ocr_engine=ocr_engine,
                    ocr_lang=ocr_lang,
                    do_table_structure=do_table_structure,
                    include_images=include_images,
                    images_scale=images_scale,
                    md_page_break_placeholder=md_page_break_placeholder,
                    image_export_mode=image_export_mode,
                    timeout=timeout,
                )
                _CIRCUIT_BREAKER.record_success()
                return response
            except (httpx.RemoteProtocolError, httpx.ConnectError) as exc:
                # Server crashed (OOM) or unreachable (cold start / ALB no targets)
                last_exc = exc
                _CIRCUIT_BREAKER.record_failure()
                if attempt <= max_retries:
                    logger.warning(
                        "docling-serve unreachable (attempt %d/%d): %s. Waiting for healthy...",
                        attempt,
                        max_retries + 1,
                        type(exc).__name__,
                    )
                    if on_cold_start is not None:
                        on_cold_start()
                        on_cold_start = None
                    await self._wait_for_healthy(timeout=300.0)
                    continue
                raise
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_exc = exc
                _CIRCUIT_BREAKER.record_failure()
                if attempt <= max_retries:
                    delay = min(2.0 ** (attempt - 1), 8.0)
                    logger.warning(
                        "docling-serve timeout (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt,
                        max_retries + 1,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status_code = exc.response.status_code

                if status_code == 503:
                    # ALB has no healthy targets — cold start
                    _CIRCUIT_BREAKER.record_failure()
                    if attempt <= max_retries:
                        logger.warning(
                            "docling-serve 503 (cold start?), waiting for healthy...",
                        )
                        if on_cold_start is not None:
                            on_cold_start()
                            on_cold_start = None  # fire only once
                        await self._wait_for_healthy(timeout=300.0)
                        continue
                    raise
                elif status_code == 504:
                    # Document too slow — not a service failure, no retry
                    logger.warning(
                        "docling-serve 504 timeout for '%s' — document too large/complex",
                        filename,
                    )
                    raise
                elif status_code >= 500:
                    _CIRCUIT_BREAKER.record_failure()
                    if attempt <= max_retries:
                        delay = min(2.0 ** (attempt - 1), 8.0)
                        logger.warning(
                            "docling-serve %d error (attempt %d/%d). Retrying in %.1fs",
                            status_code,
                            attempt,
                            max_retries + 1,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise
                # Non-retryable (4xx, etc.)
                raise

        # Should not reach here, but for type safety
        if last_exc:
            raise last_exc
        raise RuntimeError("docling-serve: unexpected retry loop exit")

    async def _wait_for_healthy(self, timeout: float = 300.0) -> None:
        """Poll docling-serve health endpoint until it responds or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout
        interval = 3.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            if await self.check_health():
                logger.info("docling-serve is healthy again")
                return
        logger.warning("docling-serve did not recover within %.0fs", timeout)

    async def _do_convert(
        self,
        file_content: bytes,
        filename: str,
        *,
        do_ocr: bool,
        force_ocr: bool,
        ocr_engine: str,
        ocr_lang: list[str],
        do_table_structure: bool,
        include_images: bool,
        images_scale: float,
        md_page_break_placeholder: str,
        image_export_mode: str,
        timeout: float | None = None,
    ) -> DoclingServeResponse:
        """Send the actual HTTP request to docling-serve."""
        # httpx 0.28+ requires all multipart fields in `files` param when using
        # AsyncClient — mixing `files` and `data` creates a SyncByteStream which
        # raises RuntimeError.  Form fields use ``(None, value)`` tuples.
        fields: list[tuple[str, tuple[str | None, str | bytes, str] | tuple[None, str]]] = [
            ("files", (filename, file_content, "application/pdf")),
            ("to_formats", (None, "md")),
            ("to_formats", (None, "json")),
            ("image_export_mode", (None, image_export_mode)),
            ("do_ocr", (None, str(do_ocr).lower())),
            ("force_ocr", (None, str(force_ocr).lower())),
            ("ocr_engine", (None, ocr_engine)),
            ("do_table_structure", (None, str(do_table_structure).lower())),
            ("include_images", (None, str(include_images).lower())),
            ("images_scale", (None, str(images_scale))),
            ("md_page_break_placeholder", (None, md_page_break_placeholder)),
        ]
        for lang in ocr_lang:
            fields.append(("ocr_lang", (None, lang)))

        request_kwargs: dict = {"files": fields}
        if timeout is not None:
            request_kwargs["timeout"] = httpx.Timeout(timeout, connect=10.0)
        resp = await self._client.post("/v1/convert/file", **request_kwargs)
        resp.raise_for_status()

        body = resp.json()

        # Parse the response envelope
        doc = body.get("document", {})
        return DoclingServeResponse(
            md_content=doc.get("md_content", ""),
            json_content=doc.get("json_content", {}),
            status=body.get("status", "unknown"),
            processing_time=body.get("processing_time", 0.0),
            errors=body.get("errors", []),
        )

    # -- health -------------------------------------------------------------

    async def check_health(self) -> bool:
        """Check if docling-serve is healthy.

        Returns:
            ``True`` if the service responds to ``/health``, ``False`` otherwise.
        """
        try:
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Module-level lifecycle helpers
# ---------------------------------------------------------------------------

_client: DoclingServeClient | None = None


def init_docling_client(base_url: str, timeout: float = 120.0) -> DoclingServeClient:
    """Create the module-level docling-serve client (call at app startup)."""
    global _client  # noqa: PLW0603
    _client = DoclingServeClient(base_url, timeout)
    logger.info("Docling-serve client initialized: %s (timeout=%.0fs)", base_url, timeout)
    return _client


def get_docling_client() -> DoclingServeClient:
    """Return the module-level client, raising if not initialized."""
    if _client is None:
        raise RuntimeError(
            "Docling-serve client not initialized. Call init_docling_client() at startup."
        )
    return _client


async def close_docling_client() -> None:
    """Close the module-level client (call at app shutdown)."""
    global _client  # noqa: PLW0603
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("Docling-serve client closed")


def reset_docling_circuit_breaker() -> None:
    """Reset the circuit breaker (for testing)."""
    _CIRCUIT_BREAKER.reset()
