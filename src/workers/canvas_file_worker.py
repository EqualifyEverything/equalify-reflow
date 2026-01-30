"""Background worker that polls Canvas courses for new PDF uploads.

Runs on a configurable schedule. For each enabled course:
1. Query Canvas Files API for PDF files
2. Compare against processed-file set in Redis
3. Queue new or updated files for processing
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from io import BytesIO

from ..canvas.client import CanvasAPIClient, CanvasAPIError
from ..canvas.course_config import CourseConfigService, ProcessedFile
from ..config import settings
from ..services.job_service import JobService
from ..services.metrics_service import (
    worker_active_gauge,
    worker_errors_total,
    worker_jobs_processed_total,
)
from ..services.queue_service import QueueService
from ..services.storage_service import StorageService

logger = logging.getLogger(__name__)


class CanvasFileWorker:
    """Background worker that polls Canvas for new PDF uploads.

    Runs on a configurable schedule. For each enabled course:
    1. Query Canvas Files API for PDF files
    2. Compare against processed-file set in Redis
    3. Queue new or updated files for processing
    """

    def __init__(
        self,
        config_service: CourseConfigService,
        job_service: JobService,
        storage_service: StorageService,
        queue_service: QueueService,
        polling_interval_seconds: int | None = None,
    ):
        self.config_service = config_service
        self.job_service = job_service
        self.storage_service = storage_service
        self.queue_service = queue_service
        self.polling_interval_seconds = (
            polling_interval_seconds
            if polling_interval_seconds is not None
            else settings.canvas_polling_interval_seconds
        )
        self.running = False

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Main worker loop. Runs until shutdown_event is set.

        Polls at the interval configured by canvas_polling_interval_seconds.
        """
        self.running = True
        logger.info(f"Canvas file discovery worker started (polling every {self.polling_interval_seconds}s)")

        worker_active_gauge.labels(worker_name="canvas_file").set(1)

        try:
            while self.running and not shutdown_event.is_set():
                try:
                    queued = await self.poll_courses()
                    if queued > 0:
                        logger.info(f"Canvas file poll complete: {queued} new files queued")
                    else:
                        logger.debug("Canvas file poll complete: no new files found")
                except Exception as e:
                    logger.error(f"Canvas file worker error: {e}", exc_info=True)
                    worker_errors_total.labels(worker_name="canvas_file", error_type=type(e).__name__).inc()
                    # Error sleep also checks shutdown to exit promptly
                    for _ in range(settings.worker_error_sleep_seconds):
                        if shutdown_event.is_set():
                            break
                        await asyncio.sleep(1)
                    continue

                # Sleep for the polling interval, checking shutdown periodically
                for _ in range(self.polling_interval_seconds):
                    if shutdown_event.is_set():
                        break
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Canvas file worker received cancellation signal")
            raise
        except Exception as e:
            logger.error(f"Canvas file worker unexpected error: {e}", exc_info=True)
            worker_errors_total.labels(worker_name="canvas_file", error_type=type(e).__name__).inc()

        finally:
            self.running = False
            worker_active_gauge.labels(worker_name="canvas_file").set(0)
            logger.info("Canvas file discovery worker stopped")

    async def poll_courses(self) -> int:
        """Poll all enabled courses for new PDFs.

        Returns:
            Number of new files queued for processing.
        """
        course_ids = await self.config_service.list_enabled_courses()
        if not course_ids:
            logger.debug("No enabled courses to poll")
            return 0

        total_queued = 0
        for course_id in course_ids:
            try:
                queued = await self.poll_course(course_id)
                total_queued += queued
            except Exception as e:
                logger.warning(f"Failed to poll course {course_id}: {e}", exc_info=True)
                worker_errors_total.labels(worker_name="canvas_file", error_type=type(e).__name__).inc()

        logger.info(f"Polled {len(course_ids)} courses, queued {total_queued} files")
        return total_queued

    async def poll_course(self, course_id: str) -> int:
        """Poll a single course for new PDF uploads.

        Args:
            course_id: Canvas course ID

        Returns:
            Number of new files queued from this course.
        """
        config = await self.config_service.get_config(course_id)
        if config is None or not config.canvas_api_token:
            logger.warning(f"Course {course_id} has no API token configured, skipping")
            return 0

        client = CanvasAPIClient(
            base_url=settings.canvas_api_url,
            api_token=config.canvas_api_token,
            rate_limit_buffer=settings.canvas_rate_limit_buffer,
        )

        queued = 0
        try:
            files = await client.list_course_files(
                course_id,
                content_types=["application/pdf"],
                sort="created_at",
                order="desc",
            )

            for canvas_file in files:
                file_id = str(canvas_file.get("id", ""))
                updated_at = canvas_file.get("updated_at", "")

                if not file_id or not updated_at:
                    logger.debug(f"Skipping file with missing id or updated_at: {canvas_file}")
                    continue

                is_new = await self.config_service.is_file_new_or_updated(course_id, file_id, updated_at)

                if is_new:
                    try:
                        job_id = await self._queue_file_for_processing(course_id, canvas_file, client)
                        logger.info(f"Queued file {file_id} from course {course_id} as job {job_id}")
                        queued += 1
                        worker_jobs_processed_total.labels(worker_name="canvas_file", result="success").inc()
                    except Exception as e:
                        logger.error(
                            f"Failed to queue file {file_id} from course {course_id}: {e}",
                            exc_info=True,
                        )
                        worker_errors_total.labels(worker_name="canvas_file", error_type=type(e).__name__).inc()
                else:
                    logger.debug(f"File {file_id} in course {course_id} already processed, skipping")

        finally:
            await client.close()

        return queued

    async def _queue_file_for_processing(
        self,
        course_id: str,
        canvas_file: dict,
        client: CanvasAPIClient,
    ) -> str:
        """Download a Canvas file and queue it for pipeline processing.

        Args:
            course_id: Canvas course ID
            canvas_file: Canvas file object from the API
            client: CanvasAPIClient instance for downloading

        Returns:
            The created job ID.
        """
        file_id = str(canvas_file["id"])
        file_url = canvas_file.get("url", "")
        filename = canvas_file.get("display_name") or canvas_file.get("filename", "document.pdf")
        updated_at = canvas_file.get("updated_at", "")

        # Download the file from Canvas using the direct URL
        logger.info(f"Downloading file {file_id} ({filename}) from Canvas")
        if not file_url:
            raise CanvasAPIError(f"File {file_id} has no download URL")

        try:
            http_client = client._get_client()
            headers: dict[str, str] = {"Authorization": f"Bearer {client.api_token}"}
            if client._is_docker_url:
                headers["Host"] = client._docker_host_header

            download_response = await http_client.get(file_url, headers=headers, follow_redirects=True)
            download_response.raise_for_status()
            file_content = download_response.content
        except Exception as e:
            raise CanvasAPIError(f"Failed to download file {file_id}: {e}") from e

        if len(file_content) < 100:
            raise CanvasAPIError(f"Downloaded file {file_id} too small ({len(file_content)} bytes)")

        # Upload to S3 temp bucket
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        self.storage_service.s3_client.put_object(
            Bucket=self.storage_service.temp_bucket,
            Key=s3_key,
            Body=BytesIO(file_content),
            ContentType="application/pdf",
        )
        logger.info(f"Uploaded file {file_id} to S3: {s3_key}")

        # Create job with canvas_auto source
        await self.job_service.create_job(
            job_id=job_id,
            s3_key=s3_key,
            status="pii_scanning",
            original_filename=filename,
        )

        # Store canvas_auto metadata on the job
        await self.job_service.redis.hset(
            f"{self.job_service.status_prefix}{job_id}",
            mapping={
                "source": "canvas_auto",
                "canvas_course_id": course_id,
                "canvas_file_id": file_id,
            },
        )

        # Queue for PII scanning (standard pipeline entry point)
        await self.queue_service.queue_pii_job(job_id, s3_key)

        # Store processed file record with status "processing"
        now = datetime.now(UTC).isoformat()
        await self.config_service.set_processed_file(
            course_id,
            file_id,
            ProcessedFile(
                canvas_file_id=file_id,
                canvas_updated_at=updated_at,
                job_id=job_id,
                status="processing",
                processed_at=now,
                original_filename=filename,
            ),
        )

        return job_id

    def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info("Stopping Canvas file discovery worker...")
        self.running = False


# Global worker instance
_worker_instance: CanvasFileWorker | None = None


async def start_canvas_file_worker(shutdown_event: asyncio.Event) -> None:
    """Initialize and start the Canvas file discovery worker."""
    global _worker_instance

    logger.info("Initializing Canvas file discovery worker...")

    from ..dependencies import get_redis_client, get_s3_client

    redis_client = await anext(get_redis_client())
    s3_client = await anext(get_s3_client())

    config_service = CourseConfigService(redis_client=redis_client)
    job_service = JobService(redis_client=redis_client)
    queue_service = QueueService(redis_client=redis_client)
    storage_service = StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )

    _worker_instance = CanvasFileWorker(
        config_service=config_service,
        job_service=job_service,
        storage_service=storage_service,
        queue_service=queue_service,
        polling_interval_seconds=settings.canvas_polling_interval_seconds,
    )

    logger.info("Canvas file discovery worker services initialized, starting worker loop...")
    await _worker_instance.run(shutdown_event)


def get_canvas_file_worker() -> CanvasFileWorker | None:
    """Get the global Canvas file worker instance."""
    return _worker_instance
