"""Approval service for PII-flagged document review workflow.

Handles token validation, approval/denial decision processing,
and routing to processing queue or cleanup.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Literal, Any

from ..config import settings
from ..shared.constants.queues import APPROVAL_TIMEOUT_KEY, PROCESSING_QUEUE
from ..shared.constants.statuses import STATUS_PROCESSING, STATUS_DENIED
from ..shared.models.queue import ProcessingQueuePayload
from .job_service import JobService
from .queue_service import QueueService
from .cleanup_service import CleanupService

logger = logging.getLogger(__name__)


class ApprovalService:
    """Service for managing approval workflow decisions."""

    def __init__(
        self,
        redis_client: Any,
        s3_client: Any,
        job_service: JobService,
        queue_service: QueueService
    ):
        """Initialize approval service with dependencies.

        Args:
            redis_client: Redis async client instance
            s3_client: Boto3 S3 async client instance
            job_service: Job status management service
            queue_service: Redis queue operations service
        """
        self.redis = redis_client
        self.job_service = job_service
        self.queue_service = queue_service
        self.cleanup_service = CleanupService(s3_client)

    async def validate_approval_token(self, token: str) -> Optional[dict]:
        """Find job by approval token and validate expiration.

        Scans all job keys to find matching approval token.
        Checks expiration timestamp to ensure token is still valid.

        Args:
            token: Approval token from URL

        Returns:
            Job data dict if valid, None if invalid/expired

        Example:
            >>> job = await service.validate_approval_token("abc123...")
            >>> if job:
            ...     print(f"Valid token for job {job['job_id']}")
        """
        try:
            # Scan all job keys to find token match
            # NOTE: O(N) operation - acceptable for MVP scale
            # TODO: Optimize with token→job_id hash mapping for production
            job_keys = await self.redis.keys(f"{settings.job_status_prefix}*")

            for key in job_keys:
                # Extract job_id from key (eq-pdf:job:XXX → XXX)
                if isinstance(key, bytes):
                    key = key.decode('utf-8')

                job_id = key.split(":")[-1]
                job = await self.job_service.get_job(job_id)

                if not job:
                    continue

                # Check if token matches
                if job.get("approval_token") == token:
                    # Validate expiration
                    expires_at_str = job.get("approval_expires_at")
                    if not expires_at_str:
                        logger.warning(f"Job {job_id} missing expires_at field")
                        return None

                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.now(timezone.utc)

                    if now < expires_at:
                        logger.info(f"Valid approval token for job {job_id}")
                        return job
                    else:
                        logger.info(f"Expired approval token for job {job_id}")
                        return None

            # No matching token found
            logger.warning(f"No job found with approval token")
            return None

        except Exception as e:
            logger.error(f"Error validating approval token: {str(e)}", exc_info=True)
            return None

    async def process_approval_decision(
        self,
        job_id: str,
        decision: Literal["approved", "denied"],
        justification: str,
        reviewed_by: str
    ) -> None:
        """Process approval or denial decision and route accordingly.

        Removes job from timeout tracking, then either:
        - Approved: Routes to processing queue, updates status
        - Denied: Cleans up S3 files, updates status

        Args:
            job_id: Job identifier
            decision: "approved" or "denied"
            justification: Required explanation for decision
            reviewed_by: Reviewer identifier (email or user ID)

        Raises:
            Exception: If job not found or queue operations fail

        Example:
            >>> await service.process_approval_decision(
            ...     job_id="abc-123",
            ...     decision="approved",
            ...     justification="Instructor name in syllabus is acceptable",
            ...     reviewed_by="faculty@uic.edu"
            ... )
        """
        # Get job data
        job = await self.job_service.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        s3_key = job.get("s3_key")
        if not s3_key:
            raise ValueError(f"Job {job_id} missing s3_key")

        # Remove from timeout tracking FIRST to prevent race condition
        try:
            await self.redis.zrem(APPROVAL_TIMEOUT_KEY, job_id)
            logger.info(f"Removed job {job_id} from approval timeout tracking")
        except Exception as e:
            logger.error(f"Failed to remove job {job_id} from timeout tracking: {str(e)}")
            # Continue processing - not critical

        # Store decision metadata
        reviewed_at = datetime.now(timezone.utc).isoformat()
        decision_metadata = {
            "decision": decision,
            "justification": justification,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at
        }

        if decision == "approved":
            await self._process_approval(job_id, s3_key, decision_metadata)
        else:
            await self._process_denial(job_id, s3_key, decision_metadata)

    async def _process_approval(
        self,
        job_id: str,
        s3_key: str,
        decision_metadata: dict
    ) -> None:
        """Handle approved decision - route to processing queue.

        Args:
            job_id: Job identifier
            s3_key: S3 object key
            decision_metadata: Decision details for audit trail
        """
        try:
            # Enqueue to processing queue
            queue_payload = ProcessingQueuePayload(
                job_id=job_id,
                s3_key=s3_key,
                approved_at=datetime.now(timezone.utc)
            )
            await self.queue_service.enqueue(PROCESSING_QUEUE, queue_payload)
            logger.info(f"Job {job_id} approved - queued for processing")

            # Update job status
            await self.job_service.update_job_status(
                job_id,
                STATUS_PROCESSING,
                approval_decision=decision_metadata
            )
            logger.info(f"Job {job_id} status updated to processing")

        except Exception as e:
            logger.error(f"Failed to process approval for job {job_id}: {str(e)}", exc_info=True)
            raise

    async def _process_denial(
        self,
        job_id: str,
        s3_key: str,
        decision_metadata: dict
    ) -> None:
        """Handle denied decision - cleanup files and update status.

        Args:
            job_id: Job identifier
            s3_key: S3 object key
            decision_metadata: Decision details for audit trail
        """
        try:
            # Cleanup S3 temp files
            cleanup_success = await self.cleanup_service.cleanup_job_files(s3_key)
            if cleanup_success:
                logger.info(f"Job {job_id} denied - S3 files cleaned up")
            else:
                logger.warning(f"Job {job_id} denied - S3 cleanup failed (non-critical)")

            # Update job status to denied
            await self.job_service.update_job_status(
                job_id,
                STATUS_DENIED,
                denial_decision=decision_metadata
            )
            logger.info(f"Job {job_id} status updated to denied")

        except Exception as e:
            logger.error(f"Failed to process denial for job {job_id}: {str(e)}", exc_info=True)
            raise
