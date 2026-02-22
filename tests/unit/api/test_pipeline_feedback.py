"""Unit tests for pipeline feedback API endpoints.

Tests the /api/v1/pipeline/sessions endpoints using FastAPI TestClient.
Session store is mocked to inject test sessions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.services.pipeline_viewer_models import (
    CandidateChange,
    DocumentChange,
    PipelineViewerResult,
    StepResult,
)
from src.services.session_store import PipelineSession, SessionStore

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_result():
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Page 1\n\nHello world\n\n# Page 2\n\nGoodbye"},
        page_markdowns={
            "v0": {
                "1": "# Page 1\n\nHello world",
                "2": "# Page 2\n\nGoodbye",
            }
        },
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
def mock_session(sample_result):
    return PipelineSession(
        session_id="test123",
        result=sample_result,
    )


@pytest.fixture
def mock_store(mock_session):
    """Patch the module-level session_store to return a controlled session."""
    store = SessionStore()
    store._sessions[mock_session.session_id] = mock_session
    with patch("src.api.pipeline_feedback.session_store", store):
        yield store


# ---------------------------------------------------------------------------
# GET /state
# ---------------------------------------------------------------------------


class TestGetSessionState:
    def test_returns_state(self, client, mock_store, mock_session):
        resp = client.get(f"/api/v1/pipeline/sessions/{mock_session.session_id}/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test123"
        assert data["revision_round"] == 0
        assert data["finalized"] is False
        assert "v0" in data["versions"]
        assert data["pending_candidates"] == 0

    def test_404_for_nonexistent(self, client, mock_store):
        resp = client.get("/api/v1/pipeline/sessions/nonexistent/state")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /feedback
# ---------------------------------------------------------------------------


class TestSubmitFeedback:
    def test_returns_candidates(self, client, mock_store, mock_session):
        candidates = [
            CandidateChange(
                id="c1",
                change=DocumentChange(
                    page=1, old_text="Hello world", new_text="Hi",
                    reasoning="shorten", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="e1",
                source_description="test",
            )
        ]

        with patch(
            "src.api.pipeline_feedback.PipelineViewerService"
        ) as mock_service_cls:
            instance = mock_service_cls.return_value
            instance.process_feedback_batch = AsyncMock(return_value=candidates)

            resp = client.post(
                f"/api/v1/pipeline/sessions/{mock_session.session_id}/feedback",
                json={
                    "items": [
                        {
                            "id": "e1",
                            "type": "edit",
                            "selector": {"exact": "Hello world"},
                            "new_text": "Hi",
                            "description": "shorten",
                        }
                    ]
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["candidates"]) == 1
        assert data["revision_round"] == 1

    def test_409_when_finalized(self, client, mock_store, mock_session):
        mock_session.finalized = True
        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/feedback",
            json={
                "items": [
                    {
                        "id": "e1",
                        "type": "edit",
                        "selector": {"exact": "text"},
                        "new_text": "new",
                    }
                ]
            },
        )
        assert resp.status_code == 409

    def test_404_for_nonexistent(self, client, mock_store):
        resp = client.post(
            "/api/v1/pipeline/sessions/nonexistent/feedback",
            json={
                "items": [
                    {"id": "e1", "type": "edit", "selector": {"exact": "x"}, "new_text": "y"}
                ]
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /review
# ---------------------------------------------------------------------------


class TestReviewChanges:
    def test_applies_accepted_changes(self, client, mock_store, mock_session):
        # Pre-populate candidates
        mock_session.candidate_changes = [
            CandidateChange(
                id="c1",
                change=DocumentChange(
                    page=1, old_text="Hello world", new_text="Hi",
                    reasoning="r", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="e1",
                source_description="d",
            ),
        ]
        mock_session.revision_round = 1

        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/review",
            json={
                "reviews": [{"change_id": "c1", "decision": "accept"}],
                "action": "request_changes",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied_count"] == 1
        assert data["new_version"] == "v1"
        assert data["finalized"] is False

    def test_reject_with_comment(self, client, mock_store, mock_session):
        mock_session.candidate_changes = [
            CandidateChange(
                id="c1",
                change=DocumentChange(
                    page=1, old_text="Hello world", new_text="Hi",
                    reasoning="r", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="e1",
                source_description="d",
            ),
        ]
        mock_session.revision_round = 1

        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/review",
            json={
                "reviews": [
                    {"change_id": "c1", "decision": "reject", "comment": "Don't like it"}
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rejected_count"] == 1
        assert "Don't like it" in data["rejection_comments"]

    def test_409_no_candidates(self, client, mock_store, mock_session):
        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/review",
            json={"reviews": [{"change_id": "c1", "decision": "accept"}]},
        )
        assert resp.status_code == 409

    def test_approve_action_finalizes(self, client, mock_store, mock_session):
        mock_session.candidate_changes = [
            CandidateChange(
                id="c1",
                change=DocumentChange(
                    page=1, old_text="Hello world", new_text="Hi",
                    reasoning="r", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="e1",
                source_description="d",
            ),
        ]
        mock_session.revision_round = 1

        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/review",
            json={
                "reviews": [{"change_id": "c1", "decision": "accept"}],
                "action": "approve",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["finalized"] is True


# ---------------------------------------------------------------------------
# POST /approve
# ---------------------------------------------------------------------------


class TestApproveSession:
    def test_finalizes_session(self, client, mock_store, mock_session):
        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/approve"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["finalized"] is True
        assert data["final_version"] == "v0"

    def test_409_already_finalized(self, client, mock_store, mock_session):
        mock_session.finalized = True
        resp = client.post(
            f"/api/v1/pipeline/sessions/{mock_session.session_id}/approve"
        )
        assert resp.status_code == 409
