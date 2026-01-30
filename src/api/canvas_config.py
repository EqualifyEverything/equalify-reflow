"""Canvas course configuration API endpoints.

REST endpoints for managing per-course auto-publishing settings,
viewing document processing status, and triggering manual actions
(process, retry, publish).
"""

import logging
import uuid
from datetime import UTC, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..canvas.client import CanvasAPIClient, CanvasAPIError
from ..canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from ..config import settings
from ..dependencies import (
    get_course_config_service,
    get_job_service,
    get_queue_service,
    get_redis_client,
    get_storage_service,
)
from ..services.job_service import JobService
from ..services.queue_service import QueueService
from ..services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/canvas/courses", tags=["canvas-config"])


# --- Request/Response Models ---


class CourseConfigUpdate(BaseModel):
    """Request body for updating course config."""

    enabled: bool | None = None
    auto_publish_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0,
    )


class CourseConfigResponse(BaseModel):
    """Response for course config endpoints."""

    course_id: str
    enabled: bool
    auto_publish_threshold: float
    created_at: str | None = None
    updated_at: str | None = None


class DocumentStatusResponse(BaseModel):
    """Response for document status endpoints."""

    canvas_file_id: str
    original_filename: str
    status: str  # "processing", "completed", "failed", "published"
    job_id: str | None = None
    confidence_score: float | None = None
    canvas_page_url: str | None = None
    canvas_page_id: int | None = None
    download_url: str | None = None
    processed_at: str | None = None
    published_at: str | None = None
    error_message: str | None = None


# --- Settings Endpoints ---


@router.get("/{course_id}/config", response_model=CourseConfigResponse)
async def get_course_config(
    course_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
) -> CourseConfigResponse:
    """Get auto-publishing configuration for a course.

    Returns current settings or defaults if not configured.
    """
    config = await config_service.get_config(course_id)
    if config is None:
        # Return defaults for unconfigured courses
        return CourseConfigResponse(
            course_id=course_id,
            enabled=False,
            auto_publish_threshold=1.0,
        )
    return CourseConfigResponse(
        course_id=course_id,
        enabled=config.enabled,
        auto_publish_threshold=config.auto_publish_threshold,
        created_at=config.created_at or None,
        updated_at=config.updated_at or None,
    )


@router.put("/{course_id}/config", response_model=CourseConfigResponse)
async def update_course_config(
    course_id: str,
    body: CourseConfigUpdate,
    config_service: CourseConfigService = Depends(get_course_config_service),
) -> CourseConfigResponse:
    """Update auto-publishing configuration for a course.

    Enables or disables auto-processing, sets auto-publish threshold.
    """
    now = datetime.now(UTC).isoformat()

    existing = await config_service.get_config(course_id)
    if existing is None:
        # Create new config with defaults
        existing = CourseConfig(created_at=now)

    # Apply partial updates
    if body.enabled is not None:
        existing.enabled = body.enabled
    if body.auto_publish_threshold is not None:
        existing.auto_publish_threshold = body.auto_publish_threshold

    existing.updated_at = now
    if not existing.created_at:
        existing.created_at = now

    await config_service.set_config(course_id, existing)

    return CourseConfigResponse(
        course_id=course_id,
        enabled=existing.enabled,
        auto_publish_threshold=existing.auto_publish_threshold,
        created_at=existing.created_at or None,
        updated_at=existing.updated_at or None,
    )


# --- Document Status Endpoints ---


