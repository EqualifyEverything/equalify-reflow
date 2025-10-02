"""Job service for job status management."""

import json
import logging
from datetime import datetime
from typing import Optional, List

from ..config import settings

logger = logging.getLogger(__name__)


class JobService:
    """Service for managing job status and metadata."""

    def __init__(self, redis_client):
        """Initialize job service with Redis client.

        Args:
            redis_client: Redis async client instance
        """
        self.redis = redis_client
        self.status_prefix = settings.job_status_prefix

    async def create_job(
        self,
        job_id: str,
        s3_key: str,
        status: str = "pii_scanning"
    ) -> None:
        """
        Create a new job in Redis.

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where document is stored
            status: Initial job status
        """
        created_at = datetime.utcnow().isoformat()

        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping={
                "job_id": job_id,
                "s3_key": s3_key,
                "status": status,
                "created_at": created_at,
                "updated_at": created_at
            }
        )

    async def get_job(self, job_id: str) -> Optional[dict]:
        """
        Get job status and metadata.

        Args:
            job_id: Job identifier

        Returns:
            Job data dictionary or None if not found
        """
        job_data = await self.redis.hgetall(f"{self.status_prefix}{job_id}")

        if not job_data:
            return None

        # Parse JSON fields if present
        if "pii_findings" in job_data and job_data["pii_findings"]:
            job_data["pii_findings"] = json.loads(job_data["pii_findings"])

        if "metadata" in job_data and job_data["metadata"]:
            job_data["metadata"] = json.loads(job_data["metadata"])

        return job_data

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        **additional_fields
    ) -> None:
        """
        Update job status and additional fields.

        Args:
            job_id: Job identifier
            status: New status
            **additional_fields: Additional fields to update
        """
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow().isoformat()
        }

        # Serialize complex fields as JSON
        for key, value in additional_fields.items():
            if isinstance(value, (dict, list)):
                update_data[key] = json.dumps(value)
            else:
                update_data[key] = str(value)

        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping=update_data
        )

    async def add_pii_findings(
        self,
        job_id: str,
        findings: List[dict]
    ) -> None:
        """
        Store PII scan results for a job.

        Args:
            job_id: Job identifier
            findings: List of PII finding dictionaries
        """
        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping={
                "pii_findings": json.dumps(findings),
                "updated_at": datetime.utcnow().isoformat()
            }
        )

    async def add_processing_result(
        self,
        job_id: str,
        result_url: str,
        confidence: float
    ) -> None:
        """
        Store processing completion data.

        Args:
            job_id: Job identifier
            result_url: URL to the processed result
            confidence: Processing confidence score (0.0 to 1.0)
        """
        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping={
                "result_url": result_url,
                "confidence_score": str(confidence),
                "completed_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
        )

    async def job_exists(self, job_id: str) -> bool:
        """
        Check if job exists in Redis.

        Args:
            job_id: Job identifier

        Returns:
            True if job exists, False otherwise
        """
        try:
            exists = await self.redis.exists(f"{self.status_prefix}{job_id}")
            return bool(exists)
        except Exception:
            return False

    async def delete_job(self, job_id: str) -> None:
        """
        Delete job and all associated metadata.

        Args:
            job_id: Job identifier
        """
        try:
            await self.redis.delete(f"{self.status_prefix}{job_id}")
        except Exception as e:
            raise Exception(f"Failed to delete job {job_id}: {str(e)}")

    async def set_expiration(self, job_id: str, ttl_seconds: int) -> None:
        """
        Set TTL for automatic job cleanup.

        Args:
            job_id: Job identifier
            ttl_seconds: Time-to-live in seconds
        """
        try:
            await self.redis.expire(
                f"{self.status_prefix}{job_id}",
                ttl_seconds
            )
        except Exception as e:
            raise Exception(f"Failed to set expiration for job {job_id}: {str(e)}")

    async def list_all_jobs(self) -> List[str]:
        """List all job IDs in Redis.

        This method scans for all job keys matching the status prefix
        and extracts the job IDs. Used by orphan detection service.

        Returns:
            List of job IDs (without prefix)

        Example:
            >>> job_service = JobService(redis_client)
            >>> job_ids = await job_service.list_all_jobs()
            >>> print(f"Found {len(job_ids)} jobs")
        """
        try:
            # Use SCAN to find all job keys (safer than KEYS for production)
            pattern = f"{self.status_prefix}*"
            job_ids = []

            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100
                )

                # Extract job IDs from keys (remove prefix)
                for key in keys:
                    # Remove prefix to get job ID
                    job_id = key.replace(self.status_prefix, "")
                    job_ids.append(job_id)

                if cursor == 0:
                    break

            logger.debug(f"Found {len(job_ids)} jobs in Redis")
            return job_ids

        except Exception as e:
            logger.error(f"Error listing jobs: {str(e)}", exc_info=True)
            return []

    async def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get job status and metadata (alias for get_job).

        This method is an alias for get_job() to match the naming
        convention used in other services (timeout_service, orphan_service).

        Args:
            job_id: Job identifier

        Returns:
            Job data dictionary or None if not found
        """
        return await self.get_job(job_id)

    async def cleanup_old_job(self, job_id: str) -> bool:
        """Delete old job status hash from Redis.

        This method is used by the timeout worker to clean up jobs that are:
        - Completed and past retention period
        - Failed and past retention period
        - Denied and past retention period
        - Stuck in processing state for too long

        Args:
            job_id: Job identifier (UUID)

        Returns:
            bool: True if job existed and was deleted, False if job didn't exist

        Example:
            >>> job_service = JobService(redis_client)
            >>> deleted = await job_service.cleanup_old_job("abc-123")
            >>> if deleted:
            ...     print("Job cleaned up successfully")
        """
        try:
            # Check if job exists before deleting
            key = f"{self.status_prefix}{job_id}"
            exists = await self.redis.exists(key)

            if not exists:
                logger.debug(f"Job {job_id} does not exist (already cleaned up)")
                return False

            # Get job status for logging before deletion
            job_data = await self.redis.hgetall(key)
            job_status = job_data.get('status', 'unknown') if job_data else 'unknown'

            # Delete the job hash
            deleted_count = await self.redis.delete(key)

            if deleted_count > 0:
                logger.info(
                    f"Cleaned up old job {job_id} (status: {job_status})"
                )
                return True
            else:
                logger.warning(
                    f"Failed to delete job {job_id} (delete returned 0)"
                )
                return False

        except Exception as e:
            logger.error(
                f"Error cleaning up job {job_id}: {str(e)}",
                exc_info=True
            )
            # Return False on error (job not cleaned up)
            return False