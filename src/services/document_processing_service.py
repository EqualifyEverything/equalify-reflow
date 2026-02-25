"""Document Processing Service — orchestrates pipeline processing with job infrastructure.

Integrates PipelineViewerService (versioned step-by-step processing) with:
- Redis job state updates
- S3 storage for markdown results, figures, and change ledger
- Prometheus metrics
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Literal

from redis.asyncio import Redis

from ..config import settings
from .metrics_service import job_duration_seconds, jobs_completed_total
from .pipeline_viewer import PipelineViewerService
from .pipeline_viewer_models import FigureData, PipelineViewerResult

logger = logging.getLogger(__name__)


class DocumentProcessingService:
    """Orchestrates document processing with job tracking and storage.

    Downloads PDF from S3, runs PipelineViewerService for processing,
    stores results back to S3, and updates Redis job state throughout.

    Usage:
        service = DocumentProcessingService(redis_client, storage_service)
        await service.process_document(
            job_id="abc-123",
            s3_key="temp/file.pdf",
            filename="document.pdf",
        )
    """

    def __init__(
        self,
        redis_client: Redis[Any] | None,
        storage_service: Any,  # StorageService
        s3_url_service: Any,  # S3URLService
    ) -> None:
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
        ocr_languages: list[str] | None = None,
    ) -> PipelineViewerResult:
        """Process a document through the versioned pipeline.

        1. Downloads PDF from S3
        2. Runs PipelineViewerService (docling → structure → corrections → boundaries → cleanup)
        3. Stores markdown and figures to S3
        4. Updates Redis job state throughout

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where PDF is stored
            filename: Original filename
            review_mode: 'auto' or 'human' (preserved for future use)
            max_rounds: Unused (kept for interface compatibility)
            ocr_languages: Tesseract OCR language codes for scanned documents

        Returns:
            PipelineViewerResult with versioned markdowns and step data
        """
        await self._update_job_state(
            job_id,
            status="processing",
            processing_phase="docling",
            review_mode=review_mode,
        )

        try:
            processing_start_time = time.time()

            # 1. Download PDF from S3
            logger.info(f"Job {job_id}: Downloading PDF from S3: {s3_key}")
            file_content = await self.storage.download_temp_file(s3_key)

            # 2. Run pipeline processing
            await self._update_job_state(job_id, processing_phase="pipeline")

            service = PipelineViewerService()
            result = await service.process(
                file_content=file_content,
                filename=filename,
                images_scale=2.0,
                do_table_structure=True,
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
                ocr_languages=ocr_languages,
            )

            # 2a. Check for classification errors (unsupported document type)
            if not result.versions:
                # Classification blocked processing — no versions were produced
                classification_step = next(
                    (s for s in result.steps if s.name == "classification" and s.error),
                    None,
                )
                error_msg = (
                    classification_step.error
                    if classification_step
                    else "PDF classification rejected this document"
                )
                await self._update_job_state(
                    job_id, status="failed", error=error_msg
                )
                jobs_completed_total.labels(status="failed").inc()
                return result

            # 3. Extract final markdown (latest version available)
            final_markdown = (
                result.versions.get("v3")
                or result.versions.get("v2")
                or result.versions.get("v1")
                or result.versions.get("v0")
                or ""
            )

            # 4. Store results to S3
            markdown_s3_key = await self._store_markdown(job_id, final_markdown)
            ledger_s3_key = await self._store_change_ledger(job_id, result)
            stored_figures = await self._store_figures_from_pipeline(job_id, result.figures)

            # 5. Aggregate cost/token info from steps
            total_input_tokens = sum(s.input_tokens for s in result.steps)
            total_output_tokens = sum(s.output_tokens for s in result.steps)
            total_tokens = total_input_tokens + total_output_tokens
            cost_cents = sum(s.cost_cents for s in result.steps)
            total_edits = sum(len(s.changes) for s in result.steps)

            # 6. Update final job state
            update_fields: dict[str, Any] = dict(
                status="completed",
                processing_phase="complete",
                result_url=markdown_s3_key,
                ledger_s3_key=ledger_s3_key,
                total_edits=total_edits,
                total_pages=result.total_pages,
                llm_input_tokens=total_input_tokens,
                llm_output_tokens=total_output_tokens,
                llm_total_tokens=total_tokens,
                llm_cost_cents=cost_cents,
                stored_figures=stored_figures,
            )
            if result.warnings:
                update_fields["warnings"] = result.warnings
            await self._update_job_state(job_id, **update_fields)

            # 7. Record Prometheus metrics
            processing_duration = time.time() - processing_start_time
            jobs_completed_total.labels(status="success").inc()
            job_duration_seconds.labels(stage="processing").observe(processing_duration)

            logger.info(
                f"Job {job_id} complete: {total_edits} edits, "
                f"{total_tokens} tokens, ${cost_cents / 100:.4f}"
            )
            return result

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            await self._update_job_state(
                job_id,
                status="failed",
                error=str(e),
            )
            jobs_completed_total.labels(status="failed").inc()
            raise

    async def _update_job_state(self, job_id: str, **fields: Any) -> None:
        """Update job state in Redis."""
        from datetime import UTC, datetime

        update_data: dict[str | bytes, bytes | float | int | str] = {
            "updated_at": datetime.now(UTC).isoformat(),
        }

        for key, value in fields.items():
            if isinstance(value, (dict, list)):
                update_data[key] = json.dumps(value)
            elif value is not None:
                update_data[key] = str(value)

        assert self.redis is not None, "Redis client required for _update_job_state"
        await self.redis.hset(f"{self.status_prefix}{job_id}", mapping=update_data)

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

    async def _store_markdown(self, job_id: str, markdown: str) -> str:
        """Store final markdown to S3. Returns S3 key."""
        s3_key = f"results/{job_id}/result.md"
        await self.storage.upload_file(
            key=s3_key,
            content=markdown.encode("utf-8"),
            content_type="text/markdown",
        )
        return s3_key

    async def _store_change_ledger(self, job_id: str, result: PipelineViewerResult) -> str:
        """Store pipeline change ledger to S3 as JSON. Returns S3 key."""
        ledger_data = {
            "document_id": job_id,
            "steps": [
                {
                    "name": step.name,
                    "display_name": step.display_name,
                    "elapsed_ms": step.elapsed_ms,
                    "input_tokens": step.input_tokens,
                    "output_tokens": step.output_tokens,
                    "cost_cents": step.cost_cents,
                    "changes": [
                        {
                            "page": change.page,
                            "old_text": change.old_text,
                            "new_text": change.new_text,
                            "reasoning": change.reasoning,
                            "stage": change.stage,
                        }
                        for change in step.changes
                    ],
                }
                for step in result.steps
                if not step.skipped
            ],
            "total_edits": sum(len(s.changes) for s in result.steps),
        }

        s3_key = f"results/{job_id}/ledger.json"
        await self.storage.upload_file(
            key=s3_key,
            content=json.dumps(ledger_data, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        return s3_key

    async def _store_figures_from_pipeline(
        self,
        job_id: str,
        figures: list[FigureData],
    ) -> list[dict[str, str | int]]:
        """Store pipeline figures to S3 (base64 → PNG files).

        Returns:
            List of stored figure metadata dicts for Redis (figure_id, s3_key,
            page_num, caption).
        """
        stored: list[dict[str, str | int]] = []
        if not figures:
            return stored

        for fig in figures:
            if not fig.image_base64:
                continue
            dest_key = f"results/{job_id}/figures/{fig.ref_id}.png"
            try:
                image_data = base64.b64decode(fig.image_base64)
                await self.storage.upload_file(
                    key=dest_key,
                    content=image_data,
                    content_type="image/png",
                )
                stored.append({
                    "figure_id": fig.ref_id,
                    "s3_key": dest_key,
                    "page_num": fig.page_number,
                    "alt_text": "",
                    "caption": fig.caption,
                })
            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to store figure {fig.ref_id}: {e}")

        logger.info(f"Job {job_id}: Stored {len(stored)} figures to results/{job_id}/figures/")
        return stored

    async def get_ledger(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve ledger from S3."""
        s3_key = f"results/{job_id}/ledger.json"
        try:
            content = await self.storage.download_file(s3_key)
            result: dict[str, Any] = json.loads(content.decode("utf-8"))
            return result
        except Exception as e:
            logger.warning(f"Failed to retrieve ledger for job {job_id}: {e}")
            return None
