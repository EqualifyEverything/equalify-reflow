"""Storage service for S3 operations."""

import uuid
from typing import BinaryIO, Optional
from io import BytesIO

from fastapi import HTTPException, UploadFile
from botocore.exceptions import ClientError

from ..config import settings


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
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Reset to beginning

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

        Args:
            job_id: Job identifier
            file_type: File extension (html or mdx)

        Returns:
            S3 URL for the result file
        """
        s3_key = f"{job_id}.{file_type}"
        return f"{settings.aws_endpoint_url}/{self.results_bucket}/{s3_key}"

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
            content: HTML or MDX content as string
            format: File format ('html' or 'mdx')

        Returns:
            Public URL to the result file

        Raises:
            HTTPException: If upload fails
        """
        s3_key = f"{job_id}.{format}"

        # Set correct Content-Type based on format
        content_type_map = {
            'html': 'text/html',
            'mdx': 'text/markdown'
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

            # Return public URL
            return f"{settings.aws_endpoint_url}/{self.results_bucket}/{s3_key}"
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload result: {str(e)}"
            )

    async def delete_temp_file(self, s3_key: str) -> None:
        """
        Delete temporary file from temp bucket.

        Args:
            s3_key: S3 key of file to delete

        Raises:
            HTTPException: If deletion fails
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.temp_bucket,
                Key=s3_key
            )
        except Exception as e:
            # Log error but don't fail - cleanup is best effort
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete temp file: {str(e)}"
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