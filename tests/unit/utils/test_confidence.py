"""Tests for hybrid confidence calculation.

Tests the calculate_confidence(), explain_confidence(), and collect_quality_signals()
functions that implement the 80/20 heuristic/model confidence blend.
"""

import pytest
from src.shared.models.quality_signals import QualitySignals
from src.utils.confidence import (
    calculate_confidence,
    collect_quality_signals,
    explain_confidence,
)

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


class TestCalculateConfidenceCharsPerPage:
    """Test chars_per_page penalty effects."""

    def test_critical_low_chars_penalty(self) -> None:
        """Less than 50 chars/page should reduce confidence by 0.30."""
        signals = QualitySignals(chars_per_page=30.0)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.30 = 0.70
        # Final: 80% * 0.70 + 20% * 1.0 = 0.56 + 0.20 = 0.76
        assert result == 0.76

    def test_warning_low_chars_penalty(self) -> None:
        """Between 50-100 chars/page should reduce confidence by 0.15."""
        signals = QualitySignals(chars_per_page=75.0)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.15 = 0.85
        # Final: 80% * 0.85 + 20% * 1.0 = 0.68 + 0.20 = 0.88
        assert result == 0.88

    def test_normal_chars_no_penalty(self) -> None:
        """100+ chars/page should have no penalty."""
        signals = QualitySignals(chars_per_page=500.0)
        result = calculate_confidence(signals, model_confidence=1.0)

        # No penalty for normal char count
        assert result == 1.0


class TestCalculateConfidenceVisionExtraction:
    """Test vision_extraction_used penalty effects."""

    def test_vision_extraction_penalty(self) -> None:
        """Vision extraction should reduce confidence by 0.10."""
        signals = QualitySignals(vision_extraction_used=True)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92

    def test_no_vision_extraction_no_penalty(self) -> None:
        """Native text extraction should have no penalty."""
        signals = QualitySignals(vision_extraction_used=False)
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0


class TestCalculateConfidencePlaceholderRatio:
    """Test placeholder_ratio penalty effects."""

    def test_high_placeholder_ratio_penalty(self) -> None:
        """Placeholder ratio > 0.3 should reduce confidence by 0.20."""
        # 4 placeholders / 10 elements = 0.4 ratio
        signals = QualitySignals(placeholder_count=4, total_elements=10)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.20 = 0.80
        # Final: 80% * 0.80 + 20% * 1.0 = 0.64 + 0.20 = 0.84
        assert result == 0.84

    def test_moderate_placeholder_ratio_penalty(self) -> None:
        """Placeholder ratio > 0.1 should reduce confidence by 0.10."""
        # 2 placeholders / 10 elements = 0.2 ratio
        signals = QualitySignals(placeholder_count=2, total_elements=10)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92

    def test_low_placeholder_ratio_no_penalty(self) -> None:
        """Placeholder ratio <= 0.1 should have no penalty."""
        # 1 placeholder / 10 elements = 0.1 ratio (at boundary)
        signals = QualitySignals(placeholder_count=1, total_elements=10)
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0


class TestCalculateConfidenceSpacingArtifacts:
    """Test spacing_artifact_count penalty effects."""

    def test_single_spacing_artifact_penalty(self) -> None:
        """Each spacing artifact should reduce confidence by 0.02."""
        signals = QualitySignals(spacing_artifact_count=1)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.02 = 0.98
        # Final: 80% * 0.98 + 20% * 1.0 = 0.784 + 0.20 = 0.984
        assert result == 0.984

    def test_multiple_spacing_artifacts_penalty(self) -> None:
        """Multiple spacing artifacts accumulate penalty."""
        signals = QualitySignals(spacing_artifact_count=3)
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - (3 * 0.02) = 1.0 - 0.06 = 0.94
        # Final: 80% * 0.94 + 20% * 1.0 = 0.752 + 0.20 = 0.952
        assert result == 0.952

    def test_max_spacing_artifact_penalty_capped(self) -> None:
        """Spacing artifact penalty should be capped at 0.10."""
        signals = QualitySignals(spacing_artifact_count=10)  # Would be 0.20 uncapped
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 (capped) = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92


