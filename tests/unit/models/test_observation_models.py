"""Unit tests for observation models (Observation, ObservationLocation)."""

import json

import pytest
from pydantic import ValidationError
from src.shared.models.observation import (
    Observation,
    ObservationLocation,
)


class TestObservationLocation:
    """Tests for ObservationLocation model."""

    def test_minimal_valid_location(self) -> None:
        """Test creating ObservationLocation with required fields."""
        location = ObservationLocation(
            value="img[alt='']",
            page_num=3
        )

        assert location.location_type == "region"  # default
        assert location.value == "img[alt='']"
        assert location.page_num == 3

    def test_element_location_type(self) -> None:
        """Test element location type."""
        location = ObservationLocation(
            location_type="element",
            value="img[src='figure-1.png']",
            page_num=5
        )

        assert location.location_type == "element"
        assert location.value == "img[src='figure-1.png']"

    def test_range_location_type(self) -> None:
        """Test range location type."""
        location = ObservationLocation(
            location_type="range",
            value="10-15",
            page_num=2
        )

        assert location.location_type == "range"
        assert location.value == "10-15"

    def test_region_location_type(self) -> None:
        """Test region location type."""
        location = ObservationLocation(
            location_type="region",
            value="top-left corner, main content area",
            page_num=1
        )

        assert location.location_type == "region"

    def test_invalid_location_type(self) -> None:
        """Test invalid location type is rejected."""
        with pytest.raises(ValidationError):
            ObservationLocation(
                location_type="invalid",  # type: ignore[arg-type]
                value="test",
                page_num=1
            )

    def test_empty_value_rejected(self) -> None:
        """Test empty value is rejected."""
        with pytest.raises(ValidationError):
            ObservationLocation(
                value="",
                page_num=1
            )

    def test_page_num_validation(self) -> None:
        """Test page_num must be >= 1."""
        with pytest.raises(ValidationError):
            ObservationLocation(value="test", page_num=0)

        with pytest.raises(ValidationError):
            ObservationLocation(value="test", page_num=-1)

    def test_json_serialization(self) -> None:
        """Test ObservationLocation serializes to JSON correctly."""
        location = ObservationLocation(
            location_type="element",
            value="table.data",
            page_num=4
        )

        json_str = location.model_dump_json()
        data = json.loads(json_str)

        assert data["location_type"] == "element"
        assert data["value"] == "table.data"
        assert data["page_num"] == 4


class TestObservation:
    """Tests for Observation model."""

    def test_minimal_valid_observation(self) -> None:
        """Test creating Observation with required fields only."""
        observation = Observation(
            job_id="job-123",
            agent="figures",
            visual_description="Image shows a flowchart",
            markup_description="Image has empty alt text",
            location=ObservationLocation(value="figure area", page_num=3)
        )

        assert observation.job_id == "job-123"
        assert observation.agent == "figures"
        assert observation.source == "agent"  # default
        assert observation.confidence == 0.8  # default
        assert observation.severity == "major"  # default
        assert observation.category == "general"  # default
        assert observation.status == "open"  # default
        assert observation.resolution is None
        assert observation.human_comment is None
        # UUID should be auto-generated
        assert len(observation.id) == 36  # UUID format

    def test_full_observation(self) -> None:
        """Test creating Observation with all fields."""
        observation = Observation(
            id="custom-id",
            job_id="job-456",
            agent="tables",
            source="human",
            visual_description="Table has merged header cells",
            markup_description="Simple markdown table without colspan",
            location=ObservationLocation(
                location_type="element",
                value="table.grades",
                page_num=5
            ),
            confidence=0.7,
            severity="critical",
            category="table_format",
            human_comment="Flagged during manual review"
        )

        assert observation.id == "custom-id"
        assert observation.source == "human"
        assert observation.severity == "critical"
        assert observation.category == "table_format"

    def test_auto_generated_id(self) -> None:
        """Test that ID is auto-generated if not provided."""
        obs1 = Observation(
            job_id="job-1",
            agent="analysis",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=1)
        )
        obs2 = Observation(
            job_id="job-2",
            agent="analysis",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=1)
        )

        # IDs should be unique
        assert obs1.id != obs2.id
        # IDs should be UUID format
        assert len(obs1.id) == 36
        assert len(obs2.id) == 36

    def test_confidence_bounds(self) -> None:
        """Test confidence must be between 0.0 and 1.0."""
        location = ObservationLocation(value="test", page_num=1)

        # Valid at boundaries
        obs_min = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=location,
            confidence=0.0
        )
        obs_max = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=location,
            confidence=1.0
        )
        assert obs_min.confidence == 0.0
        assert obs_max.confidence == 1.0

        # Invalid outside bounds
        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                confidence=1.5
            )

        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                confidence=-0.1
            )

    def test_severity_values(self) -> None:
        """Test valid severity values."""
        location = ObservationLocation(value="test", page_num=1)

        for severity in ["critical", "major", "minor"]:
            obs = Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                severity=severity
            )
            assert obs.severity == severity

    def test_invalid_severity(self) -> None:
        """Test invalid severity is rejected."""
        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(value="test", page_num=1),
                severity="invalid"  # type: ignore[arg-type]
            )

    def test_source_values(self) -> None:
        """Test valid source values."""
        location = ObservationLocation(value="test", page_num=1)

        for source in ["agent", "human"]:
            obs = Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                source=source
            )
            assert obs.source == source

    def test_invalid_source(self) -> None:
        """Test invalid source is rejected."""
        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=ObservationLocation(value="test", page_num=1),
                source="invalid"  # type: ignore[arg-type]
            )

    def test_json_serialization(self) -> None:
        """Test Observation serializes to JSON correctly."""
        observation = Observation(
            id="obs-123",
            job_id="job-456",
            agent="figures",
            visual_description="Image shows chart",
            markup_description="Empty alt text",
            location=ObservationLocation(value="figure area", page_num=2),
            category="alt_text",
            confidence=0.95
        )

        json_str = observation.model_dump_json()
        data = json.loads(json_str)

        assert data["id"] == "obs-123"
        assert data["job_id"] == "job-456"
        assert data["agent"] == "figures"
        assert data["category"] == "alt_text"
        assert data["confidence"] == 0.95
        assert data["status"] == "open"
        assert data["resolution"] is None
        assert data["location"]["page_num"] == 2

    def test_json_deserialization(self) -> None:
        """Test Observation can be deserialized from JSON."""
        json_data = {
            "id": "obs-789",
            "job_id": "job-101",
            "agent": "tables",
            "source": "agent",
            "visual_description": "Table visible",
            "markup_description": "Markdown table",
            "location": {
                "location_type": "element",
                "value": "table.data",
                "page_num": 3
            },
            "confidence": 0.85,
            "severity": "major",
            "category": "table_format",
            "status": "open",
            "resolution": None,
        }

        observation = Observation(**json_data)

        assert observation.id == "obs-789"
        assert observation.agent == "tables"
        assert observation.category == "table_format"
        assert observation.location.page_num == 3

    def test_affected_pages_field(self) -> None:
        """Test the affected_pages field for multi-page issues."""
        observation = Observation(
            job_id="job-123",
            agent="structure",
            visual_description="Heading structure issue",
            markup_description="Missing H2",
            location=ObservationLocation(value="section", page_num=1),
            affected_pages=[1, 2, 3, 4]
        )

        assert observation.affected_pages == [1, 2, 3, 4]

    def test_default_affected_pages(self) -> None:
        """Test affected_pages defaults to empty list."""
        observation = Observation(
            job_id="job-123",
            agent="figures",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=1)
        )

        assert observation.affected_pages == []
