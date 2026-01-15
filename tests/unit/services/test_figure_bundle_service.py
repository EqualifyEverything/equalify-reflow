"""Unit tests for FigureBundleService (ZIP bundle generation for markdown + figures)."""

import io
import zipfile
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from src.services.figure_bundle_service import FigureBundleService


@pytest.fixture
def mock_storage_service():
    """Create storage service with mock S3 client."""
    mock = MagicMock()
    mock.results_bucket = "equalify-pdf-results"
    mock.s3_client = MagicMock()
    return mock


@pytest.fixture
def figure_bundle_service(mock_storage_service):
    """Create FigureBundleService with mock storage."""
    return FigureBundleService(mock_storage_service)


@pytest.fixture
def sample_markdown_content():
    """Sample markdown content for testing."""
    return b"# Test Document\n\nContent here.\n\n![Figure 1](images/figure-1.png)"


@pytest.fixture
def sample_image_bytes():
    """Sample PNG bytes for testing (simple 1x1 pixel PNG)."""
    # Minimal valid PNG data
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"


@pytest.mark.unit
class TestInit:
    """Tests for FigureBundleService initialization."""

    def test_stores_storage_service_correctly(self, mock_storage_service):
        """Test that storage service is stored correctly."""
        service = FigureBundleService(mock_storage_service)

        assert service.storage is mock_storage_service

    def test_can_access_storage_attributes(self, mock_storage_service):
        """Test that storage attributes are accessible."""
        service = FigureBundleService(mock_storage_service)

        assert service.storage.results_bucket == "equalify-pdf-results"
        assert service.storage.s3_client is not None


