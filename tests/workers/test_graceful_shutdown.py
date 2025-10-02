"""Tests for graceful worker shutdown (Bug #15).

Tests that workers:
- Check shutdown event during operation
- Complete current job before exiting
- Requeue unprocessed jobs
- Shutdown within timeout period
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from src.workers.pii_worker import PIIWorker, start_pii_worker
from src.workers.processing_worker import ProcessingWorker, start_processing_worker
from src.workers.timeout_worker import TimeoutWorker, start_timeout_worker
from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


class TestPIIWorkerGracefulShutdown:
    """Tests for PII worker graceful shutdown."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for worker."""
        storage_service = Mock(spec=StorageService)
        queue_service = AsyncMock(spec=QueueService)
        job_service = AsyncMock(spec=JobService)
        return storage_service, queue_service, job_service

    @pytest.mark.asyncio
    async def test_worker_stops_on_shutdown_event(self, mock_services):
        """Test that worker stops when shutdown event is set."""
        storage_service, queue_service, job_service = mock_services

        # Mock empty queue
        queue_service.dequeue = AsyncMock(return_value=None)

        worker = PIIWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        # Start worker in background
        worker_task = asyncio.create_task(worker.start(shutdown_event))

        # Give worker time to start
        await asyncio.sleep(0.1)

        # Signal shutdown
        shutdown_event.set()

        # Worker should stop gracefully
        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_task.done()

    @pytest.mark.asyncio
    async def test_worker_requeues_job_on_shutdown(self, mock_services):
        """Test that worker requeues job if shutdown occurs before processing."""
        storage_service, queue_service, job_service = mock_services

        job_data = {
            "job_id": "test-job-123",
            "s3_key": "temp/test-job-123.pdf",
            "timestamp": "2024-01-01T00:00:00Z"
        }

        # First call returns a job, subsequent calls return None
        queue_service.dequeue = AsyncMock(side_effect=[job_data, None])
        queue_service.enqueue = AsyncMock()

        worker = PIIWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        # Start worker
        worker_task = asyncio.create_task(worker.start(shutdown_event))

        # Give worker time to dequeue job
        await asyncio.sleep(0.1)

        # Signal shutdown before job is processed
        shutdown_event.set()

        # Wait for graceful shutdown
        await asyncio.wait_for(worker_task, timeout=2.0)

        # Job should have been requeued
        queue_service.enqueue.assert_called_once()
        assert "PII_QUEUE" in str(queue_service.enqueue.call_args)

    @pytest.mark.asyncio
    async def test_worker_completes_current_job_before_shutdown(self, mock_services):
        """Test that worker completes current job before shutting down."""
        storage_service, queue_service, job_service = mock_services

        job_completed = asyncio.Event()
        shutdown_event = asyncio.Event()

        async def mock_process_pii_job(job):
            """Simulate job processing."""
            await asyncio.sleep(0.2)  # Simulate work
            job_completed.set()

        # Mock PII service
        with patch('src.workers.pii_worker.PIIDetectionService') as mock_pii_service_class:
            mock_pii_service = AsyncMock()
            mock_pii_service.process_pii_job = mock_process_pii_job
            mock_pii_service_class.return_value = mock_pii_service

            worker = PIIWorker(storage_service, queue_service, job_service)
            worker.pii_service = mock_pii_service

            job_data = {
                "job_id": "test-job-123",
                "s3_key": "temp/test-job-123.pdf",
                "timestamp": "2024-01-01T00:00:00Z"
            }

            # Return job once, then None
            queue_service.dequeue = AsyncMock(side_effect=[job_data, None])

            # Start worker
            worker_task = asyncio.create_task(worker.start(shutdown_event))

            # Wait for job to start processing
            await asyncio.sleep(0.1)

            # Signal shutdown while job is processing
            shutdown_event.set()

            # Job should complete before worker stops
            await asyncio.wait_for(job_completed.wait(), timeout=2.0)
            await asyncio.wait_for(worker_task, timeout=2.0)

            assert job_completed.is_set()


