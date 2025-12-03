"""Unit tests for PDFConverter.

Tests Docling PDF-to-markdown conversion with page image generation.
All Docling dependencies are mocked for fast, isolated unit tests.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest
from src.services.pdf_converter import PDFConversionResult, PDFConverter

pytestmark = pytest.mark.unit


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def mock_pil_image():
    """Mock PIL Image object."""
    mock_image = MagicMock()
    mock_image.save = MagicMock()
    return mock_image


@pytest.fixture
def mock_page_with_image(mock_pil_image):
    """Mock Docling page with image."""
    mock_page = MagicMock()
    mock_page.image = MagicMock()
    mock_page.image.pil_image = mock_pil_image
    return mock_page


@pytest.fixture
def mock_docling_document():
    """Mock Docling document with pages."""
    doc = MagicMock()
    doc.export_to_markdown = MagicMock(return_value="# Full Document\n\nContent")
    doc.pages = {}
    doc.iterate_items = MagicMock(return_value=[])
    return doc


@pytest.fixture
def mock_docling_converter(mock_docling_document, mock_page_with_image):
    """Mock Docling DocumentConverter."""
    # Create mock conversion result
    mock_result = MagicMock()
    mock_result.document = mock_docling_document

    # Configure document with 1 page
    mock_docling_document.pages = {1: mock_page_with_image}

    # Create mock converter
    mock_converter = MagicMock()
    mock_converter.convert = MagicMock(return_value=mock_result)

    return mock_converter


# ============================================================================
# Initialization Tests (2 tests)
# ============================================================================


def test_pdf_converter_initialization():
    """Test PDFConverter initializes with correct Docling configuration."""
    with patch("src.services.pdf_converter.DocumentConverter") as mock_converter:
        PDFConverter()

        # Should have created Docling converter with pipeline options
        mock_converter.assert_called_once()
        call_kwargs = mock_converter.call_args.kwargs

        # Verify format options configured for PDF
        assert "format_options" in call_kwargs
        # Should have PDF format configured
        from docling.datamodel.base_models import InputFormat
        assert InputFormat.PDF in call_kwargs["format_options"]


def test_pdf_converter_pipeline_options():
    """Test PDFConverter configures pipeline with correct options."""
    # This test verifies the converter is created with pipeline options
    # We don't need to mock PdfPipelineOptions, just verify converter exists
    converter = PDFConverter()

    # Converter should be initialized
    assert converter.converter is not None


# ============================================================================
# Success Path Tests (5 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_convert_with_page_images_success(sample_pdf, mock_pil_image):
    """Test successful PDF conversion with page images."""
    converter = PDFConverter()

    # Mock Docling converter
    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Test Document\n\nPage content")
    mock_doc.pages = {}
    mock_doc.iterate_items = MagicMock(return_value=[])

    # Create mock page with image
    mock_page = MagicMock()
    mock_page.image = MagicMock()
    mock_page.image.pil_image = mock_pil_image
    mock_doc.pages = {1: mock_page}

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page 1 content"):
            with patch.object(converter, "_image_to_base64", return_value="base64_image_data"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Verify result structure
                assert isinstance(result, PDFConversionResult)
                assert result.total_pages == 1
                assert result.has_page_images is True
                # mdformat adds trailing newline (MD047)
                assert result.full_markdown == "# Test Document\n\nPage content\n"
                assert len(result.pages) == 1
                assert result.pages[0].page_num == 1
                assert result.pages[0].markdown == "Page 1 content\n"
                assert result.pages[0].image_base64 == "base64_image_data"


@pytest.mark.asyncio
async def test_convert_with_page_images_multiple_pages(sample_pdf, mock_pil_image):
    """Test conversion of multi-page PDF."""
    converter = PDFConverter()

    # Mock document with 3 pages
    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Full Document")
    mock_doc.iterate_items = MagicMock(return_value=[])

    # Create 3 pages with images
    mock_doc.pages = {}
    for i in range(1, 4):
        mock_page = MagicMock()
        mock_page.image = MagicMock()
        mock_page.image.pil_image = mock_pil_image
        mock_doc.pages[i] = mock_page

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", side_effect=["Page 1", "Page 2", "Page 3"]):
            with patch.object(converter, "_image_to_base64", return_value="img"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Should have 3 pages
                assert result.total_pages == 3
                assert len(result.pages) == 3
                assert result.pages[0].page_num == 1
                assert result.pages[1].page_num == 2
                assert result.pages[2].page_num == 3
                assert result.has_page_images is True


@pytest.mark.asyncio
async def test_convert_with_page_images_full_markdown_extraction(sample_pdf, mock_pil_image):
    """Test full markdown is extracted from document."""
    converter = PDFConverter()

    raw_markdown = "# Title\n\n## Section 1\n\nContent here"
    # mdformat adds trailing newline (MD047)
    expected_markdown = raw_markdown + "\n"

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value=raw_markdown)
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {
        1: MagicMock(image=MagicMock(pil_image=mock_pil_image))
    }

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page content"):
            with patch.object(converter, "_image_to_base64", return_value="img"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Full markdown should match
                assert result.full_markdown == expected_markdown


@pytest.mark.asyncio
async def test_convert_with_page_images_has_page_images_flag(sample_pdf, mock_pil_image):
    """Test has_page_images flag is set correctly."""
    converter = PDFConverter()

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Doc")
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {
        1: MagicMock(image=MagicMock(pil_image=mock_pil_image))
    }

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page"):
            with patch.object(converter, "_image_to_base64", return_value="img"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Flag should be True when images present
                assert result.has_page_images is True


@pytest.mark.asyncio
async def test_convert_with_page_images_base64_encoding_format(sample_pdf):
    """Test page images are base64 encoded correctly."""
    converter = PDFConverter()

    # Create a real PIL-like image mock
    mock_pil_image = MagicMock()

    # Mock image.save to write fake PNG data
    def mock_save(buffer, format):
        buffer.write(b"fake_png_data")

    mock_pil_image.save = mock_save

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Doc")
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {
        1: MagicMock(image=MagicMock(pil_image=mock_pil_image))
    }

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page"):
            result = await converter.convert_with_page_images(sample_pdf)

            # Base64 encoding should be valid
            assert result.pages[0].image_base64 != ""
            # Should be decodable
            decoded = base64.b64decode(result.pages[0].image_base64)
            assert decoded == b"fake_png_data"


# ============================================================================
# Page Extraction Tests (3 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_convert_extracts_single_page_correctly(sample_pdf, mock_pil_image):
    """Test single page extraction."""
    converter = PDFConverter()

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Full")
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {1: MagicMock(image=MagicMock(pil_image=mock_pil_image))}

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Single page content") as mock_extract:
            with patch.object(converter, "_image_to_base64", return_value="img"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Should extract page 1 (page_no=1 → page_index=0)
                mock_extract.assert_called_once_with(mock_doc, 0)
                # mdformat adds trailing newline (MD047)
                assert result.pages[0].markdown == "Single page content\n"


@pytest.mark.asyncio
async def test_convert_extracts_multi_page_correctly(sample_pdf, mock_pil_image):
    """Test multi-page extraction."""
    converter = PDFConverter()

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Full")
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {
        1: MagicMock(image=MagicMock(pil_image=mock_pil_image)),
        2: MagicMock(image=MagicMock(pil_image=mock_pil_image)),
        3: MagicMock(image=MagicMock(pil_image=mock_pil_image)),
    }

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", side_effect=["P1", "P2", "P3"]) as mock_extract:
            with patch.object(converter, "_image_to_base64", return_value="img"):
                await converter.convert_with_page_images(sample_pdf)

                # Should extract pages 1, 2, 3 (1-indexed → 0-indexed)
                assert mock_extract.call_count == 3
                assert mock_extract.call_args_list[0][0] == (mock_doc, 0)  # page 1 → index 0
                assert mock_extract.call_args_list[1][0] == (mock_doc, 1)  # page 2 → index 1
                assert mock_extract.call_args_list[2][0] == (mock_doc, 2)  # page 3 → index 2


@pytest.mark.asyncio
async def test_convert_page_numbering_one_indexed(sample_pdf, mock_pil_image):
    """Test page numbers in result are 1-indexed."""
    converter = PDFConverter()

    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Full")
    mock_doc.iterate_items = MagicMock(return_value=[])
    mock_doc.pages = {
        1: MagicMock(image=MagicMock(pil_image=mock_pil_image)),
        2: MagicMock(image=MagicMock(pil_image=mock_pil_image)),
    }

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Content"):
            with patch.object(converter, "_image_to_base64", return_value="img"):
                result = await converter.convert_with_page_images(sample_pdf)

                # Page numbers should be 1-indexed (not 0-indexed)
                assert result.pages[0].page_num == 1
                assert result.pages[1].page_num == 2


# ============================================================================
# Error Handling Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_convert_invalid_pdf_raises_value_error(sample_pdf):
    """Test ValueError raised for invalid PDF."""
    converter = PDFConverter()

    # Mock Docling to raise exception
    with patch.object(converter.converter, "convert", side_effect=Exception("Invalid PDF format")):
        with pytest.raises(ValueError, match="Failed to convert PDF"):
            await converter.convert_with_page_images(sample_pdf)


@pytest.mark.asyncio
async def test_convert_corrupted_pdf_raises_value_error():
    """Test ValueError raised for corrupted PDF."""
    converter = PDFConverter()

    corrupted_pdf = b"corrupted_data"

    with patch.object(converter.converter, "convert", side_effect=Exception("PDF parsing error")):
        with pytest.raises(ValueError, match="Failed to convert PDF"):
            await converter.convert_with_page_images(corrupted_pdf)


@pytest.mark.asyncio
async def test_convert_no_page_images_raises_runtime_error(sample_pdf):
    """Test ValueError raised when no page images generated (RuntimeError wrapped)."""
    converter = PDFConverter()

    # Mock document with page but NO image
    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Doc")
    mock_doc.iterate_items = MagicMock(return_value=[])

    # Page without image
    mock_page = MagicMock()
    mock_page.image = None  # No image!
    mock_doc.pages = {1: mock_page}

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page"):
            # RuntimeError is caught and wrapped in ValueError
            with pytest.raises(ValueError, match="failed to generate page images"):
                await converter.convert_with_page_images(sample_pdf)


@pytest.mark.asyncio
async def test_convert_error_message_context(sample_pdf):
    """Test error messages include context."""
    converter = PDFConverter()

    with patch.object(converter.converter, "convert", side_effect=Exception("Docling internal error")):
        with pytest.raises(ValueError) as exc_info:
            await converter.convert_with_page_images(sample_pdf)

        # Error message should include original error
        assert "Docling internal error" in str(exc_info.value)
        assert "Failed to convert PDF" in str(exc_info.value)


# ============================================================================
# Edge Case Tests (1 test)
# ============================================================================


@pytest.mark.asyncio
async def test_convert_handles_missing_pil_image_attribute(sample_pdf):
    """Test handling when page.image exists but lacks pil_image attribute."""
    converter = PDFConverter()

    # Mock document with page.image but NO pil_image attribute
    mock_doc = MagicMock()
    mock_doc.export_to_markdown = MagicMock(return_value="# Doc")
    mock_doc.iterate_items = MagicMock(return_value=[])

    # Page with image object but no pil_image attribute
    mock_page = MagicMock()
    mock_page.image = MagicMock(spec=[])  # No pil_image attribute
    # Remove pil_image if mock created it
    if hasattr(mock_page.image, "pil_image"):
        del mock_page.image.pil_image
    mock_doc.pages = {1: mock_page}

    mock_result = MagicMock()
    mock_result.document = mock_doc

    with patch.object(converter.converter, "convert", return_value=mock_result):
        with patch.object(converter, "_extract_page_markdown", return_value="Page"):
            # RuntimeError is caught and wrapped in ValueError
            with pytest.raises(ValueError, match="failed to generate page images"):
                await converter.convert_with_page_images(sample_pdf)
