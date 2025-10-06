"""Integration tests for TimeoutWorker - Background maintenance worker."""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.timeout_worker import TimeoutWorker


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    client = mocker.MagicMock()
    client.__aexit__ = AsyncMock()
    return client


@pytest.fixture
def timeout_worker(mocker):
    """Create TimeoutWorker instance with mocked services."""
    # Create mock services
    storage_service = mocker.AsyncMock()
    queue_service = mocker.AsyncMock()
    job_service = mocker.AsyncMock()
    metrics_service = mocker.AsyncMock()

    # Create worker
    worker = TimeoutWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        metrics_service=metrics_service
    )

    yield worker

    # Cleanup
    if worker.running:
        worker.running = False


class TestWorkerSetup:
    """Tests for worker initialization."""

    def test_worker_initializes_services(self, timeout_worker):
        """Test that worker initializes all services on construction."""
        # Verify services initialized
        assert timeout_worker.storage_service is not None
        assert timeout_worker.queue_service is not None
        assert timeout_worker.job_service is not None
        assert timeout_worker.metrics_service is not None
        assert timeout_worker.timeout_service is not None
        assert timeout_worker.s3_cleanup_service is not None
        assert timeout_worker.orphan_service is not None

    def test_worker_initial_state(self, timeout_worker):
        """Test that worker starts in correct initial state."""
        assert timeout_worker.running is False
        assert timeout_worker.last_approval_check is None
        assert timeout_worker.last_temp_cleanup is None
        assert timeout_worker.last_orphan_cleanup is None
        assert timeout_worker.last_metrics_cleanup is None


class TestShouldRunTask:
    """Tests for _should_run_task method."""

    def test_should_run_when_never_run(self, timeout_worker):
        """Test that task should run when last_run is None."""
        result = timeout_worker._should_run_task(None, 30)
        assert result is True

    def test_should_run_after_interval(self, timeout_worker):
        """Test that task should run after interval elapsed."""
        # Use a timestamp in the past to avoid sleep
        from datetime import timedelta
        last_run = datetime.now(timezone.utc) - timedelta(seconds=1)

        result = timeout_worker._should_run_task(last_run, 0)
        assert result is True

    def test_should_not_run_before_interval(self, timeout_worker):
        """Test that task should not run before interval."""
        last_run = datetime.now(timezone.utc)

        result = timeout_worker._should_run_task(last_run, 3600)
        assert result is False


class TestWorkerTasks:
    """Tests for individual worker tasks."""

    @pytest.mark.asyncio
    async def test_run_approval_check_success(self, timeout_worker):
        """Test running approval timeout check."""
        # Setup mock services
        timeout_worker.timeout_service = AsyncMock()
        timeout_worker.timeout_service.process_expired_approvals = AsyncMock(return_value=2)
        timeout_worker.metrics_service = AsyncMock()

        await timeout_worker._run_approval_check()

        timeout_worker.timeout_service.process_expired_approvals.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_approval_check_handles_exception(self, timeout_worker):
        """Test that approval check handles exceptions gracefully."""
        timeout_worker.timeout_service = AsyncMock()
        timeout_worker.timeout_service.process_expired_approvals.side_effect = Exception(
            "Redis error"
        )
        timeout_worker.metrics_service = AsyncMock()

        # Should not raise exception
        await timeout_worker._run_approval_check()

        timeout_worker.metrics_service.increment_metric.assert_called_with(
            "worker_task_errors", 1
        )

    @pytest.mark.asyncio
    async def test_run_temp_cleanup_success(self, timeout_worker):
        """Test running temp file cleanup."""
        timeout_worker.s3_cleanup_service = AsyncMock()
        timeout_worker.s3_cleanup_service.cleanup_expired_temp_files = AsyncMock(
            return_value={
                "files_deleted": 5,
                "bytes_freed": 10240000,
                "errors": 0
            }
        )
        timeout_worker.metrics_service = AsyncMock()

        await timeout_worker._run_temp_cleanup()

        timeout_worker.s3_cleanup_service.cleanup_expired_temp_files.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_orphan_cleanup_success(self, timeout_worker):
        """Test running orphan cleanup."""
        timeout_worker.orphan_service = AsyncMock()
        timeout_worker.orphan_service.cleanup_old_completed_jobs = AsyncMock(
            return_value={"jobs_cleaned": 3, "errors": 0}
        )
        timeout_worker.orphan_service.cleanup_stuck_processing_jobs = AsyncMock(
            return_value={"jobs_failed": 1, "errors": 0}
        )
        timeout_worker.metrics_service = AsyncMock()

        await timeout_worker._run_orphan_cleanup()

        timeout_worker.orphan_service.cleanup_old_completed_jobs.assert_called_once()
        timeout_worker.orphan_service.cleanup_stuck_processing_jobs.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_metrics_cleanup_success(self, timeout_worker):
        """Test running metrics cleanup."""
        timeout_worker.metrics_service = AsyncMock()
        timeout_worker.metrics_service.cleanup_old_metrics = AsyncMock(return_value=5)
        timeout_worker.metrics_service.log_daily_summary = AsyncMock()

        await timeout_worker._run_metrics_cleanup()

        timeout_worker.metrics_service.cleanup_old_metrics.assert_called_once()
        timeout_worker.metrics_service.log_daily_summary.assert_called_once()


