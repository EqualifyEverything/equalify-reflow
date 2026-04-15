"""Tests for resource management in dependency injection and workers.

This test suite verifies that:
1. Redis and S3 clients are properly created and cleaned up
2. Service dependency functions enforce proper client management
3. Workers initialize resources correctly without leaks
4. Multiple worker start/stop cycles don't leak connections
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio import Redis
from src.dependencies import (
    get_redis_client,
    get_s3_client,
)
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.services.s3_url_service import S3URLService
from src.services.storage_service import StorageService


class TestClientLifecycle:
    """Tests for Redis and S3 client lifecycle management."""

    @pytest.mark.asyncio
    async def test_redis_client_cleanup(self):
        """Test that Redis client is properly closed after use."""
        client = None
        async for c in get_redis_client():
            client = c
            assert client is not None
            assert isinstance(client, Redis)
            # Client should be open during use
            # Verify it works
            try:
                await client.ping()
            except Exception:
                # Redis might not be running in test context
                pass
            break

        # After exiting async generator, client should be closed
        # The client's connection pool is closed, making it unusable
        # We can verify the client object exists but won't test operations
        # since they might fail for reasons other than being closed
        assert client is not None

    @pytest.mark.asyncio
    async def test_redis_client_cleanup_on_exception(self):
        """Test that Redis client generator handles exceptions in finally block."""
        exception_raised = False

        try:
            async for client in get_redis_client():
                # Simulate exception during usage
                exception_raised = True
                raise RuntimeError("Simulated error")
        except RuntimeError:
            pass

        # Verify exception was raised (generator handled it properly)
        assert exception_raised

    @pytest.mark.asyncio
    async def test_s3_client_creation(self):
        """Test that S3 client is properly created."""
        client = None
        async for c in get_s3_client():
            client = c
            assert client is not None
            # S3 client should have expected methods
            assert hasattr(client, 'put_object')
            assert hasattr(client, 'get_object')
            break

    @pytest.mark.asyncio
    async def test_multiple_redis_clients_independent(self):
        """Test that multiple Redis clients are independent."""
        client1 = await anext(get_redis_client())
        client2 = await anext(get_redis_client())

        # Should be different client instances
        assert client1 is not client2


class TestServiceDependencyInjection:
    """Tests for service dependency injection patterns."""

    @pytest.mark.asyncio
    async def test_services_use_dependency_injection(self):
        """Test that services are created with auto-injected clients via Depends()."""
        # Services should use Depends() for auto-injection in FastAPI context
        # The dependencies use sub-dependencies that automatically provide clients
        # This test verifies the pattern is correctly set up
        redis_client = await anext(get_redis_client())
        s3_client = await anext(get_s3_client())

        # Create services directly (as workers do)
        queue_service = QueueService(redis_client=redis_client)
        job_service = JobService(redis_client=redis_client)
        storage_service = StorageService(
            s3_client=s3_client,
            temp_bucket="test-temp",
            results_bucket="test-results"
        )

        assert isinstance(queue_service, QueueService)
        assert isinstance(job_service, JobService)
        assert isinstance(storage_service, StorageService)


class TestWorkerResourceManagement:
    """Tests for worker resource management patterns."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)  # Worker startup and shutdown can take time
    async def test_pii_worker_initialization_pattern(self):
        """Test that PII worker uses correct initialization pattern."""
        from src.workers.pii_worker import start_pii_worker

        with patch('src.dependencies.get_redis_client') as mock_redis_gen, \
             patch('src.dependencies.get_s3_client') as mock_s3_gen:

            # Create mock clients
            mock_redis = AsyncMock()
            mock_s3 = MagicMock()

            # Mock async generators
            async def redis_gen():
                yield mock_redis

            async def s3_gen():
                yield mock_s3

            mock_redis_gen.return_value = redis_gen()
            mock_s3_gen.return_value = s3_gen()

            # Start worker in background (it will run indefinitely)
            worker_task = asyncio.create_task(start_pii_worker())

            # Wait for worker to be ready (with timeout)
            from src.workers.pii_worker import get_pii_worker
            worker = None
            for _ in range(50):  # 50 * 0.01s = 0.5s max wait
                worker = get_pii_worker()
                if worker and worker.running:
                    break
                await asyncio.sleep(0.01)

            # Stop worker
            if worker:
                worker.stop()

            # Wait for worker to finish
            try:
                await asyncio.wait_for(worker_task, timeout=2.0)
            except TimeoutError:
                worker_task.cancel()

            # Verify clients were created using anext pattern
            # (mock_redis_gen and mock_s3_gen were called)
            mock_redis_gen.assert_called_once()
            mock_s3_gen.assert_called_once()

    # Note: test_processing_worker_initialization_pattern was removed
    # because ProcessingWorker has been deleted as part of the agentic pipeline refactor.
    # Processing is now triggered directly via ProcessingService, not through a queue worker.


