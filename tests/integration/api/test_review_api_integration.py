"""Integration tests for Review API endpoints with real infrastructure.

These tests verify end-to-end workflows for the Phase 4 remediation review API:
- Complete workflow: create job → add observations → consolidate → approve → apply
- Batch approve workflow
- Edit workflow (with and without 'after')
- Review summary and listing endpoints

Uses real LocalStack S3 and Redis (no mocking of infrastructure).
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.dependencies import get_job_service, get_storage_service
from src.main import app
from src.services.remediation_storage_service import RemediationStorageService
from src.shared.models.agent_models import LLMUsage
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff
from src.shared.models.remediation import DocumentManifest, PageFeatures


@pytest_asyncio.fixture
async def override_dependencies(job_service, storage_service):
    """Override FastAPI app dependencies to use testcontainer fixtures."""
    # Override dependencies with our testcontainer-based fixtures
    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_storage_service] = lambda: storage_service

    yield

    # Cleanup: remove overrides after tests
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def setup_remediation_job(
    job_service,
    storage_service,
    sample_job_id
):
    """Set up a job with remediation artifacts (manifest, observations, proposals, markdown)."""
    # Create job in Redis
    await job_service.create_job(
        job_id=sample_job_id,
        s3_key=f"temp/{sample_job_id}/test.pdf",
        status="processing"
    )

    # Update to awaiting_review substatus (create_job doesn't accept substatus)
    await job_service.update_job_status(
        job_id=sample_job_id,
        status="processing",
        substatus="awaiting_review"
    )

    # Set up remediation storage
    remediation_storage = RemediationStorageService(storage_service)

    # Save manifest
    heading_tree_data = {
        "document_title": "Test Document",
        "title_page": 1,
        "sections": [],
        "total_pages": 2,
        "layout_type": "single_column",
        "confidence": 0.95,
        "observations": ""
    }

    manifest = DocumentManifest(
        job_id=sample_job_id,
        document_title="Review API Test Document",
        total_pages=2,
        heading_tree_json=json.dumps(heading_tree_data),
        page_features=[
            PageFeatures(page_num=1, has_images=True, image_count=1),
            PageFeatures(page_num=2, has_tables=True, table_count=1)
        ],
        required_agents=["figures", "tables"]
    )

    await remediation_storage.save_manifest(sample_job_id, manifest)

    # Save observations
    obs1_id = str(uuid.uuid4())
    obs2_id = str(uuid.uuid4())
    obs3_id = str(uuid.uuid4())

    observations = [
        Observation(
            id=obs1_id,
            job_id=sample_job_id,
            agent="figures",
            visual_description="Image shows flowchart",
            markup_description="Empty alt text",
            location=ObservationLocation(
                location_type="element",
                value="img.figure-1",
                page_num=1
            ),
            severity="major",
            status="open"
        ),
        Observation(
            id=obs2_id,
            job_id=sample_job_id,
            agent="tables",
            visual_description="Table has headers",
            markup_description="Table without header row",
            location=ObservationLocation(
                location_type="region",
                value="lines 10-15",
                page_num=2
            ),
            severity="critical",
            status="open"
        ),
        Observation(
            id=obs3_id,
            job_id=sample_job_id,
            agent="analysis",
            visual_description="Low confidence observation",
            markup_description="Unclear markup",
            location=ObservationLocation(
                location_type="region",
                value="page 1",
                page_num=1
            ),
            severity="minor",
            route="manual",
            status="manual"
        )
    ]

    await remediation_storage.save_observations(sample_job_id, observations)

    # Save proposals
    proposal1_id = str(uuid.uuid4())
    proposal2_id = str(uuid.uuid4())

    proposals = [
        Proposal(
            id=proposal1_id,
            job_id=sample_job_id,
            resolves=[obs1_id],
            diff=SearchReplaceDiff(
                search="![](figure-1.png)",
                replace="![Flowchart showing registration process](figure-1.png)"
            ),
            justification="Adding descriptive alt text",
            page_nums=[1],
            route="auto",
            status="pending"
        ),
        Proposal(
            id=proposal2_id,
            job_id=sample_job_id,
            resolves=[obs2_id],
            diff=SearchReplaceDiff(
                search="| A | B |",
                replace="| Header 1 | Header 2 |\n|----------|----------|\n| A | B |"
            ),
            justification="Adding table headers",
            page_nums=[2],
            route="manual",
            status="pending"
        )
    ]

    await remediation_storage.save_proposals(sample_job_id, proposals)

    # Save current markdown
    markdown_content = """# Test Document

