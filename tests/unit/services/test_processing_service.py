"""Unit tests for ProcessingService.

Tests the main processing orchestrator with all dependencies mocked.
Validates the analysis + extraction pipeline (PRD-012).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pdf_converter import PageData, PDFConversionResult
from src.services.processing_service import ProcessingService
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

pytestmark = pytest.mark.unit


# ============================================================================
# Fixtures specific to ProcessingService tests
# ============================================================================


@pytest.fixture
def mock_heading_tree():
    """Mock HeadingTree response from FullDocumentAgent."""
    mock = MagicMock()
    mock.sections = [MagicMock(), MagicMock()]  # 2 sections
    mock.confidence = 0.92
    mock.layout_type = "single_column"
    return mock


@pytest.fixture
def mock_llm_usage():
    """Mock LLMUsage for tracking costs."""
    return LLMUsage(
        input_tokens=1500,
        output_tokens=500,
        total_tokens=2000,
        estimated_cost_cents=0.25,
    )


@pytest.fixture
def mock_storage_service_extended():
    """Mock StorageService with all methods needed for ProcessingService tests."""
    mock = AsyncMock()
    mock.download_temp_file = AsyncMock(return_value=b"fake_pdf_content")
    mock.upload_result = AsyncMock(
        return_value="s3://equalify-results/550e8400.../v20250101_120000/output.md"
    )
    return mock


@pytest.fixture
def sample_pdf_conversion_result_no_markdown():
    """Sample PDF conversion result without markdown (new approach)."""
    return PDFConversionResult(
        pages=[
            PageData(page_num=1, image_base64="dGVzdDE="),  # Valid base64
            PageData(page_num=2, image_base64="dGVzdDI="),  # Valid base64
        ],
        total_pages=2,
        has_page_images=True,
        extracted_images=[],
    )


@pytest.fixture
def mock_analysis_manifest():
    """Mock DocumentManifest from AnalysisAgent."""
    return DocumentManifest(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        document_title="Test Document",
        document_type="lecture_notes",
        total_pages=2,
        heading_tree_json='{"document_title": "Test", "sections": []}',
        page_features=[
            PageFeatures(page_num=1),
            PageFeatures(page_num=2),
        ],
        required_agents=["figures"],
        skip_agents=["tables", "structure", "typography"],
        analysis_confidence=0.9,
        analysis_notes="Test notes",
        analysis_model="us.anthropic.claude-sonnet-4-5-20250514-v1:0",
    )


@pytest.fixture
def mock_analysis_usage():
    """Mock LLMUsage for analysis phase."""
    return LLMUsage(
        input_tokens=1000,
        output_tokens=200,
        total_tokens=1200,
        estimated_cost_cents=0.45,  # Sonnet pricing
    )


# ============================================================================
# Initialization Tests
# ============================================================================


@pytest.mark.asyncio
async def test_processing_service_init_with_default_dependencies(
    mock_storage_service, mock_queue_service, mock_job_service, mocker
):
    """Test ProcessingService creates default dependencies if not provided."""
    mock_pdf = mocker.MagicMock()

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf,
    )

    assert service.pdf_converter is not None
    assert service.storage == mock_storage_service
    assert service.queue == mock_queue_service
    assert service.job == mock_job_service


# ============================================================================
# Success Path Tests
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_happy_path(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_heading_tree,
    mock_llm_usage,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
):
    """Test successful end-to-end document processing with analysis + extraction."""
    mock_pdf_converter.convert_with_page_images.return_value = (
        sample_pdf_conversion_result_no_markdown
    )

    with patch(
        "src.services.processing_service.AnalysisAgent"
    ) as mock_analysis_agent_class, patch(
        "src.services.processing_service.FullDocumentAgent"
    ) as mock_full_document_agent_class:
        # Mock analysis agent
        mock_analysis_agent = MagicMock()
        mock_analysis_agent.analyze = AsyncMock(
            return_value=(mock_analysis_manifest, [], mock_analysis_usage)
        )
        mock_analysis_agent_class.return_value = mock_analysis_agent

        # Mock extraction agent
        mock_extraction_agent = MagicMock()
        mock_extraction_agent.process = AsyncMock(
            return_value=(
                "# Test Document\n\nContent here.",
                mock_heading_tree,
                mock_llm_usage,
            )
        )
        mock_full_document_agent_class.return_value = mock_extraction_agent

        service = ProcessingService(
            storage_service=mock_storage_service_extended,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    # Verify result
    assert result.job_id == sample_job_payload.job_id
    assert result.markdown_url is not None
    assert result.confidence_score is not None
    assert result.processing_time_seconds >= 0
    assert result.error_message is None

    # Verify pipeline steps executed
    mock_storage_service_extended.download_temp_file.assert_called_once()
    mock_pdf_converter.convert_with_page_images.assert_called_once()
    mock_analysis_agent.analyze.assert_called_once()  # Analysis phase
    mock_extraction_agent.process.assert_called_once()  # Extraction phase
    mock_storage_service_extended.upload_result.assert_called()


@pytest.mark.asyncio
async def test_process_document_confidence_from_heading_tree(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_llm_usage,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
):
    """Test confidence score comes from heading tree analysis."""
    mock_pdf_converter.convert_with_page_images.return_value = (
        sample_pdf_conversion_result_no_markdown
    )

    mock_heading_tree = MagicMock()
    mock_heading_tree.sections = [MagicMock()]
    mock_heading_tree.confidence = 0.85
    mock_heading_tree.layout_type = "two_column"

    with patch(
        "src.services.processing_service.AnalysisAgent"
    ) as mock_analysis_agent_class, patch(
        "src.services.processing_service.FullDocumentAgent"
    ) as mock_full_document_agent_class:
        # Mock analysis agent
        mock_analysis_agent = MagicMock()
        mock_analysis_agent.analyze = AsyncMock(
            return_value=(mock_analysis_manifest, [], mock_analysis_usage)
        )
        mock_analysis_agent_class.return_value = mock_analysis_agent

        # Mock extraction agent
        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(
            return_value=("# Test", mock_heading_tree, mock_llm_usage)
        )
        mock_full_document_agent_class.return_value = mock_agent

        service = ProcessingService(
            storage_service=mock_storage_service_extended,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    # Confidence should come from heading tree
    assert result.confidence_score == 0.85


# ============================================================================
# Error Handling Tests
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_handles_generic_exception(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
):
    """Test generic exceptions are caught and return failed result."""
    mock_pdf_converter.convert_with_page_images.side_effect = RuntimeError(
        "Docling internal error"
    )

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
    )

    result = await service.process_document(sample_job_payload)

    # Should return failed result (not raise)
    assert result.job_id == sample_job_payload.job_id
    assert result.markdown_url is None
    assert result.confidence_score is None
    assert result.error_message is not None
    assert "Docling internal error" in result.error_message


@pytest.mark.asyncio
async def test_process_document_missing_page_images_raises_error(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
):
    """Test processing fails if Docling doesn't generate page images."""
    bad_conversion_result = PDFConversionResult(
        pages=[],
        total_pages=1,
        has_page_images=False,
        extracted_images=[],
    )
    mock_pdf_converter.convert_with_page_images.return_value = bad_conversion_result

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
    )

    result = await service.process_document(sample_job_payload)

    assert "page images" in result.error_message.lower()
    assert result.markdown_url is None


