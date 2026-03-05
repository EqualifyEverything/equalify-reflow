"""In-memory session store for pipeline feedback sessions.

Each session holds the pipeline result, structure analysis output, and
feedback state for iterative human review rounds.  Sessions expire after
a configurable TTL (default 1 hour).

This is appropriate for the Pipeline Viewer dev tool.  Production usage
would swap for Redis-backed persistence.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from .pipeline_viewer_models import (
    CandidateChange,
    FeedbackItem,
    PipelineViewerResult,
    SectionMap,
    StructureResult,
)

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 3600  # 1 hour


@dataclass
class PipelineSession:
    """State for a single feedback session."""

    session_id: str
    result: PipelineViewerResult
    structure: StructureResult | None = None
    section_map: SectionMap | None = None
    document_ref: str | None = None
    feedback_history: list[list[FeedbackItem]] = field(default_factory=list)
    candidate_changes: list[CandidateChange] = field(default_factory=list)
    revision_round: int = 0
    finalized: bool = False
    processing: bool = False
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    # SSE reconnect support
    event_buffer: list[str] = field(default_factory=list)
    status: str = "processing"  # "processing" | "completed" | "error"
    event_counter: int = 0
    # Push notification for buffer readers
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Background pipeline task reference
    pipeline_task: asyncio.Task | None = None

    def touch(self) -> None:
        """Update the last-accessed timestamp."""
        self.last_accessed = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_accessed) > SESSION_TTL_SECONDS


class SessionStore:
    """In-memory store for pipeline sessions.

    Safe for single-threaded asyncio usage.  Not thread-safe — do not
    access from sync background threads without external locking.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PipelineSession] = {}

    def create_for_stream(self, filename: str) -> PipelineSession:
        """Create a session early for SSE reconnect support.

        The session starts with an empty result and status="processing".
        The caller populates result/structure/section_map as processing completes.
        """
        self._evict_expired()
        session_id = uuid.uuid4().hex[:12]
        session = PipelineSession(
            session_id=session_id,
            result=PipelineViewerResult(filename=filename, total_pages=0),
            status="processing",
        )
        self._sessions[session_id] = session
        return session

    def create(
        self,
        result: PipelineViewerResult,
        structure: StructureResult | None = None,
        section_map: SectionMap | None = None,
    ) -> PipelineSession:
        """Create a new session and return it."""
        self._evict_expired()
        session_id = uuid.uuid4().hex[:12]
        session = PipelineSession(
            session_id=session_id,
            result=result,
            structure=structure,
            section_map=section_map,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> PipelineSession | None:
        """Retrieve a session by ID, or None if not found / expired."""
        self._evict_expired()
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            del self._sessions[session_id]
            return None
        session.touch()
        return session

    def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        return self._sessions.pop(session_id, None) is not None

    def _evict_expired(self) -> None:
        """Remove all expired sessions and cancel their pipeline tasks."""
        expired = [
            sid for sid, s in self._sessions.items() if s.is_expired
        ]
        for sid in expired:
            session = self._sessions.pop(sid)
            if session.pipeline_task and not session.pipeline_task.done():
                session.pipeline_task.cancel()
                logger.debug("Cancelled pipeline task for expired session %s", sid)

    @property
    def count(self) -> int:
        """Number of active (non-expired) sessions."""
        self._evict_expired()
        return len(self._sessions)


# Module-level singleton
session_store = SessionStore()
