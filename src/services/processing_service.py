"""Main processing service orchestrating PDF conversion and AI enhancement."""

import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

from ..agents.full_document_agent import FullDocumentAgent
from ..config import settings
from ..services.document_context_service import DocumentContextService
from ..services.job_service import JobService
from ..services.pdf_converter import PDFConverter
from ..services.queue_service import QueueService
from ..services.storage_service import StorageService
from ..services.text_correction_service import PageProcessingError, TextCorrectionService
from ..shared.constants.statuses import STATUS_AWAITING_CORRECTION_APPROVAL
from ..shared.models.processing import ProcessingResult
from ..shared.models.queue import ProcessingQueuePayload
from ..utils.confidence_scoring import calculate_document_confidence
from ..utils.retry_helpers import retry_with_backoff
from ..utils.token_generator import generate_secure_token

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
        text_correction: TextCorrectionService | None = None,
        document_context_service: DocumentContextService | None = None,
    ):
        """Initialize processing service with dependencies.

        Args:
            storage_service: S3 storage operations
            queue_service: Redis queue operations
            job_service: Job status management
            redis_client: Redis client for token storage
            pdf_converter: Optional PDF converter (created if not provided)
            text_correction: Optional text correction service (created if not provided)
            document_context_service: Optional context service for direct extraction
        """
        self.storage = storage_service
        self.queue = queue_service
        self.job = job_service
        self.redis = redis_client
        self.pdf_converter = pdf_converter or PDFConverter()
        self.text_correction = text_correction or TextCorrectionService(
            max_concurrent_pages=settings.max_concurrent_pages
        )
        self.context_service = document_context_service or DocumentContextService()

        logger.info("Processing service initialized")

    async def process_document(
        self, job: ProcessingQueuePayload
    ) -> ProcessingResult:
        """Main processing pipeline for PDF documents.

        Pipeline steps:
        1. Update job status to processing
        2. Download PDF from S3
        3. Convert PDF with Docling (extract markdown + page images)
        4. Upload extracted images to S3
        5. Replace image references in markdown with S3 URLs
        6. Upload original markdown (before corrections) to S3
        7. Upload page preview images to S3
        8. AI text correction - compare visual layout to extracted text
        9. Calculate confidence metrics from correction analysis
        10. Check auto-approval threshold:
            - If confidence >= threshold: Apply corrections and complete job (status: completed)
            - If confidence < threshold: Store corrections for manual approval (status: awaiting_correction_approval)
        11-15. Manual approval path only (if confidence < threshold):
            11. Upload original markdown to S3 results bucket
            12. Serialize correction results for approval review
            13. Generate correction approval token
            14. Prepare job metadata with correction results
            15. Add to timeout tracking
            16. Update job status to awaiting_correction_approval

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

            # Step 2: Download PDF from S3 (StorageService handles retries internally)
            logger.info(f"Downloading PDF from s3://{settings.s3_temp_bucket}/{job.s3_key}")
            pdf_content = await self.storage.download_temp_file(s3_key=job.s3_key)
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
            image_metadata: dict[str, dict[str, Any]] = {}
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
                        # StorageService handles retries internally
                        image_url = await self.storage.upload_image(
                            job_id=job.job_id,
                            image_data=img_bytes,
                            image_name=image_name
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
            original_markdown = self._replace_image_references(
                conversion_result.full_markdown,
                image_metadata
            )

            # Step 5a: Upload ORIGINAL markdown (before AI corrections) to S3
            logger.info("Uploading original markdown (before AI corrections) to S3")
            try:
                original_markdown_key = await self.storage.upload_result(
                    job_id=job.job_id,
                    content=original_markdown.encode("utf-8"),
                    format="md",
                    suffix="-original"
                )
                logger.info(f"Original markdown uploaded: {original_markdown_key}")
            except Exception as e:
                # Log error but continue with processing - original version is nice-to-have
                logger.error(
                    f"Failed to upload original markdown for job {job.job_id}: {e}",
                    exc_info=True
                )
                # Set to None so we can track that this upload failed
                original_markdown_key = None

            # Step 6: Upload page preview images to S3 for correction review
            logger.info(f"Uploading {len(conversion_result.pages)} page preview images to S3...")
            page_image_keys = []
            for page_data in conversion_result.pages:
                try:
                    # Decode base64 image to bytes
                    import base64
                    image_bytes = base64.b64decode(page_data.image_base64)

                    # Upload to S3
                    page_key = await self.storage.upload_page_image(
                        job_id=job.job_id,
                        page_num=page_data.page_num,
                        image_data=image_bytes
                    )

                    page_image_keys.append(page_key)
                    logger.debug(f"Uploaded page {page_data.page_num} image -> {page_key}")

                except Exception as e:
                    logger.error(
                        f"Failed to upload page {page_data.page_num} image: {e}",
                        exc_info=True
                    )
                    # Continue processing other pages even if one fails

            logger.info(
                f"Successfully uploaded {len(page_image_keys)}/{len(conversion_result.pages)} page images"
            )

            # Step 7: AI text correction - compare visual layout to extracted text
            logger.info("Starting AI text correction analysis")
            try:
                correction_results = (
                    await self.text_correction.process_pages_concurrently(
                        conversion_result.pages
                    )
                )
            except PageProcessingError as e:
                # Page processing failed after retries
                error_msg = (
                    f"Text correction analysis failed for page {e.page_num} "
                    f"after {settings.page_retry_attempts} attempts: "
                    f"{e.original_error}"
                )
                logger.error(error_msg)
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id, "failed", error=error_msg
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} to failed (text correction error)"
                )
                raise ValueError(error_msg) from e

            # Step 8: Calculate confidence metrics from correction results
            page_scores = [r.overall_confidence for r in correction_results]
            confidence_score, confidence_level = calculate_document_confidence(
                page_scores
            )

            logger.info(
                f"Document confidence: {confidence_score:.2f} ({confidence_level})"
            )

            # Step 8b: Calculate aggregate LLM cost from all pages
            total_llm_cost_cents = sum(
                r.usage.estimated_cost_cents for r in correction_results if r.usage
            )
            llm_page_costs = [
                {
                    "page": r.page_number,
                    "input_tokens": r.usage.input_tokens,
                    "output_tokens": r.usage.output_tokens,
                    "total_tokens": r.usage.total_tokens,
                    "estimated_cost_cents": r.usage.estimated_cost_cents,
                }
                for r in correction_results
                if r.usage
            ]

            logger.info(
                f"LLM usage summary: {len(llm_page_costs)} pages, "
                f"est. total cost: ${total_llm_cost_cents/100:.6f}"
            )

            # Step 9: Check if corrections should be auto-approved
            should_auto_approve = (
                confidence_score >= settings.min_confidence_for_auto_approval
                and settings.min_confidence_for_auto_approval > 0
            )

            logger.info(
                f"Auto-approval check: confidence={confidence_score:.3f}, "
                f"threshold={settings.min_confidence_for_auto_approval:.3f}, "
                f"auto_approve={should_auto_approve}"
            )

            if should_auto_approve:
                # AUTO-APPROVE PATH: Apply corrections and complete job
                logger.info("Auto-approving corrections (confidence above threshold)")

                # Apply corrections to markdown
                final_markdown = self.text_correction.apply_all_page_corrections(
                    conversion_result.pages, correction_results
                )

                # Upload corrected markdown to final location
                logger.info("Uploading auto-approved corrected markdown to S3")
                result_url = await self.storage.upload_result(
                    job_id=job.job_id,
                    content=final_markdown.encode("utf-8"),
                    format="md"
                )

                # Calculate processing time
                processing_time = int(time.time() - start_time)

                # Prepare update fields for auto-approved completion
                update_fields = {
                    "markdown_url": result_url,
                    "confidence_score": confidence_score,
                    "confidence_level": confidence_level,
                    "processing_time_seconds": processing_time,
                    "total_pages": conversion_result.total_pages,
                    "corrections_auto_approved": True,
                    "auto_approval_threshold": settings.min_confidence_for_auto_approval,
                    "llm_cost_cents": total_llm_cost_cents,
                    "llm_page_costs": json.dumps(llm_page_costs),
                }

                # Add page image keys as JSON array
                if page_image_keys:
                    update_fields["page_image_keys"] = json.dumps(page_image_keys)

                # Add image metadata
                if image_metadata:
                    update_fields["images"] = image_metadata
                    update_fields["image_count"] = len(image_metadata)

                # Add original markdown key if available
                if original_markdown_key:
                    update_fields["original_markdown_key"] = original_markdown_key

                # Update job status to completed (all fields at top level)
                await retry_with_backoff(
                    lambda: self.job.update_job_status(
                        job.job_id,
                        "completed",
                        **update_fields
                    ),
                    max_attempts=3,
                    operation_name=f"Update job {job.job_id} status to completed (auto-approved)"
                )

                threshold = settings.min_confidence_for_auto_approval
                logger.info(
                    f"Job {job.job_id} auto-approved and completed in {processing_time}s "
                    f"(confidence: {confidence_score:.2f} >= threshold: {threshold:.2f})"
                )

                return ProcessingResult(
                    job_id=job.job_id,
                    markdown_url=result_url,
                    confidence_score=confidence_score,
                    processing_time_seconds=processing_time,
                    error_message=None,
                )

            # MANUAL APPROVAL PATH: Store corrections and wait for human decision
            logger.info(
                f"Requiring manual approval (confidence {confidence_score:.2f} "
                f"< threshold {settings.min_confidence_for_auto_approval:.2f})"
            )

            # Step 10: Upload corrected markdown (awaiting approval) to S3 results bucket
            # Apply corrections and store for approval review
            logger.info("Uploading corrected markdown (awaiting approval) to S3 results bucket")
            corrected_markdown = self.text_correction.apply_all_page_corrections(
                conversion_result.pages, correction_results
            )
            # StorageService handles retries internally
            corrected_markdown_key = await self.storage.upload_result(
                job_id=job.job_id,
                content=corrected_markdown.encode("utf-8"),
                format="md",
                suffix="-awaiting-approval"
            )

            # Step 11: Serialize correction results for approval review
            logger.info("Serializing correction results for human review")
            correction_results_json = [
                {
                    "page": r.page_number,
                    "corrections": [
                        {
                            "type": c.correction_type,
                            "original": c.original_text[:200] if c.original_text else "",  # Truncate for storage
                            "corrected": c.corrected_text[:200] if c.corrected_text else "",
                            "confidence": c.confidence,
                            "explanation": c.explanation,
                            "location_context": c.location_context[:300] if c.location_context else ""
                        }
                        for c in r.corrections
                    ],
                    "confidence": r.overall_confidence,
                    "notes": r.processing_notes
                }
                for r in correction_results
            ]

            # Step 12: Generate correction approval token
            logger.info("Generating correction approval token")
            correction_approval_token = generate_secure_token()

            # Calculate expiration time (4 hours from now)
            expires_at = datetime.now(UTC) + timedelta(hours=4)

            # Store token mapping in Redis (token -> job_id, 4 hour TTL)
            if self.redis:
                token_key = f"eq-pdf:correction-token:{correction_approval_token}"
                await self.redis.set(token_key, job.job_id, ex=14400)  # 4 hours in seconds
                logger.info(f"Stored correction approval token in Redis (expires: {expires_at.isoformat()})")
            else:
                logger.warning("Redis client not available - token not stored!")

            # Step 13: Prepare update fields for correction approval
            processing_time = int(time.time() - start_time)

            # Build top-level update fields (no metadata blob)
            update_fields = {
                "original_markdown_key": original_markdown_key,
                "corrected_markdown_key": corrected_markdown_key,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "processing_time_seconds": processing_time,
                "total_pages": conversion_result.total_pages,
                "correction_results": json.dumps(correction_results_json),
                "correction_approval_token": correction_approval_token,
                "correction_expires_at": expires_at.isoformat(),
                "llm_cost_cents": total_llm_cost_cents,
                "llm_page_costs": json.dumps(llm_page_costs),
            }

            # Add page image keys as JSON array for correction review
            if page_image_keys:
                update_fields["page_image_keys"] = json.dumps(page_image_keys)

            # Add image metadata if images were extracted
            if image_metadata:
                update_fields["images"] = image_metadata
                update_fields["image_count"] = len(image_metadata)

            # Step 14: Add to timeout tracking (before status update)
            if self.redis:
                timeout_score = int(expires_at.timestamp())
                await self.redis.zadd(
                    "eq-pdf:timeouts:correction-approval",
                    {job.job_id: timeout_score}
                )
                logger.info(f"Added job {job.job_id} to correction approval timeout tracking")

            # Step 15: Update job status to awaiting_correction_approval (all fields at top level)
            await retry_with_backoff(
                lambda: self.job.update_job_status(
                    job.job_id,
                    STATUS_AWAITING_CORRECTION_APPROVAL,
                    **update_fields
                ),
                max_attempts=3,
                operation_name=f"Update job {job.job_id} status to awaiting_correction_approval"
            )

            logger.info(
                f"Job {job.job_id} ready for correction approval in {processing_time}s "
                f"(confidence: {confidence_score:.2f} - {confidence_level}, "
                f"expires: {expires_at.isoformat()})"
            )

            return ProcessingResult(
                job_id=job.job_id,
                markdown_url=corrected_markdown_key,
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

    def _image_to_bytes(self, pil_image: Any) -> bytes:
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
        image_metadata: dict[str, dict[str, Any]]
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

    async def process_with_direct_extraction(
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
        logger.info(f"Starting TWO-PHASE EXTRACTION for job {job.job_id}")

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
                "extraction_method": "two_phase",  # Mark as two-phase extraction
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
                f"Job {job.job_id} completed with TWO-PHASE EXTRACTION in {processing_time}s "
                f"(confidence: {avg_confidence:.2f}, est. cost: ${total_usage.estimated_cost_cents/100:.4f})"
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
            error_msg = f"Two-phase extraction failed: {str(e)}"
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
            error_msg = f"Two-phase extraction failed: {str(e)}"
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