class TestProcessingWorkerGracefulShutdown:
    """Tests for Processing worker graceful shutdown."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for worker."""
        storage_service = Mock(spec=StorageService)
        queue_service = AsyncMock(spec=QueueService)
        job_service = AsyncMock(spec=JobService)
        return storage_service, queue_service, job_service

    @pytest.mark.asyncio
    async def test_processing_worker_stops_on_shutdown(self, mock_services):
        """Test that processing worker stops on shutdown event."""
        storage_service, queue_service, job_service = mock_services

        queue_service.dequeue = AsyncMock(return_value=None)

        worker = ProcessingWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        worker_task = asyncio.create_task(worker.start(shutdown_event))
        await asyncio.sleep(0.1)

        shutdown_event.set()

        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_task.done()

    @pytest.mark.asyncio
    async def test_processing_worker_requeues_on_shutdown(self, mock_services):
        """Test that processing worker requeues job on shutdown."""
        storage_service, queue_service, job_service = mock_services

        job_data = {
            "job_id": "test-job-456",
            "s3_key": "temp/test-job-456.pdf",
            "timestamp": "2024-01-01T00:00:00Z"
        }

        queue_service.dequeue = AsyncMock(side_effect=[job_data, None])
        queue_service.enqueue = AsyncMock()

        worker = ProcessingWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        worker_task = asyncio.create_task(worker.start(shutdown_event))
        await asyncio.sleep(0.1)

        shutdown_event.set()

        await asyncio.wait_for(worker_task, timeout=2.0)

        # Job should be requeued
        queue_service.enqueue.assert_called_once()


class TestTimeoutWorkerGracefulShutdown:
    """Tests for Timeout worker graceful shutdown."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for timeout worker."""
        storage_service = Mock(spec=StorageService)
        queue_service = AsyncMock(spec=QueueService)
        job_service = AsyncMock(spec=JobService)
        metrics_service = AsyncMock()
        return storage_service, queue_service, job_service, metrics_service

    @pytest.mark.asyncio
    async def test_timeout_worker_stops_on_shutdown(self, mock_services):
        """Test that timeout worker stops on shutdown event."""
        storage_service, queue_service, job_service, metrics_service = mock_services

        worker = TimeoutWorker(
            storage_service,
            queue_service,
            job_service,
            metrics_service
        )
        shutdown_event = asyncio.Event()

        worker_task = asyncio.create_task(worker.start(shutdown_event))
        await asyncio.sleep(0.1)

        shutdown_event.set()

        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_task.done()

    @pytest.mark.asyncio
    async def test_timeout_worker_stops_periodic_tasks(self, mock_services):
        """Test that timeout worker stops periodic tasks on shutdown."""
        storage_service, queue_service, job_service, metrics_service = mock_services

        task_ran = asyncio.Event()

        # Mock timeout service to track if tasks run
        with patch('src.workers.timeout_worker.TimeoutService') as mock_timeout_service_class:
            mock_timeout_service = AsyncMock()
            mock_timeout_service.process_expired_approvals = AsyncMock(
                side_effect=lambda: task_ran.set() or 0
            )
            mock_timeout_service_class.return_value = mock_timeout_service

            worker = TimeoutWorker(
                storage_service,
                queue_service,
                job_service,
                metrics_service
            )
            worker.timeout_service = mock_timeout_service

            shutdown_event = asyncio.Event()

            worker_task = asyncio.create_task(worker.start(shutdown_event))

            # Let worker run for a bit
            await asyncio.sleep(0.2)

            # Signal shutdown
            shutdown_event.set()

            # Worker should stop cleanly
            await asyncio.wait_for(worker_task, timeout=2.0)
            assert worker_task.done()


