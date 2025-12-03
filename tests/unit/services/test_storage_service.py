"""Unit tests for StorageService (core upload/download operations)."""

import pytest
from io import BytesIO
from fastapi import HTTPException, UploadFile
from botocore.exceptions import ClientError

from src.services.storage_service import StorageService
from src.config import settings
from tests.conftest_fixtures.data_factories import create_test_upload_file


@pytest.fixture
def storage_service(mock_s3_client):
    """Create storage service with mock client."""
    return StorageService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest.fixture
def sample_pdf_upload(mocker):
    """Create a sample PDF upload file using factory."""
    return create_test_upload_file(mocker, filename="test.pdf")


class TestStoreDocument:
    """Tests for store_document method."""

    @pytest.mark.asyncio
    async def test_store_document_success(self, storage_service, mock_s3_client, sample_pdf_upload):
        """Test successful PDF upload."""
        # Configure mock
        mock_s3_client.upload_fileobj.return_value = None

        # Execute
        job_id, s3_key = await storage_service.store_document(sample_pdf_upload)

        # Verify
        assert job_id is not None
        assert s3_key.startswith("temp/")
        assert s3_key.endswith(".pdf")
        mock_s3_client.upload_fileobj.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_document_invalid_type(self, storage_service, sample_pdf_upload):
        """Test rejection of non-PDF files."""
        sample_pdf_upload.content_type = "text/plain"

        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(sample_pdf_upload)

        assert exc.value.status_code == 400
        assert "PDF files" in exc.value.detail

    @pytest.mark.asyncio
    async def test_store_document_too_large(self, storage_service, mock_s3_client, mocker):
        """Test rejection of oversized files."""
        # Create large file
        large_content = b"x" * (settings.max_upload_size + 1)
        file = BytesIO(large_content)

        upload_file = mocker.Mock(spec=UploadFile)
        upload_file.filename = "large.pdf"
        upload_file.file = file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(upload_file)

        assert exc.value.status_code == 413
        assert "exceeds maximum" in exc.value.detail

    @pytest.mark.asyncio
    async def test_store_document_upload_failure(self, storage_service, mock_s3_client, sample_pdf_upload):
        """Test handling of S3 upload failure."""
        mock_s3_client.upload_fileobj.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(sample_pdf_upload)

        assert exc.value.status_code == 500
        assert "Failed to upload" in exc.value.detail


