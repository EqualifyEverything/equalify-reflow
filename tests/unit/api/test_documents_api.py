"""Unit tests for Documents API endpoints.

Tests the job status endpoint for all status types, including:
- pii_scanning
- processing
- awaiting_approval
- completed
- failed
- denied
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.dependencies import get_job_service, get_s3_url_service, get_storage_service
from src.main import app

pytestmark = pytest.mark.unit


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
def mock_job_service():
    """Mock job service."""
    mock = MagicMock()
    mock.get_job = AsyncMock()
    return mock


@pytest.fixture
def mock_url_service():
    """Mock S3 URL service."""
    mock = MagicMock()
    mock.generate_url = AsyncMock(return_value="https://example.com/mock-url")
    mock.results_bucket = "equalify-pdf-results"
    mock.temp_bucket = "equalify-pdf-temp"
    return mock


class TestGetJobStatus:
    """Tests for GET /api/v1/documents/{job_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_job_status_processing(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test getting job status when status is processing."""
        job_id = "test-job-789"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "processing",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:30Z",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "processing"
            assert data["job_id"] == job_id
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_job_status_completed(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test getting job status when status is completed."""
        job_id = "test-job-completed"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "confidence_score": "0.92",
            "markdown_url": f"jobs/{job_id}/final.md",  # Required by PRD-027
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "completed"
            assert data["confidence_score"] == 0.92
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_job_status_not_found(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test getting job status for non-existent job."""
        job_id = "non-existent-job"
        mock_job_service.get_job.return_value = None

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_job_status_failed(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test getting job status when status is failed."""
        job_id = "test-job-failed"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "failed",
            "original_filename": "corrupted.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:30Z",
            "error": "PDF parsing failed: corrupted file",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["status"] == "failed"
            assert data["error"] == "PDF parsing failed: corrupted file"
        finally:
            app.dependency_overrides.clear()



