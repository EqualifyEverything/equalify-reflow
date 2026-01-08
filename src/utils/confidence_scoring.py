"""Confidence scoring utilities for AI processing quality assessment."""

from typing import Literal

from ..config import settings

ConfidenceLevel = Literal["high", "medium", "low"]


def classify_confidence_level(score: float) -> ConfidenceLevel:
    """Classify a confidence score into high/medium/low categories.

    Args:
        score: Confidence score between 0.0 and 1.0

    Returns:
        "high" if score >= 0.85, "medium" if >= 0.60, "low" otherwise

    Example:
        >>> classify_confidence_level(0.92)
        'high'
        >>> classify_confidence_level(0.75)
        'medium'
        >>> classify_confidence_level(0.45)
        'low'
    """
    if score >= settings.confidence_threshold_high:
        return "high"
    elif score >= settings.confidence_threshold_medium:
        return "medium"
    else:
        return "low"


def aggregate_page_confidences(page_scores: list[float]) -> float:
    """Calculate aggregate confidence score from multiple page scores.

    Uses simple arithmetic mean of all page confidence scores.

    Args:
        page_scores: List of confidence scores (0.0-1.0) for each page

    Returns:
        Average confidence score, or 0.0 if no pages provided

    Example:
        >>> aggregate_page_confidences([0.9, 0.85, 0.92])
        0.89
        >>> aggregate_page_confidences([])
        0.0
    """
    if not page_scores:
        return 0.0

    return sum(page_scores) / len(page_scores)


def calculate_document_confidence(
    page_scores: list[float]
) -> tuple[float, ConfidenceLevel]:
    """Calculate overall document confidence score and classification.

    Args:
        page_scores: List of per-page confidence scores

    Returns:
        Tuple of (aggregate_score, confidence_level)

    Example:
        >>> calculate_document_confidence([0.9, 0.88, 0.92])
        (0.9, 'high')
        >>> calculate_document_confidence([0.7, 0.65, 0.72])
        (0.69, 'medium')
    """
    aggregate_score = aggregate_page_confidences(page_scores)
    level = classify_confidence_level(aggregate_score)

    return aggregate_score, level


def calculate_confidence_from_verification(
    page_confidences: list[float],
    critical_issues_count: int = 0,
    recovery_edits: int = 0,
) -> float:
    """Calculate document confidence from verification data.

    Uses a weighted approach combining page pass rates with penalties:
    - Base: mean of page confidences (typically 0.9 for passed, 0.5 for failed)
    - Penalty: -0.05 per critical issue (max -0.30)
    - Penalty: -0.02 per recovery edit (max -0.10)

    Args:
        page_confidences: List of per-page confidence scores from verification
        critical_issues_count: Number of critical issues found during verification
        recovery_edits: Number of recovery edits applied

    Returns:
        Confidence score between 0.0 and 1.0, rounded to 3 decimals

    Example:
        >>> calculate_confidence_from_verification([0.9, 0.9, 0.9])
        0.9
        >>> calculate_confidence_from_verification([0.9, 0.5, 0.9], critical_issues_count=2)
        0.667
    """
    if not page_confidences:
        return 0.0

    # Base: mean of page confidences
    base_score = sum(page_confidences) / len(page_confidences)

    # Critical issues penalty (max 30% reduction)
    critical_penalty = min(0.30, critical_issues_count * 0.05)

    # Recovery penalty (max 10% reduction) - needing recovery indicates issues
    recovery_penalty = min(0.10, recovery_edits * 0.02)

    # Calculate final score
    final_score = base_score - critical_penalty - recovery_penalty

    # Clamp to valid range and round
    return round(max(0.0, min(1.0, final_score)), 3)