class TestMainLifespanGracefulShutdown:
    """Tests for main.py lifespan graceful shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_event_passed_to_workers(self):
        """Test that shutdown event is created and passed to all workers."""
        with patch('src.main.start_pii_worker') as mock_pii, \
             patch('src.main.start_processing_worker') as mock_processing, \
             patch('src.main.start_timeout_worker') as mock_timeout:

            # Make workers return immediately
            mock_pii.return_value = asyncio.sleep(0)
            mock_processing.return_value = asyncio.sleep(0)
            mock_timeout.return_value = asyncio.sleep(0)

            from src.main import lifespan
            from fastapi import FastAPI

            app = FastAPI()

            async with lifespan(app):
                # During startup, workers should be called with shutdown event
                await asyncio.sleep(0.1)

            # All workers should have been called with a shutdown event
            assert mock_pii.called
            assert mock_processing.called
            assert mock_timeout.called

            # Each should have received an Event as first argument
            pii_event = mock_pii.call_args[0][0]
            processing_event = mock_processing.call_args[0][0]
            timeout_event = mock_timeout.call_args[0][0]

            assert isinstance(pii_event, asyncio.Event)
            assert isinstance(processing_event, asyncio.Event)
            assert isinstance(timeout_event, asyncio.Event)

            # All should be the same event
            assert pii_event is processing_event is timeout_event

    @pytest.mark.asyncio
    async def test_graceful_shutdown_timeout_mechanism(self):
        """Test that graceful shutdown has timeout with forced cancellation fallback."""
        with patch('src.main.start_pii_worker') as mock_pii, \
             patch('src.main.start_processing_worker') as mock_processing, \
             patch('src.main.start_timeout_worker') as mock_timeout:

            # Make workers hang indefinitely
            mock_pii.return_value = asyncio.sleep(1000)
            mock_processing.return_value = asyncio.sleep(1000)
            mock_timeout.return_value = asyncio.sleep(1000)

            from src.main import lifespan
            from fastapi import FastAPI

            app = FastAPI()

            # Start lifespan
            lifespan_context = lifespan(app)
            await lifespan_context.__aenter__()

            # Trigger shutdown - should timeout and force cancel
            start_time = asyncio.get_event_loop().time()
            await lifespan_context.__aexit__(None, None, None)
            end_time = asyncio.get_event_loop().time()

            # Should complete within timeout + small margin (30s timeout configured)
            # But in test we'll allow 35s to be safe
            assert end_time - start_time < 35

    @pytest.mark.asyncio
    async def test_shutdown_event_set_on_shutdown(self):
        """Test that shutdown event is set when lifespan ends."""
        shutdown_event_was_set = asyncio.Event()

        async def mock_worker(shutdown_event):
            """Mock worker that checks if shutdown event gets set."""
            while not shutdown_event.is_set():
                await asyncio.sleep(0.1)
            shutdown_event_was_set.set()

        with patch('src.main.start_pii_worker', side_effect=mock_worker), \
             patch('src.main.start_processing_worker', side_effect=mock_worker), \
             patch('src.main.start_timeout_worker', side_effect=mock_worker):

            from src.main import lifespan
            from fastapi import FastAPI

            app = FastAPI()

            async with lifespan(app):
                await asyncio.sleep(0.1)

            # Shutdown event should have been set
            await asyncio.wait_for(shutdown_event_was_set.wait(), timeout=2.0)
            assert shutdown_event_was_set.is_set()


class TestGracefulShutdownEdgeCases:
    """Test edge cases in graceful shutdown."""

    @pytest.mark.asyncio
    async def test_worker_handles_none_shutdown_event(self):
        """Test that workers still work without shutdown event (backward compatibility)."""
        mock_services = (Mock(), AsyncMock(), AsyncMock())
        storage_service, queue_service, job_service = mock_services

        queue_service.dequeue = AsyncMock(return_value=None)

        worker = PIIWorker(storage_service, queue_service, job_service)

        # Start without shutdown event (should still work)
        worker_task = asyncio.create_task(worker.start(shutdown_event=None))

        await asyncio.sleep(0.1)

        # Stop via stop() method
        worker.stop()

        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_task.done()

    @pytest.mark.asyncio
    async def test_multiple_shutdown_signals_handled_safely(self):
        """Test that multiple shutdown signals don't cause issues."""
        mock_services = (Mock(), AsyncMock(), AsyncMock())
        storage_service, queue_service, job_service = mock_services

        queue_service.dequeue = AsyncMock(return_value=None)

        worker = PIIWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        worker_task = asyncio.create_task(worker.start(shutdown_event))
        await asyncio.sleep(0.1)

        # Signal shutdown multiple times
        shutdown_event.set()
        shutdown_event.set()
        shutdown_event.set()

        # Should still shutdown cleanly
        await asyncio.wait_for(worker_task, timeout=2.0)
        assert worker_task.done()

    @pytest.mark.asyncio
    async def test_worker_error_during_shutdown(self, caplog):
        """Test that errors during shutdown are logged but don't prevent shutdown."""
        import logging

        mock_services = (Mock(), AsyncMock(), AsyncMock())
        storage_service, queue_service, job_service = mock_services

        # Make enqueue fail
        queue_service.dequeue = AsyncMock(return_value={"job_id": "test"})
        queue_service.enqueue = AsyncMock(side_effect=Exception("Redis connection lost"))

        worker = PIIWorker(storage_service, queue_service, job_service)
        shutdown_event = asyncio.Event()

        worker_task = asyncio.create_task(worker.start(shutdown_event))
        await asyncio.sleep(0.1)

        with caplog.at_level(logging.ERROR):
            shutdown_event.set()
            await asyncio.wait_for(worker_task, timeout=2.0)

        # Worker should still complete despite error
        assert worker_task.done()
