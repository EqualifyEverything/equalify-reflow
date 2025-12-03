"""Tests for S3CleanupService - S3 temporary file cleanup operations."""

from datetime import UTC, datetime, timedelta

import pytest
from src.services.s3_cleanup_service import S3CleanupService


@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    client = mocker.MagicMock()
    # Configure paginator for list_objects_v2
    paginator = mocker.MagicMock()
    client.get_paginator.return_value = paginator
    return client


@pytest.fixture
def s3_cleanup_service(mock_s3_client):
    """Create S3CleanupService with mocked S3 client."""
    return S3CleanupService(
        s3_client=mock_s3_client,
        temp_bucket="test-temp-bucket"
    )


@pytest.fixture
def old_temp_files():
    """Sample list of old temp files from S3."""
    return [
        {
            "Key": "temp/job-1/input.pdf",
            "Size": 1024000,
            "LastModified": datetime.now(UTC) - timedelta(hours=48)
        },
        {
            "Key": "temp/job-2/input.pdf",
            "Size": 2048000,
            "LastModified": datetime.now(UTC) - timedelta(hours=36)
        },
        {
            "Key": "temp/job-3/input.pdf",
            "Size": 512000,
            "LastModified": datetime.now(UTC) - timedelta(hours=25)
        }
    ]


class TestCleanupExpiredTempFiles:
    """Tests for cleanup_expired_temp_files method."""

    @pytest.mark.asyncio
    async def test_no_expired_files(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test when no temp files have expired."""
        # Mock paginator to return empty results
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = []

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 0
        assert result["bytes_freed"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_multiple_files(
        self,
        s3_cleanup_service,
        mock_s3_client,
        old_temp_files
    ):
        """Test cleaning up multiple expired files."""
        # Mock paginator to return old temp files
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = [{'Contents': old_temp_files}]

        # Mock successful deletion
        mock_s3_client.delete_object.return_value = {}

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 3
        assert result["bytes_freed"] == 3584000  # Sum of all file sizes
        assert result["errors"] == 0

        # Verify delete_object was called for each file
        assert mock_s3_client.delete_object.call_count == 3

    @pytest.mark.asyncio
    async def test_partial_cleanup_failure(
        self,
        s3_cleanup_service,
        mock_s3_client,
        old_temp_files
    ):
        """Test handling partial cleanup failures."""
        from botocore.exceptions import ClientError

        # Mock paginator to return old temp files
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = [{'Contents': old_temp_files}]

        # First file succeeds, second fails, third succeeds
        def delete_side_effect(*args, **kwargs):
            key = kwargs.get('Key', '')
            if 'job-2' in key:
                raise ClientError(
                    {'Error': {'Code': 'AccessDenied', 'Message': 'Access denied'}},
                    'delete_object'
                )
            return {}

        mock_s3_client.delete_object.side_effect = delete_side_effect

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 2
        assert result["bytes_freed"] == 1536000  # file 1 + file 3
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_cleanup_with_delete_exception(
        self,
        s3_cleanup_service,
        mock_s3_client,
        old_temp_files
    ):
        """Test handling exceptions during file deletion."""
        # Mock paginator to return old temp files
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = [{'Contents': old_temp_files}]

        # First file raises exception, others succeed
        def delete_side_effect(*args, **kwargs):
            key = kwargs.get('Key', '')
            if "job-1" in key:
                raise Exception("S3 error")
            return {}

        mock_s3_client.delete_object.side_effect = delete_side_effect

        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 2
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_list_files_exception_handled(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test that exceptions during file listing are handled."""
        # Mock paginator to raise exception
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.side_effect = Exception("S3 list error")

        # Should not raise - errors are counted
        result = await s3_cleanup_service.cleanup_expired_temp_files()

        assert result["files_deleted"] == 0
        assert result["bytes_freed"] == 0
        assert result["errors"] == 1


class TestCleanupJobTempFiles:
    """Tests for cleanup_job_temp_files method."""

    @pytest.mark.asyncio
    async def test_cleanup_job_with_files(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test cleaning up files for a specific job."""
        # Mock paginator to return 3 files for the job
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = [{
            'Contents': [
                {'Key': 'temp/test-job-123/input.pdf'},
                {'Key': 'temp/test-job-123/page1.png'},
                {'Key': 'temp/test-job-123/page2.png'},
            ]
        }]

        # Mock successful batch deletion
        mock_s3_client.delete_objects.return_value = {
            'Deleted': [
                {'Key': 'temp/test-job-123/input.pdf'},
                {'Key': 'temp/test-job-123/page1.png'},
                {'Key': 'temp/test-job-123/page2.png'},
            ]
        }

        result = await s3_cleanup_service.cleanup_temp_files_for_job("test-job-123")

        assert result == 3
        mock_s3_client.delete_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_job_no_files(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test cleaning up job with no temp files."""
        # Mock paginator to return no files
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = []

        result = await s3_cleanup_service.cleanup_temp_files_for_job("test-job-456")

        assert result == 0
        # delete_objects should not be called when there are no files
        mock_s3_client.delete_objects.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_job_exception_raises(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test that exceptions during job cleanup are raised."""
        from fastapi import HTTPException

        # Mock paginator to raise exception
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await s3_cleanup_service.cleanup_temp_files_for_job("test-job-789")

        assert "Unexpected error during cleanup" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_cleanup_multiple_jobs(
        self,
        s3_cleanup_service,
        mock_s3_client
    ):
        """Test cleaning up multiple jobs sequentially."""
        # Mock paginator to return 2 files per job
        paginator = mock_s3_client.get_paginator.return_value
        paginator.paginate.return_value = [{
            'Contents': [
                {'Key': 'temp/job-x/file1.pdf'},
                {'Key': 'temp/job-x/file2.pdf'},
            ]
        }]

        # Mock successful batch deletion
        mock_s3_client.delete_objects.return_value = {
            'Deleted': [
                {'Key': 'temp/job-x/file1.pdf'},
                {'Key': 'temp/job-x/file2.pdf'},
            ]
        }

        job_ids = ["job-1", "job-2", "job-3"]
        results = []

        for job_id in job_ids:
            result = await s3_cleanup_service.cleanup_temp_files_for_job(job_id)
            results.append(result)

        assert all(r == 2 for r in results)
        assert mock_s3_client.delete_objects.call_count == 3
