"""Approval service for PII-flagged document review workflow.

Handles token validation, approval/denial decision processing,
and routing to document processing or cleanup.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from ..shared.constants.queues import APPROVAL_TIMEOUT_KEY
from ..shared.constants.statuses import (
    STATUS_DENIED,
    STATUS_PROCESSING,
    STATUS_PROCESSING_QUEUED,
)
from .cleanup_service import CleanupService
from .job_service import JobService
from .queue_service import QueueService

if TYPE_CHECKING:
    from .s3_url_service import S3URLService
    from .storage_service import StorageService

logger = logging.getLogger(__name__)


class ApprovalService:
    """Service for managing approval workflow decisions."""

    def __init__(
        self,
        redis_client: Any,
        s3_client: Any | None,
        job_service: JobService,
        queue_service: QueueService,
        storage_service: "StorageService | None" = None,
        s3_url_service: "S3URLService | None" = None,
    ):
        """Initialize approval service with dependencies.

        Args:
            redis_client: Redis async client instance
            s3_client: Boto3 S3 client instance (optional, lazy-loaded for denial path)
            job_service: Job status management service
            queue_service: Redis queue operations service
            storage_service: S3 storage operations (required for processing trigger)
            s3_url_service: S3 URL generation service (required for processing trigger)
        """
        self.redis = redis_client
        self.job_service = job_service
        self.queue_service = queue_service
        self.storage_service = storage_service
        self.s3_url_service = s3_url_service
        self._s3_client = s3_client
        self._cleanup_service: CleanupService | None = None

    @property
    def cleanup_service(self) -> CleanupService:
        """Lazy-load CleanupService only when needed (denial path).

        Returns:
            CleanupService instance for S3 file cleanup
        """
        if self._cleanup_service is None:
            if self._s3_client is None:
                # Get S3 client if not provided
                from ..dependencies import _get_s3_client_singleton

                self._s3_client = _get_s3_client_singleton()
            self._cleanup_service = CleanupService(self._s3_client)
        return self._cleanup_service

    async def validate_approval_token(self, token: str) -> dict[str, Any] | None:
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

                # Reject naive datetimes - they should never occur in production
                # All timestamps must be timezone-aware (created with timezone.utc)
                if expires_at.tzinfo is None:
                    logger.error(
                        f"Naive datetime detected for job {job_id}: {expires_at_str}. "
                        "All timestamps must be timezone-aware. Rejecting approval token."
                    )
                    return None

            except (ValueError, AttributeError):
                logger.error(f"Invalid expiration timestamp for job {job_id}: {expires_at_str}", exc_info=True)
                return None

            now = datetime.now(UTC)

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
        self, job_id: str, decision: Literal["approved", "denied"], justification: str, reviewed_by: str
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
        reviewed_at = datetime.now(UTC).isoformat()
        decision_metadata = {
            "decision": decision,
            "justification": justification,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
        }

        if decision == "approved":
            await self._process_approval(job_id, s3_key, decision_metadata)
        else:
            await self._process_denial(job_id, s3_key, decision_metadata)

    async def _process_approval(self, job_id: str, s3_key: str, decision_metadata: dict[str, Any]) -> None:
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
                ex=60,  # Expire after 60 seconds (safety cleanup)
            )

            if not lock_acquired:
                # Another approval is already processing or completed
                logger.info(
                    f"Job {job_id} approval already in progress or completed - ignoring duplicate approval attempt"
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
                    logger.warning(f"Job {job_id} in unexpected status '{current_status}' during approval")
                    raise ValueError(f"Job {job_id} cannot be approved from status '{current_status}'")

                # Update job status
                await self.job_service.update_job_status(job_id, STATUS_PROCESSING, approval_decision=decision_metadata)
                logger.info(f"Job {job_id} status updated to processing")

                # Trigger document processing directly instead of queueing
                filename = current_job.get("original_filename", "document.pdf")
                review_mode = current_job.get("review_mode", "auto")

                # Import here to avoid circular imports
                from .document_processing_service import DocumentProcessingService

                if self.storage_service is None or self.s3_url_service is None:
                    logger.error(f"Storage services not configured for job {job_id}")
                    await self.job_service.update_job_status(
                        job_id,
                        "failed",
                        error="Storage services not configured for processing",
                    )
                    return

                # Create processing service
                processing_service = DocumentProcessingService(
                    redis_client=self.redis,
                    storage_service=self.storage_service,
                    s3_url_service=self.s3_url_service,
                )

                # Trigger processing in background (non-blocking)
                asyncio.create_task(
                    processing_service.process_document(
                        job_id=job_id,
                        s3_key=s3_key,
                        filename=filename,
                        review_mode=review_mode,
                    )
                )
                logger.info(f"Job {job_id} approved - processing started")

            finally:
                # Release lock after processing (or on error)
                await self.redis.delete(lock_key)
                logger.debug(f"Released approval lock for job {job_id}")

        except Exception as e:
            logger.error(f"Failed to process approval for job {job_id}: {str(e)}", exc_info=True)
            raise

    async def _process_denial(self, job_id: str, s3_key: str, decision_metadata: dict[str, Any]) -> None:
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
            await self.job_service.update_job_status(job_id, STATUS_DENIED, denial_decision=decision_metadata)
            logger.info(f"Job {job_id} status updated to denied")

        except Exception as e:
            logger.error(f"Failed to process denial for job {job_id}: {str(e)}", exc_info=True)
            raise

    # -------------------------------------------------------------------------
    # Instant Response Methods (for reduced API latency)
    # -------------------------------------------------------------------------

    async def quick_approve(self, job_id: str) -> None:
        """Minimal approval - update status and return immediately.

        Sets job to 'processing_queued' status for instant user feedback.
        Full processing (lock, enqueue, status update) happens in background.

        Args:
            job_id: Job identifier

        Example:
            >>> await service.quick_approve("abc-123")
            # Returns immediately, status = "processing_queued"
        """
        await self.job_service.update_job_status(
            job_id,
            STATUS_PROCESSING_QUEUED,
            approved_at=datetime.now(UTC).isoformat(),
        )
        logger.info(f"Job {job_id} quick-approved, status set to processing_queued")

    async def quick_deny(self, job_id: str) -> None:
        """Minimal denial - update status and return immediately.

        Sets job to 'denied' status for instant user feedback.
        S3 cleanup and metadata storage happen in background.

        Args:
            job_id: Job identifier

        Example:
            >>> await service.quick_deny("abc-123")
            # Returns immediately, status = "denied"
        """
        await self.job_service.update_job_status(
            job_id,
            STATUS_DENIED,
            denied_at=datetime.now(UTC).isoformat(),
        )
        logger.info(f"Job {job_id} quick-denied, status set to denied")

    async def process_approval_background(
        self,
        job_id: str,
        s3_key: str,
        justification: str,
        reviewed_by: str,
    ) -> None:
        """Background processing after quick approval response.

        Handles locking, enqueueing, and final status update.
        Called via FastAPI BackgroundTasks after response sent.

        Args:
            job_id: Job identifier
            s3_key: S3 object key for the document
            justification: Reviewer's justification for approval
            reviewed_by: Reviewer identifier (email or user ID)
        """
        try:
            # Remove from timeout tracking
            try:
                await self.redis.zrem(APPROVAL_TIMEOUT_KEY, job_id)
                logger.debug(f"Removed job {job_id} from approval timeout tracking")
            except Exception as e:
                logger.warning(f"Failed to remove job {job_id} from timeout tracking: {e}")
                # Continue - not critical

            # Acquire atomic lock to prevent duplicates
            lock_key = f"eq-pdf:approval-lock:{job_id}"
            lock_acquired = await self.redis.set(lock_key, "processing", nx=True, ex=60)

            if not lock_acquired:
                logger.info(f"Job {job_id} approval already in progress - skipping background")
                return

            try:
                # Double-check status
                current_job = await self.job_service.get_job(job_id)
                if not current_job:
                    logger.warning(f"Job {job_id} disappeared during background approval")
                    return

                current_status = current_job.get("status")
                if current_status not in [STATUS_PROCESSING_QUEUED, "awaiting_approval"]:
                    logger.info(f"Job {job_id} in status '{current_status}' - skipping background")
                    return

                # Update to full processing status with decision metadata
                decision_metadata = {
                    "decision": "approved",
                    "justification": justification,
                    "reviewed_by": reviewed_by,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                }
                await self.job_service.update_job_status(
                    job_id,
                    STATUS_PROCESSING,
                    approval_decision=decision_metadata,
                )

                # Trigger document processing directly instead of queueing
                filename = current_job.get("original_filename", "document.pdf")
                review_mode = current_job.get("review_mode", "auto")

                # Import here to avoid circular imports
                from .document_processing_service import DocumentProcessingService

                if self.storage_service is None or self.s3_url_service is None:
                    logger.error(f"Storage services not configured for job {job_id}")
                    await self.job_service.update_job_status(
                        job_id,
                        "failed",
                        error="Storage services not configured for processing",
                    )
                    return

                # Create processing service
                processing_service = DocumentProcessingService(
                    redis_client=self.redis,
                    storage_service=self.storage_service,
                    s3_url_service=self.s3_url_service,
                )

                # Trigger processing in background (non-blocking)
                asyncio.create_task(
                    processing_service.process_document(
                        job_id=job_id,
                        s3_key=s3_key,
                        filename=filename,
                        review_mode=review_mode,
                    )
                )
                logger.info(f"Job {job_id} background approval complete - processing started")

            finally:
                await self.redis.delete(lock_key)
                logger.debug(f"Released approval lock for job {job_id}")

        except Exception as e:
            logger.error(f"Background approval failed for {job_id}: {e}", exc_info=True)
            # Job remains in processing_queued - timeout worker can handle cleanup

    async def process_denial_background(
        self,
        job_id: str,
        s3_key: str,
        justification: str,
        reviewed_by: str,
    ) -> None:
        """Background processing after quick denial response.

        Handles S3 cleanup and stores decision metadata.
        Called via FastAPI BackgroundTasks after response sent.

        Args:
            job_id: Job identifier
            s3_key: S3 object key for the document
            justification: Reviewer's justification for denial
            reviewed_by: Reviewer identifier (email or user ID)
        """
        try:
            # Remove from timeout tracking
            try:
                await self.redis.zrem(APPROVAL_TIMEOUT_KEY, job_id)
                logger.debug(f"Removed job {job_id} from approval timeout tracking")
            except Exception as e:
                logger.warning(f"Failed to remove job {job_id} from timeout tracking: {e}")
                # Continue - not critical

            # Cleanup S3 files (best-effort)
            cleanup_success = await self.cleanup_service.cleanup_job_files(s3_key)
            if cleanup_success:
                logger.info(f"Job {job_id} S3 files cleaned up")
            else:
                logger.warning(f"Job {job_id} S3 cleanup failed (non-critical)")

            # Store decision metadata (job already marked as denied)
            decision_metadata = {
                "decision": "denied",
                "justification": justification,
                "reviewed_by": reviewed_by,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
            await self.job_service.update_job_status(
                job_id,
                STATUS_DENIED,
                denial_decision=decision_metadata,
            )
            logger.info(f"Job {job_id} background denial complete")

        except Exception as e:
            logger.error(f"Background denial failed for {job_id}: {e}", exc_info=True)
            # Job already marked as denied - cleanup can be retried later by timeout worker
