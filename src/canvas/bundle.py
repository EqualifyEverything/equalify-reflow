"""Download bundle service for packaging markdown + image assets.

Packages a job's markdown output and extracted images into a downloadable
zip file, uploads it to S3, and returns a presigned download URL.

Bundle structure:
    lecture_notes-reflow/
        lecture_notes.md      # The result markdown
        images/
            figure_1.png      # Extracted figures
            figure_2.png
            ...
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError
from fastapi import HTTPException

if TYPE_CHECKING:
    from ..services.storage_service import StorageService

logger = logging.getLogger(__name__)

# Presigned URL expiry: 7 days in seconds
_SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60


class BundleError(Exception):
    """Raised when bundle creation fails."""


class DownloadBundleService:
    """Package markdown + image assets into a downloadable zip."""

    def __init__(self, storage_service: StorageService):
        self.storage = storage_service

    async def create_bundle(
        self,
        job_id: str,
        original_filename: str,
    ) -> str:
        """Create a zip bundle of markdown + images and return a presigned download URL.

        Reads the result markdown and figure images from S3 results bucket,
        packages them into a zip, uploads the zip to S3, and returns a
        presigned URL for download.

        Args:
            job_id: The processing job ID
            original_filename: Original PDF filename (used for zip naming)

        Returns:
            Presigned S3 URL for the zip download (valid for 7 days)

        Raises:
            BundleError: If result markdown is not found for the job
        """
        base_name = os.path.splitext(original_filename)[0]
        folder_name = f"{base_name}-reflow"

        # 1. Download result markdown
        markdown_key = f"results/{job_id}/result.md"
        markdown_content = await self._download_result_markdown(job_id, markdown_key)

        # 2. Download figures (best-effort)
        figures = await self._list_and_download_figures(job_id)

        # 3. Build zip in memory
        zip_bytes = self._build_zip(folder_name, base_name, markdown_content, figures)

        file_count = 1 + len(figures)
        logger.info(f"Job {job_id}: Created bundle with {file_count} files ({len(zip_bytes)} bytes)")

        # 4. Upload zip to S3
        bundle_key = f"results/{job_id}/bundle.zip"
        await self.storage.upload_file(
            key=bundle_key,
            content=zip_bytes,
            content_type="application/zip",
        )

        # 5. Generate presigned URL (7-day expiry)
        url = self.storage.s3_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.storage.results_bucket,
                "Key": bundle_key,
            },
            ExpiresIn=_SEVEN_DAYS_SECONDS,
        )

        return url  # type: ignore[no-any-return]

    async def _download_result_markdown(self, job_id: str, key: str) -> bytes:
        """Download result.md from S3 results bucket.

        Uses StorageService.download_file for circuit breaker protection
        and retry logic.

        Raises:
            BundleError: If the markdown file does not exist.
        """
        try:
            return await self.storage.download_file(key)
        except HTTPException as e:
            if e.status_code == 404:
                raise BundleError(f"No result markdown found for job {job_id}") from e
            raise

    async def _list_and_download_figures(self, job_id: str) -> list[tuple[str, bytes]]:
        """List and download all figures for a job.

        Uses StorageService.download_file for each figure to gain circuit
        breaker protection and retry-with-backoff logic.

        Returns:
            List of (filename, data) tuples. Empty if no figures exist.
        """
        prefix = f"results/{job_id}/figures/"
        figures: list[tuple[str, bytes]] = []

        try:
            response = self.storage.s3_client.list_objects_v2(
                Bucket=self.storage.results_bucket,
                Prefix=prefix,
            )
        except ClientError:
            return figures

        for obj in response.get("Contents", []):
            key = obj["Key"]
            filename = key[len(prefix) :]
            if not filename:
                continue
            try:
                data = await self.storage.download_file(key)
                figures.append((filename, data))
            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to download figure {key}: {e}")

        return figures

    @staticmethod
    def _build_zip(
        folder_name: str,
        base_name: str,
        markdown_content: bytes,
        figures: list[tuple[str, bytes]],
    ) -> bytes:
        """Build a zip file in memory.

        Args:
            folder_name: Root folder name inside the zip
            base_name: Filename stem for the markdown file
            markdown_content: Raw markdown bytes
            figures: List of (filename, data) tuples

        Returns:
            Zip file bytes
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{folder_name}/{base_name}.md", markdown_content)
            for filename, data in figures:
                zf.writestr(f"{folder_name}/images/{filename}", data)
        buf.seek(0)
        return buf.getvalue()
