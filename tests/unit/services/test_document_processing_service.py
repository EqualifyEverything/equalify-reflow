"""Unit tests for DocumentProcessingService."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.document_processing_service import DocumentProcessingService

pytestmark = pytest.mark.unit
from src.services.pipeline_viewer_models import FigureData


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return redis


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.download_temp_file = AsyncMock(return_value=b"fake-pdf-bytes")
    storage.upload_file = AsyncMock()
    return storage


@pytest.fixture
def mock_s3_url():
    return AsyncMock()


@pytest.fixture
def service(mock_redis, mock_storage, mock_s3_url):
    return DocumentProcessingService(mock_redis, mock_storage, mock_s3_url)


class TestStoreFiguresFromPipeline:
    """Tests for _store_figures_from_pipeline returning metadata."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_figures(self, service):
        result = await service._store_figures_from_pipeline("job-1", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_stores_figures_and_returns_metadata(self, service, mock_storage):
        figures = [
            FigureData(
                ref_id="figure-1",
                caption="A chart",
                page_number=2,
                image_base64="AAAA",  # valid base64
            ),
            FigureData(
                ref_id="figure-2",
                caption="A table",
                page_number=3,
                image_base64="BBBB",
            ),
        ]

        result = await service._store_figures_from_pipeline("job-42", figures)

        assert len(result) == 2
        assert result[0] == {
            "figure_id": "figure-1",
            "s3_key": "results/job-42/figures/figure-1.png",
            "page_num": 2,
            "alt_text": "",
            "caption": "A chart",
        }
        assert result[1]["figure_id"] == "figure-2"
        assert result[1]["s3_key"] == "results/job-42/figures/figure-2.png"

        # Verify S3 uploads happened
        assert mock_storage.upload_file.call_count == 2

    @pytest.mark.asyncio
    async def test_skips_figures_without_base64(self, service, mock_storage):
        figures = [
            FigureData(
                ref_id="figure-1",
                caption="Has image",
                page_number=1,
                image_base64="AAAA",
            ),
            FigureData(
                ref_id="figure-2",
                caption="No image",
                page_number=2,
                image_base64="",
            ),
        ]

        result = await service._store_figures_from_pipeline("job-1", figures)

        assert len(result) == 1
        assert result[0]["figure_id"] == "figure-1"
        assert mock_storage.upload_file.call_count == 1

    @pytest.mark.asyncio
    async def test_continues_on_upload_failure(self, service, mock_storage):
        mock_storage.upload_file = AsyncMock(
            side_effect=[Exception("S3 error"), None]
        )
        figures = [
            FigureData(
                ref_id="figure-1",
                caption="Fails",
                page_number=1,
                image_base64="AAAA",
            ),
            FigureData(
                ref_id="figure-2",
                caption="Succeeds",
                page_number=2,
                image_base64="BBBB",
            ),
        ]

        result = await service._store_figures_from_pipeline("job-1", figures)

        # Only the successful one is in the metadata
        assert len(result) == 1
        assert result[0]["figure_id"] == "figure-2"


class TestStoredFiguresInJobState:
    """Tests that stored_figures metadata is written to Redis on job completion."""

    @pytest.mark.asyncio
    async def test_stored_figures_passed_to_job_state(self, service, mock_redis, mock_storage):
        """Verify stored_figures list is serialized to Redis as JSON."""
        # Build a minimal pipeline result
        from src.services.pipeline_viewer_models import (
            PipelineViewerResult,
            StepResult,
        )

        fake_result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Test"},
            page_images={},
            page_markdowns={"v0": {"1": "# Test"}},
            figures=[
                FigureData(
                    ref_id="figure-1",
                    caption="Chart",
                    page_number=1,
                    image_base64="AAAA",
                ),
            ],
            steps=[
                StepResult(
                    name="docling",
                    display_name="Docling",
                    version_after="v0",
                    elapsed_ms=100,
                ),
            ],
            stats={},
        )

        with patch.object(
            service, "_store_figures_from_pipeline", new_callable=AsyncMock
        ) as mock_store_figs, patch(
            "src.services.document_processing_service.PipelineViewerService"
        ) as MockPVS:
            # Mock PipelineViewerService.process to return our fake result
            mock_pvs_instance = AsyncMock()
            mock_pvs_instance.process = AsyncMock(return_value=fake_result)
            MockPVS.return_value = mock_pvs_instance

            stored_figs = [
                {
                    "figure_id": "figure-1",
                    "s3_key": "results/job-1/figures/figure-1.png",
                    "page_num": 1,
                    "alt_text": "",
                    "caption": "Chart",
                }
            ]
            mock_store_figs.return_value = stored_figs

            await service.process_document(
                job_id="job-1",
                s3_key="temp/test.pdf",
                filename="test.pdf",
            )

            # Find the final hset call that has stored_figures
            final_call = None
            for call in mock_redis.hset.call_args_list:
                mapping = call.kwargs.get("mapping", {})
                if "stored_figures" in mapping:
                    final_call = mapping
                    break

            assert final_call is not None, "stored_figures not found in any hset call"
            parsed = json.loads(final_call["stored_figures"])
            assert len(parsed) == 1
            assert parsed[0]["figure_id"] == "figure-1"
            assert parsed[0]["s3_key"] == "results/job-1/figures/figure-1.png"
