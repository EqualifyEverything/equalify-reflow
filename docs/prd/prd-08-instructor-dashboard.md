# PRD-08: Instructor Dashboard

## Problem Statement

Instructors need a way to manage auto-publishing settings, view processing status, and take actions (publish, retry) for their course. The REST API from PRD-07 provides the backend, but there is no UI. The dashboard must embed within Canvas as an LTI course_navigation placement.

## Goal

A server-rendered instructor dashboard (Jinja2 + Tailwind CSS) embedded in Canvas via LTI course_navigation, providing document status, settings management, and publish/retry actions.

## Dependencies

- PRD-07: Course Configuration API (REST endpoints the dashboard calls)
- PRD-05: Course Config Storage (for LTI session to determine course context)

## Requirements

### R1: LTI course_navigation placement

Add a `course_navigation` placement to the LTI Developer Key configuration. When instructors click the sidebar item in Canvas, it opens the dashboard in an iframe.

Update `src/lti/router.py` to add a dashboard launch handler:

```python
@router.get("/lti/dashboard")
async def dashboard_launch(request: Request) -> HTMLResponse:
    """Handle LTI course_navigation launch.

    Validates the LTI session, extracts course context,
    and renders the dashboard.
    """
    ...
```

### R2: Template structure

Create Jinja2 templates in `src/canvas/templates/`:

```
src/canvas/templates/
  base.html              # Base layout (Tailwind CSS, header, nav)
  dashboard/
    index.html           # Document list view (main dashboard)
    settings.html        # Course settings view
    document_detail.html # Single document detail view
```

### R3: Document list view (index.html)

The main dashboard view shows all PDFs in the course with their processing status.

Content:
- Course name header
- Settings link
- Table with columns: Filename, Status, Confidence, Canvas Page, Actions, Processed At
- Status indicators:
  - **Pending** (grey) -- queued, not yet processed
  - **Processing** (blue, animated) -- pipeline in progress
  - **Draft** (yellow) -- page created but not published
  - **Published** (green) -- page live in Canvas
  - **Failed** (red) -- processing error
- Actions column:
  - "Publish" button for draft pages
  - "Retry" button for failed documents
  - Link to Canvas Page for published documents
- Empty state: "No PDFs tracked yet. Upload PDFs to Canvas and enable auto-processing in Settings."

### R4: Settings view (settings.html)

Course configuration form:

- Toggle: Enable/disable auto-processing for this course
- Slider or input: Auto-publish confidence threshold (0.0 to 1.0)
  - Label: "Auto-publish pages with confidence above:" with current value displayed
  - Default explanation: "Set to 1.0 to always create as draft (manual publish required)"
- Save button
- Form submits via standard HTML form POST (or htmx for no-reload update)

### R5: Document detail view (document_detail.html)

Detailed status for a single document:

- Original filename
- Processing status with timestamp
- Confidence score (if completed)
- Canvas Page link (if published or draft)
- Download bundle link (if available)
- Error message (if failed)
- Action buttons: Publish (if draft), Retry (if failed), Re-process (if published, to update)

### R6: Static assets

Set up Tailwind CSS compilation:

- Source: `src/canvas/static/src/dashboard.css` (with `@tailwind` directives)
- Output: `src/canvas/static/dist/dashboard.css` (compiled, minified)
- Compile with Tailwind CLI: `npx tailwindcss -i src/canvas/static/src/dashboard.css -o src/canvas/static/dist/dashboard.css --minify`
- Mount static directory in FastAPI:
  ```python
  app.mount("/static/canvas", StaticFiles(directory="src/canvas/static/dist"), name="canvas-static")
  ```

### R7: LTI session handling

The dashboard needs to know which course and user are in context:

1. LTI course_navigation launch provides course ID and user info via JWT claims
2. Store the LTI session in Redis (reuse existing `lti_state_ttl_seconds` pattern)
3. Dashboard routes check for a valid LTI session before rendering
4. If no valid session, show an error: "Please launch this tool from Canvas."

### R8: htmx for interactivity

Use htmx for actions that should update without a full page reload:

- Publish button: `hx-post="/lti/dashboard/{course_id}/documents/{file_id}/publish"` → replaces the row with updated status
- Retry button: `hx-post="/lti/dashboard/{course_id}/documents/{file_id}/retry"` → replaces the row
- Settings save: `hx-put="/lti/dashboard/{course_id}/config"` → shows success message

htmx is loaded from a CDN in `base.html`. No npm build step required for htmx.

### R9: Dashboard routes

