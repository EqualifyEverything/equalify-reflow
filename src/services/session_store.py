"""In-memory session store for pipeline feedback sessions.

Each session holds the pipeline result, structure analysis output, and
feedback state for iterative human review rounds.  Sessions expire after
a configurable TTL (default 1 hour).

This is appropriate for the Pipeline Viewer dev tool.  Production usage
would swap for Redis-backed persistence.
"""

from __future__ import annotations

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

SESSION_TTL_SECONDS = 3600  # 1 hour


@dataclass
class PipelineSession:
    """State for a single feedback session."""

    session_id: str
    result: PipelineViewerResult
    structure: StructureResult | None = None
    section_map: SectionMap | None = None
    feedback_history: list[list[FeedbackItem]] = field(default_factory=list)
    candidate_changes: list[CandidateChange] = field(default_factory=list)
    revision_round: int = 0
    finalized: bool = False
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update the last-accessed timestamp."""
        self.last_accessed = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_accessed) > SESSION_TTL_SECONDS


class SessionStore:
    """Thread-safe in-memory store for pipeline sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PipelineSession] = {}

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
        """Remove all expired sessions."""
        expired = [
            sid for sid, s in self._sessions.items() if s.is_expired
        ]
        for sid in expired:
            del self._sessions[sid]

    @property
    def count(self) -> int:
        """Number of active (non-expired) sessions."""
        self._evict_expired()
        return len(self._sessions)


# Module-level singleton
session_store = SessionStore()
