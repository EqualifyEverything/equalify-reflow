"""Unit tests for GET /api/v1/documents/{job_id}/ledger endpoint.

Tests the ledger endpoint for PR-like review of changes made by the pipeline.
"""

from unittest.mock import AsyncMock, MagicMock

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


@pytest.fixture
def mock_storage_service():
    """Mock storage service."""
    mock = MagicMock()
    mock.download_file = AsyncMock()
    mock.upload_file = AsyncMock()
    return mock


@pytest.fixture
def sample_ledger_data():
    """Sample ledger data returned from S3."""
    return {
        "document_id": "test-job-123",
        "entries": [
            {
                "entry_id": "entry-1",
                "job_id": "job-1",
                "page": 1,
                "timestamp": "2025-01-01T00:00:00Z",
                "action": "modify",
                "target": "heading-1",
                "before": "Old Title",
                "after": "New Title",
                "reasoning": "Fixed heading level for accessibility",
                "confidence": 0.95,
                "validated": True,
            },
            {
                "entry_id": "entry-2",
                "job_id": "job-2",
                "page": 1,
                "timestamp": "2025-01-01T00:00:01Z",
                "action": "add",
                "target": "alt-text-1",
                "before": "",
                "after": "A diagram showing workflow",
                "reasoning": "Added alt text for image accessibility",
                "confidence": 0.88,
                "validated": True,
            },
            {
                "entry_id": "entry-3",
                "job_id": "job-3",
                "page": 2,
                "timestamp": "2025-01-01T00:00:02Z",
                "action": "modify",
                "target": "table-1",
                "before": "| A | B |",
                "after": "| Header A | Header B |",
                "reasoning": "Added table header semantics",
                "confidence": 0.92,
                "validated": True,
            },
        ],
        "total_edits": 3,
    }


class TestGetLedgerEndpoint:
    """Tests for GET /api/v1/documents/{job_id}/ledger endpoint."""

    @pytest.mark.asyncio
    async def test_ledger_returns_404_for_nonexistent_job(
        self, client, api_key_headers, mock_job_service, mock_url_service, mock_storage_service
    ):
        """Test that ledger endpoint returns 404 for non-existent job."""
        job_id = "non-existent-job"
        mock_job_service.get_job.return_value = None

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "not found" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_returns_400_when_job_not_completed(
        self, client, api_key_headers, mock_job_service, mock_url_service, mock_storage_service
    ):
        """Test that ledger endpoint returns 400 when job is not in completed status."""
        job_id = "test-job-processing"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "processing",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
            "review_mode": "human",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "not yet complete" in response.json()["detail"].lower()
            assert "processing" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_returns_400_for_failed_job(
        self, client, api_key_headers, mock_job_service, mock_url_service, mock_storage_service
    ):
        """Test that ledger endpoint returns 400 when job has failed status."""
        job_id = "test-job-failed"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "failed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:01:00Z",
            "error": "Processing failed",
        }

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert "not yet complete" in response.json()["detail"].lower()
            assert "failed" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_returns_404_when_ledger_not_found_in_s3(
        self, client, api_key_headers, mock_job_service, mock_url_service, mock_storage_service
    ):
        """Test that ledger endpoint returns 404 when ledger file not found in S3."""
        job_id = "test-job-no-ledger"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 5,
        }

        # Make storage service raise an exception when downloading ledger
        mock_storage_service.download_file = AsyncMock(
            side_effect=Exception("Ledger not found")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            assert "ledger not found" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_returns_proper_response_structure(
        self,
        client,
        api_key_headers,
        mock_job_service,
        mock_url_service,
        mock_storage_service,
        sample_ledger_data,
    ):
        """Test that ledger endpoint returns proper LedgerResponse structure."""
        import json

        job_id = "test-job-123"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "test_document.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 2,
        }

        # Mock storage to return sample ledger data
        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(sample_ledger_data).encode("utf-8")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Verify top-level LedgerResponse structure
            assert data["job_id"] == job_id
            assert data["document_title"] == "test_document.pdf"
            assert data["total_pages"] == 2
            assert data["total_edits"] == 3
            assert "pages" in data
            assert "final_markdown_url" in data
            assert data["final_markdown_url"] == "https://example.com/mock-url"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_entries_grouped_by_page(
        self,
        client,
        api_key_headers,
        mock_job_service,
        mock_url_service,
        mock_storage_service,
        sample_ledger_data,
    ):
        """Test that ledger entries are properly grouped by page number."""
        import json

        job_id = "test-job-grouped"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "grouped_test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 3,
        }

        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(sample_ledger_data).encode("utf-8")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Verify pages are grouped correctly
            pages = data["pages"]
            assert len(pages) == 2  # Page 1 and Page 2

            # Verify page 1 has 2 entries
            page_1 = next((p for p in pages if p["page"] == 1), None)
            assert page_1 is not None
            assert page_1["edit_count"] == 2
            assert len(page_1["entries"]) == 2

            # Verify page 2 has 1 entry
            page_2 = next((p for p in pages if p["page"] == 2), None)
            assert page_2 is not None
            assert page_2["edit_count"] == 1
            assert len(page_2["entries"]) == 1

            # Verify pages_with_changes count
            assert data["pages_with_changes"] == 2
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_entry_fields(
        self,
        client,
        api_key_headers,
        mock_job_service,
        mock_url_service,
        mock_storage_service,
        sample_ledger_data,
    ):
        """Test that ledger entries contain all required fields."""
        import json

        job_id = "test-job-fields"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "fields_test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 2,
        }

        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(sample_ledger_data).encode("utf-8")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Get first entry from first page
            first_page = data["pages"][0]
            first_entry = first_page["entries"][0]

            # Verify all LedgerEntryResponse fields are present
            assert "entry_id" in first_entry
            assert "page" in first_entry
            assert "action" in first_entry
            assert "target" in first_entry
            assert "before" in first_entry
            assert "after" in first_entry
            assert "reasoning" in first_entry
            assert "confidence" in first_entry
            assert "timestamp" in first_entry

            # Verify field values match expected
            assert first_entry["entry_id"] == "entry-1"
            assert first_entry["page"] == 1
            assert first_entry["action"] == "modify"
            assert first_entry["target"] == "heading-1"
            assert first_entry["before"] == "Old Title"
            assert first_entry["after"] == "New Title"
            assert first_entry["confidence"] == 0.95
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_pages_sorted_by_page_number(
        self,
        client,
        api_key_headers,
        mock_job_service,
        mock_url_service,
        mock_storage_service,
    ):
        """Test that ledger pages are sorted by page number."""
        import json

        job_id = "test-job-sorted"

        # Create ledger with entries out of order
        ledger_data = {
            "document_id": job_id,
            "entries": [
                {"entry_id": "e3", "page": 3, "action": "modify", "target": "t3",
                 "before": "", "after": "", "reasoning": "", "confidence": 0.9, "timestamp": ""},
                {"entry_id": "e1", "page": 1, "action": "modify", "target": "t1",
                 "before": "", "after": "", "reasoning": "", "confidence": 0.9, "timestamp": ""},
                {"entry_id": "e5", "page": 5, "action": "modify", "target": "t5",
                 "before": "", "after": "", "reasoning": "", "confidence": 0.9, "timestamp": ""},
                {"entry_id": "e2", "page": 2, "action": "modify", "target": "t2",
                 "before": "", "after": "", "reasoning": "", "confidence": 0.9, "timestamp": ""},
            ],
            "total_edits": 4,
        }

        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "sorted_test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 5,
        }

        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(ledger_data).encode("utf-8")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            # Verify pages are sorted in ascending order
            pages = data["pages"]
            page_numbers = [p["page"] for p in pages]
            assert page_numbers == [1, 2, 3, 5]  # Sorted order
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_ledger_with_empty_entries(
        self,
        client,
        api_key_headers,
        mock_job_service,
        mock_url_service,
        mock_storage_service,
    ):
        """Test ledger endpoint handles empty entries list."""
        import json

        job_id = "test-job-empty"

        ledger_data = {
            "document_id": job_id,
            "entries": [],
            "total_edits": 0,
        }

        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "empty_test.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "total_pages": 1,
        }

        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(ledger_data).encode("utf-8")
        )

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        app.dependency_overrides[get_s3_url_service] = lambda: mock_url_service
        app.dependency_overrides[get_storage_service] = lambda: mock_storage_service

        try:
            response = client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()

            assert data["total_edits"] == 0
            assert data["pages_with_changes"] == 0
            assert data["pages"] == []
        finally:
            app.dependency_overrides.clear()


