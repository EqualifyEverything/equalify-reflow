"""Unit tests for DownloadBundleService."""

import io
import zipfile
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from src.canvas.bundle import BundleError, DownloadBundleService

pytestmark = pytest.mark.unit


def _make_client_error(code: str = "NoSuchKey") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "test"}},
        operation_name="GetObject",
    )


def _s3_get_object_response(body: bytes) -> dict:
    """Build a mock S3 get_object response."""
    body_stream = MagicMock()
    body_stream.read.return_value = body
    return {"Body": body_stream}


@pytest.fixture
def mock_storage():
    """Create a mock StorageService."""
    storage = MagicMock()
    storage.results_bucket = "equalify-results"
    storage.s3_client = MagicMock()
    # Make upload_file async
    async def upload_file(key, content, content_type="application/octet-stream"):
        return key
    storage.upload_file = upload_file
    return storage


@pytest.fixture
def service(mock_storage):
    """Create a DownloadBundleService for testing."""
    return DownloadBundleService(mock_storage)


class TestInit:
    """DownloadBundleService constructor."""

    def test_stores_storage_service(self, mock_storage):
        svc = DownloadBundleService(mock_storage)
        assert svc.storage is mock_storage


class TestCreateBundle:
    """R1: create_bundle orchestrates download, zip, upload, presigned URL."""

    @pytest.mark.asyncio
    async def test_returns_presigned_url(self, service, mock_storage):
        """create_bundle returns the presigned URL string."""
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Hello"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = (
            "https://s3.example.com/bundle.zip?sig=abc"
        )

        url = await service.create_bundle("job-1", "lecture_notes.pdf")

        assert url == "https://s3.example.com/bundle.zip?sig=abc"

    @pytest.mark.asyncio
    async def test_presigned_url_has_seven_day_expiry(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Hello"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        await service.create_bundle("job-1", "notes.pdf")

        mock_storage.s3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "equalify-results",
                "Key": "results/job-1/bundle.zip",
            },
            ExpiresIn=7 * 24 * 60 * 60,
        )

    @pytest.mark.asyncio
    async def test_downloads_result_md(self, service, mock_storage):
        """create_bundle downloads results/{job_id}/result.md."""
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Test"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        await service.create_bundle("j1", "doc.pdf")

        # First get_object call is for the markdown
        first_call = mock_storage.s3_client.get_object.call_args_list[0]
        assert first_call.kwargs["Bucket"] == "equalify-results"
        assert first_call.kwargs["Key"] == "results/j1/result.md"

    @pytest.mark.asyncio
    async def test_uploads_zip_to_correct_key(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Hello"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        # Track upload_file calls
        upload_calls = []
        async def track_upload(key, content, content_type="application/octet-stream"):
            upload_calls.append({"key": key, "content_type": content_type})
            return key
        mock_storage.upload_file = track_upload

        await service.create_bundle("job-1", "doc.pdf")

        assert len(upload_calls) == 1
        assert upload_calls[0]["key"] == "results/job-1/bundle.zip"
        assert upload_calls[0]["content_type"] == "application/zip"


class TestBundleErrorOnMissingMarkdown:
    """R4: If result.md doesn't exist, raise BundleError."""

    @pytest.mark.asyncio
    async def test_raises_bundle_error_on_no_such_key(self, service, mock_storage):
        mock_storage.s3_client.get_object.side_effect = _make_client_error("NoSuchKey")

        with pytest.raises(BundleError, match="No result markdown found for job job-1"):
            await service.create_bundle("job-1", "doc.pdf")

    @pytest.mark.asyncio
    async def test_raises_bundle_error_on_404(self, service, mock_storage):
        mock_storage.s3_client.get_object.side_effect = _make_client_error("404")

        with pytest.raises(BundleError, match="No result markdown found for job"):
            await service.create_bundle("job-1", "doc.pdf")

    @pytest.mark.asyncio
    async def test_reraises_other_client_errors(self, service, mock_storage):
        mock_storage.s3_client.get_object.side_effect = _make_client_error(
            "InternalError"
        )

        with pytest.raises(ClientError):
            await service.create_bundle("job-1", "doc.pdf")


class TestZipStructure:
    """R2: Zip file structure and naming."""

    @pytest.mark.asyncio
    async def test_zip_named_folder_uses_original_filename_stem(
        self, service, mock_storage
    ):
        """Root folder is {original_filename_without_extension}-reflow."""
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Content"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded_content = {}
        async def capture_upload(key, content, content_type="application/octet-stream"):
            uploaded_content["bytes"] = content
            return key
        mock_storage.upload_file = capture_upload

        await service.create_bundle("j1", "lecture_notes.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded_content["bytes"]))
        names = zf.namelist()
        assert "lecture_notes-reflow/lecture_notes.md" in names

    @pytest.mark.asyncio
    async def test_markdown_at_root_of_folder(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Hello"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "my_doc.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        md_path = "my_doc-reflow/my_doc.md"
        assert md_path in zf.namelist()
        assert zf.read(md_path) == b"# Hello"

    @pytest.mark.asyncio
    async def test_figures_in_images_subfolder(self, service, mock_storage):
        """Figures go in {folder}/images/{filename}."""
        # Return markdown on first call, figure on subsequent calls
        call_count = {"n": 0}
        def get_object_side_effect(**kwargs):
            call_count["n"] += 1
            if kwargs["Key"].endswith("result.md"):
                return _s3_get_object_response(b"# Doc")
            return _s3_get_object_response(b"\x89PNG-data")

        mock_storage.s3_client.get_object.side_effect = get_object_side_effect
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/j1/figures/figure_1.png"},
                {"Key": "results/j1/figures/figure_2.png"},
            ]
        }
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "slides.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        names = zf.namelist()
        assert "slides-reflow/images/figure_1.png" in names
        assert "slides-reflow/images/figure_2.png" in names

    @pytest.mark.asyncio
    async def test_no_images_folder_when_no_figures(self, service, mock_storage):
        """R4: If no figures exist, zip contains only the markdown (no images/ folder)."""
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Solo"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "solo.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        names = zf.namelist()
        assert len(names) == 1
        assert names[0] == "solo-reflow/solo.md"


class TestS3Operations:
    """R3: S3 operations — download, list, upload, presign."""

    @pytest.mark.asyncio
    async def test_lists_figures_with_correct_prefix(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        await service.create_bundle("j42", "f.pdf")

        mock_storage.s3_client.list_objects_v2.assert_called_once_with(
            Bucket="equalify-results",
            Prefix="results/j42/figures/",
        )

    @pytest.mark.asyncio
    async def test_downloads_each_listed_figure(self, service, mock_storage):
        def get_object_side_effect(**kwargs):
            if kwargs["Key"].endswith("result.md"):
                return _s3_get_object_response(b"# md")
            return _s3_get_object_response(b"img-bytes")

        mock_storage.s3_client.get_object.side_effect = get_object_side_effect
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/j1/figures/fig1.png"},
                {"Key": "results/j1/figures/fig2.png"},
            ]
        }
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "doc.pdf")

        # 1 markdown + 2 figures = 3 get_object calls
        assert mock_storage.s3_client.get_object.call_count == 3