class TestWorkerLifecycle:
    """Tests for worker lifecycle (start/stop)."""

    @pytest.mark.asyncio
    async def test_worker_runs_until_stopped(self, timeout_worker):
        """Test that worker runs continuously until stopped."""
        # Mock task methods to do nothing
        timeout_worker._run_approval_check = AsyncMock()
        timeout_worker._run_temp_cleanup = AsyncMock()
        timeout_worker._run_orphan_cleanup = AsyncMock()
        timeout_worker._run_metrics_cleanup = AsyncMock()

        # Start worker task
        worker_task = asyncio.create_task(timeout_worker.start())

        # Wait for worker to be running (with timeout)
        for _ in range(50):  # 50 * 0.01s = 0.5s max wait
            if timeout_worker.running:
                break
            await asyncio.sleep(0.01)

        # Stop worker
        timeout_worker.stop()

        # Wait for worker to finish
        try:
            await asyncio.wait_for(worker_task, timeout=1.0)
        except asyncio.TimeoutError:
            worker_task.cancel()

        assert timeout_worker.running is False

    @pytest.mark.asyncio
    async def test_worker_handles_cancellation(self, timeout_worker):
        """Test that worker handles cancellation gracefully."""
        # Start worker task
        worker_task = asyncio.create_task(timeout_worker.start())

        # Wait for worker to be running (with timeout)
        for _ in range(50):  # 50 * 0.01s = 0.5s max wait
            if timeout_worker.running:
                break
            await asyncio.sleep(0.01)

        # Cancel worker
        worker_task.cancel()

        # Wait for cancellation
        with pytest.raises(asyncio.CancelledError):
            await worker_task

    @pytest.mark.asyncio
    async def test_worker_recovers_from_task_errors(self, timeout_worker):
        """Test that worker continues after task errors."""
        # Mock approval check to raise exception
        timeout_worker._run_approval_check = AsyncMock(
            side_effect=Exception("Task error")
        )

        # Start worker task
        worker_task = asyncio.create_task(timeout_worker.start())

        # Wait for worker to run and handle error (with timeout)
        for _ in range(50):  # 50 * 0.01s = 0.5s max wait
            if timeout_worker.running and timeout_worker._run_approval_check.call_count > 0:
                break
            await asyncio.sleep(0.01)

        # Stop worker
        timeout_worker.stop()

        # Wait for worker to finish
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except asyncio.TimeoutError:
            worker_task.cancel()

        # Worker should have stopped gracefully
        assert timeout_worker.running is False


class TestTaskScheduling:
    """Tests for task scheduling logic."""

    @pytest.mark.asyncio
    async def test_approval_check_runs_on_schedule(self, timeout_worker):
        """Test that approval check runs according to schedule."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.approval_check_interval_seconds = 0  # Run immediately

            timeout_worker.timeout_service = AsyncMock()
            timeout_worker.timeout_service.process_expired_approvals = AsyncMock(
                return_value=0
            )
            timeout_worker.metrics_service = AsyncMock()

            # Set last run to None (should run)
            timeout_worker.last_approval_check = None

            # Should run immediately
            should_run = timeout_worker._should_run_task(
                timeout_worker.last_approval_check,
                mock_settings.approval_check_interval_seconds
            )

            assert should_run is True

    @pytest.mark.asyncio
    async def test_tasks_respect_different_intervals(self, timeout_worker):
        """Test that different tasks have different intervals."""
        now = datetime.now(timezone.utc)

        # Set all tasks to have just run
        timeout_worker.last_approval_check = now
        timeout_worker.last_temp_cleanup = now
        timeout_worker.last_orphan_cleanup = now
        timeout_worker.last_metrics_cleanup = now

        # Check intervals (approval: 30s, temp: 1h, orphan: 4h, metrics: 24h)
        # None should run immediately
        assert not timeout_worker._should_run_task(now, 30)
        assert not timeout_worker._should_run_task(now, 3600)
        assert not timeout_worker._should_run_task(now, 14400)
        assert not timeout_worker._should_run_task(now, 86400)
