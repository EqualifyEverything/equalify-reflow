"""Extraction package with page-by-page parallel processing.

This package provides page-by-page extraction for improved accuracy:
- Each page extracted independently with parallel processing
- Continuation markers for cross-page sentence handling
- Per-page validation with retry on failure
- Scales to any document size (no page limit)

Usage:
    from src.agents.extraction import extract_with_validation

    result, usage = await extract_with_validation(pages, manifest, job_id)
    print(f"Valid: {result.metrics.is_valid}, Confidence: {result.metrics.confidence}")
"""

from .extractor import (
    ExtractionError,
    extract_with_validation,
    get_agent,
    get_page_agent,
    reset_agent,
)
from .models import (
    CONTINUATION_MARKER_FROM,
    CONTINUATION_MARKER_TO,
    ExtractionMetrics,
    ExtractionResult,
    PageContext,
    PageExtractionResult,
    PageMetrics,
    ValidationIssue,
)
from .validator import validate_extraction, validate_page_extraction

__all__ = [
    # Main entry point
    "extract_with_validation",
    # Agent management
    "get_agent",
    "get_page_agent",
    "reset_agent",
    # Exception
    "ExtractionError",
    # Models
    "ExtractionMetrics",
    "ExtractionResult",
    "PageMetrics",
    "PageContext",
    "PageExtractionResult",
    "ValidationIssue",
    # Constants
    "CONTINUATION_MARKER_FROM",
    "CONTINUATION_MARKER_TO",
    # Validation
    "validate_extraction",
    "validate_page_extraction",
]
