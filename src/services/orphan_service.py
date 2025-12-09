"""Service for detecting and cleaning up orphaned jobs and data.

This service identifies jobs that have been stuck in processing
or are old completed/failed jobs that should be cleaned up.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import settings
from .job_service import JobService
from .metrics_service import MetricsService
from .s3_cleanup_service import S3CleanupService

logger = logging.getLogger(__name__)


class OrphanService:
    """Service for orphaned job detection and cleanup."""

    def __init__(
        self,
        job_service: JobService,
        s3_cleanup_service: S3CleanupService,
        metrics_service: MetricsService,
    ):
        """Initialize orphan service.

        Args:
            job_service: Job status management service
            s3_cleanup_service: S3 cleanup service
            metrics_service: Metrics tracking service
        """
        self.job_service = job_service
        self.s3_cleanup_service = s3_cleanup_service
        self.metrics_service = metrics_service

    async def cleanup_old_completed_jobs(self) -> dict[str, Any]:
        """Clean up old completed/failed/denied jobs beyond retention period.

        Returns:
            Dict with cleanup results (jobs_cleaned, errors)
        """
        try:
            # Calculate cutoff time based on retention policy
            cutoff_time = datetime.now(UTC) - timedelta(
                days=settings.job_retention_days
            )

            logger.info(
                f"Starting old job cleanup (retention: {settings.job_retention_days} days, "
                f"cutoff: {cutoff_time.isoformat()})"
            )

            # Get all job keys from Redis
            all_jobs = await self.job_service.list_all_jobs()

            if not all_jobs:
                logger.debug("No jobs found in Redis")
                return {"jobs_cleaned": 0, "errors": 0}

            jobs_cleaned = 0
            errors = 0

            # Check each job for cleanup eligibility
            for job_id in all_jobs:
                try:
                    should_clean = await self._should_cleanup_job(job_id, cutoff_time)

                    if should_clean:
                        success = await self._cleanup_job(job_id)
                        if success:
                            jobs_cleaned += 1
                        else:
                            errors += 1

                except Exception as e:
                    errors += 1
                    logger.error(
                        f"Error checking job {job_id} for cleanup: {e}",
                        exc_info=True
                    )

            # Update metrics
            if jobs_cleaned > 0:
                await self.metrics_service.increment_metric(
                    "old_jobs_cleaned",
                    jobs_cleaned
                )

            if errors > 0:
                await self.metrics_service.increment_metric(
                    "orphan_cleanup_errors",
                    errors
                )

            logger.info(
                f"Old job cleanup complete: {jobs_cleaned} cleaned, {errors} errors"
            )

            return {"jobs_cleaned": jobs_cleaned, "errors": errors}

        except Exception as e:
            logger.error(f"Error during old job cleanup: {e}", exc_info=True)
            await self.metrics_service.increment_metric("orphan_cleanup_errors", 1)
            raise

    async def cleanup_stuck_processing_jobs(self) -> dict[str, Any]:
        """Detect and fail jobs stuck in processing state.

        Jobs stuck in 'processing' or 'pii_scanning' status for longer than
        max_processing_hours are marked as failed and cleaned up.

        Returns:
            Dict with cleanup results (jobs_failed, errors)
        """
        try:
            # Calculate cutoff time for stuck jobs
            cutoff_time = datetime.now(UTC) - timedelta(
                hours=settings.max_processing_hours
            )

            logger.info(
                f"Starting stuck job detection (max processing: "
                f"{settings.max_processing_hours}h, cutoff: {cutoff_time.isoformat()})"
            )

            # Get all jobs
            all_jobs = await self.job_service.list_all_jobs()

            if not all_jobs:
                logger.debug("No jobs found in Redis")
                return {"jobs_failed": 0, "errors": 0}

            jobs_failed = 0
            errors = 0

            # Check each job for stuck status
            for job_id in all_jobs:
                try:
                    is_stuck = await self._is_job_stuck(job_id, cutoff_time)

                    if is_stuck:
                        success = await self._fail_stuck_job(job_id)
                        if success:
                            jobs_failed += 1
                        else:
                            errors += 1

                except Exception as e:
                    errors += 1
                    logger.error(
                        f"Error checking job {job_id} for stuck status: {e}",
                        exc_info=True
                    )

            # Update metrics
            if jobs_failed > 0:
                await self.metrics_service.increment_metric(
                    "stuck_jobs_failed",
                    jobs_failed
                )

            if errors > 0:
                await self.metrics_service.increment_metric(
                    "orphan_cleanup_errors",
                    errors
                )

            logger.info(
                f"Stuck job detection complete: {jobs_failed} failed, {errors} errors"
            )

            return {"jobs_failed": jobs_failed, "errors": errors}

        except Exception as e:
            logger.error(f"Error during stuck job detection: {e}", exc_info=True)
            await self.metrics_service.increment_metric("orphan_cleanup_errors", 1)
            raise

    async def _should_cleanup_job(self, job_id: str, cutoff_time: datetime) -> bool:
        """Check if job should be cleaned up based on age and status.

        Args:
            job_id: Job ID to check
            cutoff_time: Datetime before which jobs should be cleaned

        Returns:
            True if job should be cleaned up
        """
        try:
            job_data = await self.job_service.get_job_status(job_id)

            if not job_data:
                logger.warning(f"Job {job_id} not found, will be cleaned")
                return True

            status = job_data.get("status")
            created_at = job_data.get("created_at")

            # Only cleanup terminal states (completed, failed, denied)
            if status not in ["completed", "failed", "denied"]:
                return False

            # Check if job is older than retention period
            if created_at:
                try:
                    # Parse ISO format and ensure timezone awareness
                    created_at_str = created_at.replace("Z", "+00:00")
                    job_created = datetime.fromisoformat(created_at_str)

                    # If somehow still naive, add UTC timezone
                    if job_created.tzinfo is None:
                        job_created = job_created.replace(tzinfo=UTC)

                    if job_created < cutoff_time:
                        logger.debug(
                            f"Job {job_id} is old (created: {created_at}, "
                            f"status: {status}), marking for cleanup"
                        )
                        return True
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Invalid created_at for job {job_id}: {e}")
                    # Conservative: don't cleanup if we can't parse date
                    return False

            return False

        except Exception as e:
            logger.error(f"Error checking cleanup eligibility for job {job_id}: {e}")
            return False

    async def _cleanup_job(self, job_id: str) -> bool:
        """Clean up a single job (delete from Redis and S3).

        Args:
            job_id: Job ID to clean up

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Cleaning up old job {job_id}")

            # Cleanup temp files from S3
            await self.s3_cleanup_service.cleanup_job_temp_files(job_id)

            # Delete job hash from Redis
            await self.job_service.cleanup_old_job(job_id)

            logger.info(f"Successfully cleaned up job {job_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup job {job_id}: {e}", exc_info=True)
            return False

    async def _is_job_stuck(self, job_id: str, cutoff_time: datetime) -> bool:
        """Check if job is stuck in processing state.

        Args:
            job_id: Job ID to check
            cutoff_time: Datetime before which jobs are considered stuck

        Returns:
            True if job is stuck
        """
        try:
            job_data = await self.job_service.get_job_status(job_id)

            if not job_data:
                return False

            status = job_data.get("status")
            created_at = job_data.get("created_at")

            # Check if in processing state
            if status not in ["processing", "pii_scanning"]:
                return False

            # Check if older than max processing time
            if created_at:
                try:
                    # Parse ISO format and ensure timezone awareness
                    created_at_str = created_at.replace("Z", "+00:00")
                    job_created = datetime.fromisoformat(created_at_str)

                    # If somehow still naive, add UTC timezone
                    if job_created.tzinfo is None:
                        job_created = job_created.replace(tzinfo=UTC)

                    if job_created < cutoff_time:
                        logger.warning(
                            f"Job {job_id} stuck in {status} "
                            f"(created: {created_at})"
                        )
                        return True
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Invalid created_at for job {job_id}: {e}")

            return False

        except Exception as e:
            logger.error(f"Error checking stuck status for job {job_id}: {e}")
            return False

    async def _fail_stuck_job(self, job_id: str) -> bool:
        """Mark a stuck job as failed and clean up resources.

        Args:
            job_id: Job ID to fail

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Failing stuck job {job_id}")

            # Update job status to failed
            await self.job_service.update_job_status(
                job_id,
                status="failed",
                error_message="Job exceeded maximum processing time and was terminated"
            )

            # Cleanup temp files
            await self.s3_cleanup_service.cleanup_job_temp_files(job_id)

            logger.info(f"Successfully failed stuck job {job_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to fail stuck job {job_id}: {e}", exc_info=True)
            return False
