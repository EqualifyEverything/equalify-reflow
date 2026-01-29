# PRD-05: Course Configuration Storage

## Problem Statement

The file discovery worker and instructor dashboard need per-course settings (enabled/disabled, auto-publish threshold) and tracking data (which files have been processed, what was published). There is no storage layer for this course-level state.

## Goal

Redis-backed storage for course configuration, processed-file tracking, and published-page tracking. Used by the file discovery worker, publisher, and dashboard API.

## Dependencies

None. Uses existing Redis infrastructure.

## Requirements

### R1: Course config service class

Create `src/canvas/course_config.py`:

```python
# src/canvas/course_config.py

from pydantic import BaseModel, Field

class CourseConfig(BaseModel):
    """Per-course auto-publishing configuration."""

    enabled: bool = False
    auto_publish_threshold: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score above which pages are auto-published. "
        "1.0 means always create as draft (never auto-publish).",
    )
    canvas_api_token: str = ""
    created_at: str = ""       # ISO 8601 timestamp
    updated_at: str = ""       # ISO 8601 timestamp


class ProcessedFile(BaseModel):
    """Tracking record for a processed Canvas file."""

    canvas_file_id: str
    canvas_updated_at: str     # Canvas file's updated_at timestamp (ISO 8601)
    job_id: str                # Equalify job ID
    status: str                # "processing", "completed", "failed"
    processed_at: str          # When we processed it (ISO 8601)
    original_filename: str


class CourseConfigService:
    """Redis-backed storage for course auto-publishing configuration."""

    # Redis key patterns:
    #   eq-pdf:course-config:{course_id}         -> hash (CourseConfig fields)
    #   eq-pdf:processed:{course_id}             -> hash (file_id -> JSON ProcessedFile)
    #   eq-pdf:published:{course_id}:{file_id}   -> hash (PublishResult fields)

    def __init__(self, redis_client: "Redis"):
        self.redis = redis_client

    # --- Course Config ---

    async def get_config(self, course_id: str) -> CourseConfig | None:
        """Get course configuration. Returns None if not configured."""
        ...

    async def set_config(self, course_id: str, config: CourseConfig) -> None:
        """Create or update course configuration."""
        ...

    async def list_enabled_courses(self) -> list[str]:
        """Return course IDs of all enabled courses."""
        ...

    # --- Processed Files ---

    async def get_processed_file(
        self, course_id: str, file_id: str,
    ) -> ProcessedFile | None:
        """Get processing record for a specific file."""
        ...

    async def set_processed_file(
        self, course_id: str, file_id: str, record: ProcessedFile,
    ) -> None:
        """Create or update a processed file record."""
        ...

    async def list_processed_files(
        self, course_id: str,
    ) -> dict[str, ProcessedFile]:
        """List all processed files for a course. Returns {file_id: ProcessedFile}."""
        ...

    async def is_file_new_or_updated(
        self, course_id: str, file_id: str, canvas_updated_at: str,
    ) -> bool:
        """Check if a file needs processing.

        Returns True if:
        - File has never been processed, OR
        - File's canvas_updated_at is newer than the stored value
        """
        ...

    # --- Published Pages ---

    async def get_publish_result(
        self, course_id: str, file_id: str,
    ) -> dict | None:
        """Get publish result for a file. Returns None if not published."""
        ...

    async def set_publish_result(
        self, course_id: str, file_id: str, result: dict,
    ) -> None:
        """Store publish result for a file."""
        ...
```

### R2: Redis key structure

| Key Pattern | Type | Purpose | TTL |
|---|---|---|---|
| `eq-pdf:course-config:{course_id}` | Hash | Course settings (enabled, threshold, token) | None (persistent) |
| `eq-pdf:processed:{course_id}` | Hash | File ID → JSON ProcessedFile | None (persistent) |
| `eq-pdf:published:{course_id}:{file_id}` | Hash | Publish result fields | 30 days |
| `eq-pdf:courses:enabled` | Set | Set of enabled course IDs for fast lookup | None (persistent) |

### R3: Enabled courses tracking

When a course config is saved:
- If `enabled=True`, add the course ID to `eq-pdf:courses:enabled` (Redis set)
- If `enabled=False`, remove it from the set
- `list_enabled_courses()` reads from this set (O(n) with n = enabled courses, not all courses)

### R4: File freshness check

`is_file_new_or_updated()` implements the re-processing trigger:

1. Look up `file_id` in `eq-pdf:processed:{course_id}`
2. If not found, return `True` (new file)
3. If found, compare stored `canvas_updated_at` with the provided value
4. Return `True` if the provided value is newer (string comparison of ISO 8601 timestamps works correctly for chronological ordering)

### R5: Data serialization

- `CourseConfig` fields are stored as a Redis hash (flat key-value)
- `ProcessedFile` records are stored as JSON strings in a Redis hash (file_id → JSON)
- All timestamps use ISO 8601 format (`2024-01-15T10:30:00Z`)

## Implementation Notes

### Files to create:
1. `src/canvas/course_config.py` -- the config service with models and Redis operations

### Files to modify:
None.

### Design decisions:
- Redis hashes rather than JSON strings for course config (allows partial reads/updates)
- JSON strings for processed file records within a hash (each record has multiple fields)
- Separate enabled-courses set for O(1) lookup during polling (avoid scanning all course keys)
- No TTL on course config or processed-file tracking (these are persistent settings)
- 30-day TTL on publish results (matches job retention)
- ISO 8601 string comparison for timestamp freshness (avoids datetime parsing in the hot path)

## Success Criteria

- [ ] `src/canvas/course_config.py` exists with `CourseConfigService`, `CourseConfig`, and `ProcessedFile` classes
- [ ] `get_config()` returns `CourseConfig` from Redis hash `eq-pdf:course-config:{course_id}`
- [ ] `get_config()` returns `None` when the course has no configuration
- [ ] `set_config()` writes `CourseConfig` fields to Redis hash `eq-pdf:course-config:{course_id}`
- [ ] `set_config()` adds course ID to `eq-pdf:courses:enabled` when `enabled=True`
- [ ] `set_config()` removes course ID from `eq-pdf:courses:enabled` when `enabled=False`
- [ ] `list_enabled_courses()` returns course IDs from the `eq-pdf:courses:enabled` set
- [ ] `get_processed_file()` returns `ProcessedFile` for a specific file in a course
- [ ] `set_processed_file()` stores `ProcessedFile` as JSON in `eq-pdf:processed:{course_id}` hash
- [ ] `list_processed_files()` returns all processed files for a course as `{file_id: ProcessedFile}`
- [ ] `is_file_new_or_updated()` returns `True` for files not in the processed set
- [ ] `is_file_new_or_updated()` returns `True` when `canvas_updated_at` is newer than stored value
- [ ] `is_file_new_or_updated()` returns `False` when `canvas_updated_at` matches stored value
- [ ] `set_publish_result()` stores data in `eq-pdf:published:{course_id}:{file_id}` with 30-day TTL
- [ ] `get_publish_result()` retrieves publish result for a file
- [ ] All timestamps use ISO 8601 format
