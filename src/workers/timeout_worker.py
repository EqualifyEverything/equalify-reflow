"""Timeout and cleanup worker for background maintenance tasks.

This worker runs scheduled maintenance tasks:
- Approval timeout monitoring (every 30 seconds)
- S3 temp file cleanup (every hour)
- Orphaned job cleanup (every 4 hours)
- Metrics cleanup (daily)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from ..config import settings
from ..services.cleanup_service import CleanupService
from ..services.metrics_service import (
    MetricsService,
    worker_active_gauge,
    worker_errors_total,
)
from ..services.timeout_service import TimeoutService
from ..services.s3_cleanup_service import S3CleanupService
from ..services.orphan_service import OrphanService

logger = logging.getLogger(__name__)


class TimeoutWorker:
    """Background worker for timeout and cleanup operations."""

    def __init__(
        self,
        storage_service,
        queue_service,
        job_service,
        metrics_service
    ):
        """Initialize timeout worker with service dependencies.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            metrics_service: Metrics tracking
        """
        # Core services
        self.storage_service = storage_service
        self.queue_service = queue_service
        self.job_service = job_service
        self.metrics_service = metrics_service

        # Derived services
        self.cleanup_service = CleanupService(storage_service)

        self.timeout_service = TimeoutService(
            queue_service,
            job_service,
            self.cleanup_service,
            metrics_service
        )

        self.s3_cleanup_service = S3CleanupService(
            storage_service,
            metrics_service
        )

        self.orphan_service = OrphanService(
            job_service,
            self.s3_cleanup_service,
            metrics_service
        )

        # Task scheduling state
        self.last_approval_check = None
        self.last_temp_cleanup = None
        self.last_orphan_cleanup = None
        self.last_metrics_cleanup = None

        # Worker state
        self.running = False

    async def start(self, shutdown_event: asyncio.Event = None):
        """Main worker loop with scheduled task execution.

        Args:
            shutdown_event: Optional event to signal graceful shutdown
        """
        self.running = True
        logger.info("Timeout worker started")

        # Mark worker as active in Prometheus
        worker_active_gauge.labels(worker_name="timeout").set(1)

        try:
            while self.running and (shutdown_event is None or not shutdown_event.is_set()):
                try:
                    current_time = datetime.now(timezone.utc)

                    # Task 1: Approval timeout monitoring (every 30 seconds)
                    if self._should_run_task(
                        self.last_approval_check,
                        settings.approval_check_interval_seconds
                    ):
                        await self._run_approval_check()
                        self.last_approval_check = current_time

                    # Task 2: S3 temp file cleanup (every hour)
                    if self._should_run_task(
                        self.last_temp_cleanup,
                        settings.temp_cleanup_interval_hours * 3600
                    ):
                        await self._run_temp_cleanup()
                        self.last_temp_cleanup = current_time

                    # Task 3: Orphaned job cleanup (every 4 hours)
                    if self._should_run_task(
                        self.last_orphan_cleanup,
                        settings.orphan_cleanup_interval_hours * 3600
                    ):
                        await self._run_orphan_cleanup()
                        self.last_orphan_cleanup = current_time

                    # Task 4: Metrics cleanup (daily)
                    if self._should_run_task(
                        self.last_metrics_cleanup,
                        settings.metrics_cleanup_interval_hours * 3600
                    ):
                        await self._run_metrics_cleanup()
                        self.last_metrics_cleanup = current_time

                    # Sleep before next iteration (check every 10 seconds)
                    await asyncio.sleep(10)

                except Exception as e:
                    logger.error(f"Error in timeout worker loop: {e}", exc_info=True)
                    # Track error
                    worker_errors_total.labels(
                        worker_name="timeout", error_type=type(e).__name__
                    ).inc()
                    # Sleep longer on error to avoid rapid error loops
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("Timeout worker received cancellation signal")
            raise

        finally:
            # Mark worker as inactive when shutting down
            worker_active_gauge.labels(worker_name="timeout").set(0)
            logger.info("Timeout worker shutting down gracefully")

    def _should_run_task(
        self,
        last_run: datetime | None,
        interval_seconds: int
    ) -> bool:
        """Check if enough time has elapsed to run a scheduled task.

        Args:
            last_run: Datetime of last task execution (None if never run)
            interval_seconds: Minimum seconds between task runs

        Returns:
            True if task should run
        """
        if last_run is None:
            return True

        current_time = datetime.now(timezone.utc)
        elapsed = (current_time - last_run).total_seconds()

        return elapsed >= interval_seconds

    async def _run_approval_check(self):
        """Run approval timeout monitoring task."""
        try:
            logger.debug("Running approval timeout check...")
            processed_count = await self.timeout_service.process_expired_approvals()

            if processed_count > 0:
                logger.info(f"Processed {processed_count} expired approvals")

        except Exception as e:
            logger.error(f"Error in approval timeout check: {e}", exc_info=True)
            await self.metrics_service.increment_metric("worker_task_errors", 1)

    async def _run_temp_cleanup(self):
        """Run S3 temp file cleanup task."""
        try:
            logger.info("Running temp file cleanup...")
            result = await self.s3_cleanup_service.cleanup_expired_temp_files()

            logger.info(
                f"Temp cleanup complete: {result['files_deleted']} files deleted, "
                f"{result['bytes_freed']} bytes freed, {result['errors']} errors"
            )

        except Exception as e:
            logger.error(f"Error in temp file cleanup: {e}", exc_info=True)
            await self.metrics_service.increment_metric("worker_task_errors", 1)

    async def _run_orphan_cleanup(self):
        """Run orphaned job cleanup tasks."""
        try:
            logger.info("Running orphan cleanup...")

            # Task 1: Cleanup old completed/failed jobs
            old_jobs_result = await self.orphan_service.cleanup_old_completed_jobs()
            logger.info(
                f"Old jobs cleanup: {old_jobs_result['jobs_cleaned']} cleaned, "
                f"{old_jobs_result['errors']} errors"
            )

            # Task 2: Detect and fail stuck processing jobs
            stuck_jobs_result = await self.orphan_service.cleanup_stuck_processing_jobs()
            logger.info(
                f"Stuck jobs detection: {stuck_jobs_result['jobs_failed']} failed, "
                f"{stuck_jobs_result['errors']} errors"
            )

        except Exception as e:
            logger.error(f"Error in orphan cleanup: {e}", exc_info=True)
            await self.metrics_service.increment_metric("worker_task_errors", 1)

    async def _run_metrics_cleanup(self):
        """Run metrics cleanup task."""
        try:
            logger.info("Running metrics cleanup...")

            # Cleanup old metrics
            deleted_count = await self.metrics_service.cleanup_old_metrics()
            logger.info(f"Metrics cleanup complete: {deleted_count} keys deleted")

            # Log daily summary (useful for monitoring)
            await self.metrics_service.log_daily_summary()

        except Exception as e:
            logger.error(f"Error in metrics cleanup: {e}", exc_info=True)
            await self.metrics_service.increment_metric("worker_task_errors", 1)

    def stop(self):
        """Signal worker to stop gracefully."""
        logger.info("Stopping timeout worker...")
        self.running = False


# Global worker instance
_worker_instance = None


async def start_timeout_worker(shutdown_event: asyncio.Event = None):
    """Entry point for timeout worker (called from main.py lifespan).

    Creates service instances and starts the worker loop.
    This function is called from FastAPI lifespan context.

    Args:
        shutdown_event: Optional event to signal graceful shutdown
    """
    global _worker_instance

    logger.info("Initializing timeout worker...")

    # Import dependencies dynamically to avoid circular imports
    from ..dependencies import (
        get_storage_service,
        get_queue_service,
        get_job_service
    )
    import redis.asyncio as redis

    # Create Redis client for metrics service (not provided by dependencies)
    redis_client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    )

    # Create service instances using dependency injection pattern
    storage_service = await get_storage_service(s3_client=None)
    queue_service = await get_queue_service(redis_client=None)
    job_service = await get_job_service(redis_client=None)

    # Metrics service uses the same Redis client
    from ..services.metrics_service import MetricsService
    metrics_service = MetricsService(redis_client)

    # Create worker
    _worker_instance = TimeoutWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        metrics_service=metrics_service
    )

    logger.info("Timeout worker services initialized, starting worker loop...")

    # Start worker (runs until stopped)
    await _worker_instance.start(shutdown_event)


def get_timeout_worker():
    """Get the global timeout worker instance.

    Returns:
        Optional[TimeoutWorker]: Worker instance if started, None otherwise
    """
    return _worker_instance
