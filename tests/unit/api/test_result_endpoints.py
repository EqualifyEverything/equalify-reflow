"""Unit tests for result download endpoints.

Tests the 3 new endpoints:
- GET /api/v1/documents/{job_id}/result/markdown
- GET /api/v1/documents/{job_id}/result/figures
- GET /api/v1/documents/{job_id}/result/figures/{filename}
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.dependencies import get_converter_client, get_job_service
from src.main import app
from src.services.converter_client import FigureInfo

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_job_service():
    """Mock job service."""
    mock = MagicMock()
    mock.get_job = AsyncMock()
    return mock


@pytest.fixture
def mock_converter():
    """Mock ConverterClient."""
    mock = MagicMock()
    mock.get_result_markdown = AsyncMock()
    mock.list_result_figures = AsyncMock()
    mock.download_figure = AsyncMock()
    return mock


class TestGetResultMarkdown:
    """Tests for GET /api/v1/documents/{job_id}/result/markdown."""

    @pytest.mark.asyncio
    async def test_download_markdown_success(self, client, mock_job_service, mock_converter):
        """Test successful markdown download."""
        job_id = "test-job-123"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "report.pdf",
        }
        mock_converter.get_result_markdown.return_value = b"# Test Document\n\nContent here."

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get(f"/api/v1/documents/{job_id}/result/markdown")

            assert response.status_code == status.HTTP_200_OK
            assert response.headers["content-type"] == "text/markdown; charset=utf-8"
            assert "report.md" in response.headers["content-disposition"]
            assert response.content == b"# Test Document\n\nContent here."
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_markdown_job_not_found(self, client, mock_job_service, mock_converter):
        """Test 404 when job doesn't exist."""
        mock_job_service.get_job.return_value = None

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/nonexistent/result/markdown")

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_markdown_job_not_completed(self, client, mock_job_service, mock_converter):
        """Test 400 when job is not completed."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "processing",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/markdown")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_markdown_file_not_found(self, client, mock_job_service, mock_converter):
        """Test 404 when markdown file doesn't exist in S3."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "completed",
            "original_filename": "test.pdf",
        }
        mock_converter.get_result_markdown.side_effect = Exception("NoSuchKey")

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/markdown")

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()


class TestListResultFigures:
    """Tests for GET /api/v1/documents/{job_id}/result/figures."""

    @pytest.mark.asyncio
    async def test_list_figures_success(self, client, mock_job_service, mock_converter):
        """Test successful figure listing."""
        job_id = "test-job-123"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
        }
        mock_converter.list_result_figures.return_value = [
            FigureInfo(s3_key="results/test-job-123/figures/fig-1.png", filename="fig-1.png"),
            FigureInfo(s3_key="results/test-job-123/figures/fig-2.jpg", filename="fig-2.jpg"),
        ]

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get(f"/api/v1/documents/{job_id}/result/figures")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 2
            assert data[0]["filename"] == "fig-1.png"
            assert data[1]["filename"] == "fig-2.jpg"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_figures_empty(self, client, mock_job_service, mock_converter):
        """Test listing figures when none exist."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "completed",
        }
        mock_converter.list_result_figures.return_value = []

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/figures")

            assert response.status_code == status.HTTP_200_OK
            assert response.json() == []
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_list_figures_job_not_completed(self, client, mock_job_service, mock_converter):
        """Test 400 when job is not completed."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "pii_scanning",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/figures")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
        finally:
            app.dependency_overrides.clear()


class TestDownloadResultFigure:
    """Tests for GET /api/v1/documents/{job_id}/result/figures/{filename}."""

    @pytest.mark.asyncio
    async def test_download_figure_success(self, client, mock_job_service, mock_converter):
        """Test successful figure download."""
        job_id = "test-job-123"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
        }
        # PNG magic bytes
        mock_converter.download_figure.return_value = b"\x89PNG\r\n\x1a\n..."

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get(f"/api/v1/documents/{job_id}/result/figures/figure-1.png")

            assert response.status_code == status.HTTP_200_OK
            assert response.headers["content-type"] == "image/png"
            assert "figure-1.png" in response.headers["content-disposition"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_figure_not_found(self, client, mock_job_service, mock_converter):
        """Test 404 when figure doesn't exist."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "completed",
        }
        mock_converter.download_figure.side_effect = Exception("NoSuchKey")

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/figures/nonexistent.png")

            assert response.status_code == status.HTTP_404_NOT_FOUND
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_figure_job_not_completed(self, client, mock_job_service, mock_converter):
        """Test 400 when job is not completed."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "failed",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/figures/fig.png")

            assert response.status_code == status.HTTP_400_BAD_REQUEST
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_download_jpg_content_type(self, client, mock_job_service, mock_converter):
        """Test that JPEG files get correct content type."""
        mock_job_service.get_job.return_value = {
            "job_id": "test-job-123",
            "status": "completed",
        }
        mock_converter.download_figure.return_value = b"\xff\xd8\xff\xe0..."

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter

        try:
            response = client.get("/api/v1/documents/test-job-123/result/figures/photo.jpg")

            assert response.status_code == status.HTTP_200_OK
            assert response.headers["content-type"] == "image/jpeg"
        finally:
            app.dependency_overrides.clear()
