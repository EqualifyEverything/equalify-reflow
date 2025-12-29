"""Final verification agent for quality assurance.

This module provides the final verification step that compares extracted
markdown against source page images to catch:
- OCR errors (misspellings, wrong words)
- Missing content
- Double words and other artifacts
- Truncated text (URLs, sentences)

This is the last line of defense before the end user sees the output.
"""

from .models import (
    VerificationCorrection,
    VerificationIssue,
    VerificationResult,
    PageVerificationResult,
)
from .verifier import verify_final_output

__all__ = [
    "verify_final_output",
    "VerificationResult",
    "PageVerificationResult",
    "VerificationCorrection",
    "VerificationIssue",
]
