"""Unit tests for PDF text extraction service using Docling.

Tests the pdf_extractor module which provides PDF text extraction
capabilities for PII scanning and document processing.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pdf_extractor import (
    PDFExtractionError,
    _convert_pdf_sync,
    extract_pdf_text,
    is_text_sufficient_for_pii_scan,
)

pytestmark = [pytest.mark.unit]


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_docling_result(mocker):
    """Create mock Docling conversion result with extracted markdown."""
    mock_doc = mocker.MagicMock()
    mock_doc.export_to_markdown.return_value = "# Title\n\nThis is extracted content from the PDF."
    mock_result = mocker.MagicMock()
    mock_result.document = mock_doc
    return mock_result


@pytest.fixture
def mock_converter(mocker, mock_docling_result):
    """Mock DocumentConverter to avoid real PDF processing."""
    mock = mocker.patch("src.services.pdf_extractor.DocumentConverter")
    mock.return_value.convert.return_value = mock_docling_result
    return mock


@pytest.fixture
def sample_pdf_bytes():
    """Sample PDF content for testing."""
    return b"%PDF-1.4\nfake pdf content for testing purposes"


# ============================================================================
# Tests for extract_pdf_text()
# ============================================================================


class TestExtractPdfText:
    """Tests for the extract_pdf_text async function."""

    @pytest.mark.asyncio
    async def test_returns_extracted_text_on_success(
        self, mock_converter, mock_docling_result, sample_pdf_bytes
    ):
        """Test successful PDF text extraction returns markdown content."""
        result = await extract_pdf_text(sample_pdf_bytes)

        assert result == "# Title\n\nThis is extracted content from the PDF."
        mock_docling_result.document.export_to_markdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_error_when_text_too_short(self, mocker, sample_pdf_bytes):
        """Test PDFExtractionError when extracted text is less than 10 characters."""
        mock_doc = mocker.MagicMock()
        mock_doc.export_to_markdown.return_value = "Short"  # Only 5 chars
        mock_result = mocker.MagicMock()
        mock_result.document = mock_doc

        mocker.patch("src.services.pdf_extractor.DocumentConverter").return_value.convert.return_value = mock_result

        with pytest.raises(PDFExtractionError) as exc:
            await extract_pdf_text(sample_pdf_bytes)

        assert "insufficient text" in str(exc.value)
        assert "empty" in str(exc.value) or "corrupted" in str(exc.value)

    @pytest.mark.asyncio
    async def test_raises_error_when_text_empty(self, mocker, sample_pdf_bytes):
        """Test PDFExtractionError when extracted text is empty."""
        mock_doc = mocker.MagicMock()
        mock_doc.export_to_markdown.return_value = ""
        mock_result = mocker.MagicMock()
        mock_result.document = mock_doc

        mocker.patch("src.services.pdf_extractor.DocumentConverter").return_value.convert.return_value = mock_result

        with pytest.raises(PDFExtractionError) as exc:
            await extract_pdf_text(sample_pdf_bytes)

        assert "insufficient text" in str(exc.value)

    @pytest.mark.asyncio
    async def test_raises_error_when_text_whitespace_only(self, mocker, sample_pdf_bytes):
        """Test PDFExtractionError when extracted text is only whitespace."""
        mock_doc = mocker.MagicMock()
        mock_doc.export_to_markdown.return_value = "    \n\t\n   "  # Whitespace only
        mock_result = mocker.MagicMock()
        mock_result.document = mock_doc

        mocker.patch("src.services.pdf_extractor.DocumentConverter").return_value.convert.return_value = mock_result

        with pytest.raises(PDFExtractionError) as exc:
            await extract_pdf_text(sample_pdf_bytes)

        assert "insufficient text" in str(exc.value)

    @pytest.mark.asyncio
    async def test_raises_error_when_docling_fails(self, mocker, sample_pdf_bytes):
        """Test PDFExtractionError when Docling raises an exception."""
        mocker.patch(
            "src.services.pdf_extractor.DocumentConverter"
        ).return_value.convert.side_effect = RuntimeError("Docling processing failed")

        with pytest.raises(PDFExtractionError) as exc:
            await extract_pdf_text(sample_pdf_bytes)

        assert "Unable to extract text from PDF" in str(exc.value)
        assert "Docling processing failed" in str(exc.value)

    @pytest.mark.asyncio
    async def test_temp_file_created_and_cleaned_up(
        self, mock_converter, mock_docling_result, sample_pdf_bytes
    ):
        """Test that temporary file is created with PDF content and cleaned up after."""
        with patch("src.services.pdf_extractor.tempfile") as mock_tempfile, \
             patch("src.services.pdf_extractor.os") as mock_os:
            # Setup mock temp file
            mock_tmp_file = MagicMock()
            mock_tmp_file.__enter__ = MagicMock(return_value=mock_tmp_file)
            mock_tmp_file.__exit__ = MagicMock(return_value=False)
            mock_tmp_file.name = "/tmp/test123.pdf"
            mock_tempfile.NamedTemporaryFile.return_value = mock_tmp_file

            # Mock os.path.exists to return True (file exists)
            mock_os.path.exists.return_value = True

            await extract_pdf_text(sample_pdf_bytes)

            # Verify temp file was created with correct suffix
            mock_tempfile.NamedTemporaryFile.assert_called_once_with(
                suffix=".pdf", delete=False
            )

            # Verify PDF content was written
            mock_tmp_file.write.assert_called_once_with(sample_pdf_bytes)

            # Verify cleanup happened
            mock_os.path.exists.assert_called_with("/tmp/test123.pdf")
            mock_os.unlink.assert_called_once_with("/tmp/test123.pdf")

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_up_on_error(self, mocker, sample_pdf_bytes):
        """Test that temporary file is cleaned up even when extraction fails."""
        mocker.patch(
            "src.services.pdf_extractor.DocumentConverter"
        ).return_value.convert.side_effect = RuntimeError("Processing failed")

        with patch("src.services.pdf_extractor.tempfile") as mock_tempfile, \
             patch("src.services.pdf_extractor.os") as mock_os:
            mock_tmp_file = MagicMock()
            mock_tmp_file.__enter__ = MagicMock(return_value=mock_tmp_file)
            mock_tmp_file.__exit__ = MagicMock(return_value=False)
            mock_tmp_file.name = "/tmp/error_test.pdf"
            mock_tempfile.NamedTemporaryFile.return_value = mock_tmp_file
            mock_os.path.exists.return_value = True

            with pytest.raises(PDFExtractionError):
                await extract_pdf_text(sample_pdf_bytes)

            # Verify cleanup still happened despite error
            mock_os.unlink.assert_called_once_with("/tmp/error_test.pdf")

    @pytest.mark.asyncio
    async def test_uses_asyncio_to_thread_for_nonblocking(
        self, mock_converter, mock_docling_result, sample_pdf_bytes
    ):
        """Test that asyncio.to_thread is used to avoid blocking the event loop."""
        with patch("src.services.pdf_extractor.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.return_value = mock_docling_result

            await extract_pdf_text(sample_pdf_bytes)

            # Verify asyncio.to_thread was called with _convert_pdf_sync
            mock_to_thread.assert_called_once()
            call_args = mock_to_thread.call_args
            assert call_args[0][0] == _convert_pdf_sync

    @pytest.mark.asyncio
    async def test_handles_none_text_from_export(self, mocker, sample_pdf_bytes):
        """Test PDFExtractionError when export_to_markdown returns None."""
        mock_doc = mocker.MagicMock()
        mock_doc.export_to_markdown.return_value = None
        mock_result = mocker.MagicMock()
        mock_result.document = mock_doc

        mocker.patch("src.services.pdf_extractor.DocumentConverter").return_value.convert.return_value = mock_result

        with pytest.raises(PDFExtractionError) as exc:
            await extract_pdf_text(sample_pdf_bytes)

        assert "Unable to extract text from PDF" in str(exc.value)


# ============================================================================
# Tests for _convert_pdf_sync()
# ============================================================================


class TestConvertPdfSync:
    """Tests for the synchronous PDF conversion helper function."""

    def test_calls_document_converter_with_path(self, mock_converter):
        """Test that DocumentConverter.convert() is called with the file path."""
        test_path = "/tmp/test_document.pdf"

        _convert_pdf_sync(test_path)

        mock_converter.assert_called_once()
        mock_converter.return_value.convert.assert_called_once_with(test_path)

    def test_returns_conversion_result(self, mock_converter, mock_docling_result):
        """Test that the conversion result is returned unchanged."""
        test_path = "/tmp/another_document.pdf"

        result = _convert_pdf_sync(test_path)

        assert result == mock_docling_result

    def test_propagates_converter_exception(self, mocker):
        """Test that exceptions from DocumentConverter are propagated."""
        mocker.patch(
            "src.services.pdf_extractor.DocumentConverter"
        ).return_value.convert.side_effect = ValueError("Invalid PDF format")

        with pytest.raises(ValueError) as exc:
            _convert_pdf_sync("/tmp/invalid.pdf")

        assert "Invalid PDF format" in str(exc.value)

    def test_creates_new_converter_instance(self, mock_converter):
        """Test that a new DocumentConverter instance is created each call."""
        _convert_pdf_sync("/tmp/doc1.pdf")
        _convert_pdf_sync("/tmp/doc2.pdf")

        assert mock_converter.call_count == 2


# ============================================================================
# Tests for is_text_sufficient_for_pii_scan()
# ============================================================================


class TestIsTextSufficientForPiiScan:
    """Tests for the text sufficiency check function."""

    def test_returns_true_when_text_equals_min_length(self):
        """Test returns True when text length equals min_length."""
        text = "A" * 50  # Exactly 50 characters
        assert is_text_sufficient_for_pii_scan(text) is True

    def test_returns_true_when_text_exceeds_min_length(self):
        """Test returns True when text length exceeds min_length."""
        text = "A" * 100  # 100 characters
        assert is_text_sufficient_for_pii_scan(text) is True

    def test_returns_false_when_text_below_min_length(self):
        """Test returns False when text length is below min_length."""
        text = "A" * 49  # 49 characters, below default 50
        assert is_text_sufficient_for_pii_scan(text) is False

    def test_returns_false_for_empty_string(self):
        """Test returns False for empty string."""
        assert is_text_sufficient_for_pii_scan("") is False

    def test_returns_false_for_whitespace_only_string(self):
        """Test returns False when string is only whitespace."""
        # 100 spaces, but stripped length is 0
        whitespace_text = "   " * 50
        assert is_text_sufficient_for_pii_scan(whitespace_text) is False

    def test_uses_default_min_length_of_50(self):
        """Test that default min_length is 50 characters."""
        text_49 = "A" * 49
        text_50 = "A" * 50

        assert is_text_sufficient_for_pii_scan(text_49) is False
        assert is_text_sufficient_for_pii_scan(text_50) is True

    def test_respects_custom_min_length_parameter(self):
        """Test that custom min_length parameter is respected."""
        text = "Short text"  # 10 characters

        # With default (50), should be insufficient
        assert is_text_sufficient_for_pii_scan(text) is False

        # With custom min_length of 10, should be sufficient
        assert is_text_sufficient_for_pii_scan(text, min_length=10) is True

        # With custom min_length of 5, should be sufficient
        assert is_text_sufficient_for_pii_scan(text, min_length=5) is True

        # With custom min_length of 15, should be insufficient
        assert is_text_sufficient_for_pii_scan(text, min_length=15) is False

    def test_strips_whitespace_before_checking_length(self):
        """Test that whitespace is stripped before length comparison."""
        # 40 actual characters + 20 spaces = 60 total, but stripped = 40
        text = "  " + "A" * 40 + "  " + "\n" * 10

        # Stripped length is 40, below default 50
        assert is_text_sufficient_for_pii_scan(text) is False

        # With min_length 40, should pass
        assert is_text_sufficient_for_pii_scan(text, min_length=40) is True

    def test_handles_tabs_and_newlines(self):
        """Test that tabs and newlines are properly stripped."""
        text = "\t\n" + "X" * 30 + "\t\n"

        assert is_text_sufficient_for_pii_scan(text, min_length=30) is True
        assert is_text_sufficient_for_pii_scan(text, min_length=31) is False

    def test_handles_zero_min_length(self):
        """Test behavior with zero min_length parameter."""
        # Empty string should still fail since len("".strip()) = 0 >= 0
        assert is_text_sufficient_for_pii_scan("", min_length=0) is True
        assert is_text_sufficient_for_pii_scan("a", min_length=0) is True


# ============================================================================
# Tests for PDFExtractionError
# ============================================================================


class TestPDFExtractionError:
    """Tests for the PDFExtractionError exception class."""

    def test_is_exception_subclass(self):
        """Test that PDFExtractionError is a proper Exception subclass."""
        assert issubclass(PDFExtractionError, Exception)

    def test_can_be_raised_with_message(self):
        """Test that PDFExtractionError can be raised with a custom message."""
        with pytest.raises(PDFExtractionError) as exc:
            raise PDFExtractionError("Test error message")

        assert str(exc.value) == "Test error message"

    def test_can_be_caught_as_exception(self):
        """Test that PDFExtractionError can be caught as a generic Exception."""
        try:
            raise PDFExtractionError("Caught as Exception")
        except Exception as e:
            assert isinstance(e, PDFExtractionError)
            assert str(e) == "Caught as Exception"
