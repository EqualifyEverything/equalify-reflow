"""Server-rendered instructor dashboard for Canvas LTI course_navigation.

Provides Jinja2-rendered views for:
- Document list (index) with status, confidence, actions
- Course settings (enable/disable auto-processing, threshold)
- Document detail with action buttons
- htmx endpoints for publish, retry, and settings save
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from redis.asyncio import Redis

from ..canvas.course_config import CourseConfig, CourseConfigService
from ..dependencies import get_course_config_service, get_job_service, get_redis_client
from ..lti.adapters import RedisLaunchDataStorage
from ..services.job_service import JobService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti/dashboard", tags=["dashboard"])

# Jinja2 template directory
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


async def _get_lti_session(request: Request, redis: Redis) -> dict | None:
    """Validate LTI session token against Redis.

    LTI course_navigation launches pass a session token as a query
    parameter. This validates the token against Redis to ensure
    the session is authentic and not expired.

    Args:
        request: FastAPI request object
        redis: Redis client for session lookup

    Returns:
        LTI session data dict or None if no valid session.
    """
    session_id = request.query_params.get("lti_session")
    if not session_id:
        return None
    storage = RedisLaunchDataStorage(redis)
    session_data = await storage.validate_session_async(session_id)
    if session_data is None:
        return None
    session_data["session_id"] = session_id
    return session_data


def _no_session_response(request: Request) -> HTMLResponse:
    """Return error page when no LTI session is found."""
    return templates.TemplateResponse(
        request,
        "base.html",
        {
            "title": "Session Error",
            "error_message": "Please launch this tool from Canvas.",
        },
        status_code=403,
    )


# --- View Routes ---


@router.get("/{course_id}", response_class=HTMLResponse)
async def dashboard_index(
    course_id: str,
    request: Request,
    redis: Redis = Depends(get_redis_client),
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Render the document list view."""
    session = await _get_lti_session(request, redis)
    if not session:
        return _no_session_response(request)

    config = await config_service.get_config(course_id)
    processed_files = await config_service.list_processed_files(course_id)

    documents = []
    for file_id, pf in processed_files.items():
        publish_result = await config_service.get_publish_result(course_id, file_id)

        effective_status = pf.status
        canvas_page_url: str | None = None
        confidence_score: float | None = None

        if publish_result:
            effective_status = "published"
            canvas_page_url = publish_result.get("canvas_page_url")

        if pf.job_id:
            job = await job_service.get_job(pf.job_id)
            if job:
                score = job.get("confidence_score")
                if score is not None:
                    try:
                        confidence_score = float(score)
                    except (ValueError, TypeError):
                        pass

        documents.append(
            {
                "file_id": file_id,
                "filename": pf.original_filename,
                "status": effective_status,
                "confidence": confidence_score,
                "canvas_page_url": canvas_page_url,
                "processed_at": pf.processed_at,
                "job_id": pf.job_id,
            }
        )

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "title": "Document Dashboard",
            "course_id": course_id,
            "config": config,
            "documents": documents,
            "lti_session": request.query_params.get("lti_session", ""),
        },
    )


@router.get("/{course_id}/settings", response_class=HTMLResponse)
async def dashboard_settings(
    course_id: str,
    request: Request,
    redis: Redis = Depends(get_redis_client),
    config_service: CourseConfigService = Depends(get_course_config_service),
) -> HTMLResponse:
    """Render the settings view."""
    session = await _get_lti_session(request, redis)
    if not session:
        return _no_session_response(request)

    config = await config_service.get_config(course_id)
    if config is None:
        config = CourseConfig()

    return templates.TemplateResponse(
        request,
        "dashboard/settings.html",
        {
            "title": "Course Settings",
            "course_id": course_id,
            "config": config,
            "lti_session": request.query_params.get("lti_session", ""),
        },
    )


