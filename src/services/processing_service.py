"""Main processing service orchestrating PDF conversion and AI enhancement."""

import logging
import time
from datetime import datetime
from typing import Optional

from ..shared.models.queue import ProcessingQueuePayload
from ..shared.models.processing import ProcessingResult
from ..services.storage_service import StorageService
from ..services.queue_service import QueueService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.ai_enhancement_service import AIEnhancementService, PageProcessingError
from ..utils.confidence_scoring import calculate_document_confidence
from ..utils.retry_helpers import retry_with_backoff
from ..config import settings

logger = logging.getLogger(__name__)


class ProcessingService:
    """Main service for orchestrating PDF-to-accessible-markdown conversion."""

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService,
        pdf_converter: Optional[PDFConverter] = None,
        ai_enhancement: Optional[AIEnhancementService] = None,
    ):
        """Initialize processing service with dependencies.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            pdf_converter: Optional PDF converter (created if not provided)
            ai_enhancement: Optional AI service (created if not provided)
        """
        self.storage = storage_service
        self.queue = queue_service
        self.job = job_service
        self.pdf_converter = pdf_converter or PDFConverter()
        self.ai_enhancement = ai_enhancement or AIEnhancementService(
            max_concurrent_pages=settings.max_concurrent_pages
        )

        logger.info("Processing service initialized")

    async def process_document(
        self, job: ProcessingQueuePayload
    ) -> ProcessingResult:
        """Main processing pipeline for PDF documents.

        Pipeline steps:
        1. Download PDF from S3
        2. Convert PDF with Docling (extract markdown + page images)
        3. Process pages concurrently with AI (max 5 at once)
        4. Combine improved markdown
        5. Calculate confidence metrics
        6. Upload results to S3 with versioning
        7. Update job status

        Args:
            job: Processing queue payload with job_id and s3_key

        Returns:
            ProcessingResult with markdown URL and confidence metrics

        Raises:
            ValueError: If PDF download or conversion fails
            PageProcessingError: If AI processing fails for any page
        """
        start_time = time.time()
        logger.info(f"Starting processing for job {job.job_id}")

        try:
            # Step 1: Update job status to processing (with retry for Redis failures)
            await retry_with_backoff(
                lambda: self.job.update_job_status(job.job_id, "processing"),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to processing"
            )

            # Step 2: Download PDF from S3 (with retry on network/throttling errors)
            logger.info(f"Downloading PDF from s3://{settings.s3_temp_bucket}/{job.s3_key}")
            pdf_content = await retry_with_backoff(
                lambda: self.storage.download_temp_file(s3_key=job.s3_key),
                max_attempts=3,
                operation_name=f"Download PDF from S3 for job {job.job_id}"
            )
            logger.info(f"Downloaded {len(pdf_content)} bytes")

            # Step 3: Convert PDF with Docling (markdown + page images)
            # Note: PDF conversion is compute-intensive, not network-dependent
            # Retries handled internally by pdf_converter if needed
            logger.info("Converting PDF with Docling...")
            conversion_result = await self.pdf_converter.convert_with_page_images(
                pdf_content
            )

            # CRITICAL: Verify page images were generated
            if not conversion_result.has_page_images:
                raise RuntimeError(
                    "Docling failed to generate page images. "
                    "Cannot proceed without visual comparison capability."
                )

            logger.info(
                f"PDF converted: {conversion_result.total_pages} pages, "
                f"{len(conversion_result.full_markdown)} chars markdown"
            )

            # Step 4: Process pages concurrently with AI (max 5 at once)
            logger.info(
                f"Processing {len(conversion_result.pages)} pages with AI "
                f"(max {settings.max_concurrent_pages} concurrent)"
            )

            try:
                improvement_results = (
                    await self.ai_enhancement.process_pages_concurrently(
                        conversion_result.pages
                    )
                )
            except PageProcessingError as e:
                # Page processing failed after retries
                error_msg = (
                    f"AI processing failed for page {e.page_num} "
                    f"after {settings.page_retry_attempts} attempts: "
                    f"{e.original_error}"
                )
                logger.error(error_msg)
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id, "failed", error=error_msg
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} to failed (AI processing error)"
                )
                raise ValueError(error_msg) from e

            # Step 5: Combine improved markdown
            final_markdown = self.ai_enhancement.combine_page_markdown(
                improvement_results, conversion_result.pages
            )

            # Step 6: Calculate confidence metrics
            page_scores = [r.confidence_score for r in improvement_results]
            confidence_score, confidence_level = calculate_document_confidence(
                page_scores
            )

            logger.info(
                f"Document confidence: {confidence_score:.2f} ({confidence_level})"
            )

            # Step 7: Upload results to S3 with versioning (with retry on network errors)
            logger.info(f"Uploading markdown to S3 results bucket")
            result_url = await retry_with_backoff(
                lambda: self.storage.upload_result(
                    job_id=job.job_id,
                    content=final_markdown.encode("utf-8"),
                    format="md"
                ),
                max_attempts=3,
                operation_name=f"Upload result to S3 for job {job.job_id}"
            )

            # Step 8: Update job status with metadata (with retry for Redis failures)
            processing_time = int(time.time() - start_time)

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id,
                    "completed",
                    metadata={
                        "markdown_url": result_url,
                        "confidence_score": confidence_score,
                        "confidence_level": confidence_level,
                        "processing_time_seconds": processing_time,
                        "total_pages": conversion_result.total_pages,
                    },
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to completed"
            )

            logger.info(
                f"Job {job.job_id} completed successfully in {processing_time}s "
                f"(confidence: {confidence_score:.2f} - {confidence_level})"
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=result_url,
                confidence_score=confidence_score,
                processing_time_seconds=processing_time,
                error_message=None,
            )

        except PageProcessingError:
            # Already handled above, re-raise
            raise

        except Exception as e:
            # Unexpected error (after any retries)
            error_msg = f"Processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} failed: {error_msg}", exc_info=True)

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "failed", error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to failed (unexpected error)"
            )

            # Return failed result
            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=None,
                confidence_score=None,
                processing_time_seconds=int(time.time() - start_time),
                error_message=error_msg,
            )
