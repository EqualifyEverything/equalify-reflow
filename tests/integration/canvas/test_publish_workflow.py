"""Integration tests for CanvasPublisherService with real S3 + real Redis.

Canvas API is mocked. Tests verify the full publish orchestration reads
from real S3, writes bundles back, and stores results in real Redis.
"""

import json
from unittest.mock import AsyncMock

import pytest
from src.canvas.bundle import DownloadBundleService
from src.canvas.publisher import CanvasPublisherService, PublishError
from src.canvas.renderer import CanvasHTMLRenderer


def _mock_canvas_client(
    page_data: dict | None = None,
    existing_page: dict | None = None,
    upload_file_response: dict | None = None,
    modules: list[dict] | None = None,
) -> AsyncMock:
    """Build a mock CanvasAPIClient with configurable responses."""
    client = AsyncMock()
    client.base_url = "https://canvas.test.edu"
    client.api_token = "test-token"
    client.close = AsyncMock()

    default_page = {
        "url": "test-page-slug",
        "page_id": 101,
        "title": "Test - Reflow",
    }
    client.create_page = AsyncMock(return_value=page_data or default_page)
    client.update_page = AsyncMock(return_value=page_data or default_page)
    client.get_page = AsyncMock(return_value=existing_page)
    client.upload_file = AsyncMock(return_value=upload_file_response or {"id": 999})
    client.list_modules = AsyncMock(return_value=modules or [])
    client.create_module_item = AsyncMock(return_value={"id": 1})

    return client


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_downloads_markdown_from_s3(
    canvas_storage_service, seed_s3_result, real_redis_client
):
    """Seed result.md in S3 → publish reads it and uses it for page body."""
    job_id = "pub-test-md"
    seed_s3_result(job_id, markdown="# Published Doc\n\nReal content from S3.")

    mock_client = _mock_canvas_client()
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    result = await publisher.publish(
        job_id=job_id,
        course_id="c-pub-1",
        original_filename="doc.pdf",
        canvas_file_id="100",
        redis_client=real_redis_client,
    )

    # Canvas page was created with HTML rendered from the markdown
    mock_client.create_page.assert_called_once()
    call_kwargs = mock_client.create_page.call_args[1]
    assert "Published Doc" in call_kwargs["body"]
    assert "Real content from S3" in call_kwargs["body"]
    assert result.job_id == job_id


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_creates_download_bundle_in_s3(
    canvas_storage_service, seed_s3_result, real_redis_client, real_s3_client
):
    """After publish, bundle.zip exists in S3 results bucket."""
    from src.config import settings

    job_id = "pub-test-bundle"
    seed_s3_result(job_id, markdown="# Bundle Test\n\nContent.")

    mock_client = _mock_canvas_client()
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    await publisher.publish(
        job_id=job_id,
        course_id="c-pub-2",
        original_filename="test.pdf",
        canvas_file_id="200",
        redis_client=real_redis_client,
    )

    # Verify bundle.zip was uploaded to S3
    response = real_s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"results/{job_id}/bundle.zip",
    )
    zip_bytes = response["Body"].read()
    assert len(zip_bytes) > 0


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_stores_result_in_redis(
    canvas_storage_service, seed_s3_result, real_redis_client
):
    """PublishResult stored at correct key with TTL."""
    job_id = "pub-test-redis"
    seed_s3_result(job_id)

    mock_client = _mock_canvas_client()
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    await publisher.publish(
        job_id=job_id,
        course_id="c-pub-3",
        original_filename="stored.pdf",
        canvas_file_id="300",
        redis_client=real_redis_client,
    )

    # Verify result was stored in Redis
    key = "eq-pdf:published:c-pub-3:300"
    raw = await real_redis_client.get(key)
    assert raw is not None
    data = json.loads(raw)
    assert data["job_id"] == job_id
    assert data["course_id"] == "c-pub-3"

    # Verify TTL is set (30 days = 2592000s)
    ttl = await real_redis_client.ttl(key)
    assert ttl > 2592000 - 10


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_raises_when_no_markdown(
    canvas_storage_service, real_redis_client
):
    """No result.md in S3 → PublishError."""
    mock_client = _mock_canvas_client()
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    with pytest.raises(PublishError, match="Failed to download result markdown"):
        await publisher.publish(
            job_id="nonexistent-job",
            course_id="c-pub-4",
            original_filename="missing.pdf",
            canvas_file_id="400",
            redis_client=real_redis_client,
        )


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_page_title_convention(
    canvas_storage_service, seed_s3_result, real_redis_client
):
    """'lecture_notes.pdf' → 'lecture_notes - Reflow' page title."""
    job_id = "pub-test-title"
    seed_s3_result(job_id)

    mock_client = _mock_canvas_client()
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    result = await publisher.publish(
        job_id=job_id,
        course_id="c-pub-5",
        original_filename="lecture_notes.pdf",
        canvas_file_id="500",
        redis_client=real_redis_client,
    )

    assert result.page_title == "lecture_notes - Reflow"
    mock_client.create_page.assert_called_once()
    assert mock_client.create_page.call_args[1]["title"] == "lecture_notes - Reflow"


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_uploads_figures_to_canvas(
    canvas_storage_service, seed_s3_result, real_redis_client
):
    """Figures from S3 are passed to mocked Canvas upload."""
    job_id = "pub-test-figs"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    seed_s3_result(
        job_id,
        markdown="# With Figures\n\n![fig](images/figure_1.png)",
        figures={"figure_1.png": fake_png, "figure_2.png": fake_png},
    )

    mock_client = _mock_canvas_client(upload_file_response={"id": 777})
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    result = await publisher.publish(
        job_id=job_id,
        course_id="c-pub-6",
        original_filename="figures.pdf",
        canvas_file_id="600",
        redis_client=real_redis_client,
    )

    # Canvas upload_file was called for each figure
    assert mock_client.upload_file.call_count == 2
    assert result.figure_count == 2
    assert 777 in result.canvas_file_ids


@pytest.mark.integration
@pytest.mark.requires_s3
@pytest.mark.requires_redis
@pytest.mark.asyncio
async def test_publish_handles_figure_upload_failure(
    canvas_storage_service, seed_s3_result, real_redis_client
):
    """One figure fails to upload to Canvas, others succeed, publish completes."""
    job_id = "pub-test-fig-fail"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    seed_s3_result(
        job_id,
        markdown="# Partial Figures",
        figures={"good.png": fake_png, "bad.png": fake_png},
    )

    call_count = 0

    async def upload_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("Canvas upload failed")
        return {"id": 888}

    mock_client = _mock_canvas_client()
    mock_client.upload_file = AsyncMock(side_effect=upload_side_effect)
    renderer = CanvasHTMLRenderer()
    bundle_svc = DownloadBundleService(storage_service=canvas_storage_service)

    publisher = CanvasPublisherService(
        canvas_client=mock_client,
        renderer=renderer,
        bundle_service=bundle_svc,
        storage_service=canvas_storage_service,
    )

    result = await publisher.publish(
        job_id=job_id,
        course_id="c-pub-7",
        original_filename="partial.pdf",
        canvas_file_id="700",
        redis_client=real_redis_client,
    )

    # One figure succeeded, one failed — publish still completed
    assert result.figure_count == 1
    assert 888 in result.canvas_file_ids
