"""Unit tests for the decoupled pipeline SSE architecture.

Tests the _buffer_reader generator and session-based pipeline decoupling.
"""

import asyncio

import pytest

from src.api.pipeline_viewer import (
    _HEARTBEAT_INTERVAL_SECONDS,
    _SSE_HEARTBEAT,
    _buffer_reader,
    _sse_event,
)
from src.services.pipeline_viewer_models import PipelineViewerResult
from src.services.session_store import PipelineSession, SessionStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> PipelineSession:
    """Create a fresh PipelineSession for testing."""
    return PipelineSession(
        session_id="test123",
        result=PipelineViewerResult(filename="test.pdf", total_pages=0),
        status="processing",
    )


# ---------------------------------------------------------------------------
# _buffer_reader tests
# ---------------------------------------------------------------------------


class TestBufferReader:
    @pytest.mark.asyncio
    async def test_drains_completed_session(self, session: PipelineSession):
        """Reader yields all buffered events and stops when session is completed."""
        session.event_buffer = [
            _sse_event("session", {"session_id": "test123"}, event_id=0),
            _sse_event("init", {"filename": "test.pdf"}, event_id=1),
            _sse_event("done", {"total_steps": 0}, event_id=2),
        ]
        session.status = "completed"

        events = []
        async for event in _buffer_reader(session, cursor=0):
            events.append(event)

        assert len(events) == 3
        assert "session" in events[0]
        assert "done" in events[2]

    @pytest.mark.asyncio
    async def test_respects_cursor(self, session: PipelineSession):
        """Reader skips events before the cursor position."""
        session.event_buffer = [
            _sse_event("session", {"session_id": "test123"}, event_id=0),
            _sse_event("init", {"filename": "test.pdf"}, event_id=1),
            _sse_event("step", {"step": "structure"}, event_id=2),
            _sse_event("done", {"total_steps": 1}, event_id=3),
        ]
        session.status = "completed"

        events = []
        async for event in _buffer_reader(session, cursor=2):
            events.append(event)

        assert len(events) == 2
        assert "step" in events[0]
        assert "done" in events[1]

    @pytest.mark.asyncio
    async def test_waits_for_new_events(self, session: PipelineSession):
        """Reader waits for new events when buffer is empty."""
        events_received = []

        async def producer():
            await asyncio.sleep(0.05)
            session.event_buffer.append(
                _sse_event("init", {"filename": "test.pdf"}, event_id=0)
            )
            session.new_event.set()
            await asyncio.sleep(0.05)
            session.event_buffer.append(
                _sse_event("done", {"total_steps": 0}, event_id=1)
            )
            session.status = "completed"
            session.new_event.set()

        async def consumer():
            async for event in _buffer_reader(session, cursor=0):
                events_received.append(event)

        await asyncio.gather(producer(), consumer())

        assert len(events_received) == 2
        assert "init" in events_received[0]
        assert "done" in events_received[1]

    @pytest.mark.asyncio
    async def test_heartbeat_on_timeout(self, session: PipelineSession, monkeypatch):
        """Reader sends heartbeat when no new events arrive within timeout."""
        monkeypatch.setattr(
            "src.api.pipeline_viewer._HEARTBEAT_INTERVAL_SECONDS", 0.1,
        )

        heartbeat_received = False

        async def complete_later():
            await asyncio.sleep(0.3)
            session.status = "completed"
            session.new_event.set()

        task = asyncio.create_task(complete_later())

        async for event in _buffer_reader(session, cursor=0):
            if event == _SSE_HEARTBEAT:
                heartbeat_received = True
                break

        # Clean up
        session.status = "completed"
        session.new_event.set()
        await task

        assert heartbeat_received

    @pytest.mark.asyncio
    async def test_stops_on_error_status(self, session: PipelineSession):
        """Reader stops when session status is 'error'."""
        session.event_buffer = [
            _sse_event("error", {"message": "failed"}, event_id=0),
        ]
        session.status = "error"

        events = []
        async for event in _buffer_reader(session, cursor=0):
            events.append(event)

        assert len(events) == 1


# ---------------------------------------------------------------------------
# Session field tests
# ---------------------------------------------------------------------------


class TestSessionFields:
    def test_new_event_is_asyncio_event(self, session: PipelineSession):
        """PipelineSession should have an asyncio.Event for push notification."""
        assert isinstance(session.new_event, asyncio.Event)
        assert not session.new_event.is_set()

    def test_pipeline_task_defaults_to_none(self, session: PipelineSession):
        """PipelineSession should have pipeline_task defaulting to None."""
        assert session.pipeline_task is None

    def test_event_counter_defaults_to_zero(self, session: PipelineSession):
        """PipelineSession should have event_counter defaulting to 0."""
        assert session.event_counter == 0


# ---------------------------------------------------------------------------
# Session eviction tests
# ---------------------------------------------------------------------------


class TestSessionEviction:
    def test_evict_cancels_pipeline_task(self):
        """Evicting an expired session should cancel its pipeline task."""
        store = SessionStore()
        session = store.create_for_stream("test.pdf")

        # Create a mock task
        future = asyncio.get_event_loop().create_future()
        task = asyncio.ensure_future(future)
        session.pipeline_task = task

        # Force expiry
        session.last_accessed = 0

        # Evict
        store._evict_expired()

        assert task.cancelled()
        assert store.count == 0

    def test_evict_skips_completed_tasks(self):
        """Evicting should not cancel already-completed pipeline tasks."""
        store = SessionStore()
        session = store.create_for_stream("test.pdf")

        # Create a completed task
        future = asyncio.get_event_loop().create_future()
        future.set_result(None)
        task = asyncio.ensure_future(future)
        session.pipeline_task = task

        # Force expiry
        session.last_accessed = 0

        # Should not raise
        store._evict_expired()
        assert store.count == 0
