"""PII detection background worker."""

import asyncio
import logging
import time

from opentelemetry import trace

from ..config import settings
from ..services.job_service import JobService
from ..services.metrics_service import (
    worker_active_gauge,
    worker_errors_total,
    worker_jobs_processed_total,
)
from ..services.pii_service import PIIDetectionService
from ..services.queue_service import QueueService
from ..services.s3_url_service import S3URLService
from ..services.storage_service import StorageService
from ..shared.constants.queues import PII_QUEUE
from ..shared.models.queue import PIIQueuePayload

logger = logging.getLogger(__name__)


class PIIWorker:
    """Background worker for PII detection queue.

    Continuously polls the PII queue and processes jobs using
    PIIDetectionService.
    """

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService,
        s3_url_service: S3URLService,
    ):
        """Initialize PII worker.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            s3_url_service: S3 URL generation service (for processing trigger)
        """
        self.pii_service = PIIDetectionService(
            storage_service=storage_service,
            queue_service=queue_service,
            job_service=job_service,
            s3_url_service=s3_url_service,
        )
        self.queue = queue_service
        self.running = False

    async def start(self, shutdown_event: asyncio.Event | None = None) -> None:
        """Start the PII worker main loop.

        Continuously polls PII queue and processes jobs.
        Runs until stopped via stop() method or shutdown_event is set.

        Args:
            shutdown_event: Optional event to signal graceful shutdown
        """
        self.running = True
        logger.info("PII worker started")

        tracer = trace.get_tracer("equalify.workers")

        # Mark worker as active in Prometheus
        worker_active_gauge.labels(worker_name="pii").set(1)

        try:
            while self.running and (shutdown_event is None or not shutdown_event.is_set()):
                try:
                    # Blocking pop from PII queue with timeout
                    job_data = await self.queue.dequeue(PII_QUEUE, timeout=settings.pii_worker_queue_timeout_seconds)

                    if job_data:
                        # Parse payload (dequeue returns dict)
                        job = PIIQueuePayload.model_validate(job_data)

                        # Check shutdown before processing
                        if shutdown_event and shutdown_event.is_set():
                            logger.info("Shutdown requested, requeueing job and stopping")
                            # Requeue job for next worker
                            await self.queue.enqueue(PII_QUEUE, job)
                            break
                        logger.info(f"Received PII job: {job.job_id}")

                        # Process job with OTel span
                        job_start_time = time.time()
                        with tracer.start_as_current_span(
                            "worker.pii.job",
                            kind=trace.SpanKind.CONSUMER,
                        ) as span:
                            span.set_attribute("job_id", job.job_id)
                            span.set_attribute("worker.type", "pii")
                            span.set_attribute("s3_key", job.s3_key)
                            span.set_attribute("queue", PII_QUEUE)

                            await self.pii_service.process_pii_job(job)

                            job_duration_ms = (time.time() - job_start_time) * 1000
                            span.set_attribute("duration_ms", job_duration_ms)

                        # Track successful processing
                        worker_jobs_processed_total.labels(
                            worker_name="pii", result="success"
                        ).inc()

                    else:
                        # Queue empty, continue polling
                        logger.debug("PII queue empty, continuing to poll")

                except Exception as e:
                    logger.error(f"PII worker error: {e}", exc_info=True)

                    # Track error
                    worker_errors_total.labels(
                        worker_name="pii", error_type=type(e).__name__
                    ).inc()
                    worker_jobs_processed_total.labels(
                        worker_name="pii", result="error"
                    ).inc()
                    # Brief pause before retry to avoid tight error loop
                    await asyncio.sleep(settings.worker_error_sleep_seconds)

        finally:
            # Mark worker as inactive when shutting down
            worker_active_gauge.labels(worker_name="pii").set(0)

            logger.info("PII worker shutting down gracefully")

    def stop(self) -> None:
        """Stop the PII worker gracefully."""
        logger.info("Stopping PII worker...")
        self.running = False


# Global worker instance
_worker_instance: PIIWorker | None = None


async def start_pii_worker(shutdown_event: asyncio.Event | None = None) -> None:
    """Start the PII worker as a background task.

    Creates service instances and starts the worker loop.
    This function is called from FastAPI lifespan context.

    Args:
        shutdown_event: Optional event to signal graceful shutdown
    """
    global _worker_instance

    logger.info("Initializing PII worker...")

    # Eagerly initialize Presidio analyzer to avoid cold start on first request
    # This loads the spaCy model and Presidio recognizers during startup
    from ..services.pii_analyzer import get_pii_analyzer
    logger.info("Pre-loading Presidio PII analyzer (spaCy model + recognizers)...")
    get_pii_analyzer()
    logger.info("Presidio PII analyzer pre-loaded successfully")

    # Import dependencies dynamically to avoid circular imports
    from ..config import settings
    from ..dependencies import get_redis_client, get_s3_client

    # Get Redis and S3 clients using proper async generator pattern
    redis_client = await anext(get_redis_client())
    s3_client = await anext(get_s3_client())

    # Create service instances directly with clients
    storage_service = StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )

    queue_service = QueueService(redis_client=redis_client)
    job_service = JobService(redis_client=redis_client)

    # Create S3URLService for processing trigger
    s3_url_service = S3URLService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )

    # Create worker
    _worker_instance = PIIWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        s3_url_service=s3_url_service,
    )

    logger.info("PII worker services initialized, starting worker loop...")

    # Start worker (runs until stopped)
    await _worker_instance.start(shutdown_event)


def get_pii_worker() -> PIIWorker | None:
    """Get the global PII worker instance.

    Returns:
        Optional[PIIWorker]: Worker instance if started, None otherwise
    """
    return _worker_instance
