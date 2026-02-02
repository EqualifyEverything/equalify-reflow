"""Facade that mirrors the public REST API for document conversion.

Canvas/LTI code depends on ONE converter interface instead of reaching
into individual services. If Canvas is ever split into a separate
service, swap this facade from direct calls to HTTP calls with no
signature changes.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, Any

from ..config import settings
from ..services.metrics_service import jobs_submitted_total

if TYPE_CHECKING:
    from ..services.job_service import JobService
    from ..services.queue_service import QueueService
    from ..services.storage_service import StorageService

logger = logging.getLogger(__name__)


@dataclass
class SubmitResult:
    """Result of submitting a document for processing."""

    job_id: str
    s3_key: str
    status: str


@dataclass
class FigureInfo:
    """Metadata for a single figure stored in S3."""

    s3_key: str
    filename: str


class ConverterClient:
    """Internal API client that wraps the same services the REST API uses.

    Provides a clean extraction boundary for Canvas/LTI code.
    """

    def __init__(
        self,
        job_service: JobService,
        storage_service: StorageService,
        queue_service: QueueService,
    ) -> None:
        self.job_service = job_service
        self.storage_service = storage_service
        self.queue_service = queue_service

    async def submit_document(
        self,
        file_content: bytes,
        filename: str,
        source: str,
        metadata: dict[str, str] | None = None,
        skip_pii_scan: bool = False,
        skip_reason: str | None = None,
    ) -> SubmitResult:
        """Submit a document for processing.

        Equivalent to ``POST /api/v1/documents/submit``.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename.
            source: Submission source (e.g. "canvas_auto", "lti", "canvas_manual").
            metadata: Extra key-value pairs to store on the job hash.
            skip_pii_scan: If True, skip PII scanning and go directly to processing.
            skip_reason: Reason for skipping PII scan (audit trail).

        Returns:
            SubmitResult with job_id, s3_key, and initial status.
        """
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        # Upload to S3 temp bucket
        self.storage_service.s3_client.put_object(
            Bucket=self.storage_service.temp_bucket,
            Key=s3_key,
            Body=BytesIO(file_content),
            ContentType="application/pdf",
        )

        if skip_pii_scan:
            initial_status = "processing"
            await self.job_service.create_job(
                job_id=job_id,
                s3_key=s3_key,
                status=initial_status,
                original_filename=filename,
                pii_skipped=True,
                pii_skip_reason=skip_reason or "PII scan skipped",
            )
        else:
            initial_status = "pii_scanning"
            await self.job_service.create_job(
                job_id=job_id,
                s3_key=s3_key,
                status=initial_status,
                original_filename=filename,
            )
            await self.queue_service.queue_pii_job(job_id, s3_key)

        # Store source and extra metadata on the job hash
        extra: dict[str, str] = {"source": source}
        if metadata:
            extra.update(metadata)

        await self.job_service.redis.hset(
            f"{self.job_service.status_prefix}{job_id}",
            mapping=extra,
        )

        # Record submission metric
        jobs_submitted_total.labels(source=source).inc()

        logger.info(
            f"Submitted document: job_id={job_id}, filename={filename}, "
            f"source={source}, status={initial_status}"
        )

        return SubmitResult(job_id=job_id, s3_key=s3_key, status=initial_status)

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get job status and metadata.

        Equivalent to ``GET /api/v1/documents/{job_id}``.

        Returns:
            Job data dictionary or None if not found.
        """
        return await self.job_service.get_job(job_id)

    async def retry_job(self, job_id: str) -> bool:
        """Re-queue a failed job for processing.

        Resets status to ``pii_scanning`` and re-queues for the PII worker.

        Args:
            job_id: Job identifier.

        Returns:
            True if the job was re-queued, False if the job was not found
            or had no S3 key.
        """
        job = await self.job_service.get_job(job_id)
        if job is None:
            return False

        s3_key = job.get("s3_key", "")
        if not s3_key:
            return False

        await self.job_service.update_job_status(job_id, "pii_scanning")
        await self.queue_service.queue_pii_job(job_id, s3_key)
        return True

    async def get_result_markdown(self, job_id: str) -> bytes:
        """Download the result markdown for a completed job.

        Equivalent to ``GET /api/v1/documents/{job_id}/result/markdown``.

        Args:
            job_id: Job identifier.

        Returns:
            Raw markdown bytes.

        Raises:
            Same exceptions as StorageService.download_file.
        """
        markdown_key = f"results/{job_id}/result.md"
        return await self.storage_service.download_file(markdown_key)

    async def list_result_figures(self, job_id: str) -> list[FigureInfo]:
        """List figure metadata for a completed job.

        Equivalent to ``GET /api/v1/documents/{job_id}/result/figures``.

        Args:
            job_id: Job identifier.

        Returns:
            List of FigureInfo objects. Empty if no figures exist.
        """
        prefix = f"results/{job_id}/figures/"
        try:
            response = self.storage_service.s3_client.list_objects_v2(
                Bucket=self.storage_service.results_bucket,
                Prefix=prefix,
            )
        except Exception:
            logger.warning(f"Job {job_id}: Could not list figures in S3")
            return []

        figures: list[FigureInfo] = []
        for obj in response.get("Contents", []):
            s3_key = obj["Key"]
            filename = s3_key[len(prefix):]
            if filename:
                figures.append(FigureInfo(s3_key=s3_key, filename=filename))

        return figures

    async def download_figure(self, job_id: str, s3_key: str) -> bytes:
        """Download a single figure from the results bucket.

        Equivalent to ``GET /api/v1/documents/{job_id}/result/figures/{name}``.

        Args:
            job_id: Job identifier (for logging).
            s3_key: Full S3 key of the figure.

        Returns:
            Raw image bytes.

        Raises:
            Same exceptions as StorageService.download_file.
        """
        return await self.storage_service.download_file(s3_key)

    async def create_download_bundle(self, job_id: str, original_filename: str) -> str:
        """Create a download bundle (zip) and return a presigned URL.

        Wraps DownloadBundleService internally.

        Args:
            job_id: Job identifier.
            original_filename: Original PDF filename for zip naming.

        Returns:
            Presigned S3 URL for the zip download.
        """
        from ..canvas.bundle import DownloadBundleService

        bundle_service = DownloadBundleService(storage_service=self.storage_service)
        return await bundle_service.create_bundle(job_id, original_filename)
