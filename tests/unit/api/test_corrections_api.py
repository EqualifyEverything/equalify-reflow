"""Unit tests for Corrections API endpoints.

Tests the correction approval workflow API including:
- GET /{job_id}/review - get_correction_review endpoint
- PATCH /{job_id} - submit_correction_decision endpoint
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from src.dependencies import (
    get_correction_approval_service,
    get_job_service,
    get_s3_url_service,
)
from src.main import app

pytestmark = pytest.mark.unit


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def api_key_headers():
    """Generate API key headers for authenticated requests."""
    api_keys_env = os.getenv("API_KEYS", "")
    if api_keys_env:
        keys = api_keys_env.split(",")
        api_key = keys[0].strip() if keys else ""
    else:
        api_key = "test-key-fallback"

    return {"X-API-Key": api_key}


@pytest.fixture
def mock_correction_approval_service():
    """Mock CorrectionApprovalService."""
    mock = MagicMock()
    mock.validate_correction_approval_token = AsyncMock()
    mock.process_correction_approval_decision = AsyncMock()
    return mock


@pytest.fixture
def mock_job_service():
    """Mock JobService."""
    mock = MagicMock()
    mock.get_job = AsyncMock()
    mock.update_job_status = AsyncMock()
    return mock


@pytest.fixture
def mock_url_service():
    """Mock S3URLService."""
    mock = MagicMock()
    mock.generate_url = AsyncMock(return_value="https://example.com/mock-url")
    mock.results_bucket = "equalify-pdf-results"
    mock.temp_bucket = "equalify-pdf-temp"
    return mock


@pytest.fixture
def sample_job_data():
    """Create sample job data with correction results."""
    return {
        "job_id": "test-123",
        "status": "awaiting_correction_approval",
        "correction_results": [
            {
                "page": 1,
                "corrections": [
                    {
                        "type": "heading_level",
                        "original": "Course Title",
                        "corrected": "## Course Title",
                        "confidence": 0.95,
                        "explanation": "Visual layout indicates H2",
                        "is_auto_applied": True,
                    },
                    {
                        "type": "list_structure",
                        "original": "- Item 1 - Item 2",
                        "corrected": "- Item 1\n- Item 2",
                        "confidence": 0.88,
                        "explanation": "Split into separate list items",
                        "is_auto_applied": False,
                    },
                ],
            },
            {
                "page": 2,
                "corrections": [
                    {
                        "type": "heading_level",
                        "original": "Section Two",
                        "corrected": "### Section Two",
                        "confidence": 0.92,
                        "explanation": "Visual layout indicates H3",
                        "is_auto_applied": True,
                    },
                ],
            },
        ],
        "original_markdown_key": "results/test-123/original.md",
        "corrected_markdown_key": "results/test-123/corrected.md",
        "page_image_keys": ["temp/test-123/page-1.png", "temp/test-123/page-2.png"],
        "correction_expires_at": "2025-01-15T14:00:00Z",
        "original_filename": "test.pdf",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:01:00Z",
    }


class TestGetCorrectionReview:
    """Tests for GET /api/v1/corrections/{job_id}/review endpoint."""

    @pytest.mark.asyncio
    async def test_get_correction_review_success(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
        sample_job_data,
    ):
        """Test successful retrieval of correction review details."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = sample_job_data

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Verify response structure
            assert data["job_id"] == job_id
            assert data["total_corrections"] == 3
            assert data["auto_applied_count"] == 2
            assert data["manual_review_count"] == 1
            assert data["expires_at"] == "2025-01-15T14:00:00Z"

            # Verify by_type aggregation
            assert data["by_type"]["heading_level"] == 2
            assert data["by_type"]["list_structure"] == 1

            # Verify by_page aggregation
            assert data["by_page"]["1"] == 2
            assert data["by_page"]["2"] == 1

            # Verify corrections list
            assert len(data["corrections"]) == 3
            assert data["corrections"][0]["page"] == 1
            assert data["corrections"][0]["type"] == "heading_level"
            assert data["corrections"][0]["is_auto_applied"] is True

            # Verify URLs structure
            assert "urls" in data
            assert "original_markdown" in data["urls"]
            assert "corrected_markdown" in data["urls"]
            assert "page_images" in data["urls"]
            assert len(data["urls"]["page_images"]) == 2

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_overall_confidence_calculation(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
        sample_job_data,
    ):
        """Test that overall_confidence is calculated correctly as average."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = sample_job_data

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Average of 0.95, 0.88, 0.92 = 0.9166...
            expected_confidence = (0.95 + 0.88 + 0.92) / 3
            assert abs(data["overall_confidence"] - expected_confidence) < 0.001

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_token_mismatch_401(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 401 when token does not match job_id."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        # Token maps to different job
        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            "different-job-456"
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Token does not match job ID" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_invalid_token_401(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 401 when token is invalid or expired."""
        job_id = "test-123"
        token = "invalid-or-expired-token"

        mock_correction_approval_service.validate_correction_approval_token.side_effect = (
            HTTPException(
                status_code=401, detail="Invalid or expired correction approval token"
            )
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Invalid or expired" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_job_not_found_404(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 404 when job not found."""
        job_id = "nonexistent-job"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = None

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "Job not found" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_no_correction_results_404(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 404 when job exists but has no correction_results."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "awaiting_correction_approval",
            "correction_results": [],  # Empty correction results
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "No correction results found" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_missing_markdown_keys_500(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 500 when job is missing markdown keys."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "awaiting_correction_approval",
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "Test",
                            "corrected": "## Test",
                            "confidence": 0.9,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        }
                    ],
                }
            ],
            # Missing original_markdown_key and corrected_markdown_key
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Markdown keys not found" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_server_error_500(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test 500 when unexpected server error occurs."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.side_effect = Exception("Database connection failed")

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to retrieve correction review details" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_snippet_truncation(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test that original/corrected snippets are truncated to 200 chars."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        long_text = "A" * 300  # Text longer than 200 chars

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "paragraph",
                            "original": long_text,
                            "corrected": long_text,
                            "confidence": 0.85,
                            "explanation": "Reformatted paragraph",
                            "is_auto_applied": True,
                        }
                    ],
                }
            ],
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
            "page_image_keys": [],
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Verify snippets are truncated to 200 chars
            assert len(data["corrections"][0]["original_snippet"]) == 200
            assert len(data["corrections"][0]["corrected_snippet"]) == 200

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_get_correction_review_empty_page_images(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test review response with no page images."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "Test",
                            "corrected": "## Test",
                            "confidence": 0.9,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        }
                    ],
                }
            ],
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
            "page_image_keys": [],  # Empty list
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["urls"]["page_images"] == []

        finally:
            app.dependency_overrides.clear()


