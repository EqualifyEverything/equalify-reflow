"""Redis-backed course configuration storage.

Stores per-course auto-publishing settings, processed-file tracking,
and published-page tracking. Used by file discovery worker, publisher,
and dashboard API.
"""

import logging

from pydantic import BaseModel, Field
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# TTL for publish results (30 days)
PUBLISH_RESULT_TTL = 30 * 24 * 3600

# Redis key patterns
CONFIG_KEY = "eq-pdf:course-config:{course_id}"
PROCESSED_KEY = "eq-pdf:processed:{course_id}"
PUBLISHED_KEY = "eq-pdf:published:{course_id}:{file_id}"
ENABLED_COURSES_KEY = "eq-pdf:courses:enabled"


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
    created_at: str = ""  # ISO 8601 timestamp
    updated_at: str = ""  # ISO 8601 timestamp


class ProcessedFile(BaseModel):
    """Tracking record for a processed Canvas file."""

    canvas_file_id: str
    canvas_updated_at: str  # Canvas file's updated_at timestamp (ISO 8601)
    job_id: str  # Equalify job ID
    status: str  # "processing", "completed", "failed"
    processed_at: str  # When we processed it (ISO 8601)
    original_filename: str


class CourseConfigService:
    """Redis-backed storage for course auto-publishing configuration.

    Redis key patterns:
        eq-pdf:course-config:{course_id}         -> hash (CourseConfig fields)
        eq-pdf:processed:{course_id}             -> hash (file_id -> JSON ProcessedFile)
        eq-pdf:published:{course_id}:{file_id}   -> hash (PublishResult fields)
        eq-pdf:courses:enabled                   -> set (enabled course IDs)
    """

    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    # --- Course Config ---

    async def get_config(self, course_id: str) -> CourseConfig | None:
        """Get course configuration. Returns None if not configured."""
        key = CONFIG_KEY.format(course_id=course_id)
        data = await self.redis.hgetall(key)
        if not data:
            return None

        # Convert Redis hash to dict with proper types
        config_dict: dict[str, str | bool | float] = {}
        for k, v in data.items():
            # Handle bytes from Redis
            field = k if isinstance(k, str) else k.decode("utf-8")
            value = v if isinstance(v, str) else v.decode("utf-8")

            if field == "enabled":
                config_dict[field] = value == "true"
            elif field == "auto_publish_threshold":
                config_dict[field] = float(value)
            else:
                config_dict[field] = value

        return CourseConfig(**config_dict)

    async def set_config(self, course_id: str, config: CourseConfig) -> None:
        """Create or update course configuration."""
        key = CONFIG_KEY.format(course_id=course_id)

        # Serialize to flat hash-compatible mapping
        mapping: dict[str, str] = {
            "enabled": str(config.enabled).lower(),
            "auto_publish_threshold": str(config.auto_publish_threshold),
            "canvas_api_token": config.canvas_api_token,
            "created_at": config.created_at,
            "updated_at": config.updated_at,
        }

        await self.redis.hset(key, mapping=mapping)

        # Maintain the enabled-courses set
        if config.enabled:
            await self.redis.sadd(ENABLED_COURSES_KEY, course_id)
        else:
            await self.redis.srem(ENABLED_COURSES_KEY, course_id)

    async def list_enabled_courses(self) -> list[str]:
        """Return course IDs of all enabled courses."""
        members = await self.redis.smembers(ENABLED_COURSES_KEY)
        return [m if isinstance(m, str) else m.decode("utf-8") for m in members]

    # --- Processed Files ---

    async def get_processed_file(
        self,
        course_id: str,
        file_id: str,
    ) -> ProcessedFile | None:
        """Get processing record for a specific file."""
        key = PROCESSED_KEY.format(course_id=course_id)
        raw = await self.redis.hget(key, file_id)
        if raw is None:
            return None

        value = raw if isinstance(raw, str) else raw.decode("utf-8")
        return ProcessedFile.model_validate_json(value)

    async def set_processed_file(
        self,
        course_id: str,
        file_id: str,
        record: ProcessedFile,
    ) -> None:
        """Create or update a processed file record."""
        key = PROCESSED_KEY.format(course_id=course_id)
        await self.redis.hset(key, file_id, record.model_dump_json())

    async def list_processed_files(
        self,
        course_id: str,
    ) -> dict[str, ProcessedFile]:
        """List all processed files for a course. Returns {file_id: ProcessedFile}."""
        key = PROCESSED_KEY.format(course_id=course_id)
        data = await self.redis.hgetall(key)
        result: dict[str, ProcessedFile] = {}
        for k, v in data.items():
            fid = k if isinstance(k, str) else k.decode("utf-8")
            value = v if isinstance(v, str) else v.decode("utf-8")
            result[fid] = ProcessedFile.model_validate_json(value)
        return result

    async def is_file_new_or_updated(
        self,
        course_id: str,
        file_id: str,
        canvas_updated_at: str,
    ) -> bool:
        """Check if a file needs processing.

        Returns True if:
        - File has never been processed, OR
        - File's canvas_updated_at is newer than the stored value
        """
        existing = await self.get_processed_file(course_id, file_id)
        if existing is None:
            return True
        # ISO 8601 string comparison works for chronological ordering
        return canvas_updated_at > existing.canvas_updated_at

    # --- Published Pages ---

    async def get_publish_result(
        self,
        course_id: str,
        file_id: str,
    ) -> dict | None:
        """Get publish result for a file. Returns None if not published."""
        key = PUBLISHED_KEY.format(course_id=course_id, file_id=file_id)
        data = await self.redis.hgetall(key)
        if not data:
            return None
        return {
            (k if isinstance(k, str) else k.decode("utf-8")): (
                v if isinstance(v, str) else v.decode("utf-8")
            )
            for k, v in data.items()
        }

    async def set_publish_result(
        self,
        course_id: str,
        file_id: str,
        result: dict,
    ) -> None:
        """Store publish result for a file with 30-day TTL."""
        key = PUBLISHED_KEY.format(course_id=course_id, file_id=file_id)
        # Ensure all values are strings for Redis hash
        mapping = {str(k): str(v) for k, v in result.items()}
        await self.redis.hset(key, mapping=mapping)
        await self.redis.expire(key, PUBLISH_RESULT_TTL)
