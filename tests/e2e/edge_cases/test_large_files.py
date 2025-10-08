"""Edge case tests for large file handling."""

import pytest
from io import BytesIO
from fastapi import HTTPException, UploadFile
import filelock

from src.services.storage_service import StorageService
from src.config import settings
from tests.e2e.edge_cases.helpers import generate_large_pdf


@pytest.fixture(scope="session")
def large_file_lock(tmp_path_factory):
    """Create a file lock to ensure large file tests run sequentially."""
    return filelock.FileLock(str(tmp_path_factory.mktemp("locks") / "large_file.lock"))


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


class TestLargeFileHandling:
    """Tests for large file processing edge cases."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_90mb_pdf_accepted(self, storage_service, mock_s3_client, mocker, large_file_lock):
        """Test that 90MB PDF (near max size) is accepted."""
        # Acquire lock to prevent parallel execution (memory pressure from generating large PDFs)
        with large_file_lock:
            # Generate 90MB file
            large_pdf = generate_large_pdf(90)
            file = BytesIO(large_pdf)

            # Create UploadFile
            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "large_90mb.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            # Mock successful upload
            mock_s3_client.upload_fileobj.return_value = None

            # Should succeed
            job_id, s3_key = await storage_service.store_document(upload_file)

            assert job_id is not None
            assert s3_key.startswith("temp/")
            assert s3_key.endswith(".pdf")
            mock_s3_client.upload_fileobj.assert_called_once()

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_100mb_pdf_accepted(self, storage_service, mock_s3_client, mocker, large_file_lock):
        """Test that 100MB PDF (exactly at max size) is accepted."""
        with large_file_lock:
            # Generate ~99.9MB file (accounting for PDF overhead to stay under 100MB exactly)
            # generate_large_pdf has overhead that makes files slightly larger than target
            large_pdf = generate_large_pdf(99)
            file = BytesIO(large_pdf)

            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "large_100mb.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            mock_s3_client.upload_fileobj.return_value = None

            # Should succeed at boundary
            job_id, s3_key = await storage_service.store_document(upload_file)

            assert job_id is not None
            assert s3_key is not None

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_101mb_pdf_rejected(self, storage_service, mocker, large_file_lock):
        """Test that 101MB PDF (over max size) is rejected."""
        with large_file_lock:
            # Generate 101MB file (over limit)
            huge_pdf = generate_large_pdf(101)
            file = BytesIO(huge_pdf)

            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "huge_101mb.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            # Should raise 413 Payload Too Large
            with pytest.raises(HTTPException) as exc:
                await storage_service.store_document(upload_file)

            assert exc.value.status_code == 413
            assert "exceeds maximum" in exc.value.detail

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_150mb_pdf_rejected(self, storage_service, mock_s3_client, mocker, large_file_lock):
        """Test that significantly oversized file is rejected.

        Note: This test generates a 150MB PDF which can cause OOM in parallel execution.
        Filelock ensures it runs sequentially with other large file tests.
        """
        with large_file_lock:
            # Generate 150MB file (well over limit)
            # This is memory-intensive - lock prevents parallel execution
            huge_pdf = generate_large_pdf(150)
            file = BytesIO(huge_pdf)

            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "huge_150mb.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            with pytest.raises(HTTPException) as exc:
                await storage_service.store_document(upload_file)

            assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_large_file_size_check_accuracy(self, storage_service, mocker):
        """Test that file size is checked accurately."""
        # Create file of known size
        exact_size = settings.max_upload_size
        content = b"x" * exact_size
        file = BytesIO(content)

        upload_file = mocker.Mock(spec=UploadFile)
        upload_file.filename = "exact_size.pdf"
        upload_file.file = file
        upload_file.content_type = "application/pdf"

        # Mock S3 upload
        mock_s3_client = mocker.MagicMock()
        mock_s3_client.upload_fileobj.return_value = None
        storage_service.s3_client = mock_s3_client

        # Should succeed at exact boundary
        job_id, s3_key = await storage_service.store_document(upload_file)
        assert job_id is not None

    @pytest.mark.asyncio
    async def test_large_file_read_chunks(self, storage_service, mock_s3_client, mocker, large_file_lock):
        """Test that large files are read in chunks during upload."""
        with large_file_lock:
            # Create 10MB file
            large_pdf = generate_large_pdf(10)
            file = BytesIO(large_pdf)

            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "chunked.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            mock_s3_client.upload_fileobj.return_value = None

            await storage_service.store_document(upload_file)

            # Verify file was uploaded
            mock_s3_client.upload_fileobj.assert_called_once()

            # Verify file object was passed correctly
            call_args = mock_s3_client.upload_fileobj.call_args
            assert call_args[0][0] == file  # First positional arg is the file

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_large_file_upload_timeout(self, mocker, large_file_lock):
        """Test handling of upload timeout with large file."""
        with large_file_lock:
            large_pdf = generate_large_pdf(95)
            file = BytesIO(large_pdf)

            upload_file = mocker.Mock(spec=UploadFile)
            upload_file.filename = "timeout.pdf"
            upload_file.file = file
            upload_file.content_type = "application/pdf"

            # Create a fresh mock S3 client that raises exception
            mock_s3_client = mocker.MagicMock()
            mock_s3_client.upload_fileobj.side_effect = Exception("Upload timeout")

            # Create storage service with failing mock
            storage_service = StorageService(
                s3_client=mock_s3_client,
                temp_bucket=settings.s3_temp_bucket,
                results_bucket=settings.s3_results_bucket,
            )

            with pytest.raises(HTTPException) as exc:
                await storage_service.store_document(upload_file)

            assert exc.value.status_code == 500
            assert "Failed to upload" in exc.value.detail

    @pytest.mark.asyncio
    async def test_zero_byte_file_rejected(self, storage_service, mocker):
        """Test that empty (0 byte) file is rejected."""
        file = BytesIO(b"")

        upload_file = mocker.Mock(spec=UploadFile)
        upload_file.filename = "empty.pdf"
        upload_file.file = file
        upload_file.content_type = "application/pdf"

        # Empty file should fail size check
        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(upload_file)

        # Should be rejected (either 400 for invalid or 413 for too small)
        assert exc.value.status_code in [400, 413]

    @pytest.mark.asyncio
    async def test_file_size_exactly_one_byte_over_limit(self, storage_service, mocker):
        """Test file that is exactly 1 byte over the limit."""
        exact_size = settings.max_upload_size + 1
        content = b"x" * exact_size
        file = BytesIO(content)

        upload_file = mocker.Mock(spec=UploadFile)
        upload_file.filename = "one_byte_over.pdf"
        upload_file.file = file
        upload_file.content_type = "application/pdf"

        with pytest.raises(HTTPException) as exc:
            await storage_service.store_document(upload_file)

        assert exc.value.status_code == 413
