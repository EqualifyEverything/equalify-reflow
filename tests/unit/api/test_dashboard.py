"""Unit tests for instructor dashboard routes (PRD-08).

Tests cover:
- GET /lti/dashboard/{course_id} (document list view)
- GET /lti/dashboard/{course_id}/settings (settings view)
- GET /lti/dashboard/{course_id}/documents/{file_id} (document detail)
- POST /lti/dashboard/{course_id}/documents/{file_id}/publish (htmx publish)
- POST /lti/dashboard/{course_id}/documents/{file_id}/retry (htmx retry)
- PUT /lti/dashboard/{course_id}/config (htmx settings save)
- LTI session validation (403 without session)
- Dashboard LTI launch redirect
- course_navigation placement in LTI config
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from src.canvas.dashboard import router as dashboard_router
from src.dependencies import get_course_config_service, get_job_service

pytestmark = pytest.mark.unit


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
def dashboard_app(mock_config_service, mock_job_svc):
    """Create test app with dashboard routes."""
    app = FastAPI()
    app.include_router(dashboard_router)

    app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
    app.dependency_overrides[get_job_service] = lambda: mock_job_svc

    yield app

    app.dependency_overrides.clear()


@pytest.fixture
def client(dashboard_app):
    """Create test client."""
    return TestClient(dashboard_app)


# ===========================================================================
# LTI Session Validation
# ===========================================================================


class TestLTISessionValidation:
    """Dashboard routes require LTI session parameter."""

    def test_index_returns_403_without_lti_session(self, client):
        """GET /lti/dashboard/{course_id} returns 403 without lti_session."""
        response = client.get("/lti/dashboard/123")
        assert response.status_code == 403
        assert "Please launch this tool from Canvas" in response.text

    def test_settings_returns_403_without_lti_session(self, client):
        """GET /lti/dashboard/{course_id}/settings returns 403 without lti_session."""
        response = client.get("/lti/dashboard/123/settings")
        assert response.status_code == 403
        assert "Please launch this tool from Canvas" in response.text

    def test_document_detail_returns_403_without_lti_session(self, client, mock_config_service):
        """GET /lti/dashboard/{course_id}/documents/{file_id} returns 403 without lti_session."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        response = client.get("/lti/dashboard/123/documents/42")
        assert response.status_code == 403

    def test_index_accessible_with_lti_session(self, client):
        """GET /lti/dashboard/{course_id} works with lti_session param."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert response.status_code == 200


# ===========================================================================
# GET /lti/dashboard/{course_id} - Document List View
# ===========================================================================


class TestDashboardIndex:
    """Tests for the main document list view."""

    def test_renders_empty_state(self, client, mock_config_service):
        """Shows empty state message when no documents exist."""
        mock_config_service.list_processed_files.return_value = {}

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "No PDFs tracked yet" in response.text

    def test_renders_document_table(self, client, mock_config_service, mock_job_svc):
        """Shows document table when documents exist."""
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
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "lecture.pdf" in response.text
        assert "Draft" in response.text

    def test_shows_published_status(self, client, mock_config_service, mock_job_svc):
        """Shows 'Published' for documents with publish results."""
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
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
            "canvas_page_id": "999",
        }
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "View Page" in response.text

    def test_shows_failed_status_with_retry(self, client, mock_config_service, mock_job_svc):
        """Shows 'Failed' status with retry button."""
        mock_config_service.list_processed_files.return_value = {
            "42": ProcessedFile(
                canvas_file_id="42",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-1",
                status="failed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="broken.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry" in response.text

    def test_shows_processing_status(self, client, mock_config_service, mock_job_svc):
        """Shows 'Processing' status with polling attribute."""
        mock_config_service.list_processed_files.return_value = {
            "42": ProcessedFile(
                canvas_file_id="42",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-1",
                status="processing",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="inprogress.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Processing" in response.text
        # htmx polling should be present for processing rows
        assert "hx-trigger" in response.text
        assert "every 5s" in response.text

    def test_shows_settings_link(self, client):
        """Dashboard has a link to settings page."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert response.status_code == 200
        assert "Settings" in response.text
        assert "/lti/dashboard/123/settings" in response.text

    def test_shows_confidence_score(self, client, mock_config_service, mock_job_svc):
        """Shows confidence score as percentage."""
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
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "95%" in response.text

    def test_calls_list_processed_files(self, client, mock_config_service):
        """Calls list_processed_files with the course_id."""
        client.get("/lti/dashboard/789?lti_session=test-session")
        mock_config_service.list_processed_files.assert_awaited_once_with("789")


