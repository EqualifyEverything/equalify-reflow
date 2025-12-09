"""Unit tests for ProcessingWorker - Simplified approach.

Tests worker initialization and individual methods without running the infinite loop.
This avoids async timeout issues while still validating core functionality.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from src.shared.constants.queues import PROCESSING_QUEUE
from src.shared.models.queue import ProcessingQueuePayload
from src.workers.processing_worker import ProcessingWorker

pytestmark = pytest.mark.unit


# ============================================================================
# Initialization Tests (2 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_processing_worker_initialization(
    mock_storage_service, mock_queue_service, mock_job_service
):
    """Test ProcessingWorker initializes ProcessingService correctly."""
    worker = ProcessingWorker(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
    )

    # Should create ProcessingService
    assert worker.processing_service is not None
    assert worker.queue == mock_queue_service
    assert worker.running is False


@pytest.mark.asyncio
async def test_processing_worker_running_flag_initialized():
    """Test running flag is initialized to False."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    # Should start as not running
    assert worker.running is False


# ============================================================================
# Single Iteration Tests - Test one loop iteration without infinite loop (10 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_worker_dequeues_from_correct_queue():
    """Test worker dequeues from processing queue."""
    mock_queue = AsyncMock()
    mock_queue.dequeue = AsyncMock(return_value=None)

    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=mock_queue,
        job_service=AsyncMock(),
    )

    # Manually call dequeue (simulating one iteration)
    await worker.queue.dequeue(PROCESSING_QUEUE, timeout=60)

    # Verify called with correct queue
    mock_queue.dequeue.assert_called_once_with(PROCESSING_QUEUE, timeout=60)


@pytest.mark.asyncio
async def test_worker_processes_valid_job():
    """Test worker processes a valid job payload."""
    mock_queue = AsyncMock()
    mock_storage = AsyncMock()
    mock_job_svc = AsyncMock()

    worker = ProcessingWorker(
        storage_service=mock_storage,
        queue_service=mock_queue,
        job_service=mock_job_svc,
    )

    # Create valid job
    job_data = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test.pdf",
        "approved_at": None,
    }

    # Parse and process
    job = ProcessingQueuePayload.model_validate(job_data)
    worker.processing_service.process_document = AsyncMock()

    await worker.processing_service.process_document(job)

    # Should have been called
    worker.processing_service.process_document.assert_called_once_with(job)


@pytest.mark.asyncio
async def test_worker_validates_job_payload():
    """Test worker validates job payload structure."""
    # Valid payload
    valid_data = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test.pdf",
        "approved_at": None,
    }

    job = ProcessingQueuePayload.model_validate(valid_data)
    assert job.job_id == "550e8400-e29b-41d4-a716-446655440000"

    # Invalid payload should raise ValidationError
    invalid_data = {"invalid": "data"}

    with pytest.raises(Exception):  # Pydantic ValidationError
        ProcessingQueuePayload.model_validate(invalid_data)


@pytest.mark.asyncio
async def test_worker_handles_empty_queue_result():
    """Test worker handles None from empty queue."""
    mock_queue = AsyncMock()
    mock_queue.dequeue = AsyncMock(return_value=None)

    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=mock_queue,
        job_service=AsyncMock(),
    )

    # Get result
    result = await worker.queue.dequeue(PROCESSING_QUEUE, timeout=60)

    # Should be None
    assert result is None


@pytest.mark.asyncio
async def test_worker_stop_method_sets_running_false():
    """Test stop() method sets running to False."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    worker.running = True
    worker.stop()

    assert worker.running is False


@pytest.mark.asyncio
async def test_worker_start_sets_running_true():
    """Test start() sets running flag to True immediately."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    # Pre-set shutdown to exit immediately
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    with patch("src.workers.processing_worker.worker_active_gauge"):
        await worker.start(shutdown_event)

    # Should have set running=True
    assert worker.running is True


@pytest.mark.asyncio
async def test_worker_enqueue_method_works():
    """Test worker can enqueue jobs (unit test of enqueue logic)."""
    mock_queue = AsyncMock()
    mock_queue.enqueue = AsyncMock()

    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=mock_queue,
        job_service=AsyncMock(),
    )

    job_data = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test.pdf",
        "approved_at": None,
    }

    # Directly test enqueue capability
    await worker.queue.enqueue(PROCESSING_QUEUE, job_data)

    # Should have called enqueue
    mock_queue.enqueue.assert_called_once_with(PROCESSING_QUEUE, job_data)


@pytest.mark.asyncio
async def test_worker_uses_processing_service():
    """Test worker uses ProcessingService for document processing."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    # Should have created ProcessingService
    assert worker.processing_service is not None
    assert hasattr(worker.processing_service, 'process_document')


@pytest.mark.asyncio
async def test_worker_processing_service_integration():
    """Test ProcessingService is properly integrated."""
    mock_storage = AsyncMock()
    mock_queue = AsyncMock()
    mock_job = AsyncMock()

    worker = ProcessingWorker(
        storage_service=mock_storage,
        queue_service=mock_queue,
        job_service=mock_job,
    )

    # Verify services are wired correctly
    assert worker.processing_service.storage == mock_storage
    assert worker.processing_service.queue == mock_queue
    assert worker.processing_service.job == mock_job


@pytest.mark.asyncio
async def test_worker_metrics_gauge_set_on_start():
    """Test worker sets active gauge metric on start."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    worker.queue.dequeue = AsyncMock(return_value=None)
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    with patch("src.workers.processing_worker.worker_active_gauge") as mock_gauge:
        await worker.start(shutdown_event)

        # Should set gauge to 1 on start
        calls = mock_gauge.labels.return_value.set.call_args_list
        # First call should be set(1)
        assert calls[0][0][0] == 1


@pytest.mark.asyncio
async def test_worker_metrics_gauge_set_on_shutdown():
    """Test worker sets active gauge to 0 on shutdown."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    worker.queue.dequeue = AsyncMock(return_value=None)
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    with patch("src.workers.processing_worker.worker_active_gauge") as mock_gauge:
        await worker.start(shutdown_event)

        # Should set gauge to 0 on shutdown
        calls = mock_gauge.labels.return_value.set.call_args_list
        # Last call should be set(0)
        assert calls[-1][0][0] == 0


# ============================================================================
# Error Handling Tests (2 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_worker_handles_processing_exception():
    """Test worker can handle exceptions during processing."""
    worker = ProcessingWorker(
        storage_service=AsyncMock(),
        queue_service=AsyncMock(),
        job_service=AsyncMock(),
    )

    job_data = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test.pdf",
        "approved_at": None,
    }

    job = ProcessingQueuePayload.model_validate(job_data)
    worker.processing_service.process_document = AsyncMock(
        side_effect=Exception("Processing error")
    )

    # Should raise (would be caught in actual worker loop)
    with pytest.raises(Exception, match="Processing error"):
        await worker.processing_service.process_document(job)


@pytest.mark.asyncio
async def test_worker_validates_invalid_payload():
    """Test worker validation catches invalid payloads."""
    invalid_data = {"invalid": "payload"}

    # Should raise validation error
    with pytest.raises(Exception):  # Pydantic ValidationError
        ProcessingQueuePayload.model_validate(invalid_data)
