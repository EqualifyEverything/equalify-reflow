"""Tests for hybrid confidence calculation.

Tests the calculate_confidence() and explain_confidence() functions
that implement the 80/20 heuristic/model confidence blend.
"""

import pytest
from src.shared.models.quality_signals import QualitySignals
from src.utils.confidence import calculate_confidence, explain_confidence

pytestmark = pytest.mark.unit


class TestCalculateConfidenceDefaults:
    """Test confidence calculation with default signals."""

    def test_perfect_signals_high_model_confidence(self) -> None:
        """Perfect signals + high model confidence = high overall."""
        signals = QualitySignals()  # All defaults (best case)
        result = calculate_confidence(signals, model_confidence=1.0)

        # 80% * 1.0 (perfect heuristic) + 20% * 1.0 (model) = 1.0
        assert result == 1.0

    def test_perfect_signals_low_model_confidence(self) -> None:
        """Perfect signals + low model confidence = moderate overall."""
        signals = QualitySignals()
        result = calculate_confidence(signals, model_confidence=0.5)

        # 80% * 1.0 + 20% * 0.5 = 0.8 + 0.1 = 0.9
        assert result == 0.9

    def test_typical_model_confidence_0_8(self) -> None:
        """Typical model_confidence of 0.8 with perfect signals."""
        signals = QualitySignals()
        result = calculate_confidence(signals, model_confidence=0.8)

        # 80% * 1.0 + 20% * 0.8 = 0.8 + 0.16 = 0.96
        assert result == 0.96


class TestCalculateConfidenceImageClarity:
    """Test image_clarity penalty effects."""

    def test_blurry_image_penalty(self) -> None:
        """Blurry image should reduce confidence by 0.20."""
        signals = QualitySignals(image_clarity="blurry")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.20 = 0.80
        # Final: 80% * 0.80 + 20% * 1.0 = 0.64 + 0.20 = 0.84
        assert result == 0.84

    def test_partial_image_penalty(self) -> None:
        """Partial image should reduce confidence by 0.30."""
        signals = QualitySignals(image_clarity="partial")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.30 = 0.70
        # Final: 80% * 0.70 + 20% * 1.0 = 0.56 + 0.20 = 0.76
        assert result == 0.76

    def test_missing_image_penalty(self) -> None:
        """Missing image should reduce confidence by 0.50."""
        signals = QualitySignals(image_clarity="missing")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.50 = 0.50
        # Final: 80% * 0.50 + 20% * 1.0 = 0.40 + 0.20 = 0.60
        assert result == 0.6


class TestCalculateConfidenceTextLegibility:
    """Test text_legibility penalty effects."""

    def test_faded_text_penalty(self) -> None:
        """Faded text should reduce confidence by 0.15."""
        signals = QualitySignals(text_legibility="faded")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.15 = 0.85
        # Final: 80% * 0.85 + 20% * 1.0 = 0.68 + 0.20 = 0.88
        assert result == 0.88

    def test_mixed_text_penalty(self) -> None:
        """Mixed text should reduce confidence by 0.10."""
        signals = QualitySignals(text_legibility="mixed")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92

    def test_handwritten_text_penalty(self) -> None:
        """Handwritten text should reduce confidence by 0.25."""
        signals = QualitySignals(text_legibility="handwritten")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.25 = 0.75
        # Final: 80% * 0.75 + 20% * 1.0 = 0.60 + 0.20 = 0.80
        assert result == 0.8


class TestCalculateConfidenceStructureComplexity:
    """Test structure_complexity penalty effects."""

    def test_moderate_complexity_penalty(self) -> None:
        """Moderate complexity should reduce confidence by 0.05."""
        signals = QualitySignals(structure_complexity="moderate")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.05 = 0.95
        # Final: 80% * 0.95 + 20% * 1.0 = 0.76 + 0.20 = 0.96
        assert result == 0.96

    def test_complex_structure_penalty(self) -> None:
        """Complex structure should reduce confidence by 0.15."""
        signals = QualitySignals(structure_complexity="complex")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.15 = 0.85
        # Final: 80% * 0.85 + 20% * 1.0 = 0.68 + 0.20 = 0.88
        assert result == 0.88


