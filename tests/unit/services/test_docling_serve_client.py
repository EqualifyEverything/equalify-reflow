"""Tests for docling_serve_client — HTTP client for docling-serve."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.docling_serve_client import (
    DoclingServeClient,
    DoclingServeResponse,
    _CIRCUIT_BREAKER,
    close_docling_client,
    get_docling_client,
    init_docling_client,
    reset_docling_circuit_breaker,
)
from src.utils.circuit_breaker import CircuitBreakerOpenError


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    reset_docling_circuit_breaker()
    yield
    reset_docling_circuit_breaker()


@pytest.fixture(autouse=True)
def _reset_module_client():
    """Reset module-level client after each test."""
    yield
    # Force-reset the module-level client
    import src.services.docling_serve_client as mod
    mod._client = None


# ---------------------------------------------------------------------------
# DoclingServeResponse model
# ---------------------------------------------------------------------------


class TestDoclingServeResponse:
    """Tests for the response model."""

    def test_defaults(self):
        r = DoclingServeResponse()
        assert r.md_content == ""
        assert r.json_content == {}
        assert r.status == "unknown"
        assert r.processing_time == 0.0
        assert r.errors == []

    def test_from_dict(self):
        r = DoclingServeResponse(
            md_content="# Hello",
            json_content={"pages": {}},
            status="success",
            processing_time=1.5,
            errors=["warn"],
        )
        assert r.md_content == "# Hello"
        assert r.status == "success"
        assert r.processing_time == 1.5
        assert r.errors == ["warn"]


# ---------------------------------------------------------------------------
# Client — convert
# ---------------------------------------------------------------------------


class TestDoclingServeClientConvert:
    """Tests for the convert method."""

    @pytest.fixture
    def client(self):
        return DoclingServeClient("http://test:5001", timeout=10.0)

    @pytest.mark.asyncio
    async def test_successful_convert(self, client: DoclingServeClient):
        mock_response = httpx.Response(
            200,
            json={
                "document": {
                    "md_content": "# Page 1\n\nHello",
                    "json_content": {"pages": {"1": {}}},
                },
                "status": "success",
                "processing_time": 2.3,
                "errors": [],
            },
            request=httpx.Request("POST", "http://test:5001/v1/convert/file"),
        )

        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.convert(b"%PDF-test", "test.pdf")

        assert isinstance(result, DoclingServeResponse)
        assert result.md_content == "# Page 1\n\nHello"
        assert result.status == "success"
        assert result.processing_time == 2.3

    @pytest.mark.asyncio
    async def test_convert_sends_correct_form_data(self, client: DoclingServeClient):
        mock_response = httpx.Response(
            200,
            json={"document": {"md_content": "", "json_content": {}}, "status": "success"},
            request=httpx.Request("POST", "http://test:5001/v1/convert/file"),
        )

        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            await client.convert(
                b"%PDF-test",
                "doc.pdf",
                do_ocr=True,
                force_ocr=True,
                ocr_engine="easyocr",
                ocr_lang=["en", "de"],
                images_scale=2.0,
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # All fields are passed via `files` param (httpx 0.28+ async compat)
        fields = call_kwargs.kwargs.get("files") or call_kwargs[1].get("files")

        # Verify form field keys are present
        field_keys = [k for k, _ in fields]
        assert "files" in field_keys  # the PDF file
        assert "to_formats" in field_keys
        assert "do_ocr" in field_keys
        assert "ocr_lang" in field_keys

        # Verify OCR lang values (form fields use (None, value) tuples)
        ocr_langs = [v for k, v in fields if k == "ocr_lang"]
        assert (None, "en") in ocr_langs
        assert (None, "de") in ocr_langs

    @pytest.mark.asyncio
    async def test_convert_retries_on_timeout(self, client: DoclingServeClient):
        """Retries on httpx.TimeoutException then succeeds."""
        mock_response = httpx.Response(
            200,
            json={"document": {"md_content": "ok", "json_content": {}}, "status": "success"},
            request=httpx.Request("POST", "http://test:5001/v1/convert/file"),
        )

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("timeout")
            return mock_response

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch("src.services.docling_serve_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert result.md_content == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_convert_retries_on_5xx(self, client: DoclingServeClient):
        """Retries on 500 server error then succeeds."""
        error_response = httpx.Response(500, request=httpx.Request("POST", "http://test:5001/v1/convert/file"))
        ok_response = httpx.Response(
            200,
            json={"document": {"md_content": "ok", "json_content": {}}, "status": "success"},
            request=httpx.Request("POST", "http://test:5001/v1/convert/file"),
        )

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError("500", request=error_response.request, response=error_response)
            return ok_response

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch("src.services.docling_serve_client.asyncio.sleep", new_callable=AsyncMock):
                result = await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert result.md_content == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_convert_no_retry_on_4xx(self, client: DoclingServeClient):
        """4xx errors are not retried."""
        error_response = httpx.Response(400, request=httpx.Request("POST", "http://test:5001/v1/convert/file"))

        async def side_effect(*args, **kwargs):
            raise httpx.HTTPStatusError("400", request=error_response.request, response=error_response)

        with patch.object(client._client, "post", side_effect=side_effect):
            with pytest.raises(httpx.HTTPStatusError):
                await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

    @pytest.mark.asyncio
    async def test_convert_exhausts_retries(self, client: DoclingServeClient):
        """Raises after exhausting all retries."""
        async def side_effect(*args, **kwargs):
            raise httpx.ReadTimeout("timeout")

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch("src.services.docling_serve_client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.ReadTimeout):
                    await client.convert(b"%PDF-test", "test.pdf", max_retries=1)

    @pytest.mark.asyncio
    async def test_convert_circuit_breaker_opens(self, client: DoclingServeClient):
        """Circuit breaker opens after repeated failures (threshold=10)."""
        async def side_effect(*args, **kwargs):
            raise httpx.ConnectError("refused")

        # Need to accumulate 10 failures to trip the breaker
        with patch.object(client._client, "post", side_effect=side_effect):
            with patch("src.services.docling_serve_client.asyncio.sleep", new_callable=AsyncMock):
                with patch.object(client, "_wait_for_healthy", new_callable=AsyncMock):
                    # Each convert call with max_retries=2 records 3 failures
                    # Need 4 calls (12 failures > threshold of 10) to trip breaker
                    for _ in range(4):
                        with pytest.raises(httpx.ConnectError):
                            await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        # Circuit breaker should now be open
        assert _CIRCUIT_BREAKER.is_open

        with pytest.raises(CircuitBreakerOpenError):
            await client.convert(b"%PDF-test", "test.pdf")

    @pytest.mark.asyncio
    async def test_504_not_retried(self, client: DoclingServeClient):
        """504 (gateway timeout) is not retried — document is too complex."""
        error_response = httpx.Response(504, request=httpx.Request("POST", "http://test:5001/v1/convert/file"))

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError("504", request=error_response.request, response=error_response)

        with patch.object(client._client, "post", side_effect=side_effect):
            with pytest.raises(httpx.HTTPStatusError):
                await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert call_count == 1  # No retries on 504

    @pytest.mark.asyncio
    async def test_circuit_breaker_checked_before_semaphore(self, client: DoclingServeClient):
        """Circuit breaker check happens before semaphore acquisition."""
        # Trip the circuit breaker
        for _ in range(10):
            _CIRCUIT_BREAKER.record_failure()

        assert _CIRCUIT_BREAKER.is_open

        # Should fail immediately without touching the semaphore
        with pytest.raises(CircuitBreakerOpenError):
            await client.convert(b"%PDF-test", "test.pdf")


# ---------------------------------------------------------------------------
# Client — health
# ---------------------------------------------------------------------------


class TestDoclingServeClientHealth:
    """Tests for the health check."""

    @pytest.fixture
    def client(self):
        return DoclingServeClient("http://test:5001", timeout=10.0)

    @pytest.mark.asyncio
    async def test_healthy(self, client: DoclingServeClient):
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test:5001/health"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            assert await client.check_health() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, client: DoclingServeClient):
        mock_response = httpx.Response(503, request=httpx.Request("GET", "http://test:5001/health"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            assert await client.check_health() is False

    @pytest.mark.asyncio
    async def test_connection_error(self, client: DoclingServeClient):
        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            assert await client.check_health() is False


# ---------------------------------------------------------------------------
# Module-level lifecycle
# ---------------------------------------------------------------------------


class TestModuleLifecycle:
    """Tests for init/get/close lifecycle helpers."""

    def test_get_before_init_raises(self):
        with pytest.raises(RuntimeError, match="not initialized"):
            get_docling_client()

    def test_init_and_get(self):
        client = init_docling_client("http://test:5001", timeout=30.0)
        assert get_docling_client() is client

    @pytest.mark.asyncio
    async def test_close(self):
        init_docling_client("http://test:5001")
        await close_docling_client()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_docling_client()
