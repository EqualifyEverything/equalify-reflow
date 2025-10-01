"""PII detection service orchestration."""

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from ..shared.models.queue import PIIQueuePayload, ApprovalQueuePayload, ProcessingQueuePayload
from ..shared.models.pii import PIIFinding
from ..shared.constants.statuses import (
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
    STATUS_FAILED
)
from ..shared.constants.queues import APPROVAL_QUEUE, PROCESSING_QUEUE
from ..utils.token_generator import generate_secure_token
from .storage_service import StorageService
from .queue_service import QueueService
from .job_service import JobService
from .pdf_extractor import extract_pdf_text, PDFExtractionError
from .pii_analyzer import get_pii_analyzer

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
        1. Downloads PDF from S3
        2. Extracts text
        3. Runs PII analysis
        4. Routes based on findings

        Args:
            job: PII queue payload with job details
            retry_count: Current retry attempt (0-indexed)

        Raises:
            Exception: On unrecoverable errors after retries
        """
        logger.info(f"Processing PII job {job.job_id} (attempt {retry_count + 1})")

        try:
            # Update status to scanning
            await self.jobs.update_job_status(job.job_id, STATUS_PII_SCANNING)

            # Step 1: Download PDF from S3
            pdf_content = await self.storage.download_temp_file(job.s3_key)
            logger.info(f"Downloaded PDF for job {job.job_id}: {len(pdf_content)} bytes")

            # Step 2: Extract text content
            text_content = await extract_pdf_text(pdf_content)
            logger.info(f"Extracted {len(text_content)} characters from job {job.job_id}")

            # Step 3: Run PII analysis
            pii_findings = self.pii_analyzer.analyze_text(text_content)
            logger.info(f"Found {len(pii_findings)} PII entities in job {job.job_id}")

            # Step 4: Route based on findings
            if pii_findings:
                await self._queue_for_approval(job, pii_findings)
            else:
                await self._queue_for_processing(job)

        except PDFExtractionError as e:
            # PDF extraction failed - retry once
            if retry_count < MAX_RETRY_ATTEMPTS:
                logger.warning(f"PDF extraction failed for job {job.job_id}, retrying: {e}")
                await self.process_pii_job(job, retry_count=retry_count + 1)
            else:
                logger.error(f"PDF extraction failed for job {job.job_id} after retries: {e}")
                await self.jobs.update_job_status(
                    job.job_id,
                    STATUS_FAILED,
                    error=f"PDF extraction failed: {str(e)}"
                )

        except Exception as e:
            # Unexpected error
            logger.error(f"PII processing failed for job {job.job_id}: {e}", exc_info=True)
            await self.jobs.update_job_status(
                job.job_id,
                STATUS_FAILED,
                error=f"PII scan error: {str(e)}"
            )

    async def _queue_for_approval(self, job: PIIQueuePayload, findings: List[PIIFinding]) -> None:
        """Queue job for manual approval with PII details.

        Args:
            job: Original PII queue payload
            findings: Detected PII entities
        """
        logger.info(f"Queueing job {job.job_id} for approval with {len(findings)} PII findings")

        # Generate secure approval token
        approval_token = generate_secure_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=APPROVAL_TIMEOUT_HOURS)

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

        logger.info(f"Job {job.job_id} queued for approval, token: {approval_token[:8]}...")

    async def _queue_for_processing(self, job: PIIQueuePayload) -> None:
        """Queue clean job directly for processing.

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
