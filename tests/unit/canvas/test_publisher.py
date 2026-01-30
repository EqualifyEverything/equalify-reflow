"""Unit tests for CanvasPublisherService."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.canvas.client import CanvasAPIError
from src.canvas.publisher import (
    CanvasPublisherService,
    PublishError,
    PublishResult,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_canvas_client():
    """Mock CanvasAPIClient."""
    client = MagicMock()
    client.base_url = "https://canvas.example.com"
    client.upload_file = AsyncMock(return_value={"id": 100, "url": "https://canvas.example.com/files/100"})
    client.create_page = AsyncMock(
        return_value={
            "url": "lecture-notes-reflow",
            "page_id": 42,
            "title": "lecture_notes - Reflow",
        }
    )
    client.update_page = AsyncMock(
        return_value={
            "url": "lecture-notes-reflow",
            "page_id": 42,
            "title": "lecture_notes - Reflow",
        }
    )
    client.get_page = AsyncMock(return_value=None)
    client.list_modules = AsyncMock(return_value=[])
    client.create_module_item = AsyncMock(return_value={"id": 1})
    return client


@pytest.fixture
def mock_renderer():
    """Mock CanvasHTMLRenderer."""
    renderer = MagicMock()
    renderer.render.return_value = "<article><p>Hello</p></article>"
    return renderer


@pytest.fixture
def mock_bundle_service():
    """Mock DownloadBundleService."""
    bundle = MagicMock()
    bundle.create_bundle = AsyncMock(return_value="https://s3.example.com/bundle.zip?sig=abc")
    return bundle


@pytest.fixture
def mock_storage():
    """Mock StorageService."""
    storage = MagicMock()
    storage.results_bucket = "equalify-results"
    storage.s3_client = MagicMock()
    storage.download_file = AsyncMock(return_value=b"# Hello World")
    storage.s3_client.list_objects_v2.return_value = {"Contents": []}
    return storage


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    return redis


@pytest.fixture
def service(mock_canvas_client, mock_renderer, mock_bundle_service, mock_storage):
    """Create a CanvasPublisherService for testing."""
    return CanvasPublisherService(
        canvas_client=mock_canvas_client,
        renderer=mock_renderer,
        bundle_service=mock_bundle_service,
        storage_service=mock_storage,
    )


# --- PublishResult model ---


class TestPublishResult:
    """R2: PublishResult Pydantic model."""

    def test_serialization(self):
        result = PublishResult(
            job_id="job-1",
            course_id="101",
            canvas_page_url="lecture-notes-reflow",
            canvas_page_id=42,
            canvas_file_ids=[100, 101],
            download_url="https://s3.example.com/bundle.zip",
            page_title="lecture_notes - Reflow",
            published=False,
            figure_count=2,
        )
        data = result.model_dump()
        assert data["job_id"] == "job-1"
        assert data["canvas_file_ids"] == [100, 101]
        assert data["figure_count"] == 2

    def test_json_round_trip(self):
        result = PublishResult(
            job_id="job-1",
            course_id="101",
            canvas_page_url="test-reflow",
            canvas_page_id=1,
            canvas_file_ids=[],
            download_url="https://example.com",
            page_title="test - Reflow",
            published=True,
            figure_count=0,
        )
        json_str = result.model_dump_json()
        restored = PublishResult.model_validate_json(json_str)
        assert restored == result


# --- PublishError ---


class TestPublishError:
    """R8: PublishError includes job_id and course_id."""

    def test_includes_context(self):
        err = PublishError("something broke", job_id="j-1", course_id="c-1")
        assert err.job_id == "j-1"
        assert err.course_id == "c-1"
        assert "j-1" in str(err)
        assert "c-1" in str(err)
        assert "something broke" in str(err)


# --- CanvasPublisherService init ---


class TestInit:
    """R1: Constructor accepts required dependencies."""

    def test_stores_dependencies(self, mock_canvas_client, mock_renderer, mock_bundle_service, mock_storage):
        svc = CanvasPublisherService(
            canvas_client=mock_canvas_client,
            renderer=mock_renderer,
            bundle_service=mock_bundle_service,
            storage_service=mock_storage,
        )
        assert svc.canvas is mock_canvas_client
        assert svc.renderer is mock_renderer
        assert svc.bundle is mock_bundle_service
        assert svc.storage is mock_storage


# --- Page naming ---


class TestPageTitle:
    """R4: Page title convention."""

    def test_pdf_extension_removed(self):
        assert CanvasPublisherService._build_page_title("lecture_notes.pdf") == "lecture_notes - Reflow"

    def test_spaces_in_name(self):
        assert CanvasPublisherService._build_page_title("Chapter 4 Reading.pdf") == "Chapter 4 Reading - Reflow"

    def test_no_extension(self):
        assert CanvasPublisherService._build_page_title("notes") == "notes - Reflow"


# --- Full publish workflow ---


class TestPublish:
    """R1-R8: Full publish orchestration."""

    @pytest.mark.asyncio
    async def test_returns_publish_result(self, service, mock_redis):
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="lecture_notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert isinstance(result, PublishResult)
        assert result.job_id == "job-1"
        assert result.course_id == "101"
        assert result.page_title == "lecture_notes - Reflow"
        assert result.published is False

    @pytest.mark.asyncio
    async def test_publishes_page_when_flag_set(self, service, mock_redis):
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
            published=True,
        )
        assert result.published is True

    @pytest.mark.asyncio
    async def test_passes_published_true_to_canvas_api(self, service, mock_canvas_client, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
            published=True,
        )
        call_kwargs = mock_canvas_client.create_page.call_args.kwargs
        assert call_kwargs["published"] is True

    @pytest.mark.asyncio
    async def test_defaults_to_draft(self, service, mock_canvas_client, mock_redis):
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        call_kwargs = mock_canvas_client.create_page.call_args.kwargs
        assert call_kwargs["published"] is False
        assert result.published is False

    @pytest.mark.asyncio
    async def test_downloads_result_markdown(self, service, mock_storage, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_storage.download_file.assert_any_await("results/job-1/result.md")

    @pytest.mark.asyncio
    async def test_creates_download_bundle(self, service, mock_bundle_service, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="lecture_notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_bundle_service.create_bundle.assert_awaited_once_with("job-1", "lecture_notes.pdf")

    @pytest.mark.asyncio
    async def test_renders_html_with_download_url(self, service, mock_renderer, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_renderer.render.assert_called_once()
        call_kwargs = mock_renderer.render.call_args
        assert call_kwargs.kwargs["download_url"] == "https://s3.example.com/bundle.zip?sig=abc"
        assert call_kwargs.kwargs["markdown_content"] == "# Hello World"

    @pytest.mark.asyncio
    async def test_creates_canvas_page(self, service, mock_canvas_client, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_page.assert_awaited_once()
        call_kwargs = mock_canvas_client.create_page.call_args
        assert call_kwargs.kwargs["course_id"] == "101"
        assert call_kwargs.kwargs["title"] == "notes - Reflow"
        assert call_kwargs.kwargs["published"] is False
        assert call_kwargs.kwargs["editing_roles"] == "teachers"

    @pytest.mark.asyncio
    async def test_stores_result_in_redis(self, service, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_redis.set.assert_awaited_once()
        call_args = mock_redis.set.call_args
        assert call_args.args[0] == "eq-pdf:published:101:500"
        # TTL = 30 days
        assert call_args.kwargs["ex"] == 30 * 24 * 60 * 60


# --- R5: Page creation and update ---


class TestPageCreationAndUpdate:
    """R5: Create new page or update existing."""

    @pytest.mark.asyncio
    async def test_updates_existing_page(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.get_page.return_value = {
            "url": "notes-reflow",
            "page_id": 42,
        }
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.update_page.assert_awaited_once()
        mock_canvas_client.create_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_page_when_not_found(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.get_page.return_value = None
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_page.assert_awaited_once()
        mock_canvas_client.update_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_page_when_get_page_raises(self, service, mock_canvas_client, mock_redis):
        """If get_page raises a non-404 error, fall through to create."""
        mock_canvas_client.get_page.side_effect = CanvasAPIError("HTTP 500")
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_skips_module_placement(self, service, mock_canvas_client, mock_redis):
        """When re-processing updates an existing page, module placement is skipped to avoid duplicates."""
        mock_canvas_client.get_page.return_value = {
            "url": "notes-reflow",
            "page_id": 42,
        }
        mock_canvas_client.list_modules.return_value = [
            {
                "id": 10,
                "name": "Week 1",
                "items": [
                    {"content_id": 500, "type": "File", "position": 3},
                ],
            }
        ]
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.update_page.assert_awaited_once()
        # Module placement should be skipped entirely — no list_modules or create_module_item calls
        mock_canvas_client.list_modules.assert_not_awaited()
        mock_canvas_client.create_module_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_performs_module_placement(self, service, mock_canvas_client, mock_redis):
        """When creating a new page, module placement proceeds normally."""
        mock_canvas_client.get_page.return_value = None
        mock_canvas_client.list_modules.return_value = [
            {
                "id": 10,
                "name": "Week 1",
                "items": [
                    {"content_id": 500, "type": "File", "position": 3},
                ],
            }
        ]
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_page.assert_awaited_once()
        mock_canvas_client.create_module_item.assert_awaited_once()
        call_kwargs = mock_canvas_client.create_module_item.call_args.kwargs
        assert call_kwargs["position"] == 4  # PDF position (3) + 1


# --- R3: Image upload to Canvas Files ---


class TestFigureUpload:
    """R3: Image upload to Canvas Files."""

    @pytest.fixture
    def storage_with_figures(self, mock_storage):
        """Storage with 2 figures in S3."""
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/job-1/figures/figure_1.png"},
                {"Key": "results/job-1/figures/figure_2.png"},
            ]
        }

        async def download_file(key):
            if "figure" in key:
                return b"\x89PNG\r\n\x1a\n"  # PNG header bytes
            return b"# Hello"

        mock_storage.download_file = AsyncMock(side_effect=download_file)
        return mock_storage

    @pytest.mark.asyncio
    async def test_uploads_figures_to_canvas(self, service, mock_canvas_client, storage_with_figures, mock_redis):
        service.storage = storage_with_figures
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert mock_canvas_client.upload_file.await_count == 2
        assert result.figure_count == 2
        assert result.canvas_file_ids == [100, 100]

    @pytest.mark.asyncio
    async def test_figure_upload_uses_correct_folder(
        self, service, mock_canvas_client, storage_with_figures, mock_redis
    ):
        service.storage = storage_with_figures
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        first_call = mock_canvas_client.upload_file.call_args_list[0]
        assert first_call.kwargs["parent_folder_path"] == "equalify-reflow"
        assert first_call.kwargs["content_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_figure_upload_uses_job_id_filenames(
        self, service, mock_canvas_client, storage_with_figures, mock_redis
    ):
        service.storage = storage_with_figures
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        filenames = [call.kwargs["filename"] for call in mock_canvas_client.upload_file.call_args_list]
        assert "job-1-figure-1.png" in filenames
        assert "job-1-figure-2.png" in filenames

    @pytest.mark.asyncio
    async def test_builds_image_url_map(self, service, mock_renderer, storage_with_figures, mock_redis):
        service.storage = storage_with_figures
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        call_kwargs = mock_renderer.render.call_args.kwargs
        image_map = call_kwargs["image_url_map"]
        assert "images/figure_1.png" in image_map
        assert "images/figure_2.png" in image_map
        assert "/files/100/preview" in image_map["images/figure_1.png"]

    @pytest.mark.asyncio
    async def test_individual_figure_failure_does_not_fail_publish(
        self, service, mock_canvas_client, storage_with_figures, mock_redis
    ):
        """R3/R8: Individual image upload failures are non-fatal."""
        service.storage = storage_with_figures
        mock_canvas_client.upload_file.side_effect = [
            CanvasAPIError("Upload failed"),
            {"id": 200, "url": "https://canvas.example.com/files/200"},
        ]
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        # Only one figure succeeded
        assert result.figure_count == 1
        assert result.canvas_file_ids == [200]

    @pytest.mark.asyncio
    async def test_no_figures_in_s3(self, service, mock_canvas_client, mock_redis):
        """When no figures exist, publish succeeds with empty file IDs."""
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert result.figure_count == 0
        assert result.canvas_file_ids == []
        mock_canvas_client.upload_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_s3_listing_failure_is_non_fatal(self, service, mock_storage, mock_redis):
        """If listing figures in S3 fails, publish continues with no figures."""
        mock_storage.s3_client.list_objects_v2.side_effect = Exception("S3 unavailable")
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert result.figure_count == 0
        assert result.canvas_file_ids == []

    @pytest.mark.asyncio
    async def test_canvas_upload_returns_no_id(self, service, mock_canvas_client, storage_with_figures, mock_redis):
        """If Canvas upload returns a file without an id, skip that figure."""
        service.storage = storage_with_figures
        mock_canvas_client.upload_file = AsyncMock(return_value={"url": "https://canvas.example.com/files/unknown"})
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert result.figure_count == 0
        assert result.canvas_file_ids == []


# --- R6: Module placement ---


class TestModulePlacement:
    """R6: Add page to course module after the PDF."""

    @pytest.mark.asyncio
    async def test_adds_page_after_pdf_in_module(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.list_modules.return_value = [
            {
                "id": 10,
                "name": "Week 1",
                "items": [
                    {"content_id": 500, "type": "File", "position": 3},
                ],
            }
        ]
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_module_item.assert_awaited_once()
        call_kwargs = mock_canvas_client.create_module_item.call_args.kwargs
        assert call_kwargs["module_id"] == "10"
        assert call_kwargs["item_type"] == "Page"
        assert call_kwargs["position"] == 4  # PDF position + 1

    @pytest.mark.asyncio
    async def test_skips_when_pdf_not_in_module(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.list_modules.return_value = [
            {
                "id": 10,
                "name": "Week 1",
                "items": [
                    {"content_id": 999, "type": "File", "position": 1},
                ],
            }
        ]
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_module_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_when_no_modules(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.list_modules.return_value = []
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        mock_canvas_client.create_module_item.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_module_placement_failure_is_non_fatal(self, service, mock_canvas_client, mock_redis):
        """R8: Module placement failure does not fail the publish."""
        mock_canvas_client.list_modules.return_value = [
            {
                "id": 10,
                "name": "Week 1",
                "items": [
                    {"content_id": 500, "type": "File", "position": 1},
                ],
            }
        ]
        mock_canvas_client.create_module_item.side_effect = CanvasAPIError("Module item creation failed")
        # Should NOT raise
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert isinstance(result, PublishResult)

    @pytest.mark.asyncio
    async def test_list_modules_failure_is_non_fatal(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.list_modules.side_effect = CanvasAPIError("Modules API failed")
        result = await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        assert isinstance(result, PublishResult)


# --- R8: Error handling ---


class TestErrorHandling:
    """R8: Critical errors raise PublishError."""

    @pytest.mark.asyncio
    async def test_markdown_download_failure_raises_publish_error(self, service, mock_storage, mock_redis):
        mock_storage.download_file = AsyncMock(side_effect=Exception("S3 down"))
        with pytest.raises(PublishError) as exc_info:
            await service.publish(
                job_id="job-1",
                course_id="101",
                original_filename="notes.pdf",
                canvas_file_id="500",
                redis_client=mock_redis,
            )
        assert exc_info.value.job_id == "job-1"
        assert exc_info.value.course_id == "101"
        assert "markdown" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_page_creation_failure_raises_publish_error(self, service, mock_canvas_client, mock_redis):
        mock_canvas_client.create_page.side_effect = CanvasAPIError("Canvas API POST failed: HTTP 500")
        with pytest.raises(PublishError) as exc_info:
            await service.publish(
                job_id="job-1",
                course_id="101",
                original_filename="notes.pdf",
                canvas_file_id="500",
                redis_client=mock_redis,
            )
        assert exc_info.value.job_id == "job-1"
        assert exc_info.value.course_id == "101"


# --- R7: State tracking ---


class TestStateTracking:
    """R7: Publish result stored in Redis."""

    @pytest.mark.asyncio
    async def test_redis_key_format(self, service, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        key = mock_redis.set.call_args.args[0]
        assert key == "eq-pdf:published:101:500"

    @pytest.mark.asyncio
    async def test_redis_value_is_json(self, service, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        value = mock_redis.set.call_args.args[1]
        # Should be valid JSON that can be parsed back into PublishResult
        restored = PublishResult.model_validate_json(value)
        assert restored.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_redis_ttl_30_days(self, service, mock_redis):
        await service.publish(
            job_id="job-1",
            course_id="101",
            original_filename="notes.pdf",
            canvas_file_id="500",
            redis_client=mock_redis,
        )
        ttl = mock_redis.set.call_args.kwargs["ex"]
        assert ttl == 30 * 24 * 60 * 60