# ===========================================================================
# GET /lti/dashboard/{course_id}/settings - Settings View
# ===========================================================================


class TestDashboardSettings:
    """Tests for the settings view."""

    def test_renders_settings_form(self, client, mock_config_service):
        """Shows settings form with current values."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.8,
        )

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Course Settings" in response.text
        assert "checked" in response.text  # enabled checkbox
        assert "0.80" in response.text  # threshold value

    def test_renders_default_settings(self, client, mock_config_service):
        """Shows default settings when course is not configured."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Course Settings" in response.text
        assert "1.00" in response.text  # default threshold

    def test_has_back_link(self, client, mock_config_service):
        """Settings page has back link to documents."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Back to Documents" in response.text
        assert "/lti/dashboard/123?" in response.text

    def test_has_htmx_form(self, client, mock_config_service):
        """Settings form uses htmx PUT for save."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "hx-put" in response.text
        assert "/config" in response.text

    def test_renders_enable_toggle_unchecked_when_disabled(self, client, mock_config_service):
        """Enable toggle is unchecked when course auto-processing is disabled."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=False,
            auto_publish_threshold=0.9,
        )

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        # The checkbox input should exist but NOT be checked
        assert 'name="enabled"' in response.text
        assert 'type="checkbox"' in response.text
        # "checked" should not appear as an attribute on the checkbox
        # (the word may appear elsewhere, so check the form section specifically)
        text = response.text
        checkbox_area = text[text.index('name="enabled"') - 100 : text.index('name="enabled"') + 50]
        assert "checked" not in checkbox_area

    def test_renders_threshold_slider_attributes(self, client, mock_config_service):
        """Threshold input renders with correct min/max/step attributes."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=False,
            auto_publish_threshold=0.65,
        )

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert 'name="auto_publish_threshold"' in response.text
        assert 'min="0"' in response.text
        assert 'max="1"' in response.text
        assert 'step="0.05"' in response.text
        assert 'value="0.65"' in response.text
        assert "0.65" in response.text  # displayed value

    def test_renders_save_button(self, client, mock_config_service):
        """Settings form has a save button."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Save Settings" in response.text

    def test_renders_toggle_description(self, client, mock_config_service):
        """Settings form shows description text for the toggle."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Enable auto-processing for this course" in response.text

    def test_renders_threshold_description(self, client, mock_config_service):
        """Settings form shows description text for the threshold."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert "Auto-publish confidence threshold" in response.text
        assert "1.00" in response.text  # mentions draft behavior at 1.00


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id} - Document Detail
# ===========================================================================


class TestDashboardDocumentDetail:
    """Tests for the document detail view."""

    def test_renders_document_detail(self, client, mock_config_service, mock_job_svc):
        """Shows document details."""
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
            "confidence_score": "0.92",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "lecture.pdf" in response.text
        assert "Draft" in response.text
        assert "92%" in response.text
        assert "Publish to Canvas" in response.text

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.get("/lti/dashboard/123/documents/999?lti_session=test-session")

        assert response.status_code == 404

    def test_shows_failed_document_with_error(self, client, mock_config_service, mock_job_svc):
        """Shows error message for failed documents."""
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
            "error": "Docling extraction timeout",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Docling extraction timeout" in response.text
        assert "Retry Processing" in response.text

    def test_shows_published_document_with_links(self, client, mock_config_service, mock_job_svc):
        """Shows published document with Canvas page and download links."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
            "canvas_page_id": "999",
            "download_url": "https://s3.example.com/bundle.zip",
            "published_at": "2024-06-15T12:00:00Z",
        }
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "View in Canvas" in response.text
        assert "https://canvas.example.com/pages/lecture" in response.text
        assert "Download Bundle" in response.text

    def test_has_back_link(self, client, mock_config_service, mock_job_svc):
        """Document detail has back link to index."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Back to Documents" in response.text

    def test_shows_reprocess_button_for_published_document(self, client, mock_config_service, mock_job_svc):
        """Shows Re-process button for published documents."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
            "canvas_page_id": "999",
            "published_at": "2024-06-15T12:00:00Z",
        }
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Re-process" in response.text
        assert "/reprocess" in response.text

    def test_shows_polling_for_processing_document(self, client, mock_config_service, mock_job_svc):
        """Shows htmx polling attributes for processing documents."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="inprogress.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Processing" in response.text
        assert "hx-get" in response.text
        assert "detail_fragment" in response.text
        assert "every 5s" in response.text

    def test_no_polling_for_completed_document(self, client, mock_config_service, mock_job_svc):
        """No htmx polling for completed documents."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "every 5s" not in response.text


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id}/detail_fragment
# ===========================================================================


