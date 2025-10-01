"""PDF text extraction service using Docling."""

import logging
from typing import BinaryIO
from io import BytesIO

from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)


class PDFExtractionError(Exception):
    """Raised when PDF text extraction fails."""
    pass


async def extract_pdf_text(pdf_content: bytes) -> str:
    """Extract text from PDF bytes using Docling.

    Uses Docling library for robust PDF text extraction, including
    support for complex layouts, tables, and OCR when needed.

    Args:
        pdf_content: Raw PDF file bytes

    Returns:
        str: Extracted plain text from PDF

    Raises:
        PDFExtractionError: If extraction fails or PDF is invalid

    Example:
        >>> with open("document.pdf", "rb") as f:
        ...     pdf_bytes = f.read()
        >>> text = await extract_pdf_text(pdf_bytes)
        >>> len(text) > 0
        True
    """
    import tempfile
    import os

    try:
        # Docling requires a file path, so write to temporary file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_file.write(pdf_content)
            tmp_path = tmp_file.name

        try:
            # Initialize Docling converter
            converter = DocumentConverter()

            # Convert PDF to document structure
            result = converter.convert(tmp_path)

            # Extract markdown text (Docling's structured output)
            text = result.document.export_to_markdown()

            # Validate extraction produced content
            if not text or len(text.strip()) < 10:
                raise PDFExtractionError(
                    "PDF extraction produced insufficient text. "
                    "Document may be empty, image-only, or corrupted."
                )

            logger.info(f"Successfully extracted {len(text)} characters from PDF")
            return text

        finally:
            # Clean up temporary file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        logger.error(f"PDF text extraction failed: {e}")
        raise PDFExtractionError(f"Unable to extract text from PDF: {e}") from e


def is_text_sufficient_for_pii_scan(text: str, min_length: int = 50) -> bool:
    """Check if extracted text is sufficient for PII scanning.

    Args:
        text: Extracted text from PDF
        min_length: Minimum character count for meaningful scan

    Returns:
        bool: True if text is sufficient for PII analysis

    Example:
        >>> is_text_sufficient_for_pii_scan("Hello World")
        False
        >>> is_text_sufficient_for_pii_scan("A" * 100)
        True
    """
    return len(text.strip()) >= min_length
