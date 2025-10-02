"""Cleanup service for denied job file removal.

Handles S3 temp file cleanup when jobs are denied during approval workflow.
Implements idempotent cleanup with graceful error handling.
"""

import logging
from typing import Any

from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)


class CleanupService:
    """Service for cleaning up files from denied jobs."""

    def __init__(self, s3_client: Any):
        """Initialize cleanup service with S3 client.

        Args:
            s3_client: Boto3 S3 async client instance
        """
        self.s3 = s3_client
        self.temp_bucket = settings.s3_temp_bucket

    async def cleanup_job_files(self, s3_key: str) -> bool:
        """Delete temporary PDF file from S3.

        Idempotent operation - succeeds even if file doesn't exist.
        Logs errors but doesn't raise exceptions to avoid blocking
        the approval workflow.

        Args:
            s3_key: S3 object key to delete

        Returns:
            bool: True if cleanup successful, False if error occurred

        Example:
            >>> cleanup = CleanupService(s3_client)
            >>> success = await cleanup.cleanup_job_files("temp/abc-123.pdf")
            >>> # Returns True even if file already deleted
        """
        try:
            await self.s3.delete_object(
                Bucket=self.temp_bucket,
                Key=s3_key
            )
            logger.info(f"Successfully cleaned up S3 file: {s3_key}")
            return True

        except ClientError as e:
            error_code = e.response['Error']['Code']

            # File doesn't exist - idempotent success
            if error_code in ('NoSuchKey', '404'):
                logger.info(f"S3 file already deleted (idempotent): {s3_key}")
                return True

            # Other S3 errors - log and return False
            logger.error(
                f"S3 ClientError cleaning up {s3_key}: {error_code} - {str(e)}",
                exc_info=True
            )
            return False

        except Exception as e:
            # Log error but don't fail the approval workflow
            # Orphaned files can be cleaned up by timeout worker
            logger.error(
                f"Failed to cleanup S3 file {s3_key}: {str(e)}",
                exc_info=True
            )
            return False
