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
