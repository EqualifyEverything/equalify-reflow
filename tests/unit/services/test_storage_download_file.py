"""Unit tests for StorageService.download_file method.

Tests the download_file method that downloads from the results bucket.
This is distinct from download_temp_file which uses the temp bucket.
"""

from io import BytesIO
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from src.config import settings
from src.services.storage_service import StorageService
from src.utils.circuit_breaker import CircuitBreakerOpenError

pytestmark = pytest.mark.unit


@pytest.fixture
def storage_service(mock_s3_client):
    """Create storage service with mock client."""
    return StorageService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


class TestDownloadFile:
    """Tests for download_file method (results bucket)."""

    @pytest.mark.asyncio
    async def test_download_file_success(self, storage_service, mock_s3_client):
        """Test successful file download returns bytes."""
        expected_content = b"# Markdown content\n\nThis is the document."
        mock_response = {"Body": BytesIO(expected_content)}
        mock_s3_client.get_object.return_value = mock_response

        content = await storage_service.download_file("job123.md")

        assert content == expected_content
        mock_s3_client.get_object.assert_called_once_with(
            Bucket=settings.s3_results_bucket,
            Key="job123.md"
        )

    @pytest.mark.asyncio
    async def test_download_file_not_found_returns_404(self, storage_service, mock_s3_client):
        """Test NoSuchKey error returns 404 HTTPException."""
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        with pytest.raises(HTTPException) as exc:
            await storage_service.download_file("nonexistent.md")

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_download_file_client_error_returns_500(self, storage_service, mock_s3_client):
        """Test other ClientError returns 500 HTTPException."""
        error_response = {"Error": {"Code": "AccessDenied"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        with pytest.raises(HTTPException) as exc:
            await storage_service.download_file("restricted.md")

        assert exc.value.status_code == 500
        assert "Failed to download" in exc.value.detail

    @pytest.mark.asyncio
    async def test_download_file_unexpected_error_returns_500(self, storage_service, mock_s3_client):
        """Test unexpected Exception returns 500 HTTPException."""
        mock_s3_client.get_object.side_effect = Exception("Network timeout")

        with pytest.raises(HTTPException) as exc:
            await storage_service.download_file("job456.md")

        assert exc.value.status_code == 500
        assert "Unexpected error" in exc.value.detail

    @pytest.mark.asyncio
    async def test_download_file_circuit_breaker_open_raises(self, storage_service, mock_s3_client):
        """Test circuit breaker open state raises CircuitBreakerOpenError."""
        # Force circuit breaker to open state by recording multiple failures
        for _ in range(6):
            storage_service.download_circuit.record_failure()

        with pytest.raises(CircuitBreakerOpenError):
            await storage_service.download_file("job789.md")

        # S3 should not be called when circuit is open
        mock_s3_client.get_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_file_records_success_on_circuit_breaker(
        self, storage_service, mock_s3_client
    ):
        """Test successful download records success on circuit breaker."""
        mock_response = {"Body": BytesIO(b"content")}
        mock_s3_client.get_object.return_value = mock_response

        # Record some failures first (but not enough to open)
        storage_service.download_circuit.record_failure()
        storage_service.download_circuit.record_failure()

        await storage_service.download_file("test.md")

        # After success, circuit should have recorded the success
        # (internal state is checked by not having opened after partial failures)
        assert not storage_service.download_circuit.is_open

    @pytest.mark.asyncio
    async def test_download_file_records_failure_on_circuit_breaker(
        self, storage_service, mock_s3_client
    ):
        """Test failed download records failure on circuit breaker."""
        error_response = {"Error": {"Code": "InternalError"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        # Track initial failure count
        initial_failures = storage_service.download_circuit._failure_count

        with pytest.raises(HTTPException):
            await storage_service.download_file("error.md")

        # Verify failure was recorded
        assert storage_service.download_circuit._failure_count > initial_failures

    @pytest.mark.asyncio
    async def test_download_file_retry_logic_with_transient_failures(
        self, storage_service, mock_s3_client
    ):
        """Test retry logic handles transient failures before success."""
        # First two calls fail, third succeeds
        expected_content = b"success after retry"
        error_response = {"Error": {"Code": "InternalError"}}

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ClientError(error_response, "GetObject")
            return {"Body": BytesIO(expected_content)}

        mock_s3_client.get_object.side_effect = side_effect

        # Patch retry delay to be very short for test speed
        with patch("src.utils.retry_helpers.asyncio.sleep", return_value=None):
            content = await storage_service.download_file("retry-test.md")

        assert content == expected_content
        assert call_count == 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_download_file_uses_results_bucket(self, storage_service, mock_s3_client):
        """Test download_file uses results bucket, not temp bucket."""
        mock_response = {"Body": BytesIO(b"data")}
        mock_s3_client.get_object.return_value = mock_response

        await storage_service.download_file("jobs/abc/final.md")

        call_args = mock_s3_client.get_object.call_args
        assert call_args.kwargs["Bucket"] == settings.s3_results_bucket
        assert call_args.kwargs["Key"] == "jobs/abc/final.md"

    @pytest.mark.asyncio
    async def test_download_file_handles_nested_paths(self, storage_service, mock_s3_client):
        """Test download_file works with nested S3 key paths."""
        mock_response = {"Body": BytesIO(b"nested content")}
        mock_s3_client.get_object.return_value = mock_response

        nested_key = "jobs/550e8400-e29b-41d4-a716-446655440000/pages/page-1.png"
        content = await storage_service.download_file(nested_key)

        assert content == b"nested content"
        mock_s3_client.get_object.assert_called_once_with(
            Bucket=settings.s3_results_bucket,
            Key=nested_key
        )

    @pytest.mark.asyncio
    async def test_download_file_empty_content(self, storage_service, mock_s3_client):
        """Test download_file handles empty file content."""
        mock_response = {"Body": BytesIO(b"")}
        mock_s3_client.get_object.return_value = mock_response

        content = await storage_service.download_file("empty.md")

        assert content == b""
        assert isinstance(content, bytes)

    @pytest.mark.asyncio
    async def test_download_file_large_content(self, storage_service, mock_s3_client):
        """Test download_file handles large file content."""
        # 1MB of content
        large_content = b"x" * (1024 * 1024)
        mock_response = {"Body": BytesIO(large_content)}
        mock_s3_client.get_object.return_value = mock_response

        content = await storage_service.download_file("large.md")

        assert content == large_content
        assert len(content) == 1024 * 1024