class TestCalculateConfidenceTableExtraction:
    """Test table_extraction_rate penalty effects."""

    def test_poor_table_extraction_penalty(self) -> None:
        """Table extraction rate < 0.5 should reduce confidence by 0.15."""
        signals = QualitySignals(tables_found=1, tables_planned=4)  # 0.25 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.15 = 0.85
        # Final: 80% * 0.85 + 20% * 1.0 = 0.68 + 0.20 = 0.88
        assert result == 0.88

    def test_moderate_table_extraction_penalty(self) -> None:
        """Table extraction rate < 0.8 should reduce confidence by 0.08."""
        signals = QualitySignals(tables_found=3, tables_planned=5)  # 0.6 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.08 = 0.92
        # Final: 80% * 0.92 + 20% * 1.0 = 0.736 + 0.20 = 0.936
        assert result == 0.936

    def test_good_table_extraction_no_penalty(self) -> None:
        """Table extraction rate >= 0.8 should have no penalty."""
        signals = QualitySignals(tables_found=4, tables_planned=5)  # 0.8 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0

    def test_no_tables_planned_no_penalty(self) -> None:
        """No tables planned means 100% success rate."""
        signals = QualitySignals(tables_found=0, tables_planned=0)
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0


class TestCalculateConfidenceAltTextCoverage:
    """Test alt_text_coverage penalty effects."""

    def test_poor_alt_text_coverage_penalty(self) -> None:
        """Alt text coverage < 0.5 should reduce confidence by 0.20."""
        signals = QualitySignals(images_with_alt=1, total_images=5)  # 0.2 coverage
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.20 = 0.80
        # Final: 80% * 0.80 + 20% * 1.0 = 0.64 + 0.20 = 0.84
        assert result == 0.84

    def test_moderate_alt_text_coverage_penalty(self) -> None:
        """Alt text coverage < 0.8 should reduce confidence by 0.10."""
        signals = QualitySignals(images_with_alt=3, total_images=5)  # 0.6 coverage
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.10 = 0.90
        # Final: 80% * 0.90 + 20% * 1.0 = 0.72 + 0.20 = 0.92
        assert result == 0.92

    def test_good_alt_text_coverage_no_penalty(self) -> None:
        """Alt text coverage >= 0.8 should have no penalty."""
        signals = QualitySignals(images_with_alt=4, total_images=5)  # 0.8 coverage
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0

    def test_no_images_no_penalty(self) -> None:
        """No images means 100% coverage rate."""
        signals = QualitySignals(images_with_alt=0, total_images=0)
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0


class TestCalculateConfidenceHeadingCompleteness:
    """Test heading_completeness penalty effects."""

    def test_poor_heading_completeness_penalty(self) -> None:
        """Heading completeness < 0.5 should reduce confidence by 0.15."""
        signals = QualitySignals(headings_found=1, headings_planned=5)  # 0.2 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.15 = 0.85
        # Final: 80% * 0.85 + 20% * 1.0 = 0.68 + 0.20 = 0.88
        assert result == 0.88

    def test_moderate_heading_completeness_penalty(self) -> None:
        """Heading completeness < 0.8 should reduce confidence by 0.05."""
        signals = QualitySignals(headings_found=3, headings_planned=5)  # 0.6 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        # Heuristic: 1.0 - 0.05 = 0.95
        # Final: 80% * 0.95 + 20% * 1.0 = 0.76 + 0.20 = 0.96
        assert result == 0.96

    def test_good_heading_completeness_no_penalty(self) -> None:
        """Heading completeness >= 0.8 should have no penalty."""
        signals = QualitySignals(headings_found=4, headings_planned=5)  # 0.8 rate
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0

    def test_no_headings_planned_no_penalty(self) -> None:
        """No headings planned means 100% complete."""
        signals = QualitySignals(headings_found=0, headings_planned=0)
        result = calculate_confidence(signals, model_confidence=1.0)

        assert result == 1.0


