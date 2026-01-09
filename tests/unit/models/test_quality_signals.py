"""Tests for QualitySignals model.

Tests the quality signals model used for hybrid confidence calculation.
"""

import pytest
from pydantic import ValidationError
from src.shared.models.quality_signals import QualitySignals


class TestQualitySignalsDefaults:
    """Test default values for QualitySignals."""

    def test_default_values(self) -> None:
        """All fields should have sensible defaults."""
        signals = QualitySignals()

        assert signals.image_clarity == "clear"
        assert signals.text_legibility == "clear"
        assert signals.structure_complexity == "simple"
        assert signals.content_ambiguity == "unambiguous"

    def test_partial_override(self) -> None:
        """Can override some fields while keeping defaults for others."""
        signals = QualitySignals(image_clarity="blurry", content_ambiguity="some_ambiguity")

        assert signals.image_clarity == "blurry"
        assert signals.text_legibility == "clear"  # Default
        assert signals.content_ambiguity == "some_ambiguity"


class TestQualitySignalsImageClarity:
    """Test image_clarity field validation."""

    @pytest.mark.parametrize(
        "value",
        ["clear", "blurry", "partial", "missing"],
    )
    def test_valid_image_clarity_values(self, value: str) -> None:
        """All valid image_clarity values should be accepted."""
        signals = QualitySignals(image_clarity=value)
        assert signals.image_clarity == value

    def test_invalid_image_clarity_rejected(self) -> None:
        """Invalid image_clarity values should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            QualitySignals(image_clarity="invalid")  # type: ignore

        assert "image_clarity" in str(exc_info.value)


class TestQualitySignalsTextLegibility:
    """Test text_legibility field validation."""

    @pytest.mark.parametrize(
        "value",
        ["clear", "faded", "mixed", "handwritten"],
    )
    def test_valid_text_legibility_values(self, value: str) -> None:
        """All valid text_legibility values should be accepted."""
        signals = QualitySignals(text_legibility=value)
        assert signals.text_legibility == value

    def test_invalid_text_legibility_rejected(self) -> None:
        """Invalid text_legibility values should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            QualitySignals(text_legibility="unreadable")  # type: ignore

        assert "text_legibility" in str(exc_info.value)


class TestQualitySignalsStructureComplexity:
    """Test structure_complexity field validation."""

    @pytest.mark.parametrize(
        "value",
        ["simple", "moderate", "complex"],
    )
    def test_valid_structure_complexity_values(self, value: str) -> None:
        """All valid structure_complexity values should be accepted."""
        signals = QualitySignals(structure_complexity=value)
        assert signals.structure_complexity == value

    def test_invalid_structure_complexity_rejected(self) -> None:
        """Invalid structure_complexity values should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            QualitySignals(structure_complexity="very_complex")  # type: ignore

        assert "structure_complexity" in str(exc_info.value)


class TestQualitySignalsContentAmbiguity:
    """Test content_ambiguity field validation."""

    @pytest.mark.parametrize(
        "value",
        ["unambiguous", "some_ambiguity", "highly_ambiguous"],
    )
    def test_valid_content_ambiguity_values(self, value: str) -> None:
        """All valid content_ambiguity values should be accepted."""
        signals = QualitySignals(content_ambiguity=value)
        assert signals.content_ambiguity == value

    def test_invalid_content_ambiguity_rejected(self) -> None:
        """Invalid content_ambiguity values should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            QualitySignals(content_ambiguity="confusing")  # type: ignore

        assert "content_ambiguity" in str(exc_info.value)


class TestQualitySignalsSerialization:
    """Test JSON serialization of QualitySignals."""

    def test_serialize_to_dict(self) -> None:
        """QualitySignals should serialize to a dictionary."""
        signals = QualitySignals(
            image_clarity="blurry",
            text_legibility="faded",
            structure_complexity="complex",
            content_ambiguity="highly_ambiguous",
        )

        data = signals.model_dump()

        assert data == {
            "image_clarity": "blurry",
            "text_legibility": "faded",
            "structure_complexity": "complex",
            "content_ambiguity": "highly_ambiguous",
        }

    def test_serialize_to_json(self) -> None:
        """QualitySignals should serialize to JSON."""
        signals = QualitySignals()
        json_str = signals.model_dump_json()

        assert "image_clarity" in json_str
        assert "clear" in json_str

    def test_deserialize_from_dict(self) -> None:
        """QualitySignals should deserialize from a dictionary."""
        data = {
            "image_clarity": "partial",
            "text_legibility": "mixed",
            "structure_complexity": "moderate",
            "content_ambiguity": "some_ambiguity",
        }

        signals = QualitySignals.model_validate(data)

        assert signals.image_clarity == "partial"
        assert signals.text_legibility == "mixed"
        assert signals.structure_complexity == "moderate"
        assert signals.content_ambiguity == "some_ambiguity"


class TestQualitySignalsDescriptions:
    """Test that field descriptions are present."""

    def test_image_clarity_has_description(self) -> None:
        """image_clarity field should have a description."""
        field_info = QualitySignals.model_fields["image_clarity"]
        assert field_info.description is not None
        assert "clear" in field_info.description.lower()

    def test_text_legibility_has_description(self) -> None:
        """text_legibility field should have a description."""
        field_info = QualitySignals.model_fields["text_legibility"]
        assert field_info.description is not None
        assert "legibility" in field_info.description.lower()
