"""Unit tests for confidence_scoring utilities.

Tests confidence level classification and score aggregation.
Pure functions with no external dependencies.
"""

import pytest
from src.utils.confidence_scoring import (
    aggregate_page_confidences,
    calculate_confidence_from_verification,
    calculate_document_confidence,
    classify_confidence_level,
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


# ============================================================================
# calculate_confidence_from_verification Tests (8 tests)
# ============================================================================


def test_calculate_confidence_from_verification_empty():
    """Test empty page list returns 0.0."""
    assert calculate_confidence_from_verification([]) == 0.0


def test_calculate_confidence_from_verification_all_passed():
    """Test all pages passing gives high confidence."""
    # All pages passed (0.9 each)
    result = calculate_confidence_from_verification([0.9, 0.9, 0.9])
    assert result == 0.9


def test_calculate_confidence_from_verification_mixed():
    """Test mixed pass/fail gives medium confidence."""
    # Mix of passed (0.9) and failed (0.5)
    result = calculate_confidence_from_verification([0.9, 0.5, 0.9, 0.5])
    assert result == 0.7


def test_calculate_confidence_from_verification_critical_penalty():
    """Test critical issues reduce confidence."""
    # Base: 0.9, with 2 critical issues (-0.10)
    result = calculate_confidence_from_verification(
        page_confidences=[0.9, 0.9, 0.9],
        critical_issues_count=2,
    )
    # 0.9 - (2 * 0.05) = 0.8
    assert result == 0.8


def test_calculate_confidence_from_verification_critical_penalty_max():
    """Test critical penalty caps at 0.30."""
    result = calculate_confidence_from_verification(
        page_confidences=[0.9],
        critical_issues_count=10,  # Would be 0.50 but capped at 0.30
    )
    # 0.9 - 0.30 = 0.6
    assert result == 0.6


def test_calculate_confidence_from_verification_recovery_penalty():
    """Test recovery edits reduce confidence."""
    result = calculate_confidence_from_verification(
        page_confidences=[0.9],
        recovery_edits=3,
    )
    # 0.9 - (3 * 0.02) = 0.84
    assert result == 0.84


def test_calculate_confidence_from_verification_combined_penalties():
    """Test both penalties apply together."""
    result = calculate_confidence_from_verification(
        page_confidences=[0.9],
        critical_issues_count=2,
        recovery_edits=5,
    )
    # 0.9 - 0.10 (critical) - 0.10 (recovery) = 0.7
    assert result == 0.7


def test_calculate_confidence_from_verification_clamps_to_zero():
    """Test result is clamped to 0.0 minimum."""
    result = calculate_confidence_from_verification(
        page_confidences=[0.3],
        critical_issues_count=6,
        recovery_edits=5,
    )
    # 0.3 - 0.30 - 0.10 = -0.1 -> clamped to 0.0
    assert result == 0.0