class TestCollectQualitySignals:
    """Test the collect_quality_signals() function."""

    def test_basic_signal_collection(self) -> None:
        """Should collect basic signals from page markdowns."""
        page_markdowns = {
            1: "# Title\n\nThis is page one content.",
            2: "## Section\n\nThis is page two content.",
        }
        signals = collect_quality_signals(page_markdowns)

        assert signals.chars_per_page > 0
        assert signals.vision_extraction_used is False

    def test_vision_extraction_flag(self) -> None:
        """Should record vision extraction usage."""
        page_markdowns = {1: "Content"}
        signals = collect_quality_signals(
            page_markdowns, vision_extraction_used=True
        )

        assert signals.vision_extraction_used is True

    def test_placeholder_counting(self) -> None:
        """Should count placeholders in markdown."""
        page_markdowns = {
            1: "# Title\n\n<!-- image -->\n\n[TODO: fix this]\n\n<!-- image -->",
        }
        signals = collect_quality_signals(page_markdowns)

        assert signals.placeholder_count >= 2  # At least the placeholders we added

    def test_spacing_artifact_counting(self) -> None:
        """Should count letter-spacing artifacts."""
        page_markdowns = {
            1: "The r e q u i r e m e n t s are listed below.",
        }
        signals = collect_quality_signals(page_markdowns)

        assert signals.spacing_artifact_count >= 1

    def test_table_metrics(self) -> None:
        """Should record table extraction metrics."""
        page_markdowns = {1: "Content"}
        signals = collect_quality_signals(
            page_markdowns,
            tables_planned=5,
            tables_found=3,
        )

        assert signals.tables_planned == 5
        assert signals.tables_found == 3
        assert signals.table_extraction_rate == 0.6

    def test_image_metrics(self) -> None:
        """Should record image alt-text metrics."""
        page_markdowns = {1: "Content"}
        signals = collect_quality_signals(
            page_markdowns,
            total_images=10,
            images_with_alt=7,
        )

        assert signals.total_images == 10
        assert signals.images_with_alt == 7
        assert signals.alt_text_coverage == 0.7

    def test_heading_metrics(self) -> None:
        """Should record heading completeness metrics."""
        page_markdowns = {1: "Content"}
        signals = collect_quality_signals(
            page_markdowns,
            headings_planned=10,
            headings_found=8,
        )

        assert signals.headings_planned == 10
        assert signals.headings_found == 8
        assert signals.heading_completeness == 0.8

    def test_subjective_quality_signals(self) -> None:
        """Should pass through subjective quality signals."""
        page_markdowns = {1: "Content"}
        signals = collect_quality_signals(
            page_markdowns,
            image_clarity="blurry",
            text_legibility="faded",
            structure_complexity="complex",
            content_ambiguity="highly_ambiguous",
        )

        assert signals.image_clarity == "blurry"
        assert signals.text_legibility == "faded"
        assert signals.structure_complexity == "complex"
        assert signals.content_ambiguity == "highly_ambiguous"

    def test_empty_page_markdowns(self) -> None:
        """Should handle empty page markdowns gracefully."""
        page_markdowns: dict[int, str] = {}
        signals = collect_quality_signals(page_markdowns)

        # Should not crash, chars_per_page should be 0
        assert signals.chars_per_page == 0.0


class TestExplainConfidenceExtractionMetrics:
    """Test explain_confidence() with extraction metrics."""

    def test_records_chars_per_page_penalty(self) -> None:
        """Should record chars_per_page penalty."""
        signals = QualitySignals(chars_per_page=30.0)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "chars_per_page" in penalty_signals

    def test_records_vision_extraction_penalty(self) -> None:
        """Should record vision_extraction_used penalty."""
        signals = QualitySignals(vision_extraction_used=True)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "vision_extraction_used" in penalty_signals

    def test_records_placeholder_ratio_penalty(self) -> None:
        """Should record placeholder_ratio penalty."""
        signals = QualitySignals(placeholder_count=5, total_elements=10)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "placeholder_ratio" in penalty_signals

    def test_records_spacing_artifact_penalty(self) -> None:
        """Should record spacing_artifact_count penalty."""
        signals = QualitySignals(spacing_artifact_count=3)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "spacing_artifact_count" in penalty_signals

    def test_records_table_extraction_penalty(self) -> None:
        """Should record table_extraction_rate penalty."""
        signals = QualitySignals(tables_found=1, tables_planned=5)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "table_extraction_rate" in penalty_signals

    def test_records_alt_text_coverage_penalty(self) -> None:
        """Should record alt_text_coverage penalty."""
        signals = QualitySignals(images_with_alt=1, total_images=5)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "alt_text_coverage" in penalty_signals

    def test_records_heading_completeness_penalty(self) -> None:
        """Should record heading_completeness penalty."""
        signals = QualitySignals(headings_found=1, headings_planned=5)
        result = explain_confidence(signals, model_confidence=0.9)

        penalty_signals = [p["signal"] for p in result["penalties"]]
        assert "heading_completeness" in penalty_signals
