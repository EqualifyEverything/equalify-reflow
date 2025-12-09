"""Correction approval service for text correction review workflow.

Handles token validation, approval/rejection decision processing,
and routing corrected or original markdown to final storage.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from ..shared.constants.statuses import STATUS_COMPLETED
from ..utils.retry_helpers import retry_with_backoff_for_sync_func
from ..utils.token_generator import generate_secure_token
from .job_service import JobService
from .storage_service import StorageService

logger = logging.getLogger(__name__)


class CorrectionApprovalService:
    """Service for managing text correction approval workflow decisions."""

    def __init__(
        self,
        redis_client: Any,
        job_service: JobService,
        storage_service: StorageService
    ):
        """Initialize correction approval service with dependencies.

        Args:
            redis_client: Redis async client instance
            job_service: Job status management service
            storage_service: S3 storage operations service
        """
        self.redis = redis_client
        self.job_service = job_service
        self.storage = storage_service

    async def generate_correction_approval_token(self, job_id: str) -> str:
        """Generate secure correction approval token for a job.

        Creates a cryptographically secure token, stores the mapping in Redis
        with a 4-hour TTL, and updates the job with the token.

        Args:
            job_id: Job identifier

        Returns:
            Secure approval token string

        Raises:
            Exception: If token storage fails

        Example:
            >>> service = CorrectionApprovalService(redis, job_service, storage)
            >>> token = await service.generate_correction_approval_token("job-123")
            >>> print(f"Token: {token[:8]}...")
        """
        try:
            # Generate secure 256-bit token
            token = generate_secure_token()

            # Store token-to-job-id mapping in Redis (4-hour TTL)
            token_key = f"eq-pdf:correction-token:{token}"
            ttl_seconds = 4 * 3600  # 4 hours
            await self.redis.set(token_key, job_id, ex=ttl_seconds)

            logger.debug(
                f"Stored correction approval token mapping: {token[:8]}... → {job_id}"
            )

            # Store token in job data for lookup
            await self.job_service.update_job(
                job_id,
                correction_approval_token=token
            )

            logger.info(f"Generated correction approval token for job {job_id}")
            return token

        except Exception as e:
            logger.error(
                f"Failed to generate correction approval token for job {job_id}: {str(e)}",
                exc_info=True
            )
            raise Exception(f"Failed to generate correction approval token: {str(e)}")

    async def validate_correction_approval_token(self, token: str) -> str:
        """Validate correction approval token and return associated job_id.

        Looks up the token in Redis and verifies it hasn't expired.

        Args:
            token: Correction approval token from URL

        Returns:
            Job ID if token is valid

        Raises:
            HTTPException: If token is invalid or expired

        Example:
            >>> job_id = await service.validate_correction_approval_token("abc123...")
            >>> print(f"Valid token for job: {job_id}")
        """
        try:
            # O(1) lookup in Redis
            token_key = f"eq-pdf:correction-token:{token}"
            job_id = await self.redis.get(token_key)

            if not job_id:
                logger.debug("Correction approval token not found or expired")
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or expired correction approval token"
                )

            # Decode if bytes
            if isinstance(job_id, bytes):
                job_id = job_id.decode('utf-8')

            # Verify job still exists
            job = await self.job_service.get_job(job_id)
            if not job:
                logger.warning(f"Job {job_id} not found for valid token")
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=404,
                    detail="Job not found"
                )

            logger.info(f"Valid correction approval token for job {job_id}")
            return job_id

        except Exception as e:
            if hasattr(e, 'status_code'):
                # Re-raise HTTPException as-is
                raise
            logger.error(
                f"Error validating correction approval token: {str(e)}",
                exc_info=True
            )
            from fastapi import HTTPException
            raise HTTPException(
                status_code=500,
                detail="Failed to validate correction approval token"
            )

    async def process_correction_approval_decision(
        self,
        job_id: str,
        decision: dict
    ) -> dict:
        """Process correction approval or rejection decision.

        Uses atomic lock to prevent duplicate processing. Based on decision:
        - Approved: Uploads CORRECTED markdown to final location
        - Rejected: Uploads ORIGINAL markdown to final location

        Both outcomes result in STATUS_COMPLETED with appropriate metadata.

        Args:
            job_id: Job identifier
            decision: Decision dict with keys:
                - decision: "approved" or "rejected"
                - reviewed_by: Reviewer identifier (email or user ID)
                - justification: Explanation for decision

        Returns:
            Updated job data dictionary

        Raises:
            ValueError: If job not found or in invalid state
            Exception: If processing fails

        Example:
            >>> result = await service.process_correction_approval_decision(
            ...     job_id="abc-123",
            ...     decision={
            ...         "decision": "approved",
            ...         "reviewed_by": "instructor@uic.edu",
            ...         "justification": "Text corrections improve accessibility"
            ...     }
            ... )
        """
        # Validate decision structure
        decision_type = decision.get("decision")
        reviewed_by = decision.get("reviewed_by")
        justification = decision.get("justification")

        if decision_type not in ["approved", "rejected"]:
            raise ValueError("Decision must be 'approved' or 'rejected'")
        if not reviewed_by:
            raise ValueError("reviewed_by is required")
        if not justification:
            raise ValueError("justification is required")

        # Get job data
        job = await self.job_service.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # ATOMIC LOCK: Use Redis SETNX to claim exclusive processing
        lock_key = f"eq-pdf:correction-approval-lock:{job_id}"
        lock_acquired = await self.redis.set(
            lock_key,
            "processing",
            nx=True,  # Only set if not exists (atomic)
            ex=60  # Expire after 60 seconds (safety cleanup)
        )

        if not lock_acquired:
            logger.info(
                f"Job {job_id} correction approval already in progress - "
                "ignoring duplicate attempt"
            )
            return job

        try:
            # Double-check job status after acquiring lock
            current_job = await self.job_service.get_job(job_id)
            if not current_job:
                logger.warning(f"Job {job_id} disappeared during correction approval")
                raise ValueError(f"Job {job_id} not found")

            current_status = current_job.get("status")

            # Only allow approval from awaiting_correction_approval status
            if current_status == STATUS_COMPLETED:
                logger.info(f"Job {job_id} already completed - duplicate approval")
                return current_job

            if current_status != "awaiting_correction_approval":
                logger.warning(
                    f"Job {job_id} in unexpected status '{current_status}' "
                    "during correction approval"
                )
                raise ValueError(
                    f"Job {job_id} cannot be approved from status '{current_status}'"
                )

            # Store decision metadata
            reviewed_at = datetime.now(UTC).isoformat()
            decision_metadata = {
                "correction_decision": decision_type,
                "justification": justification,
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at
            }

            # Process based on decision
            if decision_type == "approved":
                await self._process_approval(job_id, current_job, decision_metadata)
            else:
                await self._process_rejection(job_id, current_job, decision_metadata)

            # Remove from timeout tracking
            timeout_key = "eq-pdf:timeouts:correction-approval"
            try:
                await self.redis.zrem(timeout_key, job_id)
                logger.info(f"Removed job {job_id} from correction approval timeout tracking")
            except Exception as e:
                logger.error(
                    f"Failed to remove job {job_id} from timeout tracking: {str(e)}"
                )
                # Continue processing - not critical

            # Delete correction approval token
            token = current_job.get("correction_approval_token")
            if token:
                try:
                    token_key = f"eq-pdf:correction-token:{token}"
                    await self.redis.delete(token_key)
                    logger.debug(f"Deleted correction approval token for job {job_id}")
                except Exception as e:
                    logger.error(
                        f"Failed to delete correction token for job {job_id}: {str(e)}"
                    )
                    # Continue - not critical

            # Return updated job
            return await self.job_service.get_job(job_id)

        finally:
            # Release lock after processing (or on error)
            await self.redis.delete(lock_key)
            logger.debug(f"Released correction approval lock for job {job_id}")

    async def _process_approval(
        self,
        job_id: str,
        job: dict,
        decision_metadata: dict
    ) -> None:
        """Handle approved decision - upload CORRECTED markdown to final location.

        Downloads corrected markdown from awaiting-approval location and re-uploads
        to final location ({job_id}.md), then stores decision fields at top level.

        Args:
            job_id: Job identifier
            job: Current job data
            decision_metadata: Decision details for audit trail
        """
        try:
            # Get corrected markdown key (awaiting-approval version)
            corrected_key = job.get("corrected_markdown_key")
            if not corrected_key:
                raise ValueError(f"Job {job_id} missing corrected_markdown_key")

            logger.info(f"Downloading corrected markdown for job {job_id} (key: {corrected_key})")

            # Download corrected markdown from results bucket
            response = await retry_with_backoff_for_sync_func(
                lambda: self.storage.s3_client.get_object(
                    Bucket=self.storage.results_bucket,
                    Key=corrected_key
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"download corrected {corrected_key}"
            )
            corrected_content = response['Body'].read()
            logger.info(f"Downloaded corrected markdown ({len(corrected_content)} bytes)")

            # Upload to final location (overwrites awaiting-approval version)
            final_key = f"{job_id}.md"
            logger.info(f"Uploading corrected markdown to final location ({final_key})")
            await self.storage.upload_result(
                job_id=job_id,
                content=corrected_content,
                format="md"
            )

            # Update status to completed with final markdown key and decision fields at top level
            await self.job_service.update_job_status(
                job_id,
                STATUS_COMPLETED,
                final_markdown_key=final_key,
                correction_decision=decision_metadata["correction_decision"],
                correction_reviewed_by=decision_metadata["reviewed_by"],
                correction_reviewed_at=decision_metadata["reviewed_at"],
                correction_justification=decision_metadata["justification"]
            )

            logger.info(
                f"Job {job_id} correction approved - corrected markdown uploaded to {final_key}"
            )

        except Exception as e:
            logger.error(
                f"Failed to process correction approval for job {job_id}: {str(e)}",
                exc_info=True
            )
            raise

    async def _process_rejection(
        self,
        job_id: str,
        job: dict,
        decision_metadata: dict
    ) -> None:
        """Handle rejected decision - replace final markdown with ORIGINAL version.

        Downloads the original markdown and re-uploads it to the final location
        ({job_id}.md), then stores decision fields at top level.

        Args:
            job_id: Job identifier
            job: Current job data
            decision_metadata: Decision details for audit trail
        """
        try:
            # Get original markdown key
            original_key = job.get("original_markdown_key")
            if not original_key:
                raise ValueError(f"Job {job_id} missing original_markdown_key")

            logger.info(f"Downloading original markdown for job {job_id} (key: {original_key})")

            # Download original markdown from results bucket
            response = await retry_with_backoff_for_sync_func(
                lambda: self.storage.s3_client.get_object(
                    Bucket=self.storage.results_bucket,
                    Key=original_key
                ),
                max_attempts=3,
                base_delay=1.0,
                operation_name=f"download original {original_key}"
            )
            original_content = response['Body'].read()
            logger.info(f"Downloaded original markdown ({len(original_content)} bytes)")

            # Upload to final location
            final_key = f"{job_id}.md"
            logger.info(f"Uploading original markdown to final location ({final_key})")
            await self.storage.upload_result(
                job_id=job_id,
                content=original_content,
                format="md"
            )

            # Update status to completed with final markdown key and decision fields at top level
            await self.job_service.update_job_status(
                job_id,
                STATUS_COMPLETED,
                final_markdown_key=final_key,
                correction_decision=decision_metadata["correction_decision"],
                correction_reviewed_by=decision_metadata["reviewed_by"],
                correction_reviewed_at=decision_metadata["reviewed_at"],
                correction_justification=decision_metadata["justification"]
            )

            logger.info(
                f"Job {job_id} corrections rejected - original markdown uploaded to {final_key}"
            )

        except Exception as e:
            logger.error(
                f"Failed to process correction rejection for job {job_id}: {str(e)}",
                exc_info=True
            )
            raise
