"""Unit tests for CanvasFileWorker.

Tests cover:
- Polling loop lifecycle (start, shutdown, error resilience)
- Course polling logic (enabled courses, per-course polling)
- File discovery (new files, already-processed files, updated files)
- File queuing (download, S3 upload, job creation, processed record)
- Edge cases (no enabled courses, missing tokens, download failures)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.canvas.client import CanvasAPIError
from src.canvas.course_config import CourseConfig, CourseConfigService, ProcessedFile
from src.services.converter_client import ConverterClient, SubmitResult
from src.workers.canvas_file_worker import CanvasFileWorker

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config_service():
    """Mock CourseConfigService."""
    mock = AsyncMock(spec=CourseConfigService)
    mock.list_enabled_courses = AsyncMock(return_value=[])
    mock.get_config = AsyncMock(return_value=None)
    mock.is_file_new_or_updated = AsyncMock(return_value=False)
    mock.set_processed_file = AsyncMock()
    return mock


@pytest.fixture
def mock_converter():
    """Mock ConverterClient for canvas file worker tests."""
    mock = MagicMock(spec=ConverterClient)
    mock.submit_document = AsyncMock(
        return_value=SubmitResult(job_id="test-job-id", s3_key="temp/test-job-id.pdf", status="pii_scanning")
    )
    mock.get_job_status = AsyncMock()
    mock.retry_job = AsyncMock()
    return mock


@pytest.fixture
def worker(mock_config_service, mock_converter):
    """Create a CanvasFileWorker with mocked dependencies."""
    return CanvasFileWorker(
        config_service=mock_config_service,
        converter=mock_converter,
    )


@pytest.fixture
def sample_course_config():
    """CourseConfig with a valid API token."""
    return CourseConfig(
        enabled=True,
        auto_publish_threshold=1.0,
        canvas_api_token="test-token-123",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )


@pytest.fixture
def sample_canvas_file():
    """A Canvas file object as returned by the Files API."""
    return {
        "id": 42,
        "display_name": "lecture-notes.pdf",
        "filename": "lecture-notes.pdf",
        "content-type": "application/pdf",
        "url": "https://canvas.example.com/files/42/download",
        "updated_at": "2024-06-15T10:30:00Z",
        "created_at": "2024-06-15T10:00:00Z",
        "size": 1024000,
    }


# ===========================================================================
# Polling loop lifecycle tests
# ===========================================================================


class TestRunLifecycle:
    """Tests for the run() polling loop."""

    @pytest.mark.asyncio
    async def test_run_exits_on_shutdown_event(self, worker):
        """Worker exits gracefully when shutdown_event is set."""
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        await worker.run(shutdown_event)

        assert not worker.running

    @pytest.mark.asyncio
    async def test_run_polls_courses(self, worker, mock_config_service):
        """Worker calls poll_courses on each iteration."""
        mock_config_service.list_enabled_courses.return_value = []

        shutdown_event = asyncio.Event()
        poll_count = 0

        original_poll = worker.poll_courses

        async def counting_poll():
            nonlocal poll_count
            poll_count += 1
            result = await original_poll()
            shutdown_event.set()
            return result

        worker.poll_courses = counting_poll

        await worker.run(shutdown_event)

        assert poll_count >= 1

    @pytest.mark.asyncio
    async def test_run_does_not_crash_on_exception(self, worker, mock_config_service):
        """Worker continues after exceptions in poll_courses."""
        call_count = 0
        shutdown_event = asyncio.Event()

        async def failing_then_shutdown():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated error")
            shutdown_event.set()
            return 0

        worker.poll_courses = failing_then_shutdown
        worker.polling_interval_seconds = 0

        with patch("src.workers.canvas_file_worker.settings") as mock_settings:
            mock_settings.worker_error_sleep_seconds = 0
            await worker.run(shutdown_event)

        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_run_exits_during_error_sleep_on_shutdown(self, worker):
        """Worker exits promptly from error sleep when shutdown_event is set."""
        shutdown_event = asyncio.Event()
        call_count = 0

        async def fail_then_never_called():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated error")
            # Should never reach here — shutdown during error sleep
            return 0  # pragma: no cover

        worker.poll_courses = fail_then_never_called

        sleep_calls: list[float] = []
        original_sleep = asyncio.sleep

        async def tracking_sleep(duration):
            sleep_calls.append(duration)
            # Set shutdown after the first error-sleep tick
            shutdown_event.set()
            await original_sleep(0)

        with patch("src.workers.canvas_file_worker.settings") as mock_settings:
            mock_settings.worker_error_sleep_seconds = 60  # Long sleep
            with patch("src.workers.canvas_file_worker.asyncio.sleep", side_effect=tracking_sleep):
                await worker.run(shutdown_event)

        # Only one poll attempt before error, then exit during error sleep
        assert call_count == 1
        assert not worker.running
        # Should have slept only once (1s tick), not the full 60s
        assert len(sleep_calls) == 1

    @pytest.mark.asyncio
    async def test_run_resets_active_gauge_on_exit(self, worker):
        """Worker resets the active gauge to 0 when exiting."""
        shutdown_event = asyncio.Event()
        shutdown_event.set()

        with patch("src.workers.canvas_file_worker.worker_active_gauge") as mock_gauge:
            mock_labels = MagicMock()
            mock_gauge.labels.return_value = mock_labels

            await worker.run(shutdown_event)

            # Gauge set to 1 on start, then 0 on exit
            assert mock_labels.set.call_count == 2
            mock_labels.set.assert_any_call(1)
            mock_labels.set.assert_any_call(0)

    @pytest.mark.asyncio
    async def test_run_does_not_crash_on_unexpected_exception(self, worker, mock_config_service):
        """Worker exits gracefully on unexpected exceptions outside the inner handler."""
        shutdown_event = asyncio.Event()
        mock_config_service.list_enabled_courses.return_value = []

        # Force an exception during the polling-interval sleep loop (outside inner try/except)
        with patch("src.workers.canvas_file_worker.asyncio.sleep", side_effect=RuntimeError("unexpected")):
            with patch("src.workers.canvas_file_worker.worker_errors_total") as mock_errors:
                mock_labels = MagicMock()
                mock_errors.labels.return_value = mock_labels

                # run() should NOT raise — it catches the unexpected error and exits gracefully
                await worker.run(shutdown_event)

                mock_errors.labels.assert_called_with(worker_name="canvas_file", error_type="RuntimeError")
                mock_labels.inc.assert_called_once()

        assert not worker.running

    @pytest.mark.asyncio
    async def test_run_resets_gauge_on_unexpected_exception(self, worker, mock_config_service):
        """Active gauge is reset to 0 even when an unexpected exception occurs."""
        shutdown_event = asyncio.Event()
        mock_config_service.list_enabled_courses.return_value = []

        with patch("src.workers.canvas_file_worker.asyncio.sleep", side_effect=RuntimeError("boom")):
            with patch("src.workers.canvas_file_worker.worker_active_gauge") as mock_gauge:
                mock_labels = MagicMock()
                mock_gauge.labels.return_value = mock_labels

                await worker.run(shutdown_event)

                # Gauge set to 1 on start, then 0 on exit via finally
                assert mock_labels.set.call_count == 2
                mock_labels.set.assert_any_call(1)
                mock_labels.set.assert_any_call(0)

    @pytest.mark.asyncio
    async def test_run_uses_custom_polling_interval(
        self, mock_config_service, mock_converter
    ):
        """Worker uses the polling_interval_seconds passed at construction."""
        custom_interval = 45
        worker = CanvasFileWorker(
            config_service=mock_config_service,
            converter=mock_converter,
            polling_interval_seconds=custom_interval,
        )

        assert worker.polling_interval_seconds == custom_interval

        shutdown_event = asyncio.Event()
        sleep_calls: list[float] = []

        original_sleep = asyncio.sleep

        async def tracking_sleep(duration):
            sleep_calls.append(duration)
            if len(sleep_calls) >= custom_interval:
                shutdown_event.set()
            await original_sleep(0)

        mock_config_service.list_enabled_courses.return_value = []

        with patch("src.workers.canvas_file_worker.asyncio.sleep", side_effect=tracking_sleep):
            await worker.run(shutdown_event)

        # The worker sleeps 1s per iteration for `polling_interval_seconds` iterations
        assert len(sleep_calls) == custom_interval


# ===========================================================================
# Polling interval configuration tests
# ===========================================================================


class TestPollingIntervalConfig:
    """Tests for configurable polling_interval_seconds."""

    def test_defaults_to_settings_value(
        self, mock_config_service, mock_converter
    ):
        """Without explicit interval, uses settings.canvas_polling_interval_seconds."""
        with patch("src.workers.canvas_file_worker.settings") as mock_settings:
            mock_settings.canvas_polling_interval_seconds = 200
            worker = CanvasFileWorker(
                config_service=mock_config_service,
                converter=mock_converter,
            )
        assert worker.polling_interval_seconds == 200

    def test_explicit_interval_overrides_settings(
        self, mock_config_service, mock_converter
    ):
        """Explicit polling_interval_seconds overrides the settings default."""
        worker = CanvasFileWorker(
            config_service=mock_config_service,
            converter=mock_converter,
            polling_interval_seconds=60,
        )
        assert worker.polling_interval_seconds == 60

    def test_none_interval_falls_back_to_settings(
        self, mock_config_service, mock_converter
    ):
        """Passing None explicitly falls back to settings."""
        with patch("src.workers.canvas_file_worker.settings") as mock_settings:
            mock_settings.canvas_polling_interval_seconds = 300
            worker = CanvasFileWorker(
                config_service=mock_config_service,
                converter=mock_converter,
                polling_interval_seconds=None,
            )
        assert worker.polling_interval_seconds == 300

    @pytest.mark.asyncio
    async def test_start_canvas_file_worker_passes_interval(self):
        """start_canvas_file_worker passes settings interval to the worker."""
        from src.workers.canvas_file_worker import start_canvas_file_worker

        shutdown_event = asyncio.Event()
        shutdown_event.set()

        mock_redis = AsyncMock()
        mock_s3 = MagicMock()

        async def mock_redis_gen():
            yield mock_redis

        async def mock_s3_gen():
            yield mock_s3

        with (
            patch("src.dependencies.get_redis_client", mock_redis_gen),
            patch("src.dependencies.get_s3_client", mock_s3_gen),
            patch("src.workers.canvas_file_worker.settings") as mock_settings,
            patch.object(CanvasFileWorker, "run", new_callable=AsyncMock),
            patch.object(CanvasFileWorker, "__init__", return_value=None) as mock_init,
        ):
            mock_settings.canvas_polling_interval_seconds = 90
            mock_settings.canvas_api_url = "https://canvas.example.com"
            mock_settings.canvas_rate_limit_buffer = 50
            mock_settings.s3_temp_bucket = "temp"
            mock_settings.s3_results_bucket = "results"
            await start_canvas_file_worker(shutdown_event)

            # Verify polling_interval_seconds was passed to the constructor
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["polling_interval_seconds"] == 90
            assert "converter" in call_kwargs


# ===========================================================================
# poll_courses tests
# ===========================================================================


class TestPollCourses:
    """Tests for poll_courses()."""

    @pytest.mark.asyncio
    async def test_no_enabled_courses_returns_zero(self, worker, mock_config_service):
        """Returns 0 when no courses are enabled."""
        mock_config_service.list_enabled_courses.return_value = []

        result = await worker.poll_courses()

        assert result == 0
        mock_config_service.list_enabled_courses.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_polls_each_enabled_course(self, worker, mock_config_service):
        """Calls poll_course for each enabled course."""
        mock_config_service.list_enabled_courses.return_value = ["101", "202", "303"]

        # Mock poll_course to avoid actually calling Canvas
        worker.poll_course = AsyncMock(return_value=0)

        result = await worker.poll_courses()

        assert result == 0
        assert worker.poll_course.call_count == 3
        worker.poll_course.assert_any_await("101")
        worker.poll_course.assert_any_await("202")
        worker.poll_course.assert_any_await("303")

    @pytest.mark.asyncio
    async def test_continues_after_single_course_failure(self, worker, mock_config_service):
        """A failure polling one course doesn't stop polling others."""
        mock_config_service.list_enabled_courses.return_value = ["101", "202"]

        call_count = 0

        async def mock_poll_course(course_id):
            nonlocal call_count
            call_count += 1
            if course_id == "101":
                raise CanvasAPIError("Rate limited")
            return 1

        worker.poll_course = mock_poll_course

        result = await worker.poll_courses()

        assert call_count == 2
        assert result == 1  # Only course 202 succeeded

    @pytest.mark.asyncio
    async def test_aggregates_queued_count(self, worker, mock_config_service):
        """Returns sum of queued files across all courses."""
        mock_config_service.list_enabled_courses.return_value = ["101", "202"]
        worker.poll_course = AsyncMock(side_effect=[3, 2])

        result = await worker.poll_courses()

        assert result == 5