@pytest.mark.asyncio
async def test_process_document_handles_document_too_large(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
):
    """Test handling when document exceeds max pages limit."""
    mock_pdf_converter.convert_with_page_images.return_value = (
        sample_pdf_conversion_result_no_markdown
    )

    with patch(
        "src.services.processing_service.AnalysisAgent"
    ) as mock_analysis_agent_class, patch(
        "src.services.processing_service.FullDocumentAgent"
    ) as mock_full_document_agent_class:
        # Mock analysis agent - succeeds
        mock_analysis_agent = MagicMock()
        mock_analysis_agent.analyze = AsyncMock(
            return_value=(mock_analysis_manifest, [], mock_analysis_usage)
        )
        mock_analysis_agent_class.return_value = mock_analysis_agent

        # Mock extraction agent - fails with too large
        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(
            side_effect=ValueError("Document has 20 pages, exceeding the maximum")
        )
        mock_full_document_agent_class.return_value = mock_agent

        service = ProcessingService(
            storage_service=mock_storage_service_extended,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    assert result.error_message is not None
    assert "exceeding" in result.error_message.lower() or "pages" in result.error_message.lower()


# ============================================================================
# Edge Case Tests
# ============================================================================


@pytest.mark.asyncio
async def test_process_document_s3_download_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
):
    """Test handling of S3 download failures."""
    mock_storage_service.download_temp_file.side_effect = Exception("S3 AccessDenied")

    service = ProcessingService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service,
        pdf_converter=mock_pdf_converter,
    )

    result = await service.process_document(sample_job_payload)

    assert "S3 AccessDenied" in result.error_message
    assert result.markdown_url is None


@pytest.mark.asyncio
async def test_process_document_s3_upload_failure(
    sample_job_payload,
    mock_storage_service,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_heading_tree,
    mock_llm_usage,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
):
    """Test handling of S3 upload failures."""
    mock_pdf_converter.convert_with_page_images.return_value = (
        sample_pdf_conversion_result_no_markdown
    )
    mock_storage_service.upload_result.side_effect = Exception("S3 bucket full")

    with patch(
        "src.services.processing_service.AnalysisAgent"
    ) as mock_analysis_agent_class, patch(
        "src.services.processing_service.FullDocumentAgent"
    ) as mock_full_document_agent_class:
        # Mock analysis agent
        mock_analysis_agent = MagicMock()
        mock_analysis_agent.analyze = AsyncMock(
            return_value=(mock_analysis_manifest, [], mock_analysis_usage)
        )
        mock_analysis_agent_class.return_value = mock_analysis_agent

        # Mock extraction agent
        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(
            return_value=("# Test", mock_heading_tree, mock_llm_usage)
        )
        mock_full_document_agent_class.return_value = mock_agent

        service = ProcessingService(
            storage_service=mock_storage_service,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    assert "S3 bucket full" in result.error_message
    assert result.markdown_url is None
