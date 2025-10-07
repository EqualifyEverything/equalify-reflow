"""Main processing service orchestrating PDF conversion and AI enhancement."""

import logging
import time
import re
from io import BytesIO
from datetime import datetime
from typing import Optional, Dict, Any

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
                f"{len(conversion_result.full_markdown)} chars markdown, "
                f"{len(conversion_result.extracted_images)} embedded images"
            )

            # Step 4: Upload extracted images to S3 and build URL mapping
            image_metadata: Dict[str, Dict[str, Any]] = {}
            if conversion_result.extracted_images:
                logger.info(f"Uploading {len(conversion_result.extracted_images)} images to S3...")
                for img in conversion_result.extracted_images:
                    try:
                        # Convert PIL image to PNG bytes
                        img_bytes = self._image_to_bytes(img.pil_image)

                        # Generate filename: e.g., "picture-0.png", "table-1.png"
                        ref_index = img.ref_id.split('/')[-1]
                        image_name = f"{img.image_type}-{ref_index}.png"

                        # Upload to S3 with retry
                        image_url = await retry_with_backoff(
                            lambda: self.storage.upload_image(
                                job_id=job.job_id,
                                image_data=img_bytes,
                                image_name=image_name
                            ),
                            max_attempts=3,
                            operation_name=f"Upload image {image_name} for job {job.job_id}"
                        )

                        # Store metadata for later reference
                        image_metadata[img.ref_id] = {
                            "url": image_url,
                            "caption": img.caption,
                            "type": img.image_type,
                            "page": img.page_num
                        }
                        logger.debug(f"Uploaded {image_name} -> {image_url}")

                    except Exception as e:
                        logger.error(
                            f"Failed to upload image {img.ref_id}: {e}",
                            exc_info=True
                        )
                        # Continue processing other images even if one fails

                logger.info(
                    f"Successfully uploaded {len(image_metadata)}/{len(conversion_result.extracted_images)} images"
                )

            # Step 5: Replace image references in markdown with S3 URLs
            final_markdown = self._replace_image_references(
                conversion_result.full_markdown,
                image_metadata
            )

            # Step 6: AI enhancement TEMPORARILY DISABLED
            # TODO: Re-enable AI processing once agent configuration is finalized
            logger.warning(
                "AI enhancement temporarily disabled - forwarding raw Docling markdown"
            )

            # COMMENTED OUT: AI processing
            # try:
            #     improvement_results = (
            #         await self.ai_enhancement.process_pages_concurrently(
            #             conversion_result.pages
            #         )
            #     )
            # except PageProcessingError as e:
            #     # Page processing failed after retries
            #     error_msg = (
            #         f"AI processing failed for page {e.page_num} "
            #         f"after {settings.page_retry_attempts} attempts: "
            #         f"{e.original_error}"
            #     )
            #     logger.error(error_msg)
            #     await retry_with_backoff(
            #         lambda: self.job.update_job_status(
            #             job.job_id, "failed", error=error_msg
            #         ),
            #         max_attempts=3,
            #         operation_name=f"Update job {job.job_id} to failed (AI processing error)"
            #     )
            #     raise ValueError(error_msg) from e
            #
            # # Step 5: Combine improved markdown
            # final_markdown = self.ai_enhancement.combine_page_markdown(
            #     improvement_results, conversion_result.pages
            # )
            #
            # # Step 6: Calculate confidence metrics
            # page_scores = [r.confidence_score for r in improvement_results]
            # confidence_score, confidence_level = calculate_document_confidence(
            #     page_scores
            # )

            # Step 7: Set default confidence (AI enhancement disabled)
            confidence_score = 0.0
            confidence_level = "raw_docling_output"

            logger.info(
                f"Document confidence: {confidence_score:.2f} ({confidence_level})"
            )

            # Step 8: Upload results to S3 with versioning (with retry on network errors)
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

            # Step 9: Update job status with metadata (with retry for Redis failures)
            processing_time = int(time.time() - start_time)

            job_metadata = {
                "markdown_url": result_url,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "processing_time_seconds": processing_time,
                "total_pages": conversion_result.total_pages,
            }

            # Add image metadata if images were extracted
            if image_metadata:
                job_metadata["images"] = image_metadata
                job_metadata["image_count"] = len(image_metadata)

            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id,
                    "completed",
                    metadata=job_metadata,
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

    def _image_to_bytes(self, pil_image: "PIL.Image.Image") -> bytes:  # type: ignore
        """Convert PIL Image to PNG bytes.

        Args:
            pil_image: PIL Image object

        Returns:
            PNG image as bytes
        """
        buffer = BytesIO()
        pil_image.save(buffer, format="PNG")
        return buffer.getvalue()

    def _replace_image_references(
        self,
        markdown: str,
        image_metadata: Dict[str, Dict[str, Any]]
    ) -> str:
        """Replace Docling internal image references with S3 URLs.

        Docling generates markdown image references like:
        - ![](picture-0-0.png) - inline reference
        - ![Caption text](#/pictures/0) - reference with caption

        This method replaces them with S3 URLs while preserving captions.

        Args:
            markdown: Raw markdown from Docling
            image_metadata: Mapping of ref_id -> {url, caption, type, page}

        Returns:
            Updated markdown with S3 image URLs
        """
        if not image_metadata:
            return markdown

        # Pattern 1: ![optional-alt](#/pictures/N) or ![optional-alt](#/tables/N)
        # Replace with ![caption](s3-url)
        def replace_ref_link(match):
            alt_text = match.group(1)
            ref_id = match.group(2)

            if ref_id in image_metadata:
                meta = image_metadata[ref_id]
                # Use caption if available, otherwise use alt text
                caption = meta.get("caption") or alt_text or f"{meta['type'].title()}"
                return f"![{caption}]({meta['url']})"
            return match.group(0)  # Keep original if not found

        markdown = re.sub(
            r'!\[([^\]]*)\]\((#/(?:pictures|tables)/\d+)\)',
            replace_ref_link,
            markdown
        )

        # Pattern 2: Inline image filenames like picture-0-0.png
        # These might be generated by Docling's internal export
        # Replace with S3 URLs if we can match them to our extracted images
        for ref_id, meta in image_metadata.items():
            ref_index = ref_id.split('/')[-1]
            inline_pattern = f"{meta['type']}-{ref_index}"

            # Replace inline references
            markdown = re.sub(
                rf'!\[([^\]]*)\]\(({inline_pattern}[^\)]*)\)',
                lambda m: f"![{m.group(1) or meta.get('caption', meta['type'])}]({meta['url']})",
                markdown
            )

        return markdown
