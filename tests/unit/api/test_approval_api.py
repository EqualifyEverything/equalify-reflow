"""Unit tests for Approval API endpoints.

Tests the approval workflow endpoints:
- GET /{token}/review - Get review details for PII-flagged documents
- POST /{token}/decision - Submit approval/denial decisions

All endpoints are part of the PII approval workflow where documents
flagged with potential PII require human review before processing.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.dependencies import get_redis_client, get_s3_client, get_s3_url_service, get_storage_service
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
def mock_redis_client():
    """Mock Redis client."""
    mock = MagicMock()
    mock.hgetall = AsyncMock(return_value={})
    mock.hget = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=True)
    mock.zrem = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def mock_s3_client():
    """Mock S3 client."""
    mock = MagicMock()
    mock.delete_object = MagicMock(return_value={})
    mock.head_object = MagicMock(return_value={})
    return mock


@pytest.fixture
def mock_storage_service():
    """Mock StorageService."""
    mock = MagicMock()
    mock.upload_file = AsyncMock(return_value="jobs/test/file.pdf")
    mock.download_file = AsyncMock(return_value=b"test content")
    mock.delete_file = AsyncMock(return_value=True)
    mock.temp_bucket = "equalify-pdf-temp"
    mock.results_bucket = "equalify-pdf-results"
    return mock


@pytest.fixture
def mock_s3_url_service():
    """Mock S3URLService."""
    mock = MagicMock()
    mock.generate_url = AsyncMock(return_value="https://example.com/mock-url")
    mock.temp_bucket = "equalify-pdf-temp"
    mock.results_bucket = "equalify-pdf-results"
    return mock


@pytest.fixture
def valid_job_data():
    """Create valid job data for testing."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=4)
    return {
        "job_id": "test-job-123",
        "status": "awaiting_approval",
        "s3_key": "temp/test-upload.pdf",
        "original_filename": "test.pdf",
        "pii_findings": [
            {"entity_type": "EMAIL_ADDRESS", "text": "test@example.com", "score": 0.95},
            {"entity_type": "PHONE_NUMBER", "text": "555-123-4567", "score": 0.88},
        ],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "approval_expires_at": expires_at.isoformat(),
        "approval_token": "valid-token-abc123",
    }


@pytest.fixture
def expired_job_data(valid_job_data):
    """Create expired job data for testing."""
    expired_time = datetime.now(UTC) - timedelta(hours=1)
    job_data = valid_job_data.copy()
    job_data["approval_expires_at"] = expired_time.isoformat()
    return job_data