class TestDashboardDetailFragment:
    """Tests for the htmx detail fragment polling endpoint."""

    def test_returns_fragment_for_processing_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML detail fragment with polling attrs for processing document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="inprogress.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="document-detail"' in response.text
        assert "Processing" in response.text
        assert "hx-get" in response.text
        assert "every 5s" in response.text

    def test_returns_fragment_for_completed_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML detail fragment with publish button for completed document."""
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
            "confidence_score": "0.88",
        }

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Publish to Canvas" in response.text
        assert "88%" in response.text
        # Completed should NOT have polling
        assert "every 5s" not in response.text

    def test_returns_fragment_for_published_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML detail fragment with reprocess button for published document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
            "published_at": "2024-06-15T12:00:00Z",
        }
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "Re-process" in response.text
        assert "View in Canvas" in response.text

    def test_returns_fragment_for_failed_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML detail fragment with retry button and error for failed document."""
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
            "error": "Timeout error",
        }

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry Processing" in response.text
        assert "Timeout error" in response.text

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 with error fragment when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.get("/lti/dashboard/123/documents/999/detail_fragment?lti_session=test-session")

        assert response.status_code == 404
        assert "Document not found" in response.text


# ===========================================================================
# POST /lti/dashboard/{course_id}/documents/{file_id}/reprocess - htmx Re-process
# ===========================================================================


class TestDashboardReprocess:
    """Tests for the htmx reprocess endpoint."""

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.post("/lti/dashboard/123/documents/999/reprocess?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_for_non_published_document(self, client, mock_config_service):
        """Returns 400 when trying to reprocess a non-published document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None

        response = client.post("/lti/dashboard/123/documents/42/reprocess?lti_session=test-session")

        assert response.status_code == 400
        assert "not published" in response.text

    def test_returns_404_when_job_missing(self, client, mock_config_service, mock_job_svc):
        """Returns 404 when job record is missing."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
        }
        mock_job_svc.get_job.return_value = None

        response = client.post("/lti/dashboard/123/documents/42/reprocess?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_when_s3_key_missing(self, client, mock_config_service, mock_job_svc):
        """Returns 400 when job has no S3 key."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
        }
        mock_job_svc.get_job.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "s3_key": "",
        }

        response = client.post("/lti/dashboard/123/documents/42/reprocess?lti_session=test-session")

        assert response.status_code == 400
        assert "Original file not found" in response.text


# ===========================================================================
# POST /lti/dashboard/{course_id}/documents/{file_id}/retry - htmx Retry
# ===========================================================================


class TestDashboardRetry:
    """Tests for the htmx retry endpoint."""

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.post("/lti/dashboard/123/documents/999/retry?lti_session=test-session")

        assert response.status_code == 404

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

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 400
        assert "Cannot retry" in response.text

    def test_returns_404_when_job_missing(self, client, mock_config_service, mock_job_svc):
        """Returns 404 when job record is missing."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_job_svc.get_job.return_value = None

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_when_s3_key_missing(self, client, mock_config_service, mock_job_svc):
        """Returns 400 when job has no S3 key."""
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

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 400
        assert "Original file not found" in response.text


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id}/row - htmx Row Polling
# ===========================================================================


