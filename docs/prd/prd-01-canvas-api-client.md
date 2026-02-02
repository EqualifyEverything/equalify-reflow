# PRD-01: Canvas API Client

## Problem Statement

The system needs to create Canvas Pages, upload images to Canvas Files, and read Canvas Modules. There is no reusable Canvas REST API client. The existing code in `src/lti/service.py` has ad-hoc httpx calls for file downloads but no structured client for write operations (Pages, Files, Modules).

## Goal

A reusable async Canvas API client with authentication, rate limiting, and methods for Pages, Files, and Modules APIs.

## Dependencies

None. This is the foundation for all Canvas integration work.

## Requirements

### R1: Canvas API client class

Create `src/canvas/client.py` with an async client that wraps httpx.

```python
# src/canvas/client.py

class CanvasAPIClient:
    """Async client for Canvas LMS REST API with rate limiting."""

    def __init__(
        self,
        base_url: str,           # e.g., "https://canvas.uic.edu"
        api_token: str,          # Bearer token
        rate_limit_buffer: int = 50,  # pause when remaining < buffer
    ): ...

    # --- Pages API ---
    async def create_page(
        self, course_id: str, title: str, body: str,
        published: bool = False, editing_roles: str = "teachers",
    ) -> dict: ...

    async def update_page(
        self, course_id: str, page_url: str, body: str,
        title: str | None = None, published: bool | None = None,
    ) -> dict: ...

    async def get_page(self, course_id: str, page_url: str) -> dict | None: ...

    # --- Files API ---
    async def upload_file(
        self, course_id: str, file_content: bytes,
        filename: str, content_type: str,
        parent_folder_path: str = "equalify-reflow",
    ) -> dict: ...

    # --- Modules API ---
    async def list_modules(
        self, course_id: str, include_items: bool = True,
    ) -> list[dict]: ...

    async def create_module_item(
        self, course_id: str, module_id: str,
        title: str, item_type: str, page_url: str,
        position: int | None = None,
    ) -> dict: ...

    # --- Files listing ---
    async def list_course_files(
        self, course_id: str, content_types: list[str] | None = None,
        sort: str = "created_at", order: str = "desc", per_page: int = 50,
    ) -> list[dict]: ...

    # --- Internal ---
    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response: ...
    async def _handle_rate_limit(self, response: httpx.Response) -> None: ...
    async def close(self) -> None: ...
```

### R2: Rate limit handling

Canvas returns `X-Rate-Limit-Remaining` in response headers. The client must:
- Read this header after every response
- Log a warning when remaining < `rate_limit_buffer`
- Sleep and retry when remaining reaches 0 (Canvas returns 403 with rate limit info)
- Log rate limit state at DEBUG level

### R3: 3-step file upload

Canvas file upload is a 3-step process:
1. `POST /api/v1/courses/{id}/files` with filename, size, content_type, parent_folder_path -> returns `upload_url` and `upload_params`
2. `POST {upload_url}` with multipart form data (upload_params + file) -> returns 3xx redirect
3. Follow redirect or `POST /api/v1/files/{id}/confirm` -> returns file object with `url`

The `upload_file` method must handle all 3 steps and return the final file object.

### R4: Docker host rewriting

Reuse the pattern from `src/lti/service.py`. When `base_url` contains `host.docker.internal`, set `Host: localhost:3000` header on all requests. This allows the client to work in local Docker development where Canvas runs on the host.

### R5: Configuration

Add settings to `src/config.py`:

```python
# Canvas Auto-Publishing Configuration
canvas_autopublish_enabled: bool = Field(
    default=False, description="Enable automatic PDF-to-Canvas Page publishing"
)
canvas_polling_interval_seconds: int = Field(
    ge=30, le=600, default=120, description="How often to poll Canvas for new PDFs (seconds)"
)
canvas_rate_limit_buffer: int = Field(
    ge=10, le=200, default=50, description="Pause API calls when Canvas rate limit remaining is below this"
)
```

## Implementation Notes

### Files to create:
1. `src/canvas/__init__.py` -- package init, export `CanvasAPIClient`
2. `src/canvas/client.py` -- the client class

### Files to modify:
1. `src/config.py` -- add `canvas_autopublish_enabled`, `canvas_polling_interval_seconds`, `canvas_rate_limit_buffer` settings

### Design decisions:
- Use httpx (already a dependency) for async HTTP
- Client is instantiated per-use, not a singleton, to avoid stale connections
- Rate limit handling is conservative (pause early) to avoid Canvas banning the token
- Docker host rewriting follows the same pattern as `src/lti/service.py:_rewrite_canvas_url()`

## Success Criteria

- [x] `src/canvas/__init__.py` exists and exports `CanvasAPIClient`
- [x] `src/canvas/client.py` exists with `CanvasAPIClient` class
- [x] `CanvasAPIClient.create_page()` calls `POST /api/v1/courses/{course_id}/pages` with `wiki_page[title]`, `wiki_page[body]`, `wiki_page[published]`, `wiki_page[editing_roles]`
- [x] `CanvasAPIClient.update_page()` calls `PUT /api/v1/courses/{course_id}/pages/{page_url}` with provided fields
- [x] `CanvasAPIClient.get_page()` calls `GET /api/v1/courses/{course_id}/pages/{page_url}` and returns `None` on 404
- [x] `CanvasAPIClient.upload_file()` implements the 3-step Canvas file upload workflow and returns the final file object with `url` field
- [x] `CanvasAPIClient.list_modules()` calls `GET /api/v1/courses/{course_id}/modules` with `include[]=items` when `include_items=True`
- [x] `CanvasAPIClient.create_module_item()` calls `POST /api/v1/courses/{course_id}/modules/{module_id}/items`
- [x] `CanvasAPIClient.list_course_files()` calls `GET /api/v1/courses/{course_id}/files` with pagination params
- [x] `CanvasAPIClient._handle_rate_limit()` reads `X-Rate-Limit-Remaining` header and sleeps when below buffer
- [x] `CanvasAPIClient._request()` sets `Host: localhost:3000` header when base_url contains `host.docker.internal`
- [x] `src/config.py` has `canvas_autopublish_enabled`, `canvas_polling_interval_seconds`, and `canvas_rate_limit_buffer` fields
- [x] All methods use `Authorization: Bearer {api_token}` header
- [x] All methods include logging at INFO (operations) and DEBUG (request/response details) levels