class TestSubmitCorrectionDecision:
    """Tests for PATCH /api/v1/corrections/{job_id} endpoint."""

    @pytest.mark.asyncio
    async def test_submit_approval_success(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test successful approval of corrections."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_correction_approval_service.process_correction_approval_decision.return_value = {
            "job_id": job_id,
            "status": "completed",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability and structure",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == job_id
            assert data["status"] == "completed"
            assert data["decision"] == "approved"
            assert "corrected markdown is now final" in data["message"]

            # Verify service was called correctly
            mock_correction_approval_service.process_correction_approval_decision.assert_called_once_with(
                job_id=job_id,
                decision={
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability and structure",
                },
            )

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_rejection_success(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test successful rejection of corrections."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_correction_approval_service.process_correction_approval_decision.return_value = {
            "job_id": job_id,
            "status": "completed",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "rejected",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Original formatting is correct as-is",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["job_id"] == job_id
            assert data["status"] == "completed"
            assert data["decision"] == "rejected"
            assert "original markdown is now final" in data["message"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_token_mismatch_401(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test 401 when token does not match job_id."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        # Token maps to different job
        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            "different-job-456"
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Token does not match job ID" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_token_too_short_400(
        self, client, api_key_headers
    ):
        """Test 400 when token is too short (< 10 chars)."""
        job_id = "test-123"

        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "short",  # Only 5 chars, minimum is 10
                "decision": "approved",
                "reviewed_by": "instructor@uic.edu",
                "justification": "Text corrections improve readability",
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        # Pydantic validation error for min_length

    @pytest.mark.asyncio
    async def test_submit_decision_justification_too_short_400(
        self, client, api_key_headers
    ):
        """Test 400 when justification is too short (< 10 chars)."""
        job_id = "test-123"

        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "valid-secure-token-12345",
                "decision": "approved",
                "reviewed_by": "instructor@uic.edu",
                "justification": "Short",  # Only 5 chars, minimum is 10
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        # Pydantic validation error for min_length

    @pytest.mark.asyncio
    async def test_submit_decision_invalid_decision_value_422(
        self, client, api_key_headers
    ):
        """Test 422 when decision is not 'approved' or 'rejected'."""
        job_id = "test-123"

        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "valid-secure-token-12345",
                "decision": "maybe",  # Invalid value
                "reviewed_by": "instructor@uic.edu",
                "justification": "Text corrections improve readability",
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        # Pydantic validation error for Literal type

    @pytest.mark.asyncio
    async def test_submit_decision_invalid_token_401(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test 401 when token is invalid or expired."""
        job_id = "test-123"
        token = "expired-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.side_effect = (
            HTTPException(
                status_code=401, detail="Invalid or expired correction approval token"
            )
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert "Invalid or expired" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_value_error_400(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test 400 when ValueError is raised from service (e.g., job in wrong state)."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_correction_approval_service.process_correction_approval_decision.side_effect = (
            ValueError("Job test-123 cannot be approved from status 'processing'")
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "cannot be approved from status" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_server_error_500(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
    ):
        """Test 500 when unexpected server error occurs."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_correction_approval_service.process_correction_approval_decision.side_effect = (
            Exception("S3 upload failed")
        )

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service

        try:
            response = client.patch(
                f"/api/v1/corrections/{job_id}",
                json={
                    "token": token,
                    "decision": "approved",
                    "reviewed_by": "instructor@uic.edu",
                    "justification": "Text corrections improve readability",
                },
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            assert "Failed to process correction decision" in response.json()["detail"]

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_submit_decision_missing_required_fields_422(
        self, client, api_key_headers
    ):
        """Test 422 when required fields are missing."""
        job_id = "test-123"

        # Missing 'decision' field
        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "valid-secure-token-12345",
                "reviewed_by": "instructor@uic.edu",
                "justification": "Text corrections improve readability",
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_submit_decision_reviewed_by_too_short_422(
        self, client, api_key_headers
    ):
        """Test 422 when reviewed_by is too short (< 3 chars)."""
        job_id = "test-123"

        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "valid-secure-token-12345",
                "decision": "approved",
                "reviewed_by": "ab",  # Only 2 chars, minimum is 3
                "justification": "Text corrections improve readability",
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_submit_decision_justification_too_long_422(
        self, client, api_key_headers
    ):
        """Test 422 when justification exceeds max length (1000 chars)."""
        job_id = "test-123"

        response = client.patch(
            f"/api/v1/corrections/{job_id}",
            json={
                "token": "valid-secure-token-12345",
                "decision": "approved",
                "reviewed_by": "instructor@uic.edu",
                "justification": "A" * 1001,  # Exceeds 1000 char limit
            },
            headers=api_key_headers,
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestCorrectionReviewAggregation:
    """Tests specifically for aggregation calculations in review endpoint."""

    @pytest.mark.asyncio
    async def test_aggregation_with_single_correction(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test aggregation with only one correction."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "Test",
                            "corrected": "## Test",
                            "confidence": 0.9,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        }
                    ],
                }
            ],
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
            "page_image_keys": [],
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            assert data["total_corrections"] == 1
            assert data["auto_applied_count"] == 1
            assert data["manual_review_count"] == 0
            assert data["overall_confidence"] == 0.9
            assert data["by_type"]["heading_level"] == 1
            assert data["by_page"]["1"] == 1

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_aggregation_all_manual_review(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test aggregation when all corrections need manual review."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "Test",
                            "corrected": "## Test",
                            "confidence": 0.6,
                            "explanation": "Low confidence",
                            "is_auto_applied": False,
                        },
                        {
                            "type": "list_structure",
                            "original": "List",
                            "corrected": "- List",
                            "confidence": 0.5,
                            "explanation": "Very low confidence",
                            "is_auto_applied": False,
                        },
                    ],
                }
            ],
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
            "page_image_keys": [],
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            assert data["total_corrections"] == 2
            assert data["auto_applied_count"] == 0
            assert data["manual_review_count"] == 2
            assert data["overall_confidence"] == 0.55  # Average of 0.6 and 0.5

        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_aggregation_multiple_correction_types(
        self,
        client,
        api_key_headers,
        mock_correction_approval_service,
        mock_job_service,
        mock_url_service,
    ):
        """Test aggregation correctly counts multiple correction types."""
        job_id = "test-123"
        token = "valid-secure-token-12345"

        mock_correction_approval_service.validate_correction_approval_token.return_value = (
            job_id
        )
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "correction_results": [
                {
                    "page": 1,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "H1",
                            "corrected": "# H1",
                            "confidence": 0.9,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        },
                        {
                            "type": "heading_level",
                            "original": "H2",
                            "corrected": "## H2",
                            "confidence": 0.9,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        },
                        {
                            "type": "list_structure",
                            "original": "List",
                            "corrected": "- List",
                            "confidence": 0.8,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        },
                    ],
                },
                {
                    "page": 2,
                    "corrections": [
                        {
                            "type": "heading_level",
                            "original": "H3",
                            "corrected": "### H3",
                            "confidence": 0.85,
                            "explanation": "Test",
                            "is_auto_applied": True,
                        },
                        {
                            "type": "table_format",
                            "original": "Table",
                            "corrected": "| Table |",
                            "confidence": 0.75,
                            "explanation": "Test",
                            "is_auto_applied": False,
                        },
                    ],
                },
            ],
            "original_markdown_key": "results/test-123/original.md",
            "corrected_markdown_key": "results/test-123/corrected.md",
            "page_image_keys": [],
            "correction_expires_at": "2025-01-15T14:00:00Z",
        }

        app.dependency_overrides[get_correction_approval_service] = (
            lambda: mock_correction_approval_service
        )
        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service

        try:
            response = client.get(
                f"/api/v1/corrections/{job_id}/review?token={token}",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            assert data["total_corrections"] == 5
            assert data["auto_applied_count"] == 4
            assert data["manual_review_count"] == 1

            # Verify by_type counts
            assert data["by_type"]["heading_level"] == 3
            assert data["by_type"]["list_structure"] == 1
            assert data["by_type"]["table_format"] == 1

            # Verify by_page counts
            assert data["by_page"]["1"] == 3
            assert data["by_page"]["2"] == 2

        finally:
            app.dependency_overrides.clear()