class TestDashboardDocumentRow:
    """Tests for the htmx row polling endpoint."""

    def test_returns_row_for_processing_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML row fragment with polling attrs for processing document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="inprogress.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="doc-42"' in response.text
        assert "inprogress.pdf" in response.text
        assert "Processing" in response.text
        # Should include polling attrs so it keeps polling
        assert "hx-get" in response.text
        assert "every 5s" in response.text

    def test_returns_row_for_completed_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML row fragment with publish button for completed document."""
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
            "confidence_score": "0.88",
        }

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "lecture.pdf" in response.text
        assert "Completed" in response.text
        assert "Publish" in response.text
        assert "88%" in response.text
        # Completed rows should NOT have polling attrs
        assert "every 5s" not in response.text

    def test_returns_row_for_published_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML row with View Page link for published document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = {
            "canvas_page_url": "https://canvas.example.com/pages/lecture",
        }
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "View Page" in response.text
        assert "https://canvas.example.com/pages/lecture" in response.text

    def test_returns_row_for_failed_document(self, client, mock_config_service, mock_job_svc):
        """Returns HTML row with retry button for failed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_job_svc.get_job.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry" in response.text

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 with error row when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.get("/lti/dashboard/123/documents/999/row?lti_session=test-session")

        assert response.status_code == 404
        assert "Document not found" in response.text


# ===========================================================================
# POST /lti/dashboard/{course_id}/documents/{file_id}/publish - htmx Publish
# ===========================================================================


class TestDashboardPublish:
    """Tests for the htmx publish endpoint."""

    def test_returns_404_for_missing_document(self, client, mock_config_service):
        """Returns 404 when document not found."""
        mock_config_service.get_processed_file.return_value = None

        response = client.post("/lti/dashboard/123/documents/999/publish?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_for_non_completed_document(self, client, mock_config_service):
        """Returns 400 when trying to publish non-completed document."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )

        response = client.post("/lti/dashboard/123/documents/42/publish?lti_session=test-session")

        assert response.status_code == 400
        assert "Cannot publish" in response.text

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

        response = client.post("/lti/dashboard/123/documents/42/publish?lti_session=test-session")

        assert response.status_code == 400
        assert "API token" in response.text


# ===========================================================================
# PUT /lti/dashboard/{course_id}/config - htmx Settings Save
# ===========================================================================


class TestDashboardConfigUpdate:
    """Tests for the htmx settings save endpoint."""

    def test_saves_settings(self, client, mock_config_service):
        """Saves settings and returns success message."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 200
        assert "saved successfully" in response.text
        mock_config_service.set_config.assert_awaited_once()

    def test_saves_disabled_state(self, client, mock_config_service):
        """Saves settings with disabled state (no checkbox)."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.8,
            created_at="2024-01-01T00:00:00Z",
        )

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"auto_publish_threshold": "0.90"},
        )

        assert response.status_code == 200
        assert "saved successfully" in response.text
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.enabled is False

    def test_clamps_threshold_to_valid_range(self, client, mock_config_service):
        """Clamps threshold to 0.0-1.0 range."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"auto_publish_threshold": "5.0"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.auto_publish_threshold == 1.0

    def test_handles_invalid_threshold(self, client, mock_config_service):
        """Handles invalid threshold value gracefully."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"auto_publish_threshold": "not-a-number"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.auto_publish_threshold == 1.0  # default fallback

    def test_clamps_negative_threshold_to_zero(self, client, mock_config_service):
        """Clamps negative threshold to 0.0."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"auto_publish_threshold": "-0.5"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.auto_publish_threshold == 0.0

    def test_saves_enabled_state_with_threshold(self, client, mock_config_service):
        """Saves both enabled state and threshold together."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.75"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.enabled is True
        assert saved_config.auto_publish_threshold == 0.75

    def test_preserves_existing_created_at(self, client, mock_config_service):
        """Preserves created_at timestamp from existing config."""
        mock_config_service.get_config.return_value = CourseConfig(
            enabled=True,
            auto_publish_threshold=0.9,
            created_at="2024-01-15T10:00:00Z",
        )

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.created_at == "2024-01-15T10:00:00Z"

    def test_sets_updated_at_timestamp(self, client, mock_config_service):
        """Sets updated_at timestamp on save."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.5"},
        )

        assert response.status_code == 200
        call_args = mock_config_service.set_config.call_args[0]
        saved_config = call_args[1]
        assert saved_config.updated_at != ""


# ===========================================================================
# LTI Dashboard Launch
# ===========================================================================