This is a test document.

![](figure-1.png)

## Section 2

| A | B |
| 1 | 2 |
"""

    await remediation_storage.save_current_markdown(sample_job_id, markdown_content)

    return {
        "job_id": sample_job_id,
        "obs1_id": obs1_id,
        "obs2_id": obs2_id,
        "obs3_id": obs3_id,
        "proposal1_id": proposal1_id,
        "proposal2_id": proposal2_id,
        "remediation_storage": remediation_storage
    }


@pytest.mark.integration
class TestReviewSummaryEndpoint:
    """Tests for GET /api/documents/{job_id}/review endpoint."""

    @pytest.mark.asyncio
    async def test_get_review_summary(self, override_dependencies, setup_remediation_job, api_key_headers):
        """Test getting review summary for a job."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/review",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        summary = response.json()
        assert summary["job_id"] == job_id
        assert summary["status"] == "processing"
        assert summary["substatus"] == "awaiting_review"
        assert summary["observation_count"] == 3
        assert summary["proposal_count"] == 2
        assert summary["pending_proposals"] == 2
        assert summary["approved_proposals"] == 0
        assert summary["rejected_proposals"] == 0
        assert summary["manual_observations"] == 1

    @pytest.mark.asyncio
    async def test_get_review_summary_nonexistent_job(self, override_dependencies, api_key_headers):
        """Test getting review summary for nonexistent job returns 404."""
        # Arrange
        job_id = str(uuid.uuid4())

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/review",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 404