Add server-rendered routes to `src/lti/router.py` (or a new `src/canvas/dashboard.py`):

```python
@router.get("/lti/dashboard/{course_id}")
async def dashboard_index(course_id: str, request: Request) -> HTMLResponse:
    """Render the document list view."""
    ...

@router.get("/lti/dashboard/{course_id}/settings")
async def dashboard_settings(course_id: str, request: Request) -> HTMLResponse:
    """Render the settings view."""
    ...

@router.get("/lti/dashboard/{course_id}/documents/{file_id}")
async def dashboard_document_detail(
    course_id: str, file_id: str, request: Request,
) -> HTMLResponse:
    """Render the document detail view."""
    ...

@router.post("/lti/dashboard/{course_id}/documents/{file_id}/publish")
async def dashboard_publish(
    course_id: str, file_id: str, request: Request,
) -> HTMLResponse:
    """Publish a draft page and return updated HTML fragment (htmx)."""
    ...

@router.post("/lti/dashboard/{course_id}/documents/{file_id}/retry")
async def dashboard_retry(
    course_id: str, file_id: str, request: Request,
) -> HTMLResponse:
    """Retry a failed document and return updated HTML fragment (htmx)."""
    ...

@router.put("/lti/dashboard/{course_id}/config")
async def dashboard_update_config(
    course_id: str, request: Request,
) -> HTMLResponse:
    """Update course config and return success fragment (htmx)."""
    ...
```

### R10: Canvas iframe compatibility

The dashboard renders inside a Canvas iframe. Ensure:

- No `X-Frame-Options: DENY` on dashboard routes (allow Canvas to iframe)
- Set `Content-Security-Policy: frame-ancestors` to include the Canvas domain
- Templates use relative URLs (no absolute URLs that break in iframe context)
- No third-party cookies required (session stored server-side in Redis, referenced by URL parameter or cookie)

## Implementation Notes

### Files to create:
1. `src/canvas/templates/base.html` -- base Jinja2 layout with Tailwind CSS
2. `src/canvas/templates/dashboard/index.html` -- document list view
3. `src/canvas/templates/dashboard/settings.html` -- course settings form
4. `src/canvas/templates/dashboard/document_detail.html` -- single document detail
5. `src/canvas/static/src/dashboard.css` -- Tailwind source CSS
6. `src/canvas/dashboard.py` -- dashboard routes (server-rendered HTML)
7. `tailwind.config.js` -- Tailwind config pointing to template files

### Files to modify:
1. `src/main.py` -- mount static files directory, register dashboard routes
2. `src/lti/router.py` -- add dashboard LTI launch handler

### Design decisions:
- Jinja2 + Tailwind CSS (no JS framework) for simplicity inside Canvas iframe
- htmx for partial page updates (publish, retry, settings save) without a SPA build
- htmx loaded from CDN (no npm dependency for the dashboard)
- Server-rendered HTML keeps the dashboard simple and Canvas-compatible
- Dashboard routes under `/lti/dashboard/` to share LTI authentication context
- Tailwind compiled via CLI during build (not at runtime)
- Status polling for in-progress jobs can use htmx `hx-trigger="every 5s"` on processing rows

## Success Criteria

- [x] `src/canvas/templates/base.html` exists with Tailwind CSS link and htmx script
- [x] `src/canvas/templates/dashboard/index.html` renders a table of documents with status, confidence, actions
- [x] `src/canvas/templates/dashboard/settings.html` renders an enable/disable toggle and threshold input
- [x] `src/canvas/templates/dashboard/document_detail.html` renders detailed status with action buttons
- [x] `src/canvas/static/src/dashboard.css` contains Tailwind directives
- [x] `tailwind.config.js` exists and points to `src/canvas/templates/**/*.html`
- [x] `src/canvas/dashboard.py` (or `src/lti/router.py`) has routes for index, settings, document detail
- [x] Dashboard LTI launch at `/lti/dashboard` validates LTI session and renders the index view
- [x] Publish button on draft documents calls POST and updates the row via htmx
- [x] Retry button on failed documents calls POST and updates the row via htmx
- [x] Settings form saves via PUT and shows a success indicator via htmx
- [x] Dashboard renders correctly inside a Canvas iframe (no `X-Frame-Options: DENY`)
- [x] Dashboard shows "Please launch from Canvas" error when accessed without LTI session
- [x] `src/main.py` mounts the static files directory for compiled Tailwind CSS
- [x] Processing-status rows auto-refresh via htmx polling (`hx-trigger="every 5s"`)
- [x] Empty state message shows when no documents are tracked
