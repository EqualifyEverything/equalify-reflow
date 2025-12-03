"""Job service for job status management."""

import json
import logging
from datetime import UTC, datetime

from ..config import settings
from ..shared.constants.statuses import STATUS_COMPLETED, STATUS_DENIED, STATUS_FAILED

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
        # TTL settings from config
        self.job_ttl_active = settings.job_ttl_active
        self.job_ttl_completed = settings.job_ttl_completed
        self.job_ttl_failed = settings.job_ttl_failed
        self.job_ttl_denied = settings.job_ttl_denied

    def _get_ttl_for_status(self, status: str) -> int:
        """Get appropriate TTL (time-to-live) for job based on status.

        Different statuses have different retention requirements:
        - Completed jobs: 30 days (for result retrieval and audit)
        - Failed jobs: 30 days (for debugging and retry decisions)
        - Denied jobs: 7 days (shorter retention, decision recorded)
        - Active jobs (processing, awaiting_approval, etc.): 7 days

        Args:
            status: Job status string

        Returns:
            TTL in seconds for the given status

        Example:
            >>> ttl = self._get_ttl_for_status("completed")
            >>> print(f"Completed jobs expire after {ttl / 86400} days")
        """
        if status == STATUS_COMPLETED:
            return self.job_ttl_completed
        elif status == STATUS_FAILED:
            return self.job_ttl_failed
        elif status == STATUS_DENIED:
            return self.job_ttl_denied
        else:
            # Default for active states: pii_scanning, awaiting_approval, processing
            return self.job_ttl_active

    async def _set_job_ttl(self, job_id: str, status: str) -> None:
        """Set TTL for job hash based on status.

        Automatically sets appropriate expiration time based on job status.
        Critical for preventing Redis memory exhaustion from abandoned jobs.

        Args:
            job_id: Job identifier
            status: Current job status (determines TTL duration)

        Raises:
            Exception: If Redis EXPIRE command fails

        Example:
            >>> await self._set_job_ttl("job-123", "completed")
            # Sets 30-day TTL for completed job
        """
        ttl = self._get_ttl_for_status(status)
        key = f"{self.status_prefix}{job_id}"

        try:
            await self.redis.expire(key, ttl)
            logger.debug(
                f"Set TTL for job {job_id} to {ttl}s "
                f"({ttl / 86400:.1f} days) for status '{status}'"
            )
        except Exception as e:
            logger.error(
                f"Failed to set TTL for job {job_id}: {str(e)}",
                exc_info=True
            )
            raise Exception(f"Failed to set TTL for job {job_id}: {str(e)}")

    async def create_job(
        self,
        job_id: str,
        s3_key: str,
        status: str = "pii_scanning",
        original_filename: str | None = None
    ) -> None:
        """
        Create a new job in Redis with automatic TTL.

        Sets initial TTL based on job status to prevent Redis memory exhaustion.
        Jobs will auto-expire after retention period unless status changes.

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where document is stored
            status: Initial job status (default: "pii_scanning")
            original_filename: Original filename from upload

        Example:
            >>> await job_service.create_job("job-123", "temp/file.pdf", original_filename="doc.pdf")
            # Creates job with 7-day TTL (active job default)
        """
        created_at = datetime.now(UTC).isoformat()

        mapping: dict[str, str] = {
            "job_id": job_id,
            "s3_key": s3_key,
            "status": status,
            "created_at": created_at,
            "updated_at": created_at
        }
        if original_filename:
            mapping["original_filename"] = original_filename

        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping=mapping
        )

        # Set TTL based on initial status (prevents memory leaks)
        await self._set_job_ttl(job_id, status)

    async def get_job(self, job_id: str) -> dict | None:
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

        # Parse JSON array/object fields only
        # These fields are stored as JSON strings and need to be parsed
        json_fields = [
            "pii_findings",         # Array of PII findings
            "correction_results",   # Array of correction results per page
            "page_image_keys",      # Array of S3 keys for page images
            "llm_page_costs"        # Array of per-page LLM token usage and costs
        ]

        for field in json_fields:
            if field in job_data and job_data[field]:
                try:
                    job_data[field] = json.loads(job_data[field])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse {field} for job {job_id}")

        return job_data

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        **additional_fields
    ) -> None:
        """
        Update job status and additional fields with automatic TTL adjustment.

        When job status changes, TTL is automatically adjusted based on new status:
        - Transition to 'completed': Sets 30-day TTL
        - Transition to 'failed': Sets 30-day TTL
        - Transition to 'denied': Sets 7-day TTL
        - Other statuses (active): Sets 7-day TTL

        Args:
            job_id: Job identifier
            status: New status
            **additional_fields: Additional fields to update (auto-serialized if dict/list)

        Example:
            >>> await job_service.update_job_status("job-123", "completed")
            # Updates status and sets 30-day TTL
        """
        update_data = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat()
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

        # Adjust TTL based on new status (critical for memory management)
        await self._set_job_ttl(job_id, status)

    async def add_pii_findings(
        self,
        job_id: str,
        findings: list[dict]
    ) -> None:
        """
        Store PII scan results for a job and maintain TTL.

        Updates job with PII findings without changing status.
        Does NOT modify TTL since status remains unchanged.
        TTL will be adjusted when status changes (e.g., to awaiting_approval).

        Args:
            job_id: Job identifier
            findings: List of PII finding dictionaries

        Note:
            This method is typically called before status update to awaiting_approval,
            which will set the appropriate TTL via update_job_status().
        """
        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping={
                "pii_findings": json.dumps(findings),
                "updated_at": datetime.now(UTC).isoformat()
            }
        )
        # Note: TTL maintained from previous status, will be updated on next status change

    async def add_processing_result(
        self,
        job_id: str,
        result_url: str,
        confidence: float
    ) -> None:
        """
        Store processing completion data and maintain TTL.

        Updates job with processing results without changing status.
        Does NOT modify TTL since status remains unchanged.
        TTL will be adjusted when status changes to 'completed' via update_job_status().

        Args:
            job_id: Job identifier
            result_url: URL to the processed result
            confidence: Processing confidence score (0.0 to 1.0)

        Note:
            This method is typically called before status update to 'completed',
            which will set the 30-day retention TTL via update_job_status().
        """
        await self.redis.hset(
            f"{self.status_prefix}{job_id}",
            mapping={
                "result_url": result_url,
                "confidence_score": str(confidence),
                "completed_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat()
            }
        )
        # Note: TTL maintained from previous status, will be updated on next status change

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

    async def list_all_jobs(self) -> list[str]:
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

    async def get_job_status(self, job_id: str) -> dict | None:
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

    async def store_approval_token_mapping(
        self,
        approval_token: str,
        job_id: str,
        ttl_hours: int = 4
    ) -> None:
        """Store approval token to job ID mapping for O(1) lookup.

        Creates a Redis key: eq-pdf:approval-token:{token} → job_id
        This enables direct token lookup without scanning all job hashes.

        Args:
            approval_token: Secure approval token
            job_id: Job identifier
            ttl_hours: Time-to-live in hours (matches approval expiration)

        Example:
            >>> job_service = JobService(redis_client)
            >>> await job_service.store_approval_token_mapping(
            ...     "abc123token",
            ...     "job-uuid-123",
            ...     ttl_hours=4
            ... )
        """
        try:
            token_key = f"eq-pdf:approval-token:{approval_token}"
            ttl_seconds = ttl_hours * 3600

            # Store mapping with expiration
            await self.redis.set(token_key, job_id, ex=ttl_seconds)

            logger.debug(
                f"Stored approval token mapping: {approval_token[:8]}... → {job_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to store approval token mapping: {str(e)}",
                exc_info=True
            )
            raise Exception(f"Failed to store token mapping: {str(e)}")

    async def get_job_by_approval_token(self, token: str) -> dict | None:
        """Get job by approval token using O(1) Redis lookup.

        Uses token mapping stored in Redis to directly fetch job ID,
        then retrieves full job data. Much faster than scanning all jobs.

        Args:
            token: Approval token from URL

        Returns:
            Job data dictionary or None if token not found/expired

        Example:
            >>> job_service = JobService(redis_client)
            >>> job = await job_service.get_job_by_approval_token("abc123token")
            >>> if job:
            ...     print(f"Found job: {job['job_id']}")
        """
        try:
            # O(1) lookup in Redis
            token_key = f"eq-pdf:approval-token:{token}"
            job_id = await self.redis.get(token_key)

            if not job_id:
                logger.debug("Approval token not found or expired")
                return None

            # Decode if bytes
            if isinstance(job_id, bytes):
                job_id = job_id.decode('utf-8')

            # Fetch full job data
            return await self.get_job(job_id)

        except Exception as e:
            logger.error(
                f"Error fetching job by token: {str(e)}",
                exc_info=True
            )
            return None
