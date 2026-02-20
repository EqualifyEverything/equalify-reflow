"""Unit tests for the in-memory session store."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from src.services.pipeline_viewer_models import (
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    SectionMap,
    StepResult,
    StructureResult,
)
from src.services.session_store import (
    SESSION_TTL_SECONDS,
    PipelineSession,
    SessionStore,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    return SessionStore()


@pytest.fixture
def sample_result():
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Page 1\n\nHello"},
        page_markdowns={"v0": {"1": "# Page 1", "2": "Hello"}},
        steps=[
            StepResult(
                name="docling",
                display_name="Docling",
                version_after="v0",
                elapsed_ms=100,
            )
        ],
    )


@pytest.fixture
def sample_structure():
    return StructureResult(
        page_attributes={1: PageAttributes(layout=LayoutType.SINGLE_COLUMN)},
        outline=[OutlineEntry(level=1, text="Page 1", page=1)],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionStore:
    def test_create_returns_session(self, store, sample_result):
        session = store.create(sample_result)
        assert isinstance(session, PipelineSession)
        assert session.session_id
        assert session.result is sample_result
        assert session.revision_round == 0
        assert not session.finalized

    def test_get_existing_session(self, store, sample_result):
        session = store.create(sample_result)
        retrieved = store.get(session.session_id)
        assert retrieved is session

    def test_get_nonexistent_returns_none(self, store):
        assert store.get("nonexistent") is None

    def test_delete_existing(self, store, sample_result):
        session = store.create(sample_result)
        assert store.delete(session.session_id) is True
        assert store.get(session.session_id) is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("nonexistent") is False

    def test_ttl_expiry(self, store, sample_result):
        session = store.create(sample_result)
        # Simulate time passage beyond TTL
        with patch("src.services.session_store.time.time", return_value=time.time() + SESSION_TTL_SECONDS + 1):
            assert store.get(session.session_id) is None

    def test_multiple_sessions(self, store, sample_result):
        s1 = store.create(sample_result)
        s2 = store.create(sample_result)
        assert s1.session_id != s2.session_id
        assert store.count == 2

    def test_session_fields_accessible(self, store, sample_result, sample_structure):
        session = store.create(
            sample_result,
            structure=sample_structure,
            section_map=SectionMap(),
        )
        assert session.structure is sample_structure
        assert session.section_map is not None
        assert session.feedback_history == []
        assert session.candidate_changes == []
