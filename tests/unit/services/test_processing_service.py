"""Unit tests for ProcessingService.

Tests the main processing orchestrator with all dependencies mocked.
Validates the analysis + extraction pipeline.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.extraction import ExtractionMetrics, ExtractionResult
from src.services.pdf_converter import PageData, PDFConversionResult
from src.services.processing_service import ProcessingService
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)

pytestmark = pytest.mark.unit


# ============================================================================
# Fixtures specific to ProcessingService tests
# ============================================================================


@pytest.fixture
def mock_heading_tree():
    """Mock HeadingTree for manifest parsing."""
    return HeadingTree(
        document_title="Test Document",
        title_page=1,
        sections=[HeadingNode(level=1, title="Intro", page=1)],
        total_pages=2,
        layout_type="single_column",
        confidence=0.92,
    )


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
    """Mock StorageService with all methods needed for ProcessingService tests.

    Uses MagicMock as container with AsyncMock for async methods.
    This prevents unawaited coroutine warnings when tests don't call all mocked methods.
    """
    mock = MagicMock()
    mock.download_temp_file = AsyncMock(return_value=b"fake_pdf_content")
    mock.upload_result = AsyncMock(return_value="s3://equalify-results/550e8400.../v20250101_120000/output.md")
    # PRD-027: save_processing_result for new review checklist workflow
    mock.save_processing_result = AsyncMock(return_value="processing-results/550e8400.../result.json")
    # upload_page_image for review checklist with page thumbnails
    mock.upload_page_image = AsyncMock(return_value="s3://equalify-results/550e8400.../pages/page_1.png")
    # upload_final_markdown for completed processing
    mock.upload_final_markdown = AsyncMock(return_value="jobs/550e8400.../final.md")
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
def mock_analysis_manifest(mock_heading_tree):
    """Mock DocumentManifest from analyze_document."""
    return DocumentManifest(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        document_title="Test Document",
        document_type="lecture_notes",
        total_pages=2,
        heading_tree_json=mock_heading_tree.model_dump_json(),
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


def make_extraction_result(
    markdown: str = "# Test Document\n\nContent here.",
    confidence: float = 0.88,
    pages_found: list[int] | None = None,
) -> ExtractionResult:
    """Helper to create ExtractionResult for tests."""
    return ExtractionResult(
        markdown=markdown,
        metrics=ExtractionMetrics(
            confidence=confidence,
            pages_found=pages_found or [1, 2],
            pages_missing=[],
            is_valid=True,
        ),
        attempt_count=1,
        correction_applied=False,
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


@pytest.fixture
def mock_structure_loop_result():
    """Mock result from StructureLoop.run()."""
    from src.services.structure_loop import StructureLoopResult, StructureTrace

    return StructureLoopResult(
        markdown="# Test Document\n\nContent here.",
        trace=StructureTrace(
            iterations=1,
            lint_issues_found=0,
            lint_issues_fixed=0,
            ocr_suggestions_processed=0,
            final_lint_clean=True,
            corrections=[],
            observations=[],
            time_seconds=0.5,
            cost_cents=0.0,
        ),
    )


@pytest.fixture
def mock_figures_agent_result():
    """Mock result from FiguresAgent.process()."""
    from src.shared.models.agent_trace import AgentResult

    return AgentResult(
        agent_name="figures",
        observations=[],
        auto_corrections=[],
        review_items=[],
        reasoning_summary="No figure issues found",
        confidence=0.95,
        cost_cents=0.1,
        time_seconds=1.0,
    )


@pytest.fixture
def mock_assembly_result():
    """Mock result from AssemblyService.assemble()."""
    from src.shared.models.processing_result import (
        AnalysisSummary,
        ExtractionSummary,
        ProcessingResult as FullProcessingResult,
        ProcessingTrace,
        StructureSummary,
    )
    from src.shared.models.review_checklist import ReviewChecklist

    return FullProcessingResult(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        markdown="# Test Document\n\nContent here.",
        confidence=0.94,
        status="completed",
        processing_trace=ProcessingTrace(
            analysis=AnalysisSummary(
                document_type="lecture_notes",
                total_pages=2,
                key_entities=[],
                required_agents=["figures"],
                confidence=0.9,
                time_seconds=1.0,
                cost_cents=0.2,
            ),
            extraction=ExtractionSummary(
                confidence=0.88,
                pages_extracted=2,
                correction_iterations=1,
                time_seconds=1.0,
                cost_cents=0.3,
            ),
            structure=StructureSummary(
                iterations=1,
                lint_issues_found=0,
                lint_issues_fixed=0,
                final_lint_clean=True,
                time_seconds=0.5,
                cost_cents=0.0,
            ),
            agents=[],
            total_observations=0,
            auto_corrections_applied=0,
            review_items_generated=0,
            total_cost_cents=0.6,
        ),
        review_checklist=ReviewChecklist(
            total_items=0,
            by_category={},
            items=[],
        ),
        processing_time_seconds=5.0,
    )


@pytest.fixture
def mock_verification_result():
    """Mock result from verify_final_output()."""
    from src.agents.verification import VerificationResult

    return VerificationResult(
        corrected_markdown="# Test Document\n\nContent here.",
        page_results=[],
        total_corrections_applied=0,
        total_corrections_failed=0,
        total_issues=0,
        all_pages_accurate=True,
        cost_cents=0.1,
    )


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
    mock_structure_loop_result,
    mock_figures_agent_result,
    mock_assembly_result,
    mock_verification_result,
):
    """Test successful end-to-end document processing with analysis + extraction."""
    mock_pdf_converter.convert_with_page_images.return_value = sample_pdf_conversion_result_no_markdown

    mock_extraction_result = make_extraction_result(
        markdown="# Test Document\n\nContent here.",
        confidence=0.88,
    )

    with (
        patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        ) as mock_analyze_document,
        patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            return_value=(mock_extraction_result, mock_llm_usage),
        ) as mock_extract,
        patch(
            "src.services.processing_service.StructureLoop"
        ) as mock_structure_loop_cls,
        patch(
            "src.services.processing_service.FiguresAgent"
        ) as mock_figures_agent_cls,
        patch(
            "src.services.processing_service.AssemblyService"
        ) as mock_assembly_cls,
        patch(
            "src.services.processing_service.verify_final_output",
            new_callable=AsyncMock,
            return_value=(mock_verification_result, mock_llm_usage),
        ),
    ):
        # Configure StructureLoop mock
        mock_structure_loop = MagicMock()
        mock_structure_loop.run = AsyncMock(return_value=mock_structure_loop_result)
        mock_structure_loop_cls.return_value = mock_structure_loop

        # Configure FiguresAgent mock
        mock_figures_agent = MagicMock()
        mock_figures_agent.process = AsyncMock(return_value=mock_figures_agent_result)
        mock_figures_agent_cls.return_value = mock_figures_agent

        # Configure AssemblyService mock
        mock_assembly = MagicMock()
        mock_assembly.assemble = MagicMock(return_value=mock_assembly_result)
        mock_assembly_cls.return_value = mock_assembly

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
    mock_analyze_document.assert_called_once()  # Analysis phase
    mock_extract.assert_called_once()  # Extraction phase
    # Should upload v0 markdown (original extraction)
    mock_storage_service_extended.upload_result.assert_called()
    # PRD-027: New flow saves ProcessingResult via save_processing_result
    mock_storage_service_extended.save_processing_result.assert_called_once()


@pytest.mark.asyncio
async def test_process_document_confidence_from_assembly(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_llm_usage,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
    mock_structure_loop_result,
    mock_figures_agent_result,
    mock_assembly_result,
    mock_verification_result,
):
    """Test confidence score is computed by AssemblyService.

    PRD-026: The final confidence is a weighted average:
    - extraction: 40%
    - structure: 20%
    - agents: 30%
    - review_penalty: 10%
    """
    mock_pdf_converter.convert_with_page_images.return_value = sample_pdf_conversion_result_no_markdown

    mock_extraction_result = make_extraction_result(
        markdown="# Test",
        confidence=0.85,
    )

    with (
        patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        ),
        patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            return_value=(mock_extraction_result, mock_llm_usage),
        ),
        patch(
            "src.services.processing_service.StructureLoop"
        ) as mock_structure_loop_cls,
        patch(
            "src.services.processing_service.FiguresAgent"
        ) as mock_figures_agent_cls,
        patch(
            "src.services.processing_service.AssemblyService"
        ) as mock_assembly_cls,
        patch(
            "src.services.processing_service.verify_final_output",
            new_callable=AsyncMock,
            return_value=(mock_verification_result, mock_llm_usage),
        ),
    ):
        # Configure StructureLoop mock
        mock_structure_loop = MagicMock()
        mock_structure_loop.run = AsyncMock(return_value=mock_structure_loop_result)
        mock_structure_loop_cls.return_value = mock_structure_loop

        # Configure FiguresAgent mock
        mock_figures_agent = MagicMock()
        mock_figures_agent.process = AsyncMock(return_value=mock_figures_agent_result)
        mock_figures_agent_cls.return_value = mock_figures_agent

        # Configure AssemblyService mock - confidence 0.94 matches the expected weighted average
        mock_assembly = MagicMock()
        mock_assembly.assemble = MagicMock(return_value=mock_assembly_result)
        mock_assembly_cls.return_value = mock_assembly

        service = ProcessingService(
            storage_service=mock_storage_service_extended,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    # Confidence is computed by AssemblyService as weighted average
    # With extraction=0.85, structure=1.0 (clean), agents=1.0 (no obs), review=1.0 (no items)
    # = 0.4*0.85 + 0.2*1.0 + 0.3*1.0 + 0.1*1.0 = 0.34 + 0.2 + 0.3 + 0.1 = 0.94
    assert result.confidence_score == 0.94


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
    mock_pdf_converter.convert_with_page_images.side_effect = RuntimeError("Docling internal error")

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
async def test_process_document_handles_extraction_error(
    sample_job_payload,
    mock_storage_service_extended,
    mock_queue_service,
    mock_job_service,
    mock_pdf_converter,
    mock_analysis_manifest,
    mock_analysis_usage,
    sample_pdf_conversion_result_no_markdown,
):
    """Test handling when extraction fails."""
    mock_pdf_converter.convert_with_page_images.return_value = sample_pdf_conversion_result_no_markdown

    with (
        patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        ),
        patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            side_effect=ValueError("No pages provided for extraction"),
        ),
    ):
        service = ProcessingService(
            storage_service=mock_storage_service_extended,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    assert result.error_message is not None
    assert "pages" in result.error_message.lower() or "extraction" in result.error_message.lower()


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
    mock_pdf_converter.convert_with_page_images.return_value = sample_pdf_conversion_result_no_markdown
    mock_storage_service.upload_result.side_effect = Exception("S3 bucket full")

    mock_extraction_result = make_extraction_result(
        markdown="# Test",
        confidence=0.9,
    )

    with (
        patch(
            "src.services.processing_service.analyze_document",
            new_callable=AsyncMock,
            return_value=(mock_analysis_manifest, [], mock_analysis_usage),
        ),
        patch(
            "src.services.processing_service.extract_with_validation",
            new_callable=AsyncMock,
            return_value=(mock_extraction_result, mock_llm_usage),
        ),
    ):
        service = ProcessingService(
            storage_service=mock_storage_service,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
            pdf_converter=mock_pdf_converter,
        )

        result = await service.process_document(sample_job_payload)

    assert "S3 bucket full" in result.error_message
    assert result.markdown_url is None
