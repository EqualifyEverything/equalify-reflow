"""Unit tests for ConverterClient facade."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.converter_client import ConverterClient, FigureInfo, SubmitResult

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_job_service():
    """Mock job service with common async operations."""
    mock = MagicMock()
    mock.create_job = AsyncMock()
    mock.get_job = AsyncMock()
    mock.update_job_status = AsyncMock()
    mock.redis = AsyncMock()
    mock.status_prefix = "job:"
    return mock


@pytest.fixture
def mock_storage_service():
    """Mock storage service with S3 client."""
    mock = MagicMock()
    mock.s3_client = MagicMock()
    mock.temp_bucket = "test-temp-bucket"
    mock.results_bucket = "test-results-bucket"
    mock.download_file = AsyncMock()
    return mock


@pytest.fixture
def mock_queue_service():
    """Mock queue service."""
    mock = MagicMock()
    mock.queue_pii_job = AsyncMock()
    return mock


@pytest.fixture
def converter(mock_job_service, mock_storage_service, mock_queue_service):
    """Create ConverterClient with mocked services."""
    return ConverterClient(
        job_service=mock_job_service,
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
    )


class TestSubmitDocument:
    """Tests for submit_document method."""

    @pytest.mark.asyncio
    async def test_submit_with_pii_scan(self, converter, mock_job_service, mock_storage_service, mock_queue_service):
        """Test submitting a document with PII scanning (default flow)."""
        result = await converter.submit_document(
            file_content=b"fake-pdf-bytes",
            filename="test.pdf",
            source="canvas_auto",
        )

        assert isinstance(result, SubmitResult)
        assert result.status == "pii_scanning"
        assert result.s3_key.startswith("temp/")
        assert result.s3_key.endswith(".pdf")

        # Verify S3 upload
        mock_storage_service.s3_client.put_object.assert_called_once()
        call_kwargs = mock_storage_service.s3_client.put_object.call_args
        assert call_kwargs.kwargs["Bucket"] == "test-temp-bucket"
        assert call_kwargs.kwargs["ContentType"] == "application/pdf"

        # Verify job creation without PII skip
        mock_job_service.create_job.assert_called_once()
        create_kwargs = mock_job_service.create_job.call_args.kwargs
        assert create_kwargs["status"] == "pii_scanning"
        assert create_kwargs["original_filename"] == "test.pdf"
        assert "pii_skipped" not in create_kwargs

        # Verify PII queue
        mock_queue_service.queue_pii_job.assert_called_once()

        # Verify metadata stored
        mock_job_service.redis.hset.assert_called_once()
        hset_kwargs = mock_job_service.redis.hset.call_args.kwargs
        assert hset_kwargs["mapping"]["source"] == "canvas_auto"

    @pytest.mark.asyncio
    async def test_submit_skip_pii(self, converter, mock_job_service, mock_queue_service):
        """Test submitting a document with PII scan skipped."""
        result = await converter.submit_document(
            file_content=b"fake-pdf-bytes",
            filename="test.pdf",
            source="lti",
            skip_pii_scan=True,
            skip_reason="LTI authenticated user",
        )

        assert result.status == "processing"

        # Verify job creation with PII skip
        create_kwargs = mock_job_service.create_job.call_args.kwargs
        assert create_kwargs["status"] == "processing"
        assert create_kwargs["pii_skipped"] is True
        assert create_kwargs["pii_skip_reason"] == "LTI authenticated user"

        # PII queue should NOT be called
        mock_queue_service.queue_pii_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_submit_with_metadata(self, converter, mock_job_service):
        """Test submitting with extra metadata."""
        await converter.submit_document(
            file_content=b"fake-pdf-bytes",
            filename="test.pdf",
            source="canvas_manual",
            metadata={"course_id": "123", "canvas_file_id": "456"},
        )

        hset_kwargs = mock_job_service.redis.hset.call_args.kwargs
        assert hset_kwargs["mapping"]["source"] == "canvas_manual"
        assert hset_kwargs["mapping"]["course_id"] == "123"
        assert hset_kwargs["mapping"]["canvas_file_id"] == "456"

    @pytest.mark.asyncio
    async def test_submit_default_skip_reason(self, converter, mock_job_service):
        """Test default skip reason when none provided."""
        await converter.submit_document(
            file_content=b"fake-pdf-bytes",
            filename="test.pdf",
            source="lti",
            skip_pii_scan=True,
        )

        create_kwargs = mock_job_service.create_job.call_args.kwargs
        assert create_kwargs["pii_skip_reason"] == "PII scan skipped"


class TestGetJobStatus:
    """Tests for get_job_status method."""

    @pytest.mark.asyncio
    async def test_get_existing_job(self, converter, mock_job_service):
        """Test getting status of an existing job."""
        expected = {"job_id": "abc-123", "status": "completed"}
        mock_job_service.get_job.return_value = expected

        result = await converter.get_job_status("abc-123")

        assert result == expected
        mock_job_service.get_job.assert_called_once_with("abc-123")

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, converter, mock_job_service):
        """Test getting status of a nonexistent job."""
        mock_job_service.get_job.return_value = None

        result = await converter.get_job_status("nonexistent")

        assert result is None


class TestRetryJob:
    """Tests for retry_job method."""

    @pytest.mark.asyncio
    async def test_retry_existing_job(self, converter, mock_job_service, mock_queue_service):
        """Test retrying an existing failed job."""
        mock_job_service.get_job.return_value = {
            "job_id": "abc-123",
            "s3_key": "temp/abc-123.pdf",
            "status": "failed",
        }

        result = await converter.retry_job("abc-123")

        assert result is True
        mock_job_service.update_job_status.assert_called_once_with("abc-123", "pii_scanning")
        mock_queue_service.queue_pii_job.assert_called_once_with("abc-123", "temp/abc-123.pdf")

    @pytest.mark.asyncio
    async def test_retry_nonexistent_job(self, converter, mock_job_service):
        """Test retrying a nonexistent job."""
        mock_job_service.get_job.return_value = None

        result = await converter.retry_job("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_retry_job_without_s3_key(self, converter, mock_job_service):
        """Test retrying a job that has no S3 key."""
        mock_job_service.get_job.return_value = {
            "job_id": "abc-123",
            "s3_key": "",
            "status": "failed",
        }

        result = await converter.retry_job("abc-123")

        assert result is False


class TestGetResultMarkdown:
    """Tests for get_result_markdown method."""

    @pytest.mark.asyncio
    async def test_download_markdown(self, converter, mock_storage_service):
        """Test downloading result markdown."""
        mock_storage_service.download_file.return_value = b"# Hello World"

        result = await converter.get_result_markdown("abc-123")

        assert result == b"# Hello World"
        mock_storage_service.download_file.assert_called_once_with("results/abc-123/result.md")

    @pytest.mark.asyncio
    async def test_download_markdown_not_found(self, converter, mock_storage_service):
        """Test downloading markdown when file doesn't exist."""
        mock_storage_service.download_file.side_effect = Exception("NoSuchKey")

        with pytest.raises(Exception, match="NoSuchKey"):
            await converter.get_result_markdown("nonexistent")


