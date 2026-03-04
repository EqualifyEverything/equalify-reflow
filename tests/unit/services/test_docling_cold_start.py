"""Tests for docling-serve cold start handling (PRD-1).

Verifies 503 handling, ConnectError health-polling, and circuit breaker
tuning for the decoupled docling service behind an internal ALB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.docling_serve_client import (
    DoclingServeClient,
    _CIRCUIT_BREAKER,
    reset_docling_circuit_breaker,
)
from src.utils.circuit_breaker import CircuitBreakerOpenError


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    reset_docling_circuit_breaker()
    yield
    reset_docling_circuit_breaker()


@pytest.fixture
def client():
    return DoclingServeClient("http://test:5001", timeout=10.0)


class TestColdStart503:
    """Tests for 503 (ALB no healthy targets) handling."""

    @pytest.mark.asyncio
    async def test_503_triggers_wait_for_healthy(self, client: DoclingServeClient):
        """503 from ALB triggers health poll with 300s timeout."""
        error_response = httpx.Response(503, request=httpx.Request("POST", "http://test:5001/v1/convert/file"))
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
                raise httpx.HTTPStatusError("503", request=error_response.request, response=error_response)
            return ok_response

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch.object(client, "_wait_for_healthy", new_callable=AsyncMock) as mock_wait:
                result = await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert result.md_content == "ok"
        mock_wait.assert_called_once_with(timeout=300.0)

    @pytest.mark.asyncio
    async def test_503_exhausts_retries(self, client: DoclingServeClient):
        """503 raises after exhausting retries."""
        error_response = httpx.Response(503, request=httpx.Request("POST", "http://test:5001/v1/convert/file"))

        async def side_effect(*args, **kwargs):
            raise httpx.HTTPStatusError("503", request=error_response.request, response=error_response)

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch.object(client, "_wait_for_healthy", new_callable=AsyncMock):
                with pytest.raises(httpx.HTTPStatusError):
                    await client.convert(b"%PDF-test", "test.pdf", max_retries=1)


class TestConnectError:
    """Tests for ConnectError (server unreachable) handling."""

    @pytest.mark.asyncio
    async def test_connect_error_triggers_wait_for_healthy(self, client: DoclingServeClient):
        """ConnectError triggers health poll with 300s timeout."""
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
                raise httpx.ConnectError("refused")
            return ok_response

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch.object(client, "_wait_for_healthy", new_callable=AsyncMock) as mock_wait:
                result = await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert result.md_content == "ok"
        mock_wait.assert_called_once_with(timeout=300.0)

    @pytest.mark.asyncio
    async def test_remote_protocol_error_triggers_wait_for_healthy(self, client: DoclingServeClient):
        """RemoteProtocolError (server crash) triggers health poll."""
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
                raise httpx.RemoteProtocolError("peer closed connection")
            return ok_response

        with patch.object(client._client, "post", side_effect=side_effect):
            with patch.object(client, "_wait_for_healthy", new_callable=AsyncMock) as mock_wait:
                result = await client.convert(b"%PDF-test", "test.pdf", max_retries=2)

        assert result.md_content == "ok"
        mock_wait.assert_called_once_with(timeout=300.0)


class TestCircuitBreakerTuning:
    """Tests for PRD-1 circuit breaker configuration."""

    def test_circuit_breaker_threshold_is_10(self):
        """Breaker trips after 10 failures (not 3 like old sidecar config)."""
        assert _CIRCUIT_BREAKER.config.failure_threshold == 10

    def test_circuit_breaker_timeout_360s(self):
        """Breaker half-opens after 360s (model loading time)."""
        assert _CIRCUIT_BREAKER.config.timeout == 360.0

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_10_failures(self, client: DoclingServeClient):
        """Circuit breaker opens after accumulating 10 failures."""
        # Record exactly 9 failures — should still be closed
        for _ in range(9):
            _CIRCUIT_BREAKER.record_failure()
        assert not _CIRCUIT_BREAKER.is_open

        # 10th failure trips it
        _CIRCUIT_BREAKER.record_failure()
        assert _CIRCUIT_BREAKER.is_open

        with pytest.raises(CircuitBreakerOpenError):
            await client.convert(b"%PDF-test", "test.pdf")
