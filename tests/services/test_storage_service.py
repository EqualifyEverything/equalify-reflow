"""Integration tests for Storage Service."""

import pytest
from io import BytesIO
from fastapi import HTTPException, UploadFile
from botocore.exceptions import ClientError

from src.services.storage_service import StorageService
from src.config import settings


@pytest.fixture
def mock_s3_client(mocker):
    """Create mock S3 client."""
    client = mocker.MagicMock()
    return client


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
    """Create a sample PDF upload file."""
    pdf_content = b"%PDF-1.4\n%Test PDF content\n%%EOF"
    file = BytesIO(pdf_content)

    # Create UploadFile with proper content_type
    upload_file = mocker.Mock(spec=UploadFile)
    upload_file.filename = "test.pdf"
    upload_file.file = file
    upload_file.content_type = "application/pdf"

    return upload_file


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
    async def test_upload_html_result(self, storage_service, mock_s3_client):
        """Test uploading HTML result."""
        mock_s3_client.put_object.return_value = None

        url = await storage_service.upload_result(
            job_id="job123",
            content="<html>Accessible content</html>",
            format="html"
        )

        assert "job123.html" in url
        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "text/html"
        assert call_kwargs["Bucket"] == settings.s3_results_bucket

    @pytest.mark.asyncio
    async def test_upload_mdx_result(self, storage_service, mock_s3_client):
        """Test uploading MDX result."""
        mock_s3_client.put_object.return_value = None

        url = await storage_service.upload_result(
            job_id="job456",
            content="# Markdown content",
            format="mdx"
        )

        assert "job456.mdx" in url
        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_upload_result_with_cache_control(self, storage_service, mock_s3_client):
        """Test that cache control header is set."""
        mock_s3_client.put_object.return_value = None

        await storage_service.upload_result("job789", "content", "html")

        call_kwargs = mock_s3_client.put_object.call_args.kwargs
        assert "CacheControl" in call_kwargs
        assert "max-age" in call_kwargs["CacheControl"]

    @pytest.mark.asyncio
    async def test_upload_result_failure(self, storage_service, mock_s3_client):
        """Test handling of upload failure."""
        mock_s3_client.put_object.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.upload_result("job123", "content", "html")

        assert exc.value.status_code == 500
        assert "Failed to upload result" in exc.value.detail


class TestDeleteTempFile:
    """Tests for delete_temp_file method."""

    @pytest.mark.asyncio
    async def test_delete_success(self, storage_service, mock_s3_client):
        """Test successful file deletion."""
        mock_s3_client.delete_object.return_value = None

        await storage_service.delete_temp_file("temp/job123/file.pdf")

        mock_s3_client.delete_object.assert_called_once_with(
            Bucket=settings.s3_temp_bucket,
            Key="temp/job123/file.pdf"
        )

    @pytest.mark.asyncio
    async def test_delete_failure(self, storage_service, mock_s3_client):
        """Test handling of deletion failure."""
        mock_s3_client.delete_object.side_effect = Exception("S3 error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.delete_temp_file("temp/file.pdf")

        assert exc.value.status_code == 500


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


class TestGetPresignedUrl:
    """Tests for get_presigned_url method."""

    @pytest.mark.asyncio
    async def test_generate_presigned_url(self, storage_service, mock_s3_client):
        """Test presigned URL generation."""
        expected_url = "https://s3.amazonaws.com/bucket/key?signature=..."
        mock_s3_client.generate_presigned_url.return_value = expected_url

        url = await storage_service.get_presigned_url("bucket", "key", expiration=3600)

        assert url == expected_url
        mock_s3_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={"Bucket": "bucket", "Key": "key"},
            ExpiresIn=3600
        )

    @pytest.mark.asyncio
    async def test_presigned_url_default_expiration(self, storage_service, mock_s3_client):
        """Test default expiration time."""
        mock_s3_client.generate_presigned_url.return_value = "https://url"

        await storage_service.get_presigned_url("bucket", "key")

        call_kwargs = mock_s3_client.generate_presigned_url.call_args.kwargs
        assert call_kwargs["ExpiresIn"] == 3600  # Default 1 hour

    @pytest.mark.asyncio
    async def test_presigned_url_failure(self, storage_service, mock_s3_client):
        """Test handling of URL generation failure."""
        mock_s3_client.generate_presigned_url.side_effect = Exception("Error")

        with pytest.raises(HTTPException) as exc:
            await storage_service.get_presigned_url("bucket", "key")

        assert exc.value.status_code == 500
        assert "Failed to generate presigned URL" in exc.value.detail


class TestGetResultUrl:
    """Tests for get_result_url method."""

    @pytest.mark.asyncio
    async def test_get_result_url(self, storage_service):
        """Test result URL generation."""
        url = storage_service.get_result_url("job123", "html")

        assert "job123.html" in url
        assert settings.s3_results_bucket in url

    @pytest.mark.asyncio
    async def test_get_result_url_mdx(self, storage_service):
        """Test MDX result URL generation."""
        url = storage_service.get_result_url("job456", "mdx")

        assert "job456.mdx" in url


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
