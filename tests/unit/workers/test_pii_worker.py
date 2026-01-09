"""Unit tests for PIIWorker shutdown requeueing.

These tests verify that jobs are not lost during graceful shutdowns -
a critical requirement for zero-downtime deployments.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.shared.constants.queues import PII_QUEUE
from src.shared.models.queue import PIIQueuePayload
from src.workers.pii_worker import PIIWorker


@pytest.fixture
def mock_services():
    """Create mock services for PIIWorker."""
    storage = MagicMock()
    queue = AsyncMock()
    job = AsyncMock()
    s3_url = MagicMock()
    return storage, queue, job, s3_url


@pytest.mark.asyncio
async def test_pii_worker_requeues_on_shutdown(mock_services):
    """Job in progress gets requeued when shutdown event fires mid-processing.

    Catches: Job loss during deployment rollouts - if this fails, jobs
    would disappear when workers restart.
    """
    storage, queue, job, s3_url = mock_services

    # Create test job payload with required created_at field
    job_payload = {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
        "created_at": datetime.now(UTC).isoformat(),
    }

    shutdown_event = asyncio.Event()

    async def dequeue_with_shutdown(*args, **kwargs):
        """Return job on first call and signal shutdown, then return None."""
        if queue.dequeue.call_count == 1:
            # First call: return job and signal shutdown
            shutdown_event.set()
            return job_payload
        # Subsequent calls: return None to exit loop
        return None

    queue.dequeue.side_effect = dequeue_with_shutdown

    # Create worker
    worker = PIIWorker(
        storage_service=storage,
        queue_service=queue,
        job_service=job,
        s3_url_service=s3_url,
    )

    # Start worker - should dequeue, see shutdown, requeue, and exit
    await worker.start(shutdown_event)

    # Verify job was requeued to the same queue
    queue.enqueue.assert_called_once()
    call_args = queue.enqueue.call_args
    assert call_args[0][0] == PII_QUEUE

    # Verify the requeued payload has correct job_id
    requeued_payload = call_args[0][1]
    assert isinstance(requeued_payload, PIIQueuePayload)
    assert requeued_payload.job_id == job_payload["job_id"]
