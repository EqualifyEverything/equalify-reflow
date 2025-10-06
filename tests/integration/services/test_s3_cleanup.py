"""Tests for S3CleanupService - S3 temporary file cleanup operations."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

from src.services.s3_cleanup_service import S3CleanupService


@pytest.fixture
def mock_storage_service(mocker):
    """Create mock StorageService."""
    service = mocker.AsyncMock()
    service.list_temp_files = AsyncMock(return_value=[])
    service.delete_from_s3 = AsyncMock(return_value=True)
    service.cleanup_temp_files_for_job = AsyncMock(return_value=0)
    return service


@pytest.fixture
def mock_metrics_service(mocker):
    """Create mock MetricsService."""
    service = mocker.AsyncMock()
    service.increment_metric = AsyncMock()
    return service


@pytest.fixture
def s3_cleanup_service(mock_storage_service, mock_metrics_service):
    """Create S3CleanupService with mocked dependencies."""
    return S3CleanupService(
        storage_service=mock_storage_service,
        metrics_service=mock_metrics_service
    )


@pytest.fixture
def old_temp_files():
    """Sample list of old temp files from S3."""
    return [
        {
            "Key": "temp/job-1/input.pdf",
            "Size": 1024000,
            "LastModified": datetime.now(timezone.utc) - timedelta(hours=48)
        },
        {
            "Key": "temp/job-2/input.pdf",
            "Size": 2048000,
            "LastModified": datetime.now(timezone.utc) - timedelta(hours=36)
        },
        {
            "Key": "temp/job-3/input.pdf",
            "Size": 512000,
            "LastModified": datetime.now(timezone.utc) - timedelta(hours=25)
        }
    ]


class TestCleanupExpiredTempFiles:
    """Tests for cleanup_expired_temp_files method."""

    @pytest.mark.asyncio
    async def test_no_expired_files(
        self,
        s3_cleanup_service,
        mock_storage_service
    ):
        """Test when no temp files have expired."""
        mock_storage_service.list_temp_files.return_value = []

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 0
        assert result["bytes_freed"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_multiple_files(
        self,
        s3_cleanup_service,
        mock_storage_service,
        mock_metrics_service,
        old_temp_files
    ):
        """Test cleaning up multiple expired files."""
        mock_storage_service.list_temp_files.return_value = old_temp_files
        mock_storage_service.delete_from_s3.return_value = True

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 3
        assert result["bytes_freed"] == 3584000  # Sum of all file sizes
        assert result["errors"] == 0

        # Verify metrics updated
        mock_metrics_service.increment_metric.assert_any_call(
            "temp_files_deleted", 3
        )
        mock_metrics_service.increment_metric.assert_any_call(
            "temp_bytes_freed", 3584000
        )

    @pytest.mark.asyncio
    async def test_partial_cleanup_failure(
        self,
        s3_cleanup_service,
        mock_storage_service,
        mock_metrics_service,
        old_temp_files
    ):
        """Test handling partial cleanup failures."""
        mock_storage_service.list_temp_files.return_value = old_temp_files

        # First file succeeds, second fails, third succeeds
        delete_results = [True, False, True]
        mock_storage_service.delete_from_s3.side_effect = delete_results

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 2
        assert result["bytes_freed"] == 1536000  # file 1 + file 3
        assert result["errors"] == 1

        # Verify error metric incremented
        mock_metrics_service.increment_metric.assert_any_call(
            "temp_cleanup_errors", 1
        )

    @pytest.mark.asyncio
    async def test_cleanup_with_delete_exception(
        self,
        s3_cleanup_service,
        mock_storage_service,
        old_temp_files
    ):
        """Test handling exceptions during file deletion."""
        mock_storage_service.list_temp_files.return_value = old_temp_files

        # First file raises exception, others succeed
        def delete_side_effect(bucket, key):
            if "job-1" in key:
                raise Exception("S3 error")
            return True

        mock_storage_service.delete_from_s3.side_effect = delete_side_effect

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 2
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_list_files_exception_propagates(
        self,
        s3_cleanup_service,
        mock_storage_service,
        mock_metrics_service
    ):
        """Test that exceptions from list_temp_files propagate."""
        mock_storage_service.list_temp_files.side_effect = Exception("S3 list error")

        with pytest.raises(Exception) as exc:
            await s3_cleanup_service.cleanup_expired_temp_files()

        assert "S3 list error" in str(exc.value)

        # Verify error metric incremented
        mock_metrics_service.increment_metric.assert_called_with(
            "temp_cleanup_errors", 1
        )


class TestCleanupJobTempFiles:
    """Tests for cleanup_job_temp_files method."""

    @pytest.mark.asyncio
    async def test_cleanup_job_with_files(
        self,
        s3_cleanup_service,
        mock_storage_service,
        mock_metrics_service
    ):
        """Test cleaning up files for a specific job."""
        mock_storage_service.cleanup_temp_files_for_job.return_value = 3

        result = await s3_cleanup_service.cleanup_job_temp_files("test-job-123")

        assert result is True
        mock_storage_service.cleanup_temp_files_for_job.assert_called_once_with(
            "test-job-123"
        )
        mock_metrics_service.increment_metric.assert_called_once_with(
            "job_temp_files_deleted", 3
        )

    @pytest.mark.asyncio
    async def test_cleanup_job_no_files(
        self,
        s3_cleanup_service,
        mock_storage_service,
        mock_metrics_service
    ):
        """Test cleaning up job with no temp files."""
        mock_storage_service.cleanup_temp_files_for_job.return_value = 0

        result = await s3_cleanup_service.cleanup_job_temp_files("test-job-456")

        assert result is True
        # Should not increment metric when no files deleted
        mock_metrics_service.increment_metric.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_job_exception_returns_false(
        self,
        s3_cleanup_service,
        mock_storage_service
    ):
        """Test that exceptions during job cleanup return False."""
        mock_storage_service.cleanup_temp_files_for_job.side_effect = Exception(
            "S3 error"
        )

        result = await s3_cleanup_service.cleanup_job_temp_files("test-job-789")

        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_multiple_jobs(
        self,
        s3_cleanup_service,
        mock_storage_service
    ):
        """Test cleaning up multiple jobs sequentially."""
        mock_storage_service.cleanup_temp_files_for_job.return_value = 2

        job_ids = ["job-1", "job-2", "job-3"]
        results = []

        for job_id in job_ids:
            result = await s3_cleanup_service.cleanup_job_temp_files(job_id)
            results.append(result)

        assert all(results)
        assert mock_storage_service.cleanup_temp_files_for_job.call_count == 3
