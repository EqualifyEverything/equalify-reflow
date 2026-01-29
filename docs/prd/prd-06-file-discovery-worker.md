# PRD-06: File Discovery Worker

## Problem Statement

For automatic publishing, the system needs to detect when instructors upload new PDFs to Canvas. There is no component that monitors Canvas courses for new files and queues them for processing.

## Goal

A background worker that polls Canvas courses for new PDF uploads, checks them against the processed-file set, and queues new or updated files for the existing processing pipeline.

## Dependencies

- PRD-01: Canvas API Client (for `list_course_files()`)
- PRD-05: Course Config Storage (for `list_enabled_courses()`, `is_file_new_or_updated()`, `set_processed_file()`)

## Requirements

### R1: Worker class

Create `src/workers/canvas_file_worker.py`:

```python
# src/workers/canvas_file_worker.py

class CanvasFileWorker:
    """Background worker that polls Canvas for new PDF uploads.

    Runs on a configurable schedule. For each enabled course:
    1. Query Canvas Files API for PDF files
    2. Compare against processed-file set in Redis
    3. Queue new or updated files for processing
    """

    def __init__(
        self,
        config_service: "CourseConfigService",
        job_service: "JobService",
        storage_service: "StorageService",
    ):
        self.config_service = config_service
        self.job_service = job_service
        self.storage_service = storage_service

    async def run(self, shutdown_event: asyncio.Event) -> None:
        """Main worker loop. Runs until shutdown_event is set.

        Polls at the interval configured by canvas_polling_interval_seconds.
        """
        ...

    async def poll_courses(self) -> int:
        """Poll all enabled courses for new PDFs.

        Returns:
            Number of new files queued for processing.
        """
        ...

    async def poll_course(self, course_id: str) -> int:
        """Poll a single course for new PDF uploads.

        Args:
            course_id: Canvas course ID

        Returns:
            Number of new files queued from this course.
        """
        ...

    async def _queue_file_for_processing(
        self,
        course_id: str,
        canvas_file: dict,
    ) -> str:
        """Download a Canvas file and queue it for pipeline processing.

        Args:
            course_id: Canvas course ID
            canvas_file: Canvas file object from the API

        Returns:
            The created job ID.
        """
        ...
```

### R2: Polling loop

The `run()` method implements a polling loop:

1. Sleep for `canvas_polling_interval_seconds` (from settings, default 120s)
2. Check `shutdown_event` -- if set, exit gracefully
3. Call `poll_courses()`
4. Log the results (courses polled, files queued)
5. Repeat

On error, log the exception, sleep for `worker_error_sleep_seconds`, and continue. Never crash the loop.

### R3: Course polling logic

`poll_course()` for a single course:

1. Get the course's `CourseConfig` to obtain the Canvas API token
2. Create a `CanvasAPIClient` with the course's token and the Canvas base URL
3. Call `list_course_files(course_id, content_types=["application/pdf"], sort="created_at", order="desc")`
4. For each PDF file:
   - Call `config_service.is_file_new_or_updated(course_id, file_id, file["updated_at"])`
   - If new or updated: call `_queue_file_for_processing()`
   - Update the processed-file record with status "processing"
5. Close the Canvas client when done

### R4: File queuing

`_queue_file_for_processing()`:

1. Download the file content from Canvas using `CanvasAPIClient` (the file object includes a `url` field)
2. Upload to S3 temp bucket via `StorageService`
3. Create a job via `JobService` with metadata:
   - `source: "canvas_auto"`
   - `course_id`
   - `canvas_file_id`
   - `original_filename`
4. Store the `ProcessedFile` record with status `"processing"` and the job ID
5. Queue the job for the existing processing pipeline
6. Return the job ID

### R5: Configuration

Add to `src/config.py`:

```python
# Canvas Auto-Publishing Configuration
canvas_autopublish_enabled: bool = Field(
    default=False, description="Enable Canvas file discovery worker"
)
canvas_polling_interval_seconds: int = Field(
    ge=30, le=600, default=120, description="How often to poll Canvas for new PDFs (seconds)"
)
canvas_rate_limit_buffer: int = Field(
    ge=10, le=200, default=50, description="Pause API calls when Canvas rate limit remaining is below this"
)
```

### R6: Worker startup

Register the worker in `src/main.py` lifespan, gated behind `canvas_autopublish_enabled`:

```python
if settings.canvas_autopublish_enabled:
    worker_tasks.append(
        asyncio.create_task(start_canvas_file_worker(shutdown_event))
    )
    logger.info("Canvas file discovery worker started")
```

Create the startup function:

```python
# src/workers/canvas_file_worker.py

async def start_canvas_file_worker(shutdown_event: asyncio.Event) -> None:
    """Initialize and start the Canvas file discovery worker."""
    ...
```

### R7: Logging

- INFO: Worker start/stop, courses polled, files queued per poll cycle
- WARNING: Rate limit approaching, individual course poll failure
- DEBUG: Each file checked, skip reasons (already processed, not PDF)
- ERROR: Worker loop exceptions (with traceback)

## Implementation Notes

### Files to create:
1. `src/workers/canvas_file_worker.py` -- the worker class and `start_canvas_file_worker()` function

### Files to modify:
1. `src/config.py` -- add `canvas_autopublish_enabled`, `canvas_polling_interval_seconds`, `canvas_rate_limit_buffer` settings
2. `src/main.py` -- register the worker in the lifespan function (gated behind `canvas_autopublish_enabled`)

### Design decisions:
- Polling rather than webhooks for v1 (Canvas Live Events requires admin setup)
- One Canvas API client per course per poll (each course may have a different token)
- Files are downloaded and re-uploaded to S3 to reuse the existing pipeline (no special code path)
- Worker follows the same pattern as `PIIWorker` and `TimeoutWorker` (shutdown event, error sleep)
- Rate limit buffer prevents Canvas from throttling the token mid-poll

## Success Criteria

- [ ] `src/workers/canvas_file_worker.py` exists with `CanvasFileWorker` class
- [ ] `CanvasFileWorker.run()` polls on a configurable interval using `canvas_polling_interval_seconds`
- [ ] `CanvasFileWorker.run()` exits gracefully when `shutdown_event` is set
- [ ] `CanvasFileWorker.run()` does not crash on exceptions (logs and continues)
- [ ] `poll_courses()` calls `config_service.list_enabled_courses()` and polls each course
- [ ] `poll_course()` creates a `CanvasAPIClient` using the course's stored Canvas API token
- [ ] `poll_course()` calls `list_course_files()` filtering for `application/pdf` content type
- [ ] `poll_course()` calls `is_file_new_or_updated()` for each PDF file
- [ ] `poll_course()` skips files that have already been processed (same `updated_at`)
- [ ] `_queue_file_for_processing()` downloads the file from Canvas and uploads to S3 temp bucket
- [ ] `_queue_file_for_processing()` creates a job with `source: "canvas_auto"`, `course_id`, `canvas_file_id`, `original_filename` metadata
- [ ] `_queue_file_for_processing()` stores a `ProcessedFile` record with status `"processing"`
- [ ] `src/config.py` has `canvas_autopublish_enabled`, `canvas_polling_interval_seconds`, `canvas_rate_limit_buffer` fields
- [ ] `src/main.py` starts the worker when `canvas_autopublish_enabled=True`
- [ ] `src/main.py` does not start the worker when `canvas_autopublish_enabled=False`
- [ ] `start_canvas_file_worker()` function exists and initializes dependencies before starting the worker
