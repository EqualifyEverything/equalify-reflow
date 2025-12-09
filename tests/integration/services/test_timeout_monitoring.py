"""Tests for TimeoutService - Approval timeout monitoring."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from src.services.timeout_service import TimeoutService


@pytest.fixture
def mock_queue_service(mocker):
    """Create mock QueueService."""
    service = mocker.AsyncMock()
    service.get_expired_timeouts = AsyncMock(return_value=[])
    service.remove_from_timeout_tracking = AsyncMock()
    service.get_timeout_count = AsyncMock(return_value=0)
    return service


@pytest.fixture
def mock_job_service(mocker):
    """Create mock JobService."""
    service = mocker.AsyncMock()
    service.get_job_status = AsyncMock(return_value=None)
    service.update_job_status = AsyncMock()
    return service


@pytest.fixture
def mock_cleanup_service(mocker):
    """Create mock CleanupService."""
    service = mocker.AsyncMock()
    service.cleanup_denied_job = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_metrics_service(mocker):
    """Create mock MetricsService."""
    service = mocker.AsyncMock()
    service.increment_metric = AsyncMock()
    return service


@pytest.fixture
def timeout_service(
    mock_queue_service,
    mock_job_service,
    mock_cleanup_service,
    mock_metrics_service
):
    """Create TimeoutService with mocked dependencies."""
    return TimeoutService(
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        cleanup_service=mock_cleanup_service,
        metrics_service=mock_metrics_service
    )


@pytest.fixture
def expired_job_data():
    """Sample job data for expired approval."""
    return {
        "job_id": "test-job-123",
        "status": "awaiting_approval",
        "s3_key": "temp/test-job-123.pdf",
        "created_at": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
        "pii_findings": '[{"type": "SSN", "confidence": 0.9}]'
    }


@pytest.fixture
def completed_job_data():
    """Sample job data for already completed job."""
    return {
        "job_id": "test-job-456",
        "status": "completed",
        "s3_key": "temp/test-job-456.pdf",
        "created_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "result_url": "https://s3.example.com/results/test-job-456/output.md"
    }


class TestProcessExpiredApprovals:
    """Tests for process_expired_approvals method."""

    @pytest.mark.asyncio
    async def test_no_expired_approvals(
        self,
        timeout_service,
        mock_queue_service
    ):
        """Test when no approvals have expired."""
        mock_queue_service.get_expired_timeouts.return_value = []

        result = await timeout_service.process_expired_approvals()

        assert result == 0
        mock_queue_service.get_expired_timeouts.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_single_expired_approval(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service,
        mock_cleanup_service,
        mock_metrics_service,
        expired_job_data
    ):
        """Test processing a single expired approval."""
        mock_queue_service.get_expired_timeouts.return_value = [("test-job-123", 1609459200.0)]
        mock_job_service.get_job_status.return_value = expired_job_data

        result = await timeout_service.process_expired_approvals()

        assert result == 1
        mock_job_service.update_job_status.assert_called_once_with(
            "test-job-123",
            status="failed",
            error_message="Approval deadline exceeded - no response received"
        )
        mock_cleanup_service.cleanup_denied_job.assert_called_once_with("test-job-123")
        mock_queue_service.remove_from_timeout_tracking.assert_called_once_with(
            "test-job-123"
        )
        mock_metrics_service.increment_metric.assert_called_with(
            "approval_timeouts_processed", 1
        )

    @pytest.mark.asyncio
    async def test_process_multiple_expired_approvals(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service,
        expired_job_data
    ):
        """Test processing multiple expired approvals."""
        mock_queue_service.get_expired_timeouts.return_value = [
            ("job-1", 1609459200.0),
            ("job-2", 1609459300.0),
            ("job-3", 1609459400.0)
        ]
        mock_job_service.get_job_status.return_value = expired_job_data

        result = await timeout_service.process_expired_approvals()

        assert result == 3
        assert mock_job_service.update_job_status.call_count == 3

    @pytest.mark.asyncio
    async def test_skip_already_processed_job(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service,
        completed_job_data
    ):
        """Test skipping jobs that are no longer awaiting_approval."""
        mock_queue_service.get_expired_timeouts.return_value = [("test-job-456", 1609459200.0)]
        mock_job_service.get_job_status.return_value = completed_job_data

        result = await timeout_service.process_expired_approvals()

        # Should not process, but should remove from timeout tracking
        assert result == 0
        mock_job_service.update_job_status.assert_not_called()
        mock_queue_service.remove_from_timeout_tracking.assert_called_once_with(
            "test-job-456"
        )

    @pytest.mark.asyncio
    async def test_handle_missing_job(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service
    ):
        """Test handling job that doesn't exist in Redis."""
        mock_queue_service.get_expired_timeouts.return_value = [("missing-job", 1609459200.0)]
        mock_job_service.get_job_status.return_value = None

        result = await timeout_service.process_expired_approvals()

        # Should remove from tracking but not process
        assert result == 0
        mock_queue_service.remove_from_timeout_tracking.assert_called_once_with(
            "missing-job"
        )

    @pytest.mark.asyncio
    async def test_cleanup_failure_continues_processing(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service,
        mock_cleanup_service,
        expired_job_data
    ):
        """Test that cleanup failure doesn't prevent status update."""
        mock_queue_service.get_expired_timeouts.return_value = [("test-job-123", 1609459200.0)]
        mock_job_service.get_job_status.return_value = expired_job_data
        mock_cleanup_service.cleanup_denied_job.return_value = False

        result = await timeout_service.process_expired_approvals()

        # Should still count as processed even if cleanup failed
        assert result == 1
        mock_job_service.update_job_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_failure_continues_processing(
        self,
        timeout_service,
        mock_queue_service,
        mock_job_service,
        expired_job_data
    ):
        """Test that one job failure doesn't stop processing others."""
        mock_queue_service.get_expired_timeouts.return_value = [
            ("job-1", 1609459200.0),
            ("job-2", 1609459300.0),
            ("job-3", 1609459400.0)
        ]

        # First job fails, others succeed
        def get_job_side_effect(job_id):
            if job_id == "job-1":
                raise Exception("Redis error")
            return expired_job_data

        mock_job_service.get_job_status.side_effect = get_job_side_effect

        result = await timeout_service.process_expired_approvals()

        # Should process 2 out of 3
        assert result == 2


class TestGetPendingApprovalCount:
    """Tests for get_pending_approval_count method."""

    @pytest.mark.asyncio
    async def test_get_count_success(
        self,
        timeout_service,
        mock_queue_service
    ):
        """Test getting pending approval count."""
        mock_queue_service.get_timeout_count.return_value = 5

        result = await timeout_service.get_pending_approval_count()

        assert result == 5
        mock_queue_service.get_timeout_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_count_zero(
        self,
        timeout_service,
        mock_queue_service
    ):
        """Test getting count when no approvals pending."""
        mock_queue_service.get_timeout_count.return_value = 0

        result = await timeout_service.get_pending_approval_count()

        assert result == 0

    @pytest.mark.asyncio
    async def test_get_count_error_returns_zero(
        self,
        timeout_service,
        mock_queue_service
    ):
        """Test that errors return 0 instead of raising."""
        mock_queue_service.get_timeout_count.side_effect = Exception("Redis error")

        result = await timeout_service.get_pending_approval_count()

        assert result == 0
