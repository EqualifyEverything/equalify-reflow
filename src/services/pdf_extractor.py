"""PDF text extraction service using docling-serve."""

import logging

from .docling_serve_client import get_docling_client

logger = logging.getLogger(__name__)


class PDFExtractionError(Exception):
    """Raised when PDF text extraction fails."""
    pass


async def extract_pdf_text(pdf_content: bytes) -> str:
    """Extract text from PDF bytes via docling-serve.

    Args:
        pdf_content: Raw PDF file bytes

    Returns:
        str: Extracted markdown text from PDF

    Raises:
        PDFExtractionError: If extraction fails or PDF is invalid
    """
    try:
        client = get_docling_client()
        response = await client.convert(
            pdf_content,
            "document.pdf",
            do_ocr=False,
            do_table_structure=False,
            include_images=False,
        )

        text = response.md_content

        if not text or len(text.strip()) < 10:
            raise PDFExtractionError(
                "PDF extraction produced insufficient text. "
                "Document may be empty, image-only, or corrupted."
            )

        logger.info(f"Successfully extracted {len(text)} characters from PDF")
        return text

    except PDFExtractionError:
        raise
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
    """
    return len(text.strip()) >= min_length
