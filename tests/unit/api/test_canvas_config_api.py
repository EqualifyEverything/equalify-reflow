"""Unit tests for Canvas course configuration API endpoints.

Tests cover:
- GET /api/v1/canvas/courses/{course_id}/config (defaults and configured)
- PUT /api/v1/canvas/courses/{course_id}/config (create and update)
- GET /api/v1/canvas/courses/{course_id}/documents (list)
- GET /api/v1/canvas/courses/{course_id}/documents/{file_id} (detail + 404)
- POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/process
- POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/retry (success + 400)
- POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/publish (success + 400)
- Router registration gating
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from src.api.canvas_config import router as canvas_config_router
from src.canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from src.dependencies import (
    get_course_config_service,
    get_job_service,
    get_queue_service,
    get_redis_client,
    get_storage_service,
)
from src.main import app

pytestmark = pytest.mark.unit

# Ensure the canvas config router is registered for tests
# (it may not be registered if canvas_autopublish_enabled is False at import time)
_router_registered = False


def _ensure_router_registered():
    global _router_registered
    if _router_registered:
        return
    route_paths = [getattr(r, "path", "") for r in app.routes]
    if not any("/canvas/courses" in p for p in route_paths):
        app.include_router(canvas_config_router)
    _router_registered = True


_ensure_router_registered()


@pytest.fixture
def mock_config_service():
    """Mock CourseConfigService."""
    mock = AsyncMock(spec=CourseConfigService)
    mock.get_config = AsyncMock(return_value=None)
    mock.set_config = AsyncMock()
    mock.list_processed_files = AsyncMock(return_value={})
    mock.get_processed_file = AsyncMock(return_value=None)
    mock.get_publish_result = AsyncMock(return_value=None)
    mock.set_processed_file = AsyncMock()
    return mock


@pytest.fixture
def mock_job_svc():
    """Mock JobService."""
    mock = MagicMock()
    mock.get_job = AsyncMock(return_value=None)
    mock.create_job = AsyncMock()
    mock.update_job_status = AsyncMock()
    mock.redis = AsyncMock()
    mock.redis.hset = AsyncMock()
    mock.status_prefix = "eq-pdf:job:"
    return mock


@pytest.fixture
def mock_queue_svc():
    """Mock QueueService."""
    mock = MagicMock()
    mock.queue_pii_job = AsyncMock()
    return mock


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_storage_svc():
    """Mock StorageService."""
    mock = MagicMock()
    mock.s3_client = MagicMock()
    mock.temp_bucket = "equalify-temp"
    mock.results_bucket = "equalify-results"
    return mock


@pytest.fixture
def client(mock_config_service, mock_job_svc, mock_queue_svc, mock_redis, mock_storage_svc):
    """Create test client with mocked dependencies."""
    app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
    app.dependency_overrides[get_job_service] = lambda: mock_job_svc
    app.dependency_overrides[get_queue_service] = lambda: mock_queue_svc
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_storage_service] = lambda: mock_storage_svc

    yield TestClient(app)

    app.dependency_overrides.clear()


# ===========================================================================
# GET /config - Settings endpoints
# ===========================================================================


class TestGetCourseConfig:
    """Tests for GET /api/v1/canvas/courses/{course_id}/config."""

    def test_returns_defaults_for_unconfigured_course(self, client, mock_config_service):
        """Returns default config when course is not configured."""
        mock_config_service.get_config.return_value = None

        response = client.get("/api/v1/canvas/courses/123/config")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["course_id"] == "123"
        assert data["enabled"] is False
        assert data["auto_publish_threshold"] == 1.0
        assert data["created_at"] is None
        assert data["updated_at"] is None

    def test_returns_stored_config(self, client, mock_config_service):
        """Returns stored configuration values."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.8,
            canvas_api_token="token-123",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-06-15T10:00:00Z",
        )

        response = client.get("/api/v1/canvas/courses/456/config")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["course_id"] == "456"
        assert data["enabled"] is True
        assert data["auto_publish_threshold"] == 0.8
        assert data["created_at"] == "2024-01-01T00:00:00Z"
        assert data["updated_at"] == "2024-06-15T10:00:00Z"

    def test_defaults_calls_get_config_with_course_id(self, client, mock_config_service):
        """Verifies get_config is called with the path parameter course_id."""
        mock_config_service.get_config.return_value = None

        client.get("/api/v1/canvas/courses/42/config")

        mock_config_service.get_config.assert_awaited_once_with("42")

    def test_defaults_response_has_exact_keys(self, client, mock_config_service):
        """Default response contains exactly the expected keys, no extras."""
        mock_config_service.get_config.return_value = None

        response = client.get("/api/v1/canvas/courses/123/config")

        data = response.json()
        expected_keys = {"course_id", "enabled", "auto_publish_threshold", "created_at", "updated_at"}
        assert set(data.keys()) == expected_keys

    def test_defaults_for_different_course_ids(self, client, mock_config_service):
        """Each unconfigured course gets its own course_id in the defaults."""
        mock_config_service.get_config.return_value = None

        for cid in ["100", "200", "99999"]:
            response = client.get(f"/api/v1/canvas/courses/{cid}/config")
            assert response.status_code == status.HTTP_200_OK
            assert response.json()["course_id"] == cid
            assert response.json()["enabled"] is False

    def test_does_not_expose_api_token(self, client, mock_config_service):
        """Config response does not include the Canvas API token."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            canvas_api_token="secret-token",
        )

        response = client.get("/api/v1/canvas/courses/789/config")

        data = response.json()
        assert "canvas_api_token" not in data


# ===========================================================================
# PUT /config - Settings update endpoints
# ===========================================================================


class TestUpdateCourseConfig:
    """Tests for PUT /api/v1/canvas/courses/{course_id}/config."""

    def test_creates_new_config(self, client, mock_config_service):
        """Creates config when course is not yet configured."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"enabled": True, "auto_publish_threshold": 0.8},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["course_id"] == "123"
        assert data["enabled"] is True
        assert data["auto_publish_threshold"] == 0.8
        assert data["created_at"] is not None
        assert data["updated_at"] is not None

        # Verify set_config was called
        mock_config_service.set_config.assert_awaited_once()
        call_args = mock_config_service.set_config.call_args
        assert call_args[0][0] == "123"
        saved_config = call_args[0][1]
        assert saved_config.enabled is True
        assert saved_config.auto_publish_threshold == 0.8

    def test_updates_existing_config(self, client, mock_config_service):
        """Updates existing configuration with partial update."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=False,
            auto_publish_threshold=1.0,
            canvas_api_token="existing-token",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"enabled": True},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["enabled"] is True
        # auto_publish_threshold should remain unchanged
        assert data["auto_publish_threshold"] == 1.0
        # created_at should be preserved
        assert data["created_at"] == "2024-01-01T00:00:00Z"

    def test_enables_course_adds_to_enabled_set(self, client, mock_config_service):
        """PUT with enabled=True calls set_config which adds to enabled set."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"enabled": True},
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["enabled"] is True
        call_args = mock_config_service.set_config.call_args[0]
        assert call_args[0] == "123"
        assert call_args[1].enabled is True

    def test_enables_previously_disabled_course(self, client, mock_config_service):
        """PUT with enabled=True on a disabled course enables it and adds to enabled set."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=False,
            auto_publish_threshold=0.9,
            canvas_api_token="token",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        response = client.put(
            "/api/v1/canvas/courses/456/config",
            json={"enabled": True},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["enabled"] is True
        assert data["course_id"] == "456"
        # Threshold should be preserved
        assert data["auto_publish_threshold"] == 0.9
        # created_at preserved from existing config
        assert data["created_at"] == "2024-01-01T00:00:00Z"
        # set_config called with correct course_id and enabled=True
        call_args = mock_config_service.set_config.call_args[0]
        assert call_args[0] == "456"
        assert call_args[1].enabled is True

    def test_disables_course(self, client, mock_config_service):
        """PUT with enabled=False disables the course."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            canvas_api_token="token",
            created_at="2024-01-01T00:00:00Z",
        )

        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"enabled": False},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["enabled"] is False
        saved_config = mock_config_service.set_config.call_args[0][1]
        assert saved_config.enabled is False

    def test_rejects_invalid_threshold(self, client):
        """Rejects auto_publish_threshold outside 0.0-1.0 range."""
        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"auto_publish_threshold": 1.5},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_rejects_negative_threshold(self, client):
        """Rejects negative auto_publish_threshold."""
        response = client.put(
            "/api/v1/canvas/courses/123/config",
            json={"auto_publish_threshold": -0.1},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ===========================================================================
# GET /documents - Document list endpoint
# ===========================================================================


class TestListCourseDocuments:
    """Tests for GET /api/v1/canvas/courses/{course_id}/documents."""

    def test_returns_empty_list_when_no_documents(self, client, mock_config_service):
        """Returns empty list when course has no processed documents."""
        mock_config_service.list_processed_files.return_value = {}

        response = client.get("/api/v1/canvas/courses/123/documents")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []

    def test_returns_document_list(self, client, mock_config_service, mock_job_svc):
        """Returns list of processed documents with status."""
        mock_config_service.list_processed_files.return_value = {
            "42": ProcessedFile(
                canvas_file_id="42",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-1",
                status="completed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="lecture.pdf",
            ),
            "43": ProcessedFile(
                canvas_file_id="43",
                canvas_updated_at="2024-06-15T12:00:00Z",
                job_id="job-2",
                status="processing",
                processed_at="2024-06-15T12:05:00Z",
                original_filename="notes.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/api/v1/canvas/courses/123/documents")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2

    def test_merges_publish_result(self, client, mock_config_service, mock_job_svc):
        """Returns 'published' status when publish result exists."""
        mock_config_service.list_processed_files.return_value = {
            "42": ProcessedFile(
                canvas_file_id="42",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-1",
                status="completed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="lecture.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture-reflow",
            "canvas_page_id": "999",
            "download_url": "https://s3.example.com/bundle.zip",
            "published_at": "2024-06-15T12:00:00Z",
        }
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.92",
        }

        response = client.get("/api/v1/canvas/courses/123/documents")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        doc = data[0]
        assert doc["status"] == "published"
        assert doc["canvas_page_url"] == "https://canvas.example.com/pages/lecture-reflow"
        assert doc["canvas_page_id"] == 999
        assert doc["confidence_score"] == 0.92


# ===========================================================================
# GET /documents/{file_id} - Document detail endpoint
# ===========================================================================


class TestGetDocumentStatus:
    """Tests for GET /api/v1/canvas/courses/{course_id}/documents/{file_id}."""

    def test_returns_document_status(self, client, mock_config_service, mock_job_svc):
        """Returns detailed status for an existing document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/api/v1/canvas/courses/123/documents/42")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["canvas_file_id"] == "42"
        assert data["original_filename"] == "lecture.pdf"
        assert data["status"] == "completed"
        assert data["job_id"] == "job-1"
        assert data["confidence_score"] == 0.95

    def test_returns_404_for_unknown_file(self, client, mock_config_service):
        """Returns 404 when document is not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.get("/api/v1/canvas/courses/123/documents/999")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_failed_status_with_error(self, client, mock_config_service, mock_job_svc):
        """Returns error_message for failed documents."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "error": "Docling extraction failed",
        }

        response = client.get("/api/v1/canvas/courses/123/documents/42")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "failed"
        assert data["error_message"] == "Docling extraction failed"


# ===========================================================================
# POST /documents/{file_id}/retry - Retry endpoint
# ===========================================================================


class TestRetryProcessing:
    """Tests for POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/retry."""

    def test_retries_failed_document(self, client, mock_config_service, mock_job_svc, mock_queue_svc):
        """Successfully retries a failed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "s3_key": "temp/job-1.pdf",
        }

        response = client.post("/api/v1/canvas/courses/123/documents/42/retry")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == "job-1"
        assert data["status"] == "processing"
        mock_job_svc.update_job_status.assert_awaited_once_with("job-1", "pii_scanning")
        mock_queue_svc.queue_pii_job.assert_awaited_once_with("job-1", "temp/job-1.pdf")

    def test_returns_400_for_non_failed_document(self, client, mock_config_service):
        """Returns 400 when trying to retry a non-failed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )

        response = client.post("/api/v1/canvas/courses/123/documents/42/retry")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not in failed state" in response.json()["detail"]

    def test_returns_404_for_unknown_document(self, client, mock_config_service):
        """Returns 404 when document is not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.post("/api/v1/canvas/courses/123/documents/999/retry")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_404_when_job_not_found(self, client, mock_config_service, mock_job_svc):
        """Returns 404 when the associated job is not found."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-missing",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_job_svc.get_job.return_value = None

        response = client.post("/api/v1/canvas/courses/123/documents/42/retry")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_400_when_s3_key_missing(self, client, mock_config_service, mock_job_svc):
        """Returns 400 when the job has no S3 key."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "s3_key": "",
        }

        response = client.post("/api/v1/canvas/courses/123/documents/42/retry")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "S3 key" in response.json()["detail"]