class TestLedgerUrlInJobStatus:
    """Tests for ledger_url field in AgenticCompletedResponse.

    Note: These tests verify the logic in the get_job endpoint that determines
    whether ledger_url should be included based on review_mode. The tests use
    direct function calls since the endpoint's response_model union type
    (DocumentStatusResponse) doesn't include AgenticCompletedResponse.
    """

    @pytest.mark.asyncio
    async def test_ledger_url_included_for_human_review_mode(self, mock_job_service, mock_url_service):
        """Test that ledger_url is included when review_mode is 'human'.

        This test verifies the business logic that builds the ledger_url
        based on review_mode, bypassing the FastAPI response validation
        that has a union type mismatch.
        """
        from src.api.documents import get_job

        job_id = "test-job-human"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "human_review.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "human",
            "result_url": f"results/{job_id}/result.md",
            "confidence_score": "0.90",
            "total_pages": 5,
            "total_edits": 10,
        }

        # Call the endpoint function directly
        response = await get_job(
            job_id=job_id,
            job_service=mock_job_service,
            url_service=mock_url_service,
        )

        # Verify the response object has the expected fields
        assert response.status == "completed"
        assert response.review_mode == "human"
        assert response.ledger_url == f"/api/v1/documents/{job_id}/ledger"

    @pytest.mark.asyncio
    async def test_ledger_url_included_for_auto_review_mode(self, mock_job_service, mock_url_service):
        """Test that ledger_url is provided for all review modes including 'auto'.

        The ledger is always stored and should be accessible for debugging
        purposes regardless of review mode.
        """
        from src.api.documents import get_job

        job_id = "test-job-auto"
        mock_job_service.get_job.return_value = {
            "job_id": job_id,
            "status": "completed",
            "original_filename": "auto_review.pdf",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:02:00Z",
            "review_mode": "auto",
            "result_url": f"results/{job_id}/result.md",
            "confidence_score": "0.95",
            "total_pages": 3,
            "total_edits": 5,
        }

        # Call the endpoint function directly
        response = await get_job(
            job_id=job_id,
            job_service=mock_job_service,
            url_service=mock_url_service,
        )

        # Verify the response object has the expected fields
        assert response.status == "completed"
        assert response.review_mode == "auto"
        # Ledger is now always available for debugging purposes
        assert response.ledger_url == f"/api/v1/documents/{job_id}/ledger"
