"""Shared fixtures for Canvas auto-publishing integration tests.

Provides canvas-specific service fixtures built on top of the shared
real_redis_client and real_s3_client fixtures from tests/integration/conftest.py.
Canvas API is always mocked — no real Canvas instance needed.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from src.canvas.bundle import DownloadBundleService
from src.canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from src.config import settings
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.services.storage_service import StorageService

# ============================================================================
# CANVAS SERVICE FIXTURES
# ============================================================================


@pytest_asyncio.fixture
async def course_config_service(real_redis_client):
    """CourseConfigService backed by real Redis."""
    return CourseConfigService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def canvas_job_service(real_redis_client):
    """JobService backed by real Redis (canvas-specific alias)."""
    return JobService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def canvas_queue_service(real_redis_client):
    """QueueService backed by real Redis (canvas-specific alias)."""
    return QueueService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def canvas_storage_service(real_s3_client):
    """StorageService backed by real S3 (canvas-specific alias)."""
    return StorageService(
        s3_client=real_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest_asyncio.fixture
async def bundle_service(canvas_storage_service):
    """DownloadBundleService backed by real S3."""
    return DownloadBundleService(storage_service=canvas_storage_service)


# ============================================================================
# MOCK CANVAS API CLIENT
# ============================================================================


@pytest.fixture
def mock_canvas_client():
    """Mocked CanvasAPIClient — no real Canvas instance available."""
    client = AsyncMock()
    client.base_url = "https://canvas.test.edu"
    client.api_token = "test-token-abc123"
    client.close = AsyncMock()
    client._is_docker_url = False
    client._docker_host_header = ""
    return client


# ============================================================================
# SAMPLE DATA FACTORIES
# ============================================================================


@pytest.fixture
def sample_course_config():
    """Factory for CourseConfig objects."""

    def _make(
        enabled: bool = True,
        threshold: float = 0.85,
        token: str = "test-canvas-token",
    ) -> CourseConfig:
        now = datetime.now(UTC).isoformat()
        return CourseConfig(
            enabled=enabled,
            auto_publish_threshold=threshold,
            canvas_api_token=token,
            created_at=now,
            updated_at=now,
        )

    return _make


@pytest.fixture
def sample_processed_file():
    """Factory for ProcessedFile objects."""

    def _make(
        canvas_file_id: str = "42",
        canvas_updated_at: str = "2025-01-15T10:00:00+00:00",
        job_id: str = "job-abc-123",
        status: str = "completed",
        original_filename: str = "lecture_notes.pdf",
    ) -> ProcessedFile:
        return ProcessedFile(
            canvas_file_id=canvas_file_id,
            canvas_updated_at=canvas_updated_at,
            job_id=job_id,
            status=status,
            processed_at=datetime.now(UTC).isoformat(),
            original_filename=original_filename,
        )

    return _make


@pytest.fixture
def seed_s3_result(real_s3_client):
    """Helper to upload result.md and optional figures to S3 results bucket."""

    def _seed(
        job_id: str,
        markdown: str = "# Test Document\n\nSample content.",
        figures: dict[str, bytes] | None = None,
    ) -> None:
        # Upload result.md
        real_s3_client.put_object(
            Bucket=settings.s3_results_bucket,
            Key=f"results/{job_id}/result.md",
            Body=markdown.encode("utf-8"),
            ContentType="text/markdown",
        )
        # Upload figures if provided
        if figures:
            for name, data in figures.items():
                real_s3_client.put_object(
                    Bucket=settings.s3_results_bucket,
                    Key=f"results/{job_id}/figures/{name}",
                    Body=data,
                    ContentType="image/png",
                )

    return _seed
