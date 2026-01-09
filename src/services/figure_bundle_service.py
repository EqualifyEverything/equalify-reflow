"""Figure bundle service for packaging markdown with images.

This service generates downloadable ZIP bundles containing the final markdown
and all extracted figure images, allowing users to download a complete
self-contained document.

Bundle structure:
    document.zip
    ├── document.md      # Final markdown with relative image paths
    └── images/
        ├── figure-1.png
        ├── figure-2.png
        └── ...
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from .storage_service import StorageService

logger = logging.getLogger(__name__)


class FigureBundleService:
    """Service for generating downloadable bundles with markdown and images.

    This service packages the final markdown output together with all
    extracted figure images into a single ZIP file for download.

    Example:
        >>> service = FigureBundleService(storage_service)
        >>> zip_bytes = await service.generate_bundle(
        ...     job_id="job123",
        ...     markdown_key="results/job123/result.md",
        ...     stored_figures=[{"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"}]
        ... )
    """

    def __init__(self, storage_service: StorageService):
        """Initialize figure bundle service.

        Args:
            storage_service: StorageService instance for S3 operations
        """
        self.storage = storage_service

    async def generate_bundle(
        self,
        job_id: str,
        markdown_key: str,
        stored_figures: list[dict],
    ) -> bytes:
        """Generate a ZIP bundle containing markdown and figure images.

        Args:
            job_id: Job identifier
            markdown_key: S3 key for the final markdown
            stored_figures: List of StoredFigure dicts with s3_key and figure_id

        Returns:
            ZIP file contents as bytes

        Raises:
            RuntimeError: If markdown download fails
        """
        logger.info(f"Generating figure bundle for job {job_id}")

        # Create in-memory zip file
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. Add the markdown file
            try:
                markdown_content = await self._download_file(
                    markdown_key, self.storage.results_bucket
                )
                zf.writestr("document.md", markdown_content)
                logger.debug(f"Added document.md ({len(markdown_content)} bytes)")
            except Exception as e:
                logger.error(f"Failed to download markdown for job {job_id}: {e}")
                raise RuntimeError(f"Failed to download markdown: {e}") from e

            # 2. Add figure images
            figures_added = 0
            for fig in stored_figures:
                figure_id = fig.get("figure_id", "")
                s3_key = fig.get("s3_key", "")

                if not s3_key:
                    continue

                try:
                    image_data = await self._download_file(
                        s3_key, self.storage.results_bucket
                    )
                    # Store in images/ subfolder to match relative paths in markdown
                    zf.writestr(f"images/{figure_id}.png", image_data)
                    figures_added += 1
                    logger.debug(f"Added images/{figure_id}.png ({len(image_data)} bytes)")
                except Exception as e:
                    logger.warning(
                        f"Failed to download figure {figure_id} for job {job_id}: {e}"
                    )
                    # Continue with other figures - best effort

            logger.info(
                f"Generated bundle for job {job_id}: "
                f"document.md + {figures_added} figures"
            )

        zip_buffer.seek(0)
        return zip_buffer.getvalue()

    async def _download_file(self, s3_key: str, bucket: str) -> bytes:
        """Download a file from S3.

        Args:
            s3_key: S3 object key
            bucket: S3 bucket name

        Returns:
            File contents as bytes
        """
        try:
            response = self.storage.s3_client.get_object(
                Bucket=bucket,
                Key=s3_key,
            )
            body: bytes = response["Body"].read()
            return body
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(
                f"Failed to download s3://{bucket}/{s3_key}: {error_code}"
            ) from e