@pytest.mark.integration
class TestListEndpoints:
    """Tests for observation and proposal listing endpoints."""

    @pytest.mark.asyncio
    async def test_list_all_observations(self, override_dependencies, setup_remediation_job, api_key_headers):
        """Test listing all observations for a job."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/observations",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        observations = response.json()
        assert len(observations) == 3

        # Check fields
        assert all("id" in obs for obs in observations)
        assert all("agent" in obs for obs in observations)
        assert all("page_num" in obs for obs in observations)
        assert all("visual_description" in obs for obs in observations)

        # Check specific agents
        agents = [obs["agent"] for obs in observations]
        assert "figures" in agents
        assert "tables" in agents
        assert "analysis" in agents

    @pytest.mark.asyncio
    async def test_list_observations_filtered_by_status(
        self,
        override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test filtering observations by status."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act - Filter for manual status
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/observations?status=manual",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        observations = response.json()
        assert len(observations) == 1
        assert observations[0]["status"] == "manual"

    @pytest.mark.asyncio
    async def test_list_observations_filtered_by_agent(
        self,
        override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test filtering observations by agent."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act - Filter for figures agent
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/observations?agent=figures",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        observations = response.json()
        assert len(observations) == 1
        assert observations[0]["agent"] == "figures"

    @pytest.mark.asyncio
    async def test_list_all_proposals(self, override_dependencies, setup_remediation_job, api_key_headers):
        """Test listing all proposals for a job."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/proposals",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        proposals = response.json()
        assert len(proposals) == 2

        # Check fields
        assert all("id" in prop for prop in proposals)
        assert all("search" in prop for prop in proposals)
        assert all("replace" in prop for prop in proposals)
        assert all("justification" in prop for prop in proposals)
        assert all("status" in prop for prop in proposals)

    @pytest.mark.asyncio
    async def test_list_proposals_filtered_by_route(
        self,
        override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test filtering proposals by route."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Act - Filter for auto route
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/documents/{job_id}/proposals?route=auto",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        proposals = response.json()
        assert len(proposals) == 1
        assert proposals[0]["route"] == "auto"


@pytest.mark.integration
class TestApproveRejectEndpoints:
    """Tests for approve and reject proposal endpoints."""

    @pytest.mark.asyncio
    async def test_approve_proposal(self, override_dependencies, setup_remediation_job, api_key_headers):
        """Test approving a single proposal."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal1_id"]

        approve_request = {
            "reviewed_by": "reviewer@test.com",
            "review_notes": "Looks good"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/approve",
                json=approve_request,
                headers=api_key_headers
            )

        # Assert - Response
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "approved"
        assert result["proposal_id"] == proposal_id

        # Verify proposal updated in storage
        storage = data["remediation_storage"]
        proposals = await storage.load_proposals(job_id)
        approved = next(p for p in proposals if p.id == proposal_id)
        assert approved.status == "approved"
        assert approved.reviewed_by == "reviewer@test.com"
        assert approved.review_notes == "Looks good"
        assert approved.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_reject_proposal(self, override_dependencies, setup_remediation_job, api_key_headers):
        """Test rejecting a single proposal."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal2_id"]

        reject_request = {
            "reviewed_by": "reviewer@test.com",
            "review_notes": "Incorrect table structure"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/reject",
                json=reject_request,
                headers=api_key_headers
            )

        # Assert - Response
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "rejected"
        assert result["proposal_id"] == proposal_id

        # Verify proposal updated in storage
        storage = data["remediation_storage"]
        proposals = await storage.load_proposals(job_id)
        rejected = next(p for p in proposals if p.id == proposal_id)
        assert rejected.status == "rejected"
        assert rejected.reviewed_by == "reviewer@test.com"
        assert rejected.review_notes == "Incorrect table structure"

    @pytest.mark.asyncio
    async def test_approve_nonexistent_proposal(
        self,
        setup_remediation_job,
        api_key_headers
    ):
        """Test approving nonexistent proposal returns 404."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = str(uuid.uuid4())

        approve_request = {
            "reviewed_by": "reviewer@test.com"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/approve",
                json=approve_request,
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_already_approved_proposal(
        self,
        override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test approving already approved proposal returns 400."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal1_id"]

        # First approval
        approve_request = {
            "reviewed_by": "reviewer@test.com"
        }

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/approve",
                json=approve_request,
                headers=api_key_headers
            )

            # Act - Second approval attempt
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/approve",
                json=approve_request,
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 400
        assert "Cannot approve" in response.json()["detail"]


@pytest.mark.integration
class TestBatchApproveEndpoint:
    """Tests for batch approve endpoint."""

    @pytest.mark.asyncio
    async def test_batch_approve_all_auto_routed(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test batch approving all auto-routed proposals."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        batch_request = {
            "reviewed_by": "batch-reviewer@test.com"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/batch-approve",
                json=batch_request,
                headers=api_key_headers
            )

        # Assert - Response
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "batch_approved"
        assert result["approved_count"] == 1  # Only 1 auto-routed proposal
        assert result["remaining_pending"] == 1  # 1 manual proposal still pending

        # Verify proposals updated
        storage = data["remediation_storage"]
        proposals = await storage.load_proposals(job_id)

        auto_proposals = [p for p in proposals if p.route == "auto"]
        assert len(auto_proposals) == 1
        assert auto_proposals[0].status == "approved"
        assert auto_proposals[0].reviewed_by == "batch-reviewer@test.com"

        manual_proposals = [p for p in proposals if p.route == "manual"]
        assert len(manual_proposals) == 1
        assert manual_proposals[0].status == "pending"  # Not affected

    @pytest.mark.asyncio
    async def test_batch_approve_specific_proposals(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test batch approving specific proposal IDs."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal1_id = data["proposal1_id"]
        proposal2_id = data["proposal2_id"]

        batch_request = {
            "reviewed_by": "specific-reviewer@test.com",
            "proposal_ids": [proposal1_id, proposal2_id]
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/batch-approve",
                json=batch_request,
                headers=api_key_headers
            )

        # Assert - Response
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "batch_approved"
        assert result["approved_count"] == 2
        assert result["remaining_pending"] == 0

        # Verify both proposals approved
        storage = data["remediation_storage"]
        proposals = await storage.load_proposals(job_id)
        assert all(p.status == "approved" for p in proposals)


@pytest.mark.integration
class TestEditEndpoint:
    """Tests for edit endpoint (human observations and direct edits)."""

    @pytest.mark.asyncio
    async def test_edit_with_after_creates_approved_proposal(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test edit with 'after' creates auto-approved proposal."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal1_id"]

        edit_request = {
            "before": "This is a test document.",
            "after": "This is an improved test document.",
            "comment": "Improving document introduction",
            "reviewed_by": "editor@test.com"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/edit",
                json=edit_request,
                headers=api_key_headers
            )

        # Assert - Response
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "created_and_approved"
        assert result["proposal_id"] is not None
        assert "automatically" in result["message"]

        # Verify new proposal created and approved
        storage = data["remediation_storage"]
        proposals = await storage.load_proposals(job_id)
        assert len(proposals) == 3  # 2 original + 1 new

        new_proposal = next(p for p in proposals if p.id == result["proposal_id"])
        assert new_proposal.status == "approved"
        assert new_proposal.diff.search == "This is a test document."
        assert new_proposal.diff.replace == "This is an improved test document."
        assert new_proposal.reviewed_by == "editor@test.com"
        assert "Improving document introduction" in new_proposal.justification

    @pytest.mark.asyncio
    async def test_edit_without_after_creates_observation(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test edit without 'after' creates observation and triggers consolidation."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal1_id"]

        edit_request = {
            "before": "This is a test document.",
            "after": None,
            "comment": "This text needs improvement",
            "reviewed_by": "editor@test.com"
        }

        # Create a mock proposal that will be returned by the consolidation service
        mock_proposal = Proposal(
            id=str(uuid.uuid4()),
            job_id=job_id,
            resolves=[],
            diff=SearchReplaceDiff(
                search="This is a test document.",
                replace="This is an enhanced document."
            ),
            justification="AI-generated from human observation"
        )

        # Mock the consolidation service's reconsolidate_observation method
        from src.dependencies import get_consolidation_service

        mock_usage = LLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0
        )
        mock_consolidation = MagicMock()
        mock_consolidation.reconsolidate_observation = AsyncMock(
            return_value=(mock_proposal, mock_usage)
        )

        # Override the consolidation service dependency
        app.dependency_overrides[get_consolidation_service] = lambda: mock_consolidation

        try:
            # Act
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/documents/{job_id}/proposals/{proposal_id}/edit",
                    json=edit_request,
                    headers=api_key_headers
                )

            # Assert - Response
            assert response.status_code == 200
            result = response.json()
            assert result["status"] == "observation_created"
            assert result["observation_id"] is not None
            assert result["proposal_id"] is not None
            assert "AI generated" in result["message"]

            # Verify observation created
            storage = data["remediation_storage"]
            observations = await storage.load_observations(job_id)
            assert len(observations) == 4  # 3 original + 1 new

            new_obs = next(o for o in observations if o.id == result["observation_id"])
            assert new_obs.agent == "human"
            assert new_obs.source == "human"
            assert new_obs.human_comment == "This text needs improvement"
        finally:
            # Cleanup: remove the consolidation service override
            # Note: override_dependencies fixture will clear all overrides after test,
            # but being explicit here for clarity
            if get_consolidation_service in app.dependency_overrides:
                del app.dependency_overrides[get_consolidation_service]

    @pytest.mark.asyncio
    async def test_edit_with_nonexistent_before_text(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test edit with 'before' text not in document returns 400."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal_id = data["proposal1_id"]

        edit_request = {
            "before": "This text does not exist in the document.",
            "after": "New text",
            "comment": "Test edit",
            "reviewed_by": "editor@test.com"
        }

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal_id}/edit",
                json=edit_request,
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 400
        assert "not found in document" in response.json()["detail"]


@pytest.mark.integration
class TestCompleteWorkflow:
    """Test complete end-to-end workflow from observations to application."""

    @pytest.mark.asyncio
    async def test_complete_review_and_apply_workflow(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test complete workflow: review summary → approve → apply."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]
        proposal1_id = data["proposal1_id"]
        _ = data["obs1_id"]  # noqa: F841 - Used in fixture setup

        # Step 1: Get review summary
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            summary_response = await client.get(
                f"/api/documents/{job_id}/review",
                headers=api_key_headers
            )

            assert summary_response.status_code == 200
            summary = summary_response.json()
            assert summary["pending_proposals"] == 2

            # Step 2: Approve auto-routed proposal
            approve_response = await client.post(
                f"/api/documents/{job_id}/proposals/{proposal1_id}/approve",
                json={"reviewed_by": "reviewer@test.com"},
                headers=api_key_headers
            )

            assert approve_response.status_code == 200

            # Step 3: Apply approved proposals
            # Mock the application service
            with patch("src.api.review.ApplicationService") as mock_app_service_class:
                mock_app_service = MagicMock()

                # Mock application result
                from src.services.application_service import ApplicationResult

                mock_result = ApplicationResult(
                    applied_count=1,
                    failed_count=0,
                    skipped_count=0,
                    failed_proposals=[],
                    final_markdown_url=f"{job_id}.md",
                    validation_warnings=[]
                )

                mock_app_service.apply_approved_proposals = AsyncMock(
                    return_value=mock_result
                )
                mock_app_service.count_manual_observations = AsyncMock(return_value=1)
                mock_app_service_class.return_value = mock_app_service

                apply_response = await client.post(
                    f"/api/documents/{job_id}/apply",
                    headers=api_key_headers
                )

                # Assert - Application successful
                assert apply_response.status_code == 200
                apply_result = apply_response.json()
                assert apply_result["status"] == "completed"
                assert apply_result["applied_count"] == 1
                assert apply_result["failed_count"] == 0
                assert apply_result["manual_observations"] == 1

    @pytest.mark.asyncio
    async def test_batch_approve_and_apply_workflow(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers
    ):
        """Test batch approve followed by application."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Step 1: Batch approve all auto-routed
            batch_response = await client.post(
                f"/api/documents/{job_id}/proposals/batch-approve",
                json={"reviewed_by": "batch@test.com"},
                headers=api_key_headers
            )

            assert batch_response.status_code == 200
            batch_result = batch_response.json()
            assert batch_result["approved_count"] == 1

            # Step 2: Verify proposals state
            proposals_response = await client.get(
                f"/api/documents/{job_id}/proposals",
                headers=api_key_headers
            )

            proposals = proposals_response.json()
            approved = [p for p in proposals if p["status"] == "approved"]
            assert len(approved) == 1

    @pytest.mark.asyncio
    async def test_apply_without_awaiting_review_status(
        self, override_dependencies,
        setup_remediation_job,
        api_key_headers,
        job_service
    ):
        """Test applying when job is not in awaiting_review status returns 400."""
        # Arrange
        data = setup_remediation_job
        job_id = data["job_id"]

        # Change job substatus to something other than awaiting_review
        await job_service.update_job_status(
            job_id,
            "processing",
            substatus="extracting"
        )

        # Act
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/documents/{job_id}/apply",
                headers=api_key_headers
            )

        # Assert
        assert response.status_code == 400
        assert "not in review state" in response.json()["detail"]