class TestDashboardLaunch:
    """Tests for the LTI dashboard launch endpoint."""

    def test_dashboard_launch_redirects(self):
        """GET /lti/dashboard with session and course_id redirects."""
        import os
        import sys
        import tempfile
        from pathlib import Path

        from jwcrypto import jwk

        # Generate temp keys
        temp_dir = tempfile.mkdtemp()
        private_key_path = Path(temp_dir) / "lti_private.pem"
        public_key_path = Path(temp_dir) / "lti_public.pem"

        key = jwk.JWK.generate(kty="RSA", size=2048)
        private_key_path.write_bytes(key.export_to_pem(private_key=True, password=None))
        public_key_path.write_bytes(key.export_to_pem(private_key=False, password=None))

        # Set env vars
        orig_env = {}
        env_vars = {
            "LTI_ENABLED": "true",
            "LTI_ISSUER": "https://canvas.test.instructure.com",
            "LTI_CLIENT_ID": "10000000000001",
            "LTI_DEPLOYMENT_ID": "10000000000001",
            "LTI_AUTH_LOGIN_URL": "https://canvas.test.instructure.com/api/lti/authorize_redirect",
            "LTI_AUTH_TOKEN_URL": "https://canvas.test.instructure.com/login/oauth2/token",
            "LTI_JWKS_URL": "https://canvas.test.instructure.com/api/lti/security/jwks",
            "LTI_PRIVATE_KEY_PATH": str(private_key_path),
            "LTI_PUBLIC_KEY_PATH": str(public_key_path),
        }

        for k, v in env_vars.items():
            orig_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            # Clear modules
            for mod in list(sys.modules.keys()):
                if mod.startswith("src.lti") or mod == "src.config":
                    del sys.modules[mod]

            from src.lti.config import get_tool_config
            from src.lti.keys import load_private_key, load_public_key

            get_tool_config.cache_clear()
            load_private_key.cache_clear()
            load_public_key.cache_clear()

            from src.dependencies import get_redis_client
            from src.lti.router import router as lti_router

            app = FastAPI()
            app.include_router(lti_router)
            mock_redis = MagicMock()
            mock_redis.get = AsyncMock(return_value=None)
            app.dependency_overrides[get_redis_client] = lambda: mock_redis

            test_client = TestClient(app, follow_redirects=False)

            response = test_client.get("/lti/dashboard?lti_session=abc123&course_id=456")

            assert response.status_code == 302
            assert "/lti/dashboard/456" in response.headers["location"]
            assert "lti_session=abc123" in response.headers["location"]

            app.dependency_overrides.clear()

        finally:
            for k, v in orig_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_dashboard_launch_error_without_params(self):
        """GET /lti/dashboard without params shows error."""
        import os
        import sys
        import tempfile
        from pathlib import Path

        from jwcrypto import jwk

        temp_dir = tempfile.mkdtemp()
        private_key_path = Path(temp_dir) / "lti_private.pem"
        public_key_path = Path(temp_dir) / "lti_public.pem"

        key = jwk.JWK.generate(kty="RSA", size=2048)
        private_key_path.write_bytes(key.export_to_pem(private_key=True, password=None))
        public_key_path.write_bytes(key.export_to_pem(private_key=False, password=None))

        orig_env = {}
        env_vars = {
            "LTI_ENABLED": "true",
            "LTI_ISSUER": "https://canvas.test.instructure.com",
            "LTI_CLIENT_ID": "10000000000001",
            "LTI_DEPLOYMENT_ID": "10000000000001",
            "LTI_AUTH_LOGIN_URL": "https://canvas.test.instructure.com/api/lti/authorize_redirect",
            "LTI_AUTH_TOKEN_URL": "https://canvas.test.instructure.com/login/oauth2/token",
            "LTI_JWKS_URL": "https://canvas.test.instructure.com/api/lti/security/jwks",
            "LTI_PRIVATE_KEY_PATH": str(private_key_path),
            "LTI_PUBLIC_KEY_PATH": str(public_key_path),
        }

        for k, v in env_vars.items():
            orig_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            for mod in list(sys.modules.keys()):
                if mod.startswith("src.lti") or mod == "src.config":
                    del sys.modules[mod]

            from src.lti.config import get_tool_config
            from src.lti.keys import load_private_key, load_public_key

            get_tool_config.cache_clear()
            load_private_key.cache_clear()
            load_public_key.cache_clear()

            from src.dependencies import get_redis_client
            from src.lti.router import router as lti_router

            app = FastAPI()
            app.include_router(lti_router)
            mock_redis = MagicMock()
            mock_redis.get = AsyncMock(return_value=None)
            app.dependency_overrides[get_redis_client] = lambda: mock_redis

            test_client = TestClient(app)

            response = test_client.get("/lti/dashboard")

            assert response.status_code == 200
            assert "Launch Error" in response.text or "Please launch this tool from Canvas" in response.text

            app.dependency_overrides.clear()

        finally:
            for k, v in orig_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# LTI Config - course_navigation placement
