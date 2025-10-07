"""Storage service for S3 operations."""

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Optional
from io import BytesIO

from fastapi import HTTPException, UploadFile
from botocore.exceptions import ClientError

from ..config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """Service for managing document storage in S3."""

    def __init__(self, s3_client, temp_bucket: str, results_bucket: str):
        """Initialize storage service with S3 client and bucket names.

        Args:
            s3_client: Boto3 S3 client instance
            temp_bucket: Name of temporary storage bucket
            results_bucket: Name of results storage bucket
        """
        self.s3_client = s3_client
        self.temp_bucket = temp_bucket
        self.results_bucket = results_bucket

    async def store_document(self, file: UploadFile) -> tuple[str, str]:
        """
        Store uploaded document in S3 temp bucket.

        Args:
            file: Uploaded PDF file

        Returns:
            Tuple of (job_id, s3_key)

        Raises:
            HTTPException: If file validation fails or upload fails
        """
        # Validate PDF format
        if file.content_type != "application/pdf":
            raise HTTPException(
                status_code=400,
                detail="Only PDF files are accepted"
            )

        # Validate file size
        try:
            file.file.seek(0, 2)  # Seek to end
            file_size = file.file.tell()
            file.file.seek(0)  # Reset to beginning
        except (OSError, IOError, AttributeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Unable to read file: {str(e)}"
            )

        # Check minimum file size (empty or corrupt files)
        MIN_FILE_SIZE = 100  # 100 bytes minimum
        if file_size < MIN_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File too small. Minimum file size is {MIN_FILE_SIZE} bytes"
            )

        # Check maximum file size (files up to max_upload_size are accepted)
        if file_size > settings.max_upload_size:
            raise HTTPException(
                status_code=413,
                detail=f"File size exceeds maximum allowed size of {settings.max_upload_size / (1024 * 1024)}MB"
            )

        # Generate unique job ID and S3 key
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        try:
            # Upload to S3
            self.s3_client.upload_fileobj(
                file.file,
                self.temp_bucket,
                s3_key
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file to storage: {str(e)}"
            )

        return job_id, s3_key

    def get_result_url(self, job_id: str, file_type: str) -> str:
        """
        Generate S3 URL for result file.

        Supports both LocalStack (development) and production AWS S3:
        - LocalStack: http://localstack:4566/bucket/key (path-style)
        - Production: https://bucket.s3.region.amazonaws.com/key (virtual-hosted-style)

        Args:
            job_id: Job identifier
            file_type: File extension (md)

        Returns:
            S3 URL for the result file
        """
        s3_key = f"{job_id}.{file_type}"

        # LocalStack development environment
        if settings.aws_endpoint_url:
            return f"{settings.aws_endpoint_url}/{self.results_bucket}/{s3_key}"

        # Production AWS S3 - use virtual-hosted-style URL
        region = settings.aws_region or "us-east-1"
        return f"https://{self.results_bucket}.s3.{region}.amazonaws.com/{s3_key}"

    async def check_s3_access(self) -> bool:
        """
        Check if S3 is accessible.

        Returns:
            True if S3 is accessible, False otherwise
        """
        try:
            self.s3_client.head_bucket(Bucket=self.temp_bucket)
            return True
        except Exception:
            return False

    async def download_temp_file(self, s3_key: str) -> bytes:
        """
        Download file from temp bucket.

        Args:
            s3_key: S3 key of file to download

        Returns:
            File contents as bytes

        Raises:
            HTTPException: If download fails
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.temp_bucket,
                Key=s3_key
            )
            return response['Body'].read()
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {s3_key}"
                )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to download file: {str(e)}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error downloading file: {str(e)}"
            )

    async def upload_result(
        self,
        job_id: str,
        content: str,
        format: str
    ) -> str:
        """
        Upload processed result to results bucket.

        Args:
            job_id: Job identifier
            content: Markdown content as string
            format: File format ('md')

        Returns:
            Public URL to the result file

        Raises:
            HTTPException: If upload fails
        """
        s3_key = f"{job_id}.{format}"

        # Set correct Content-Type based on format
        content_type_map = {
            'md': 'text/markdown'
        }
        content_type = content_type_map.get(format, 'text/plain')

        try:
            # Upload to results bucket
            # Handle both str and bytes content
            body = content if isinstance(content, bytes) else content.encode('utf-8')
            self.s3_client.put_object(
                Bucket=self.results_bucket,
                Key=s3_key,
                Body=body,
                ContentType=content_type,
                CacheControl='public, max-age=31536000'  # Cache for 1 year
            )

            # Return public URL using same logic as get_result_url
            if settings.aws_endpoint_url:
                return f"{settings.aws_endpoint_url}/{self.results_bucket}/{s3_key}"
            else:
                region = settings.aws_region or "us-east-1"
                return f"https://{self.results_bucket}.s3.{region}.amazonaws.com/{s3_key}"
        except ClientError as e:
            # Preserve ClientError for retry logic to categorize properly
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload result: {error_code}"
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload result: {str(e)}"
            )

    async def upload_image(
        self,
        job_id: str,
        image_data: bytes,
        image_name: str
    ) -> str:
        """Upload extracted image (figure/table) to results bucket.

        Images are stored in a subfolder structure: {job_id}/images/{image_name}
        This allows grouping all assets for a job together.

        Args:
            job_id: Job identifier
            image_data: PNG image bytes
            image_name: Filename for image (e.g., "figure-1.png", "table-2.png")

        Returns:
            Public URL to the uploaded image

        Raises:
            HTTPException: If upload fails
        """
        s3_key = f"{job_id}/images/{image_name}"

        try:
            self.s3_client.put_object(
                Bucket=self.results_bucket,
                Key=s3_key,
                Body=image_data,
                ContentType='image/png',
                CacheControl='public, max-age=31536000'  # Cache for 1 year
            )

            # Return public URL
            if settings.aws_endpoint_url:
                return f"{settings.aws_endpoint_url}/{self.results_bucket}/{s3_key}"
            else:
                region = settings.aws_region or "us-east-1"
                return f"https://{self.results_bucket}.s3.{region}.amazonaws.com/{s3_key}"

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload image {image_name}: {error_code}"
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload image {image_name}: {str(e)}"
            )

    async def delete_temp_file(self, s3_key: str) -> bool:
        """
        Delete temporary file from temp bucket.

        This is a best-effort operation - errors are logged but not raised.
        Cleanup failures should not block the main workflow.

        Args:
            s3_key: S3 key of file to delete

        Returns:
            bool: True if deletion succeeded, False on error
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.temp_bucket,
                Key=s3_key
            )
            logger.debug(f"Successfully deleted temp file: {s3_key}")
            return True
        except ClientError as e:
            # Log error but don't fail - cleanup is best effort
            logger.warning(f"Failed to delete temp file {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.warning(f"Unexpected error deleting temp file {s3_key}: {str(e)}")
            return False

    async def file_exists(self, bucket: str, key: str) -> bool:
        """
        Check if file exists in specified bucket.

        Args:
            bucket: Bucket name
            key: S3 object key

        Returns:
            True if file exists, False otherwise
        """
        try:
            self.s3_client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            # For other errors, assume file doesn't exist
            return False
        except Exception:
            return False

    async def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expiration: int = 3600
    ) -> str:
        """
        Generate presigned URL for secure file access.

        Args:
            bucket: Bucket name
            key: S3 object key
            expiration: URL expiration time in seconds (default: 1 hour)

        Returns:
            Presigned URL string

        Raises:
            HTTPException: If URL generation fails
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': key},
                ExpiresIn=expiration
            )
            return url
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate presigned URL: {str(e)}"
            )

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
            >>> storage = StorageService(s3_client, "temp-bucket", "results-bucket")
            >>> deleted = await storage.cleanup_temp_files_for_job("abc-123")
            >>> print(f"Deleted {deleted} files")
        """
        try:
            deleted_count = 0

            # List all objects with job_id prefix (handles both temp/{job_id}.pdf and temp/{job_id}/*)
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.temp_bucket,
                Prefix=f"temp/{job_id}"
            )

            # Collect all object keys to delete
            objects_to_delete = []
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects_to_delete.append({'Key': obj['Key']})

            # Batch delete objects (S3 allows up to 1000 per request)
            if objects_to_delete:
                # Delete in batches of 1000
                batch_size = 1000
                for i in range(0, len(objects_to_delete), batch_size):
                    batch = objects_to_delete[i:i + batch_size]
                    response = self.s3_client.delete_objects(
                        Bucket=self.temp_bucket,
                        Delete={'Objects': batch}
                    )
                    deleted_count += len(response.get('Deleted', []))

                    # Log any errors
                    if 'Errors' in response and response['Errors']:
                        for error in response['Errors']:
                            logger.error(
                                f"Failed to delete {error['Key']}: {error['Message']}"
                            )

                logger.info(
                    f"Cleaned up {deleted_count} temp files for job {job_id}"
                )

            return deleted_count

        except ClientError as e:
            logger.error(
                f"S3 ClientError cleaning up job {job_id}: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to cleanup temp files: {str(e)}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error cleaning up job {job_id}: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error during cleanup: {str(e)}"
            )

    async def list_temp_files(
        self,
        older_than_hours: int = 24
    ) -> list[dict[str, any]]:
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
            >>> storage = StorageService(s3_client, "temp-bucket", "results-bucket")
            >>> old_files = await storage.list_temp_files(older_than_hours=48)
            >>> for file in old_files:
            ...     print(f"{file['key']} is {file['age_hours']:.1f} hours old")
        """
        try:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
            old_files = []

            # Paginate through temp bucket
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.temp_bucket,
                Prefix='temp/'
            )

            for page in pages:
                if 'Contents' not in page:
                    continue

                for obj in page['Contents']:
                    # Ensure LastModified is timezone-aware
                    last_modified = obj['LastModified']
                    if last_modified.tzinfo is None:
                        last_modified = last_modified.replace(tzinfo=timezone.utc)

                    # Check if file is older than cutoff
                    if last_modified < cutoff_time:
                        age_hours = (datetime.now(timezone.utc) - last_modified).total_seconds() / 3600
                        old_files.append({
                            'key': obj['Key'],
                            'size': obj['Size'],
                            'last_modified': last_modified,
                            'age_hours': age_hours
                        })

            logger.info(
                f"Found {len(old_files)} temp files older than {older_than_hours} hours"
            )
            return old_files

        except ClientError as e:
            logger.error(
                f"S3 ClientError listing temp files: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list temp files: {str(e)}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error listing temp files: {str(e)}",
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error listing temp files: {str(e)}"
            )

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
            >>> storage = StorageService(s3_client, "temp-bucket", "results-bucket")
            >>> success = await storage.delete_from_s3("temp-bucket", "temp/abc-123.pdf")
        """
        try:
            self.s3_client.delete_object(
                Bucket=bucket,
                Key=key
            )
            logger.debug(f"Deleted s3://{bucket}/{key}")
            return True

        except ClientError as e:
            # Check if object doesn't exist (idempotent success)
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.debug(f"Object already deleted: s3://{bucket}/{key}")
                return True

            logger.error(
                f"Failed to delete s3://{bucket}/{key}: {str(e)}",
                exc_info=True
            )
            return False

        except Exception as e:
            logger.error(
                f"Unexpected error deleting s3://{bucket}/{key}: {str(e)}",
                exc_info=True
            )
            return False