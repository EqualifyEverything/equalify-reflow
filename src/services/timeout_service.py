"""Service for monitoring and processing approval timeouts.

This service detects jobs that have exceeded the approval deadline
and performs cleanup operations (status updates, temp file removal).
"""

import logging

from .cleanup_service import CleanupService
from .job_service import JobService
from .metrics_service import MetricsService
from .queue_service import QueueService

logger = logging.getLogger(__name__)


class TimeoutService:
    """Service for approval timeout monitoring and processing."""

    def __init__(
        self,
        queue_service: QueueService,
        job_service: JobService,
        cleanup_service: CleanupService,
        metrics_service: MetricsService,
    ):
        """Initialize timeout service.

        Args:
            queue_service: Redis queue operations service
            job_service: Job status management service
            cleanup_service: S3 cleanup service
            metrics_service: Metrics tracking service
        """
        self.queue_service = queue_service
        self.job_service = job_service
        self.cleanup_service = cleanup_service
        self.metrics_service = metrics_service

    async def process_expired_approvals(self) -> int:
        """Process all jobs that have exceeded the approval deadline.

        Returns:
            Number of expired approvals processed
        """
        try:
            # Query Redis sorted set for expired timeouts
            # Returns [(job_id, timestamp), ...] tuples
            expired_timeouts = await self.queue_service.get_expired_timeouts()

            if not expired_timeouts:
                logger.debug("No expired approvals found")
                return 0

            logger.info(f"Found {len(expired_timeouts)} expired approvals to process")

            # Process each expired job
            processed_count = 0
            for job_id, expiration_timestamp in expired_timeouts:
                try:
                    success = await self._process_expired_job(job_id)
                    if success:
                        processed_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to process expired approval for job {job_id}: {e}",
                        exc_info=True
                    )
                    # Continue processing other jobs

            # Update metrics
            if processed_count > 0:
                await self.metrics_service.increment_metric(
                    "approval_timeouts_processed",
                    processed_count
                )

            logger.info(
                f"Processed {processed_count}/{len(expired_timeouts)} expired approvals"
            )
            return processed_count

        except Exception as e:
            logger.error(f"Error processing expired approvals: {e}", exc_info=True)
            await self.metrics_service.increment_metric("approval_timeout_errors", 1)
            raise

    async def _process_expired_job(self, job_id: str) -> bool:
        """Process a single expired approval job.

        Args:
            job_id: Job ID to process

        Returns:
            True if successfully processed, False otherwise
        """
        try:
            # Check current job status (idempotency - might already be processed)
            job_data = await self.job_service.get_job_status(job_id)

            if not job_data:
                logger.warning(
                    f"Job {job_id} not found in Redis, removing from timeout tracking"
                )
                await self.queue_service.remove_from_timeout_tracking(job_id)
                return False

            current_status = job_data.get("status")

            # Only process jobs still awaiting approval
            if current_status != "awaiting_approval":
                logger.debug(
                    f"Job {job_id} status is {current_status}, "
                    f"skipping timeout processing"
                )
                await self.queue_service.remove_from_timeout_tracking(job_id)
                return False

            logger.info(f"Processing timeout for job {job_id}")

            # Update job status to failed with timeout reason
            await self.job_service.update_job_status(
                job_id,
                status="failed",
                error_message="Approval deadline exceeded - no response received"
            )

            # Cleanup temp files from S3
            cleanup_success = await self.cleanup_service.cleanup_denied_job(job_id)

            if not cleanup_success:
                logger.warning(
                    f"Cleanup failed for timed-out job {job_id}, "
                    f"but status updated to failed"
                )

            # Remove from timeout tracking sorted set
            await self.queue_service.remove_from_timeout_tracking(job_id)

            logger.info(f"Successfully processed timeout for job {job_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to process expired job {job_id}: {e}", exc_info=True)
            return False

    async def get_pending_approval_count(self) -> int:
        """Get count of jobs currently awaiting approval.

        Returns:
            Number of jobs in timeout tracking
        """
        try:
            count = await self.queue_service.get_timeout_count()
            logger.debug(f"Pending approval count: {count}")
            return count
        except Exception as e:
            logger.error(f"Failed to get pending approval count: {e}", exc_info=True)
            return 0
