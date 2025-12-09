"""Tests for OrphanService - Orphaned job detection and cleanup."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from src.services.orphan_service import OrphanService


@pytest.fixture
def mock_job_service(mocker):
    """Create mock JobService."""
    service = mocker.AsyncMock()
    service.list_all_jobs = AsyncMock(return_value=[])
    service.get_job_status = AsyncMock(return_value=None)
    service.update_job_status = AsyncMock()
    service.cleanup_old_job = AsyncMock()
    return service


@pytest.fixture
def mock_s3_cleanup_service(mocker):
    """Create mock S3CleanupService."""
    service = mocker.AsyncMock()
    service.cleanup_job_temp_files = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_metrics_service(mocker):
    """Create mock MetricsService."""
    service = mocker.AsyncMock()
    service.increment_metric = AsyncMock()
    return service


@pytest.fixture
def orphan_service(
    mock_job_service,
    mock_s3_cleanup_service,
    mock_metrics_service
):
    """Create OrphanService with mocked dependencies."""
    return OrphanService(
        job_service=mock_job_service,
        s3_cleanup_service=mock_s3_cleanup_service,
        metrics_service=mock_metrics_service
    )


@pytest.fixture
def old_completed_job():
    """Sample old completed job (35 days ago)."""
    return {
        "job_id": "old-job-123",
        "status": "completed",
        "s3_key": "temp/old-job-123.pdf",
        "created_at": (datetime.now(UTC) - timedelta(days=35)).isoformat(),
        "result_url": "https://s3.example.com/results/old-job-123/output.md"
    }


@pytest.fixture
def old_failed_job():
    """Sample old failed job (40 days ago)."""
    return {
        "job_id": "failed-job-456",
        "status": "failed",
        "s3_key": "temp/failed-job-456.pdf",
        "created_at": (datetime.now(UTC) - timedelta(days=40)).isoformat(),
        "error_message": "Processing failed"
    }


@pytest.fixture
def recent_completed_job():
    """Sample recent completed job (5 days ago)."""
    return {
        "job_id": "recent-job-789",
        "status": "completed",
        "s3_key": "temp/recent-job-789.pdf",
        "created_at": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
        "result_url": "https://s3.example.com/results/recent-job-789/output.md"
    }


@pytest.fixture
def stuck_processing_job():
    """Sample job stuck in processing (3 hours ago)."""
    return {
        "job_id": "stuck-job-111",
        "status": "processing",
        "s3_key": "temp/stuck-job-111.pdf",
        "created_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    }


@pytest.fixture
def normal_processing_job():
    """Sample job normally processing (30 minutes ago)."""
    return {
        "job_id": "normal-job-222",
        "status": "processing",
        "s3_key": "temp/normal-job-222.pdf",
        "created_at": (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    }


class TestCleanupOldCompletedJobs:
    """Tests for cleanup_old_completed_jobs method."""

    @pytest.mark.asyncio
    async def test_no_jobs_in_redis(
        self,
        orphan_service,
        mock_job_service
    ):
        """Test when no jobs exist in Redis."""
        mock_job_service.list_all_jobs.return_value = []

        result = await orphan_service.cleanup_old_completed_jobs()

        assert result["jobs_cleaned"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_cleanup_single_old_job(
        self,
        orphan_service,
        mock_job_service,
        mock_s3_cleanup_service,
        mock_metrics_service,
        old_completed_job
    ):
        """Test cleaning up a single old completed job."""
        mock_job_service.list_all_jobs.return_value = ["old-job-123"]
        mock_job_service.get_job_status.return_value = old_completed_job

        result = await orphan_service.cleanup_old_completed_jobs()

        assert result["jobs_cleaned"] == 1
        assert result["errors"] == 0

        mock_s3_cleanup_service.cleanup_job_temp_files.assert_called_once_with(
            "old-job-123"
        )
        mock_job_service.cleanup_old_job.assert_called_once_with("old-job-123")
        mock_metrics_service.increment_metric.assert_called_once_with(
            "old_jobs_cleaned", 1
        )

    @pytest.mark.asyncio
    async def test_skip_recent_completed_jobs(
        self,
        orphan_service,
        mock_job_service,
        recent_completed_job
    ):
        """Test that recent completed jobs are not cleaned up."""
        mock_job_service.list_all_jobs.return_value = ["recent-job-789"]
        mock_job_service.get_job_status.return_value = recent_completed_job

        result = await orphan_service.cleanup_old_completed_jobs()

        assert result["jobs_cleaned"] == 0
        mock_job_service.cleanup_old_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_multiple_old_jobs(
        self,
        orphan_service,
        mock_job_service,
        old_completed_job,
        old_failed_job
    ):
        """Test cleaning up multiple old jobs."""
        mock_job_service.list_all_jobs.return_value = ["old-job-123", "failed-job-456"]

        def get_job_status_side_effect(job_id):
            if job_id == "old-job-123":
                return old_completed_job
            return old_failed_job

        mock_job_service.get_job_status.side_effect = get_job_status_side_effect

        result = await orphan_service.cleanup_old_completed_jobs()

        assert result["jobs_cleaned"] == 2
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_skip_active_jobs(
        self,
        orphan_service,
        mock_job_service
    ):
        """Test that active jobs (processing, awaiting_approval) are not cleaned."""
        mock_job_service.list_all_jobs.return_value = ["active-job"]
        mock_job_service.get_job_status.return_value = {
            "job_id": "active-job",
            "status": "processing",
            "created_at": (datetime.now(UTC) - timedelta(days=40)).isoformat()
        }

        result = await orphan_service.cleanup_old_completed_jobs()

        # Should not cleanup active jobs even if old
        assert result["jobs_cleaned"] == 0

    @pytest.mark.asyncio
    async def test_partial_cleanup_failure(
        self,
        orphan_service,
        mock_job_service,
        mock_s3_cleanup_service,
        old_completed_job,
        old_failed_job
    ):
        """Test handling partial cleanup failures."""
        mock_job_service.list_all_jobs.return_value = ["job-1", "job-2"]

        def get_job_status_side_effect(job_id):
            if job_id == "job-1":
                return old_completed_job
            return old_failed_job

        mock_job_service.get_job_status.side_effect = get_job_status_side_effect

        # First cleanup succeeds, second fails
        async def cleanup_side_effect(job_id):
            if job_id == "job-1":
                return None
            raise Exception("Cleanup failed")

        mock_job_service.cleanup_old_job.side_effect = cleanup_side_effect

        result = await orphan_service.cleanup_old_completed_jobs()

        assert result["jobs_cleaned"] == 1
        assert result["errors"] == 1


class TestCleanupStuckProcessingJobs:
    """Tests for cleanup_stuck_processing_jobs method."""

    @pytest.mark.asyncio
    async def test_no_stuck_jobs(
        self,
        orphan_service,
        mock_job_service,
        normal_processing_job
    ):
        """Test when no jobs are stuck."""
        mock_job_service.list_all_jobs.return_value = ["normal-job-222"]
        mock_job_service.get_job_status.return_value = normal_processing_job

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_fail_stuck_processing_job(
        self,
        orphan_service,
        mock_job_service,
        mock_s3_cleanup_service,
        mock_metrics_service,
        stuck_processing_job
    ):
        """Test failing a job stuck in processing."""
        mock_job_service.list_all_jobs.return_value = ["stuck-job-111"]
        mock_job_service.get_job_status.return_value = stuck_processing_job

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 1
        assert result["errors"] == 0

        mock_job_service.update_job_status.assert_called_once_with(
            "stuck-job-111",
            status="failed",
            error_message="Job exceeded maximum processing time and was terminated"
        )
        mock_s3_cleanup_service.cleanup_job_temp_files.assert_called_once_with(
            "stuck-job-111"
        )
        mock_metrics_service.increment_metric.assert_called_once_with(
            "stuck_jobs_failed", 1
        )

    @pytest.mark.asyncio
    async def test_fail_stuck_pii_scanning_job(
        self,
        orphan_service,
        mock_job_service
    ):
        """Test failing a job stuck in PII scanning."""
        stuck_pii_job = {
            "job_id": "stuck-pii-333",
            "status": "pii_scanning",
            "created_at": (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        }

        mock_job_service.list_all_jobs.return_value = ["stuck-pii-333"]
        mock_job_service.get_job_status.return_value = stuck_pii_job

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 1
        mock_job_service.update_job_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_completed_jobs(
        self,
        orphan_service,
        mock_job_service,
        old_completed_job
    ):
        """Test that completed jobs are not marked as stuck."""
        mock_job_service.list_all_jobs.return_value = ["old-job-123"]
        mock_job_service.get_job_status.return_value = old_completed_job

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 0
        mock_job_service.update_job_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_stuck_jobs(
        self,
        orphan_service,
        mock_job_service,
        stuck_processing_job
    ):
        """Test handling multiple stuck jobs."""
        mock_job_service.list_all_jobs.return_value = ["stuck-1", "stuck-2", "stuck-3"]
        mock_job_service.get_job_status.return_value = stuck_processing_job

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 3
        assert mock_job_service.update_job_status.call_count == 3

    @pytest.mark.asyncio
    async def test_cleanup_failure_increments_errors(
        self,
        orphan_service,
        mock_job_service,
        mock_metrics_service,
        stuck_processing_job
    ):
        """Test that cleanup failures increment error metric."""
        mock_job_service.list_all_jobs.return_value = ["stuck-job"]
        mock_job_service.get_job_status.return_value = stuck_processing_job
        mock_job_service.update_job_status.side_effect = Exception("Update failed")

        result = await orphan_service.cleanup_stuck_processing_jobs()

        assert result["jobs_failed"] == 0
        assert result["errors"] == 1
        mock_metrics_service.increment_metric.assert_called_with(
            "orphan_cleanup_errors", 1
        )


class TestShouldCleanupJob:
    """Tests for _should_cleanup_job method."""

    @pytest.mark.asyncio
    async def test_old_completed_job_should_cleanup(
        self,
        orphan_service,
        mock_job_service,
        old_completed_job
    ):
        """Test that old completed jobs should be cleaned."""
        mock_job_service.get_job_status.return_value = old_completed_job
        cutoff = datetime.now(UTC) - timedelta(days=30)

        result = await orphan_service._should_cleanup_job("old-job-123", cutoff)

        assert result is True

    @pytest.mark.asyncio
    async def test_recent_job_should_not_cleanup(
        self,
        orphan_service,
        mock_job_service,
        recent_completed_job
    ):
        """Test that recent jobs should not be cleaned."""
        mock_job_service.get_job_status.return_value = recent_completed_job
        cutoff = datetime.now(UTC) - timedelta(days=30)

        result = await orphan_service._should_cleanup_job("recent-job-789", cutoff)

        assert result is False

    @pytest.mark.asyncio
    async def test_missing_job_should_cleanup(
        self,
        orphan_service,
        mock_job_service
    ):
        """Test that missing jobs should be cleaned (removed from tracking)."""
        mock_job_service.get_job_status.return_value = None
        cutoff = datetime.now(UTC) - timedelta(days=30)

        result = await orphan_service._should_cleanup_job("missing-job", cutoff)

        assert result is True