# ===========================================================================
# poll_course tests
# ===========================================================================


class TestPollCourse:
    """Tests for poll_course()."""

    @pytest.mark.asyncio
    async def test_skips_course_without_token(self, worker, mock_config_service):
        """Returns 0 and logs warning when course has no API token."""
        mock_config_service.get_config.return_value = CourseConfig(enabled=True, canvas_api_token="")

        result = await worker.poll_course("101")

        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_course_with_no_config(self, worker, mock_config_service):
        """Returns 0 when course config is None."""
        mock_config_service.get_config.return_value = None

        result = await worker.poll_course("101")

        assert result == 0

    @pytest.mark.asyncio
    async def test_creates_client_with_course_api_token(self, worker, mock_config_service, sample_course_config):
        """CanvasAPIClient is created using the course's stored API token."""
        mock_config_service.get_config.return_value = sample_course_config

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[])
            mock_client_instance.close = AsyncMock()

            with patch("src.workers.canvas_file_worker.settings") as mock_settings:
                mock_settings.canvas_api_url = "https://canvas.example.com"
                mock_settings.canvas_rate_limit_buffer = 75

                await worker.poll_course("101")

            mock_client_cls.assert_called_once_with(
                base_url="https://canvas.example.com",
                api_token="test-token-123",
                rate_limit_buffer=75,
                host_header=mock_settings.canvas_host_header,
            )

    @pytest.mark.asyncio
    async def test_each_course_uses_its_own_token(self, worker, mock_config_service):
        """Each course creates a CanvasAPIClient with its own stored token."""
        course_a_config = CourseConfig(enabled=True, canvas_api_token="token-course-a")
        course_b_config = CourseConfig(enabled=True, canvas_api_token="token-course-b")

        mock_config_service.get_config.side_effect = [course_a_config, course_b_config]
        mock_config_service.list_enabled_courses.return_value = ["A", "B"]

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[])
            mock_client_instance.close = AsyncMock()

            await worker.poll_courses()

        assert mock_client_cls.call_count == 2
        tokens_used = [call.kwargs["api_token"] for call in mock_client_cls.call_args_list]
        assert tokens_used == ["token-course-a", "token-course-b"]

    @pytest.mark.asyncio
    async def test_filters_for_pdf_content_type(self, worker, mock_config_service, sample_course_config):
        """poll_course() passes content_types=['application/pdf'] to list_course_files."""
        mock_config_service.get_config.return_value = sample_course_config

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[])
            mock_client_instance.close = AsyncMock()

            await worker.poll_course("101")

            mock_client_instance.list_course_files.assert_awaited_once_with(
                "101",
                content_types=["application/pdf"],
                sort="created_at",
                order="desc",
            )

    @pytest.mark.asyncio
    async def test_skips_already_processed_files(
        self, worker, mock_config_service, sample_course_config, sample_canvas_file
    ):
        """Files already processed are skipped."""
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = False

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[sample_canvas_file])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 0
        mock_config_service.is_file_new_or_updated.assert_awaited_once_with("101", "42", "2024-06-15T10:30:00Z")

    @pytest.mark.asyncio
    async def test_queues_new_file(
        self,
        worker,
        mock_config_service,
        mock_converter,
        sample_course_config,
        sample_canvas_file,
    ):
        """New files are queued for processing."""
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = True

        # Build a fake PDF content (>100 bytes)
        fake_pdf = b"%PDF-1.4 " + b"x" * 200

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[sample_canvas_file])
            mock_client_instance.close = AsyncMock()
            mock_client_instance.api_token = "test-token-123"
            mock_client_instance._is_docker_url = False
            mock_client_instance._docker_host_header = "localhost"

            # Mock the HTTP client for file download
            mock_http_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = fake_pdf
            mock_response.raise_for_status = MagicMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_client_instance._get_client = MagicMock(return_value=mock_http_client)

            result = await worker.poll_course("101")

        assert result == 1
        mock_converter.submit_document.assert_awaited_once()
        mock_config_service.set_processed_file.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_closes_client_on_success(self, worker, mock_config_service, sample_course_config):
        """CanvasAPIClient is closed after polling."""
        mock_config_service.get_config.return_value = sample_course_config

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[])
            mock_client_instance.close = AsyncMock()

            await worker.poll_course("101")

            mock_client_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_closes_client_on_error(self, worker, mock_config_service, sample_course_config):
        """CanvasAPIClient is closed even when listing files fails."""
        mock_config_service.get_config.return_value = sample_course_config

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(side_effect=CanvasAPIError("API error"))
            mock_client_instance.close = AsyncMock()

            with pytest.raises(CanvasAPIError):
                await worker.poll_course("101")

            mock_client_instance.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_is_file_new_or_updated_for_each_pdf(self, worker, mock_config_service, sample_course_config):
        """poll_course() calls is_file_new_or_updated() for every PDF file returned."""
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = False

        files = [
            {"id": 10, "display_name": "a.pdf", "updated_at": "2024-06-15T10:00:00Z"},
            {"id": 20, "display_name": "b.pdf", "updated_at": "2024-06-15T11:00:00Z"},
            {"id": 30, "display_name": "c.pdf", "updated_at": "2024-06-15T12:00:00Z"},
        ]

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=files)
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 0
        assert mock_config_service.is_file_new_or_updated.await_count == 3
        mock_config_service.is_file_new_or_updated.assert_any_await("101", "10", "2024-06-15T10:00:00Z")
        mock_config_service.is_file_new_or_updated.assert_any_await("101", "20", "2024-06-15T11:00:00Z")
        mock_config_service.is_file_new_or_updated.assert_any_await("101", "30", "2024-06-15T12:00:00Z")

    @pytest.mark.asyncio
    async def test_skips_file_with_same_updated_at_as_completed(
        self, worker, mock_config_service, sample_course_config
    ):
        """poll_course() skips a file whose updated_at matches a completed ProcessedFile record.

        Verifies the deduplication contract: when is_file_new_or_updated() returns False
        (same updated_at + status=completed), the file is not queued and _queue_file_for_processing
        is never called.
        """
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = False

        file_already_done = {
            "id": 42,
            "display_name": "lecture-notes.pdf",
            "url": "https://canvas.example.com/files/42/download",
            "updated_at": "2024-06-15T10:30:00Z",
        }

        worker._queue_file_for_processing = AsyncMock(return_value="should-not-be-called")

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[file_already_done])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 0
        mock_config_service.is_file_new_or_updated.assert_awaited_once_with("101", "42", "2024-06-15T10:30:00Z")
        worker._queue_file_for_processing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queues_file_with_newer_updated_at(self, worker, mock_config_service, sample_course_config):
        """poll_course() queues a file whose updated_at is newer than the stored record.

        When is_file_new_or_updated() returns True (Canvas updated_at > stored value),
        the file is passed to _queue_file_for_processing.
        """
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = True

        updated_file = {
            "id": 42,
            "display_name": "lecture-notes-v2.pdf",
            "url": "https://canvas.example.com/files/42/download",
            "updated_at": "2024-06-16T10:30:00Z",
        }

        worker._queue_file_for_processing = AsyncMock(return_value="job-123")

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[updated_file])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 1
        mock_config_service.is_file_new_or_updated.assert_awaited_once_with("101", "42", "2024-06-16T10:30:00Z")
        worker._queue_file_for_processing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_new_and_already_processed_files(self, worker, mock_config_service, sample_course_config):
        """poll_course() processes new files and skips already-processed ones in the same batch.

        Given a mix of files where some have already been processed (same updated_at,
        completed status) and some are new, only new files are queued.
        """
        mock_config_service.get_config.return_value = sample_course_config

        already_done = {
            "id": 10,
            "display_name": "old.pdf",
            "url": "https://canvas.example.com/files/10/download",
            "updated_at": "2024-06-01T08:00:00Z",
        }
        brand_new = {
            "id": 20,
            "display_name": "new.pdf",
            "url": "https://canvas.example.com/files/20/download",
            "updated_at": "2024-06-15T10:00:00Z",
        }
        also_done = {
            "id": 30,
            "display_name": "also-old.pdf",
            "url": "https://canvas.example.com/files/30/download",
            "updated_at": "2024-05-20T12:00:00Z",
        }

        # is_file_new_or_updated returns False for already-done, True for new
        mock_config_service.is_file_new_or_updated.side_effect = [False, True, False]

        worker._queue_file_for_processing = AsyncMock(return_value="job-new")

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[already_done, brand_new, also_done])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 1
        assert mock_config_service.is_file_new_or_updated.await_count == 3
        # Only the new file should have been queued
        worker._queue_file_for_processing.assert_awaited_once()
        queued_file = worker._queue_file_for_processing.call_args[0][1]
        assert queued_file["id"] == 20

    @pytest.mark.asyncio
    async def test_skips_files_without_id(self, worker, mock_config_service, sample_course_config):
        """Files missing id field are skipped."""
        mock_config_service.get_config.return_value = sample_course_config

        bad_file = {"display_name": "no-id.pdf", "updated_at": "2024-06-15T10:30:00Z"}

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[bad_file])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert result == 0
        mock_config_service.is_file_new_or_updated.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_after_single_file_queue_failure(
        self,
        worker,
        mock_config_service,
        sample_course_config,
    ):
        """Failure queuing one file doesn't stop processing others."""
        mock_config_service.get_config.return_value = sample_course_config
        mock_config_service.is_file_new_or_updated.return_value = True

        file1 = {
            "id": 1,
            "display_name": "a.pdf",
            "url": "https://x.com/1",
            "updated_at": "2024-06-15T10:00:00Z",
        }
        file2 = {
            "id": 2,
            "display_name": "b.pdf",
            "url": "https://x.com/2",
            "updated_at": "2024-06-15T11:00:00Z",
        }

        call_count = 0

        async def mock_queue_file(course_id, canvas_file, client):
            nonlocal call_count
            call_count += 1
            if str(canvas_file["id"]) == "1":
                raise CanvasAPIError("Download failed")
            return "job-id-2"

        worker._queue_file_for_processing = mock_queue_file

        with patch("src.workers.canvas_file_worker.CanvasAPIClient") as mock_client_cls:
            mock_client_instance = AsyncMock()
            mock_client_cls.return_value = mock_client_instance
            mock_client_instance.list_course_files = AsyncMock(return_value=[file1, file2])
            mock_client_instance.close = AsyncMock()

            result = await worker.poll_course("101")

        assert call_count == 2
        assert result == 1  # Only file2 succeeded