# ===========================================================================


class TestCourseNavigationPlacement:
    """Tests for course_navigation placement in LTI config."""

    def test_config_includes_course_navigation_placement(self):
        """LTI config includes course_navigation placement."""
        from src.lti.config import get_canvas_config_json

        config = get_canvas_config_json("https://pdf.equalify.app")

        placements = config["extensions"][0]["settings"]["placements"]
        nav_placements = [p for p in placements if p["placement"] == "course_navigation"]

        assert len(nav_placements) == 1
        nav = nav_placements[0]
        assert nav["text"] == "Equalify Dashboard"
        assert nav["enabled"] is True
        assert nav["message_type"] == "LtiResourceLinkRequest"
        assert "/lti/dashboard" in nav["target_link_uri"]

    def test_config_still_has_file_menu_placement(self):
        """LTI config still includes file_menu placement."""
        from src.lti.config import get_canvas_config_json

        config = get_canvas_config_json("https://pdf.equalify.app")

        placements = config["extensions"][0]["settings"]["placements"]
        file_placements = [p for p in placements if p["placement"] == "file_menu"]

        assert len(file_placements) == 1


# ===========================================================================
# Template Rendering Basics
# ===========================================================================


class TestTemplateRendering:
    """Basic template rendering tests."""

    def test_base_template_includes_dashboard_css(self, client):
        """Base template links dashboard CSS stylesheet."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert "dashboard.css" in response.text

    def test_base_template_includes_htmx(self, client):
        """Base template loads htmx from CDN."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert "htmx.org" in response.text

    def test_index_renders_html(self, client):
        """Index returns valid HTML."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text
        assert "</html>" in response.text

    def test_settings_renders_html(self, client, mock_config_service):
        """Settings returns valid HTML."""
        mock_config_service.get_config.return_value = None
        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text

    def test_no_x_frame_options_deny(self, client):
        """Dashboard does not set X-Frame-Options: DENY (Canvas iframe)."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        xfo = response.headers.get("x-frame-options", "")
        assert xfo.upper() != "DENY"


# ===========================================================================
# Dashboard CSS - Plain CSS (no Tailwind directives)
# ===========================================================================


class TestDashboardCSS:
    """Tests that dashboard CSS uses plain CSS without Tailwind build dependencies."""

    def test_source_css_has_no_tailwind_directives(self):
        """Source CSS must not contain @tailwind directives."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "src" / "dashboard.css"
        content = css_path.read_text()
        assert "@tailwind" not in content

    def test_source_css_contains_plain_rules(self):
        """Source CSS contains plain CSS rules (not just directives)."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "src" / "dashboard.css"
        content = css_path.read_text()
        # Should contain actual CSS property declarations
        assert "box-sizing:" in content
        assert "font-family:" in content
        assert "display: flex" in content

    def test_dist_css_has_no_tailwind_directives(self):
        """Compiled dist CSS must not contain @tailwind directives."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "dist" / "dashboard.css"
        content = css_path.read_text()
        assert "@tailwind" not in content

    def test_dist_css_contains_utility_classes_used_in_templates(self):
        """Dist CSS defines all utility classes referenced by dashboard templates."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "dist" / "dashboard.css"
        content = css_path.read_text()

        # Key classes used across templates
        required_classes = [
            ".bg-gray-50",
            ".bg-white",
            ".min-h-screen",
            ".max-w-6xl",
            ".flex",
            ".text-sm",
            ".rounded-lg",
            ".shadow",
            ".hover\\:bg-blue-700:hover",
            ".animate-pulse",
            ".inline-block",
            ".grid-cols-3",
            ".divide-y",
        ]
        for cls in required_classes:
            assert cls in content, f"Missing CSS class: {cls}"

    def test_tailwind_config_exists_at_root(self):
        """Root tailwind.config.js exists and points to canvas templates."""
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[3] / "tailwind.config.js"
        assert config_path.exists(), "tailwind.config.js should exist at project root"
        content = config_path.read_text()
        assert "src/canvas/templates/**/*.html" in content
