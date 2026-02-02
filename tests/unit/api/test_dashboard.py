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

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from src.canvas.dashboard import router as dashboard_router
from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
from src.services.converter_client import ConverterClient

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
    mock.dismiss_file = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_converter():
    """Mock ConverterClient."""
    mock = MagicMock(spec=ConverterClient)
    mock.get_job_status = AsyncMock(return_value=None)
    mock.retry_job = AsyncMock(return_value=True)
    mock.submit_document = AsyncMock()
    mock.get_result_markdown = AsyncMock()
    mock.list_result_figures = AsyncMock(return_value=[])
    mock.download_figure = AsyncMock()
    mock.create_download_bundle = AsyncMock()
    return mock


@pytest.fixture
def mock_redis():
    """Mock Redis client that returns valid LTI session data."""
    mock = MagicMock()
    session_data = json.dumps({"course_id": "123", "user_sub": "user1"})

    async def mock_get(key):
        if "eq-pdf:lti:session:" in key:
            return session_data
        return None

    mock.get = AsyncMock(side_effect=mock_get)
    mock.set = AsyncMock(return_value=True)
    mock.exists = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.setnx = AsyncMock(return_value=True)
    mock.expire = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def dashboard_app(mock_config_service, mock_converter, mock_redis):
    """Create test app with dashboard routes."""
    app = FastAPI()
    app.include_router(dashboard_router)

    app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
    app.dependency_overrides[get_converter_client] = lambda: mock_converter
    app.dependency_overrides[get_redis_client] = lambda: mock_redis

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

    def test_expired_session_shows_expired_message(self, mock_config_service, mock_converter):
        """Dashboard shows expired message when lti_session token exists but is invalid."""
        app = FastAPI()
        app.include_router(dashboard_router)

        # Redis returns None for all session lookups (expired)
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard/123?lti_session=expired-token")

        assert response.status_code == 403
        assert "session has expired" in response.text.lower()

        app.dependency_overrides.clear()

    def test_missing_session_shows_launch_message(self, client):
        """Dashboard shows 'launch from Canvas' message when no lti_session param."""
        response = client.get("/lti/dashboard/123")
        assert response.status_code == 403
        assert "Please launch this tool from Canvas" in response.text
        # Should NOT mention expired
        assert "expired" not in response.text.lower()


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

    def test_empty_state_has_document_icon(self, client, mock_config_service):
        """Empty state includes an SVG document icon."""
        mock_config_service.list_processed_files.return_value = {}

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "<svg" in response.text
        # SVG is the document/file icon
        assert "stroke-linecap" in response.text

    def test_empty_state_links_to_settings(self, client, mock_config_service):
        """Empty state 'Settings' text links to the course settings page."""
        mock_config_service.list_processed_files.return_value = {}

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "/lti/dashboard/123/settings?lti_session=test-session" in response.text

    def test_empty_state_hides_table(self, client, mock_config_service):
        """Empty state does not render the document table."""
        mock_config_service.list_processed_files.return_value = {}

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "<table" not in response.text

    def test_renders_document_table(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "lecture.pdf" in response.text
        assert "Draft" in response.text

    def test_shows_published_status(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "View Page" in response.text

    def test_shows_failed_status_with_retry(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry" in response.text

    def test_shows_processing_status(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "Processing" in response.text
        # htmx polling should be present for processing rows
        assert "hx-trigger" in response.text
        assert "every 5s" in response.text

    def test_processing_row_polling_url_includes_session(self, client, mock_config_service, mock_converter):
        """Processing row polling URL points to the row endpoint with lti_session param."""
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        assert "/lti/dashboard/123/documents/42/row?lti_session=test-session" in response.text
        assert 'hx-swap="outerHTML"' in response.text

    def test_non_processing_rows_do_not_poll(self, client, mock_config_service, mock_converter):
        """Pending, completed, failed, and published rows do not have polling attributes."""
        mock_config_service.list_processed_files.return_value = {
            "10": ProcessedFile(
                canvas_file_id="10",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-10",
                status="pending",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="pending.pdf",
            ),
            "20": ProcessedFile(
                canvas_file_id="20",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-20",
                status="completed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="completed.pdf",
            ),
            "30": ProcessedFile(
                canvas_file_id="30",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-30",
                status="failed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="failed.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        # None of these statuses should trigger polling
        assert "every 5s" not in response.text
        assert "hx-trigger" not in response.text

    def test_mixed_status_rows_only_processing_polls(self, client, mock_config_service, mock_converter):
        """In a list with mixed statuses, only processing rows have polling attributes."""
        mock_config_service.list_processed_files.return_value = {
            "10": ProcessedFile(
                canvas_file_id="10",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-10",
                status="completed",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="done.pdf",
            ),
            "20": ProcessedFile(
                canvas_file_id="20",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-20",
                status="processing",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="active.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        # Only the processing document row should poll
        assert "/documents/20/row?" in response.text
        assert "every 5s" in response.text
        # The completed document row should NOT have a polling URL
        assert "/documents/10/row?" not in response.text

    def test_shows_settings_link(self, client):
        """Dashboard has a link to settings page."""
        response = client.get("/lti/dashboard/123?lti_session=test-session")
        assert response.status_code == 200
        assert "Settings" in response.text
        assert "/lti/dashboard/123/settings" in response.text

    def test_shows_confidence_score(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
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

    def test_save_result_container_has_indicator_class(self, client, mock_config_service):
        """Settings form has save-result container with save-indicator class for fade animation."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="save-result"' in response.text
        assert "save-indicator" in response.text

    def test_save_button_has_id_for_htmx_state(self, client, mock_config_service):
        """Save button has id for CSS-based htmx request state styling."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="save-btn"' in response.text

    def test_form_targets_save_result(self, client, mock_config_service):
        """Settings form htmx PUT targets #save-result with innerHTML swap."""
        mock_config_service.get_config.return_value = None

        response = client.get("/lti/dashboard/123/settings?lti_session=test-session")

        assert response.status_code == 200
        assert 'hx-target="#save-result"' in response.text
        assert 'hx-swap="innerHTML"' in response.text


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id} - Document Detail
# ===========================================================================


class TestDashboardDocumentDetail:
    """Tests for the document detail view."""

    def test_renders_document_detail(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
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

    def test_shows_failed_document_with_error(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "error": "Docling extraction timeout",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Docling extraction timeout" in response.text
        assert "Retry Processing" in response.text

    def test_shows_published_document_with_links(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
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

    def test_has_back_link(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Back to Documents" in response.text

    def test_shows_reprocess_button_for_published_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.95",
        }

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Re-process" in response.text
        assert "/reprocess" in response.text

    def test_shows_polling_for_processing_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "Processing" in response.text
        assert "hx-get" in response.text
        assert "detail_fragment" in response.text
        assert "every 5s" in response.text

    def test_no_polling_for_completed_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert "every 5s" not in response.text


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id}/detail_fragment
# ===========================================================================


class TestDashboardDetailFragment:
    """Tests for the htmx detail fragment polling endpoint."""

    def test_returns_fragment_for_processing_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="document-detail"' in response.text
        assert "Processing" in response.text
        assert "hx-get" in response.text
        assert "every 5s" in response.text

    def test_returns_fragment_for_completed_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
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

    def test_returns_fragment_for_published_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "Re-process" in response.text
        assert "View in Canvas" in response.text

    def test_returns_fragment_for_failed_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "error": "Timeout error",
        }

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry Processing" in response.text
        assert "Timeout error" in response.text
        # Retry button targets whole detail card for outerHTML swap (enables polling)
        assert 'hx-target="#document-detail"' in response.text
        assert 'hx-swap="outerHTML"' in response.text

    def test_processing_fragment_polling_url_preserves_session(self, client, mock_config_service, mock_converter):
        """Detail fragment for processing document includes lti_session in polling URL."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="inprogress.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=my-session-token")

        assert response.status_code == 200
        assert "lti_session=my-session-token" in response.text
        assert 'hx-swap="outerHTML"' in response.text
        assert 'hx-target="#document-detail"' in response.text

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

    def test_returns_404_when_job_missing(self, client, mock_config_service, mock_converter):
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
        mock_converter.retry_job.return_value = False
        mock_converter.get_job_status.return_value = None

        response = client.post("/lti/dashboard/123/documents/42/reprocess?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_when_s3_key_missing(self, client, mock_config_service, mock_converter):
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
        mock_converter.retry_job.return_value = False
        mock_converter.get_job_status.return_value = {
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

    def test_returns_404_when_job_missing(self, client, mock_config_service, mock_converter):
        """Returns 404 when job record is missing."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_converter.retry_job.return_value = False
        mock_converter.get_job_status.return_value = None

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 404

    def test_returns_400_when_s3_key_missing(self, client, mock_config_service, mock_converter):
        """Returns 400 when job has no S3 key."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_converter.retry_job.return_value = False
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "s3_key": "",
        }

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 400
        assert "Original file not found" in response.text

    def test_successful_retry_returns_processing_row(self, client, mock_config_service, mock_converter):
        """Successful retry queues PII job and returns updated row with processing status."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_converter.retry_job.return_value = True

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 200
        assert "Processing" in response.text
        assert 'id="doc-42"' in response.text
        assert "broken.pdf" in response.text
        # Processing row should have polling attrs
        assert "hx-get" in response.text
        assert "every 5s" in response.text
        mock_converter.retry_job.assert_awaited_once_with("job-1")
        mock_config_service.set_processed_file.assert_awaited_once()

    def test_successful_retry_from_detail_returns_detail_fragment(self, client, mock_config_service, mock_converter):
        """Retry from detail view returns detail fragment with polling when HX-Target is document-detail."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_converter.retry_job.return_value = True

        response = client.post(
            "/lti/dashboard/123/documents/42/retry?lti_session=test-session",
            headers={"HX-Target": "document-detail"},
        )

        assert response.status_code == 200
        assert 'id="document-detail"' in response.text
        assert "Processing" in response.text
        # Detail fragment should have polling attrs for continued updates
        assert "hx-get" in response.text
        assert "every 5s" in response.text
        assert "detail_fragment" in response.text

    def test_retry_returns_500_on_queue_error(self, client, mock_config_service, mock_converter):
        """Returns 500 with error message when queueing fails."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_converter.retry_job.side_effect = RuntimeError("Redis connection lost")

        response = client.post("/lti/dashboard/123/documents/42/retry?lti_session=test-session")

        assert response.status_code == 500
        assert "Retry failed" in response.text
        assert "Redis connection lost" in response.text


# ===========================================================================
# GET /lti/dashboard/{course_id}/documents/{file_id}/row - htmx Row Polling
# ===========================================================================


class TestDashboardDocumentRow:
    """Tests for the htmx row polling endpoint."""

    def test_returns_row_for_processing_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert 'id="doc-42"' in response.text
        assert "inprogress.pdf" in response.text
        assert "Processing" in response.text
        # Should include polling attrs so it keeps polling
        assert "hx-get" in response.text
        assert "every 5s" in response.text

    def test_returns_row_for_completed_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = {
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

    def test_returns_row_for_published_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert "View Page" in response.text
        assert "https://canvas.example.com/pages/lecture" in response.text

    def test_returns_row_for_failed_document(self, client, mock_config_service, mock_converter):
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry" in response.text

    def test_processing_row_polling_url_preserves_session(self, client, mock_config_service, mock_converter):
        """Row fragment for processing document includes lti_session in polling URL."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="inprogress.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=my-session-token")

        assert response.status_code == 200
        assert "lti_session=my-session-token" in response.text
        assert 'hx-swap="outerHTML"' in response.text

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

    def test_successful_publish_returns_published_row(self, client, mock_config_service):
        """Successful publish calls publisher and returns updated row with Published status."""
        from unittest.mock import patch

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
            auto_publish_threshold=0.8,
            canvas_api_token="test-token-abc",
        )

        mock_publisher_instance = AsyncMock()
        mock_canvas_client = AsyncMock()
        mock_redis_for_publish = AsyncMock()

        # get_redis_client is an async generator; the handler calls anext() on it
        async def fake_get_redis_client():
            yield mock_redis_for_publish

        mock_settings = MagicMock()
        mock_settings.canvas_api_url = "https://canvas.example.com"
        mock_settings.canvas_rate_limit_buffer = 10

        with (
            patch("src.canvas.publisher.CanvasPublisherService", return_value=mock_publisher_instance),
            patch("src.canvas.client.CanvasAPIClient", return_value=mock_canvas_client),
            patch("src.canvas.renderer.CanvasHTMLRenderer"),
            patch("src.dependencies.get_redis_client", side_effect=fake_get_redis_client),
            patch("src.config.settings", mock_settings),
        ):
            response = client.post("/lti/dashboard/123/documents/42/publish?lti_session=test-session")

        assert response.status_code == 200
        assert "Published" in response.text
        assert 'id="doc-42"' in response.text
        assert "lecture.pdf" in response.text
        mock_publisher_instance.publish.assert_awaited_once()

    def test_publish_returns_500_on_publisher_error(self, client, mock_config_service):
        """Returns 500 with error message when publisher raises an exception."""
        from unittest.mock import patch

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
            auto_publish_threshold=0.8,
            canvas_api_token="test-token-abc",
        )

        mock_publisher_instance = AsyncMock()
        mock_publisher_instance.publish.side_effect = RuntimeError("Canvas API timeout")
        mock_canvas_client = AsyncMock()
        mock_redis_for_publish = AsyncMock()

        async def fake_get_redis_client():
            yield mock_redis_for_publish

        mock_settings = MagicMock()
        mock_settings.canvas_api_url = "https://canvas.example.com"
        mock_settings.canvas_rate_limit_buffer = 10

        with (
            patch("src.canvas.publisher.CanvasPublisherService", return_value=mock_publisher_instance),
            patch("src.canvas.client.CanvasAPIClient", return_value=mock_canvas_client),
            patch("src.canvas.renderer.CanvasHTMLRenderer"),
            patch("src.dependencies.get_redis_client", side_effect=fake_get_redis_client),
            patch("src.config.settings", mock_settings),
        ):
            response = client.post("/lti/dashboard/123/documents/42/publish?lti_session=test-session")

        assert response.status_code == 500
        assert "Publish failed" in response.text
        assert "Canvas API timeout" in response.text


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

    def test_success_response_has_status_role(self, client, mock_config_service):
        """Success response includes role=status for accessibility."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 200
        assert 'role="status"' in response.text

    def test_success_response_has_green_styling(self, client, mock_config_service):
        """Success response has green styling classes for visual feedback."""
        mock_config_service.get_config.return_value = None

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 200
        assert "text-green-700" in response.text
        assert "bg-green-50" in response.text
        assert "border-green-200" in response.text

    def test_error_response_has_alert_role(self, client, mock_config_service):
        """Error response includes role=alert for accessibility."""
        mock_config_service.get_config.side_effect = RuntimeError("Redis down")

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 500
        assert 'role="alert"' in response.text

    def test_error_response_has_red_styling(self, client, mock_config_service):
        """Error response has red styling classes for visual feedback."""
        mock_config_service.get_config.side_effect = RuntimeError("Redis down")

        response = client.put(
            "/lti/dashboard/123/config?lti_session=test-session",
            data={"enabled": "on", "auto_publish_threshold": "0.85"},
        )

        assert response.status_code == 500
        assert "text-red-700" in response.text
        assert "bg-red-50" in response.text
        assert "Failed to save" in response.text


# ===========================================================================
# LTI Dashboard Launch
# ===========================================================================


class TestDashboardLaunch:
    """Tests for the LTI dashboard launch endpoint at /lti/dashboard."""

    @pytest.fixture
    def _lti_env(self):
        """Set up LTI environment for tests that need the LTI router."""
        import os
        import shutil
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

        for mod in list(sys.modules.keys()):
            if mod.startswith("src.lti") or mod == "src.config":
                del sys.modules[mod]

        from src.lti.config import get_tool_config
        from src.lti.keys import load_private_key, load_public_key

        get_tool_config.cache_clear()
        load_private_key.cache_clear()
        load_public_key.cache_clear()

        yield

        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_dashboard_launch_redirects_with_valid_session(self, _lti_env):
        """GET /lti/dashboard with valid session and course_id redirects to index."""
        from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
        from src.lti.router import router as lti_router

        app = FastAPI()
        app.include_router(lti_router)

        session_data = json.dumps({"course_id": "456", "user_sub": "user1"})
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=session_data)
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.exists = AsyncMock(return_value=True)

        mock_config_svc = AsyncMock(spec=CourseConfigService)
        mock_job = MagicMock(spec=ConverterClient)
        mock_job.get_job_status = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_course_config_service] = lambda: mock_config_svc
        app.dependency_overrides[get_converter_client] = lambda: mock_job

        test_client = TestClient(app, follow_redirects=False)
        response = test_client.get("/lti/dashboard?lti_session=abc123&course_id=456")

        assert response.status_code == 302
        assert "/lti/dashboard/456" in response.headers["location"]
        assert "lti_session=abc123" in response.headers["location"]

        app.dependency_overrides.clear()

    def test_dashboard_launch_rejects_invalid_session(self, _lti_env):
        """GET /lti/dashboard with invalid/expired session shows error using dashboard template."""
        from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
        from src.lti.router import router as lti_router

        app = FastAPI()
        app.include_router(lti_router)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_config_svc = AsyncMock(spec=CourseConfigService)
        mock_job = MagicMock(spec=ConverterClient)
        mock_job.get_job_status = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_course_config_service] = lambda: mock_config_svc
        app.dependency_overrides[get_converter_client] = lambda: mock_job

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard?lti_session=expired-token&course_id=456")

        assert response.status_code == 403
        assert "session has expired" in response.text.lower()
        assert "Session Error" in response.text

        app.dependency_overrides.clear()

    def test_dashboard_launch_rejects_course_id_mismatch(self, _lti_env):
        """GET /lti/dashboard rejects when course_id doesn't match session."""
        from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
        from src.lti.router import router as lti_router

        app = FastAPI()
        app.include_router(lti_router)

        # Session has course_id=789 but request has course_id=456
        session_data = json.dumps({"course_id": "789", "user_sub": "user1"})
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=session_data)

        mock_config_svc = AsyncMock(spec=CourseConfigService)
        mock_job = MagicMock(spec=ConverterClient)
        mock_job.get_job_status = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_course_config_service] = lambda: mock_config_svc
        app.dependency_overrides[get_converter_client] = lambda: mock_job

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard?lti_session=abc123&course_id=456")

        assert response.status_code == 403
        assert "does not match" in response.text

        app.dependency_overrides.clear()

    def test_dashboard_launch_error_without_params(self, _lti_env):
        """GET /lti/dashboard without params shows dashboard-styled error."""
        from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
        from src.lti.router import router as lti_router

        app = FastAPI()
        app.include_router(lti_router)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_config_svc = AsyncMock(spec=CourseConfigService)
        mock_job = MagicMock(spec=ConverterClient)
        mock_job.get_job_status = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_course_config_service] = lambda: mock_config_svc
        app.dependency_overrides[get_converter_client] = lambda: mock_job

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard")

        assert response.status_code == 403
        assert "Session Error" in response.text
        assert "Please launch this tool from Canvas" in response.text

        app.dependency_overrides.clear()

    def test_dashboard_launch_uses_dashboard_template(self, _lti_env):
        """GET /lti/dashboard error pages use the dashboard Jinja2 template, not inline HTML."""
        from src.dependencies import get_converter_client, get_course_config_service, get_redis_client
        from src.lti.router import router as lti_router

        app = FastAPI()
        app.include_router(lti_router)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        mock_config_svc = AsyncMock(spec=CourseConfigService)
        mock_job = MagicMock(spec=ConverterClient)
        mock_job.get_job_status = AsyncMock(return_value=None)

        app.dependency_overrides[get_redis_client] = lambda: mock_redis
        app.dependency_overrides[get_course_config_service] = lambda: mock_config_svc
        app.dependency_overrides[get_converter_client] = lambda: mock_job

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard")

        # Should use the dashboard template (base.html), not inline HTML
        assert "dashboard.css" in response.text
        assert "htmx.org" in response.text

        app.dependency_overrides.clear()


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

    def test_source_css_contains_save_indicator_animation(self):
        """Source CSS contains save-indicator fade-out animation."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "src" / "dashboard.css"
        content = css_path.read_text()
        assert ".save-indicator" in content
        assert "fade-out" in content

    def test_source_css_contains_htmx_request_state(self):
        """Source CSS contains htmx-request state for save button."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "src" / "dashboard.css"
        content = css_path.read_text()
        assert ".htmx-request #save-btn" in content

    def test_dist_css_contains_save_indicator_animation(self):
        """Dist CSS contains save-indicator fade-out animation."""
        from pathlib import Path

        css_path = Path(__file__).resolve().parents[3] / "src" / "canvas" / "static" / "dist" / "dashboard.css"
        content = css_path.read_text()
        assert ".save-indicator" in content
        assert "fade-out" in content

    def test_tailwind_config_exists_at_root(self):
        """Root tailwind.config.js exists and points to canvas templates."""
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[3] / "tailwind.config.js"
        assert config_path.exists(), "tailwind.config.js should exist at project root"
        content = config_path.read_text()
        assert "src/canvas/templates/**/*.html" in content


# ===========================================================================
# LTI Session Storage (create/validate)
# ===========================================================================


class TestLTISessionStorage:
    """Tests for LTI session creation and validation in RedisLaunchDataStorage."""

    @pytest.mark.asyncio
    async def test_create_session_returns_token(self):
        """create_session_async returns a non-empty token string."""
        from src.lti.adapters import RedisLaunchDataStorage

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(return_value=True)
        storage = RedisLaunchDataStorage(mock_redis)

        token = await storage.create_session_async({"course_id": "123"})

        assert isinstance(token, str)
        assert len(token) > 0
        mock_redis.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_session_stores_data_in_redis(self):
        """create_session_async stores JSON data with session prefix and TTL."""
        from src.lti.adapters import RedisLaunchDataStorage

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock(return_value=True)
        storage = RedisLaunchDataStorage(mock_redis)

        session_data = {"course_id": "456", "user_sub": "user1"}
        token = await storage.create_session_async(session_data)

        call_args = mock_redis.set.call_args
        key = call_args[0][0]
        value = call_args[0][1]
        assert key.startswith("eq-pdf:lti:session:")
        assert token in key
        stored = json.loads(value)
        assert stored["course_id"] == "456"
        assert stored["user_sub"] == "user1"
        assert call_args[1]["ex"] > 0  # TTL is set

    @pytest.mark.asyncio
    async def test_validate_session_returns_data_for_valid_token(self):
        """validate_session_async returns session data for a valid token."""
        from src.lti.adapters import RedisLaunchDataStorage

        session_data = {"course_id": "123", "user_sub": "user1"}
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(session_data))
        storage = RedisLaunchDataStorage(mock_redis)

        result = await storage.validate_session_async("valid-token")

        assert result == session_data
        mock_redis.get.assert_awaited_once()
        call_key = mock_redis.get.call_args[0][0]
        assert "eq-pdf:lti:session:valid-token" == call_key

    @pytest.mark.asyncio
    async def test_validate_session_returns_none_for_expired_token(self):
        """validate_session_async returns None for expired/invalid token."""
        from src.lti.adapters import RedisLaunchDataStorage

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)
        storage = RedisLaunchDataStorage(mock_redis)

        result = await storage.validate_session_async("expired-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_validate_session_returns_none_for_invalid_json(self):
        """validate_session_async returns None for corrupted session data."""
        from src.lti.adapters import RedisLaunchDataStorage

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value="not-valid-json{{{")
        storage = RedisLaunchDataStorage(mock_redis)

        result = await storage.validate_session_async("bad-data-token")

        assert result is None


# ===========================================================================
# Dashboard Session Validation (Redis-backed)
# ===========================================================================


class TestDashboardSessionValidation:
    """Tests for Redis-backed LTI session validation in dashboard views."""

    def test_index_returns_403_with_invalid_redis_session(self, mock_config_service, mock_converter):
        """Dashboard returns 403 with expired message when lti_session token is not found in Redis."""
        app = FastAPI()
        app.include_router(dashboard_router)

        # Redis returns None for all session lookups
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard/123?lti_session=invalid-token")

        assert response.status_code == 403
        assert "session has expired" in response.text.lower()

        app.dependency_overrides.clear()

    def test_index_returns_403_without_lti_session_param(self, mock_config_service, mock_converter):
        """Dashboard returns 403 with launch message when no lti_session param is present."""
        app = FastAPI()
        app.include_router(dashboard_router)

        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)

        app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard/123")

        assert response.status_code == 403
        assert "Please launch this tool from Canvas" in response.text

        app.dependency_overrides.clear()

    def test_index_accessible_with_valid_redis_session(self, mock_config_service, mock_converter):
        """Dashboard returns 200 when lti_session token is valid in Redis."""
        app = FastAPI()
        app.include_router(dashboard_router)

        session_data = json.dumps({"course_id": "123", "user_sub": "user1"})
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=session_data)

        app.dependency_overrides[get_course_config_service] = lambda: mock_config_service
        app.dependency_overrides[get_converter_client] = lambda: mock_converter
        app.dependency_overrides[get_redis_client] = lambda: mock_redis

        test_client = TestClient(app)
        response = test_client.get("/lti/dashboard/123?lti_session=valid-token")

        assert response.status_code == 200

        app.dependency_overrides.clear()


