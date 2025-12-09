"""S3 URL generation service (pure functions, no S3 API calls)."""

import logging

from fastapi import HTTPException

from ..config import settings

logger = logging.getLogger(__name__)


class S3URLService:
    """Service for generating S3 URLs without making API calls.

    This service provides pure functions for URL generation based on
    environment configuration. It does not interact with S3 API directly.
    """

    def __init__(self, s3_client, temp_bucket: str, results_bucket: str):
        """Initialize URL service with bucket names and optional S3 client.

        Args:
            s3_client: Boto3 S3 client instance (only needed for presigned URLs)
            temp_bucket: Name of temporary storage bucket
            results_bucket: Name of results storage bucket
        """
        self.s3_client = s3_client
        self.temp_bucket = temp_bucket
        self.results_bucket = results_bucket

    def get_result_url(self, job_id: str, file_type: str) -> str:
        """Generate public S3 URL for result file.

        Args:
            job_id: Job identifier
            file_type: File extension (md)

        Returns:
            Public S3 URL for the result file
        """
        s3_key = f"{job_id}.{file_type}"
        return self.build_public_url(self.results_bucket, s3_key)

    def build_public_url(self, bucket: str, s3_key: str) -> str:
        """Build a public-facing S3 URL.

        Uses S3_PUBLIC_URL setting for dev (localhost:4566) or constructs
        AWS virtual-hosted URL for production.

        Args:
            bucket: S3 bucket name
            s3_key: Object key within the bucket

        Returns:
            Public URL accessible from client
        """
        if settings.s3_public_url:
            # Dev: use configured public URL (e.g., http://localhost:4566)
            return f"{settings.s3_public_url}/{bucket}/{s3_key}"

        # Production: AWS virtual-hosted style URL
        region = settings.aws_region or "us-east-1"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"

    async def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expiration: int = 3600
    ) -> str:
        """Generate presigned URL for secure file access.

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

    async def generate_url(
        self,
        s3_key: str,
        bucket: str | None = None,
        expiration: int = 3600
    ) -> str:
        """Generate URL from S3 key.

        For LocalStack: Returns path-style URL (http://localstack:4566/bucket/key)
        For AWS: Returns virtual-hosted style URL (https://bucket.s3.region.amazonaws.com/key)

        Args:
            s3_key: S3 object key (e.g., "results/abc-123.md")
            bucket: Bucket name (defaults to results_bucket)
            expiration: Presigned URL expiration in seconds (default: 1 hour)

        Returns:
            Full URL to S3 object

        Example:
            >>> url_service = S3URLService(s3_client, "temp-bucket", "results-bucket")
            >>> url = await url_service.generate_url("results/abc-123.md")
            >>> # LocalStack: http://localstack:4566/results-bucket/results/abc-123.md
            >>> # AWS: https://results-bucket.s3.us-east-1.amazonaws.com/results/abc-123.md
        """
        bucket = bucket or self.results_bucket
        return self.build_public_url(bucket, s3_key)