@pytest.mark.unit
class TestGenerateBundle:
    """Tests for generate_bundle method."""

    @pytest.mark.asyncio
    async def test_success_creates_valid_zip_with_markdown_and_figures(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test successful bundle generation with markdown and figures."""
        # Configure mock to return different content for markdown and image
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"}
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify ZIP is valid and contains expected files
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            assert "document.md" in zf.namelist()
            assert "images/figure-1.png" in zf.namelist()

            # Verify content
            assert zf.read("document.md") == sample_markdown_content
            assert zf.read("images/figure-1.png") == sample_image_bytes

    @pytest.mark.asyncio
    async def test_success_zip_structure_is_correct(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test that ZIP structure has document.md at root and images/ folder."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"},
            {"figure_id": "figure-2", "s3_key": "job123/images/figure-2.png"},
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify structure
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()

            # document.md should be at root (no path prefix)
            assert "document.md" in namelist
            assert not any(name.startswith("document/") for name in namelist)

            # Images should be in images/ folder
            assert "images/figure-1.png" in namelist
            assert "images/figure-2.png" in namelist

    @pytest.mark.asyncio
    async def test_success_handles_multiple_figures_correctly(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test bundle generation with multiple figures."""
        image_data = {
            "job123/images/figure-1.png": b"image1",
            "job123/images/figure-2.png": b"image2",
            "job123/images/figure-3.png": b"image3",
        }

        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(image_data.get(Key, sample_image_bytes))}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"},
            {"figure_id": "figure-2", "s3_key": "job123/images/figure-2.png"},
            {"figure_id": "figure-3", "s3_key": "job123/images/figure-3.png"},
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify all figures are included
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            assert "images/figure-1.png" in zf.namelist()
            assert "images/figure-2.png" in zf.namelist()
            assert "images/figure-3.png" in zf.namelist()

            # Verify each image has correct content
            assert zf.read("images/figure-1.png") == b"image1"
            assert zf.read("images/figure-2.png") == b"image2"
            assert zf.read("images/figure-3.png") == b"image3"

    @pytest.mark.asyncio
    async def test_success_empty_stored_figures_list(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content
    ):
        """Test bundle generation with no figures (just markdown)."""
        mock_storage_service.s3_client.get_object.return_value = {
            "Body": io.BytesIO(sample_markdown_content)
        }

        # Execute with empty figures list
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=[],
        )

        # Verify ZIP contains only markdown
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            assert namelist == ["document.md"]
            assert zf.read("document.md") == sample_markdown_content

    @pytest.mark.asyncio
    async def test_partial_failure_continues_when_figure_download_fails(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test that bundle generation continues when individual figure download fails (best effort)."""
        call_count = 0

        def get_object_side_effect(Bucket, Key):
            nonlocal call_count
            call_count += 1
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            elif "figure-2" in Key:
                # Simulate failure for figure-2
                raise ClientError(
                    {"Error": {"Code": "NoSuchKey", "Message": "Key not found"}},
                    "GetObject"
                )
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"},
            {"figure_id": "figure-2", "s3_key": "job123/images/figure-2.png"},  # This will fail
            {"figure_id": "figure-3", "s3_key": "job123/images/figure-3.png"},
        ]

        # Execute - should not raise despite figure-2 failure
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify ZIP contains markdown and successful figures, but not failed one
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            assert "document.md" in namelist
            assert "images/figure-1.png" in namelist
            assert "images/figure-2.png" not in namelist  # Failed download
            assert "images/figure-3.png" in namelist

    @pytest.mark.asyncio
    async def test_runtime_error_when_markdown_download_fails(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test that RuntimeError is raised when markdown download fails."""
        error_response = {"Error": {"Code": "NoSuchKey", "Message": "Key not found"}}
        mock_storage_service.s3_client.get_object.side_effect = ClientError(
            error_response, "GetObject"
        )

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"}
        ]

        # Execute - should raise RuntimeError
        with pytest.raises(RuntimeError) as exc:
            await figure_bundle_service.generate_bundle(
                job_id="job123",
                markdown_key="results/job123/result.md",
                stored_figures=stored_figures,
            )

        assert "Failed to download markdown" in str(exc.value)

    @pytest.mark.asyncio
    async def test_zip_is_properly_formed_and_readable(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test that generated ZIP can be read back correctly."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"}
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify ZIP is properly formed
        zip_buffer = io.BytesIO(zip_bytes)

        # Should not raise any exception when opening
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            # Test ZIP file integrity
            bad_file = zf.testzip()
            assert bad_file is None, f"ZIP contains corrupted file: {bad_file}"

            # Verify compression
            for info in zf.infolist():
                assert info.compress_type == zipfile.ZIP_DEFLATED

    @pytest.mark.asyncio
    async def test_skips_figures_with_empty_s3_key(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test that figures with empty s3_key are skipped."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"},
            {"figure_id": "figure-2", "s3_key": ""},  # Empty s3_key
            {"figure_id": "figure-3"},  # Missing s3_key
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify only figure-1 is included
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            assert "images/figure-1.png" in namelist
            assert "images/figure-2.png" not in namelist
            assert "images/figure-3.png" not in namelist

    @pytest.mark.asyncio
    async def test_uses_correct_bucket_for_downloads(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test that downloads use the results bucket."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-1", "s3_key": "job123/images/figure-1.png"}
        ]

        # Execute
        await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify all get_object calls used the results bucket
        calls = mock_storage_service.s3_client.get_object.call_args_list
        assert len(calls) == 2  # markdown + 1 figure

        for call in calls:
            assert call.kwargs["Bucket"] == "equalify-pdf-results"