class TestListResultFigures:
    """Tests for list_result_figures method."""

    @pytest.mark.asyncio
    async def test_list_figures(self, converter, mock_storage_service):
        """Test listing figures for a job."""
        mock_storage_service.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/abc-123/figures/figure-1.png"},
                {"Key": "results/abc-123/figures/figure-2.jpg"},
            ]
        }

        result = await converter.list_result_figures("abc-123")

        assert len(result) == 2
        assert result[0] == FigureInfo(s3_key="results/abc-123/figures/figure-1.png", filename="figure-1.png")
        assert result[1] == FigureInfo(s3_key="results/abc-123/figures/figure-2.jpg", filename="figure-2.jpg")

    @pytest.mark.asyncio
    async def test_list_figures_empty(self, converter, mock_storage_service):
        """Test listing figures when none exist."""
        mock_storage_service.s3_client.list_objects_v2.return_value = {}

        result = await converter.list_result_figures("abc-123")

        assert result == []

    @pytest.mark.asyncio
    async def test_list_figures_s3_error(self, converter, mock_storage_service):
        """Test listing figures when S3 returns an error."""
        mock_storage_service.s3_client.list_objects_v2.side_effect = Exception("S3 error")

        result = await converter.list_result_figures("abc-123")

        assert result == []


class TestDownloadFigure:
    """Tests for download_figure method."""

    @pytest.mark.asyncio
    async def test_download_figure(self, converter, mock_storage_service):
        """Test downloading a single figure."""
        mock_storage_service.download_file.return_value = b"\x89PNG..."

        result = await converter.download_figure("abc-123", "results/abc-123/figures/fig.png")

        assert result == b"\x89PNG..."
        mock_storage_service.download_file.assert_called_once_with("results/abc-123/figures/fig.png")


class TestCreateDownloadBundle:
    """Tests for create_download_bundle method."""

    @pytest.mark.asyncio
    async def test_create_bundle(self, converter):
        """Test creating a download bundle."""
        with patch("src.canvas.bundle.DownloadBundleService") as mock_bundle_cls:
            mock_bundle = MagicMock()
            mock_bundle.create_bundle = AsyncMock(return_value="https://s3.example.com/bundle.zip")
            mock_bundle_cls.return_value = mock_bundle

            result = await converter.create_download_bundle("abc-123", "document.pdf")

            assert result == "https://s3.example.com/bundle.zip"
            mock_bundle.create_bundle.assert_called_once_with("abc-123", "document.pdf")
