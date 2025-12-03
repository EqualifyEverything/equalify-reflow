"""PII detection service orchestration."""

import logging
from datetime import UTC, datetime, timedelta

from ..shared.constants.queues import APPROVAL_QUEUE, PROCESSING_QUEUE
from ..shared.constants.statuses import STATUS_AWAITING_APPROVAL, STATUS_FAILED, STATUS_PII_SCANNING, STATUS_PROCESSING
from ..shared.models.pii import PIIFinding
from ..shared.models.queue import ApprovalQueuePayload, PIIQueuePayload, ProcessingQueuePayload
from ..utils.retry_helpers import retry_with_backoff
from ..utils.token_generator import generate_secure_token
from .job_service import JobService
from .pdf_extractor import PDFExtractionError, extract_pdf_text
from .pii_analyzer import get_pii_analyzer
from .queue_service import QueueService
from .storage_service import StorageService

logger = logging.getLogger(__name__)

# Configuration
APPROVAL_TIMEOUT_HOURS = 4
MAX_RETRY_ATTEMPTS = 1


class PIIDetectionService:
    """Orchestrates PII detection workflow.

    Coordinates PDF download, text extraction, PII scanning,
    and routing to approval or processing queues.
    """

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService
    ):
        """Initialize PII detection service.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
        """
        self.storage = storage_service
        self.queue = queue_service
        self.jobs = job_service
        self.pii_analyzer = get_pii_analyzer()

    async def process_pii_job(self, job: PIIQueuePayload, retry_count: int = 0) -> None:
        """Process a single PII detection job.

        Main orchestration method that:
        1. Downloads PDF from S3 (with retry on transient errors)
        2. Extracts text (with retry on transient errors)
        3. Runs PII analysis
        4. Routes based on findings (with retry on transient errors)

        All external service calls (S3, Redis) are wrapped with exponential
        backoff retry logic to handle transient network/service failures.

        Args:
            job: PII queue payload with job details
            retry_count: Current retry attempt (0-indexed, deprecated - kept for compatibility)

        Raises:
            Exception: On unrecoverable errors after retries
        """
        logger.info(f"Processing PII job {job.job_id}")

        try:
            # Update status to scanning (with retry for Redis failures)
            await retry_with_backoff(
                lambda: self.jobs.update_job_status(job.job_id, STATUS_PII_SCANNING),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to PII_SCANNING"
            )

            # Step 1: Download PDF from S3 (StorageService handles retries internally)
            pdf_content = await self.storage.download_temp_file(job.s3_key)
            logger.info(f"Downloaded PDF for job {job.job_id}: {len(pdf_content)} bytes")

            # Step 2: Extract text content (with retry on transient extraction errors)
            text_content = await retry_with_backoff(
                lambda: extract_pdf_text(pdf_content),
                max_attempts=MAX_RETRY_ATTEMPTS + 1,  # Maintain existing retry count for PDF extraction
                operation_name=f"Extract text from PDF for job {job.job_id}"
            )
            logger.info(f"Extracted {len(text_content)} characters from job {job.job_id}")

            # Step 3: Run PII analysis (synchronous, no network calls - no retry needed)
            pii_findings = self.pii_analyzer.analyze_text(text_content)
            logger.info(f"Found {len(pii_findings)} PII entities in job {job.job_id}")

            # Step 4: Route based on findings (with retry for queue/status operations)
            if pii_findings:
                await self._queue_for_approval_with_retry(job, pii_findings)
            else:
                await self._queue_for_processing_with_retry(job)

        except PDFExtractionError as e:
            # PDF extraction failed after retries - permanent failure
            logger.error(f"PDF extraction failed for job {job.job_id} after retries: {e}")
            error_msg = f"PDF extraction failed: {str(e)}"
            await retry_with_backoff(
                lambda: self.jobs.update_job_status(
                    job.job_id,
                    STATUS_FAILED,
                    error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to FAILED"
            )

        except Exception as e:
            # Unexpected error (after any retries)
            logger.error(f"PII processing failed for job {job.job_id}: {e}", exc_info=True)
            error_msg = f"PII scan error: {str(e)}"
            await retry_with_backoff(
                lambda: self.jobs.update_job_status(
                    job.job_id,
                    STATUS_FAILED,
                    error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to FAILED"
            )

    async def _queue_for_approval(self, job: PIIQueuePayload, findings: list[PIIFinding]) -> None:
        """Queue job for manual approval with PII details.

        NOTE: This method does not include retry logic internally.
        Use _queue_for_approval_with_retry() for automatic retries on transient failures.

        Args:
            job: Original PII queue payload
            findings: Detected PII entities
        """
        logger.info(f"Queueing job {job.job_id} for approval with {len(findings)} PII findings")

        # Generate secure approval token
        approval_token = generate_secure_token()
        expires_at = datetime.now(UTC) + timedelta(hours=APPROVAL_TIMEOUT_HOURS)

        # Create approval queue payload
        approval_payload = ApprovalQueuePayload(
            job_id=job.job_id,
            s3_key=job.s3_key,
            pii_findings=findings,
            approval_token=approval_token,
            expires_at=expires_at
        )

        # Push to approval queue
        await self.queue.enqueue(APPROVAL_QUEUE, approval_payload)

        # Update job status with PII findings
        await self.jobs.update_job_status(
            job.job_id,
            STATUS_AWAITING_APPROVAL,
            pii_findings=[f.model_dump() for f in findings],
            approval_token=approval_token,
            approval_expires_at=expires_at.isoformat()
        )

        # Add to timeout tracking so timeout worker can find it
        await self.queue.add_to_timeout_tracking(job.job_id, expires_at)

        # Store token-to-job mapping for O(1) lookup (expires with approval)
        await self.jobs.store_approval_token_mapping(
            approval_token,
            job.job_id,
            ttl_hours=APPROVAL_TIMEOUT_HOURS
        )

        logger.info(f"Job {job.job_id} queued for approval, token: {approval_token[:8]}...")

    async def _queue_for_approval_with_retry(self, job: PIIQueuePayload, findings: list[PIIFinding]) -> None:
        """Queue job for approval with retry logic for transient failures.

        Wraps _queue_for_approval with exponential backoff retry for:
        - Redis queue operations (enqueue)
        - Redis status updates
        - Redis timeout tracking

        Args:
            job: Original PII queue payload
            findings: Detected PII entities
        """
        await retry_with_backoff(
            lambda: self._queue_for_approval(job, findings),
            max_attempts=3,
            operation_name=f"Queue job {job.job_id} for approval"
        )

    async def _queue_for_processing(self, job: PIIQueuePayload) -> None:
        """Queue clean job directly for processing.

        NOTE: This method does not include retry logic internally.
        Use _queue_for_processing_with_retry() for automatic retries on transient failures.

        Args:
            job: Original PII queue payload
        """
        logger.info(f"Queueing job {job.job_id} for processing (no PII detected)")

        # Create processing queue payload
        processing_payload = ProcessingQueuePayload(
            job_id=job.job_id,
            s3_key=job.s3_key,
            approved_at=None  # No approval needed
        )

        # Push to processing queue
        await self.queue.enqueue(PROCESSING_QUEUE, processing_payload)

        # Update job status
        await self.jobs.update_job_status(job.job_id, STATUS_PROCESSING)

        logger.info(f"Job {job.job_id} queued for processing")

    async def _queue_for_processing_with_retry(self, job: PIIQueuePayload) -> None:
        """Queue job for processing with retry logic for transient failures.

        Wraps _queue_for_processing with exponential backoff retry for:
        - Redis queue operations (enqueue)
        - Redis status updates

        Args:
            job: Original PII queue payload
        """
        await retry_with_backoff(
            lambda: self._queue_for_processing(job),
            max_attempts=3,
            operation_name=f"Queue job {job.job_id} for processing"
        )
