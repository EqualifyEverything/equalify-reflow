"""Unit tests for Review API endpoints.

Tests the simplified review workflow API including:
- List observations endpoint
- List auto corrections endpoint
- Close observation endpoint
- Apply auto corrections endpoint
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.dependencies import (
    get_application_service,
    get_job_service,
    get_remediation_storage,
)
from src.main import app
from src.services.application_service import ApplicationResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation


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
            category="alt_text",
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
            category="table_format",
            status="closed",
            resolution="fixed",
        ),
    ]


@pytest.fixture
def sample_corrections():
    """Create sample auto corrections for testing."""
    return [
        AutoCorrection(
            id="corr-1",
            observation_id="obs-1",
            search="![](fig1.png)",
            replace="![Flowchart showing process](fig1.png)",
            justification="Adding alt text for image",
            confidence=0.95,
            agent="figures",
            page_num=1,
            applied=False,
        ),
        AutoCorrection(
            id="corr-2",
            observation_id="obs-2",
            search="| A | B |",
            replace="| A | B |\n|---|---|",
            justification="Adding table header markup",
            confidence=0.88,
            agent="tables",
            page_num=2,
            applied=True,
        ),
    ]


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
                "/api/documents/job-123/observations?status=open",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 1
            assert data[0]["status"] == "open"
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


class TestListCorrections:
    """Tests for GET /{job_id}/corrections endpoint."""

    @pytest.mark.asyncio
    async def test_list_corrections_all(self, client, api_key_headers, sample_corrections):
        """Test listing all auto corrections."""
        mock_storage = MagicMock()
        mock_storage.load_auto_corrections = AsyncMock(return_value=sample_corrections)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get("/api/documents/job-123/corrections", headers=api_key_headers)

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 2
            assert data[0]["id"] == "corr-1"
            assert data[0]["search"] == "![](fig1.png)"
            assert "Flowchart" in data[0]["replace"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_corrections_filter_unapplied(
        self, client, api_key_headers, sample_corrections
    ):
        """Test filtering to only unapplied corrections."""
        mock_storage = MagicMock()
        mock_storage.load_auto_corrections = AsyncMock(return_value=sample_corrections)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.get(
                "/api/documents/job-123/corrections?applied=false",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 1
            assert data[0]["applied"] is False
        finally:
            app.dependency_overrides.clear()


class TestCloseObservation:
    """Tests for POST /{job_id}/observations/{observation_id}/close endpoint."""

    @pytest.mark.asyncio
    async def test_close_observation_success(
        self, client, api_key_headers, sample_observations
    ):
        """Test successfully closing an observation."""
        mock_storage = MagicMock()
        mock_storage.close_observation = AsyncMock(return_value=True)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/observations/obs-1/close",
                json={"resolution": "fixed", "reviewed_by": "test-user"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "closed"
            assert data["observation_id"] == "obs-1"

            mock_storage.close_observation.assert_called_once_with(
                job_id="job-123", observation_id="obs-1", resolution="fixed"
            )
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_close_observation_not_found(self, client, api_key_headers):
        """Test closing non-existent observation."""
        mock_storage = MagicMock()
        mock_storage.close_observation = AsyncMock(return_value=False)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            response = client.post(
                "/api/documents/job-123/observations/nonexistent/close",
                json={"resolution": "fixed", "reviewed_by": "test-user"},
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_close_observation_valid_resolutions(self, client, api_key_headers):
        """Test that valid resolution values are accepted."""
        mock_storage = MagicMock()
        mock_storage.close_observation = AsyncMock(return_value=True)

        app.dependency_overrides[get_remediation_storage] = lambda: mock_storage

        try:
            for resolution in ["fixed", "kept_original", "skipped"]:
                response = client.post(
                    "/api/documents/job-123/observations/obs-1/close",
                    json={"resolution": resolution, "reviewed_by": "test-user"},
                    headers=api_key_headers,
                )
                assert response.status_code == status.HTTP_200_OK
        finally:
            app.dependency_overrides.clear()


class TestTriggerApplication:
    """Tests for POST /{job_id}/apply endpoint."""

    @pytest.mark.asyncio
    async def test_apply_success(self, client, api_key_headers):
        """Test successful application of auto corrections."""
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
        mock_application_service.apply_auto_corrections = AsyncMock(
            return_value=ApplicationResult(
                applied_count=3,
                failed_count=0,
                skipped_count=1,
                failed_corrections=[],
                final_markdown_url="job-123.md",
                validation_warnings=[],
            )
        )
        mock_application_service.count_open_observations = AsyncMock(return_value=0)

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
    async def test_apply_all_corrections_failed(self, client, api_key_headers):
        """Test apply when all corrections fail."""
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
        mock_application_service.apply_auto_corrections = AsyncMock(
            return_value=ApplicationResult(
                applied_count=0,
                failed_count=2,
                skipped_count=0,
                failed_corrections=[
                    {"correction_id": "corr-1", "error": "Not found"},
                    {"correction_id": "corr-2", "error": "Multiple matches"},
                ],
                final_markdown_url=None,
                validation_warnings=[],
            )
        )
        mock_application_service.count_open_observations = AsyncMock(return_value=0)

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
        """Test apply with some corrections succeeding and some failing."""
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
        mock_application_service.apply_auto_corrections = AsyncMock(
            return_value=ApplicationResult(
                applied_count=2,
                failed_count=1,
                skipped_count=0,
                failed_corrections=[{"correction_id": "corr-3", "error": "Not found"}],
                final_markdown_url="job-123.md",
                validation_warnings=["Unbalanced code fences"],
            )
        )
        mock_application_service.count_open_observations = AsyncMock(return_value=1)

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
            assert data["open_observations"] == 1
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
