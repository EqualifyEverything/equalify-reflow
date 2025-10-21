"""Tests for document endpoints."""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

from src.main import app
from src.dependencies import get_storage_service, get_queue_service, get_job_service


@pytest.mark.asyncio
async def test_submit_document_success(client, sample_pdf):
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
        response = client.post("/api/documents/submit", files=files)

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pii_scanning"
        assert data["estimated_completion_minutes"] == 5
    finally:
        app.dependency_overrides.clear()


def test_submit_document_invalid_type(client):
    """Test document submission with invalid file type."""
    # Create mock storage that raises validation error
    mock_storage = MagicMock()
    mock_storage.store_document = AsyncMock(side_effect=HTTPException(status_code=400, detail="Only PDF files are accepted"))

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage

    try:
        files = {"file": ("test.txt", io.BytesIO(b"not a pdf"), "text/plain")}
        response = client.post("/api/documents/submit", files=files)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_status_success(client):
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
        response = client.get("/api/documents/test-job-id")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == "test-job-id"
        assert data["status"] == "processing"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_status_not_found(client):
    """Test getting status for non-existent job."""
    # Create mock job service
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value=None)

    # Override dependency
    app.dependency_overrides[get_job_service] = lambda: mock_job

    try:
        # Get status
        response = client.get("/api/documents/nonexistent-job")

        # Assertions
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_result_completed(client):
    """Test getting result for completed job."""
    # Create mock services
    mock_job = MagicMock()
    mock_job.get_job = AsyncMock(return_value={
        "job_id": "test-job-id",
        "status": "completed",
        "created_at": "2025-01-01T12:00:00Z",
        "updated_at": "2025-01-01T12:05:00Z",
        "confidence_score": "0.87",
        "processing_time_seconds": "300"
    })

    mock_storage = MagicMock()
    mock_storage.get_result_url = MagicMock(
        side_effect=lambda job_id, file_type: f"http://localhost:4566/equalify-pdf-results/{job_id}.{file_type}"
    )

    # Override dependencies
    app.dependency_overrides[get_job_service] = lambda: mock_job
    app.dependency_overrides[get_storage_service] = lambda: mock_storage

    try:
        # Get result
        response = client.get("/api/documents/test-job-id/result")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "completed"
        assert "markdown_url" in data
        assert data["confidence_score"] == 0.87
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_job_result_processing(client):
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
        # Get result
        response = client.get("/api/documents/test-job-id/result")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "processing"
        assert "estimated_completion_at" in data
    finally:
        app.dependency_overrides.clear()
