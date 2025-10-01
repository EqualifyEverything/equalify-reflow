"""PII detection background worker."""

import asyncio
import logging
from typing import Optional

from ..shared.models.queue import PIIQueuePayload
from ..shared.constants.queues import PII_QUEUE
from ..services.pii_service import PIIDetectionService
from ..services.storage_service import StorageService
from ..services.queue_service import QueueService
from ..services.job_service import JobService
from ..config import settings

logger = logging.getLogger(__name__)

# Worker configuration
QUEUE_TIMEOUT_SECONDS = 30
WORKER_SLEEP_SECONDS = 5


class PIIWorker:
    """Background worker for PII detection queue.

    Continuously polls the PII queue and processes jobs using
    PIIDetectionService.
    """

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService
    ):
        """Initialize PII worker.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
        """
        self.pii_service = PIIDetectionService(
            storage_service=storage_service,
            queue_service=queue_service,
            job_service=job_service
        )
        self.queue = queue_service
        self.running = False

    async def start(self) -> None:
        """Start the PII worker main loop.

        Continuously polls PII queue and processes jobs.
        Runs until stopped via stop() method.
        """
        self.running = True
        logger.info("PII worker started")

        while self.running:
            try:
                # Blocking pop from PII queue with timeout
                job_data = await self.queue.dequeue(PII_QUEUE, timeout=QUEUE_TIMEOUT_SECONDS)

                if job_data:
                    # Parse payload (dequeue returns dict)
                    job = PIIQueuePayload.model_validate(job_data)
                    logger.info(f"Received PII job: {job.job_id}")

                    # Process job
                    await self.pii_service.process_pii_job(job)

                else:
                    # Queue empty, continue polling
                    logger.debug("PII queue empty, continuing to poll")

            except Exception as e:
                logger.error(f"PII worker error: {e}", exc_info=True)
                # Brief pause before retry to avoid tight error loop
                await asyncio.sleep(WORKER_SLEEP_SECONDS)

        logger.info("PII worker stopped")

    def stop(self) -> None:
        """Stop the PII worker gracefully."""
        logger.info("Stopping PII worker...")
        self.running = False


# Global worker instance
_worker_instance: Optional[PIIWorker] = None


async def start_pii_worker() -> None:
    """Start the PII worker as a background task.

    Creates service instances and starts the worker loop.
    This function is called from FastAPI lifespan context.
    """
    global _worker_instance

    logger.info("Initializing PII worker...")

    # Import dependencies dynamically to avoid circular imports
    from ..dependencies import get_storage_service, get_queue_service, get_job_service

    # Create service instances using dependency injection pattern
    storage_service = await get_storage_service(s3_client=None)
    queue_service = await get_queue_service(redis_client=None)
    job_service = await get_job_service(redis_client=None)

    # Create worker
    _worker_instance = PIIWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )

    logger.info("PII worker services initialized, starting worker loop...")

    # Start worker (runs until stopped)
    await _worker_instance.start()


def get_pii_worker() -> Optional[PIIWorker]:
    """Get the global PII worker instance.

    Returns:
        Optional[PIIWorker]: Worker instance if started, None otherwise
    """
    return _worker_instance
