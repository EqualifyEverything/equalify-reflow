"""Background worker for processing AI-enhanced PDF conversions."""

import asyncio
import logging

from redis.asyncio import Redis

from ..config import settings
from ..dependencies import get_redis_client, get_s3_client
from ..services.application_service import ApplicationService
from ..services.correction_approval_service import CorrectionApprovalService
from ..services.job_service import JobService
from ..services.metrics_service import (
    worker_active_gauge,
    worker_errors_total,
    worker_jobs_processed_total,
)
from ..services.processing_service import ProcessingService
from ..services.queue_service import QueueService
from ..services.remediation_storage_service import RemediationStorageService
from ..services.storage_service import StorageService
from ..shared.constants.queues import PROCESSING_QUEUE
from ..shared.models.queue import ProcessingQueuePayload

logger = logging.getLogger(__name__)


class ProcessingWorker:
    """Background worker for AI processing queue.

    Continuously polls the processing queue and processes jobs using
    ProcessingService with AI-powered accessibility enhancement.
    """

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService,
        redis_client: Redis | None = None,
        correction_approval_service: CorrectionApprovalService | None = None,
        application_service: ApplicationService | None = None,
    ):
        """Initialize processing worker.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            redis_client: Redis client for token storage
            correction_approval_service: Correction approval service for review workflow
            application_service: Application service for applying proposals
        """
        self.processing_service = ProcessingService(
            storage_service=storage_service,
            queue_service=queue_service,
            job_service=job_service,
            redis_client=redis_client,
            correction_approval_service=correction_approval_service,
            application_service=application_service,
        )
        self.queue = queue_service
        self.running = False

    async def start(self, shutdown_event: asyncio.Event | None = None) -> None:
        """Start the processing worker main loop.

        Continuously polls processing queue and processes jobs.
        Runs until stopped via stop() method or shutdown_event is set.

        Args:
            shutdown_event: Optional event to signal graceful shutdown
        """
        self.running = True
        logger.info("Processing worker started")

        # Mark worker as active in Prometheus
        worker_active_gauge.labels(worker_name="processing").set(1)

        try:
            while self.running and (shutdown_event is None or not shutdown_event.is_set()):
                try:
                    # Blocking pop from processing queue with timeout
                    job_data = await self.queue.dequeue(
                        PROCESSING_QUEUE, timeout=settings.processing_worker_queue_timeout_seconds
                    )

                    if job_data:
                        # Parse payload
                        job = ProcessingQueuePayload.model_validate(job_data)

                        # Check shutdown before processing
                        if shutdown_event and shutdown_event.is_set():
                            logger.info("Shutdown requested, requeueing job and stopping")
                            # Requeue job for next worker
                            await self.queue.enqueue(PROCESSING_QUEUE, job)
                            break
                        logger.info(f"Received processing job: {job.job_id}")

                        # Process job
                        await self.processing_service.process_document(job)

                        # Track successful processing
                        worker_jobs_processed_total.labels(
                            worker_name="processing", result="success"
                        ).inc()

                    else:
                        # Queue empty, continue polling
                        logger.debug("Processing queue empty, continuing to poll")

                except Exception as e:
                    logger.error(f"Processing worker error: {e}", exc_info=True)
                    # Track error
                    worker_errors_total.labels(
                        worker_name="processing", error_type=type(e).__name__
                    ).inc()
                    worker_jobs_processed_total.labels(
                        worker_name="processing", result="error"
                    ).inc()
                    # Brief pause before retry to avoid tight error loop
                    await asyncio.sleep(settings.worker_error_sleep_seconds)

        finally:
            # Mark worker as inactive when shutting down
            worker_active_gauge.labels(worker_name="processing").set(0)
            logger.info("Processing worker shutting down gracefully")

    def stop(self) -> None:
        """Stop the processing worker gracefully."""
        logger.info("Stopping processing worker...")
        self.running = False


# Global worker instance
_worker_instance: ProcessingWorker | None = None


async def start_processing_worker(shutdown_event: asyncio.Event | None = None) -> None:
    """Start the processing worker as a background task.

    Creates service instances and starts the worker loop.
    This function is called from FastAPI lifespan context.

    Args:
        shutdown_event: Optional event to signal graceful shutdown
    """
    global _worker_instance

    logger.info("Initializing processing worker...")

    # Get Redis and S3 clients
    redis_client = await anext(get_redis_client())
    s3_client = await anext(get_s3_client())

    # Create service instances
    storage_service = StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )

    queue_service = QueueService(redis_client=redis_client)
    job_service = JobService(redis_client=redis_client)

    # Create correction approval service
    correction_approval_service = CorrectionApprovalService(
        redis_client=redis_client,
        job_service=job_service,
        storage_service=storage_service,
    )

    # Create remediation storage service for application service
    remediation_storage_service = RemediationStorageService(storage_service)

    # Create application service
    application_service = ApplicationService(
        remediation_storage=remediation_storage_service,
        storage=storage_service,
        job_service=job_service,
    )

    # Create and start worker
    _worker_instance = ProcessingWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        redis_client=redis_client,
        correction_approval_service=correction_approval_service,
        application_service=application_service,
    )

    await _worker_instance.start(shutdown_event)


def stop_processing_worker() -> None:
    """Stop the processing worker gracefully."""
    global _worker_instance

    if _worker_instance:
        _worker_instance.stop()
