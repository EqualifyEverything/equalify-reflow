"""Integration tests for stream token authentication.

These tests verify the stream token API contract and security behavior.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app


@pytest.fixture
def mock_job_service():
    """Mock job service for tests."""
    mock = AsyncMock()
    mock.get_job.return_value = {
        "job_id": "test-job-123",
        "status": "processing",
        "filename": "test.pdf",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }
    mock.create_stream_token.return_value = "test-stream-token-abcdefghijklmnopqrstuvwxyz123456"
    mock.validate_and_consume_stream_token.return_value = "test-job-123"
    return mock


@pytest.mark.integration
class TestStreamTokenCreation:
    """Tests for stream token creation endpoint."""

    @pytest.mark.asyncio
    async def test_create_stream_token_returns_valid_response(self, mock_job_service):
        """Test successful stream token creation returns expected response."""
        from src.dependencies import get_job_service

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key-123"},
            ) as client:
                response = await client.post("/api/v1/documents/test-job-123/stream/token")

                assert response.status_code == 200
                data = response.json()
                assert "token" in data
                assert data["expires_in_seconds"] == 300
                assert "stream_url" in data
                assert "/stream?token=" in data["stream_url"]
                assert "test-job-123" in data["stream_url"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_stream_token_returns_404_for_nonexistent_job(self):
        """Test token creation returns 404 if job doesn't exist."""
        from src.dependencies import get_job_service

        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = None  # Job not found

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key-123"},
            ) as client:
                response = await client.post("/api/v1/documents/nonexistent-job/stream/token")

                assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_create_stream_token_calls_job_service(self, mock_job_service):
        """Test that token creation calls the job service correctly."""
        from src.dependencies import get_job_service

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-API-Key": "test-api-key-123"},
            ) as client:
                await client.post("/api/v1/documents/test-job-123/stream/token")

                # Verify job service methods were called
                mock_job_service.get_job.assert_called_once_with("test-job-123")
                mock_job_service.create_stream_token.assert_called_once_with("test-job-123")
        finally:
            app.dependency_overrides.clear()


@pytest.mark.integration
class TestStreamTokenValidation:
    """Tests for stream endpoint with token authentication."""

    @pytest.mark.asyncio
    async def test_stream_rejects_invalid_token(self):
        """Test that stream endpoint rejects invalid/expired token."""
        from src.dependencies import get_job_service

        mock_job_service = AsyncMock()
        mock_job_service.validate_and_consume_stream_token.return_value = None  # Invalid token
        mock_job_service.get_job.return_value = {"job_id": "test-job-123", "status": "processing"}

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                # Invalid token (but valid format to pass middleware)
                token = "invalid-expired-token-abcdefghijklmnopqrstuvwxyz"
                response = await client.get(f"/api/v1/documents/test-job-123/stream?token={token}")

                assert response.status_code == 401
                detail = response.json()["detail"].lower()
                assert "expired" in detail or "invalid" in detail or "already-used" in detail
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_rejects_wrong_job_token(self):
        """Test that token scoped to one job cannot be used for another."""
        from src.dependencies import get_job_service

        mock_job_service = AsyncMock()
        # Token is valid but for a different job
        mock_job_service.validate_and_consume_stream_token.return_value = "different-job-456"
        mock_job_service.get_job.return_value = {"job_id": "test-job-123", "status": "processing"}

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                token = "valid-but-wrong-job-token-abcdefghijklmnopqrst"
                response = await client.get(f"/api/v1/documents/test-job-123/stream?token={token}")

                assert response.status_code == 401
                assert "not valid for this job" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_consumes_token_on_valid_request(self, mock_job_service):
        """Test that valid token is consumed (single-use)."""
        from src.dependencies import get_job_service

        # Mock event bus to avoid streaming issues
        with patch("src.agents.events.get_event_bus") as mock_get_event_bus:
            mock_event_bus = MagicMock()
            mock_event_bus.events = []
            mock_get_event_bus.return_value = mock_event_bus

            app.dependency_overrides[get_job_service] = lambda: mock_job_service

            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    token = "valid-stream-token-abcdefghijklmnopqrstuvwxyz123"
                    response = await client.get(
                        f"/api/v1/documents/test-job-123/stream?token={token}",
                        timeout=2.0,
                    )

                    # Verify token was validated
                    mock_job_service.validate_and_consume_stream_token.assert_called_once_with(token)

                    # Should not be 401 (auth passed)
                    assert response.status_code != 401
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_works_with_api_key_header(self, mock_job_service):
        """Test that API key header auth still works for stream endpoint."""
        from src.dependencies import get_job_service

        # Mock event bus
        with patch("src.agents.events.get_event_bus") as mock_get_event_bus:
            mock_event_bus = MagicMock()
            mock_event_bus.events = []
            mock_get_event_bus.return_value = mock_event_bus

            app.dependency_overrides[get_job_service] = lambda: mock_job_service

            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                    headers={"X-API-Key": "test-api-key-123"},  # Use API key, no token
                ) as client:
                    response = await client.get(
                        "/api/v1/documents/test-job-123/stream",
                        timeout=2.0,
                    )

                    # Should work with API key
                    assert response.status_code != 401

                    # Token validation should NOT be called when using API key
                    mock_job_service.validate_and_consume_stream_token.assert_not_called()
            finally:
                app.dependency_overrides.clear()


@pytest.mark.integration
class TestStreamTokenMiddleware:
    """Tests for middleware stream token bypass behavior."""

    @pytest.mark.asyncio
    async def test_short_token_does_not_bypass_middleware(self):
        """Test that tokens shorter than 40 chars don't bypass middleware."""
        from src.dependencies import get_job_service

        mock_job_service = AsyncMock()
        mock_job_service.validate_and_consume_stream_token.return_value = None
        mock_job_service.get_job.return_value = {"job_id": "test-job", "status": "processing"}

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                # No API key header
            ) as client:
                # Short token should not bypass API key auth requirement
                response = await client.get("/api/v1/documents/test-job/stream?token=short")

                # Should fail auth (either 401 from middleware or token validation)
                assert response.status_code in [401, 403]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_middleware_only_checks_stream_endpoints(self):
        """Test that _is_stream_token_request only matches /stream paths."""
        from unittest.mock import MagicMock

        from src.middleware.api_key_auth import APIKeyAuthMiddleware

        middleware = MagicMock(spec=APIKeyAuthMiddleware)
        middleware._is_stream_token_request = APIKeyAuthMiddleware._is_stream_token_request

        # Create mock request for non-stream endpoint with token
        non_stream_request = MagicMock()
        non_stream_request.url.path = "/api/v1/documents/test-job"
        non_stream_request.query_params.get.return_value = "valid-looking-token-abcdefghijklmnopqrstuvwxyz123"

        # Non-stream endpoint should NOT be treated as stream token request
        result = middleware._is_stream_token_request(middleware, non_stream_request)
        assert result is False

        # Create mock request for stream endpoint with token
        stream_request = MagicMock()
        stream_request.url.path = "/api/v1/documents/test-job/stream"
        stream_request.query_params.get.return_value = "valid-looking-token-abcdefghijklmnopqrstuvwxyz123"

        # Stream endpoint with valid token SHOULD be treated as stream token request
        result = middleware._is_stream_token_request(middleware, stream_request)
        assert result is True
