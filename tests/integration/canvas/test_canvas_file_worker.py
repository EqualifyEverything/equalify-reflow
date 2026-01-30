"""Integration tests for CanvasFileWorker with real Redis + real S3.

Canvas API is mocked — tests verify the worker's file queuing pipeline
creates real state in Redis and S3.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.config import settings
from src.workers.canvas_file_worker import CanvasFileWorker


def _make_canvas_file(
    file_id: int = 42,
    display_name: str = "lecture_notes.pdf",
    updated_at: str = "2025-01-15T10:00:00+00:00",
    url: str = "https://canvas.test.edu/files/42/download",
) -> dict:
    """Create a mock Canvas file API response object."""
    return {
        "id": file_id,
        "display_name": display_name,
        "filename": display_name,
        "updated_at": updated_at,
        "url": url,
        "content-type": "application/pdf",
        "size": 50000,
    }


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_course_queues_new_file(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
):
    """Mock Canvas returns 1 PDF → job created in real Redis, PDF uploaded to real S3."""
    # Setup: enable course with API token
    config = sample_course_config(enabled=True, token="tok-1")
    await course_config_service.set_config("course-w1", config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200

    # Mock the CanvasAPIClient that poll_course creates internally
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(return_value=[_make_canvas_file()])
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-1"

    # Mock HTTP download
    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        queued = await worker.poll_course("course-w1")

    assert queued == 1

    # Verify processed file record was stored in Redis
    pf = await course_config_service.get_processed_file("course-w1", "42")
    assert pf is not None
    assert pf.status == "processing"
    assert pf.original_filename == "lecture_notes.pdf"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_course_skips_already_processed(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
    sample_processed_file,
):
    """Pre-seed processed file → Canvas file skipped."""
    config = sample_course_config(enabled=True, token="tok-2")
    await course_config_service.set_config("course-w2", config)

    ts = "2025-01-15T10:00:00+00:00"
    pf = sample_processed_file(canvas_file_id="42", canvas_updated_at=ts, status="completed")
    await course_config_service.set_processed_file("course-w2", "42", pf)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(
        return_value=[_make_canvas_file(updated_at=ts)]
    )
    mock_client.close = AsyncMock()

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        queued = await worker.poll_course("course-w2")

    assert queued == 0


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_course_reprocesses_updated_file(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
    sample_processed_file,
):
    """Pre-seed with old timestamp → new file queued."""
    config = sample_course_config(enabled=True, token="tok-3")
    await course_config_service.set_config("course-w3", config)

    old_ts = "2025-01-01T00:00:00+00:00"
    pf = sample_processed_file(canvas_file_id="42", canvas_updated_at=old_ts, status="completed")
    await course_config_service.set_processed_file("course-w3", "42", pf)

    new_ts = "2025-02-01T00:00:00+00:00"

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(
        return_value=[_make_canvas_file(updated_at=new_ts)]
    )
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-3"

    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        queued = await worker.poll_course("course-w3")

    assert queued == 1


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_course_stores_processed_file_record(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
):
    """After queuing, ProcessedFile exists in Redis with correct data."""
    config = sample_course_config(enabled=True, token="tok-4")
    await course_config_service.set_config("course-w4", config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(
        return_value=[_make_canvas_file(file_id=55, display_name="slides.pdf")]
    )
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-4"

    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        await worker.poll_course("course-w4")

    pf = await course_config_service.get_processed_file("course-w4", "55")
    assert pf is not None
    assert pf.canvas_file_id == "55"
    assert pf.original_filename == "slides.pdf"
    assert pf.status == "processing"
    assert pf.canvas_updated_at == "2025-01-15T10:00:00+00:00"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_course_stores_canvas_metadata_on_job(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
):
    """Job in Redis has source=canvas_auto, course_id, canvas_file_id."""
    config = sample_course_config(enabled=True, token="tok-5")
    await course_config_service.set_config("course-w5", config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(
        return_value=[_make_canvas_file(file_id=66)]
    )
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-5"

    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        await worker.poll_course("course-w5")

    # Find the job created by the worker
    pf = await course_config_service.get_processed_file("course-w5", "66")
    assert pf is not None
    job = await canvas_job_service.get_job(pf.job_id)
    assert job is not None
    assert job["source"] == "canvas_auto"
    assert job["course_id"] == "course-w5"
    assert job["canvas_file_id"] == "66"


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_poll_courses_multiple_courses(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
):
    """Enable 2 courses, each gets polled and files are queued."""
    for cid in ["course-multi-a", "course-multi-b"]:
        config = sample_course_config(enabled=True, token=f"tok-{cid}")
        await course_config_service.set_config(cid, config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(return_value=[_make_canvas_file()])
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-multi"

    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        total = await worker.poll_courses()

    assert total == 2


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_poll_course_skips_missing_token(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
):
    """Course with no token → returns 0."""
    config = sample_course_config(enabled=True, token="")
    await course_config_service.set_config("course-no-tok", config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    queued = await worker.poll_course("course-no-tok")
    assert queued == 0


@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.requires_s3
@pytest.mark.asyncio
async def test_queue_file_creates_pii_queue_entry(
    course_config_service,
    canvas_job_service,
    canvas_storage_service,
    canvas_queue_service,
    sample_course_config,
    real_redis_client,
):
    """PII queue has the job after queuing."""
    config = sample_course_config(enabled=True, token="tok-pii")
    await course_config_service.set_config("course-pii", config)

    worker = CanvasFileWorker(
        config_service=course_config_service,
        job_service=canvas_job_service,
        storage_service=canvas_storage_service,
        queue_service=canvas_queue_service,
    )

    fake_pdf = b"%PDF-1.4 " + b"x" * 200
    mock_client = AsyncMock()
    mock_client.list_course_files = AsyncMock(
        return_value=[_make_canvas_file(file_id=88)]
    )
    mock_client.close = AsyncMock()
    mock_client._is_docker_url = False
    mock_client.api_token = "tok-pii"

    mock_response = MagicMock()
    mock_response.content = fake_pdf
    mock_response.raise_for_status = MagicMock()
    mock_http_client = AsyncMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)
    mock_client._get_client = MagicMock(return_value=mock_http_client)

    with patch(
        "src.workers.canvas_file_worker.CanvasAPIClient",
        return_value=mock_client,
    ):
        await worker.poll_course("course-pii")

    # Check PII queue has an entry
    queue_len = await real_redis_client.llen(settings.pii_queue_name)
    assert queue_len >= 1