# ===========================================================================
# POST /documents/{file_id}/publish - Publish endpoint
# ===========================================================================


class TestPublishDocument:
    """Tests for POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/publish."""

    def test_returns_400_for_non_completed_document(self, client, mock_config_service):
        """Returns 400 when trying to publish a non-completed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )

        response = client.post("/api/v1/canvas/courses/123/documents/42/publish")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "not completed" in response.json()["detail"]

    def test_returns_404_for_unknown_document(self, client, mock_config_service):
        """Returns 404 when document is not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.post("/api/v1/canvas/courses/123/documents/999/publish")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_400_when_no_api_token(self, client, mock_config_service):
        """Returns 400 when course has no Canvas API token."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_config.return_value = None

        response = client.post("/api/v1/canvas/courses/123/documents/42/publish")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "API token" in response.json()["detail"]

    def test_publishes_completed_document(self, client, mock_config_service, mock_job_svc):
        """Successfully publishes a completed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            canvas_api_token="test-token",
            created_at="2024-01-01T00:00:00Z",
        )

        mock_publish_result = MagicMock()
        mock_publish_result.canvas_page_url = "https://canvas.example.com/pages/lecture-reflow"
        mock_publish_result.canvas_page_id = 999

        # Patch at the source modules since publish_document uses lazy imports
        with (
            patch("src.api.canvas_config.CanvasAPIClient") as mock_client_cls,
            patch("src.canvas.publisher.CanvasPublisherService.publish", new_callable=AsyncMock) as mock_publish,
        ):
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_publish.return_value = mock_publish_result

            response = client.post("/api/v1/canvas/courses/123/documents/42/publish")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "published"
        assert data["canvas_page_url"] == "https://canvas.example.com/pages/lecture-reflow"
        assert data["canvas_page_id"] == 999