async def _build_document_status(
    course_id: str,
    file_id: str,
    processed_file: ProcessedFile,
    config_service: CourseConfigService,
    job_service: JobService,
) -> DocumentStatusResponse:
    """Build a DocumentStatusResponse by merging processed file and publish result data."""
    publish_result = await config_service.get_publish_result(course_id, file_id)

    # Determine effective status
    effective_status = processed_file.status
    canvas_page_url: str | None = None
    canvas_page_id: int | None = None
    download_url: str | None = None
    published_at: str | None = None
    confidence_score: float | None = None
    error_message: str | None = None

    if publish_result:
        effective_status = "published"
        canvas_page_url = publish_result.get("canvas_page_url")
        canvas_page_id_str = publish_result.get("canvas_page_id")
        if canvas_page_id_str:
            try:
                canvas_page_id = int(canvas_page_id_str)
            except (ValueError, TypeError):
                pass
        download_url = publish_result.get("download_url")
        published_at = publish_result.get("published_at")

    # Get confidence score and error from job if available
    if processed_file.job_id:
        job = await job_service.get_job(processed_file.job_id)
        if job:
            score = job.get("confidence_score")
            if score is not None:
                try:
                    confidence_score = float(score)
                except (ValueError, TypeError):
                    pass
            if job.get("status") == "failed":
                error_message = job.get("error")

    return DocumentStatusResponse(
        canvas_file_id=processed_file.canvas_file_id,
        original_filename=processed_file.original_filename,
        status=effective_status,
        job_id=processed_file.job_id,
        confidence_score=confidence_score,
        canvas_page_url=canvas_page_url,
        canvas_page_id=canvas_page_id,
        download_url=download_url,
        processed_at=processed_file.processed_at,
        published_at=published_at,
        error_message=error_message,
    )


@router.get("/{course_id}/documents", response_model=list[DocumentStatusResponse])
async def list_course_documents(
    course_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> list[DocumentStatusResponse]:
    """List all tracked PDFs in a course with their processing status.

    Returns processed files merged with their publish status.
    """
    processed_files = await config_service.list_processed_files(course_id)
    results: list[DocumentStatusResponse] = []

    for file_id, processed_file in processed_files.items():
        doc_status = await _build_document_status(
            course_id, file_id, processed_file, config_service, job_service,
        )
        results.append(doc_status)

    return results


@router.get("/{course_id}/documents/{file_id}", response_model=DocumentStatusResponse)
async def get_document_status(
    course_id: str,
    file_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> DocumentStatusResponse:
    """Get detailed status for a specific document."""
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {file_id} not found in course {course_id}",
        )

    return await _build_document_status(
        course_id, file_id, processed_file, config_service, job_service,
    )


# --- Action Endpoints ---


@router.post("/{course_id}/documents/{file_id}/process")
async def trigger_processing(
    course_id: str,
    file_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
    queue_service: QueueService = Depends(get_queue_service),
    storage: StorageService = Depends(get_storage_service),
) -> dict:
    """Manually trigger processing for a Canvas file.

    Downloads the file from Canvas and queues for the pipeline.
    Used for backfilling existing PDFs or re-processing.
    """
    config = await config_service.get_config(course_id)
    if config is None or not config.canvas_api_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Course {course_id} has no Canvas API token configured",
        )

    # Create Canvas client and fetch the file info
    client = CanvasAPIClient(
        base_url=settings.canvas_api_url,
        api_token=config.canvas_api_token,
        rate_limit_buffer=settings.canvas_rate_limit_buffer,
        host_header=settings.canvas_host_header,
    )

    try:
        # List files and find the target file
        files = await client.list_course_files(
            course_id, content_types=["application/pdf"],
        )
        canvas_file = next((f for f in files if str(f.get("id", "")) == file_id), None)
        if canvas_file is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File {file_id} not found in Canvas course {course_id}",
            )

        # Download the file
        file_url = canvas_file.get("url", "")
        if not file_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File {file_id} has no download URL",
            )

        # Rewrite localhost URLs for Docker container networking
        file_url = client.rewrite_canvas_url(file_url)

        http_client = client._get_client()
        headers: dict[str, str] = {"Authorization": f"Bearer {client.api_token}"}
        if client._is_docker_url:
            headers["Host"] = client._docker_host_header

        download_response = await http_client.get(file_url, headers=headers, follow_redirects=True)
        download_response.raise_for_status()
        file_content = download_response.content

        # Upload to S3 temp bucket
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"
        filename = canvas_file.get("display_name") or canvas_file.get("filename", "document.pdf")

        storage.s3_client.put_object(
            Bucket=storage.temp_bucket,
            Key=s3_key,
            Body=BytesIO(file_content),
            ContentType="application/pdf",
        )

        # Create job
        await job_service.create_job(
            job_id=job_id,
            s3_key=s3_key,
            status="pii_scanning",
            original_filename=filename,
        )

        # Store canvas metadata on job
        await job_service.redis.hset(
            f"{job_service.status_prefix}{job_id}",
            mapping={
                "source": "canvas_manual",
                "course_id": course_id,
                "canvas_file_id": file_id,
            },
        )

        # Queue for PII scanning
        await queue_service.queue_pii_job(job_id, s3_key)

        # Store processed file record
        now = datetime.now(UTC).isoformat()
        updated_at = canvas_file.get("updated_at", now)
        await config_service.set_processed_file(
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

        return {"job_id": job_id, "status": "processing", "message": f"File {file_id} queued for processing"}

    except HTTPException:
        raise
    except CanvasAPIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Canvas API error: {e}",
        )
    except Exception as e:
        logger.error(f"Failed to trigger processing for file {file_id} in course {course_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger processing: {e}",
        )
    finally:
        await client.close()


