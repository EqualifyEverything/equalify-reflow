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

        Uses O(1) Redis lookup via token mapping to find job directly.
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
            # O(1) lookup using token mapping (stored in job_service)
            job = await self.job_service.get_job_by_approval_token(token)

            if not job:
                logger.debug("Approval token not found or expired")
                return None

            job_id = job.get("job_id")

            # Validate expiration timestamp
            expires_at_str = job.get("approval_expires_at")
            if not expires_at_str:
                logger.warning(f"Job {job_id} missing expires_at field")
                return None

            try:
                # Parse ISO format datetime and ensure timezone awareness
                # Replace Z suffix with +00:00 for proper parsing
                expires_at_str = expires_at_str.replace("Z", "+00:00")
                expires_at = datetime.fromisoformat(expires_at_str)

                # If somehow still naive, add UTC timezone
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)

            except (ValueError, AttributeError) as e:
                logger.error(
                    f"Invalid expiration timestamp for job {job_id}: {expires_at_str}",
                    exc_info=True
                )
                return None

            now = datetime.now(timezone.utc)

            if now < expires_at:
                logger.info(f"Valid approval token for job {job_id}")
                return job
            else:
                logger.info(f"Expired approval token for job {job_id}")
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

        Uses Redis atomic lock to prevent duplicate queue entries
        from concurrent approval attempts.

        Args:
            job_id: Job identifier
            s3_key: S3 object key
            decision_metadata: Decision details for audit trail
        """
        try:
            # ATOMIC LOCK: Use Redis SETNX to claim exclusive approval processing
            # This prevents race condition where two concurrent approvals
            # both try to enqueue the same job
            lock_key = f"eq-pdf:approval-lock:{job_id}"
            lock_acquired = await self.redis.set(
                lock_key,
                "processing",
                nx=True,  # Only set if not exists (atomic)
                ex=60  # Expire after 60 seconds (safety cleanup)
            )

            if not lock_acquired:
                # Another approval is already processing or completed
                logger.info(
                    f"Job {job_id} approval already in progress or completed - "
                    "ignoring duplicate approval attempt"
                )
                return

            try:
                # Double-check job status after acquiring lock
                current_job = await self.job_service.get_job(job_id)
                if not current_job:
                    logger.warning(f"Job {job_id} disappeared during approval processing")
                    raise ValueError(f"Job {job_id} not found")

                current_status = current_job.get("status")

                # If job already processed/processing, release lock and return
                if current_status == STATUS_PROCESSING:
                    logger.info(f"Job {job_id} already in processing status - duplicate approval")
                    return

                # If job is in any other non-awaiting state, abort
                if current_status not in ["awaiting_approval", "pii_scanning"]:
                    logger.warning(
                        f"Job {job_id} in unexpected status '{current_status}' during approval"
                    )
                    raise ValueError(f"Job {job_id} cannot be approved from status '{current_status}'")

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

            finally:
                # Release lock after processing (or on error)
                await self.redis.delete(lock_key)
                logger.debug(f"Released approval lock for job {job_id}")

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
