"""Tests for jobs-in-processing Redis counter (PRD-1).

Verifies that DocumentProcessingService increments/decrements the
eq-pdf:metrics:jobs_in_processing counter for CloudWatch scaling metrics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.document_processing_service import DocumentProcessingService


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    redis.incr = AsyncMock()
    redis.decr = AsyncMock()
    return redis


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.download_temp_file = AsyncMock(return_value=b"%PDF-test")
    storage.upload_file = AsyncMock()
    return storage


@pytest.fixture
def mock_s3_url():
    return AsyncMock()


@pytest.fixture
def service(mock_redis, mock_storage, mock_s3_url):
    return DocumentProcessingService(
        redis_client=mock_redis,
        storage_service=mock_storage,
        s3_url_service=mock_s3_url,
    )


class TestJobsInProcessingCounter:
    """Tests for the Redis jobs_in_processing counter."""

    @pytest.mark.asyncio
    async def test_incr_on_start(self, service, mock_redis):
        """Redis incr called at start of processing."""
        with patch("src.services.document_processing_service.PipelineViewerService") as mock_pvs:
            mock_instance = mock_pvs.return_value
            mock_result = MagicMock()
            mock_result.versions = {"v0": "# Test"}
            mock_result.steps = []
            mock_result.figures = []
            mock_result.total_pages = 1
            mock_result.warnings = []
            mock_instance.process = AsyncMock(return_value=mock_result)

            await service.process_document(
                job_id="test-123",
                s3_key="temp/test.pdf",
                filename="test.pdf",
            )

        mock_redis.incr.assert_called_once_with("eq-pdf:metrics:jobs_in_processing")

    @pytest.mark.asyncio
    async def test_decr_on_success(self, service, mock_redis):
        """Redis decr called after successful completion."""
        with patch("src.services.document_processing_service.PipelineViewerService") as mock_pvs:
            mock_instance = mock_pvs.return_value
            mock_result = MagicMock()
            mock_result.versions = {"v0": "# Test"}
            mock_result.steps = []
            mock_result.figures = []
            mock_result.total_pages = 1
            mock_result.warnings = []
            mock_instance.process = AsyncMock(return_value=mock_result)

            await service.process_document(
                job_id="test-123",
                s3_key="temp/test.pdf",
                filename="test.pdf",
            )

        mock_redis.decr.assert_called_once_with("eq-pdf:metrics:jobs_in_processing")

    @pytest.mark.asyncio
    async def test_decr_on_failure(self, service, mock_redis, mock_storage):
        """Redis decr called even when processing fails (finally block)."""
        mock_storage.download_temp_file = AsyncMock(side_effect=Exception("S3 error"))

        with pytest.raises(Exception, match="S3 error"):
            await service.process_document(
                job_id="test-123",
                s3_key="temp/test.pdf",
                filename="test.pdf",
            )

        mock_redis.incr.assert_called_once_with("eq-pdf:metrics:jobs_in_processing")
        mock_redis.decr.assert_called_once_with("eq-pdf:metrics:jobs_in_processing")

    @pytest.mark.asyncio
    async def test_incr_failure_does_not_block_processing(self, service, mock_redis):
        """If Redis incr fails, processing continues."""
        mock_redis.incr = AsyncMock(side_effect=Exception("Redis error"))

        with patch("src.services.document_processing_service.PipelineViewerService") as mock_pvs:
            mock_instance = mock_pvs.return_value
            mock_result = MagicMock()
            mock_result.versions = {"v0": "# Test"}
            mock_result.steps = []
            mock_result.figures = []
            mock_result.total_pages = 1
            mock_result.warnings = []
            mock_instance.process = AsyncMock(return_value=mock_result)

            # Should not raise despite Redis incr failure
            result = await service.process_document(
                job_id="test-123",
                s3_key="temp/test.pdf",
                filename="test.pdf",
            )

        assert result.versions == {"v0": "# Test"}
