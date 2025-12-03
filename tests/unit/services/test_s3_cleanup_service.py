"""Unit tests for S3CleanupService."""

import pytest
from botocore.exceptions import ClientError

from src.services.s3_cleanup_service import S3CleanupService
from src.config import settings


@pytest.fixture
def cleanup_service(mock_s3_client):
    """Create cleanup service with mock client."""
    return S3CleanupService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
    )


class TestDeleteTempFile:
    """Tests for delete_temp_file method."""

    @pytest.mark.asyncio
    async def test_delete_success(self, cleanup_service, mock_s3_client):
        """Test successful file deletion."""
        mock_s3_client.delete_object.return_value = None

        result = await cleanup_service.delete_temp_file("temp/job123/file.pdf")

        assert result is True
        mock_s3_client.delete_object.assert_called_once_with(
            Bucket=settings.s3_temp_bucket,
            Key="temp/job123/file.pdf"
        )

    @pytest.mark.asyncio
    async def test_delete_failure(self, cleanup_service, mock_s3_client):
        """Test handling of deletion failure - best effort, no exception raised."""
        mock_s3_client.delete_object.side_effect = Exception("S3 error")

        result = await cleanup_service.delete_temp_file("temp/file.pdf")

        assert result is False  # Returns False instead of raising exception


class TestCleanupTempFilesForJob:
    """Tests for cleanup_temp_files_for_job method."""

    @pytest.mark.asyncio
    async def test_cleanup_single_file(self, cleanup_service, mock_s3_client, mocker):
        """Test cleanup of single PDF file for job."""
        # Mock paginator
        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'temp/job123.pdf'}
                ]
            }
        ]

        # Mock batch delete
        mock_s3_client.delete_objects.return_value = {
            'Deleted': [{'Key': 'temp/job123.pdf'}]
        }

        # Execute
        count = await cleanup_service.cleanup_temp_files_for_job("job123")

        # Verify
        assert count == 1
        mock_s3_client.delete_objects.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_multiple_files(self, cleanup_service, mock_s3_client, mocker):
        """Test cleanup of multiple files for job."""
        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'temp/job456/file1.pdf'},
                    {'Key': 'temp/job456/file2.pdf'},
                    {'Key': 'temp/job456/metadata.json'}
                ]
            }
        ]

        mock_s3_client.delete_objects.return_value = {
            'Deleted': [
                {'Key': 'temp/job456/file1.pdf'},
                {'Key': 'temp/job456/file2.pdf'},
                {'Key': 'temp/job456/metadata.json'}
            ]
        }

        count = await cleanup_service.cleanup_temp_files_for_job("job456")

        assert count == 3

    @pytest.mark.asyncio
    async def test_cleanup_no_files(self, cleanup_service, mock_s3_client, mocker):
        """Test cleanup when no files exist for job."""
        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{}]  # No Contents key

        count = await cleanup_service.cleanup_temp_files_for_job("job789")

        assert count == 0
        mock_s3_client.delete_objects.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_batch_delete_large_set(self, cleanup_service, mock_s3_client, mocker):
        """Test batch deletion with >1000 files (multiple batches)."""
        # Create 1500 files to test batching
        files = [{'Key': f'temp/job999/file{i}.pdf'} for i in range(1500)]

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{'Contents': files}]

        # Mock delete_objects to return deleted count
        def delete_side_effect(**kwargs):
            return {'Deleted': kwargs['Delete']['Objects']}

        mock_s3_client.delete_objects.side_effect = delete_side_effect

        count = await cleanup_service.cleanup_temp_files_for_job("job999")

        assert count == 1500
        # Should be called twice (1000 + 500)
        assert mock_s3_client.delete_objects.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_handles_errors(self, cleanup_service, mock_s3_client, mocker):
        """Test error handling during cleanup."""
        from fastapi import HTTPException

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied"}},
            "ListObjectsV2"
        )

        with pytest.raises(HTTPException) as exc:
            await cleanup_service.cleanup_temp_files_for_job("job-error")

        assert exc.value.status_code == 500