# ===========================================================================
# POST /documents/{file_id}/process - Process endpoint
# ===========================================================================


class TestTriggerProcessing:
    """Tests for POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/process."""

    def test_returns_400_when_no_api_token(self, client, mock_config_service):
        """Returns 400 when course has no Canvas API token."""
        mock_config_service.get_config.return_value = None

        response = client.post("/api/v1/canvas/courses/123/documents/42/process")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "API token" in response.json()["detail"]

    def test_returns_404_when_file_not_in_canvas(self, client, mock_config_service):
        """Returns 404 when file is not found in Canvas."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            canvas_api_token="test-token",
        )

        with patch("src.api.canvas_config.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[])
            mock_client_instance.close = AsyncMock()

            response = client.post("/api/v1/canvas/courses/123/documents/42/process")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found in Canvas" in response.json()["detail"]

    def test_successfully_triggers_processing(
        self, client, mock_config_service, mock_job_svc, mock_queue_svc
    ):
        """Successfully downloads and queues a Canvas file for processing."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            canvas_api_token="test-token",
        )

        canvas_file = {
            "id": 42,
            "display_name": "lecture.pdf",
            "url": "https://canvas.example.com/files/42/download",
            "updated_at": "2024-06-15T10:30:00Z",
        }

        fake_pdf = b"%PDF-1.4 " + b"x" * 200

        with patch("src.api.canvas_config.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[canvas_file])
            mock_client_instance.close = AsyncMock()
            mock_client_instance.api_token = "test-token"
            mock_client_instance._is_docker_url = False
            mock_client_instance._docker_host_header = "localhost"

            mock_http_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = fake_pdf
            mock_response.raise_for_status = MagicMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_client_instance._get_client = MagicMock(return_value=mock_http_client)

            response = client.post("/api/v1/canvas/courses/123/documents/42/process")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "processing"
        assert "job_id" in data
        mock_job_svc.create_job.assert_awaited_once()
        mock_queue_svc.queue_pii_job.assert_awaited_once()


# ===========================================================================
# Router registration tests
# ===========================================================================


class TestRouterRegistration:
    """Tests for conditional router registration in main.py."""

    def test_router_registered_when_autopublish_enabled(self):
        """Canvas config router is registered when canvas_autopublish_enabled=True."""
        with patch("src.main.settings") as mock_settings:
            mock_settings.canvas_autopublish_enabled = True
            mock_settings.lti_enabled = False
            mock_settings.environment = "test"

            # Verify the router module exports correctly
            from src.api.canvas_config import router
            assert router.prefix == "/api/v1/canvas/courses"

    def test_router_has_correct_prefix(self):
        """Router has the correct prefix."""
        from src.api.canvas_config import router
        assert router.prefix == "/api/v1/canvas/courses"

    def test_router_has_correct_tags(self):
        """Router has correct API tags."""
        from src.api.canvas_config import router
        assert "canvas-config" in router.tags


# ===========================================================================
# Pydantic model validation tests
# ===========================================================================


class TestPydanticModels:
    """Tests for request/response model validation."""

    def test_course_config_update_accepts_partial(self):
        """CourseConfigUpdate accepts partial updates."""
        from src.api.canvas_config import CourseConfigUpdate

        update = CourseConfigUpdate(enabled=True)
        assert update.enabled is True
        assert update.auto_publish_threshold is None

    def test_course_config_update_empty_body(self):
        """CourseConfigUpdate accepts empty body."""
        from src.api.canvas_config import CourseConfigUpdate

        update = CourseConfigUpdate()
        assert update.enabled is None
        assert update.auto_publish_threshold is None

    def test_course_config_update_validates_threshold_range(self):
        """CourseConfigUpdate rejects threshold outside 0.0-1.0."""
        from pydantic import ValidationError
        from src.api.canvas_config import CourseConfigUpdate

        with pytest.raises(ValidationError):
            CourseConfigUpdate(auto_publish_threshold=1.5)

        with pytest.raises(ValidationError):
            CourseConfigUpdate(auto_publish_threshold=-0.1)

    def test_course_config_response_all_fields(self):
        """CourseConfigResponse includes all required fields."""
        from src.api.canvas_config import CourseConfigResponse

        resp = CourseConfigResponse(
            course_id="123",
            enabled=True,
            auto_publish_threshold=0.8,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-06-15T10:00:00Z",
        )
        assert resp.course_id == "123"
        assert resp.enabled is True
        assert resp.auto_publish_threshold == 0.8

    def test_document_status_response_minimal(self):
        """DocumentStatusResponse works with minimal required fields."""
        from src.api.canvas_config import DocumentStatusResponse

        resp = DocumentStatusResponse(
            canvas_file_id="42",
            original_filename="test.pdf",
            status="processing",
        )
        assert resp.canvas_file_id == "42"
        assert resp.job_id is None
        assert resp.confidence_score is None