# ===========================================================================
# Status Sync (lazy write-back from converter job to ProcessedFile)
# ===========================================================================


class TestStatusSync:
    """Tests for lazy write-back of converter job status to ProcessedFile."""

    def test_row_syncs_completed_status(self, client, mock_config_service, mock_converter):
        """Row route syncs ProcessedFile from 'processing' to 'completed' when job is done."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.92",
        }

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        # Should show "Completed" badge (not "Processing")
        assert "Completed" in response.text
        assert "Publish" in response.text
        # Should NOT have polling attrs since status is now terminal
        assert "every 5s" not in response.text
        # Should have written the updated status back
        mock_config_service.set_processed_file.assert_awaited_once()
        saved_pf = mock_config_service.set_processed_file.call_args[0][2]
        assert saved_pf.status == "completed"

    def test_row_syncs_failed_status(self, client, mock_config_service, mock_converter):
        """Row route syncs ProcessedFile from 'processing' to 'failed' when job failed."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "failed",
            "error": "OCR timeout",
        }

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Failed" in response.text
        assert "Retry" in response.text
        assert "every 5s" not in response.text
        mock_config_service.set_processed_file.assert_awaited_once()
        saved_pf = mock_config_service.set_processed_file.call_args[0][2]
        assert saved_pf.status == "failed"

    def test_row_sync_failure_still_returns_response(self, client, mock_config_service, mock_converter):
        """Row route still returns 200 even if set_processed_file raises."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.85",
        }
        mock_config_service.set_processed_file.side_effect = RuntimeError("Redis down")

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        # Should still return 200 with correct status despite write failure
        assert response.status_code == 200
        assert "Completed" in response.text

    def test_detail_fragment_syncs_completed_status(self, client, mock_config_service, mock_converter):
        """Detail fragment route syncs ProcessedFile from 'processing' to 'completed'."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.90",
        }

        response = client.get("/lti/dashboard/123/documents/42/detail_fragment?lti_session=test-session")

        assert response.status_code == 200
        assert "Completed" in response.text
        assert "Publish to Canvas" in response.text
        assert "every 5s" not in response.text
        mock_config_service.set_processed_file.assert_awaited_once()
        saved_pf = mock_config_service.set_processed_file.call_args[0][2]
        assert saved_pf.status == "completed"

    def test_index_syncs_stale_processing_files(self, client, mock_config_service, mock_converter):
        """Index route syncs stale ProcessedFile when converter job is terminal."""
        mock_config_service.list_processed_files.return_value = {
            "42": ProcessedFile(
                canvas_file_id="42",
                canvas_updated_at="2024-06-15T10:30:00Z",
                job_id="job-1",
                status="processing",
                processed_at="2024-06-15T11:00:00Z",
                original_filename="lecture.pdf",
            ),
        }
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.88",
        }

        response = client.get("/lti/dashboard/123?lti_session=test-session")

        assert response.status_code == 200
        mock_config_service.set_processed_file.assert_awaited_once()
        saved_pf = mock_config_service.set_processed_file.call_args[0][2]
        assert saved_pf.status == "completed"

    def test_no_sync_when_already_terminal(self, client, mock_config_service, mock_converter):
        """Does NOT call set_processed_file when ProcessedFile is already in terminal state."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="completed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="lecture.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = {
            "job_id": "job-1",
            "status": "completed",
            "confidence_score": "0.88",
        }

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        mock_config_service.set_processed_file.assert_not_awaited()


# ===========================================================================
# DELETE /lti/dashboard/{course_id}/documents/{file_id} - Remove Document
# ===========================================================================


class TestDashboardDeleteDocument:
    """Tests for the delete document endpoint."""

    def test_dismiss_returns_empty_body(self, client, mock_config_service):
        """Successful dismiss returns 200 with empty body for htmx outerHTML swap."""
        mock_config_service.dismiss_file.return_value = True

        response = client.delete("/lti/dashboard/123/documents/42?lti_session=test-session")

        assert response.status_code == 200
        assert response.text == ""
        mock_config_service.dismiss_file.assert_awaited_once_with("123", "42")

    def test_dismiss_succeeds_even_if_record_missing(self, client, mock_config_service):
        """Dismiss returns 200 even if record was already gone (still adds to dismissed set)."""
        mock_config_service.dismiss_file.return_value = False

        response = client.delete("/lti/dashboard/123/documents/999?lti_session=test-session")

        assert response.status_code == 200
        assert response.text == ""
        mock_config_service.dismiss_file.assert_awaited_once_with("123", "999")

    def test_processing_row_has_remove_button(self, client, mock_config_service, mock_converter):
        """Processing rows show a Remove button."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="processing",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="stuck.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Remove" in response.text
        assert "hx-delete" in response.text

    def test_failed_row_has_remove_button(self, client, mock_config_service, mock_converter):
        """Failed rows show both Retry and Remove buttons."""
        mock_config_service.get_processed_file.return_value = ProcessedFile(
            canvas_file_id="42",
            canvas_updated_at="2024-06-15T10:30:00Z",
            job_id="job-1",
            status="failed",
            processed_at="2024-06-15T11:00:00Z",
            original_filename="broken.pdf",
        )
        mock_config_service.get_publish_result.return_value = None
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Retry" in response.text
        assert "Remove" in response.text

    def test_published_row_has_no_remove_button(self, client, mock_config_service, mock_converter):
        """Published rows do NOT show a Remove button."""
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
        mock_converter.get_job_status.return_value = None

        response = client.get("/lti/dashboard/123/documents/42/row?lti_session=test-session")

        assert response.status_code == 200
        assert "Remove" not in response.text