@router.post("/{course_id}/documents/{file_id}/retry")
async def retry_processing(
    course_id: str,
    file_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
    queue_service: QueueService = Depends(get_queue_service),
) -> dict:
    """Retry processing for a failed document.

    Only works if the document's status is 'failed'.
    """
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {file_id} not found in course {course_id}",
        )

    if processed_file.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document {file_id} is not in failed state (current: {processed_file.status})",
        )

    # Get the job to find the S3 key
    job = await job_service.get_job(processed_file.job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {processed_file.job_id} not found",
        )

    s3_key = job.get("s3_key", "")
    if not s3_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original file S3 key not found in job record",
        )

    # Re-queue for PII scanning
    await job_service.update_job_status(processed_file.job_id, "pii_scanning")
    await queue_service.queue_pii_job(processed_file.job_id, s3_key)

    # Update processed file status
    now = datetime.now(UTC).isoformat()
    processed_file.status = "processing"
    processed_file.processed_at = now
    await config_service.set_processed_file(course_id, file_id, processed_file)

    return {
        "job_id": processed_file.job_id,
        "status": "processing",
        "message": f"Document {file_id} re-queued for processing",
    }


@router.post("/{course_id}/documents/{file_id}/publish")
async def publish_document(
    course_id: str,
    file_id: str,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service),
    redis_client=Depends(get_redis_client),
) -> dict:
    """Publish a draft Canvas Page for a completed document.

    Only works if the document has been processed successfully.
    """
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {file_id} not found in course {course_id}",
        )

    if processed_file.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document {file_id} is not completed (current: {processed_file.status})",
        )

    config = await config_service.get_config(course_id)
    if config is None or not config.canvas_api_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Course {course_id} has no Canvas API token configured",
        )

    # Use the publisher service (lazy imports to avoid circular deps at module level)
    from ..canvas.bundle import DownloadBundleService
    from ..canvas.publisher import CanvasPublisherService
    from ..canvas.renderer import CanvasHTMLRenderer

    canvas_client = CanvasAPIClient(
        base_url=settings.canvas_api_url,
        api_token=config.canvas_api_token,
        rate_limit_buffer=settings.canvas_rate_limit_buffer,
        host_header=settings.canvas_host_header,
    )

    try:
        renderer = CanvasHTMLRenderer()
        bundle_service = DownloadBundleService(storage_service=storage)
        publisher = CanvasPublisherService(
            canvas_client=canvas_client,
            renderer=renderer,
            bundle_service=bundle_service,
            storage_service=storage,
        )

        result = await publisher.publish(
            job_id=processed_file.job_id,
            course_id=course_id,
            original_filename=processed_file.original_filename,
            canvas_file_id=file_id,
            redis_client=redis_client,
            published=True,
        )

        return {
            "status": "published",
            "canvas_page_url": result.canvas_page_url,
            "canvas_page_id": result.canvas_page_id,
            "message": f"Document {file_id} published to Canvas",
        }

    except Exception as e:
        logger.error(f"Failed to publish document {file_id} in course {course_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish document: {e}",
        )
    finally:
        await canvas_client.close()