class TestZipInMemory:
    """R3/Design: Zip is built in memory using io.BytesIO (no temp files)."""

    @pytest.mark.asyncio
    async def test_uploaded_content_is_valid_zip(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# Valid"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "test.pdf")

        # Should be valid zip
        assert zipfile.is_zipfile(io.BytesIO(uploaded["bytes"]))


class TestBuildZip:
    """Static _build_zip method."""

    def test_build_zip_with_markdown_only(self):
        result = DownloadBundleService._build_zip(
            "my-reflow", "my", b"# Content", []
        )
        zf = zipfile.ZipFile(io.BytesIO(result))
        assert zf.namelist() == ["my-reflow/my.md"]
        assert zf.read("my-reflow/my.md") == b"# Content"

    def test_build_zip_with_figures(self):
        figures = [("fig1.png", b"png1"), ("fig2.png", b"png2")]
        result = DownloadBundleService._build_zip(
            "doc-reflow", "doc", b"# Doc", figures
        )
        zf = zipfile.ZipFile(io.BytesIO(result))
        names = zf.namelist()
        assert "doc-reflow/doc.md" in names
        assert "doc-reflow/images/fig1.png" in names
        assert "doc-reflow/images/fig2.png" in names
        assert zf.read("doc-reflow/images/fig1.png") == b"png1"

    def test_build_zip_uses_deflated_compression(self):
        result = DownloadBundleService._build_zip(
            "a-reflow", "a", b"x" * 1000, []
        )
        zf = zipfile.ZipFile(io.BytesIO(result))
        info = zf.getinfo("a-reflow/a.md")
        assert info.compress_type == zipfile.ZIP_DEFLATED


class TestFigureDownloadErrors:
    """R4: Failed figure downloads are skipped (best-effort)."""

    @pytest.mark.asyncio
    async def test_skips_failed_figure_download(self, service, mock_storage):
        call_count = {"n": 0}
        def get_object_side_effect(**kwargs):
            call_count["n"] += 1
            if kwargs["Key"].endswith("result.md"):
                return _s3_get_object_response(b"# md")
            if "fig_bad" in kwargs["Key"]:
                raise _make_client_error("InternalError")
            return _s3_get_object_response(b"good-img")

        mock_storage.s3_client.get_object.side_effect = get_object_side_effect
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/j1/figures/fig_bad.png"},
                {"Key": "results/j1/figures/fig_good.png"},
            ]
        }
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        url = await service.create_bundle("j1", "doc.pdf")

        # Should still succeed
        assert url == "https://url"
        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        names = zf.namelist()
        assert "doc-reflow/images/fig_good.png" in names
        assert "doc-reflow/images/fig_bad.png" not in names

    @pytest.mark.asyncio
    async def test_list_objects_failure_returns_no_figures(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.side_effect = _make_client_error(
            "InternalError"
        )
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        url = await service.create_bundle("j1", "doc.pdf")

        assert url == "https://url"
        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        assert len(zf.namelist()) == 1  # markdown only


class TestLogging:
    """R4: Bundle size and file count logged at INFO."""

    @pytest.mark.asyncio
    async def test_logs_bundle_info(self, service, mock_storage, caplog):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        with caplog.at_level("INFO", logger="src.canvas.bundle"):
            await service.create_bundle("j1", "doc.pdf")

        assert any("1 files" in msg for msg in caplog.messages)
        assert any("bytes" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_logs_file_count_with_figures(self, service, mock_storage, caplog):
        def get_object_side_effect(**kwargs):
            if kwargs["Key"].endswith("result.md"):
                return _s3_get_object_response(b"# md")
            return _s3_get_object_response(b"img")

        mock_storage.s3_client.get_object.side_effect = get_object_side_effect
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/j1/figures/f1.png"},
                {"Key": "results/j1/figures/f2.png"},
            ]
        }
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        with caplog.at_level("INFO", logger="src.canvas.bundle"):
            await service.create_bundle("j1", "doc.pdf")

        assert any("3 files" in msg for msg in caplog.messages)


