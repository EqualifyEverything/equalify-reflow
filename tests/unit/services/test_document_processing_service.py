"""Unit tests for DocumentProcessingService.

Tests the document processing pipeline orchestration including:
- process_document() main method
- Redis state updates throughout processing
- S3 storage for PDFs, ledger, and markdown
- Error handling paths
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import sys

import pytest
from src.config import settings
from src.services.document_processing_service import DocumentProcessingService


# Module path where document_processing_service imports its dependencies
SERVICE_MODULE = "src.services.document_processing_service"


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    client.hset = mocker.AsyncMock(return_value=1)
    client.expire = mocker.AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_storage_service(mocker):
    """Create mock StorageService."""
    service = mocker.MagicMock()
    service.download_file = mocker.AsyncMock(return_value=b"fake_pdf_content")
    service.upload_file = mocker.AsyncMock(return_value=None)
    return service


@pytest.fixture
def mock_s3_url_service(mocker):
    """Create mock S3URLService."""
    service = mocker.MagicMock()
    service.generate_presigned_url = mocker.AsyncMock(
        side_effect=lambda key: f"https://s3.example.com/{key}?signed=true"
    )
    return service


@pytest.fixture
def document_processing_service(mock_redis_client, mock_storage_service, mock_s3_url_service):
    """Create DocumentProcessingService with mock dependencies."""
    return DocumentProcessingService(
        redis_client=mock_redis_client,
        storage_service=mock_storage_service,
        s3_url_service=mock_s3_url_service,
    )


@pytest.fixture
def mock_processing_result():
    """Create a mock ProcessingResult for testing."""
    from src.agents.models import (
        Ledger,
        LedgerEntry,
        ProcessingResult,
        TaskType,
        VerificationReport,
    )

    ledger = Ledger(document_id="test-job-123")
    ledger.append(
        LedgerEntry(
            entry_id="entry-1",
            job_id="job-1",
            page=1,
            action=TaskType.ALT_TEXT,
            target="fig:1",
            before="![](image.png)",
            after="![A detailed diagram showing system architecture](image.png)",
            reasoning="Added alt text for accessibility",
            confidence=0.95,
            validated=True,
        )
    )

    return ProcessingResult(
        document_id="test-job-123",
        success=True,
        final_markdown="# Test Document\n\nProcessed content",
        ledger=ledger,
        verification=VerificationReport(
            document_id="test-job-123",
            passed=True,
            pages_passed=1,
            pages_failed=0,
        ),
        total_pages=1,
        total_edits=1,
        total_jobs=1,
        total_input_tokens=1000,
        total_output_tokens=500,
        total_cost=0.05,
        total_duration_ms=5000,
    )


class TestDocumentProcessingServiceInit:
    """Tests for service initialization."""

    def test_init_with_dependencies(self, mock_redis_client, mock_storage_service, mock_s3_url_service):
        """Test service initialization with all dependencies."""
        service = DocumentProcessingService(
            redis_client=mock_redis_client,
            storage_service=mock_storage_service,
            s3_url_service=mock_s3_url_service,
        )

        assert service.redis is mock_redis_client
        assert service.storage is mock_storage_service
        assert service.s3_url is mock_s3_url_service
        assert service.status_prefix == settings.job_status_prefix


class TestUpdateJobState:
    """Tests for _update_job_state method."""

    @pytest.mark.asyncio
    async def test_update_job_state_simple(self, document_processing_service, mock_redis_client):
        """Test simple job state update."""
        await document_processing_service._update_job_state(
            "job-123",
            status="processing",
        )

        mock_redis_client.hset.assert_called_once()
        call_args = mock_redis_client.hset.call_args
        key = call_args.args[0]
        mapping = call_args.kwargs["mapping"]

        assert key == f"{settings.job_status_prefix}job-123"
        assert mapping["status"] == "processing"
        assert "updated_at" in mapping

    @pytest.mark.asyncio
    async def test_update_job_state_with_multiple_fields(self, document_processing_service, mock_redis_client):
        """Test job state update with multiple fields."""
        await document_processing_service._update_job_state(
            "job-123",
            status="processing",
            processing_phase="docling",
            review_mode="human",
            jobs_total=5,
            jobs_complete=0,
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert mapping["status"] == "processing"
        assert mapping["processing_phase"] == "docling"
        assert mapping["review_mode"] == "human"
        assert mapping["jobs_total"] == "5"
        assert mapping["jobs_complete"] == "0"

    @pytest.mark.asyncio
    async def test_update_job_state_with_dict_field(self, document_processing_service, mock_redis_client):
        """Test job state update with dict field (JSON serialized)."""
        await document_processing_service._update_job_state(
            "job-123",
            metadata={"key": "value", "nested": {"a": 1}},
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert "metadata" in mapping
        # Dict fields should be JSON serialized
        parsed = json.loads(mapping["metadata"])
        assert parsed == {"key": "value", "nested": {"a": 1}}

    @pytest.mark.asyncio
    async def test_update_job_state_with_list_field(self, document_processing_service, mock_redis_client):
        """Test job state update with list field (JSON serialized)."""
        await document_processing_service._update_job_state(
            "job-123",
            tags=["accessibility", "pdf", "conversion"],
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert "tags" in mapping
        parsed = json.loads(mapping["tags"])
        assert parsed == ["accessibility", "pdf", "conversion"]

    @pytest.mark.asyncio
    async def test_update_job_state_skips_none_values(self, document_processing_service, mock_redis_client):
        """Test that None values are not included in update."""
        await document_processing_service._update_job_state(
            "job-123",
            status="processing",
            error=None,
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert "status" in mapping
        assert "error" not in mapping

    @pytest.mark.asyncio
    async def test_update_job_state_sets_ttl_on_status_change(self, document_processing_service, mock_redis_client):
        """Test that TTL is set when status changes."""
        await document_processing_service._update_job_state(
            "job-123",
            status="completed",
        )

        # Should set expire after status change
        mock_redis_client.expire.assert_called_once()
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[0] == f"{settings.job_status_prefix}job-123"


class TestSetJobTTL:
    """Tests for _set_job_ttl method."""

    @pytest.mark.asyncio
    async def test_set_ttl_for_completed_status(self, document_processing_service, mock_redis_client):
        """Test TTL setting for completed jobs."""
        await document_processing_service._set_job_ttl("job-123", "completed")

        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job-123",
            settings.job_ttl_completed,
        )

    @pytest.mark.asyncio
    async def test_set_ttl_for_failed_status(self, document_processing_service, mock_redis_client):
        """Test TTL setting for failed jobs."""
        await document_processing_service._set_job_ttl("job-123", "failed")

        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job-123",
            settings.job_ttl_failed,
        )

    @pytest.mark.asyncio
    async def test_set_ttl_for_active_status(self, document_processing_service, mock_redis_client):
        """Test TTL setting for active (processing) jobs."""
        await document_processing_service._set_job_ttl("job-123", "processing")

        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job-123",
            settings.job_ttl_active,
        )


class TestStoreLedger:
    """Tests for _store_ledger method."""

    @pytest.mark.asyncio
    async def test_store_ledger_success(self, document_processing_service, mock_storage_service, mock_processing_result):
        """Test successful ledger storage to S3."""
        s3_key = await document_processing_service._store_ledger("job-123", mock_processing_result)

        assert s3_key == "results/job-123/ledger.json"
        mock_storage_service.upload_file.assert_called_once()

        call_kwargs = mock_storage_service.upload_file.call_args.kwargs
        assert call_kwargs["key"] == "results/job-123/ledger.json"
        assert call_kwargs["content_type"] == "application/json"

        # Verify the content is valid JSON
        content = call_kwargs["content"]
        ledger_data = json.loads(content.decode("utf-8"))

        assert ledger_data["document_id"] == "test-job-123"
        assert "entries" in ledger_data
        assert len(ledger_data["entries"]) == 1
        assert ledger_data["entries"][0]["action"] == "alt_text"

    @pytest.mark.asyncio
    async def test_store_ledger_preserves_entry_fields(self, document_processing_service, mock_storage_service, mock_processing_result):
        """Test that all ledger entry fields are preserved."""
        await document_processing_service._store_ledger("job-123", mock_processing_result)

        content = mock_storage_service.upload_file.call_args.kwargs["content"]
        ledger_data = json.loads(content.decode("utf-8"))
        entry = ledger_data["entries"][0]

        assert "entry_id" in entry
        assert "job_id" in entry
        assert "page" in entry
        assert "timestamp" in entry
        assert "action" in entry
        assert "target" in entry
        assert "before" in entry
        assert "after" in entry
        assert "reasoning" in entry
        assert "confidence" in entry
        assert "validated" in entry


class TestStoreMarkdown:
    """Tests for _store_markdown method."""

    @pytest.mark.asyncio
    async def test_store_markdown_success(self, document_processing_service, mock_storage_service):
        """Test successful markdown storage to S3."""
        markdown_content = "# Test Document\n\nThis is test content."

        s3_key = await document_processing_service._store_markdown("job-123", markdown_content)

        assert s3_key == "results/job-123/result.md"
        mock_storage_service.upload_file.assert_called_once()

        call_kwargs = mock_storage_service.upload_file.call_args.kwargs
        assert call_kwargs["key"] == "results/job-123/result.md"
        assert call_kwargs["content"] == markdown_content.encode("utf-8")
        assert call_kwargs["content_type"] == "text/markdown"

    @pytest.mark.asyncio
    async def test_store_markdown_with_unicode(self, document_processing_service, mock_storage_service):
        """Test markdown storage with unicode characters."""
        markdown_content = "# Test Document\n\nSpecial chars: \u2014 \u2018 \u2019 \u201c \u201d"

        await document_processing_service._store_markdown("job-123", markdown_content)

        content = mock_storage_service.upload_file.call_args.kwargs["content"]
        assert content == markdown_content.encode("utf-8")


class TestStoreEventBus:
    """Tests for _store_event_bus method."""

    @pytest.mark.asyncio
    async def test_store_event_bus_registers_bus(self, document_processing_service):
        """Test that event bus is registered globally."""
        from src.agents.events import EventBus

        event_bus = EventBus("job-123")

        with patch("src.agents.events.register_event_bus") as mock_register:
            await document_processing_service._store_event_bus("job-123", event_bus)
            mock_register.assert_called_once_with("job-123", event_bus)


class TestGetLedger:
    """Tests for get_ledger method."""

    @pytest.mark.asyncio
    async def test_get_ledger_success(self, document_processing_service, mock_storage_service):
        """Test successful ledger retrieval from S3."""
        ledger_data = {
            "document_id": "job-123",
            "entries": [{"entry_id": "e1", "action": "alt_text"}],
            "total_edits": 1,
        }
        mock_storage_service.download_file = AsyncMock(
            return_value=json.dumps(ledger_data).encode("utf-8")
        )

        result = await document_processing_service.get_ledger("job-123")

        assert result == ledger_data
        mock_storage_service.download_file.assert_called_once_with("results/job-123/ledger.json")

    @pytest.mark.asyncio
    async def test_get_ledger_not_found(self, document_processing_service, mock_storage_service):
        """Test ledger retrieval when not found."""
        mock_storage_service.download_file = AsyncMock(
            side_effect=Exception("NoSuchKey: The specified key does not exist.")
        )

        result = await document_processing_service.get_ledger("nonexistent-job")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_ledger_handles_invalid_json(self, document_processing_service, mock_storage_service):
        """Test ledger retrieval with invalid JSON content."""
        mock_storage_service.download_file = AsyncMock(
            return_value=b"not valid json {"
        )

        result = await document_processing_service.get_ledger("job-123")

        assert result is None


class TestProcessDocumentErrorHandling:
    """Tests for error handling in process_document method."""

    @pytest.mark.asyncio
    async def test_process_document_download_failure(self, document_processing_service, mock_redis_client, mock_storage_service):
        """Test handling of S3 download failure."""
        mock_storage_service.download_file = AsyncMock(
            side_effect=Exception("S3 connection error")
        )

        with pytest.raises(Exception) as exc_info:
            await document_processing_service.process_document(
                job_id="job-123",
                s3_key="temp/job-123/file.pdf",
                filename="test.pdf",
            )

        assert "S3 connection error" in str(exc_info.value)

        # Verify job was marked as failed
        last_hset_call = mock_redis_client.hset.call_args_list[-1]
        mapping = last_hset_call.kwargs["mapping"]
        assert mapping["status"] == "failed"
        assert "S3 connection error" in mapping["error"]

    @pytest.mark.asyncio
    async def test_process_document_sets_initial_state(self, document_processing_service, mock_redis_client, mock_storage_service):
        """Test that initial processing state is set correctly."""
        # Make download fail early so we can check initial state
        mock_storage_service.download_file = AsyncMock(
            side_effect=Exception("Download failed")
        )

        with pytest.raises(Exception):
            await document_processing_service.process_document(
                job_id="job-123",
                s3_key="temp/job-123/file.pdf",
                filename="test.pdf",
                review_mode="human",
            )

        # Check the first hset call (initial state)
        first_hset_call = mock_redis_client.hset.call_args_list[0]
        mapping = first_hset_call.kwargs["mapping"]

        assert mapping["status"] == "processing"
        assert mapping["processing_phase"] == "docling"
        assert mapping["review_mode"] == "human"
        assert mapping["jobs_total"] == "0"
        assert mapping["jobs_complete"] == "0"


class TestProcessDocumentIntegration:
    """Integration-style tests for the full process_document flow.

    Note: Since process_document() uses late imports inside the function body,
    we need to patch at the source modules (e.g., docling.document_converter)
    rather than the consuming module.
    """

    @pytest.mark.asyncio
    async def test_process_document_success_flow(
        self,
        document_processing_service,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        mock_processing_result,
    ):
        """Test successful document processing flow with all mocks."""
        # Mock the entire pipeline chain - patch source modules for late imports
        with patch("docling.document_converter.DocumentConverter") as mock_converter_class, \
             patch("docling.datamodel.pipeline_options.PdfPipelineOptions"), \
             patch("docling.document_converter.PdfFormatOption"), \
             patch("docling.datamodel.base_models.InputFormat") as mock_input_format, \
             patch("docling.datamodel.document.DocumentStream"), \
             patch("src.agents.events.EventBus") as mock_event_bus_class, \
             patch("src.agents.events.register_event_bus"), \
             patch("src.agents.orchestrator.process_document_v5") as mock_process_v5, \
             patch("src.services.vision_extraction_service.is_scanned_pdf", return_value=False), \
             patch("asyncio.to_thread") as mock_to_thread:

            # Setup InputFormat.PDF enum value
            mock_input_format.PDF = "PDF"

            # Setup mock DocumentConverter
            mock_doc = MagicMock()
            mock_doc.pages = {1: MagicMock()}
            mock_doc.pages[1].image = MagicMock()
            mock_doc.pages[1].image.pil_image = MagicMock()
            mock_doc.pages[1].size = MagicMock(width=612.0)
            mock_doc.export_to_markdown = MagicMock(return_value="# Test Document")

            mock_result = MagicMock()
            mock_result.document = mock_doc

            mock_converter = MagicMock()
            mock_converter.convert = MagicMock(return_value=mock_result)
            mock_converter_class.return_value = mock_converter

            # Make to_thread return the result synchronously for testing
            mock_to_thread.return_value = mock_result

            # Setup EventBus mock
            mock_event_bus = MagicMock()
            mock_event_bus.emit = MagicMock()
            mock_event_bus_class.return_value = mock_event_bus

            # Setup process_document_v5 mock
            mock_process_v5.return_value = (mock_processing_result, mock_event_bus)

            # Mock iterate_items to return empty (no figures/tables)
            mock_doc.iterate_items = MagicMock(return_value=[])

            # Run the processing
            result = await document_processing_service.process_document(
                job_id="test-job-123",
                s3_key="temp/test-job-123/document.pdf",
                filename="document.pdf",
                review_mode="auto",
            )

            # Verify result
            assert result.success is True
            assert result.document_id == "test-job-123"

            # Verify S3 storage calls
            assert mock_storage_service.upload_file.call_count >= 2  # ledger + markdown

            # Verify final job state update
            final_hset_call = mock_redis_client.hset.call_args_list[-1]
            mapping = final_hset_call.kwargs["mapping"]
            assert mapping["status"] == "completed"

    @pytest.mark.asyncio
    async def test_process_document_with_human_review_mode(
        self,
        document_processing_service,
        mock_redis_client,
        mock_storage_service,
        mock_s3_url_service,
        mock_processing_result,
    ):
        """Test document processing with human review mode generates ledger URL."""
        with patch("docling.document_converter.DocumentConverter") as mock_converter_class, \
             patch("docling.datamodel.pipeline_options.PdfPipelineOptions"), \
             patch("docling.document_converter.PdfFormatOption"), \
             patch("docling.datamodel.base_models.InputFormat") as mock_input_format, \
             patch("docling.datamodel.document.DocumentStream"), \
             patch("src.agents.events.EventBus") as mock_event_bus_class, \
             patch("src.agents.events.register_event_bus"), \
             patch("src.agents.orchestrator.process_document_v5") as mock_process_v5, \
             patch("src.services.vision_extraction_service.is_scanned_pdf", return_value=False), \
             patch("asyncio.to_thread") as mock_to_thread:

            # Setup InputFormat.PDF enum value
            mock_input_format.PDF = "PDF"

            # Setup mocks (simplified)
            mock_doc = MagicMock()
            mock_doc.pages = {1: MagicMock()}
            mock_doc.pages[1].image = MagicMock()
            mock_doc.pages[1].image.pil_image = MagicMock()
            mock_doc.pages[1].size = MagicMock(width=612.0)
            mock_doc.export_to_markdown = MagicMock(return_value="# Test")
            mock_doc.iterate_items = MagicMock(return_value=[])

            mock_result = MagicMock()
            mock_result.document = mock_doc
            mock_to_thread.return_value = mock_result

            mock_converter = MagicMock()
            mock_converter.convert = MagicMock(return_value=mock_result)
            mock_converter_class.return_value = mock_converter

            mock_event_bus = MagicMock()
            mock_event_bus_class.return_value = mock_event_bus

            mock_process_v5.return_value = (mock_processing_result, mock_event_bus)

            await document_processing_service.process_document(
                job_id="test-job-123",
                s3_key="temp/test-job-123/document.pdf",
                filename="document.pdf",
                review_mode="human",
            )

            # Verify presigned URLs were generated for both markdown and ledger
            # In human mode, ledger URL should also be generated
            assert mock_s3_url_service.generate_presigned_url.call_count >= 2


class TestProcessDocumentPhaseUpdates:
    """Tests for phase update progression during processing."""

    @pytest.mark.asyncio
    async def test_process_document_updates_phases(
        self,
        document_processing_service,
        mock_redis_client,
        mock_storage_service,
        mock_processing_result,
    ):
        """Test that processing phases are updated in Redis."""
        with patch("docling.document_converter.DocumentConverter") as mock_converter_class, \
             patch("docling.datamodel.pipeline_options.PdfPipelineOptions"), \
             patch("docling.document_converter.PdfFormatOption"), \
             patch("docling.datamodel.base_models.InputFormat") as mock_input_format, \
             patch("docling.datamodel.document.DocumentStream"), \
             patch("src.agents.events.EventBus") as mock_event_bus_class, \
             patch("src.agents.events.register_event_bus"), \
             patch("src.agents.orchestrator.process_document_v5") as mock_process_v5, \
             patch("src.services.vision_extraction_service.is_scanned_pdf", return_value=False), \
             patch("asyncio.to_thread") as mock_to_thread:

            mock_input_format.PDF = "PDF"

            # Setup mocks
            mock_doc = MagicMock()
            mock_doc.pages = {1: MagicMock()}
            mock_doc.pages[1].image = MagicMock()
            mock_doc.pages[1].image.pil_image = MagicMock()
            mock_doc.pages[1].size = MagicMock(width=612.0)
            mock_doc.export_to_markdown = MagicMock(return_value="# Test")
            mock_doc.iterate_items = MagicMock(return_value=[])

            mock_result = MagicMock()
            mock_result.document = mock_doc
            mock_to_thread.return_value = mock_result

            mock_converter = MagicMock()
            mock_converter.convert = MagicMock(return_value=mock_result)
            mock_converter_class.return_value = mock_converter

            mock_event_bus = MagicMock()
            mock_event_bus_class.return_value = mock_event_bus

            mock_process_v5.return_value = (mock_processing_result, mock_event_bus)

            await document_processing_service.process_document(
                job_id="test-job-123",
                s3_key="temp/test-job-123/document.pdf",
                filename="document.pdf",
            )

            # Collect all phase updates from hset calls
            phases_updated = []
            for call in mock_redis_client.hset.call_args_list:
                mapping = call.kwargs.get("mapping", {})
                if "processing_phase" in mapping:
                    phases_updated.append(mapping["processing_phase"])

            # Should have progressed through at least docling and planning phases
            assert "docling" in phases_updated
            assert "planning" in phases_updated
            assert "complete" in phases_updated


class TestProcessDocumentEventEmission:
    """Tests for event emission during processing."""

    @pytest.mark.asyncio
    async def test_process_document_emits_docling_events(
        self,
        document_processing_service,
        mock_storage_service,
        mock_processing_result,
    ):
        """Test that Docling events are emitted."""
        with patch("docling.document_converter.DocumentConverter") as mock_converter_class, \
             patch("docling.datamodel.pipeline_options.PdfPipelineOptions"), \
             patch("docling.document_converter.PdfFormatOption"), \
             patch("docling.datamodel.base_models.InputFormat") as mock_input_format, \
             patch("docling.datamodel.document.DocumentStream"), \
             patch("src.agents.events.EventBus") as mock_event_bus_class, \
             patch("src.agents.events.register_event_bus"), \
             patch("src.agents.orchestrator.process_document_v5") as mock_process_v5, \
             patch("src.services.vision_extraction_service.is_scanned_pdf", return_value=False), \
             patch("asyncio.to_thread") as mock_to_thread:

            mock_input_format.PDF = "PDF"

            # Setup mocks
            mock_doc = MagicMock()
            mock_doc.pages = {1: MagicMock()}
            mock_doc.pages[1].image = MagicMock()
            mock_doc.pages[1].image.pil_image = MagicMock()
            mock_doc.pages[1].size = MagicMock(width=612.0)
            mock_doc.export_to_markdown = MagicMock(return_value="# Test")
            mock_doc.iterate_items = MagicMock(return_value=[])

            mock_result = MagicMock()
            mock_result.document = mock_doc
            mock_to_thread.return_value = mock_result

            mock_converter = MagicMock()
            mock_converter.convert = MagicMock(return_value=mock_result)
            mock_converter_class.return_value = mock_converter

            mock_event_bus = MagicMock()
            mock_event_bus_class.return_value = mock_event_bus

            mock_process_v5.return_value = (mock_processing_result, mock_event_bus)

            await document_processing_service.process_document(
                job_id="test-job-123",
                s3_key="temp/test-job-123/document.pdf",
                filename="document.pdf",
            )

            # Verify events were emitted
            emit_calls = mock_event_bus.emit.call_args_list
            assert len(emit_calls) >= 2  # At least DoclingStarted and DoclingComplete

            # Check event types
            event_types = [str(type(call.args[0]).__name__) for call in emit_calls]
            assert "DoclingStartedEvent" in event_types
            assert "DoclingCompleteEvent" in event_types


class TestProcessDocumentScannedPdfHandling:
    """Tests for scanned PDF detection and vision extraction."""

    @pytest.mark.asyncio
    async def test_process_document_detects_scanned_pdf(
        self,
        document_processing_service,
        mock_redis_client,
        mock_storage_service,
        mock_processing_result,
    ):
        """Test that scanned PDFs trigger vision extraction."""
        with patch("docling.document_converter.DocumentConverter") as mock_converter_class, \
             patch("docling.datamodel.pipeline_options.PdfPipelineOptions"), \
             patch("docling.document_converter.PdfFormatOption"), \
             patch("docling.datamodel.base_models.InputFormat") as mock_input_format, \
             patch("docling.datamodel.document.DocumentStream"), \
             patch("src.agents.events.EventBus") as mock_event_bus_class, \
             patch("src.agents.events.register_event_bus"), \
             patch("src.agents.orchestrator.process_document_v5") as mock_process_v5, \
             patch("src.services.vision_extraction_service.is_scanned_pdf", return_value=True) as mock_is_scanned, \
             patch("src.services.vision_extraction_service.detect_visual_document_type") as mock_detect_visual, \
             patch("src.services.vision_extraction_service.extract_all_pages_vision") as mock_extract_vision, \
             patch("asyncio.to_thread") as mock_to_thread:

            from src.services.vision_extraction_service import VisualDocumentType

            mock_input_format.PDF = "PDF"

            # Setup mocks
            mock_doc = MagicMock()
            mock_doc.pages = {1: MagicMock()}
            mock_doc.pages[1].image = MagicMock()
            mock_doc.pages[1].image.pil_image = MagicMock()
            mock_doc.pages[1].size = MagicMock(width=612.0)
            mock_doc.export_to_markdown = MagicMock(return_value="")  # Empty = scanned
            mock_doc.iterate_items = MagicMock(return_value=[])

            mock_result = MagicMock()
            mock_result.document = mock_doc
            mock_to_thread.return_value = mock_result

            mock_converter = MagicMock()
            mock_converter.convert = MagicMock(return_value=mock_result)
            mock_converter_class.return_value = mock_converter

            mock_event_bus = MagicMock()
            mock_event_bus_class.return_value = mock_event_bus

            # Mock vision extraction results
            mock_vision_stats = MagicMock()
            mock_vision_stats.pages_extracted = 1
            mock_vision_stats.total_tokens_input = 500
            mock_vision_stats.total_tokens_output = 200
            mock_vision_stats.estimated_cost = 0.01
            mock_vision_stats.total_duration_ms = 2000
            mock_extract_vision.return_value = ({1: "# Extracted content"}, mock_vision_stats)

            mock_process_v5.return_value = (mock_processing_result, mock_event_bus)

            await document_processing_service.process_document(
                job_id="test-job-123",
                s3_key="temp/test-job-123/scanned.pdf",
                filename="scanned.pdf",
            )

            # Verify vision extraction was called
            mock_extract_vision.assert_called_once()

            # Verify phase was updated to vision_extraction
            phases_updated = []
            for call in mock_redis_client.hset.call_args_list:
                mapping = call.kwargs.get("mapping", {})
                if "processing_phase" in mapping:
                    phases_updated.append(mapping["processing_phase"])

            assert "vision_extraction" in phases_updated
