"""Test helpers for edge case testing."""

from io import BytesIO
from typing import Literal


def generate_large_pdf(size_mb: int) -> bytes:
    """Generate a minimal PDF of specified size.

    Args:
        size_mb: Target size in megabytes

    Returns:
        PDF content as bytes
    """
    # Basic minimal PDF structure
    header = b"%PDF-1.4\n"
    catalog = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    pages = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    page = b"""3 0 obj
<< /Type /Page /Parent 2 0 R
   /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>
   /MediaBox [0 0 612 792]
   /Contents 4 0 R >>
endobj
"""
    content_start = b"4 0 obj\n<< /Length "

    # Calculate how much padding we need
    target_size = size_mb * 1024 * 1024
    base_size = len(header) + len(catalog) + len(pages) + len(page) + len(content_start) + 100

    # Generate padding text
    padding_size = max(target_size - base_size, 100)
    padding = b"Large file test content. " * (padding_size // 25)

    # Build complete PDF
    content_length = str(len(padding) + 50).encode()
    content_obj = (
        b"4 0 obj\n<< /Length " + content_length + b" >>\nstream\n"
        b"BT\n/F1 12 Tf\n100 700 Td\n(Large Test PDF) Tj\nET\n"
        + padding +
        b"\nendstream\nendobj\n"
    )

    xref = b"xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n"
    trailer = b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n500\n%%EOF"

    pdf = header + catalog + pages + page + content_obj + xref + trailer

    return pdf


def create_corrupted_pdf() -> bytes:
    """Create a corrupted PDF for testing error handling.

    Returns:
        Invalid PDF-like content
    """
    return b"%PDF-1.4\nThis is not a valid PDF structure\n%%EOF"


def create_encrypted_pdf() -> bytes:
    """Create an encrypted/password-protected PDF.

    Returns:
        Encrypted PDF content
    """
    # Minimal encrypted PDF structure
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
trailer
<< /Size 4 /Root 1 0 R /Encrypt << /Filter /Standard /V 1 /R 2 >> >>
startxref
200
%%EOF"""


def create_empty_pdf() -> bytes:
    """Create a zero-byte file.

    Returns:
        Empty bytes
    """
    return b""


def create_truncated_pdf() -> bytes:
    """Create a partial/truncated PDF.

    Returns:
        Incomplete PDF content
    """
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog"


def create_non_pdf_file() -> bytes:
    """Create a non-PDF file with .pdf extension.

    Returns:
        Text file content
    """
    return b"This is just a text file, not a PDF at all!"


def generate_pii_text(entity_type: Literal["ssn", "email", "phone", "name", "address"]) -> str:
    """Generate text containing specific PII types for testing.

    Args:
        entity_type: Type of PII to generate

    Returns:
        Text containing the specified PII type
    """
    pii_examples = {
        "ssn": "My Social Security Number is 123-45-6789 for tax purposes.",
        "email": "Contact me at john.doe@example.com for more information.",
        "phone": "You can reach me at (555) 123-4567 or 555-987-6543.",
        "name": "John Smith and Mary Johnson are the primary contacts.",
        "address": "My address is 123 Main Street, Chicago, IL 60601."
    }
    return pii_examples.get(entity_type, "No PII here.")


def generate_false_positive_text() -> str:
    """Generate text that looks like PII but isn't.

    Returns:
        Text with PII-like patterns
    """
    return """
    Course numbers: CS-123-45-6789 and MATH-111-11-1111
    Reference codes: REF-000-00-0000
    Test data: test@test.test
    Sample phone: 000-000-0000
    """


def generate_valid_academic_content() -> str:
    """Generate valid academic content without PII.

    Returns:
        Academic text content
    """
    return """
    Introduction to Computer Science

    This course covers fundamental concepts in programming,
    data structures, and algorithms. Students will learn
    Python, Java, and C++ throughout the semester.

    Prerequisites: None
    Credit Hours: 3
    Meeting Times: MWF 10:00-10:50
    """


def create_pdf_with_content(content: str) -> bytes:
    """Create a valid PDF containing specific text content.

    Args:
        content: Text to include in PDF

    Returns:
        PDF with the specified content
    """
    # Escape special characters for PDF
    escaped_content = content.replace('(', '\\(').replace(')', '\\)')

    pdf = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R
   /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>
   /MediaBox [0 0 612 792]
   /Contents 4 0 R >>
endobj
4 0 obj
<< /Length {len(escaped_content) + 50} >>
stream
BT
/F1 12 Tf
72 720 Td
({escaped_content}) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer
<< /Size 5 /Root 1 0 R >>
startxref
400
%%EOF""".encode()

    return pdf
