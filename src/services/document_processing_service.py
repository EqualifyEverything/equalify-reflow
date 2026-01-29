"""Document Processing Service for agentic pipeline orchestration.

This service orchestrates the full document processing pipeline:
1. Docling extraction (with scanned PDF detection)
2. Planning phase (structure inference, job generation)
3. Execution phase (parallel workers)
4. Verification phase
5. Recovery phase (if needed)

It manages:
- Redis job state updates with processing fields
- S3 storage for ledger and final markdown
- EventBus for SSE streaming
- Review mode support ('auto' | 'human')
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal

from redis.asyncio import Redis

from ..config import settings
from .metrics_service import job_duration_seconds, jobs_completed_total

if TYPE_CHECKING:
    from ..agents.models import ProcessingResult, RoundLoopResult, StoredFigure

from .pdf_converter import ExtractedImage

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """Orchestrates agentic document processing pipeline.

    This service replaces the Processing Worker by running the pipeline
    inline (via BackgroundTasks) instead of through a Redis queue.

    State Management:
        - Job state (status, metadata): Redis via JobService
        - Ledger (change history): S3
        - Final markdown: S3
        - EventBus (SSE events): In-memory (single instance)

    Usage:
        service = DocumentProcessingService(redis_client, storage_service)
        result = await service.process_document(
            job_id="abc-123",
            s3_key="temp/file.pdf",
            filename="document.pdf",
            review_mode="human",
        )
    """

    def __init__(
        self,
        redis_client: Redis[Any] | None,
        storage_service: Any,  # StorageService
        s3_url_service: Any,  # S3URLService
    ) -> None:
        """Initialize document processing service.

        Args:
            redis_client: Redis async client for job state (optional for read-only ops)
            storage_service: Storage service for S3 operations
            s3_url_service: Service for generating S3 URLs
        """
        self.redis = redis_client
        self.storage = storage_service
        self.s3_url = s3_url_service
        self.status_prefix = settings.job_status_prefix

    async def process_document(
        self,
        job_id: str,
        s3_key: str,
        filename: str,
        review_mode: Literal["auto", "human"] = "auto",
        max_rounds: int = 1,
    ) -> ProcessingResult:
        """Process a document through the agentic pipeline.

        This is the main entry point for document processing. It:
        1. Downloads PDF from S3
        2. Runs Docling extraction (detects scanned PDFs)
        3. Runs planning phase (structure inference, job generation)
        4. Runs execution phase (parallel workers with ledger)
        5. Runs verification phase
        6. Runs recovery phase if needed
        7. Stores ledger and markdown to S3
        8. Updates Redis job state throughout

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where PDF is stored
            filename: Original filename
            review_mode: 'auto' (immediate completion) or 'human' (ledger available)
            max_rounds: Maximum processing rounds (default 1). Use > 1 for multi-round improvement.

        Returns:
            ProcessingResult with final markdown, ledger, and stats
        """
        # Import here to avoid circular imports
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.document import DocumentStream  # type: ignore[attr-defined]
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from PIL import Image

        from ..agents.events import (
            DoclingCompleteEvent,
            DoclingStartedEvent,
            EventBus,
            ProcessingCompleteEvent,
            VisionExtractionCompleteEvent,
            VisionExtractionStartedEvent,
            VisionPageExtractedEvent,
        )
        from ..agents.orchestrator import run_agentic_pipeline, run_multi_round_pipeline
        from ..services.vision_extraction_service import (
            VisualDocumentType,
            detect_visual_document_type,
            extract_all_pages_vision,
            is_scanned_pdf,
        )

        # Create event bus EARLY so streaming can connect immediately
        event_bus = EventBus(job_id)

        # Store event bus reference for SSE streaming
        # Note: This is in-memory, so only works for single instance
        await self._store_event_bus(job_id, event_bus)

        # Update job state with initial processing fields
        await self._update_job_state(
            job_id,
            status="processing",
            processing_phase="docling",
            review_mode=review_mode,
            jobs_total=0,
            jobs_complete=0,
        )

        try:
            # Track processing duration for metrics
            processing_start_time = time.time()

            # Download PDF from S3
            logger.info(f"Downloading PDF from S3: {s3_key}")
            file_content = await self.storage.download_temp_file(s3_key)

            # Emit Docling started event
            event_bus.emit(
                DoclingStartedEvent(
                    document_id=job_id,
                    filename=filename,
                )
            )

            # Convert PDF with Docling
            docling_start = time.time()
            logger.info(f"Converting PDF: {filename}")

            # Configure Docling
            pipeline_options = PdfPipelineOptions(
                do_ocr=False,  # We use LLM vision for scanned PDFs instead
                do_table_structure=True,
                generate_page_images=True,
                generate_picture_images=True,  # Extract embedded figures for storage
                images_scale=2.0,
            )

            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )

            # Convert from bytes using DocumentStream
            # Run in thread pool to avoid blocking event loop (Docling is synchronous)
            pdf_stream = BytesIO(file_content)
            doc_stream = DocumentStream(name=filename, stream=pdf_stream)
            result = await asyncio.to_thread(converter.convert, source=doc_stream)
            doc = result.document

            logger.info(f"Docling conversion: {len(doc.pages)} pages")

            # Extract full document markdown for scanned detection
            full_docling_markdown = ""
            try:
                full_docling_markdown = doc.export_to_markdown()
            except Exception as e:
                logger.warning(f"Failed to export full Docling markdown: {e}")

            # Extract page markdowns (from Docling)
            page_markdowns: dict[int, str] = {}
            for page_no in sorted(doc.pages.keys()):
                page_md = doc.export_to_markdown(page_no=page_no)
                page_markdowns[page_no] = page_md

            # Extract page images (PIL)
            page_images: dict[int, Image.Image] = {}
            for page_no in sorted(doc.pages.keys()):
                page = doc.pages[page_no]
                if page.image and hasattr(page.image, "pil_image") and page.image.pil_image:
                    page_images[page_no] = page.image.pil_image

            logger.info(f"Extracted {len(page_images)} page images")

            # Extract embedded figures (pictures) from document for storage
            extracted_figures: list[ExtractedImage] = []
            for pic in doc.pictures:
                if pic.image and hasattr(pic.image, "pil_image") and pic.image.pil_image:
                    bbox = None
                    if pic.prov and pic.prov[0].bbox:
                        bb = pic.prov[0].bbox
                        bbox = (bb.l, bb.t, bb.r, bb.b)

                    extracted_figures.append(
                        ExtractedImage(
                            ref_id=pic.self_ref,
                            image_type="picture",
                            caption=pic.caption_text(doc=doc) or "",
                            page_num=pic.prov[0].page_no if pic.prov else 1,
                            pil_image=pic.image.pil_image,
                            bbox=bbox,
                        )
                    )

            if extracted_figures:
                logger.info(f"Extracted {len(extracted_figures)} figures from PDF")

            # Upload figures to S3 (best-effort, non-blocking)
            stored_figures: list[StoredFigure] = []
            if extracted_figures:
                try:
                    stored_figures = await self.storage.upload_figures(job_id, extracted_figures)
                except Exception as e:
                    logger.warning(f"Failed to upload figures for job {job_id}: {e}")

            # Detect if PDF is scanned
            total_pages = len(doc.pages)
            scanned = is_scanned_pdf(full_docling_markdown, total_pages)
            chars_per_page = len(full_docling_markdown) / max(total_pages, 1)

            # Detect visual document type (posters, infographics, etc.)
            is_visual_document = False
            visual_doc_type = VisualDocumentType.STANDARD

            if total_pages == 1 and len(page_images) > 0:
                first_page_image = page_images.get(1) or next(iter(page_images.values()))
                buffer = BytesIO()
                first_page_image.save(buffer, format="PNG")
                first_page_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

                visual_doc_type = await detect_visual_document_type(
                    image_base64=first_page_base64,
                    filename=filename,
                    total_pages=total_pages,
                )

                is_visual_document = visual_doc_type != VisualDocumentType.STANDARD
                if is_visual_document:
                    logger.info(f"Visual document detected: {visual_doc_type.value}")

            # Emit Docling complete event
            docling_duration_ms = int((time.time() - docling_start) * 1000)
            event_bus.emit(
                DoclingCompleteEvent(
                    document_id=job_id,
                    pages_extracted=len(page_markdowns),
                    images_extracted=len(page_images),
                    duration_ms=docling_duration_ms,
                    is_scanned=scanned,
                    chars_per_page=chars_per_page,
                )
            )

            # Use vision extraction for scanned PDFs or visual documents
            use_vision_extraction = scanned or is_visual_document

            if use_vision_extraction:
                reason = (
                    f"visual_document ({visual_doc_type.value})"
                    if is_visual_document
                    else f"scanned_pdf ({chars_per_page:.1f} chars/page)"
                )
                logger.info(f"Using LLM vision extraction: {reason}")

                # Emit vision extraction started
                event_bus.emit(
                    VisionExtractionStartedEvent(
                        document_id=job_id,
                        total_pages=len(page_images),
                        reason=reason,
                    )
                )

                await self._update_job_state(
                    job_id,
                    processing_phase="vision_extraction",
                )

                # Convert PIL images to base64 for vision extraction
                page_images_base64: dict[int, str] = {}
                for page_no, pil_image in page_images.items():
                    buffer = BytesIO()
                    pil_image.save(buffer, format="PNG")
                    page_images_base64[page_no] = base64.b64encode(buffer.getvalue()).decode("utf-8")

                # Callback for progress events
                def on_page_complete(page_num: int, markdown: str, success: bool) -> None:
                    event_bus.emit(
                        VisionPageExtractedEvent(
                            document_id=job_id,
                            page_num=page_num,
                            chars_extracted=len(markdown),
                            success=success,
                        )
                    )

                # Run vision extraction
                page_markdowns, vision_stats = await extract_all_pages_vision(
                    page_images_base64=page_images_base64,
                    document_type="course material",
                    max_concurrent=3,
                    on_page_complete=on_page_complete,
                    filename=filename,
                    auto_detect_visual_type=True,
                )

                # Emit vision extraction complete
                total_chars = sum(len(md) for md in page_markdowns.values())
                event_bus.emit(
                    VisionExtractionCompleteEvent(
                        document_id=job_id,
                        pages_extracted=vision_stats.pages_extracted,
                        total_chars=total_chars,
                        total_tokens_input=vision_stats.total_tokens_input,
                        total_tokens_output=vision_stats.total_tokens_output,
                        estimated_cost=vision_stats.estimated_cost,
                        duration_ms=vision_stats.total_duration_ms,
                    )
                )

                logger.info(
                    f"Vision extraction complete: {vision_stats.pages_extracted} pages, "
                    f"{total_chars} chars, ${vision_stats.estimated_cost:.4f}"
                )

            # Extract element bboxes for image cropping
            element_bboxes: dict[tuple[int, str, int], tuple[float, float, float, float]] = {}
            figure_counts: dict[int, int] = {}
            table_counts: dict[int, int] = {}

            for item, level in doc.iterate_items():
                page_no = getattr(item, "page_no", 1)

                if hasattr(item, "label"):
                    if item.label == "picture":
                        figure_counts[page_no] = figure_counts.get(page_no, 0) + 1
                        idx = figure_counts[page_no]
                        if hasattr(item, "prov") and item.prov:
                            bbox = item.prov[0].bbox
                            element_bboxes[(page_no, "figure", idx)] = (bbox.l, bbox.t, bbox.r, bbox.b)
                    elif item.label == "table":
                        table_counts[page_no] = table_counts.get(page_no, 0) + 1
                        idx = table_counts[page_no]
                        if hasattr(item, "prov") and item.prov:
                            bbox = item.prov[0].bbox
                            element_bboxes[(page_no, "table", idx)] = (bbox.l, bbox.t, bbox.r, bbox.b)

            # Get page width
            page_width = 612.0  # Default letter size
            if doc.pages:
                first_page = doc.pages.get(1) or next(iter(doc.pages.values()), None)
                if first_page and hasattr(first_page, "size") and first_page.size:
                    page_width = first_page.size.width

            # Update job state to planning phase
            await self._update_job_state(
                job_id,
                processing_phase="planning",
                total_pages=len(page_markdowns),
            )

            # Check if debug bundle was requested
            assert self.redis is not None, "Redis client required for process_document"
            job_key = f"{self.status_prefix}{job_id}"
            debug_bundle_requested = await self.redis.hget(job_key, "debug_bundle_requested")
            capture_debug = debug_bundle_requested == b"true" or debug_bundle_requested == "true"

            # Run agentic pipeline (single-round or multi-round based on max_rounds)
            if max_rounds > 1:
                logger.info(
                    f"Starting multi-round pipeline for {filename} "
                    f"(max_rounds={max_rounds}, debug_bundle={capture_debug})"
                )
                round_loop_result, event_bus = await run_multi_round_pipeline(
                    filename=filename,
                    page_markdowns=page_markdowns,
                    page_images=page_images,
                    element_bboxes=element_bboxes,
                    page_width=page_width,
                    document_id=job_id,
                    max_rounds=max_rounds,
                    event_bus=event_bus,
                    capture_debug=capture_debug,
                    stored_figures=stored_figures,
                )
                # Convert RoundLoopResult to ProcessingResult for downstream compatibility
                processing_result = self._convert_round_loop_result(
                    round_loop_result, job_id, len(page_markdowns), stored_figures
                )
            else:
                logger.info(f"Starting agentic pipeline for {filename} (debug_bundle={capture_debug})")
                processing_result, event_bus = await run_agentic_pipeline(
                    filename=filename,
                    page_markdowns=page_markdowns,
                    page_images=page_images,
                    element_bboxes=element_bboxes,
                    page_width=page_width,
                    document_id=job_id,
                    event_bus=event_bus,
                    capture_debug=capture_debug,
                    stored_figures=stored_figures,
                )

            # Save debug artifacts if requested
            if capture_debug:
                await self._save_debug_artifacts(job_id, processing_result)

            # Store ledger to S3
            ledger_s3_key = await self._store_ledger(job_id, processing_result)

            # Store final markdown to S3
            markdown_s3_key = await self._store_markdown(job_id, processing_result.final_markdown)

            # Store figures to results/{job_id}/figures/ for publisher and bundle access
            await self._store_figures(job_id, processing_result.stored_figures)

            # Update final job state (store S3 key, not presigned URL - API generates URLs on demand)
            # Note: field names must match what _build_llm_cost() expects in api/documents.py
            total_tokens = processing_result.total_input_tokens + processing_result.total_output_tokens
            cost_cents = processing_result.total_cost * 100  # Convert dollars to cents

            # Serialize stored figures for API access
            stored_figures_json = [fig.model_dump(mode="json") for fig in processing_result.stored_figures]

            await self._update_job_state(
                job_id,
                status="completed",
                processing_phase="complete",
                jobs_total=processing_result.total_jobs,
                jobs_complete=processing_result.total_jobs,
                result_url=markdown_s3_key,
                ledger_s3_key=ledger_s3_key,
                confidence_score=processing_result.confidence_score,
                total_edits=processing_result.total_edits,
                total_pages=processing_result.total_pages,
                llm_input_tokens=processing_result.total_input_tokens,
                llm_output_tokens=processing_result.total_output_tokens,
                llm_total_tokens=total_tokens,
                llm_cost_cents=cost_cents,
                llm_calls=[call.model_dump(mode="json") for call in processing_result.llm_calls],
                stored_figures=stored_figures_json,
            )

            # Emit processing:complete AFTER markdown is stored and job state updated
            # This ensures the markdown_url is available when the UI fetches it
            processing_duration_ms = int((time.time() - processing_start_time) * 1000)
            event_bus.emit(
                ProcessingCompleteEvent(
                    document_id=job_id,
                    success=True,
                    total_edits=processing_result.total_edits,
                    total_jobs=processing_result.total_jobs,
                    total_cost=processing_result.total_cost,
                    total_duration_ms=processing_duration_ms,
                    result_url=f"/api/v1/documents/{job_id}",
                )
            )

            # Record job completion metrics
            processing_duration = time.time() - processing_start_time
            jobs_completed_total.labels(status="success").inc()
            job_duration_seconds.labels(stage="processing").observe(processing_duration)

            logger.info(f"Job {job_id} complete: {processing_result.total_edits} edits")
            return processing_result

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            await self._update_job_state(
                job_id,
                status="failed",
                error=str(e),
            )

            # Record job failure metric
            jobs_completed_total.labels(status="failed").inc()

            raise

    async def _update_job_state(self, job_id: str, **fields: Any) -> None:
        """Update job state in Redis.

        Args:
            job_id: Job identifier
            **fields: Fields to update
        """
        from datetime import UTC, datetime

        update_data: dict[str | bytes, bytes | float | int | str] = {
            "updated_at": datetime.now(UTC).isoformat(),
        }

        for key, value in fields.items():
            if isinstance(value, (dict, list)):
                update_data[key] = json.dumps(value)
            elif value is not None:
                update_data[key] = str(value)

        assert self.redis is not None, "Redis client required for _update_job_status"
        await self.redis.hset(f"{self.status_prefix}{job_id}", mapping=update_data)

        # Set TTL based on status if changed
        if "status" in fields:
            await self._set_job_ttl(job_id, fields["status"])

    async def _set_job_ttl(self, job_id: str, status: str) -> None:
        """Set TTL for job based on status."""
        if status == "completed":
            ttl = settings.job_ttl_completed
        elif status == "failed":
            ttl = settings.job_ttl_failed
        else:
            ttl = settings.job_ttl_active

        assert self.redis is not None, "Redis client required for _set_job_ttl"
        await self.redis.expire(f"{self.status_prefix}{job_id}", ttl)

    async def _store_ledger(self, job_id: str, result: ProcessingResult) -> str:
        """Store ledger to S3 as JSON.

        Args:
            job_id: Job identifier
            result: Processing result containing ledger

        Returns:
            S3 key where ledger is stored
        """
        ledger_data = {
            "document_id": result.document_id,
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "job_id": entry.job_id,
                    "page": entry.page,
                    "timestamp": entry.timestamp.isoformat(),
                    "action": entry.action.value,
                    "target": entry.target,
                    "before": entry.before,
                    "after": entry.after,
                    "reasoning": entry.reasoning,
                    "confidence": entry.confidence,
                    "validated": entry.validated,
                }
                for entry in result.ledger.entries
            ],
            "total_edits": result.total_edits,
        }

        ledger_json = json.dumps(ledger_data, indent=2)
        s3_key = f"results/{job_id}/ledger.json"

        await self.storage.upload_file(
            key=s3_key,
            content=ledger_json.encode("utf-8"),
            content_type="application/json",
        )

        return s3_key

    async def _store_markdown(self, job_id: str, markdown: str) -> str:
        """Store final markdown to S3.

        Args:
            job_id: Job identifier
            markdown: Final markdown content

        Returns:
            S3 key where markdown is stored
        """
        s3_key = f"results/{job_id}/result.md"

        await self.storage.upload_file(
            key=s3_key,
            content=markdown.encode("utf-8"),
            content_type="text/markdown",
        )

        return s3_key

    async def _store_figures(
        self,
        job_id: str,
        stored_figures: list[StoredFigure],
    ) -> None:
        """Copy extracted figures to the results prefix for publisher access.

        Figures are initially uploaded at ``{job_id}/images/figure-N.png``
        by :meth:`StorageService.upload_figures`.  The Canvas publisher and
        download-bundle services list figures at
        ``results/{job_id}/figures/``, so this method downloads each figure
        from its original key and re-uploads it under the ``results/`` prefix.

        Individual failures are logged and skipped (best-effort).

        Args:
            job_id: Job identifier
            stored_figures: Figures already uploaded to S3
        """
        if not stored_figures:
            return

        for fig in stored_figures:
            original_key = fig.s3_key
            filename = f"{fig.figure_id}.png"
            dest_key = f"results/{job_id}/figures/{filename}"

            try:
                data = await self.storage.download_file(original_key)
                await self.storage.upload_file(
                    key=dest_key,
                    content=data,
                    content_type="image/png",
                )
            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to copy figure {fig.figure_id} to {dest_key}: {e}")

        logger.info(f"Job {job_id}: Copied {len(stored_figures)} figures to results/{job_id}/figures/")

    async def _store_event_bus(self, job_id: str, event_bus: Any) -> None:
        """Store event bus reference for SSE streaming.

        Note: This uses in-memory storage (module-level dict).
        For horizontal scaling, would need Redis Pub/Sub.

        Args:
            job_id: Job identifier
            event_bus: EventBus instance
        """
        # Import the global event bus registry
        from ..agents.events import register_event_bus

        register_event_bus(job_id, event_bus)

    async def get_ledger(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve ledger from S3.

        Args:
            job_id: Job identifier

        Returns:
            Ledger data dictionary or None if not found
        """
        s3_key = f"results/{job_id}/ledger.json"

        try:
            content = await self.storage.download_file(s3_key)
            result: dict[str, Any] = json.loads(content.decode("utf-8"))
            return result
        except Exception as e:
            logger.warning(f"Failed to retrieve ledger for job {job_id}: {e}")
            return None

    async def _save_debug_artifacts(self, job_id: str, result: ProcessingResult) -> None:
        """Save LLM call artifacts to S3 for debug bundle generation.

        Iterates through all LLM calls that have debug data captured
        and saves them as artifacts using the DebugBundleService.

        Args:
            job_id: Job identifier
            result: Processing result containing llm_calls with debug data
        """
        from ..services.debug_bundle_service import get_debug_bundle_service

        debug_service = get_debug_bundle_service(self.storage)
        saved_count = 0

        for llm_call in result.llm_calls:
            # Only save if debug data was captured
            if llm_call.prompt_text:
                phase = self._map_purpose_to_phase(llm_call.purpose)
                agent_name = f"{llm_call.agent}_{llm_call.purpose}"

                try:
                    await debug_service.save_artifact_from_agent_run(
                        job_id=job_id,
                        phase=phase,
                        agent_name=agent_name,
                        input_data={"purpose": llm_call.purpose, "page": llm_call.page},
                        prompt=llm_call.prompt_text,
                        response_raw=llm_call.response_raw or "",
                        output_parsed="",
                        tokens={
                            "input": llm_call.input_tokens,
                            "output": llm_call.output_tokens,
                            "total": llm_call.input_tokens + llm_call.output_tokens,
                        },
                        cost_cents=llm_call.cost_cents,
                        model_id=llm_call.model_id or "unknown",
                        duration_ms=llm_call.duration_ms,
                    )
                    saved_count += 1
                except Exception as e:
                    logger.warning(f"Failed to save debug artifact for {agent_name}: {e}")

        logger.info(f"Saved {saved_count} debug artifacts for job {job_id}")

    def _convert_round_loop_result(
        self,
        round_result: RoundLoopResult,
        job_id: str,
        total_pages: int,
        stored_figures: list,
    ) -> ProcessingResult:
        """Convert RoundLoopResult from multi-round pipeline to ProcessingResult.

        Maps multi-round result to the standard ProcessingResult format for
        downstream compatibility with ledger storage and state updates.

        Args:
            round_result: Result from run_multi_round_pipeline
            job_id: Job identifier
            total_pages: Total pages in document
            stored_figures: List of StoredFigure objects from S3 upload

        Returns:
            ProcessingResult compatible with downstream processing
        """
        from ..agents.models import Ledger, ProcessingResult, VerificationReport

        # Create a minimal ledger (multi-round doesn't have detailed ledger)
        ledger = Ledger(document_id=job_id)

        # Create a minimal verification report
        verification = VerificationReport(
            document_id=job_id,
            passed=True,
            total_issues=0,
        )

        # Calculate approximate cost (rough estimate based on tokens)
        # Using typical Bedrock Claude 3 pricing: ~$0.003 per 1K input, ~$0.015 per 1K output
        estimated_cost = (round_result.total_input_tokens / 1000) * 0.003 + (
            round_result.total_output_tokens / 1000
        ) * 0.015

        return ProcessingResult(
            document_id=job_id,
            success=True,
            final_markdown=round_result.final_markdown,
            ledger=ledger,
            verification=verification,
            stored_figures=stored_figures,  # Include uploaded figures
            confidence_score=round_result.final_quality,
            total_pages=total_pages,
            total_edits=round_result.total_edits,
            total_jobs=0,  # Multi-round doesn't track traditional jobs
            total_input_tokens=round_result.total_input_tokens,
            total_output_tokens=round_result.total_output_tokens,
            total_cost=estimated_cost,
            llm_calls=round_result.llm_calls,
            total_duration_ms=round_result.total_duration_ms,
        )

    def _map_purpose_to_phase(self, purpose: str) -> str:
        """Map LLMCallRecord purpose to debug bundle phase name.

        Args:
            purpose: Purpose string from LLMCallRecord (e.g., "page_1_analysis")

        Returns:
            Phase name for organizing in debug bundle
        """
        if "analysis" in purpose:
            return "phase_1_planning"
        elif "recovery" in purpose:
            return "phase_4_recovery"
        elif "verification" in purpose:
            return "phase_3_verification"
        else:
            # alt_text, table_transcription, heading_fix, paragraph_fixes, etc.
            return "phase_2_execution"
