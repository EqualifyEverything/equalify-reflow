"""Hybrid confidence calculation utilities.

This module provides functions for calculating calibrated confidence scores
using a hybrid approach that combines:
- Heuristic penalties based on observable quality signals (80% weight)
- Model self-assessment (20% weight)

The heuristic approach provides more stable, explainable confidence
than pure model self-assessment, which tends to be overconfident.
"""

from __future__ import annotations

from src.shared.models.quality_signals import QualitySignals


def calculate_confidence(signals: QualitySignals, model_confidence: float) -> float:
    """Calculate confidence from quality signals + model self-assessment.

    Uses a hybrid approach:
    - 80% weight on heuristic score (based on observable quality signals)
    - 20% weight on model self-reported confidence

    Args:
        signals: Quality signals observed during processing
        model_confidence: Model's self-reported confidence (0.0-1.0)

    Returns:
        Calibrated confidence score (0.0-1.0), rounded to 3 decimal places

    Example:
        >>> signals = QualitySignals(
        ...     image_clarity="blurry",
        ...     text_legibility="clear",
        ...     structure_complexity="complex",
        ...     content_ambiguity="some_ambiguity",
        ... )
        >>> calculate_confidence(signals, model_confidence=0.9)
        0.47  # Significantly reduced due to quality issues
    """
    # Start with perfect heuristic score
    score = 1.0

    # Image clarity penalties
    if signals.image_clarity == "blurry":
        score -= 0.20
    elif signals.image_clarity == "partial":
        score -= 0.30
    elif signals.image_clarity == "missing":
        score -= 0.50

    # Text legibility penalties
    if signals.text_legibility == "faded":
        score -= 0.15
    elif signals.text_legibility == "mixed":
        score -= 0.10
    elif signals.text_legibility == "handwritten":
        score -= 0.25

    # Structure complexity penalties
    if signals.structure_complexity == "moderate":
        score -= 0.05
    elif signals.structure_complexity == "complex":
        score -= 0.15

    # Content ambiguity penalties
    if signals.content_ambiguity == "some_ambiguity":
        score -= 0.10
    elif signals.content_ambiguity == "highly_ambiguous":
        score -= 0.25

    # Clamp heuristic score to valid range
    heuristic_score = max(0.0, min(1.0, score))

    # Clamp model confidence to valid range
    model_confidence = max(0.0, min(1.0, model_confidence))

    # Blend: 80% heuristics, 20% model self-assessment
    final_confidence = 0.8 * heuristic_score + 0.2 * model_confidence

    return round(final_confidence, 3)


def explain_confidence(signals: QualitySignals, model_confidence: float) -> dict:
    """Explain the confidence calculation for debugging/logging.

    Args:
        signals: Quality signals observed during processing
        model_confidence: Model's self-reported confidence (0.0-1.0)

    Returns:
        Dictionary with breakdown of confidence calculation

    Example:
        >>> signals = QualitySignals(image_clarity="blurry")
        >>> explain_confidence(signals, 0.9)
        {
            'heuristic_score': 0.8,
            'model_confidence': 0.9,
            'final_confidence': 0.82,
            'penalties': [{'signal': 'image_clarity', 'value': 'blurry', 'penalty': -0.2}],
            'weight_heuristic': 0.8,
            'weight_model': 0.2,
        }
    """
    penalties = []
    score = 1.0

    # Track penalties
    if signals.image_clarity == "blurry":
        penalties.append({"signal": "image_clarity", "value": "blurry", "penalty": -0.20})
        score -= 0.20
    elif signals.image_clarity == "partial":
        penalties.append({"signal": "image_clarity", "value": "partial", "penalty": -0.30})
        score -= 0.30
    elif signals.image_clarity == "missing":
        penalties.append({"signal": "image_clarity", "value": "missing", "penalty": -0.50})
        score -= 0.50

    if signals.text_legibility == "faded":
        penalties.append({"signal": "text_legibility", "value": "faded", "penalty": -0.15})
        score -= 0.15
    elif signals.text_legibility == "mixed":
        penalties.append({"signal": "text_legibility", "value": "mixed", "penalty": -0.10})
        score -= 0.10
    elif signals.text_legibility == "handwritten":
        penalties.append({"signal": "text_legibility", "value": "handwritten", "penalty": -0.25})
        score -= 0.25

    if signals.structure_complexity == "moderate":
        penalties.append({"signal": "structure_complexity", "value": "moderate", "penalty": -0.05})
        score -= 0.05
    elif signals.structure_complexity == "complex":
        penalties.append({"signal": "structure_complexity", "value": "complex", "penalty": -0.15})
        score -= 0.15

    if signals.content_ambiguity == "some_ambiguity":
        penalties.append({"signal": "content_ambiguity", "value": "some_ambiguity", "penalty": -0.10})
        score -= 0.10
    elif signals.content_ambiguity == "highly_ambiguous":
        penalties.append({"signal": "content_ambiguity", "value": "highly_ambiguous", "penalty": -0.25})
        score -= 0.25

    heuristic_score = max(0.0, min(1.0, score))
    model_confidence_clamped = max(0.0, min(1.0, model_confidence))
    final_confidence = round(0.8 * heuristic_score + 0.2 * model_confidence_clamped, 3)

    return {
        "heuristic_score": round(heuristic_score, 3),
        "model_confidence": model_confidence,
        "final_confidence": final_confidence,
        "penalties": penalties,
        "weight_heuristic": 0.8,
        "weight_model": 0.2,
    }


__all__ = ["calculate_confidence", "explain_confidence"]
