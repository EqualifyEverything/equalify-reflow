"""Main processing service orchestrating PDF conversion and AI enhancement."""

import logging
import time

from ..agents.full_document_agent import FullDocumentAgent
from ..config import settings
from ..services.document_context_service import DocumentContextService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.queue_service import QueueService
from ..services.storage_service import StorageService
from ..shared.models.processing import ProcessingResult
from ..shared.models.queue import ProcessingQueuePayload
from ..utils.retry_helpers import retry_with_backoff

logger = logging.getLogger(__name__)


class ProcessingService:
    """Main service for orchestrating PDF-to-accessible-markdown conversion."""

    def __init__(
        self,
        storage_service: StorageService,
        queue_service: QueueService,
        job_service: JobService,
        redis_client=None,
        pdf_converter: PDFConverter | None = None,
        document_context_service: DocumentContextService | None = None,
    ):
        """Initialize processing service with dependencies.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            redis_client: Redis client for token storage
            pdf_converter: Optional PDF converter (created if not provided)
            document_context_service: Optional context service for direct extraction
        """
        self.storage = storage_service
        self.queue = queue_service
        self.job = job_service
        self.redis = redis_client
        self.pdf_converter = pdf_converter or PDFConverter()
        self.context_service = document_context_service or DocumentContextService()

        logger.info("Processing service initialized")

    async def process_document(
        self,
        job: ProcessingQueuePayload,
    ) -> ProcessingResult:
        """Process PDF using two-phase full-document extraction.

        This approach loads ALL page images into context and processes in two phases:
        1. Phase 1: Analyze structure to build heading tree
        2. Phase 2: Transcribe document guided by heading tree

        This avoids the duplicate content issues that occur with per-page processing
        and sliding context windows on multi-column documents.

        Args:
            job: Processing queue payload with job_id and s3_key

        Returns:
            ProcessingResult with markdown URL and confidence metrics
        """
        start_time = time.time()
        logger.info(f"Starting processing for job {job.job_id}")

        try:
            # Step 1: Update job status to processing
            await retry_with_backoff(
                lambda: self.job.update_job_status(job.job_id, "processing"),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to processing",
            )

            # Step 2: Download PDF from S3
            logger.info(
                f"Downloading PDF from s3://{settings.s3_temp_bucket}/{job.s3_key}"
            )
            pdf_content = await self.storage.download_temp_file(s3_key=job.s3_key)
            logger.info(f"Downloaded {len(pdf_content)} bytes")

            # Step 3: Convert PDF with Docling (we need page images)
            logger.info("Converting PDF with Docling for page images...")
            conversion_result = await self.pdf_converter.convert_with_page_images(
                pdf_content
            )

            if not conversion_result.has_page_images:
                raise RuntimeError(
                    "Docling failed to generate page images. "
                    "Cannot proceed without visual content."
                )

            logger.info(
                f"PDF converted: {conversion_result.total_pages} pages with images"
            )

            # Step 4: Process with FullDocumentAgent (two-phase approach)
            full_doc_agent = FullDocumentAgent()
            pages = conversion_result.pages

            logger.info(
                f"Processing {len(pages)}-page document with two-phase extraction..."
            )

            # This will:
            # - Phase 1: Analyze structure → HeadingTree
            # - Phase 2: Transcribe with heading guidance → Markdown
            full_markdown, heading_tree, total_usage = await full_doc_agent.process(
                pages
            )

            logger.info(
                f"Two-phase extraction complete: "
                f"{len(full_markdown)} chars, "
                f"{len(heading_tree.sections)} sections, "
                f"layout={heading_tree.layout_type}, "
                f"est. cost=${total_usage.estimated_cost_cents/100:.4f}"
            )

            # Step 5: Calculate confidence level from heading tree
            avg_confidence = heading_tree.confidence

            if avg_confidence >= 0.9:
                confidence_level = "high"
            elif avg_confidence >= 0.7:
                confidence_level = "medium"
            else:
                confidence_level = "low"

            # Step 6: Upload result to S3
            logger.info("Uploading extracted markdown to S3")
            result_url = await self.storage.upload_result(
                job_id=job.job_id,
                content=full_markdown.encode("utf-8"),
                format="md",
            )

            # Step 7: Update job status to completed
            processing_time = int(time.time() - start_time)

            update_fields = {
                "markdown_url": result_url,
                "confidence_score": avg_confidence,
                "confidence_level": confidence_level,
                "processing_time_seconds": processing_time,
                "total_pages": conversion_result.total_pages,
                "llm_cost_cents": total_usage.estimated_cost_cents,
                "llm_input_tokens": total_usage.input_tokens,
                "llm_output_tokens": total_usage.output_tokens,
                "llm_total_tokens": total_usage.total_tokens,
                "extraction_method": "two_phase",
                "layout_type": heading_tree.layout_type,
                "section_count": len(heading_tree.sections),
            }

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "completed", **update_fields
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to completed",
            )

            logger.info(
                f"Job {job.job_id} completed in {processing_time}s "
                f"(confidence: {avg_confidence:.2f}, "
                f"est. cost: ${total_usage.estimated_cost_cents/100:.4f})"
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=result_url,
                confidence_score=avg_confidence,
                processing_time_seconds=processing_time,
                error_message=None,
            )

        except ValueError as e:
            # Handle document too large error from FullDocumentAgent
            error_msg = f"Processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} failed: {error_msg}")

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "failed", error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to failed",
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=None,
                confidence_score=None,
                processing_time_seconds=int(time.time() - start_time),
                error_message=error_msg,
            )

        except Exception as e:
            error_msg = f"Processing failed: {str(e)}"
            logger.error(f"Job {job.job_id} failed: {error_msg}", exc_info=True)

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id, "failed", error=error_msg
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} to failed",
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=None,
                confidence_score=None,
                processing_time_seconds=int(time.time() - start_time),
                error_message=error_msg,
            )
