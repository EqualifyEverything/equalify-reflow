"""Storage service for S3 operations."""

import logging
import uuid
from typing import Any

from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile

from ..config import settings
from ..utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from ..utils.retry_helpers import retry_with_backoff_for_sync_func

logger = logging.getLogger(__name__)


class StorageService:
    """Service for managing document storage in S3."""

    def __init__(self, s3_client: Any, temp_bucket: str, results_bucket: str):
        """Initialize storage service with S3 client and bucket names.

        Args:
            s3_client: Boto3 S3 client instance
            temp_bucket: Name of temporary storage bucket
            results_bucket: Name of results storage bucket
        """
        self.s3_client = s3_client
        self.temp_bucket = temp_bucket
        self.results_bucket = results_bucket

        # Circuit breakers for S3 operation types
        self.upload_circuit = CircuitBreaker(
            name="s3-upload",
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0
        )
        self.download_circuit = CircuitBreaker(
            name="s3-download",
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0
        )

    async def store_document(self, file: UploadFile) -> tuple[str, str]:
        """
        Store uploaded document in S3 temp bucket.

        Args:
            file: Uploaded PDF file

        Returns:
            Tuple of (job_id, s3_key)

        Raises:
            HTTPException: If file validation fails or upload fails
            CircuitBreakerOpenError: If S3 upload circuit breaker is open
        """
        # Check circuit breaker first
        self.upload_circuit.check_state()
        if self.upload_circuit.is_open:
            raise CircuitBreakerOpenError("S3 upload circuit breaker is open due to repeated failures")

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
        except (OSError, AttributeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Unable to read file: {str(e)}"
            )

        # Check minimum file size (empty or corrupt files)
        min_file_size = 100  # 100 bytes minimum
        if file_size < min_file_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too small. Minimum file size is {min_file_size} bytes"
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
            # Upload to S3 with retry logic
            await retry_with_backoff_for_sync_func(
                lambda: self.s3_client.upload_fileobj(
                    file.file,
                    self.temp_bucket,
                    s3_key
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"upload {s3_key}"
            )

            # Record success in circuit breaker
            self.upload_circuit.record_success()

        except CircuitBreakerOpenError:
            # Circuit breaker already logged the issue
            raise
        except Exception as e:
            # Record failure in circuit breaker
            self.upload_circuit.record_failure()

            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file to storage: {str(e)}"
            )

        return job_id, s3_key


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
        Download file from temp bucket with retry and circuit breaker.

        Args:
            s3_key: S3 key of file to download

        Returns:
            File contents as bytes

        Raises:
            HTTPException: If download fails
            CircuitBreakerOpenError: If S3 download circuit breaker is open
        """
        # Check circuit breaker
        self.download_circuit.check_state()
        if self.download_circuit.is_open:
            raise CircuitBreakerOpenError("S3 download circuit breaker is open due to repeated failures")

        try:
            # Download with retry logic
            response: Any = await retry_with_backoff_for_sync_func(
                lambda: self.s3_client.get_object(
                    Bucket=self.temp_bucket,
                    Key=s3_key
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"download {s3_key}"
            )

            # Record success
            self.download_circuit.record_success()

            return response['Body'].read()  # type: ignore[no-any-return]

        except CircuitBreakerOpenError:
            raise
        except ClientError as e:
            # Record failure
            self.download_circuit.record_failure()

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
            # Record failure
            self.download_circuit.record_failure()

            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error downloading file: {str(e)}"
            )

    async def upload_result(
        self,
        job_id: str,
        content: str,
        format: str,
        suffix: str | None = None
    ) -> str:
        """
        Upload processed result to results bucket with retry and circuit breaker.

        Args:
            job_id: Job identifier
            content: Markdown content as string
            format: File format ('md')
            suffix: Optional suffix for versioning (e.g., 'original', 'corrected')

        Returns:
            S3 key (e.g., "abc-123.md" or "abc-123-original.md")

        Raises:
            HTTPException: If upload fails
            CircuitBreakerOpenError: If S3 upload circuit breaker is open
        """
        # Check circuit breaker
        self.upload_circuit.check_state()
        if self.upload_circuit.is_open:
            raise CircuitBreakerOpenError("S3 upload circuit breaker is open due to repeated failures")

        # Build S3 key with optional suffix
        if suffix:
            s3_key = f"{job_id}-{suffix}.{format}"
        else:
            s3_key = f"{job_id}.{format}"

        # Set correct Content-Type based on format
        content_type_map = {
            'md': 'text/markdown'
        }
        content_type = content_type_map.get(format, 'text/plain')

        try:
            # Upload to results bucket with retry
            body = content if isinstance(content, bytes) else content.encode('utf-8')

            await retry_with_backoff_for_sync_func(
                lambda: self.s3_client.put_object(
                    Bucket=self.results_bucket,
                    Key=s3_key,
                    Body=body,
                    ContentType=content_type,
                    CacheControl='public, max-age=31536000'
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"upload result {s3_key}"
            )

            # Record success
            self.upload_circuit.record_success()

            # Return S3 key (not URL)
            return s3_key

        except CircuitBreakerOpenError:
            raise
        except ClientError as e:
            # Record failure
            self.upload_circuit.record_failure()

            # Preserve ClientError for retry logic to categorize properly
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload result: {error_code}"
            ) from e
        except Exception as e:
            # Record failure
            self.upload_circuit.record_failure()

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
        """Upload extracted image (figure/table) to results bucket with retry and circuit breaker.

        Images are stored in a subfolder structure: {job_id}/images/{image_name}
        This allows grouping all assets for a job together.

        Args:
            job_id: Job identifier
            image_data: PNG image bytes
            image_name: Filename for image (e.g., "figure-1.png", "table-2.png")

        Returns:
            S3 key (e.g., "abc-123/images/figure-1.png")

        Raises:
            HTTPException: If upload fails
            CircuitBreakerOpenError: If S3 upload circuit breaker is open
        """
        # Check circuit breaker
        self.upload_circuit.check_state()
        if self.upload_circuit.is_open:
            raise CircuitBreakerOpenError("S3 upload circuit breaker is open due to repeated failures")

        s3_key = f"{job_id}/images/{image_name}"

        try:
            await retry_with_backoff_for_sync_func(
                lambda: self.s3_client.put_object(
                    Bucket=self.results_bucket,
                    Key=s3_key,
                    Body=image_data,
                    ContentType='image/png',
                    CacheControl='public, max-age=31536000'
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"upload image {image_name}"
            )

            # Record success
            self.upload_circuit.record_success()

            # Return S3 key (URL generation delegated to S3URLService)
            return s3_key

        except CircuitBreakerOpenError:
            raise
        except ClientError as e:
            # Record failure
            self.upload_circuit.record_failure()

            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload image {image_name}: {error_code}"
            ) from e
        except Exception as e:
            # Record failure
            self.upload_circuit.record_failure()

            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload image {image_name}: {str(e)}"
            )

    async def upload_page_image(
        self,
        job_id: str,
        page_num: int,
        image_data: bytes
    ) -> str:
        """Upload page preview image to temp bucket for correction review.

        Page images are stored temporarily for human review of AI corrections.
        They are stored in: {job_id}/pages/page-{num}.png

        These images are deleted after correction approval (7-day TTL).

        Args:
            job_id: Job identifier
            page_num: Page number (1-indexed)
            image_data: PNG image bytes

        Returns:
            S3 key (e.g., "abc-123/pages/page-1.png")

        Raises:
            HTTPException: If upload fails
            CircuitBreakerOpenError: If S3 upload circuit breaker is open
        """
        # Check circuit breaker
        self.upload_circuit.check_state()
        if self.upload_circuit.is_open:
            raise CircuitBreakerOpenError("S3 upload circuit breaker is open due to repeated failures")

        s3_key = f"{job_id}/pages/page-{page_num}.png"

        try:
            await retry_with_backoff_for_sync_func(
                lambda: self.s3_client.put_object(
                    Bucket=self.temp_bucket,
                    Key=s3_key,
                    Body=image_data,
                    ContentType='image/png',
                    CacheControl='public, max-age=604800'  # 7 days
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"upload page {page_num} image"
            )

            # Record success
            self.upload_circuit.record_success()

            # Return S3 key (not URL)
            return s3_key

        except CircuitBreakerOpenError:
            raise
        except ClientError as e:
            # Record failure
            self.upload_circuit.record_failure()

            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload page {page_num} image: {error_code}"
            ) from e
        except Exception as e:
            # Record failure
            self.upload_circuit.record_failure()

            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload page {page_num} image: {str(e)}"
            )


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