@pytest.mark.unit
class TestDownloadFile:
    """Tests for _download_file method."""

    @pytest.mark.asyncio
    async def test_success_returns_file_bytes(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test successful file download returns bytes."""
        expected_content = b"test file content"
        mock_storage_service.s3_client.get_object.return_value = {
            "Body": io.BytesIO(expected_content)
        }

        # Execute
        result = await figure_bundle_service._download_file(
            s3_key="path/to/file.txt",
            bucket="test-bucket"
        )

        assert result == expected_content
        mock_storage_service.s3_client.get_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="path/to/file.txt"
        )

    @pytest.mark.asyncio
    async def test_runtime_error_when_client_error_occurs(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test RuntimeError when S3 ClientError occurs."""
        error_response = {"Error": {"Code": "NoSuchKey", "Message": "Key not found"}}
        mock_storage_service.s3_client.get_object.side_effect = ClientError(
            error_response, "GetObject"
        )

        # Execute
        with pytest.raises(RuntimeError) as exc:
            await figure_bundle_service._download_file(
                s3_key="missing/file.txt",
                bucket="test-bucket"
            )

        # Verify error code is in message
        assert "NoSuchKey" in str(exc.value)
        assert "test-bucket" in str(exc.value)
        assert "missing/file.txt" in str(exc.value)

    @pytest.mark.asyncio
    async def test_runtime_error_includes_error_code(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test that RuntimeError includes the specific S3 error code."""
        error_response = {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}
        mock_storage_service.s3_client.get_object.side_effect = ClientError(
            error_response, "GetObject"
        )

        with pytest.raises(RuntimeError) as exc:
            await figure_bundle_service._download_file(
                s3_key="restricted/file.txt",
                bucket="secure-bucket"
            )

        assert "AccessDenied" in str(exc.value)

    @pytest.mark.asyncio
    async def test_verify_correct_bucket_is_used(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test that correct bucket parameter is used in S3 call."""
        mock_storage_service.s3_client.get_object.return_value = {
            "Body": io.BytesIO(b"content")
        }

        # Execute with specific bucket
        await figure_bundle_service._download_file(
            s3_key="path/to/file.txt",
            bucket="custom-bucket-name"
        )

        # Verify bucket was passed correctly
        mock_storage_service.s3_client.get_object.assert_called_once_with(
            Bucket="custom-bucket-name",
            Key="path/to/file.txt"
        )

    @pytest.mark.asyncio
    async def test_handles_empty_error_response(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test handling of ClientError with empty error response."""
        error_response = {"Error": {}}  # No Code in response
        mock_storage_service.s3_client.get_object.side_effect = ClientError(
            error_response, "GetObject"
        )

        with pytest.raises(RuntimeError) as exc:
            await figure_bundle_service._download_file(
                s3_key="some/file.txt",
                bucket="test-bucket"
            )

        # Should handle gracefully with "Unknown" as fallback
        assert "Unknown" in str(exc.value)


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_large_markdown_file(
        self, figure_bundle_service, mock_storage_service
    ):
        """Test handling of large markdown file."""
        # Create a large markdown content (1MB)
        large_markdown = b"# Large Document\n" + b"Content line.\n" * 70000

        mock_storage_service.s3_client.get_object.return_value = {
            "Body": io.BytesIO(large_markdown)
        }

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=[],
        )

        # Verify content is intact
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            content = zf.read("document.md")
            assert content == large_markdown

    @pytest.mark.asyncio
    async def test_many_figures(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test handling of many figures (50 images)."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        # Create 50 figures
        stored_figures = [
            {"figure_id": f"figure-{i}", "s3_key": f"job123/images/figure-{i}.png"}
            for i in range(1, 51)
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify all 50 figures plus markdown
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            assert len(namelist) == 51  # 1 markdown + 50 figures
            for i in range(1, 51):
                assert f"images/figure-{i}.png" in namelist

    @pytest.mark.asyncio
    async def test_figure_id_with_special_characters(
        self, figure_bundle_service, mock_storage_service, sample_markdown_content, sample_image_bytes
    ):
        """Test figure IDs with special characters in names."""
        def get_object_side_effect(Bucket, Key):
            if Key.endswith(".md"):
                return {"Body": io.BytesIO(sample_markdown_content)}
            else:
                return {"Body": io.BytesIO(sample_image_bytes)}

        mock_storage_service.s3_client.get_object.side_effect = get_object_side_effect

        stored_figures = [
            {"figure_id": "figure-with-dashes", "s3_key": "job123/images/figure-1.png"},
            {"figure_id": "figure_with_underscores", "s3_key": "job123/images/figure-2.png"},
            {"figure_id": "figure.with.dots", "s3_key": "job123/images/figure-3.png"},
        ]

        # Execute
        zip_bytes = await figure_bundle_service.generate_bundle(
            job_id="job123",
            markdown_key="results/job123/result.md",
            stored_figures=stored_figures,
        )

        # Verify figure IDs are preserved in filename
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            namelist = zf.namelist()
            assert "images/figure-with-dashes.png" in namelist
            assert "images/figure_with_underscores.png" in namelist
            assert "images/figure.with.dots.png" in namelist
