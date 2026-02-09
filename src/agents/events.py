"""Event bus registry for job streaming.

Minimal stub — the full event system was removed with the agentic pipeline.
Only the registry functions remain for documents.py SSE compatibility.
"""

from __future__ import annotations

from typing import Any

# Global event bus registry (job_id -> event bus)
_event_registry: dict[str, Any] = {}


def register_event_bus(job_id: str, event_bus: Any) -> None:
    """Register an event bus for a job."""
    _event_registry[job_id] = event_bus


def get_event_bus(job_id: str) -> Any | None:
    """Get the event bus for a job (returns None if not registered)."""
    return _event_registry.get(job_id)


def unregister_event_bus(job_id: str) -> None:
    """Unregister an event bus for a job."""
    _event_registry.pop(job_id, None)