class TestGetReviewDetails:
    """Tests for GET /api/v1/approval/{token}/review endpoint."""

    @pytest.mark.asyncio
    async def test_get_review_details_success(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Test successfully retrieving review details with valid token."""
        # Setup mock to return valid job via ApprovalService.validate_approval_token
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token-abc123/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # Verify response structure matches ReviewDetailsResponse
                assert data["job_id"] == "test-job-123"
                assert data["status"] == "awaiting_approval"
                assert len(data["pii_findings"]) == 2
                assert data["pii_findings"][0]["entity_type"] == "EMAIL_ADDRESS"
                assert data["pii_findings"][1]["entity_type"] == "PHONE_NUMBER"
                assert "created_at" in data
                assert "expires_at" in data
                assert data["s3_key"] == "temp/test-upload.pdf"
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_review_details_invalid_token(
        self, client, api_key_headers, mock_redis_client, mock_s3_client
    ):
        """Test 404 response for invalid approval token."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/invalid-token-xyz/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_404_NOT_FOUND
                data = response.json()
                assert "Invalid or expired approval token" in data["detail"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_review_details_expired_token(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, expired_job_data
    ):
        """Test 404 response for expired approval token."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            # ApprovalService.validate_approval_token returns None for expired tokens
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/expired-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_404_NOT_FOUND
                data = response.json()
                assert "Invalid or expired approval token" in data["detail"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_review_details_server_error(
        self, client, api_key_headers, mock_redis_client, mock_s3_client
    ):
        """Test 500 response when ApprovalService raises exception."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(
                side_effect=Exception("Redis connection failed")
            )
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/some-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
                data = response.json()
                assert "Failed to retrieve review details" in data["detail"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_review_details_empty_pii_findings(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Test response with empty PII findings list."""
        job_data = valid_job_data.copy()
        job_data["pii_findings"] = []

        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()
                assert data["pii_findings"] == []
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_review_details_missing_pii_findings_field(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Test response when pii_findings field is missing from job data."""
        job_data = valid_job_data.copy()
        del job_data["pii_findings"]

        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()
                # Should default to empty list
                assert data["pii_findings"] == []
            finally:
                app.dependency_overrides.clear()


class TestSubmitDecision:
    """Tests for POST /api/v1/approval/{token}/decision endpoint."""

    @pytest.mark.asyncio
    async def test_submit_approval_success(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test successfully approving a PII-flagged document."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token-abc123/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "justification": "Instructor contact info in syllabus is acceptable for this course material.",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # Verify response structure matches ApprovalResponse
                assert data["message"] == "Job approved - processing started"
                assert data["job_id"] == "test-job-123"
                assert data["decision"] == "approved"

                # Verify quick_approve was called
                mock_approval_service.quick_approve.assert_called_once_with("test-job-123")
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_denial_success(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test successfully denying a PII-flagged document."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_deny = AsyncMock(return_value=None)
            mock_approval_service.process_denial_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token-abc123/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "denied",
                        "justification": "Document contains student personal information that should not be processed.",
                        "reviewed_by": "admin@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # Verify response structure
                assert data["message"] == "Job denied - cleanup started"
                assert data["job_id"] == "test-job-123"
                assert data["decision"] == "denied"

                # Verify quick_deny was called
                mock_approval_service.quick_deny.assert_called_once_with("test-job-123")
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_invalid_token(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 404 response for invalid/expired token in decision submission."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/invalid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_404_NOT_FOUND
                data = response.json()
                assert "Invalid or expired approval token" in data["detail"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_invalid_decision_value(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response for invalid decision value (not 'approved' or 'denied')."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "decision": "maybe",  # Invalid value
                    "reviewed_by": "faculty@uic.edu",
                },
            )

            # Pydantic validation should reject invalid Literal value
            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_justification_too_short(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response when justification is too short (min 10 chars)."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "decision": "approved",
                    "justification": "OK",  # Too short (min 10 chars required)
                    "reviewed_by": "faculty@uic.edu",
                },
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            data = response.json()
            # Check that validation error mentions justification field
            assert any("justification" in str(err).lower() for err in data.get("detail", []))
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_reviewed_by_too_short(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response when reviewed_by is too short (min 3 chars)."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "decision": "approved",
                    "reviewed_by": "ab",  # Too short (min 3 chars required)
                },
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            data = response.json()
            # Check that validation error mentions reviewed_by field
            assert any("reviewed_by" in str(err).lower() for err in data.get("detail", []))
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_missing_reviewed_by(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response when reviewed_by is missing (required field)."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "decision": "approved",
                    # reviewed_by is missing
                },
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_missing_decision(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response when decision is missing (required field)."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "reviewed_by": "faculty@uic.edu",
                    # decision is missing
                },
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_server_error(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test 500 response when ApprovalService raises exception."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
                data = response.json()
                assert "Failed to process approval decision" in data["detail"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_approval_without_justification(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test approval without optional justification field."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "faculty@uic.edu",
                        # justification is optional and not provided
                    },
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()
                assert data["decision"] == "approved"
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_justification_max_length(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response when justification exceeds max length (1000 chars)."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={
                    "decision": "approved",
                    "justification": "x" * 1001,  # Exceeds max 1000 chars
                    "reviewed_by": "faculty@uic.edu",
                },
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()


class TestBackgroundTaskScheduling:
    """Tests to verify background tasks are properly scheduled."""

    @pytest.mark.asyncio
    async def test_approval_schedules_background_task(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Verify background task is scheduled for approval processing."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "justification": "This is a valid justification for approval.",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK

                # Note: Background tasks are scheduled via FastAPI BackgroundTasks
                # The actual task execution happens after response is sent
                # We verify the response indicates background processing started
                data = response.json()
                assert "processing started" in data["message"]
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_denial_schedules_cleanup_task(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Verify background cleanup task is scheduled for denial."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_deny = AsyncMock(return_value=None)
            mock_approval_service.process_denial_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "denied",
                        "justification": "This document contains sensitive student data.",
                        "reviewed_by": "admin@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK

                # Verify the response indicates cleanup started
                data = response.json()
                assert "cleanup started" in data["message"]
            finally:
                app.dependency_overrides.clear()


class TestValueError400Response:
    """Tests for ValueError handling returning 400 status."""

    @pytest.mark.asyncio
    async def test_value_error_returns_400(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test that ValueError from service returns 400 Bad Request."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(
                side_effect=ValueError("Invalid job state for approval")
            )
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_400_BAD_REQUEST
                data = response.json()
                assert "Invalid job state for approval" in data["detail"]
            finally:
                app.dependency_overrides.clear()


class TestResponseStructure:
    """Tests to verify response models match expected structure."""

    @pytest.mark.asyncio
    async def test_review_details_response_structure(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Verify ReviewDetailsResponse has all required fields."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # All required fields from ReviewDetailsResponse
                required_fields = ["job_id", "status", "pii_findings", "created_at", "expires_at", "s3_key"]
                for field in required_fields:
                    assert field in data, f"Missing required field: {field}"

                # Verify pii_findings structure
                for finding in data["pii_findings"]:
                    assert "entity_type" in finding
                    assert "text" in finding
                    assert "score" in finding
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_approval_response_structure(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Verify ApprovalResponse has all required fields."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # All required fields from ApprovalResponse
                required_fields = ["message", "job_id", "decision"]
                for field in required_fields:
                    assert field in data, f"Missing required field: {field}"
            finally:
                app.dependency_overrides.clear()


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_justification_exactly_min_length(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test justification with exactly minimum length (10 chars)."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "justification": "1234567890",  # Exactly 10 chars
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_justification_exactly_max_length(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test justification with exactly maximum length (1000 chars)."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "justification": "x" * 1000,  # Exactly 1000 chars
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_reviewed_by_exactly_min_length(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test reviewed_by with exactly minimum length (3 chars)."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "reviewed_by": "abc",  # Exactly 3 chars
                    },
                )

                assert response.status_code == status.HTTP_200_OK
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_empty_request_body(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
    ):
        """Test 422 response for empty request body."""
        app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            response = client.post(
                "/api/v1/approval/some-token/decision",
                headers=api_key_headers,
                json={},
            )

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_special_characters_in_justification(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        valid_job_data,
    ):
        """Test justification with special characters and unicode."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=valid_job_data)
            mock_approval_service.quick_approve = AsyncMock(return_value=None)
            mock_approval_service.process_approval_background = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_storage_service] = lambda: mock_storage_service
            app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

            try:
                response = client.post(
                    "/api/v1/approval/valid-token/decision",
                    headers=api_key_headers,
                    json={
                        "decision": "approved",
                        "justification": "Valid justification with special chars: <>&\"' and unicode: ",
                        "reviewed_by": "faculty@uic.edu",
                    },
                )

                assert response.status_code == status.HTTP_200_OK
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_very_long_token(
        self,
        client,
        api_key_headers,
        mock_redis_client,
        mock_s3_client,
    ):
        """Test handling of very long token in path."""
        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=None)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                very_long_token = "a" * 1000
                response = client.get(
                    f"/api/v1/approval/{very_long_token}/review",
                    headers=api_key_headers,
                )

                # Should return 404 (invalid token) not 500
                assert response.status_code == status.HTTP_404_NOT_FOUND
            finally:
                app.dependency_overrides.clear()


class TestPIIFindingsValidation:
    """Tests for PII findings structure in responses."""

    @pytest.mark.asyncio
    async def test_pii_findings_with_multiple_types(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Test response with various PII entity types."""
        job_data = valid_job_data.copy()
        job_data["pii_findings"] = [
            {"entity_type": "EMAIL_ADDRESS", "text": "test@example.com", "score": 0.95},
            {"entity_type": "PHONE_NUMBER", "text": "555-123-4567", "score": 0.88},
            {"entity_type": "CREDIT_CARD", "text": "4111-****-****-1111", "score": 0.99},
            {"entity_type": "US_SSN", "text": "***-**-1234", "score": 0.92},
            {"entity_type": "PERSON", "text": "John Smith", "score": 0.75},
        ]

        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()
                assert len(data["pii_findings"]) == 5

                # Verify all entity types are present
                entity_types = [f["entity_type"] for f in data["pii_findings"]]
                assert "EMAIL_ADDRESS" in entity_types
                assert "PHONE_NUMBER" in entity_types
                assert "CREDIT_CARD" in entity_types
                assert "US_SSN" in entity_types
                assert "PERSON" in entity_types
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_pii_findings_score_values(
        self, client, api_key_headers, mock_redis_client, mock_s3_client, valid_job_data
    ):
        """Test PII findings with various confidence scores."""
        job_data = valid_job_data.copy()
        job_data["pii_findings"] = [
            {"entity_type": "EMAIL_ADDRESS", "text": "test@example.com", "score": 0.0},
            {"entity_type": "PHONE_NUMBER", "text": "555-123-4567", "score": 0.5},
            {"entity_type": "PERSON", "text": "Jane Doe", "score": 1.0},
        ]

        with patch("src.api.approval.ApprovalService") as MockApprovalService:
            mock_approval_service = MagicMock()
            mock_approval_service.validate_approval_token = AsyncMock(return_value=job_data)
            MockApprovalService.return_value = mock_approval_service

            app.dependency_overrides[get_redis_client] = lambda: mock_redis_client
            app.dependency_overrides[get_s3_client] = lambda: mock_s3_client

            try:
                response = client.get(
                    "/api/v1/approval/valid-token/review",
                    headers=api_key_headers,
                )

                assert response.status_code == status.HTTP_200_OK
                data = response.json()

                # Verify scores are correctly returned
                scores = [f["score"] for f in data["pii_findings"]]
                assert 0.0 in scores
                assert 0.5 in scores
                assert 1.0 in scores
            finally:
                app.dependency_overrides.clear()