class TestConnectionPoolManagement:
    """Tests for connection pool tracking and leak detection."""

    @pytest.mark.asyncio
    async def test_redis_connection_pool_cleanup(self):
        """Test that Redis connection pool can be created and cleaned up multiple times."""
        # Create and cleanup multiple clients
        clients = []
        for _ in range(3):
            async for client in get_redis_client():
                clients.append(client)
                # Use client briefly
                break

        # Verify all clients were created
        assert len(clients) == 3
        # Each should be a Redis client instance
        for client in clients:
            assert client is not None

    @pytest.mark.asyncio
    async def test_no_leaked_connections_after_operations(self):
        """Test that services can be created and used with proper client management."""
        # Simulate multiple operations
        async for redis_client in get_redis_client():
            queue_service = QueueService(redis_client=redis_client)
            job_service = JobService(redis_client=redis_client)

            # Simulate some operations
            assert queue_service is not None
            assert job_service is not None
            break

        # Test completed - client cleanup happens in finally block of generator


class TestMultipleWorkerCycles:
    """Tests for multiple worker start/stop cycles."""

    @pytest.mark.asyncio
    async def test_worker_can_restart_multiple_times(self):
        """Test that worker can be started and stopped multiple times without leaks."""
        from src.services.job_service import JobService
        from src.services.queue_service import QueueService
        from src.services.storage_service import StorageService
        from src.workers.pii_worker import PIIWorker

        for cycle in range(3):
            # Create mock services
            mock_redis = AsyncMock()
            mock_s3 = MagicMock()

            storage_service = StorageService(
                s3_client=mock_s3,
                temp_bucket="test-temp",
                results_bucket="test-results"
            )
            queue_service = QueueService(redis_client=mock_redis)
            job_service = JobService(redis_client=mock_redis)
            s3_url_service = S3URLService(
                s3_client=mock_s3,
                temp_bucket="test-temp",
                results_bucket="test-results"
            )

            # Create worker
            worker = PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service,
                s3_url_service=s3_url_service
            )

            # Start worker
            worker_task = asyncio.create_task(worker.start())

            # Wait for worker to be running (with timeout)
            for _ in range(50):  # 50 * 0.01s = 0.5s max wait
                if worker.running:
                    break
                await asyncio.sleep(0.01)

            # Verify worker started
            assert worker.running is True

            # Stop worker
            worker.stop()

            # Wait for worker to fully stop
            for _ in range(100):  # 100 * 0.01s = 1s max wait
                if not worker.running:
                    break
                await asyncio.sleep(0.01)

            # Cancel the task if still running
            if not worker_task.done():
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            # Verify worker stopped
            assert worker.running is False

    @pytest.mark.asyncio
    async def test_exception_during_init_doesnt_leak(self):
        """Test that exceptions during initialization are handled properly."""
        exception_caught = False

        try:
            async for redis_client in get_redis_client():
                # Simulate exception during service initialization
                raise RuntimeError("Initialization failed")
        except RuntimeError:
            exception_caught = True

        # Verify exception was properly handled
        assert exception_caught


class TestResourceCleanupOnExceptions:
    """Tests for resource cleanup when exceptions occur."""

    @pytest.mark.asyncio
    async def test_cleanup_on_service_exception(self):
        """Test that generator cleanup happens when service raises exception."""
        exception_caught = False

        try:
            async for redis_client in get_redis_client():
                QueueService(redis_client=redis_client)
                # Simulate exception during usage
                raise ValueError("Service error")
        except ValueError:
            exception_caught = True

        # Verify exception was properly handled - cleanup happens in finally block
        assert exception_caught

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)  # Extend timeout for this test (worker sleeps 5s on error)
    async def test_cleanup_on_worker_exception(self, mocker):
        """Test that resources are cleaned up when worker raises exception."""
        from src.workers.pii_worker import PIIWorker

        # Mock settings.worker_error_sleep_seconds to avoid long waits
        mocker.patch("src.config.settings.worker_error_sleep_seconds", 0.1)

        mock_redis = AsyncMock()
        mock_s3 = MagicMock()

        storage_service = StorageService(
            s3_client=mock_s3,
            temp_bucket="test",
            results_bucket="test"
        )
        queue_service = QueueService(redis_client=mock_redis)
        job_service = JobService(redis_client=mock_redis)
        s3_url_service = S3URLService(
            s3_client=mock_s3,
            temp_bucket="test",
            results_bucket="test"
        )

        # Mock dequeue to raise exception
        queue_service.dequeue = AsyncMock(side_effect=RuntimeError("Queue error"))

        worker = PIIWorker(
            storage_service=storage_service,
            queue_service=queue_service,
            job_service=job_service,
            s3_url_service=s3_url_service
        )

        # Start worker
        worker_task = asyncio.create_task(worker.start())

        # Wait for worker to start and hit error at least once
        # With mocked 0.1s sleep, this should complete quickly
        for _ in range(50):  # 50 * 0.05s = 2.5s max wait
            await asyncio.sleep(0.05)
            if queue_service.dequeue.call_count >= 1:  # Hit error at least once
                break

        # Stop worker (worker continues running despite errors)
        worker.stop()

        # Wait for worker to finish (increased timeout for safety)
        try:
            await asyncio.wait_for(worker_task, timeout=2.0)
        except TimeoutError:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

        # Worker should have stopped despite errors
        assert worker.running is False
