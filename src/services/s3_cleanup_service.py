"""Service for S3 temporary file cleanup operations.

This service handles periodic cleanup of old temporary files
from the S3 temp bucket based on retention policies.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from .storage_service import StorageService
from .metrics_service import MetricsService
from ..config import settings

logger = logging.getLogger(__name__)


class S3CleanupService:
    """Service for S3 temporary file cleanup operations."""

    def __init__(
        self,
        storage_service: StorageService,
        metrics_service: MetricsService,
    ):
        """Initialize S3 cleanup service.

        Args:
            storage_service: S3 storage operations service
            metrics_service: Metrics tracking service
        """
        self.storage_service = storage_service
        self.metrics_service = metrics_service

    async def cleanup_expired_temp_files(self) -> Dict[str, Any]:
        """Clean up temporary files older than retention period.

        Returns:
            Dict with cleanup results (files_deleted, bytes_freed, errors)
        """
        try:
            logger.info(
                f"Starting temp file cleanup (retention: "
                f"{settings.temp_file_retention_hours}h)"
            )

            # List files older than retention period (pass hours, not datetime)
            old_files = await self.storage_service.list_temp_files(
                older_than_hours=settings.temp_file_retention_hours
            )

            if not old_files:
                logger.debug("No expired temp files found")
                return {
                    "files_deleted": 0,
                    "bytes_freed": 0,
                    "errors": 0
                }

            logger.info(f"Found {len(old_files)} expired temp files to delete")

            # Delete files in batches
            files_deleted = 0
            bytes_freed = 0
            errors = 0

            for file_info in old_files:
                try:
                    s3_key = file_info["Key"]
                    file_size = file_info.get("Size", 0)

                    # Delete file from S3
                    success = await self.storage_service.delete_from_s3(
                        bucket=settings.s3_temp_bucket,
                        key=s3_key
                    )

                    if success:
                        files_deleted += 1
                        bytes_freed += file_size
                        logger.debug(f"Deleted temp file: {s3_key} ({file_size} bytes)")
                    else:
                        errors += 1
                        logger.warning(f"Failed to delete temp file: {s3_key}")

                except Exception as e:
                    errors += 1
                    logger.error(
                        f"Error deleting temp file {file_info.get('Key', 'unknown')}: {e}",
                        exc_info=True
                    )

            # Update metrics
            if files_deleted > 0:
                await self.metrics_service.increment_metric(
                    "temp_files_deleted",
                    files_deleted
                )
                await self.metrics_service.increment_metric(
                    "temp_bytes_freed",
                    bytes_freed
                )

            if errors > 0:
                await self.metrics_service.increment_metric(
                    "temp_cleanup_errors",
                    errors
                )

            logger.info(
                f"Temp file cleanup complete: {files_deleted} deleted, "
                f"{bytes_freed} bytes freed, {errors} errors"
            )

            return {
                "files_deleted": files_deleted,
                "bytes_freed": bytes_freed,
                "errors": errors
            }

        except Exception as e:
            logger.error(f"Error during temp file cleanup: {e}", exc_info=True)
            await self.metrics_service.increment_metric("temp_cleanup_errors", 1)
            raise

    async def cleanup_job_temp_files(self, job_id: str) -> bool:
        """Clean up all temporary files for a specific job.

        Args:
            job_id: Job ID to clean up

        Returns:
            True if cleanup successful, False otherwise
        """
        try:
            logger.debug(f"Cleaning up temp files for job {job_id}")

            # Use storage service batch cleanup method
            deleted_count = await self.storage_service.cleanup_temp_files_for_job(
                job_id
            )

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} temp files for job {job_id}")
                await self.metrics_service.increment_metric(
                    "job_temp_files_deleted",
                    deleted_count
                )
            else:
                logger.debug(f"No temp files found for job {job_id}")

            return True

        except Exception as e:
            logger.error(
                f"Failed to cleanup temp files for job {job_id}: {e}",
                exc_info=True
            )
            return False