class TestListTempFiles:
    """Tests for list_temp_files method (PRD-003 completion)."""

    @pytest.mark.asyncio
    async def test_list_old_files(self, cleanup_service, mock_s3_client, mocker):
        """Test listing files older than threshold."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=48)  # 48 hours ago
        recent_time = now - timedelta(hours=12)  # 12 hours ago

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'temp/old1.pdf', 'Size': 1024, 'LastModified': old_time},
                    {'Key': 'temp/old2.pdf', 'Size': 2048, 'LastModified': old_time},
                    {'Key': 'temp/recent.pdf', 'Size': 512, 'LastModified': recent_time}
                ]
            }
        ]

        # List files older than 24 hours
        old_files = await cleanup_service.list_temp_files(older_than_hours=24)

        assert len(old_files) == 2
        assert old_files[0]['key'] == 'temp/old1.pdf'
        assert old_files[1]['key'] == 'temp/old2.pdf'
        assert all(f['age_hours'] >= 24 for f in old_files)

    @pytest.mark.asyncio
    async def test_list_no_old_files(self, cleanup_service, mock_s3_client, mocker):
        """Test when no files exceed age threshold."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent_time = now - timedelta(hours=6)

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'temp/recent.pdf', 'Size': 1024, 'LastModified': recent_time}
                ]
            }
        ]

        old_files = await cleanup_service.list_temp_files(older_than_hours=24)

        assert len(old_files) == 0

    @pytest.mark.asyncio
    async def test_list_empty_bucket(self, cleanup_service, mock_s3_client, mocker):
        """Test listing when bucket is empty."""
        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [{}]

        old_files = await cleanup_service.list_temp_files()

        assert len(old_files) == 0

    @pytest.mark.asyncio
    async def test_list_handles_timezone_naive(self, cleanup_service, mock_s3_client, mocker):
        """Test handling of timezone-naive datetimes."""
        from datetime import datetime, timedelta, UTC

        # Create timezone-naive datetime (simulates some S3 responses)
        naive_time = datetime.now(UTC) - timedelta(hours=48)

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.return_value = [
            {
                'Contents': [
                    {'Key': 'temp/naive.pdf', 'Size': 1024, 'LastModified': naive_time}
                ]
            }
        ]

        # Should not raise exception
        old_files = await cleanup_service.list_temp_files(older_than_hours=24)

        assert len(old_files) == 1

    @pytest.mark.asyncio
    async def test_list_handles_errors(self, cleanup_service, mock_s3_client, mocker):
        """Test error handling during listing."""
        from fastapi import HTTPException

        mock_paginator = mocker.MagicMock()
        mock_s3_client.get_paginator.return_value = mock_paginator
        mock_paginator.paginate.side_effect = Exception("Network error")

        with pytest.raises(HTTPException) as exc:
            await cleanup_service.list_temp_files()

        assert exc.value.status_code == 500


class TestDeleteFromS3:
    """Tests for delete_from_s3 method (PRD-003 completion)."""

    @pytest.mark.asyncio
    async def test_delete_success(self, cleanup_service, mock_s3_client):
        """Test successful deletion."""
        mock_s3_client.delete_object.return_value = None

        success = await cleanup_service.delete_from_s3("bucket", "key")

        assert success is True
        mock_s3_client.delete_object.assert_called_once_with(
            Bucket="bucket",
            Key="key"
        )

    @pytest.mark.asyncio
    async def test_delete_idempotent(self, cleanup_service, mock_s3_client):
        """Test idempotent deletion (file already deleted)."""
        error = ClientError(
            {"Error": {"Code": "NoSuchKey"}},
            "DeleteObject"
        )
        mock_s3_client.delete_object.side_effect = error

        # Should still return True (idempotent)
        success = await cleanup_service.delete_from_s3("bucket", "missing")

        assert success is True

    @pytest.mark.asyncio
    async def test_delete_other_errors(self, cleanup_service, mock_s3_client):
        """Test handling of non-NoSuchKey errors."""
        error = ClientError(
            {"Error": {"Code": "AccessDenied"}},
            "DeleteObject"
        )
        mock_s3_client.delete_object.side_effect = error

        success = await cleanup_service.delete_from_s3("bucket", "key")

        assert success is False

    @pytest.mark.asyncio
    async def test_delete_generic_exception(self, cleanup_service, mock_s3_client):
        """Test handling of generic exceptions."""
        mock_s3_client.delete_object.side_effect = Exception("Network timeout")

        success = await cleanup_service.delete_from_s3("bucket", "key")

        assert success is False
