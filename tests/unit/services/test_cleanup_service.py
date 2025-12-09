"""Tests for cleanup service."""

from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from src.services.cleanup_service import CleanupService


@pytest.fixture
def mock_s3_client():
    """Mock S3 client for testing."""
    client = AsyncMock()
    return client


@pytest.fixture
def cleanup_service(mock_s3_client):
    """Cleanup service instance with mocked S3."""
    return CleanupService(mock_s3_client)


@pytest.mark.asyncio
async def test_cleanup_job_files_success(cleanup_service, mock_s3_client):
    """Test successful file cleanup."""
    # Arrange
    s3_key = "temp/test-job-123.pdf"
    mock_s3_client.delete_object.return_value = {}

    # Act
    result = await cleanup_service.cleanup_job_files(s3_key)

    # Assert
    assert result is True
    mock_s3_client.delete_object.assert_called_once_with(
        Bucket="equalify-pdf-temp",
        Key=s3_key
    )


@pytest.mark.asyncio
async def test_cleanup_job_files_already_deleted(cleanup_service, mock_s3_client):
    """Test cleanup of non-existent file (idempotent behavior)."""
    # Arrange
    s3_key = "temp/deleted-job-456.pdf"
    # Mock ClientError with NoSuchKey code
    error = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
        "DeleteObject"
    )
    mock_s3_client.delete_object.side_effect = error

    # Act
    result = await cleanup_service.cleanup_job_files(s3_key)

    # Assert
    assert result is True  # Idempotent success
    mock_s3_client.delete_object.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_job_files_s3_error(cleanup_service, mock_s3_client):
    """Test cleanup with S3 error (logs but doesn't fail)."""
    # Arrange
    s3_key = "temp/error-job-789.pdf"
    mock_s3_client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "InternalError", "Message": "S3 error"}},
        "DeleteObject"
    )

    # Act
    result = await cleanup_service.cleanup_job_files(s3_key)

    # Assert
    assert result is False  # Error logged, returns False
    mock_s3_client.delete_object.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_job_files_generic_exception(cleanup_service, mock_s3_client):
    """Test cleanup with unexpected exception."""
    # Arrange
    s3_key = "temp/exception-job-999.pdf"
    mock_s3_client.delete_object.side_effect = RuntimeError("Unexpected error")

    # Act
    result = await cleanup_service.cleanup_job_files(s3_key)

    # Assert
    assert result is False
    mock_s3_client.delete_object.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_service_uses_correct_bucket(cleanup_service, mock_s3_client):
    """Test that cleanup service uses configured temp bucket."""
    # Arrange
    s3_key = "temp/bucket-test.pdf"
    mock_s3_client.delete_object.return_value = {}

    # Act
    await cleanup_service.cleanup_job_files(s3_key)

    # Assert
    call_args = mock_s3_client.delete_object.call_args
    assert call_args[1]["Bucket"] == "equalify-pdf-temp"
    assert call_args[1]["Key"] == s3_key