# ===========================================================================
# _queue_file_for_processing tests
# ===========================================================================


class TestQueueFileForProcessing:
    """Tests for _queue_file_for_processing()."""

    @pytest.fixture
    def mock_canvas_client(self):
        """Mock CanvasAPIClient for download tests."""
        mock = AsyncMock()
        mock.api_token = "test-token"
        mock._is_docker_url = False
        mock._docker_host_header = "localhost"

        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"%PDF-1.4 " + b"x" * 200
        mock_response.raise_for_status = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock._get_client = MagicMock(return_value=mock_http)

        return mock

    @pytest.mark.asyncio
    async def test_submits_document_via_converter(
        self,
        worker,
        mock_converter,
        mock_canvas_client,
        sample_canvas_file,
    ):
        """File is submitted via ConverterClient with correct arguments."""
        job_id = await worker._queue_file_for_processing("101", sample_canvas_file, mock_canvas_client)

        assert job_id == "test-job-id"
        mock_converter.submit_document.assert_awaited_once()
        call_kwargs = mock_converter.submit_document.call_args.kwargs
        assert call_kwargs["filename"] == "lecture-notes.pdf"
        assert call_kwargs["source"] == "canvas_auto"
        assert call_kwargs["metadata"] == {"course_id": "101", "canvas_file_id": "42"}
        assert len(call_kwargs["file_content"]) > 100

    @pytest.mark.asyncio
    async def test_stores_processed_file_record(
        self,
        worker,
        mock_config_service,
        mock_converter,
        mock_canvas_client,
        sample_canvas_file,
    ):
        """ProcessedFile record is stored with status 'processing'."""
        await worker._queue_file_for_processing("101", sample_canvas_file, mock_canvas_client)

        mock_config_service.set_processed_file.assert_awaited_once()
        call_args = mock_config_service.set_processed_file.call_args
        assert call_args[0][0] == "101"  # course_id
        assert call_args[0][1] == "42"  # file_id

        record = call_args[0][2]
        assert isinstance(record, ProcessedFile)
        assert record.canvas_file_id == "42"
        assert record.canvas_updated_at == "2024-06-15T10:30:00Z"
        assert record.status == "processing"
        assert record.original_filename == "lecture-notes.pdf"
        assert record.job_id == "test-job-id"

    @pytest.mark.asyncio
    async def test_raises_on_empty_download_url(
        self,
        worker,
        mock_canvas_client,
    ):
        """Raises CanvasAPIError when file has no download URL."""
        file_no_url = {
            "id": 99,
            "display_name": "no-url.pdf",
            "url": "",
            "updated_at": "2024-06-15T10:00:00Z",
        }

        with pytest.raises(CanvasAPIError, match="no download URL"):
            await worker._queue_file_for_processing("101", file_no_url, mock_canvas_client)

    @pytest.mark.asyncio
    async def test_raises_on_too_small_download(
        self,
        worker,
        sample_canvas_file,
    ):
        """Raises CanvasAPIError when downloaded file is too small."""
        mock_client = AsyncMock()
        mock_client.api_token = "test-token"
        mock_client._is_docker_url = False

        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"tiny"  # < 100 bytes
        mock_response.raise_for_status = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_client._get_client = MagicMock(return_value=mock_http)

        with pytest.raises(CanvasAPIError, match="too small"):
            await worker._queue_file_for_processing("101", sample_canvas_file, mock_client)

    @pytest.mark.asyncio
    async def test_metadata_includes_course_and_file_ids(
        self,
        worker,
        mock_converter,
        mock_canvas_client,
        sample_canvas_file,
    ):
        """Submit call includes course_id and canvas_file_id in metadata.

        Validates the complete metadata contract per PRD-06 R4: the job hash
        must contain all four fields so downstream consumers can trace the file
        back to its Canvas origin.
        """
        await worker._queue_file_for_processing("505", sample_canvas_file, mock_canvas_client)

        call_kwargs = mock_converter.submit_document.call_args.kwargs
        assert call_kwargs["filename"] == "lecture-notes.pdf"
        assert call_kwargs["source"] == "canvas_auto"
        assert call_kwargs["metadata"] == {"course_id": "505", "canvas_file_id": "42"}

    @pytest.mark.asyncio
    async def test_uses_display_name_over_filename(
        self,
        worker,
        mock_converter,
        mock_canvas_client,
    ):
        """Prefers display_name for original_filename, falls back to filename."""
        canvas_file = {
            "id": 99,
            "display_name": "Syllabus (Final).pdf",
            "filename": "upload_12345.pdf",
            "url": "https://canvas.example.com/files/99/download",
            "updated_at": "2024-06-15T10:30:00Z",
        }

        await worker._queue_file_for_processing("101", canvas_file, mock_canvas_client)

        call_kwargs = mock_converter.submit_document.call_args.kwargs
        assert call_kwargs["filename"] == "Syllabus (Final).pdf"

    @pytest.mark.asyncio
    async def test_falls_back_to_filename_when_no_display_name(
        self,
        worker,
        mock_converter,
        mock_canvas_client,
    ):
        """Falls back to filename field when display_name is absent."""
        canvas_file = {
            "id": 99,
            "filename": "upload_12345.pdf",
            "url": "https://canvas.example.com/files/99/download",
            "updated_at": "2024-06-15T10:30:00Z",
        }

        await worker._queue_file_for_processing("101", canvas_file, mock_canvas_client)

        call_kwargs = mock_converter.submit_document.call_args.kwargs
        assert call_kwargs["filename"] == "upload_12345.pdf"

    @pytest.mark.asyncio
    async def test_defaults_to_document_pdf_when_no_name_fields(
        self,
        worker,
        mock_converter,
        mock_canvas_client,
    ):
        """Defaults to 'document.pdf' when both display_name and filename are missing."""
        canvas_file = {
            "id": 99,
            "url": "https://canvas.example.com/files/99/download",
            "updated_at": "2024-06-15T10:30:00Z",
        }

        await worker._queue_file_for_processing("101", canvas_file, mock_canvas_client)

        call_kwargs = mock_converter.submit_document.call_args.kwargs
        assert call_kwargs["filename"] == "document.pdf"


