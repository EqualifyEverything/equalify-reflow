"""Tests for document endpoints."""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from src.dependencies import get_job_service, get_queue_service, get_s3_url_service, get_storage_service
from src.main import app


@pytest.mark.asyncio
async def test_submit_document_success(client, sample_pdf, api_key_headers):
    """Test successful document submission."""
    # Create mock services
    mock_storage = MagicMock()
    mock_storage.store_document = AsyncMock(return_value=("test-job-id", "temp/test-job-id.pdf"))

    mock_queue = MagicMock()
    mock_queue.queue_pii_job = AsyncMock()

    mock_job = MagicMock()
    mock_job.create_job = AsyncMock()

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Submit document
        files = {"file": ("test.pdf", io.BytesIO(sample_pdf), "application/pdf")}
        response = client.post("/api/v1/documents/submit", files=files, headers=api_key_headers)

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pii_scanning"
        assert data["estimated_completion_minutes"] == 5
    finally:
        app.dependency_overrides.clear()


def test_submit_document_invalid_type(client, api_key_headers):
    """Test document submission with invalid file type."""
    # Create mock storage that raises validation error
    mock_storage = MagicMock()
    mock_storage.store_document = AsyncMock(
        side_effect=HTTPException(
            status_code=400, detail="Only PDF files are accepted"
        )
    )

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage

    try:
        files = {"file": ("test.txt", io.BytesIO(b"not a pdf"), "text/plain")}
        response = client.post("/api/v1/documents/submit", files=files, headers=api_key_headers)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_status_success(client, api_key_headers):
    """Test getting job status."""
    # Create mock job service
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value={
        "job_id": "test-job-id",
        "status": "processing",
        "created_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:01:00Z"
    })

    # Override dependency
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Get status
        response = client.get("/api/v1/documents/test-job-id", headers=api_key_headers)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == "test-job-id"
        assert data["status"] == "processing"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_status_not_found(client, api_key_headers):
    """Test getting status for non-existent job."""
    # Create mock job service
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value=None)

    # Override dependency
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Get status
        response = client.get("/api/v1/documents/nonexistent-job", headers=api_key_headers)

        # Assertions
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_result_completed(client, api_key_headers):
    """Test getting result for completed job."""
    # Create mock services
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value={
        "job_id": "test-job-id",
        "status": "completed",
        "created_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:05:00Z",
        "confidence_score": "0.87",
        "processing_time_seconds": "300",
        "correction_decision": "approved",
        "correction_reviewed_by": "",
        "correction_reviewed_at": "",
        "correction_justification": ""
    })

    mock_s3_url_service = MagicMock()
    mock_s3_url_service.generate_url = AsyncMock(
        side_effect=lambda s3_key, bucket=None: f"http://localhost:4566/{bucket}/{s3_key}"
    )
    mock_s3_url_service.results_bucket = "equalify-pdf-results"

    # Override dependencies
    app.dependency_overrides[get_job_service] = lambda: mock_job
    app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

    try:
        # Get result
        response = client.get("/api/v1/documents/test-job-id", headers=api_key_headers)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "completed"
        assert "markdown_url" in data
        assert data["confidence_score"] == 0.87
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_result_processing(client, api_key_headers):
    """Test getting result for job still processing."""
    # Create mock job service
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value={
        "job_id": "test-job-id",
        "status": "processing",
        "created_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:02:00Z"
    })

    # Override dependency
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Get status
        response = client.get("/api/v1/documents/test-job-id", headers=api_key_headers)

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "processing"
        assert "estimated_completion_minutes" in data
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_submit_document_skip_pii_scan(client, sample_pdf, api_key_headers):
    """Test skip_pii_scan=True bypasses PII queue and goes directly to processing.

    Catches: PII bypass flow routing incorrectly (compliance risk if broken).
    """
    # Use valid UUID format (required by ProcessingQueuePayload validation)
    test_job_id = "550e8400-e29b-41d4-a716-446655440000"

    # Create mock services
    mock_storage = MagicMock()
    mock_storage.store_document = AsyncMock(return_value=(test_job_id, f"temp/{test_job_id}.pdf"))

    mock_queue = MagicMock()
    mock_queue.enqueue = AsyncMock()
    mock_queue.queue_pii_job = AsyncMock()  # Should NOT be called

    mock_job = MagicMock()
    mock_job.create_job = AsyncMock()

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Submit document with skip_pii_scan=True
        files = {"file": ("test.pdf", io.BytesIO(sample_pdf), "application/pdf")}
        data = {"skip_pii_scan": "true", "skip_reason": "Pre-scanned document"}
        response = client.post(
            "/api/v1/documents/submit",
            files=files,
            data=data,
            headers=api_key_headers
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        response_data = response.json()

        # Key assertion: status should be "processing", NOT "pii_scanning"
        assert response_data["status"] == "processing"
        assert response_data["job_id"] == test_job_id

        # Verify job was created with correct parameters
        mock_job.create_job.assert_called_once()
        call_kwargs = mock_job.create_job.call_args
        # Check positional args: job_id, s3_key
        assert call_kwargs[0][0] == test_job_id
        assert call_kwargs[0][1] == f"temp/{test_job_id}.pdf"
        # Check keyword args
        assert call_kwargs[1]["status"] == "processing"
        assert call_kwargs[1]["pii_skipped"] is True
        assert call_kwargs[1]["pii_skip_reason"] == "Pre-scanned document"

        # Verify enqueue was called (to PROCESSING_QUEUE), NOT queue_pii_job
        mock_queue.enqueue.assert_called_once()
        mock_queue.queue_pii_job.assert_not_called()
    finally:
        app.dependency_overrides.clear()