@router.get("/{course_id}/documents/{file_id}", response_class=HTMLResponse)
async def dashboard_document_detail(
    course_id: str,
    file_id: str,
    request: Request,
    redis: Redis = Depends(get_redis_client),
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Render the document detail view."""
    session = await _get_lti_session(request, redis)
    if not session:
        return _no_session_response(request)

    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {file_id} not found in course {course_id}",
        )

    publish_result = await config_service.get_publish_result(course_id, file_id)

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
        page_id_str = publish_result.get("canvas_page_id")
        if page_id_str:
            try:
                canvas_page_id = int(page_id_str)
            except (ValueError, TypeError):
                pass
        download_url = publish_result.get("download_url")
        published_at = publish_result.get("published_at")

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

    document = {
        "file_id": file_id,
        "filename": processed_file.original_filename,
        "status": effective_status,
        "confidence": confidence_score,
        "canvas_page_url": canvas_page_url,
        "canvas_page_id": canvas_page_id,
        "download_url": download_url,
        "processed_at": processed_file.processed_at,
        "published_at": published_at,
        "error_message": error_message,
        "job_id": processed_file.job_id,
    }

    return templates.TemplateResponse(
        request,
        "dashboard/document_detail.html",
        {
            "title": f"Document: {processed_file.original_filename}",
            "course_id": course_id,
            "document": document,
            "lti_session": request.query_params.get("lti_session", ""),
        },
    )


# --- htmx Fragment Routes ---


@router.get("/{course_id}/documents/{file_id}/detail_fragment", response_class=HTMLResponse)
async def dashboard_document_detail_fragment(
    course_id: str,
    file_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Return the document detail card as an HTML fragment for htmx polling.

    Used by the detail view to poll for status updates every 5s when
    a document is in the 'processing' state. Returns the updated card
    element for in-place swap.
    """
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        return HTMLResponse(
            content='<div id="document-detail" class="bg-white rounded-lg shadow p-6 max-w-2xl">'
            '<p class="text-red-600">Document not found</p></div>',
            status_code=404,
        )

    publish_result = await config_service.get_publish_result(course_id, file_id)

    effective_status = processed_file.status
    canvas_page_url: str | None = None
    download_url: str | None = None
    published_at: str | None = None
    confidence_score: float | None = None
    error_message: str | None = None

    if publish_result:
        effective_status = "published"
        canvas_page_url = publish_result.get("canvas_page_url")
        download_url = publish_result.get("download_url")
        published_at = publish_result.get("published_at")

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

    document = {
        "file_id": file_id,
        "filename": processed_file.original_filename,
        "status": effective_status,
        "confidence": confidence_score,
        "canvas_page_url": canvas_page_url,
        "download_url": download_url,
        "processed_at": processed_file.processed_at,
        "published_at": published_at,
        "error_message": error_message,
    }

    lti_session = request.query_params.get("lti_session", "")
    fragment_html = _render_detail_fragment(course_id, document, lti_session)
    return HTMLResponse(content=fragment_html)


# --- htmx Action Routes ---


@router.get("/{course_id}/documents/{file_id}/row", response_class=HTMLResponse)
async def dashboard_document_row(
    course_id: str,
    file_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Return a single table row fragment for htmx polling.

    Used by processing rows to poll for status updates every 5s.
    Returns the updated <tr> element for in-place swap.
    """
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        not_found_html = (
            f'<tr id="doc-{file_id}">'
            '<td colspan="5" class="px-4 py-3 text-sm text-red-600">'
            "Document not found</td></tr>"
        )
        return HTMLResponse(content=not_found_html, status_code=404)

    publish_result = await config_service.get_publish_result(course_id, file_id)

    effective_status = processed_file.status
    canvas_page_url: str | None = None
    confidence_score: float | None = None

    if publish_result:
        effective_status = "published"
        canvas_page_url = publish_result.get("canvas_page_url")

    if processed_file.job_id:
        job = await job_service.get_job(processed_file.job_id)
        if job:
            score = job.get("confidence_score")
            if score is not None:
                try:
                    confidence_score = float(score)
                except (ValueError, TypeError):
                    pass

    lti_session = request.query_params.get("lti_session", "")
    row_html = _render_document_row_fragment(
        course_id,
        file_id,
        processed_file.original_filename,
        effective_status,
        confidence_score,
        processed_file.processed_at,
        lti_session,
        canvas_page_url=canvas_page_url,
    )

    return HTMLResponse(content=row_html)


@router.post("/{course_id}/documents/{file_id}/publish", response_class=HTMLResponse)
async def dashboard_publish(
    course_id: str,
    file_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Publish a draft page and return updated HTML fragment (htmx)."""
    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        return HTMLResponse(
            content='<span class="text-red-600">Document not found</span>',
            status_code=404,
        )

    if processed_file.status != "completed":
        return HTMLResponse(
            content=f'<span class="text-red-600">Cannot publish: status is {processed_file.status}</span>',
            status_code=400,
        )

    config = await config_service.get_config(course_id)
    if config is None or not config.canvas_api_token:
        return HTMLResponse(
            content='<span class="text-red-600">No Canvas API token configured</span>',
            status_code=400,
        )

    try:
        from ..canvas.bundle import DownloadBundleService
        from ..canvas.client import CanvasAPIClient
        from ..canvas.publisher import CanvasPublisherService
        from ..canvas.renderer import CanvasHTMLRenderer
        from ..config import settings
        from ..dependencies import get_redis_client, get_storage_service

        # Get services - lazy import to avoid circular deps
        storage = await get_storage_service()
        redis_gen = get_redis_client()
        redis_client = await anext(redis_gen)

        canvas_client = CanvasAPIClient(
            base_url=settings.canvas_api_url,
            api_token=config.canvas_api_token,
            rate_limit_buffer=settings.canvas_rate_limit_buffer,
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

            await publisher.publish(
                job_id=processed_file.job_id,
                course_id=course_id,
                original_filename=processed_file.original_filename,
                canvas_file_id=file_id,
                redis_client=redis_client,
                published=True,
            )

            return HTMLResponse(
                content=_render_document_row_fragment(
                    course_id,
                    file_id,
                    processed_file.original_filename,
                    "published",
                    None,
                    processed_file.processed_at,
                    request.query_params.get("lti_session", ""),
                ),
            )
        finally:
            await canvas_client.close()

    except Exception as e:
        logger.error(f"Dashboard publish failed for {file_id}: {e}", exc_info=True)
        return HTMLResponse(
            content=f'<span class="text-red-600">Publish failed: {e}</span>',
            status_code=500,
        )


@router.post("/{course_id}/documents/{file_id}/retry", response_class=HTMLResponse)
async def dashboard_retry(
    course_id: str,
    file_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Retry a failed document and return updated HTML fragment (htmx)."""
    from ..dependencies import get_redis_client

    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        return HTMLResponse(
            content='<span class="text-red-600">Document not found</span>',
            status_code=404,
        )

    if processed_file.status != "failed":
        return HTMLResponse(
            content=f'<span class="text-red-600">Cannot retry: status is {processed_file.status}</span>',
            status_code=400,
        )

    job = await job_service.get_job(processed_file.job_id)
    if job is None:
        return HTMLResponse(
            content='<span class="text-red-600">Job record not found</span>',
            status_code=404,
        )

    s3_key = job.get("s3_key", "")
    if not s3_key:
        return HTMLResponse(
            content='<span class="text-red-600">Original file not found</span>',
            status_code=400,
        )

    try:
        redis_gen = get_redis_client()
        redis_client = await anext(redis_gen)
        from ..services.queue_service import QueueService

        queue_service = QueueService(redis_client=redis_client)

        await job_service.update_job_status(processed_file.job_id, "pii_scanning")
        await queue_service.queue_pii_job(processed_file.job_id, s3_key)

        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        processed_file.status = "processing"
        processed_file.processed_at = now
        await config_service.set_processed_file(course_id, file_id, processed_file)

        # Return appropriate response based on htmx target context:
        # - detail view targets #document-detail → return detail fragment with polling
        # - index view targets #doc-{file_id} → return updated row fragment
        hx_target = request.headers.get("HX-Target", "")
        if hx_target == "document-detail":
            lti_session = request.query_params.get("lti_session", "")
            document = {
                "file_id": file_id,
                "filename": processed_file.original_filename,
                "status": "processing",
                "confidence": None,
                "canvas_page_url": None,
                "download_url": None,
                "processed_at": now,
                "published_at": None,
                "error_message": None,
            }
            return HTMLResponse(
                content=_render_detail_fragment(course_id, document, lti_session),
            )

        return HTMLResponse(
            content=_render_document_row_fragment(
                course_id,
                file_id,
                processed_file.original_filename,
                "processing",
                None,
                now,
                request.query_params.get("lti_session", ""),
            ),
        )

    except Exception as e:
        logger.error(f"Dashboard retry failed for {file_id}: {e}", exc_info=True)
        return HTMLResponse(
            content=f'<span class="text-red-600">Retry failed: {e}</span>',
            status_code=500,
        )


@router.post("/{course_id}/documents/{file_id}/reprocess", response_class=HTMLResponse)
async def dashboard_reprocess(
    course_id: str,
    file_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
    job_service: JobService = Depends(get_job_service),
) -> HTMLResponse:
    """Re-process a published document and return updated HTML fragment (htmx)."""
    from ..dependencies import get_redis_client

    processed_file = await config_service.get_processed_file(course_id, file_id)
    if processed_file is None:
        return HTMLResponse(
            content='<span class="text-red-600">Document not found</span>',
            status_code=404,
        )

    publish_result = await config_service.get_publish_result(course_id, file_id)
    if not publish_result:
        return HTMLResponse(
            content='<span class="text-red-600">Cannot re-process: document is not published</span>',
            status_code=400,
        )

    job = await job_service.get_job(processed_file.job_id)
    if job is None:
        return HTMLResponse(
            content='<span class="text-red-600">Job record not found</span>',
            status_code=404,
        )

    s3_key = job.get("s3_key", "")
    if not s3_key:
        return HTMLResponse(
            content='<span class="text-red-600">Original file not found</span>',
            status_code=400,
        )

    try:
        redis_gen = get_redis_client()
        redis_client = await anext(redis_gen)
        from ..services.queue_service import QueueService

        queue_service = QueueService(redis_client=redis_client)

        await job_service.update_job_status(processed_file.job_id, "pii_scanning")
        await queue_service.queue_pii_job(processed_file.job_id, s3_key)

        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        processed_file.status = "processing"
        processed_file.processed_at = now
        await config_service.set_processed_file(course_id, file_id, processed_file)

        return HTMLResponse(
            content='<span class="text-sm text-blue-600">'
            "Re-processing started. The page will update automatically.</span>",
        )

    except Exception as e:
        logger.error(f"Dashboard reprocess failed for {file_id}: {e}", exc_info=True)
        return HTMLResponse(
            content=f'<span class="text-red-600">Re-process failed: {e}</span>',
            status_code=500,
        )


@router.put("/{course_id}/config", response_class=HTMLResponse)
async def dashboard_update_config(
    course_id: str,
    request: Request,
    config_service: CourseConfigService = Depends(get_course_config_service),
) -> HTMLResponse:
    """Update course config and return success fragment (htmx)."""
    from datetime import UTC, datetime

    try:
        form_data = await request.form()
        enabled = form_data.get("enabled") == "on"
        threshold_str = form_data.get("auto_publish_threshold", "1.0")
        try:
            threshold = float(str(threshold_str))
            threshold = max(0.0, min(1.0, threshold))
        except (ValueError, TypeError):
            threshold = 1.0

        now = datetime.now(UTC).isoformat()
        existing = await config_service.get_config(course_id)
        if existing is None:
            existing = CourseConfig(created_at=now)

        existing.enabled = enabled
        existing.auto_publish_threshold = threshold
        existing.updated_at = now
        if not existing.created_at:
            existing.created_at = now

        await config_service.set_config(course_id, existing)

        return HTMLResponse(
            content='<div class="text-green-700 bg-green-50 border border-green-200 '
            'rounded px-4 py-2 text-sm">Settings saved successfully.</div>',
        )

    except Exception as e:
        logger.error(f"Dashboard config update failed for {course_id}: {e}", exc_info=True)
        return HTMLResponse(
            content=f'<div class="text-red-700 bg-red-50 border border-red-200 '
            f'rounded px-4 py-2 text-sm">Failed to save: {e}</div>',
            status_code=500,
        )


def _render_document_row_fragment(
    course_id: str,
    file_id: str,
    filename: str,
    doc_status: str,
    confidence: float | None,
    processed_at: str | None,
    lti_session: str,
    canvas_page_url: str | None = None,
) -> str:
    """Render an HTML table row fragment for htmx swap."""
    status_badge = _status_badge_html(doc_status)
    confidence_display = f"{confidence:.0%}" if confidence is not None else "-"
    processed_display = processed_at or "-"
    actions = _actions_html(course_id, file_id, doc_status, lti_session, canvas_page_url)
    detail_url = f"/lti/dashboard/{course_id}/documents/{file_id}?lti_session={lti_session}"

    # Add htmx polling for processing rows so they continue to auto-update
    polling_attrs = ""
    if doc_status == "processing":
        row_url = f"/lti/dashboard/{course_id}/documents/{file_id}/row?lti_session={lti_session}"
        polling_attrs = f' hx-get="{row_url}" hx-trigger="every 5s" hx-target="#doc-{file_id}" hx-swap="outerHTML"'

    return (
        f'<tr id="doc-{file_id}"{polling_attrs}>'
        f'<td class="px-4 py-3 text-sm">'
        f'<a href="{detail_url}" class="text-blue-600 hover:underline">{filename}</a></td>'
        f'<td class="px-4 py-3 text-sm">{status_badge}</td>'
        f'<td class="px-4 py-3 text-sm">{confidence_display}</td>'
        f'<td class="px-4 py-3 text-sm">{actions}</td>'
        f'<td class="px-4 py-3 text-sm text-gray-500">{processed_display}</td>'
        f"</tr>"
    )


def _status_badge_html(doc_status: str) -> str:
    """Generate status badge HTML."""
    colors = {
        "pending": "bg-gray-100 text-gray-700",
        "processing": "bg-blue-100 text-blue-700",
        "completed": "bg-yellow-100 text-yellow-700",
        "published": "bg-green-100 text-green-700",
        "failed": "bg-red-100 text-red-700",
    }
    css = colors.get(doc_status, "bg-gray-100 text-gray-700")
    label = doc_status.capitalize()
    extra = ""
    if doc_status == "processing":
        extra = ' <span class="inline-block animate-pulse">...</span>'
    return f'<span class="inline-block px-2 py-0.5 rounded text-xs font-medium {css}">{label}{extra}</span>'


def _actions_html(
    course_id: str,
    file_id: str,
    doc_status: str,
    lti_session: str,
    canvas_page_url: str | None = None,
) -> str:
    """Generate action buttons HTML for a document row."""
    parts = []
    if doc_status == "completed":
        parts.append(
            f'<button hx-post="/lti/dashboard/{course_id}/documents/{file_id}/publish?lti_session={lti_session}" '
            f'hx-target="#doc-{file_id}" hx-swap="outerHTML" '
            f'class="text-xs px-2 py-1 bg-green-600 text-white rounded hover:bg-green-700">Publish</button>'
        )
    elif doc_status == "failed":
        parts.append(
            f'<button hx-post="/lti/dashboard/{course_id}/documents/{file_id}/retry?lti_session={lti_session}" '
            f'hx-target="#doc-{file_id}" hx-swap="outerHTML" '
            f'class="text-xs px-2 py-1 bg-blue-600 text-white rounded hover:bg-blue-700">Retry</button>'
        )
    elif doc_status == "published":
        if canvas_page_url:
            parts.append(
                f'<a href="{canvas_page_url}" target="_blank" rel="noopener" '
                f'class="text-xs text-green-600 hover:underline">View Page</a>'
            )
        else:
            parts.append('<span class="text-xs text-green-600">Live</span>')

    return " ".join(parts) if parts else "-"


def _render_detail_fragment(
    course_id: str,
    document: dict,
    lti_session: str,
) -> str:
    """Render the document detail card as an HTML fragment for htmx swap."""
    doc_status = document["status"]
    status_badge = _status_badge_html(doc_status)

    # Build description list rows
    rows = []

    # Status row
    rows.append(
        f'<div class="py-3 grid grid-cols-3 gap-4">'
        f'<dt class="text-sm font-medium text-gray-500">Status</dt>'
        f'<dd class="text-sm text-gray-900 col-span-2">{status_badge}</dd>'
        f"</div>"
    )

    # Processed row
    processed_at = document.get("processed_at") or "Not yet processed"
    rows.append(
        f'<div class="py-3 grid grid-cols-3 gap-4">'
        f'<dt class="text-sm font-medium text-gray-500">Processed</dt>'
        f'<dd class="text-sm text-gray-900 col-span-2">{processed_at}</dd>'
        f"</div>"
    )

    # Confidence row
    confidence = document.get("confidence")
    if confidence is not None:
        rows.append(
            f'<div class="py-3 grid grid-cols-3 gap-4">'
            f'<dt class="text-sm font-medium text-gray-500">Confidence</dt>'
            f'<dd class="text-sm text-gray-900 col-span-2">{confidence:.0%}</dd>'
            f"</div>"
        )

    # Canvas page row
    canvas_page_url = document.get("canvas_page_url")
    if canvas_page_url:
        rows.append(
            f'<div class="py-3 grid grid-cols-3 gap-4">'
            f'<dt class="text-sm font-medium text-gray-500">Canvas Page</dt>'
            f'<dd class="text-sm col-span-2">'
            f'<a href="{canvas_page_url}" target="_blank" rel="noopener" '
            f'class="text-blue-600 hover:underline">View in Canvas</a>'
            f"</dd></div>"
        )

    # Download row
    download_url = document.get("download_url")
    if download_url:
        rows.append(
            f'<div class="py-3 grid grid-cols-3 gap-4">'
            f'<dt class="text-sm font-medium text-gray-500">Download</dt>'
            f'<dd class="text-sm col-span-2">'
            f'<a href="{download_url}" class="text-blue-600 hover:underline">Download Bundle</a>'
            f"</dd></div>"
        )

    # Published row
    published_at = document.get("published_at")
    if published_at:
        rows.append(
            f'<div class="py-3 grid grid-cols-3 gap-4">'
            f'<dt class="text-sm font-medium text-gray-500">Published</dt>'
            f'<dd class="text-sm text-gray-900 col-span-2">{published_at}</dd>'
            f"</div>"
        )

    # Error row
    error_message = document.get("error_message")
    if error_message:
        rows.append(
            f'<div class="py-3 grid grid-cols-3 gap-4">'
            f'<dt class="text-sm font-medium text-gray-500">Error</dt>'
            f'<dd class="text-sm text-red-600 col-span-2">{error_message}</dd>'
            f"</div>"
        )

    # Action buttons
    file_id = document["file_id"]
    action_buttons = []
    if doc_status == "completed":
        action_buttons.append(
            f'<button hx-post="/lti/dashboard/{course_id}/documents/{file_id}/publish?lti_session={lti_session}" '
            f'hx-target="#detail-actions" hx-swap="innerHTML" '
            f'class="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700">'
            f"Publish to Canvas</button>"
        )
    if doc_status == "failed":
        action_buttons.append(
            f'<button hx-post="/lti/dashboard/{course_id}/documents/{file_id}/retry?lti_session={lti_session}" '
            f'hx-target="#document-detail" hx-swap="outerHTML" '
            f'class="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">'
            f"Retry Processing</button>"
        )
    if doc_status == "published":
        action_buttons.append(
            f'<button hx-post="/lti/dashboard/{course_id}/documents/{file_id}/reprocess?lti_session={lti_session}" '
            f'hx-target="#detail-actions" hx-swap="innerHTML" '
            f'class="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">'
            f"Re-process</button>"
        )

    actions_html = "\n        ".join(action_buttons)

    # Polling attrs for processing status
    polling_attrs = ""
    if doc_status == "processing":
        fragment_url = f"/lti/dashboard/{course_id}/documents/{file_id}/detail_fragment?lti_session={lti_session}"
        polling_attrs = (
            f' hx-get="{fragment_url}" hx-trigger="every 5s" hx-target="#document-detail" hx-swap="outerHTML"'
        )

    dl_content = "\n        ".join(rows)

    return (
        f'<div class="bg-white rounded-lg shadow p-6 max-w-2xl" id="document-detail"{polling_attrs}>'
        f'<dl class="divide-y divide-gray-100">'
        f"{dl_content}"
        f"</dl>"
        f'<div class="mt-6 flex space-x-3" id="detail-actions">'
        f"{actions_html}"
        f"</div>"
        f"</div>"
    )