# ===========================================================================
# Worker stop test
# ===========================================================================


class TestStop:
    """Tests for stop()."""

    def test_stop_sets_running_false(self, worker):
        """stop() sets running flag to False."""
        worker.running = True
        worker.stop()
        assert not worker.running


# ===========================================================================
# start_canvas_file_worker tests
# ===========================================================================


class TestStartCanvasFileWorker:
    """Tests for the start_canvas_file_worker entry point."""

    @pytest.mark.asyncio
    async def test_initializes_and_starts_worker(self):
        """start_canvas_file_worker creates services and starts the worker."""
        from src.workers.canvas_file_worker import start_canvas_file_worker

        shutdown_event = asyncio.Event()
        shutdown_event.set()  # Immediately shut down

        mock_redis = AsyncMock()
        mock_s3 = MagicMock()

        async def mock_redis_gen():
            yield mock_redis

        async def mock_s3_gen():
            yield mock_s3

        with (
            patch("src.dependencies.get_redis_client", mock_redis_gen),
            patch("src.dependencies.get_s3_client", mock_s3_gen),
            patch.object(CanvasFileWorker, "run", new_callable=AsyncMock) as mock_run,
        ):
            await start_canvas_file_worker(shutdown_event)

        mock_run.assert_awaited_once_with(shutdown_event)
