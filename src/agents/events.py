"""Event bus registry for job streaming.

Provides an EventBus for publishing pipeline events to SSE subscribers,
plus Pydantic event models for the document stream contract.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel


# ── Event models ────────────────────────────────────────────────


class PipelinePhaseEvent(BaseModel):
    """Emitted when a pipeline phase starts."""

    event_type: str = "pipeline:phase"
    phase: str
    display_name: str
    step_number: int
    total_steps: int


class ProcessingCompleteEvent(BaseModel):
    """Emitted when processing finishes successfully."""

    event_type: str = "processing:complete"


class ProcessingErrorEvent(BaseModel):
    """Emitted when processing fails."""

    event_type: str = "processing:error"
    error: str


# ── EventBus ────────────────────────────────────────────────────


class EventBus:
    """Publish-subscribe event bus backed by asyncio queues.

    Events are stored in `.events` for replay to late-connecting subscribers.
    """

    def __init__(self) -> None:
        self.events: list[BaseModel] = []
        self._subscribers: list[asyncio.Queue[BaseModel]] = []

    def publish(self, event: BaseModel) -> None:
        """Store event and push to all subscribers."""
        self.events.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[BaseModel]:
        """Create and return a new subscriber queue."""
        queue: asyncio.Queue[BaseModel] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[BaseModel]) -> None:
        """Remove a subscriber queue."""
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass


# ── Global registry (job_id → EventBus) ────────────────────────

_event_registry: dict[str, EventBus] = {}


def register_event_bus(job_id: str, event_bus: EventBus) -> None:
    """Register an event bus for a job."""
    _event_registry[job_id] = event_bus


def get_event_bus(job_id: str) -> EventBus | None:
    """Get the event bus for a job (returns None if not registered)."""
    return _event_registry.get(job_id)


def unregister_event_bus(job_id: str) -> None:
    """Unregister an event bus for a job."""
    _event_registry.pop(job_id, None)
