"""Tests for Observation model lifecycle (simplified 2-field version)."""

import pytest

from src.shared.models.observation import Observation, ObservationLocation


class TestObservationLifecycle:
    """Tests for the simplified Observation lifecycle."""

    def _create_observation(self, **kwargs) -> Observation:
        """Helper to create a test observation."""
        defaults = {
            "job_id": "job-123",
            "agent": "figures",
            "visual_description": "Image shows a chart",
            "markup_description": "Empty alt text",
            "location": ObservationLocation(
                location_type="element",
                value="img[src='fig1.png']",
                page_num=1,
            ),
        }
        defaults.update(kwargs)
        return Observation(**defaults)

    def test_default_status_is_open(self):
        """Test that new observations start with open status."""
        obs = self._create_observation()
        assert obs.status == "open"
        assert obs.resolution is None

    def test_close_with_fixed(self):
        """Test closing observation with 'fixed' resolution."""
        obs = self._create_observation()
        obs.close("fixed")

        assert obs.status == "closed"
        assert obs.resolution == "fixed"

    def test_close_with_kept_original(self):
        """Test closing observation with 'kept_original' resolution."""
        obs = self._create_observation()
        obs.close("kept_original")

        assert obs.status == "closed"
        assert obs.resolution == "kept_original"

    def test_close_with_skipped(self):
        """Test closing observation with 'skipped' resolution."""
        obs = self._create_observation()
        obs.close("skipped")

        assert obs.status == "closed"
        assert obs.resolution == "skipped"

    def test_cannot_close_twice(self):
        """Test that closing an already-closed observation raises error."""
        obs = self._create_observation()
        obs.close("fixed")

        with pytest.raises(ValueError, match="already closed"):
            obs.close("kept_original")

    def test_category_field(self):
        """Test the category field for grouping."""
        obs = self._create_observation(category="alt_text")
        assert obs.category == "alt_text"

    def test_default_category_is_general(self):
        """Test that default category is 'general'."""
        obs = self._create_observation()
        assert obs.category == "general"

    def test_affected_pages_field(self):
        """Test the affected_pages field for multi-page issues."""
        obs = self._create_observation(affected_pages=[1, 2, 3])
        assert obs.affected_pages == [1, 2, 3]

    def test_severity_levels(self):
        """Test different severity levels."""
        critical = self._create_observation(severity="critical")
        major = self._create_observation(severity="major")
        minor = self._create_observation(severity="minor")

        assert critical.severity == "critical"
        assert major.severity == "major"
        assert minor.severity == "minor"

    def test_confidence_bounds(self):
        """Test confidence must be between 0 and 1."""
        obs = self._create_observation(confidence=0.9)
        assert obs.confidence == 0.9

        with pytest.raises(ValueError):
            self._create_observation(confidence=1.5)

        with pytest.raises(ValueError):
            self._create_observation(confidence=-0.1)

    def test_json_serialization(self):
        """Test that Observation serializes to JSON correctly."""
        obs = self._create_observation(
            category="ocr",
            confidence=0.85,
            severity="major",
        )

        json_str = obs.model_dump_json()
        assert "ocr" in json_str
        assert "0.85" in json_str

        # Deserialize and verify
        restored = Observation.model_validate_json(json_str)
        assert restored.category == obs.category
        assert restored.confidence == obs.confidence
        assert restored.status == "open"

    def test_serialization_after_close(self):
        """Test serialization preserves closed state."""
        obs = self._create_observation()
        obs.close("fixed")

        json_str = obs.model_dump_json()
        restored = Observation.model_validate_json(json_str)

        assert restored.status == "closed"
        assert restored.resolution == "fixed"


class TestObservationLocation:
    """Tests for ObservationLocation model."""

    def test_element_location(self):
        """Test element-type location."""
        loc = ObservationLocation(
            location_type="element",
            value="img[alt='']",
            page_num=3,
        )
        assert loc.location_type == "element"
        assert loc.value == "img[alt='']"
        assert loc.page_num == 3

    def test_range_location(self):
        """Test range-type location."""
        loc = ObservationLocation(
            location_type="range",
            value="10-15",
            page_num=2,
        )
        assert loc.location_type == "range"

    def test_region_location(self):
        """Test region-type location (default)."""
        loc = ObservationLocation(
            value="top-right corner",
            page_num=1,
        )
        assert loc.location_type == "region"

    def test_value_must_not_be_empty(self):
        """Test that value cannot be empty."""
        with pytest.raises(ValueError):
            ObservationLocation(
                value="",
                page_num=1,
            )

    def test_page_num_must_be_positive(self):
        """Test that page_num must be >= 1."""
        with pytest.raises(ValueError):
            ObservationLocation(
                value="test",
                page_num=0,
            )