class TestCalculateConfidenceContentAmbiguity:
    """Test content_ambiguity penalty effects."""

    def test_some_ambiguity_penalty(self) -> None:
        """Some ambiguity should reduce confidence by 0.10."""
        signals = QualitySignals(content_ambiguity="some_ambiguity")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92

    def test_highly_ambiguous_penalty(self) -> None:
        """Highly ambiguous should reduce confidence by 0.25."""
        signals = QualitySignals(content_ambiguity="highly_ambiguous")
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.25 = 0.75
        # Final: 80% * 0.75 + 20% * 1.0 = 0.60 + 0.20 = 0.80
        assert result == 0.8


class TestCalculateConfidenceCombinedPenalties:
    """Test combined penalty effects."""

    def test_multiple_penalties_additive(self) -> None:
        """Multiple penalties should be additive."""
        signals = QualitySignals(
            image_clarity="blurry",  # -0.20
            text_legibility="faded",  # -0.15
        )
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.20 - 0.15 = 0.65
        # Final: 80% * 0.65 + 20% * 1.0 = 0.52 + 0.20 = 0.72
        assert result == 0.72

    def test_worst_case_signals(self) -> None:
        """All worst-case signals should still produce valid confidence."""
        signals = QualitySignals(
            image_clarity="missing",  # -0.50
            text_legibility="handwritten",  # -0.25
            structure_complexity="complex",  # -0.15
            content_ambiguity="highly_ambiguous",  # -0.25
        )
        result = calculate_confidence(signals, model_confidence=0.5)

        # Total penalty: 0.50 + 0.25 + 0.15 + 0.25 = 1.15
        # Heuristic: max(0, 1.0 - 1.15) = 0.0 (clamped)
        # Final: 80% * 0.0 + 20% * 0.5 = 0.0 + 0.1 = 0.1
        assert result == 0.1

    def test_clamping_to_valid_range(self) -> None:
        """Confidence should always be in [0.0, 1.0] range."""
        # Test extreme negative case
        signals = QualitySignals(
            image_clarity="missing",
            text_legibility="handwritten",
            structure_complexity="complex",
            content_ambiguity="highly_ambiguous",
        )
        result = calculate_confidence(signals, model_confidence=0.0)
        assert 0.0 <= result <= 1.0

        # Test edge case with model_confidence > 1.0 (should be clamped)
        signals = QualitySignals()
        result = calculate_confidence(signals, model_confidence=1.5)
        assert result <= 1.0


class TestCalculateConfidenceRounding:
    """Test rounding behavior."""

    def test_rounds_to_three_decimal_places(self) -> None:
        """Result should be rounded to 3 decimal places."""
        signals = QualitySignals(image_clarity="blurry")
        result = calculate_confidence(signals, model_confidence=0.777)

        # The result should not have more than 3 decimal places
        assert result == round(result, 3)


class TestExplainConfidence:
    """Test the explain_confidence() function."""

    def test_returns_breakdown_dict(self) -> None:
        """explain_confidence should return a breakdown dictionary."""
        signals = QualitySignals()
        result = explain_confidence(signals, model_confidence=0.9)

        assert "heuristic_score" in result
        assert "model_confidence" in result
        assert "final_confidence" in result
        assert "penalties" in result
        assert "weight_heuristic" in result
        assert "weight_model" in result

    def test_weights_are_80_20(self) -> None:
        """Weights should be 80% heuristic, 20% model."""
        signals = QualitySignals()
        result = explain_confidence(signals, model_confidence=0.9)

        assert result["weight_heuristic"] == 0.8
        assert result["weight_model"] == 0.2

    def test_records_penalties(self) -> None:
        """Penalties should be recorded in the result."""
        signals = QualitySignals(
            image_clarity="blurry",
            structure_complexity="complex",
        )
        result = explain_confidence(signals, model_confidence=0.9)

        penalties = result["penalties"]
        assert len(penalties) == 2

        # Check penalty structure
        penalty_signals = [p["signal"] for p in penalties]
        assert "image_clarity" in penalty_signals
        assert "structure_complexity" in penalty_signals

    def test_no_penalties_for_best_case(self) -> None:
        """No penalties should be recorded for best-case signals."""
        signals = QualitySignals()  # All defaults (best case)
        result = explain_confidence(signals, model_confidence=0.9)

        assert result["penalties"] == []

    def test_final_confidence_matches_calculate(self) -> None:
        """Final confidence in explanation should match calculate_confidence()."""
        signals = QualitySignals(
            image_clarity="blurry",
            text_legibility="faded",
        )

        explanation = explain_confidence(signals, model_confidence=0.85)
        calculated = calculate_confidence(signals, model_confidence=0.85)

        assert explanation["final_confidence"] == calculated