class TestFilenameEdgeCases:
    """Edge cases for original_filename handling."""

    @pytest.mark.asyncio
    async def test_filename_without_extension(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "readme")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        assert "readme-reflow/readme.md" in zf.namelist()

    @pytest.mark.asyncio
    async def test_filename_with_multiple_dots(self, service, mock_storage):
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {"Contents": []}
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "my.file.name.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        assert "my.file.name-reflow/my.file.name.md" in zf.namelist()

    @pytest.mark.asyncio
    async def test_empty_figures_prefix_entry_skipped(self, service, mock_storage):
        """list_objects_v2 can return the prefix itself as a key — skip it."""
        mock_storage.s3_client.get_object.return_value = _s3_get_object_response(
            b"# md"
        )
        mock_storage.s3_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "results/j1/figures/"},  # prefix-only entry
            ]
        }
        mock_storage.s3_client.generate_presigned_url.return_value = "https://url"

        uploaded = {}
        async def capture(key, content, content_type="application/octet-stream"):
            uploaded["bytes"] = content
            return key
        mock_storage.upload_file = capture

        await service.create_bundle("j1", "doc.pdf")

        zf = zipfile.ZipFile(io.BytesIO(uploaded["bytes"]))
        assert len(zf.namelist()) == 1  # markdown only


class TestBundleErrorClass:
    """BundleError is a proper exception."""

    def test_bundle_error_is_exception(self):
        assert issubclass(BundleError, Exception)

    def test_bundle_error_message(self):
        err = BundleError("test message")
        assert str(err) == "test message"
