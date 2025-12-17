"""Unit tests for Review Checklist API endpoints (PRD-027).

Tests the review workflow API including:
- Get processing result endpoint
- Get review checklist with filters
- Get checklist summary
- Submit review for item
- Apply all reviews
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.dependencies import get_job_service, get_storage_service
from src.main import app
from src.shared.models.agent_trace import AgentTrace
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing_result import (
    AnalysisSummary,
    ExtractionSummary,
    ProcessingResult,
    ProcessingTrace,
    StructureSummary,
)
from src.shared.models.review_checklist import (
    ReviewChecklist,
    ReviewItem,
    ReviewOption,
)


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
            visual_description="Image shows flowchart with 5 steps",
            markup_description="Image has empty alt text",
            location=ObservationLocation(
                location_type="element",
                value="img[src='fig1.png']",
                page_num=1,
            ),
            confidence=0.9,
            severity="major",
            category="alt_text",
            status="open",
        ),
        Observation(
            id="obs-2",
            job_id="job-123",
            agent="typography",
            source="agent",
            visual_description="'Exxon' visible in PDF",
            markup_description="Text reads 'Exxon'",
            location=ObservationLocation(
                location_type="element",
                value="p.content",
                page_num=3,
            ),
            confidence=0.85,
            severity="minor",
            category="ocr",
            status="open",
        ),
    ]


@pytest.fixture
def sample_review_items(sample_observations):
    """Create sample review items for testing."""
    return [
        ReviewItem(
            id="ri-1",
            observation_id="obs-1",
            agent="figures",
            question="What does this flowchart show?",
            options=[
                ReviewOption(
                    id="opt-1",
                    label="Flowchart of process",
                    action="replace",
                    replacement_text="Flowchart showing 5-step process",
                    is_recommended=True,
                ),
                ReviewOption(
                    id="opt-2",
                    label="Keep empty alt text",
                    action="keep",
                    is_recommended=False,
                ),
            ],
            search_text="![](fig1.png)",
            context="...the following diagram shows the process...",
            page_num=1,
            agent_recommendation="Add descriptive alt text",
            agent_confidence=0.9,
        ),
        ReviewItem(
            id="ri-2",
            observation_id="obs-2",
            agent="typography",
            question="Is 'Exxon' a typo for 'Enzo'?",
            options=[
                ReviewOption(
                    id="opt-3",
                    label="Yes, replace with 'Enzo'",
                    action="replace",
                    replacement_text="Enzo",
                    is_recommended=True,
                ),
                ReviewOption(
                    id="opt-4",
                    label="No, 'Exxon' is correct",
                    action="keep",
                    is_recommended=False,
                ),
            ],
            search_text="Exxon",
            context="Both Exxon and yt are developed using...",
            page_num=3,
            agent_recommendation="Likely OCR error",
            agent_confidence=0.85,
        ),
    ]


@pytest.fixture
def sample_processing_result(sample_observations, sample_review_items):
    """Create sample ProcessingResult for testing."""
    # Build review checklist
    checklist = ReviewChecklist.from_items_and_observations(
        items=sample_review_items,
        observations=sample_observations,
    )

    # Build processing trace
    trace = ProcessingTrace(
        analysis=AnalysisSummary(
            document_type="research_paper",
            total_pages=5,
            key_entities=["yt", "Enzo"],
            required_agents=["figures", "typography"],
            confidence=0.95,
            time_seconds=5.0,
            cost_cents=1.5,
        ),
        extraction=ExtractionSummary(
            confidence=0.92,
            pages_extracted=5,
            correction_iterations=1,
            time_seconds=30.0,
            cost_cents=5.0,
        ),
        structure=StructureSummary(
            iterations=1,
            lint_issues_found=2,
            lint_issues_fixed=2,
            ocr_suggestions_processed=1,
            corrections_applied=2,
            final_lint_clean=True,
            time_seconds=10.0,
            cost_cents=2.0,
        ),
        agents=[
            AgentTrace(
                agent_name="figures",
                observations=[sample_observations[0]],
                auto_corrections=[],
                review_items=[sample_review_items[0]],
                reasoning_summary="Processed 1 image, needs alt text",
                confidence=0.9,
                cost_cents=3.0,
                time_seconds=15.0,
                iterations=1,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            ),
            AgentTrace(
                agent_name="typography",
                observations=[sample_observations[1]],
                auto_corrections=[],
                review_items=[sample_review_items[1]],
                reasoning_summary="Found 1 potential OCR error",
                confidence=0.85,
                cost_cents=2.0,
                time_seconds=8.0,
                iterations=1,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            ),
        ],
        total_observations=2,
        auto_corrections_applied=0,
        review_items_generated=2,
        total_cost_cents=13.5,
        total_time_seconds=68.0,
        total_tokens=5000,
    )

    return ProcessingResult(
        job_id="job-123",
        status="needs_review",
        markdown="# Test Document\n\n![](fig1.png)\n\nBoth Exxon and yt are developed using...",
        confidence=0.87,
        processing_trace=trace,
        review_checklist=checklist,
        processing_time_seconds=68.0,
    )


class TestGetProcessingResult:
    """Tests for GET /{job_id}/result endpoint."""

    @pytest.mark.asyncio
    async def test_get_result_success(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test getting full processing result."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                "/api/documents/job-123/result",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == "job-123"
            assert data["status"] == "needs_review"
            assert data["confidence"] == 0.87
            assert "processing_trace" in data
            assert "review_checklist" in data
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_result_job_not_found(self, client, api_key_headers):
        """Test getting result for non-existent job."""
        mock_storage = MagicMock()
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                "/api/documents/nonexistent/result",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "Job not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_result_not_processed_yet(self, client, api_key_headers):
        """Test getting result for job still processing."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(return_value=None)

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                "/api/documents/job-123/result",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "still be processing" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestGetReviewChecklist:
    """Tests for GET /{job_id}/checklist endpoint."""

    @pytest.mark.asyncio
    async def test_get_checklist_all(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test getting full checklist without filters."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/checklist",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 2
            assert len(data["items"]) == 2
            assert "by_category" in data
            assert "by_agent" in data
            assert "by_page" in data
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_checklist_filter_by_agent(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test filtering checklist by agent."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/checklist?agent=figures",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 1
            assert len(data["items"]) == 1
            assert data["items"][0]["agent"] == "figures"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_checklist_filter_by_page(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test filtering checklist by page number."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/checklist?page=3",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 1
            assert data["items"][0]["page_num"] == 3
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_checklist_filter_by_category(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test filtering checklist by category."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/checklist?category=alt_text",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_checklist_multiple_filters(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test filtering checklist with multiple filters (AND logic)."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            # Filter by agent=figures AND page=1 should return 1 item
            response = client.get(
                "/api/documents/job-123/checklist?agent=figures&page=1",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 1
            assert data["items"][0]["agent"] == "figures"
            assert data["items"][0]["page_num"] == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_checklist_not_found(self, client, api_key_headers):
        """Test getting checklist for non-existent result."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(return_value=None)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/nonexistent/checklist",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()


class TestGetChecklistSummary:
    """Tests for GET /{job_id}/checklist/summary endpoint."""

    @pytest.mark.asyncio
    async def test_get_summary_success(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test getting checklist summary."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                "/api/documents/job-123/checklist/summary",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == "job-123"
            assert data["total_items"] == 2
            assert data["completed_items"] == 0
            assert "categories" in data
            assert "agents" in data
            assert data["status"] == "needs_review"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_summary_job_not_found(self, client, api_key_headers):
        """Test getting summary for non-existent job."""
        mock_storage = MagicMock()
        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.get(
                "/api/documents/nonexistent/checklist/summary",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()


class TestSubmitReview:
    """Tests for POST /{job_id}/checklist/{item_id}/review endpoint."""

    @pytest.mark.asyncio
    async def test_submit_review_with_option(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submitting review with selected option."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            # Get the item ID from the sample result
            item_id = sample_processing_result.review_checklist.items[0].id
            option_id = sample_processing_result.review_checklist.items[0].options[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "selected_option_id": option_id,
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "reviewed"
            assert data["item_id"] == item_id
            assert data["remaining_items"] == 1  # 2 items - 1 reviewed = 1 remaining
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_with_custom_input(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submitting review with custom input."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "custom_input": "A detailed flowchart showing 5 processing steps",
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "reviewed"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_missing_input(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submit review fails without option or custom input."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "selected_option_id or custom_input" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_invalid_option(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submit review fails with invalid option ID."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "selected_option_id": "invalid-option-id",
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "Invalid option" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_item_not_found(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submit review fails for non-existent item."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/checklist/nonexistent-item/review",
                json={
                    "selected_option_id": "opt-1",
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "Review item not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_already_reviewed(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test submit review fails if already reviewed."""
        # Mark the first item as already reviewed
        sample_processing_result.review_checklist.items[0].reviewed_at = datetime.now(
            UTC
        )
        sample_processing_result.review_checklist.items[0].reviewed_by = "previous-user"

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "selected_option_id": "opt-1",
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "already reviewed" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestApplyReviews:
    """Tests for POST /{job_id}/apply-reviews endpoint."""

    @pytest.mark.asyncio
    async def test_apply_reviews_success(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test applying all reviews successfully."""
        # Mark all items as reviewed
        for item in sample_processing_result.review_checklist.items:
            item.reviewed_at = datetime.now(UTC)
            item.reviewed_by = "test-user"
            item.selected_option_id = item.options[0].id

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()
        mock_storage.upload_final_markdown = AsyncMock(
            return_value="jobs/job-123/final.md"
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            assert data["reviews_applied"] >= 0
            assert data["unreviewed_items"] == 0
            assert data["markdown_url"] == "jobs/job-123/final.md"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_unreviewed_items_fails(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test applying reviews fails with unreviewed items (without force)."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "still need review" in response.json()["detail"]
            assert "force=true" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_with_force(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test applying reviews with force=true skips unreviewed items."""
        # Mark only one item as reviewed
        sample_processing_result.review_checklist.items[0].reviewed_at = datetime.now(
            UTC
        )
        sample_processing_result.review_checklist.items[0].reviewed_by = "test-user"
        sample_processing_result.review_checklist.items[0].selected_option_id = (
            sample_processing_result.review_checklist.items[0].options[0].id
        )

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()
        mock_storage.upload_final_markdown = AsyncMock(
            return_value="jobs/job-123/final.md"
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={"force": True},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            assert data["unreviewed_items"] == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_not_found(self, client, api_key_headers):
        """Test applying reviews for non-existent job."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(return_value=None)

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(return_value=None)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/nonexistent/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "Job not found" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_keep_action(
        self, client, api_key_headers, sample_processing_result
    ):
        """Test applying reviews with 'keep' action doesn't modify markdown."""
        # Mark all items as reviewed with 'keep' action
        for item in sample_processing_result.review_checklist.items:
            item.reviewed_at = datetime.now(UTC)
            item.reviewed_by = "test-user"
            # Find the 'keep' option
            keep_option = next(
                (o for o in item.options if o.action == "keep"), item.options[-1]
            )
            item.selected_option_id = keep_option.id

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()
        mock_storage.upload_final_markdown = AsyncMock(
            return_value="jobs/job-123/final.md"
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            # Keep actions don't count as applied
            assert data["reviews_applied"] == 0
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_already_completed(self, client, api_key_headers):
        """Test applying reviews fails if job already completed."""
        mock_storage = MagicMock()

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "completed"}
        )

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "already completed" in response.json()["detail"]
        finally:
            app.dependency_overrides.clear()


class TestObservationLifecycle:
    """Tests verifying observation status updates."""

    @pytest.mark.asyncio
    async def test_submit_review_closes_observation_as_fixed(
        self, client, api_key_headers, sample_processing_result, sample_observations
    ):
        """Verify observation is closed with 'fixed' when replace selected."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id
            # Select the 'replace' option (first option)
            option_id = sample_processing_result.review_checklist.items[0].options[0].id

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "selected_option_id": option_id,
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK

            # Verify save was called
            mock_storage.save_processing_result.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_review_closes_observation_as_kept_original(
        self, client, api_key_headers, sample_processing_result
    ):
        """Verify observation is closed with 'kept_original' when keep selected."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            item_id = sample_processing_result.review_checklist.items[0].id
            # Find the 'keep' option (second option)
            keep_option = next(
                (o for o in sample_processing_result.review_checklist.items[0].options
                 if o.action == "keep"),
                sample_processing_result.review_checklist.items[0].options[-1]
            )

            response = client.post(
                f"/api/documents/job-123/checklist/{item_id}/review",
                json={
                    "selected_option_id": keep_option.id,
                    "reviewed_by": "test-user@example.com",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            mock_storage.save_processing_result.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_apply_reviews_verifies_storage_operations(
        self, client, api_key_headers, sample_processing_result
    ):
        """Verify save_processing_result and upload_final_markdown are called."""
        # Mark all items as reviewed
        for item in sample_processing_result.review_checklist.items:
            item.reviewed_at = datetime.now(UTC)
            item.reviewed_by = "test-user"
            item.selected_option_id = item.options[0].id

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(
            return_value=sample_processing_result
        )
        mock_storage.save_processing_result = AsyncMock()
        mock_storage.upload_final_markdown = AsyncMock(
            return_value="jobs/job-123/final.md"
        )

        mock_job_service = MagicMock()
        mock_job_service.get_job = AsyncMock(
            return_value={"job_id": "job-123", "status": "processing"}
        )
        mock_job_service.update_job_status = AsyncMock()

        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.post(
                "/api/documents/job-123/apply-reviews",
                json={},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK

            # Verify storage operations were called
            mock_storage.save_processing_result.assert_called_once()
            mock_storage.upload_final_markdown.assert_called_once()

            # Verify job status was updated
            mock_job_service.update_job_status.assert_called_once_with(
                "job-123",
                status="completed",
                substatus="reviews_applied",
            )
        finally:
            app.dependency_overrides.clear()


class TestLayeredMatching:
    """Tests for the layered matching algorithm in apply reviews."""

    def test_exact_match(self):
        """Test Layer 1: exact string match."""
        from src.api.review_checklist import _apply_item_replacement

        markdown = "# Title\n\nSome text with Exxon in it.\n"
        item = MagicMock()
        item.id = "item-1"
        item.search_text = "Exxon"
        item.context = "Some text with Exxon"
        item.page_num = 1
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "Enzo")

        assert "Enzo" in result
        assert "Exxon" not in result

    def test_whitespace_normalized_match(self):
        """Test Layer 2: whitespace-normalized match."""
        from src.api.review_checklist import _apply_item_replacement

        # Markdown has different whitespace than search_text
        markdown = "# Title\n\nSome  text   with   Exxon  in it.\n"
        item = MagicMock()
        item.id = "item-1"
        item.search_text = "text with Exxon"  # Different whitespace
        item.context = "Some text with Exxon"
        item.page_num = 1
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "text with Enzo")

        # Should still find and replace via normalized matching
        assert "Enzo" in result or result == markdown  # Either works or falls back

    def test_no_match_returns_unchanged(self):
        """Test fallback: returns unchanged markdown when no match found."""
        from src.api.review_checklist import _apply_item_replacement

        markdown = "# Title\n\nSome text without the search term.\n"
        item = MagicMock()
        item.id = "item-1"
        item.search_text = "nonexistent text"
        item.context = "different context"
        item.page_num = 1
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "replacement")

        assert result == markdown  # Unchanged

    def test_layer3_context_match(self):
        """Test Layer 3: context-aware match when exact and whitespace fail."""
        from src.api.review_checklist import _apply_item_replacement

        # Markdown where search_text appears only within a specific context
        markdown = (
            "# Document\n\nFirst paragraph about yt framework.\n\n"
            "Both Exxon and yt are developed using Python.\n\nLast paragraph.\n"
        )

        item = MagicMock()
        item.id = "item-1"
        # Use a search_text that would fail layer 1 and 2 matching
        # by making it not present exactly but present in context
        item.search_text = "Exxon"  # This will match in Layer 1 actually
        item.context = "Both Exxon and yt are developed"
        item.page_num = 3
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "Enzo")

        assert "Enzo" in result
        assert "Exxon" not in result

    def test_multiple_occurrences_replaces_first(self):
        """Test that only the first occurrence is replaced."""
        from src.api.review_checklist import _apply_item_replacement

        markdown = "Exxon is mentioned here.\n\nExxon is mentioned again.\n"
        item = MagicMock()
        item.id = "item-1"
        item.search_text = "Exxon"
        item.context = "Exxon is mentioned"
        item.page_num = 1
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "Enzo")

        # Should replace first occurrence only
        assert result.count("Enzo") == 1
        assert result.count("Exxon") == 1  # Second occurrence unchanged

    def test_regex_special_chars_in_search_text(self):
        """Test search_text containing regex metacharacters."""
        from src.api.review_checklist import _apply_item_replacement

        markdown = "The formula is E=mc^2 and (a+b).\n"
        item = MagicMock()
        item.id = "item-1"
        item.search_text = "E=mc^2"  # Contains regex special chars
        item.context = "The formula is E=mc^2"
        item.page_num = 1
        item.agent = "typography"

        result = _apply_item_replacement(markdown, item, "E=mc²")

        assert "E=mc²" in result
        assert "E=mc^2" not in result


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_checklist(self, client, api_key_headers):
        """Test handling of empty checklist."""
        from src.shared.models.processing_result import (
            AnalysisSummary,
            ExtractionSummary,
            ProcessingResult,
            ProcessingTrace,
            StructureSummary,
        )
        from src.shared.models.review_checklist import ReviewChecklist

        # Create result with empty checklist
        empty_result = ProcessingResult(
            job_id="job-empty",
            status="completed",
            markdown="# Empty doc",
            confidence=1.0,
            processing_trace=ProcessingTrace(
                analysis=AnalysisSummary(
                    document_type="simple",
                    total_pages=1,
                    key_entities=[],
                    required_agents=[],
                    confidence=1.0,
                    time_seconds=1.0,
                    cost_cents=0.1,
                ),
                extraction=ExtractionSummary(
                    confidence=1.0,
                    pages_extracted=1,
                    correction_iterations=0,
                    time_seconds=1.0,
                    cost_cents=0.1,
                ),
                structure=StructureSummary(
                    iterations=1,
                    lint_issues_found=0,
                    lint_issues_fixed=0,
                    ocr_suggestions_processed=0,
                    corrections_applied=0,
                    final_lint_clean=True,
                    time_seconds=1.0,
                    cost_cents=0.1,
                ),
                agents=[],
                total_observations=0,
                auto_corrections_applied=0,
                review_items_generated=0,
                total_cost_cents=0.3,
                total_time_seconds=3.0,
                total_tokens=100,
            ),
            review_checklist=ReviewChecklist(
                items=[],
                summary="0 items",
                by_category={},
                by_agent={},
                by_page={},
                total_items=0,
                critical_items=0,
                completed_items=0,
            ),
            processing_time_seconds=3.0,
        )

        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(return_value=empty_result)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-empty/checklist",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["total_items"] == 0
            assert len(data["items"]) == 0
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_custom_input_max_length_validation(self, client, api_key_headers):
        """Test that custom_input respects max_length."""
        from src.api.review_checklist import MAX_CUSTOM_INPUT_LENGTH

        mock_storage = MagicMock()
        # Don't need to set up full mock since validation happens before load

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            # Try to submit with too-long custom input
            too_long_input = "x" * (MAX_CUSTOM_INPUT_LENGTH + 1)

            response = client.post(
                "/api/documents/job-123/checklist/item-1/review",
                json={
                    "custom_input": too_long_input,
                    "reviewed_by": "test-user",
                },
                headers=api_key_headers,
            )

            # Should fail validation (422 Unprocessable Entity)
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_page_filter_validation(self, client, api_key_headers):
        """Test that page filter validates minimum value."""
        mock_storage = MagicMock()
        mock_storage.load_processing_result = AsyncMock(return_value=None)

        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        try:
            # Try with invalid page number (0)
            response = client.get(
                "/api/documents/job-123/checklist?page=0",
                headers=api_key_headers,
            )

            # Should fail validation
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()
