"""Canvas publisher service for orchestrating page creation.

Downloads processed markdown and figures from S3, uploads images to
Canvas Files, renders HTML, creates a Canvas Page, and links it in
the appropriate course module.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .client import CanvasAPIError

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from ..services.storage_service import StorageService
    from .bundle import DownloadBundleService
    from .client import CanvasAPIClient
    from .renderer import CanvasHTMLRenderer

logger = logging.getLogger(__name__)

# Redis key prefix for publish results
_PUBLISH_KEY_PREFIX = "eq-pdf:published"

# TTL for publish results: 30 days (matches completed job TTL)
_PUBLISH_TTL_SECONDS = 30 * 24 * 60 * 60


class PublishError(Exception):
    """Raised when a critical publishing step fails."""

    def __init__(self, message: str, job_id: str, course_id: str) -> None:
        self.job_id = job_id
        self.course_id = course_id
        super().__init__(f"PublishError [job={job_id}, course={course_id}]: {message}")


class PublishResult(BaseModel):
    """Result of publishing a document to Canvas."""

    job_id: str
    course_id: str
    canvas_page_url: str
    canvas_page_id: int
    canvas_file_ids: list[int]
    download_url: str
    page_title: str
    published: bool
    figure_count: int


class CanvasPublisherService:
    """Orchestrate publishing pipeline output to Canvas."""

    def __init__(
        self,
        canvas_client: CanvasAPIClient,
        renderer: CanvasHTMLRenderer,
        bundle_service: DownloadBundleService,
        storage_service: StorageService,
    ):
        self.canvas = canvas_client
        self.renderer = renderer
        self.bundle = bundle_service
        self.storage = storage_service

    async def publish(
        self,
        job_id: str,
        course_id: str,
        original_filename: str,
        canvas_file_id: str,
        redis_client: Redis,
        published: bool = False,
    ) -> PublishResult:
        """Publish a processed document as a Canvas Page.

        Orchestrates the full workflow:
        1. Download result markdown and figures from S3
        2. Upload figures to Canvas Files API
        3. Create download bundle (zip) and get presigned URL
        4. Render markdown to HTML with Canvas image URLs and download link
        5. Create or update Canvas Page
        6. Add page to course module (if PDF is in a module)
        7. Store publish result in Redis

        Args:
            job_id: The processing job ID
            course_id: Canvas course ID
            original_filename: Original PDF filename (e.g., "lecture_notes.pdf")
            canvas_file_id: Canvas file ID of the original PDF
            redis_client: Redis async client for storing publish result
            published: If True, publish the page immediately. If False (default), create as draft.

        Returns:
            PublishResult with page URL, status, and metadata

        Raises:
            PublishError: If markdown download or page creation fails
        """
        logger.info(f"Publishing job {job_id} to course {course_id}")

        # 1. Download result markdown from S3
        markdown_content = await self._download_markdown(job_id, course_id)

        # 2. Download and upload figures to Canvas
        image_url_map, canvas_file_ids = await self._upload_figures_to_canvas(job_id, course_id)

        # 3. Create download bundle
        download_url = await self.bundle.create_bundle(job_id, original_filename)

        # 4. Render markdown to HTML
        html_body = self.renderer.render(
            markdown_content=markdown_content,
            image_url_map=image_url_map,
            download_url=download_url,
        )

        # 5. Create or update Canvas Page
        page_title = self._build_page_title(original_filename)
        page_data, was_update = await self._create_or_update_page(job_id, course_id, page_title, html_body, published)

        page_url = page_data.get("url", "")
        page_id = page_data.get("page_id", 0)

        # 6. Module placement (non-fatal; skip on update to avoid duplicates)
        if not was_update:
            await self._place_in_module(job_id, course_id, canvas_file_id, page_title, page_url)
        else:
            logger.info(f"Job {job_id}: Skipping module placement (page updated, not created)")

        # 7. Build and store result
        result = PublishResult(
            job_id=job_id,
            course_id=course_id,
            canvas_page_url=page_url,
            canvas_page_id=page_id,
            canvas_file_ids=canvas_file_ids,
            download_url=download_url,
            page_title=page_title,
            published=published,
            figure_count=len(canvas_file_ids),
        )

        await self._store_result(redis_client, course_id, canvas_file_id, result)

        logger.info(f"Published job {job_id} as page '{page_title}' (url={page_url}, figures={len(canvas_file_ids)})")
        return result

    # --- Internal helpers ---

    async def _download_markdown(self, job_id: str, course_id: str) -> str:
        """Download result markdown from S3.

        Raises:
            PublishError: If the markdown file cannot be downloaded.
        """
        markdown_key = f"results/{job_id}/result.md"
        try:
            data = await self.storage.download_file(markdown_key)
            return data.decode("utf-8")
        except Exception as e:
            raise PublishError(
                f"Failed to download result markdown: {e}",
                job_id=job_id,
                course_id=course_id,
            ) from e

    async def _upload_figures_to_canvas(self, job_id: str, course_id: str) -> tuple[dict[str, str], list[int]]:
        """Download figures from S3 and upload to Canvas Files.

        Returns:
            Tuple of (image_url_map, canvas_file_ids).
            image_url_map maps local paths to Canvas URLs.
        """
        image_url_map: dict[str, str] = {}
        canvas_file_ids: list[int] = []

        # List figures in S3
        prefix = f"results/{job_id}/figures/"
        try:
            response = self.storage.s3_client.list_objects_v2(
                Bucket=self.storage.results_bucket,
                Prefix=prefix,
            )
        except Exception:
            logger.warning(f"Job {job_id}: Could not list figures in S3")
            return image_url_map, canvas_file_ids

        objects = response.get("Contents", [])
        if not objects:
            logger.info(f"Job {job_id}: No figures found in S3")
            return image_url_map, canvas_file_ids

        for idx, obj in enumerate(objects):
            s3_key = obj["Key"]
            original_filename = s3_key[len(prefix) :]
            if not original_filename:
                continue

            try:
                # Download from S3
                figure_data = await self.storage.download_file(s3_key)

                # Upload to Canvas
                canvas_filename = f"{job_id}-figure-{idx + 1}.png"
                canvas_file = await self.canvas.upload_file(
                    course_id=course_id,
                    file_content=figure_data,
                    filename=canvas_filename,
                    content_type="image/png",
                    parent_folder_path="equalify-reflow",
                )

                file_id = canvas_file.get("id")

                # Build preview URL for Canvas pages
                if file_id:
                    canvas_file_ids.append(file_id)
                    preview_url = f"{self.canvas.base_url}/courses/{course_id}/files/{file_id}/preview"
                    image_url_map[f"images/{original_filename}"] = preview_url

                logger.debug(f"Job {job_id}: Uploaded figure {original_filename} to Canvas (id={file_id})")

            except Exception as e:
                logger.warning(f"Job {job_id}: Failed to upload figure {original_filename} to Canvas: {e}")

        logger.info(f"Job {job_id}: Uploaded {len(canvas_file_ids)}/{len(objects)} figures to Canvas")
        return image_url_map, canvas_file_ids

    @staticmethod
    def _build_page_title(original_filename: str) -> str:
        """Build Canvas Page title from the original filename.

        Examples:
            "lecture_notes.pdf" -> "lecture_notes - Reflow"
            "Chapter 4 Reading.pdf" -> "Chapter 4 Reading - Reflow"
        """
        base_name = os.path.splitext(original_filename)[0]
        return f"{base_name} - Reflow"

    async def _create_or_update_page(
        self,
        job_id: str,
        course_id: str,
        title: str,
        body: str,
        published: bool,
    ) -> tuple[dict, bool]:
        """Create a new page or update an existing one.

        Checks for an existing page by URL slug before creating.

        Returns:
            Tuple of (page_data, was_update). was_update is True when
            an existing page was updated instead of creating a new one.

        Raises:
            PublishError: If page creation/update fails.
        """
        # Canvas generates URL slugs from titles by lowering + hyphenating
        # We use the title to check, but Canvas API handles slug generation
        page_slug = title.lower().replace(" ", "-")

        try:
            existing_page = await self.canvas.get_page(course_id, page_slug)
        except CanvasAPIError:
            existing_page = None

        try:
            if existing_page:
                logger.info(f"Job {job_id}: Page '{title}' already exists, updating")
                page_data = await self.canvas.update_page(
                    course_id=course_id,
                    page_url=page_slug,
                    body=body,
                    title=title,
                    published=published,
                    editing_roles="teachers",
                )
                return page_data, True
            else:
                page_data = await self.canvas.create_page(
                    course_id=course_id,
                    title=title,
                    body=body,
                    published=published,
                    editing_roles="teachers",
                )
                return page_data, False
        except CanvasAPIError as e:
            raise PublishError(
                f"Failed to create/update Canvas page: {e}",
                job_id=job_id,
                course_id=course_id,
            ) from e

    async def _place_in_module(
        self,
        job_id: str,
        course_id: str,
        canvas_file_id: str,
        page_title: str,
        page_url: str,
    ) -> None:
        """Find the PDF in a module and add the page after it.

        This is non-fatal: failures are logged but do not raise.
        """
        try:
            modules = await self.canvas.list_modules(course_id, include_items=True)
        except CanvasAPIError as e:
            logger.warning(f"Job {job_id}: Failed to list modules for course {course_id}: {e}")
            return

        for module in modules:
            items = module.get("items", [])
            for item in items:
                # Canvas module items for files have content_id matching the file ID
                item_content_id = str(item.get("content_id", ""))
                if item_content_id == str(canvas_file_id):
                    module_id = str(module["id"])
                    pdf_position = item.get("position", 1)
                    try:
                        await self.canvas.create_module_item(
                            course_id=course_id,
                            module_id=module_id,
                            title=page_title,
                            item_type="Page",
                            page_url=page_url,
                            position=pdf_position + 1,
                        )
                        logger.info(f"Job {job_id}: Added page to module {module_id} at position {pdf_position + 1}")
                    except CanvasAPIError as e:
                        logger.warning(f"Job {job_id}: Failed to add page to module {module_id}: {e}")
                    return

        logger.info(f"Job {job_id}: PDF file {canvas_file_id} not found in any module, skipping module placement")

    @staticmethod
    async def _store_result(
        redis_client: Redis,
        course_id: str,
        canvas_file_id: str,
        result: PublishResult,
    ) -> None:
        """Store publish result in Redis for status tracking."""
        key = f"{_PUBLISH_KEY_PREFIX}:{course_id}:{canvas_file_id}"
        await redis_client.set(key, result.model_dump_json(), ex=_PUBLISH_TTL_SECONDS)
        logger.debug(f"Stored publish result at {key}")
