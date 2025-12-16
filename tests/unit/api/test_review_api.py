"""Unit tests for Review API endpoints.

Tests the review workflow API including:
- Review summary endpoint
- List observations/proposals endpoints
- Approve/reject endpoints
- Edit endpoint (both paths)
- Batch approve endpoint
- Apply trigger endpoint
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.dependencies import (
    get_application_service,
    get_consolidation_service,
    get_job_service,
    get_remediation_storage,
)
from src.main import app
from src.services.application_service import ApplicationResult
from src.shared.models.agent_models import LLMUsage
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff


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
def sample_observations():
    """Create sample observations for testing."""
    return [
        Observation(
            id="obs-1",
            job_id="job-123",
            agent="figures",
            source="agent",
            visual_description="Image shows flowchart",
            markup_description="Empty alt text",
            location=ObservationLocation(
                location_type="element",
                value="img[src='fig1.png']",
                page_num=1,
            ),
            confidence=0.9,
            severity="major",
            route="auto",
            status="open",
        ),
        Observation(
            id="obs-2",
            job_id="job-123",
            agent="tables",
            source="agent",
            visual_description="Table with headers",
            markup_description="Missing header markup",
            location=ObservationLocation(
                location_type="element",
                value="table",
                page_num=2,
            ),
            confidence=0.6,
            severity="major",
            route="manual",
            manual_reason="Low confidence",
            status="manual",
        ),
    ]


@pytest.fixture
def sample_proposals():
    """Create sample proposals for testing."""
    return [
        Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(
                search="![](fig1.png)",
                replace="![Flowchart showing process](fig1.png)",
            ),
            justification="Adding alt text for image",
            page_nums=[1],
            estimated_impact="Adds alt text to 1 image",
            route="auto",
            status="pending",
        ),
        Proposal(
            id="prop-2",
            job_id="job-123",
            resolves=["obs-2"],
            diff=SearchReplaceDiff(
                search="| A | B |",
                replace="| A | B |\n|---|---|",
            ),
            justification="Adding table header markup",
            page_nums=[2],
            estimated_impact="Improves table accessibility",
            route="manual",
            status="pending",
        ),
    ]


class TestReviewSummary:
    """Tests for GET /{job_id}/review endpoint."""

    @pytest.mark.asyncio
    async def test_review_summary_success(
        self, client, api_key_headers, sample_observations, sample_proposals
    ):
        """Test successful review summary retrieval."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "awaiting_review",
                "markdown_url": "http://example.com/job-123.md",
            }
        )

        mock_storage = MagicMock()
        mock_storage.load_observations = AsyncMock(return_value=sample_observations)
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get("/api/documents/job-123/review", headers=api_key_headers)

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == "job-123"
            assert data["status"] == "processing"
            assert data["substatus"] == "awaiting_review"
            assert data["observation_count"] == 2
            assert data["proposal_count"] == 2
            assert data["pending_proposals"] == 2
            assert data["manual_observations"] == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_review_summary_job_not_found(self, client, api_key_headers):
        """Test review summary with non-existent job."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get("/api/documents/nonexistent/review", headers=api_key_headers)
            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()


class TestListObservations:
    """Tests for GET /{job_id}/observations endpoint."""

    @pytest.mark.asyncio
    async def test_list_observations_all(
        self, client, api_key_headers, sample_observations
    ):
        """Test listing all observations."""
        mock_storage = MagicMock()
        mock_storage.load_observations = AsyncMock(return_value=sample_observations)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get("/api/documents/job-123/observations", headers=api_key_headers)

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 2
            assert data[0]["id"] == "obs-1"
            assert data[1]["id"] == "obs-2"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_observations_filter_by_status(
        self, client, api_key_headers, sample_observations
    ):
        """Test filtering observations by status."""
        mock_storage = MagicMock()
        mock_storage.load_observations = AsyncMock(return_value=sample_observations)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/observations?status=manual",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 1
            assert data[0]["status"] == "manual"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_observations_filter_by_agent(
        self, client, api_key_headers, sample_observations
    ):
        """Test filtering observations by agent."""
        mock_storage = MagicMock()
        mock_storage.load_observations = AsyncMock(return_value=sample_observations)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/observations?agent=figures",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 1
            assert data[0]["agent"] == "figures"
        finally:
            app.dependency_overrides.clear()


class TestListProposals:
    """Tests for GET /{job_id}/proposals endpoint."""

    @pytest.mark.asyncio
    async def test_list_proposals_all(self, client, api_key_headers, sample_proposals):
        """Test listing all proposals."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get("/api/documents/job-123/proposals", headers=api_key_headers)

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 2
            assert data[0]["id"] == "prop-1"
            assert data[0]["search"] == "![](fig1.png)"
            assert "Flowchart" in data[0]["replace"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_proposals_filter_by_route(
        self, client, api_key_headers, sample_proposals
    ):
        """Test filtering proposals by route."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/proposals?route=auto",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 1
            assert data[0]["route"] == "auto"
        finally:
            app.dependency_overrides.clear()


class TestApproveProposal:
    """Tests for POST /{job_id}/proposals/{proposal_id}/approve endpoint."""

    @pytest.mark.asyncio
    async def test_approve_proposal_success(
        self, client, api_key_headers, sample_proposals
    ):
        """Test successful proposal approval."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        mock_job_service = MagicMock()
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/approve",
                json={"reviewed_by": "test-user", "review_notes": "Looks good"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "approved"
            assert data["proposal_id"] == "prop-1"

            # Verify save was called
            mock_storage.save_proposals.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_approve_proposal_not_found(self, client, api_key_headers):
        """Test approving non-existent proposal."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=[])

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/nonexistent/approve",
                json={"reviewed_by": "test-user"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_approve_already_approved(self, client, api_key_headers):
        """Test approving already-approved proposal."""
        approved_proposal = Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="approved",
        )

        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=[approved_proposal])

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/approve",
                json={"reviewed_by": "test-user"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Cannot approve proposal with status: approved" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestRejectProposal:
    """Tests for POST /{job_id}/proposals/{proposal_id}/reject endpoint."""

    @pytest.mark.asyncio
    async def test_reject_proposal_success(
        self, client, api_key_headers, sample_proposals
    ):
        """Test successful proposal rejection."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        mock_job_service = MagicMock()
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/reject",
                json={"reviewed_by": "test-user", "review_notes": "Not accurate"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "rejected"
            assert data["proposal_id"] == "prop-1"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_reject_requires_notes(self, client, api_key_headers, sample_proposals):
        """Test that rejection requires review notes."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/reject",
                json={"reviewed_by": "test-user"},  # Missing review_notes
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()


class TestEditProposal:
    """Tests for POST /{job_id}/proposals/{proposal_id}/edit endpoint."""

    @pytest.mark.asyncio
    async def test_edit_with_after_creates_proposal(
        self, client, api_key_headers, sample_proposals
    ):
        """Test edit with 'after' creates auto-approved proposal."""
        mock_storage = MagicMock()
        mock_storage.load_current_markdown = AsyncMock(
            return_value="# Document\n\n![](fig1.png)\n\nContent"
        )
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/edit",
                json={
                    "before": "![](fig1.png)",
                    "after": "![New alt text](fig1.png)",
                    "comment": "Adding better alt text",
                    "reviewed_by": "human-user",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "created_and_approved"
            assert "proposal_id" in data
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_edit_without_after_creates_observation(
        self, client, api_key_headers, sample_proposals, sample_observations
    ):
        """Test edit without 'after' creates observation and reconsolidates."""
        mock_storage = MagicMock()
        mock_storage.load_current_markdown = AsyncMock(
            return_value="# Document\n\n![](fig1.png)\n\nContent"
        )
        mock_storage.load_observations = AsyncMock(return_value=sample_observations.copy())
        mock_storage.save_observations = AsyncMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        # Mock consolidation service to return a proposal with usage
        mock_proposal = Proposal(
            id="new-prop",
            job_id="job-123",
            resolves=["new-obs"],
            diff=SearchReplaceDiff(search="![](fig1.png)", replace="![AI generated](fig1.png)"),
            justification="AI generated",
        )
        mock_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0
        )
        mock_consolidation = MagicMock()
        mock_consolidation.reconsolidate_observation = AsyncMock(
            return_value=(mock_proposal, mock_usage)
        )

        # Mock job service for cost tracking
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value={
            "status": "awaiting_correction_approval",
            "llm_cost_cents": 100.0,
            "llm_input_tokens": 1000,
            "llm_output_tokens": 500,
            "llm_total_tokens": 1500,
        })
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage
        app.dependency_overrides[get_consolidation_service] = lambda: mock_consolidation
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/edit",
                json={
                    "before": "![](fig1.png)",
                    "after": None,  # No 'after' - triggers reconsolidation
                    "comment": "This image needs alt text",
                    "reviewed_by": "human-user",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "observation_created"
            assert "observation_id" in data
            assert data["proposal_id"] == "new-prop"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_edit_before_not_found(self, client, api_key_headers):
        """Test edit fails when 'before' text not in document."""
        mock_storage = MagicMock()
        mock_storage.load_current_markdown = AsyncMock(
            return_value="# Document\n\nOther content"
        )

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/edit",
                json={
                    "before": "nonexistent text",
                    "after": "replacement",
                    "comment": "Fix",
                    "reviewed_by": "user",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "'before' text not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_edit_markdown_not_found(self, client, api_key_headers):
        """Test edit fails when markdown not found."""
        mock_storage = MagicMock()
        mock_storage.load_current_markdown = AsyncMock(return_value=None)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/proposals/prop-1/edit",
                json={
                    "before": "text",
                    "after": "replacement",
                    "comment": "Fix",
                    "reviewed_by": "user",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "Markdown not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestBatchApprove:
    """Tests for POST /{job_id}/proposals/batch-approve endpoint."""

    @pytest.mark.asyncio
    async def test_batch_approve_all_auto(
        self, client, api_key_headers, sample_proposals
    ):
        """Test batch approve all auto-routed proposals."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        mock_job_service = MagicMock()
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/proposals/batch-approve",
                json={"reviewed_by": "batch-user"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "batch_approved"
            # Only prop-1 is auto-routed and pending
            assert data["approved_count"] == 1
            assert data["remaining_pending"] == 1  # prop-2 is manual
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_batch_approve_specific_ids(
        self, client, api_key_headers, sample_proposals
    ):
        """Test batch approve with specific proposal IDs."""
        mock_storage = MagicMock()
        mock_storage.load_proposals = AsyncMock(return_value=sample_proposals.copy())
        mock_storage.save_proposals = AsyncMock()

        mock_job_service = MagicMock()
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/proposals/batch-approve",
                json={
                    "reviewed_by": "batch-user",
                    "proposal_ids": ["prop-1", "prop-2"],  # Include manual one
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["approved_count"] == 2  # Both approved
            assert data["remaining_pending"] == 0
        finally:
            app.dependency_overrides.clear()


class TestTriggerApplication:
    """Tests for POST /{job_id}/apply endpoint."""

    @pytest.mark.asyncio
    async def test_apply_success(self, client, api_key_headers):
        """Test successful application of approved proposals."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "awaiting_review",
            }
        )
        mock_job_service.update_job_status = AsyncMock()

        mock_application_service = MagicMock()
        mock_application_service.apply_approved_proposals = AsyncMock(
            return_value=ApplicationResult(
                applied_count=3,
                failed_count=0,
                skipped_count=1,
                failed_proposals=[],
                final_markdown_url="job-123.md",
                validation_warnings=[],
            )
        )
        mock_application_service.count_manual_observations = AsyncMock(return_value=0)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_application_service] = lambda: mock_application_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            assert data["applied_count"] == 3
            assert data["failed_count"] == 0
            assert data["final_markdown_url"] == "job-123.md"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_job_not_found(self, client, api_key_headers):
        """Test apply with non-existent job."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/nonexistent/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_all_proposals_failed(self, client, api_key_headers):
        """Test apply when all proposals fail."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "awaiting_review",
            }
        )
        mock_job_service.update_job_status = AsyncMock()

        mock_application_service = MagicMock()
        mock_application_service.apply_approved_proposals = AsyncMock(
            return_value=ApplicationResult(
                applied_count=0,
                failed_count=2,
                skipped_count=0,
                failed_proposals=[
                    {"proposal_id": "prop-1", "error": "Not found"},
                    {"proposal_id": "prop-2", "error": "Multiple matches"},
                ],
                final_markdown_url=None,
                validation_warnings=[],
            )
        )
        mock_application_service.count_manual_observations = AsyncMock(return_value=0)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_application_service] = lambda: mock_application_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "failed"
            assert data["applied_count"] == 0
            assert data["failed_count"] == 2
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_partial_success(self, client, api_key_headers):
        """Test apply with some proposals succeeding and some failing."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "awaiting_review",
            }
        )
        mock_job_service.update_job_status = AsyncMock()

        mock_application_service = MagicMock()
        mock_application_service.apply_approved_proposals = AsyncMock(
            return_value=ApplicationResult(
                applied_count=2,
                failed_count=1,
                skipped_count=0,
                failed_proposals=[{"proposal_id": "prop-3", "error": "Not found"}],
                final_markdown_url="job-123.md",
                validation_warnings=["Unbalanced code fences"],
            )
        )
        mock_application_service.count_manual_observations = AsyncMock(return_value=1)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_application_service] = lambda: mock_application_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"  # Partial success still completes
            assert data["applied_count"] == 2
            assert data["failed_count"] == 1
            assert data["manual_observations"] == 1
            assert len(data["validation_warnings"]) == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_wrong_substatus(self, client, api_key_headers):
        """Test apply fails when job is not in awaiting_review substatus."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "analyzing",  # Wrong substatus
            }
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Job not in review state" in response.json()["detail"]
            assert "analyzing" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_missing_substatus(self, client, api_key_headers):
        """Test apply fails when job has no substatus."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                # No substatus field
            }
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Job not in review state" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_empty_substatus(self, client, api_key_headers):
        """Test apply fails when job has empty substatus."""
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "status": "processing",
                "substatus": "",  # Empty substatus
            }
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Job not in review state" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()
