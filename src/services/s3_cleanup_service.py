"""S3 cleanup service for best-effort file deletion operations."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError
from fastapi import HTTPException

from ..config import settings

logger = logging.getLogger(__name__)


class S3CleanupService:
    """Service for non-critical S3 cleanup operations.

    This service handles best-effort deletion operations without circuit breakers.
    Cleanup failures are logged but do not block critical workflows.
    """

    def __init__(self, s3_client: Any, temp_bucket: str):
        """Initialize cleanup service with S3 client and temp bucket.

        Args:
            s3_client: Boto3 S3 client instance
            temp_bucket: Name of temporary storage bucket
        """
        self.s3_client = s3_client
        self.temp_bucket = temp_bucket

    async def delete_temp_file(self, s3_key: str) -> bool:
        """Delete temporary file from temp bucket.

        This is a best-effort operation - errors are logged but not raised.
        Cleanup failures should not block the main workflow.

        Args:
            s3_key: S3 key of file to delete

        Returns:
            bool: True if deletion succeeded, False on error
        """
        try:
            self.s3_client.delete_object(Bucket=self.temp_bucket, Key=s3_key)
            logger.debug(f"Successfully deleted temp file: {s3_key}")
            return True
        except ClientError as e:
            # Log error but don't fail - cleanup is best effort
            logger.warning(f"Failed to delete temp file {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error deleting temp file {s3_key}: {str(e)}")
            return False

    async def cleanup_temp_files_for_job(self, job_id: str) -> int:
        """Delete all temporary files associated with a specific job.

        This method finds and deletes all S3 objects with the prefix temp/{job_id}/
        or the specific temp/{job_id}.pdf file. Useful for cleaning up after job
        completion, failure, or timeout.

        Args:
            job_id: Job identifier (UUID)

        Returns:
            int: Number of files successfully deleted

        Raises:
            HTTPException: If S3 list or delete operations fail

        Example:
            >>> cleanup = S3CleanupService(s3_client, "temp-bucket")
            >>> deleted = await cleanup.cleanup_temp_files_for_job("abc-123")
            >>> print(f"Deleted {deleted} files")
        """
        try:
            deleted_count = 0

            # List all objects with job_id prefix (handles both temp/{job_id}.pdf and temp/{job_id}/*)
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.temp_bucket, Prefix=f"temp/{job_id}")

            # Collect all object keys to delete
            objects_to_delete = []
            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        objects_to_delete.append({"Key": obj["Key"]})

            # Batch delete objects (S3 allows up to 1000 per request)
            if objects_to_delete:
                # Delete in batches of 1000
                batch_size = 1000
                for i in range(0, len(objects_to_delete), batch_size):
                    batch = objects_to_delete[i : i + batch_size]
                    response = self.s3_client.delete_objects(Bucket=self.temp_bucket, Delete={"Objects": batch})
                    deleted_count += len(response.get("Deleted", []))

                    # Log any errors
                    if "Errors" in response and response["Errors"]:
                        for error in response["Errors"]:
                            logger.error(f"Failed to delete {error['Key']}: {error['Message']}")

                logger.info(f"Cleaned up {deleted_count} temp files for job {job_id}")

            return deleted_count

        except ClientError as e:
            logger.error(f"S3 ClientError cleaning up job {job_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to cleanup temp files: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error cleaning up job {job_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Unexpected error during cleanup: {str(e)}")

    async def list_temp_files(self, older_than_hours: int = 24) -> list[dict[str, Any]]:
        """List temporary files older than specified hours.

        Scans the temp bucket and returns metadata for files that are older
        than the retention period. Used by timeout worker for periodic cleanup.

        Args:
            older_than_hours: Age threshold in hours (default: 24)

        Returns:
            List of dicts with keys:
                - 'key': S3 object key
                - 'size': File size in bytes
                - 'last_modified': datetime object (timezone-aware)
                - 'age_hours': Age in hours (float)

        Raises:
            HTTPException: If S3 list operation fails

        Example:
            >>> cleanup = S3CleanupService(s3_client, "temp-bucket")
            >>> old_files = await cleanup.list_temp_files(older_than_hours=48)
            >>> for file in old_files:
            ...     print(f"{file['key']} is {file['age_hours']:.1f} hours old")
        """
        try:
            cutoff_time = datetime.now(UTC) - timedelta(hours=older_than_hours)
            old_files = []

            # Paginate through temp bucket
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.temp_bucket, Prefix="temp/")

            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    # Ensure LastModified is timezone-aware
                    last_modified = obj["LastModified"]
                    if last_modified.tzinfo is None:
                        last_modified = last_modified.replace(tzinfo=UTC)

                    # Check if file is older than cutoff
                    if last_modified < cutoff_time:
                        age_hours = (datetime.now(UTC) - last_modified).total_seconds() / 3600
                        old_files.append(
                            {
                                "key": obj["Key"],
                                "size": obj["Size"],
                                "last_modified": last_modified,
                                "age_hours": age_hours,
                            }
                        )

            logger.info(f"Found {len(old_files)} temp files older than {older_than_hours} hours")
            return old_files

        except ClientError as e:
            logger.error(f"S3 ClientError listing temp files: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to list temp files: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error listing temp files: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Unexpected error listing temp files: {str(e)}")

    async def delete_from_s3(self, bucket: str, key: str) -> bool:
        """Delete a specific object from S3.

        Generic deletion method for any bucket/key combination. Idempotent -
        succeeds even if object doesn't exist.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            bool: True if object was deleted or didn't exist, False on error

        Example:
            >>> cleanup = S3CleanupService(s3_client, "temp-bucket")
            >>> success = await cleanup.delete_from_s3("temp-bucket", "temp/abc-123.pdf")
        """
        try:
            self.s3_client.delete_object(Bucket=bucket, Key=key)
            logger.debug(f"Deleted s3://{bucket}/{key}")
            return True

        except ClientError as e:
            # Check if object doesn't exist (idempotent success)
            if e.response["Error"]["Code"] == "NoSuchKey":
                logger.debug(f"Object already deleted: s3://{bucket}/{key}")
                return True

            logger.error(f"Failed to delete s3://{bucket}/{key}: {str(e)}", exc_info=True)
            return False

        except Exception as e:
            logger.error(f"Unexpected error deleting s3://{bucket}/{key}: {str(e)}", exc_info=True)
            return False

    async def cleanup_expired_temp_files(self, older_than_hours: int | None = None) -> dict[str, int]:
        """Clean up expired temporary files from S3.

        Lists temporary files older than the retention period and deletes them.
        Returns statistics about the cleanup operation.

        Args:
            older_than_hours: Age threshold in hours (default: from settings)

        Returns:
            Dict with cleanup statistics:
                - files_deleted: Number of files successfully deleted
                - bytes_freed: Total bytes freed
                - errors: Number of deletion errors encountered

        Example:
            >>> cleanup = S3CleanupService(s3_client, "temp-bucket")
            >>> stats = await cleanup.cleanup_expired_temp_files()
            >>> print(f"Deleted {stats['files_deleted']} files")
        """
        if older_than_hours is None:
            older_than_hours = settings.temp_file_retention_hours

        files_deleted = 0
        bytes_freed = 0
        errors = 0

        try:
            # Get list of old temp files
            old_files = await self.list_temp_files(older_than_hours=older_than_hours)

            # Delete each file
            for file_info in old_files:
                s3_key = file_info["key"]
                file_size = file_info["size"]

                success = await self.delete_from_s3(self.temp_bucket, s3_key)

                if success:
                    files_deleted += 1
                    bytes_freed += file_size
                else:
                    errors += 1

        except Exception as e:
            logger.error(f"Error during temp file cleanup: {str(e)}", exc_info=True)
            errors += 1

        return {"files_deleted": files_deleted, "bytes_freed": bytes_freed, "errors": errors}

    async def list_debug_artifacts(self, older_than_hours: int = 24) -> list[dict[str, Any]]:
        """List debug artifacts older than specified hours.

        Scans the temp bucket debug/ prefix and returns metadata for files that
        are older than the retention period. Used by timeout worker for periodic cleanup.

        Args:
            older_than_hours: Age threshold in hours (default: 24)

        Returns:
            List of dicts with keys:
                - 'key': S3 object key
                - 'size': File size in bytes
                - 'last_modified': datetime object (timezone-aware)
                - 'age_hours': Age in hours (float)
        """
        try:
            cutoff_time = datetime.now(UTC) - timedelta(hours=older_than_hours)
            old_files = []

            # Paginate through debug artifacts in temp bucket
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.temp_bucket, Prefix="debug/")

            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    # Ensure LastModified is timezone-aware
                    last_modified = obj["LastModified"]
                    if last_modified.tzinfo is None:
                        last_modified = last_modified.replace(tzinfo=UTC)

                    # Check if file is older than cutoff
                    if last_modified < cutoff_time:
                        age_hours = (datetime.now(UTC) - last_modified).total_seconds() / 3600
                        old_files.append(
                            {
                                "key": obj["Key"],
                                "size": obj["Size"],
                                "last_modified": last_modified,
                                "age_hours": age_hours,
                            }
                        )

            logger.info(f"Found {len(old_files)} debug artifacts older than {older_than_hours} hours")
            return old_files

        except ClientError as e:
            logger.error(f"S3 ClientError listing debug artifacts: {str(e)}", exc_info=True)
            # Return empty list instead of raising - cleanup is best effort
            return []
        except Exception as e:
            logger.error(f"Unexpected error listing debug artifacts: {str(e)}", exc_info=True)
            return []

    async def cleanup_expired_debug_artifacts(self, older_than_hours: int | None = None) -> dict[str, int]:
        """Clean up expired debug artifacts from S3.

        Lists debug artifacts (under debug/ prefix) older than the retention period
        and deletes them. Returns statistics about the cleanup operation.

        Args:
            older_than_hours: Age threshold in hours (default: from settings)

        Returns:
            Dict with cleanup statistics:
                - files_deleted: Number of files successfully deleted
                - bytes_freed: Total bytes freed
                - errors: Number of deletion errors encountered

        Example:
            >>> cleanup = S3CleanupService(s3_client, "temp-bucket")
            >>> stats = await cleanup.cleanup_expired_debug_artifacts()
            >>> print(f"Deleted {stats['files_deleted']} debug artifacts")
        """
        if older_than_hours is None:
            older_than_hours = settings.debug_artifact_retention_hours

        files_deleted = 0
        bytes_freed = 0
        errors = 0

        try:
            # Get list of old debug artifacts
            old_files = await self.list_debug_artifacts(older_than_hours=older_than_hours)

            # Delete each file
            for file_info in old_files:
                s3_key = file_info["key"]
                file_size = file_info["size"]

                success = await self.delete_from_s3(self.temp_bucket, s3_key)

                if success:
                    files_deleted += 1
                    bytes_freed += file_size
                else:
                    errors += 1

        except Exception as e:
            logger.error(f"Error during debug artifact cleanup: {str(e)}", exc_info=True)
            errors += 1

        return {"files_deleted": files_deleted, "bytes_freed": bytes_freed, "errors": errors}
