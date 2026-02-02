"""Unit tests for Documents API endpoints.

Tests the job status endpoint for all status types, including:
- pii_scanning
- processing
- awaiting_approval
- awaiting_correction_approval
- needs_review (PRD-027)
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
    async def test_get_job_status_needs_review(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test getting job status when status is needs_review (PRD-027)."""
        job_id = "test-job-123"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "needs_review",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
            "confidence_score": "0.95",
            "review_item_count": "3",
            "processing_result_key": f"jobs/{job_id}/processing_result.json",
            "llm_cost_cents": "5.25",
            "llm_input_tokens": "1000",
            "llm_output_tokens": "500",
            "llm_total_tokens": "1500",
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
            assert data["status"] == "needs_review"
            assert data["job_id"] == job_id
            assert data["confidence_score"] == 0.95
            assert data["review_item_count"] == 3
            assert data["processing_result_key"] == f"jobs/{job_id}/processing_result.json"
            assert data["review_url"] == f"/api/v1/documents/{job_id}/result/checklist"
            assert "llm_cost" in data
            assert data["llm_cost"]["estimated_cost_cents"] == 5.25
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_job_status_needs_review_with_zero_items(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Test needs_review status with zero review items.

        This can happen when always_require_correction_review=True but
        no agents generated review items.
        """
        job_id = "test-job-456"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "needs_review",
            "original_filename": "clean.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
            "confidence_score": "0.98",
            "review_item_count": "0",
            "processing_result_key": f"jobs/{job_id}/processing_result.json",
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
            assert data["status"] == "needs_review"
            assert data["review_item_count"] == 0
        finally:
            app.dependency_overrides.clear()

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


class TestNeedsReviewStatusValidation:
    """Tests to ensure needs_review status is properly recognized.

    These tests prevent regression where unknown status causes 500 errors.
    """

    @pytest.mark.asyncio
    async def test_needs_review_not_treated_as_unknown(
        self, client, api_key_headers, mock_job_service, mock_url_service
    ):
        """Verify needs_review is a recognized status, not triggering 500 error.

        This is a regression test for the bug where needs_review status
        caused 'Unknown job status' 500 errors.
        """
        job_id = "test-regression"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "needs_review",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
            "confidence_score": "0.90",
            "review_item_count": "1",
            "processing_result_key": "jobs/test/result.json",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers
            )

            # Should NOT return 500 Internal Server Error
            assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR
            # Should return 200 OK with proper response
            assert response.status_code == status.HTTP_200_OK

            data = response.json()
            # Should have needs_review specific fields
            assert data["status"] == "needs_review"
            assert "review_url" in data
            assert "processing_result_key" in data
        finally:
            app.dependency_overrides.clear()


class TestDownloadBundleFilename:
    """Tests for GET /api/v1/documents/{job_id}/bundle download filename.

    The zip download should be named {original_filename_without_extension}-reflow.zip.
    """

    @pytest.mark.asyncio
    async def test_bundle_filename_uses_original_stem_reflow(
        self, client, api_key_headers, mock_job_service
    ):
        """Content-Disposition filename is {stem}-reflow.zip."""
        job_id = "test-bundle-job"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "lecture_notes.pdf",
            "result_url": f"results/{job_id}/result.md",
        }

        mock_storage = MagicMock()
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        with patch(
            "src.api.documents.FigureBundleService"
        ) as mock_bundle_cls:
            mock_instance = mock_bundle_cls.return_value
            mock_instance.generate_bundle = AsyncMock(return_value=b"PK\x03\x04fake-zip")

            try:
                response = client.get(
                    f"/api/v1/documents/{job_id}/bundle",
                    headers=api_key_headers,
                )

                assert response.status_code == 200
                disposition = response.headers["content-disposition"]
                assert disposition == 'attachment; filename="lecture_notes-reflow.zip"'
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_bundle_filename_fallback_when_no_original_filename(
        self, client, api_key_headers, mock_job_service
    ):
        """Falls back to 'document-reflow.zip' when original_filename is missing."""
        job_id = "test-no-filename"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "result_url": f"results/{job_id}/result.md",
        }

        mock_storage = MagicMock()
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        with patch(
            "src.api.documents.FigureBundleService"
        ) as mock_bundle_cls:
            mock_instance = mock_bundle_cls.return_value
            mock_instance.generate_bundle = AsyncMock(return_value=b"PK\x03\x04fake-zip")

            try:
                response = client.get(
                    f"/api/v1/documents/{job_id}/bundle",
                    headers=api_key_headers,
                )

                assert response.status_code == 200
                disposition = response.headers["content-disposition"]
                assert disposition == 'attachment; filename="document-reflow.zip"'
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_bundle_filename_with_multiple_dots(
        self, client, api_key_headers, mock_job_service
    ):
        """Multi-dot filenames strip only the last extension."""
        job_id = "test-dots"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "my.file.name.pdf",
            "result_url": f"results/{job_id}/result.md",
        }

        mock_storage = MagicMock()
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage

        with patch(
            "src.api.documents.FigureBundleService"
        ) as mock_bundle_cls:
            mock_instance = mock_bundle_cls.return_value
            mock_instance.generate_bundle = AsyncMock(return_value=b"PK\x03\x04fake-zip")

            try:
                response = client.get(
                    f"/api/v1/documents/{job_id}/bundle",
                    headers=api_key_headers,
                )

                assert response.status_code == 200
                disposition = response.headers["content-disposition"]
                assert disposition == 'attachment; filename="my.file.name-reflow.zip"'
            finally:
                app.dependency_overrides.clear()
