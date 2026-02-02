# PRD-04: Canvas Publisher Service

## Problem Statement

After the pipeline processes a PDF into semantic markdown, there is no component that orchestrates the full publishing workflow: uploading images to Canvas Files, rendering HTML, creating a Canvas Page, and linking it in the course module. These steps must happen in sequence with proper error handling and state tracking.

## Goal

A publisher service that takes a completed pipeline job and publishes it as a Canvas Page with images, module placement, and a download footer.

## Dependencies

- PRD-01: Canvas API Client (for all Canvas API calls)
- PRD-02: Markdown-to-HTML Renderer (for converting markdown to Canvas HTML)
- PRD-03: Download Bundle Generator (for creating the download zip)

## Requirements

### R1: Publisher service class

Create `src/canvas/publisher.py`:

```python
# src/canvas/publisher.py

class CanvasPublisherService:
    """Orchestrate publishing pipeline output to Canvas."""

    def __init__(
        self,
        canvas_client: "CanvasAPIClient",
        renderer: "CanvasHTMLRenderer",
        bundle_service: "DownloadBundleService",
        storage_service: "StorageService",
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
        publish_page: bool = False,
    ) -> "PublishResult":
        """Publish a processed document as a Canvas Page.

        Orchestrates the full workflow:
        1. Download result markdown and figures from S3
        2. Upload figures to Canvas Files API
        3. Create download bundle (zip) and get presigned URL
        4. Render markdown to HTML with Canvas image URLs and download link
        5. Create or update Canvas Page
        6. Add page to course module (if PDF is in a module)

        Args:
            job_id: The processing job ID
            course_id: Canvas course ID
            original_filename: Original PDF filename (e.g., "lecture_notes.pdf")
            canvas_file_id: Canvas file ID of the original PDF
            publish_page: If True, publish the page immediately. If False, create as draft.

        Returns:
            PublishResult with page URL, status, and metadata
        """
        ...
```

### R2: Publish result model

Create a Pydantic model for the publish result in the same file:

```python
from pydantic import BaseModel

class PublishResult(BaseModel):
    """Result of publishing a document to Canvas."""

    job_id: str
    course_id: str
    canvas_page_url: str         # The page URL slug (e.g., "lecture-notes-reflow")
    canvas_page_id: int          # Canvas page ID
    canvas_file_ids: list[int]   # IDs of uploaded image files in Canvas
    download_url: str            # Presigned S3 URL for the zip bundle
    page_title: str              # e.g., "lecture_notes - Reflow"
    published: bool              # Whether the page was published or created as draft
    figure_count: int            # Number of images uploaded
```

### R3: Image upload to Canvas Files

The publisher must upload each extracted figure to Canvas Files before creating the page:

1. Read figure files from S3 at `results/{job_id}/figures/*`
2. For each figure, call `CanvasAPIClient.upload_file()` with:
   - `parent_folder_path="equalify-reflow"` (dedicated folder in course files)
   - `filename="{job_id}-figure-{n}.png"`
   - `content_type="image/png"`
3. Collect the returned Canvas file URLs into a map: `{"images/figure_1.png": "https://canvas.../files/42/preview"}`
4. Pass this map to the renderer as `image_url_map`

If an individual image upload fails, log a warning and skip that image (don't fail the whole publish).

### R4: Page naming

The Canvas Page title follows the convention: `{original_filename_without_extension} - Reflow`

Examples:
- `lecture_notes.pdf` → `lecture_notes - Reflow`
- `Chapter 4 Reading.pdf` → `Chapter 4 Reading - Reflow`

### R5: Page creation and update

- Before creating a page, call `CanvasAPIClient.get_page()` with the expected page URL slug to check if it already exists
- If the page exists (re-processing), call `update_page()` instead of `create_page()`
- Set `editing_roles="teachers"` so students cannot edit
- Set `published` based on the `publish_page` parameter (default: draft)

### R6: Module placement

After creating the page, find and link it in the course module:

1. Call `CanvasAPIClient.list_modules()` with `include_items=True`
2. Search module items for the original PDF file by `canvas_file_id`
3. If found, call `CanvasAPIClient.create_module_item()` to add the page after the PDF:
   - `item_type="Page"`
   - `page_url="{page_slug}"`
   - `position={pdf_position + 1}`
4. If the PDF is not in any module, skip module placement (log at INFO)

### R7: State tracking

After successful publishing, store the publish result in Redis for status tracking:

- Key: `eq-pdf:published:{course_id}:{canvas_file_id}`
- Value: JSON-serialized `PublishResult`
- TTL: Same as job TTL (30 days)

### R8: Error handling

- If S3 download of `result.md` fails, raise `PublishError` with the job ID and reason
- If Canvas Page creation fails, raise `PublishError` (don't leave orphaned images)
- If module placement fails, log a warning but still return success (the page exists, just not in a module)
- All errors should include the `job_id` and `course_id` for debugging

## Implementation Notes

### Files to create:
1. `src/canvas/publisher.py` -- the publisher service with `CanvasPublisherService` and `PublishResult`

### Files to modify:
None.

### Design decisions:
- Publisher orchestrates the other services (client, renderer, bundle) rather than implementing Canvas calls directly
- Image upload failures are non-fatal (page is created without that image rather than failing entirely)
- Module placement failure is non-fatal (page exists but isn't linked in a module)
- Page URL slug is derived from the title by Canvas (we use whatever slug Canvas returns from `create_page`)
- Re-publishing updates the existing page rather than creating duplicates

## Success Criteria

- [x] `src/canvas/publisher.py` exists with `CanvasPublisherService` class
- [x] `CanvasPublisherService.__init__()` accepts `CanvasAPIClient`, `CanvasHTMLRenderer`, `DownloadBundleService`, and `StorageService`
- [x] `publish()` downloads result markdown from S3 at `results/{job_id}/result.md`
- [x] `publish()` downloads figure files from S3 at `results/{job_id}/figures/*`
- [x] `publish()` uploads each figure to Canvas Files API in the `equalify-reflow` folder
- [x] `publish()` creates a download bundle via `DownloadBundleService.create_bundle()`
- [x] `publish()` renders markdown to HTML via `CanvasHTMLRenderer.render()` with `image_url_map` and `download_url`
- [x] `publish()` creates a Canvas Page with title `{filename} - Reflow` and the rendered HTML as body
- [x] `publish()` updates an existing page instead of creating a duplicate when re-processing
- [x] `publish()` sets `published=False` by default (draft), configurable via parameter
- [x] `publish()` searches course modules for the original PDF and adds the page after it
- [x] `publish()` stores `PublishResult` in Redis at `eq-pdf:published:{course_id}:{canvas_file_id}`
- [x] `publish()` returns a `PublishResult` Pydantic model with page URL, page ID, file IDs, and download URL
- [x] Individual image upload failures are logged but do not fail the publish
- [x] Module placement failure is logged but does not fail the publish
- [x] `PublishError` is raised with `job_id` and `course_id` when critical steps fail (markdown download, page creation)
