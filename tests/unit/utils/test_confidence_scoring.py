"""Unit tests for confidence_scoring utilities.

Tests confidence level classification and score aggregation.
Pure functions with no external dependencies.
"""

import pytest

from src.utils.confidence_scoring import (
    classify_confidence_level,
    aggregate_page_confidences,
    calculate_document_confidence,
)


pytestmark = pytest.mark.unit


# ============================================================================
# classify_confidence_level Tests (4 tests)
# ============================================================================


@pytest.mark.unit

def test_classify_confidence_level_high():
    """Test high confidence classification (>= 0.85)."""
    assert classify_confidence_level(0.85) == "high"
    assert classify_confidence_level(0.90) == "high"
    assert classify_confidence_level(0.95) == "high"
    assert classify_confidence_level(1.0) == "high"


def test_classify_confidence_level_medium():
    """Test medium confidence classification (>= 0.60, < 0.85)."""
    assert classify_confidence_level(0.60) == "medium"
    assert classify_confidence_level(0.70) == "medium"
    assert classify_confidence_level(0.80) == "medium"
    assert classify_confidence_level(0.84) == "medium"


def test_classify_confidence_level_low():
    """Test low confidence classification (< 0.60)."""
    assert classify_confidence_level(0.0) == "low"
    assert classify_confidence_level(0.30) == "low"
    assert classify_confidence_level(0.50) == "low"
    assert classify_confidence_level(0.59) == "low"


def test_classify_confidence_level_boundary_values():
    """Test boundary values between confidence levels."""
    # Exact boundaries
    assert classify_confidence_level(0.85) == "high"  # Exactly 0.85 → high
    assert classify_confidence_level(0.8499) == "medium"  # Just below 0.85 → medium
    assert classify_confidence_level(0.60) == "medium"  # Exactly 0.60 → medium
    assert classify_confidence_level(0.5999) == "low"  # Just below 0.60 → low


# ============================================================================
# aggregate_page_confidences Tests (3 tests)
# ============================================================================


def test_aggregate_page_confidences_empty_list():
    """Test aggregation of empty list returns 0.0."""
    assert aggregate_page_confidences([]) == 0.0


def test_aggregate_page_confidences_single_page():
    """Test aggregation of single page returns that score."""
    assert aggregate_page_confidences([0.92]) == 0.92
    assert aggregate_page_confidences([0.50]) == 0.50
    assert aggregate_page_confidences([1.0]) == 1.0


def test_aggregate_page_confidences_multiple_pages():
    """Test aggregation calculates average of multiple pages."""
    # Average of 0.9, 0.8, 0.7 = 0.8
    assert aggregate_page_confidences([0.9, 0.8, 0.7]) == pytest.approx(0.8)

    # Average of 0.95, 0.85 = 0.9
    assert aggregate_page_confidences([0.95, 0.85]) == pytest.approx(0.9)

    # Average of 0.6, 0.7, 0.8, 0.9 = 0.75
    assert aggregate_page_confidences([0.6, 0.7, 0.8, 0.9]) == pytest.approx(0.75)


# ============================================================================
# calculate_document_confidence Test (1 test)
# ============================================================================


def test_calculate_document_confidence_returns_tuple():
    """Test calculate_document_confidence returns (score, level) tuple."""
    # High confidence
    score, level = calculate_document_confidence([0.9, 0.95, 0.85])
    assert score == pytest.approx(0.9)
    assert level == "high"

    # Medium confidence
    score, level = calculate_document_confidence([0.7, 0.8, 0.65])
    assert score == pytest.approx(0.7166, rel=1e-3)
    assert level == "medium"

    # Low confidence
    score, level = calculate_document_confidence([0.5, 0.4, 0.55])
    assert score == pytest.approx(0.48333, rel=1e-3)
    assert level == "low"

    # Empty list
    score, level = calculate_document_confidence([])
    assert score == 0.0
    assert level == "low"
