"""Integration tests for Canvas Config API endpoints with real Redis.

Canvas API is mocked. Tests verify HTTP endpoints with real Redis backend:
config CRUD, document status, retry logic.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from src.api.canvas_config import router as canvas_config_router
from src.canvas.course_config import CourseConfigService, ProcessedFile
from src.dependencies import (
    get_course_config_service,
    get_job_service,
    get_queue_service,
    get_redis_client,
)
from src.services.job_service import JobService
from src.services.queue_service import QueueService


@pytest_asyncio.fixture
async def canvas_api_client(real_redis_client):
    """AsyncClient with real-Redis-backed dependency injection for canvas config."""
    # Late import to avoid circular imports at module level
    from fastapi import FastAPI

    # Create a minimal app with just the canvas config router
    test_app = FastAPI()
    test_app.include_router(canvas_config_router)

    config_svc = CourseConfigService(real_redis_client)
    job_svc = JobService(redis_client=real_redis_client)
    queue_svc = QueueService(redis_client=real_redis_client)

    test_app.dependency_overrides[get_course_config_service] = lambda: config_svc
    test_app.dependency_overrides[get_job_service] = lambda: job_svc
    test_app.dependency_overrides[get_queue_service] = lambda: queue_svc
    test_app.dependency_overrides[get_redis_client] = lambda: real_redis_client

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        yield client

    test_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def canvas_api_services(real_redis_client):
    """Return real-Redis-backed services for direct seeding in tests."""
    return {
        "config_svc": CourseConfigService(real_redis_client),
        "job_svc": JobService(redis_client=real_redis_client),
        "queue_svc": QueueService(redis_client=real_redis_client),
    }


# ============================================================================
# Config CRUD
# ============================================================================


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_put_then_get_config_round_trip(canvas_api_client):
    """PUT config, GET returns same values."""
    put_resp = await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-1/config",
        json={"enabled": True, "auto_publish_threshold": 0.8},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["enabled"] is True
    assert put_data["auto_publish_threshold"] == 0.8

    get_resp = await canvas_api_client.get("/api/v1/canvas/courses/c-api-1/config")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["enabled"] is True
    assert get_data["auto_publish_threshold"] == 0.8
    assert get_data["course_id"] == "c-api-1"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_put_config_enables_course_in_redis(canvas_api_client, canvas_api_services):
    """After PUT enabled=True, course is in enabled set."""
    await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-2/config",
        json={"enabled": True},
    )

    enabled = await canvas_api_services["config_svc"].list_enabled_courses()
    assert "c-api-2" in enabled


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_put_config_disables_course_in_redis(canvas_api_client, canvas_api_services):
    """After PUT enabled=False, course is removed from enabled set."""
    # First enable
    await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-3/config",
        json={"enabled": True},
    )
    # Then disable
    await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-3/config",
        json={"enabled": False},
    )

    enabled = await canvas_api_services["config_svc"].list_enabled_courses()
    assert "c-api-3" not in enabled


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_partial_update_preserves_fields(canvas_api_client):
    """PUT only `enabled`, threshold unchanged from previous value."""
    await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-partial/config",
        json={"enabled": True, "auto_publish_threshold": 0.7},
    )

    # Update only enabled
    await canvas_api_client.put(
        "/api/v1/canvas/courses/c-api-partial/config",
        json={"enabled": False},
    )

    get_resp = await canvas_api_client.get("/api/v1/canvas/courses/c-api-partial/config")
    data = get_resp.json()
    assert data["enabled"] is False
    assert data["auto_publish_threshold"] == 0.7


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_config_defaults_for_unconfigured(canvas_api_client):
    """GET unconfigured course returns defaults."""
    resp = await canvas_api_client.get("/api/v1/canvas/courses/c-api-unconfigured/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["auto_publish_threshold"] == 1.0
    assert data["course_id"] == "c-api-unconfigured"


# ============================================================================
# Document Status
# ============================================================================


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_get_documents_empty_course(canvas_api_client):
    """No docs → empty list."""
    resp = await canvas_api_client.get("/api/v1/canvas/courses/c-api-empty/documents")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_get_documents_with_seeded_data(canvas_api_client, canvas_api_services):
    """Seed ProcessedFile + job in Redis, GET returns merged data."""
    config_svc = canvas_api_services["config_svc"]
    job_svc = canvas_api_services["job_svc"]

    # Seed a job
    job_id = "api-job-docs-1"
    await job_svc.create_job(
        job_id=job_id,
        s3_key="temp/test.pdf",
        status="completed",
        original_filename="reading.pdf",
    )
    # Add confidence score
    await job_svc.update_job_status(job_id, "completed", confidence_score="0.92")

    # Seed a processed file
    now = datetime.now(UTC).isoformat()
    pf = ProcessedFile(
        canvas_file_id="file-50",
        canvas_updated_at=now,
        job_id=job_id,
        status="completed",
        processed_at=now,
        original_filename="reading.pdf",
    )
    await config_svc.set_processed_file("c-api-docs", "file-50", pf)

    resp = await canvas_api_client.get("/api/v1/canvas/courses/c-api-docs/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1
    assert docs[0]["canvas_file_id"] == "file-50"
    assert docs[0]["status"] == "completed"
    assert docs[0]["job_id"] == job_id
    assert docs[0]["original_filename"] == "reading.pdf"
    assert docs[0]["confidence_score"] == 0.92


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_get_document_404_for_missing(canvas_api_client):
    """Unknown file_id → 404."""
    resp = await canvas_api_client.get(
        "/api/v1/canvas/courses/c-api-404/documents/nonexistent-file"
    )
    assert resp.status_code == 404


# ============================================================================
# Retry Logic
# ============================================================================


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_retry_updates_redis_state(canvas_api_client, canvas_api_services):
    """Seed failed job, retry → status changes in Redis to processing."""
    config_svc = canvas_api_services["config_svc"]
    job_svc = canvas_api_services["job_svc"]

    job_id = "api-job-retry-1"
    await job_svc.create_job(
        job_id=job_id,
        s3_key="temp/retry.pdf",
        status="failed",
        original_filename="retry.pdf",
    )

    now = datetime.now(UTC).isoformat()
    pf = ProcessedFile(
        canvas_file_id="file-retry",
        canvas_updated_at=now,
        job_id=job_id,
        status="failed",
        processed_at=now,
        original_filename="retry.pdf",
    )
    await config_svc.set_processed_file("c-api-retry", "file-retry", pf)

    resp = await canvas_api_client.post(
        "/api/v1/canvas/courses/c-api-retry/documents/file-retry/retry"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processing"
    assert data["job_id"] == job_id

    # Verify state changed in Redis
    updated_pf = await config_svc.get_processed_file("c-api-retry", "file-retry")
    assert updated_pf is not None
    assert updated_pf.status == "processing"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_retry_returns_400_for_non_failed(canvas_api_client, canvas_api_services):
    """Seed completed job, retry → 400."""
    config_svc = canvas_api_services["config_svc"]
    job_svc = canvas_api_services["job_svc"]

    job_id = "api-job-not-failed"
    await job_svc.create_job(
        job_id=job_id,
        s3_key="temp/ok.pdf",
        status="completed",
        original_filename="ok.pdf",
    )

    now = datetime.now(UTC).isoformat()
    pf = ProcessedFile(
        canvas_file_id="file-ok",
        canvas_updated_at=now,
        job_id=job_id,
        status="completed",
        processed_at=now,
        original_filename="ok.pdf",
    )
    await config_svc.set_processed_file("c-api-400", "file-ok", pf)

    resp = await canvas_api_client.post(
        "/api/v1/canvas/courses/c-api-400/documents/file-ok/retry"
    )
    assert resp.status_code == 400
    assert "not in failed state" in resp.json()["detail"]
