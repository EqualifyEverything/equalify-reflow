# PRD-07: Course Configuration API

## Problem Statement

The instructor dashboard needs REST endpoints to read and update course processing settings, view document status, and trigger actions (process, retry, publish). There are no API endpoints for course-level Canvas configuration.

## Goal

REST API endpoints for managing per-course auto-publishing settings, viewing document processing status, and triggering manual actions.

## Dependencies

- PRD-04: Canvas Publisher Service (for manual publish action)
- PRD-05: Course Config Storage (for reading/writing course settings and processed file records)

## Requirements

### R1: Course config router

Create `src/api/canvas_config.py`:

```python
# src/api/canvas_config.py

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/canvas/courses", tags=["canvas-config"])
```

### R2: Settings endpoints

```python
@router.get("/{course_id}/config")
async def get_course_config(course_id: str) -> CourseConfigResponse:
    """Get auto-publishing configuration for a course.

    Returns current settings or defaults if not configured.
    """
    ...

@router.put("/{course_id}/config")
async def update_course_config(
    course_id: str,
    body: CourseConfigUpdate,
) -> CourseConfigResponse:
    """Update auto-publishing configuration for a course.

    Enables or disables auto-processing, sets auto-publish threshold.
    """
    ...
```

### R3: Document status endpoints

```python
@router.get("/{course_id}/documents")
async def list_course_documents(course_id: str) -> list[DocumentStatusResponse]:
    """List all tracked PDFs in a course with their processing status.

    Returns processed files merged with their publish status.
    Each document shows: filename, processing status, confidence score,
    page URL (if published), last processed timestamp.
    """
    ...

@router.get("/{course_id}/documents/{file_id}")
async def get_document_status(
    course_id: str, file_id: str,
) -> DocumentStatusResponse:
    """Get detailed status for a specific document."""
    ...
```

### R4: Action endpoints

```python
@router.post("/{course_id}/documents/{file_id}/process")
async def trigger_processing(
    course_id: str, file_id: str,
) -> dict:
    """Manually trigger processing for a Canvas file.

    Used for backfilling existing PDFs or re-processing.
    Downloads the file from Canvas and queues for the pipeline.
    """
    ...

@router.post("/{course_id}/documents/{file_id}/retry")
async def retry_processing(
    course_id: str, file_id: str,
) -> dict:
    """Retry processing for a failed document.

    Only works if the document's status is 'failed'.
    """
    ...

@router.post("/{course_id}/documents/{file_id}/publish")
async def publish_document(
    course_id: str, file_id: str,
) -> dict:
    """Publish a draft Canvas Page for a completed document.

    Only works if the document has been processed successfully
    and has a draft page.
    """
    ...
```

### R5: Request/response models

Create Pydantic models in the same file:

```python
from pydantic import BaseModel, Field

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
    status: str                    # "processing", "completed", "failed", "published"
    job_id: str | None = None
    confidence_score: float | None = None
    canvas_page_url: str | None = None
    canvas_page_id: int | None = None
    download_url: str | None = None
    processed_at: str | None = None
    published_at: str | None = None
    error_message: str | None = None
```

### R6: Router registration

Register the router in `src/main.py`, gated behind the Canvas auto-publish feature flag:

```python
if settings.canvas_autopublish_enabled:
    from .api.canvas_config import router as canvas_config_router
    app.include_router(canvas_config_router)
    logger.info("✅ Canvas config endpoints enabled at /api/v1/canvas/courses/*")
```

### R7: Authentication

These endpoints should be accessible from the LTI dashboard (iframe). Authentication will use the LTI session context. For now, the endpoints are gated behind the existing API key middleware. LTI session authentication will be added in the dashboard PRD.

## Implementation Notes

### Files to create:
1. `src/api/canvas_config.py` -- the router with endpoints and Pydantic models

### Files to modify:
1. `src/main.py` -- register the canvas config router (gated behind `canvas_autopublish_enabled`)

### Design decisions:
- Endpoints are under `/api/v1/canvas/courses/` to namespace them separately from the document processing API
- `PUT` for config update (idempotent, replaces the config)
- `POST` for actions (process, retry, publish) since they have side effects
- Document status merges data from processed-file records and publish results
- API key auth as a stopgap; LTI session auth added when the dashboard is built
- Router is only registered when `canvas_autopublish_enabled=True` to avoid exposing unused endpoints

## Success Criteria

- [ ] `src/api/canvas_config.py` exists with `router` as an `APIRouter`
- [ ] `GET /api/v1/canvas/courses/{course_id}/config` returns `CourseConfigResponse`
- [ ] `GET /api/v1/canvas/courses/{course_id}/config` returns defaults when course is not configured
- [ ] `PUT /api/v1/canvas/courses/{course_id}/config` accepts `CourseConfigUpdate` and returns `CourseConfigResponse`
- [ ] `PUT /api/v1/canvas/courses/{course_id}/config` with `enabled=True` adds course to enabled set
- [ ] `PUT /api/v1/canvas/courses/{course_id}/config` with `enabled=False` removes course from enabled set
- [ ] `GET /api/v1/canvas/courses/{course_id}/documents` returns `list[DocumentStatusResponse]`
- [ ] `GET /api/v1/canvas/courses/{course_id}/documents/{file_id}` returns `DocumentStatusResponse`
- [ ] `GET /api/v1/canvas/courses/{course_id}/documents/{file_id}` returns 404 for unknown files
- [ ] `POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/process` downloads file from Canvas and queues for processing
- [ ] `POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/retry` re-queues a failed document
- [ ] `POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/retry` returns 400 if document is not in failed state
- [ ] `POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/publish` publishes a draft Canvas Page
- [ ] `POST /api/v1/canvas/courses/{course_id}/documents/{file_id}/publish` returns 400 if document is not completed
- [ ] `src/main.py` registers the router when `canvas_autopublish_enabled=True`
- [ ] All endpoints use Pydantic models for request/response validation