class TestDownloadTempFile:
    """Tests for download_temp_file method."""

    @pytest.mark.asyncio
    async def test_download_success(self, storage_service, mock_s3_client):
        """Test successful file download."""
        expected_content = b"PDF content"
        mock_response = {"Body": BytesIO(expected_content)}
        mock_s3_client.get_object.return_value = mock_response

        content = await storage_service.download_temp_file("temp/job123/file.pdf")

        assert content == expected_content
        mock_s3_client.get_object.assert_called_once_with(
            Bucket=settings.s3_temp_bucket,
            Key="temp/job123/file.pdf"
        )

    @pytest.mark.asyncio
    async def test_download_file_not_found(self, storage_service, mock_s3_client):
        """Test download of non-existent file."""
        error_response = {"Error": {"Code": "NoSuchKey"}}
        mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

        with pytest.raises(HTTPException) as exc:
            await storage_service.download_temp_file("temp/missing.pdf")

        assert exc.value.status_code == 404
        assert "not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_download_unexpected_error(self, storage_service, mock_s3_client):
        """Test handling of unexpected download errors."""
        mock_s3_client.get_object.side_effect = Exception("Network error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.download_temp_file("temp/file.pdf")

        assert exc.value.status_code == 500


class TestUploadResult:
    """Tests for upload_result method."""

    @pytest.mark.asyncio
    async def test_upload_markdown_result(self, storage_service, mock_s3_client):
        """Test uploading Markdown result returns S3 key."""
        mock_s3_client.put_object.return_value = None

        s3_key = await storage_service.upload_result(
            job_id="job123",
            content="# Accessible content",
            format="md"
        )

        # Should return S3 key, not URL
        assert s3_key == "job123.md"
        assert "http" not in s3_key  # Verify it's not a URL
        assert "s3.amazonaws.com" not in s3_key  # Verify it's not a URL

        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "text/markdown"
        assert call_kwargs["Bucket"] == settings.s3_results_bucket

    @pytest.mark.asyncio
    async def test_upload_result_with_suffix(self, storage_service, mock_s3_client):
        """Test uploading result with suffix returns correct S3 key."""
        mock_s3_client.put_object.return_value = None

        s3_key = await storage_service.upload_result(
            job_id="job456",
            content="# Original markdown",
            format="md",
            suffix="original"
        )

        # Should return S3 key with suffix
        assert s3_key == "job456-original.md"
        assert "http" not in s3_key

    @pytest.mark.asyncio
    async def test_upload_unsupported_format(self, storage_service, mock_s3_client):
        """Test uploading with unsupported format defaults to text/plain."""
        mock_s3_client.put_object.return_value = None

        s3_key = await storage_service.upload_result(
            job_id="job456",
            content="# Markdown content",
            format="txt"
        )

        assert s3_key == "job456.txt"
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "text/plain"

    @pytest.mark.asyncio
    async def test_upload_result_with_cache_control(self, storage_service, mock_s3_client):
        """Test that cache control header is set."""
        mock_s3_client.put_object.return_value = None

        await storage_service.upload_result("job789", "content", "md")

        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert "CacheControl" in call_kwargs
        assert "max-age" in call_kwargs["CacheControl"]

    @pytest.mark.asyncio
    async def test_upload_result_failure(self, storage_service, mock_s3_client):
        """Test handling of upload failure."""
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.upload_result("job123", "content", "md")

        assert exc.value.status_code == 500
        assert "Failed to upload result" in exc.value.detail


class TestUploadImage:
    """Tests for upload_image method."""

    @pytest.mark.asyncio
    async def test_upload_image_returns_key(self, storage_service, mock_s3_client):
        """Test uploading generic image returns S3 key."""
        mock_s3_client.put_object.return_value = None

        s3_key = await storage_service.upload_image(
            job_id="job123",
            image_data=b"PNG image data",
            image_name="figure-1.png"
        )

        # Should return S3 key, not URL
        assert s3_key == "job123/images/figure-1.png"
        assert "http" not in s3_key
        assert "s3.amazonaws.com" not in s3_key

        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "image/png"
        assert call_kwargs["Bucket"] == settings.s3_results_bucket
        assert call_kwargs["Key"] == "job123/images/figure-1.png"

    @pytest.mark.asyncio
    async def test_upload_image_failure(self, storage_service, mock_s3_client):
        """Test handling of image upload failure."""
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.upload_image(
                job_id="job456",
                image_data=b"PNG",
                image_name="table-1.png"
            )

        assert exc.value.status_code == 500
        assert "Failed to upload image" in exc.value.detail


class TestUploadPageImage:
    """Tests for upload_page_image method."""

    @pytest.mark.asyncio
    async def test_upload_page_image_returns_key(self, storage_service, mock_s3_client):
        """Test uploading page image returns S3 key."""
        mock_s3_client.put_object.return_value = None

        s3_key = await storage_service.upload_page_image(
            job_id="job123",
            page_num=1,
            image_data=b"PNG image data"
        )

        # Should return S3 key, not URL
        assert s3_key == "job123/pages/page-1.png"
        assert "http" not in s3_key  # Verify it's not a URL
        assert "s3.amazonaws.com" not in s3_key  # Verify it's not a URL

        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "image/png"
        assert call_kwargs["Bucket"] == settings.s3_temp_bucket
        assert call_kwargs["Key"] == "job123/pages/page-1.png"

    @pytest.mark.asyncio
    async def test_upload_page_image_multiple_pages(self, storage_service, mock_s3_client):
        """Test uploading multiple page images returns correct keys."""
        mock_s3_client.put_object.return_value = None

        # Upload page 1
        s3_key_1 = await storage_service.upload_page_image(
            job_id="job456",
            page_num=1,
            image_data=b"Page 1 PNG"
        )
        assert s3_key_1 == "job456/pages/page-1.png"

        # Upload page 10
        s3_key_10 = await storage_service.upload_page_image(
            job_id="job456",
            page_num=10,
            image_data=b"Page 10 PNG"
        )
        assert s3_key_10 == "job456/pages/page-10.png"

    @pytest.mark.asyncio
    async def test_upload_page_image_with_cache_control(self, storage_service, mock_s3_client):
        """Test that cache control header is set for page images."""
        mock_s3_client.put_object.return_value = None

        await storage_service.upload_page_image(
            job_id="job789",
            page_num=5,
            image_data=b"PNG data"
        )

        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert "CacheControl" in call_kwargs
        assert "604800" in call_kwargs["CacheControl"]  # 7 days

    @pytest.mark.asyncio
    async def test_upload_page_image_failure(self, storage_service, mock_s3_client):
        """Test handling of page image upload failure."""
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.upload_page_image(
                job_id="job999",
                page_num=1,
                image_data=b"PNG"
            )

        assert exc.value.status_code == 500
        assert "Failed to upload page 1 image" in exc.value.detail


class TestFileExists:
    """Tests for file_exists method."""

    @pytest.mark.asyncio
    async def test_file_exists_true(self, storage_service, mock_s3_client):
        """Test checking existing file."""
        mock_s3_client.head_object.return_value = {"ContentLength": 1024}

        exists = await storage_service.file_exists("bucket", "key")

        assert exists is True
        mock_s3_client.head_object.assert_called_once_with(Bucket="bucket", Key="key")

    @pytest.mark.asyncio
    async def test_file_exists_false(self, storage_service, mock_s3_client):
        """Test checking non-existent file."""
        error_response = {"Error": {"Code": "404"}}
        mock_s3_client.head_object.side_effect = ClientError(error_response, "HeadObject")

        exists = await storage_service.file_exists("bucket", "key")

        assert exists is False

    @pytest.mark.asyncio
    async def test_file_exists_error(self, storage_service, mock_s3_client):
        """Test handling of unexpected errors."""
        mock_s3_client.head_object.side_effect = Exception("Network error")

        exists = await storage_service.file_exists("bucket", "key")

        assert exists is False  # Should default to False on error


class TestCheckS3Access:
    """Tests for check_s3_access method."""

    @pytest.mark.asyncio
    async def test_s3_accessible(self, storage_service, mock_s3_client):
        """Test S3 accessibility check when healthy."""
        mock_s3_client.head_bucket.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

        accessible = await storage_service.check_s3_access()

        assert accessible is True
        mock_s3_client.head_bucket.assert_called_once_with(Bucket=settings.s3_temp_bucket)

    @pytest.mark.asyncio
    async def test_s3_not_accessible(self, storage_service, mock_s3_client):
        """Test S3 accessibility check when unhealthy."""
        mock_s3_client.head_bucket.side_effect = Exception("Connection refused")

        accessible = await storage_service.check_s3_access()

        assert accessible is False
