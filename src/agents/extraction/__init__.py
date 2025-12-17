"""Extraction package with plain text output and validation-driven correction.

This package provides a simplified extraction approach:
- Plain text output (no JSON escaping overhead)
- Required page markers for validation
- Pure Python validation with heuristic confidence
- Correction loop with specific feedback

Usage:
    from src.agents.extraction import extract_with_validation

    result, usage = await extract_with_validation(pages, manifest, job_id)
    print(f"Valid: {result.metrics.is_valid}, Confidence: {result.metrics.confidence}")
"""

from .extractor import (
    MAX_CORRECTION_ATTEMPTS,
    extract_with_validation,
    get_agent,
    reset_agent,
)
from .models import (
    ExtractionMetrics,
    ExtractionResult,
    ValidationIssue,
)
from .validator import validate_extraction

__all__ = [
    # Main entry point
    "extract_with_validation",
    # Agent management
    "get_agent",
    "reset_agent",
    "MAX_CORRECTION_ATTEMPTS",
    # Models
    "ExtractionMetrics",
    "ExtractionResult",
    "ValidationIssue",
    # Validation
    "validate_extraction",
]
