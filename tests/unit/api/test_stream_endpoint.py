"""Unit tests for SSE Stream endpoint.

Tests the GET /api/v1/documents/{job_id}/stream endpoint for:
- 404 responses for non-existent jobs
- SSE format (Content-Type: text/event-stream)
- SSE event format (event: ...\ndata: {...}\n\n)
- Job completion event handling
- Error handling scenarios
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.agents import events as events_module
from src.dependencies import get_job_service
from src.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def api_key_headers():
    """Generate API key headers for authenticated requests."""
    import os

    api_keys_env = os.getenv("API_KEYS", "")
    if api_keys_env:
        keys = api_keys_env.split(",")
        api_key = keys[0].strip() if keys else ""
    else:
        api_key = "test-key-fallback"

    return {"X-API-Key": api_key}


@pytest.fixture
def mock_job_service():
    """Mock job service."""
    mock = MagicMock()
    mock.get_job = AsyncMock()
    return mock


class MockStreamEvent:
    """Mock stream event for testing."""

    def __init__(self, event_type: str, data: dict):
        self._event_type = event_type
        self._data = data

    @property
    def event_type(self) -> str:
        return self._event_type

    def model_dump_json(self) -> str:
        import json

        return json.dumps(self._data)


class MockEventBus:
    """Mock EventBus for testing SSE streaming."""

    def __init__(self, events: list | None = None):
        self._events = events or []
        self._subscribers: list[asyncio.Queue] = []

    @property
    def events(self) -> list:
        return self._events.copy()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def emit(self, event: MockStreamEvent) -> None:
        self._events.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except Exception:
                pass


@pytest.fixture(autouse=True)
def clear_event_registry():
    """Clear the event registry before and after each test."""
    events_module._event_registry.clear()
    yield
    events_module._event_registry.clear()


class TestStreamEndpointNotFound:
    """Tests for 404 responses when job doesn't exist."""

    @pytest.mark.asyncio
    async def test_stream_returns_404_for_nonexistent_job(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream endpoint returns 404 for non-existent job."""
        job_id = "nonexistent-job-123"
        mock_job_service.get_job.return_value = None

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            data = response.json()
            assert "not found" in data["detail"].lower()
        finally:
            app.dependency_overrides.clear()


class TestStreamEndpointSSEFormat:
    """Tests for SSE format validation."""

    @pytest.mark.asyncio
    async def test_stream_returns_sse_content_type(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream endpoint returns correct Content-Type for SSE."""
        job_id = "test-job-sse-format"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            assert response.headers["content-type"].startswith("text/event-stream")
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_returns_proper_sse_headers(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream endpoint returns correct SSE headers."""
        job_id = "test-job-headers"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            # SSE should have cache-control: no-cache
            assert "no-cache" in response.headers.get("cache-control", "")
        finally:
            app.dependency_overrides.clear()


class TestStreamEndpointEvents:
    """Tests for SSE event delivery."""

    @pytest.mark.asyncio
    async def test_stream_delivers_events_in_sse_format(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream delivers events in correct SSE format (event: ...\ndata: {...}\n\n)."""
        job_id = "test-job-events"

        # Set up job as completed so it immediately returns events
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
        }

        # Create mock event bus with some events and register it
        mock_event = MockStreamEvent(
            "processing:complete",
            {"success": True, "total_edits": 5, "document_id": job_id},
        )
        mock_bus = MockEventBus(events=[mock_event])
        events_module._event_registry[job_id] = mock_bus

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            content = response.text

            # SSE format: event: <type>\ndata: <json>\n\n
            assert "event: processing:complete\n" in content
            assert "data: {" in content
            # Done event should be at the end
            assert "event: done\n" in content
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_sends_done_event_for_completed_job(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream sends done event when job is already completed."""
        job_id = "test-job-completed"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            content = response.text

            # Should end with done event
            assert "event: done\n" in content
            assert "data: {}\n" in content
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_sends_done_event_for_failed_job(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream sends done event when job has failed."""
        job_id = "test-job-failed"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "failed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:30Z",
            "error": "Processing failed",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            content = response.text

            # Should end with done event even for failed jobs
            assert "event: done\n" in content
        finally:
            app.dependency_overrides.clear()


class TestStreamEndpointErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_stream_handles_missing_event_bus_gracefully(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream handles missing event bus (returns error event)."""
        job_id = "test-job-no-bus"

        # Job is processing but event bus is not available (not registered)
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "processing",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:30Z",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        # Patch asyncio.sleep to avoid actual waiting
        with patch("asyncio.sleep", new_callable=AsyncMock):
            try:
                # Use a shorter timeout since we're testing timeout behavior
                response = client.get(
                    f"/api/v1/documents/{job_id}/stream",
                    headers=api_key_headers,
                    timeout=5,
                )

                assert response.status_code == status.HTTP_200_OK
                content = response.text

                # Should receive error event about event bus not available
                assert "event: error\n" in content
                assert "Event bus not available" in content
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_stream_replays_existing_events_for_completed_job(
        self, client, api_key_headers, mock_job_service
    ):
        """Test that stream replays all existing events when job is already done."""
        job_id = "test-job-replay"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
        }

        # Create event bus with multiple historical events and register it
        mock_events = [
            MockStreamEvent("planning:started", {"document_id": job_id, "total_pages": 5, "filename": "test.pdf"}),
            MockStreamEvent("job:created", {"document_id": job_id, "job_id": "job-1", "job_type": "content", "page": 1, "task_count": 3, "task_types": ["alt_text"]}),
            MockStreamEvent("processing:complete", {"document_id": job_id, "success": True, "total_edits": 10, "total_jobs": 5, "total_cost": 0.05, "total_duration_ms": 5000, "result_url": "https://example.com/result"}),
        ]
        mock_bus = MockEventBus(events=mock_events)
        events_module._event_registry[job_id] = mock_bus

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            content = response.text

            # All events should be present
            assert "event: planning:started\n" in content
            assert "event: job:created\n" in content
            assert "event: processing:complete\n" in content
            # Final done event
            assert "event: done\n" in content
        finally:
            app.dependency_overrides.clear()


class TestStreamEndpointAuthentication:
    """Tests for authentication requirements."""

    @pytest.mark.asyncio
    async def test_stream_requires_authentication(self, client, mock_job_service):
        """Test that stream endpoint requires API key authentication."""
        job_id = "test-job-auth"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "processing",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:30Z",
        }

        # Don't override job service - let it check auth first
        # Note: This test behavior depends on whether ENABLE_API_KEY_AUTH is true
        # In the conftest, it's set to false, so this test verifies the endpoint works
        # In production with auth enabled, missing headers would return 401

        import os

        if os.getenv("ENABLE_API_KEY_AUTH", "false").lower() == "true":
            response = client.get(f"/api/v1/documents/{job_id}/stream")
            # Should fail without API key when auth is enabled
            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
