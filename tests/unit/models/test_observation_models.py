"""Unit tests for observation models (Observation, ObservationLocation)."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from src.shared.models.observation import (
    VALID_OBSERVATION_TRANSITIONS,
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
        assert observation.route == "auto"  # default
        assert observation.status == "open"  # default
        assert observation.resolved_by is None
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
            route="manual",
            manual_reason="Complex merged cell structure needs human judgment",
            status="open",
            human_comment="Flagged during manual review"
        )

        assert observation.id == "custom-id"
        assert observation.source == "human"
        assert observation.severity == "critical"
        assert observation.route == "manual"
        assert observation.manual_reason is not None

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

        # Invalid values
        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                confidence=1.5
            )

    def test_severity_validation(self) -> None:
        """Test severity accepts only valid values."""
        location = ObservationLocation(value="test", page_num=1)

        for severity in ["critical", "major", "minor"]:
            obs = Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                severity=severity  # type: ignore[arg-type]
            )
            assert obs.severity == severity

        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                severity="moderate"  # type: ignore[arg-type]
            )

    def test_status_validation(self) -> None:
        """Test status accepts only valid values."""
        location = ObservationLocation(value="test", page_num=1)

        for status in ["open", "resolved", "wont_fix", "manual"]:
            obs = Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                status=status  # type: ignore[arg-type]
            )
            assert obs.status == status

    def test_source_validation(self) -> None:
        """Test source accepts only agent or human."""
        location = ObservationLocation(value="test", page_num=1)

        for source in ["agent", "human"]:
            obs = Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                source=source  # type: ignore[arg-type]
            )
            assert obs.source == source

        with pytest.raises(ValidationError):
            Observation(
                job_id="job-1",
                agent="test",
                visual_description="test",
                markup_description="test",
                location=location,
                source="automated"  # type: ignore[arg-type]
            )

    def test_created_at_default(self) -> None:
        """Test created_at is set to current UTC time by default."""
        before = datetime.now(UTC)
        obs = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(value="test", page_num=1)
        )
        after = datetime.now(UTC)

        assert before <= obs.created_at <= after

    def test_can_transition_to_valid(self) -> None:
        """Test can_transition_to returns True for valid transitions."""
        location = ObservationLocation(value="test", page_num=1)
        obs = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=location,
            status="open"
        )

        # From open, can transition to resolved, wont_fix, manual
        assert obs.can_transition_to("resolved") is True
        assert obs.can_transition_to("wont_fix") is True
        assert obs.can_transition_to("manual") is True

    def test_can_transition_to_invalid(self) -> None:
        """Test can_transition_to returns False for invalid transitions."""
        location = ObservationLocation(value="test", page_num=1)
        obs = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=location,
            status="resolved"  # Terminal state
        )

        # From resolved, cannot transition to anything
        assert obs.can_transition_to("open") is False
        assert obs.can_transition_to("manual") is False
        assert obs.can_transition_to("wont_fix") is False

    def test_manual_to_resolved_transition(self) -> None:
        """Test manual status can transition to resolved."""
        location = ObservationLocation(value="test", page_num=1)
        obs = Observation(
            job_id="job-1",
            agent="test",
            visual_description="test",
            markup_description="test",
            location=location,
            status="manual"
        )

        assert obs.can_transition_to("resolved") is True
        assert obs.can_transition_to("wont_fix") is True

    def test_json_serialization(self) -> None:
        """Test Observation serializes to JSON correctly."""
        obs = Observation(
            id="obs-123",
            job_id="job-456",
            agent="figures",
            visual_description="Flowchart image",
            markup_description="Empty alt text",
            location=ObservationLocation(
                location_type="element",
                value="img.flowchart",
                page_num=3
            ),
            confidence=0.9,
            severity="major"
        )

        json_str = obs.model_dump_json()
        data = json.loads(json_str)

        assert data["id"] == "obs-123"
        assert data["job_id"] == "job-456"
        assert data["agent"] == "figures"
        assert data["location"]["page_num"] == 3
        assert data["confidence"] == 0.9

    def test_json_round_trip(self) -> None:
        """Test Observation survives JSON round-trip."""
        original = Observation(
            job_id="job-789",
            agent="tables",
            visual_description="Complex table",
            markup_description="Simple table",
            location=ObservationLocation(
                location_type="range",
                value="50-75",
                page_num=5
            ),
            confidence=0.75,
            severity="critical",
            route="manual",
            manual_reason="Complex structure"
        )

        json_str = original.model_dump_json()
        restored = Observation.model_validate_json(json_str)

        assert restored.id == original.id
        assert restored.job_id == original.job_id
        assert restored.agent == original.agent
        assert restored.location.page_num == original.location.page_num
        assert restored.manual_reason == original.manual_reason


class TestObservationTransitions:
    """Tests for observation status transition rules."""

    def test_valid_transitions_from_open(self) -> None:
        """Test valid transitions from open status."""
        assert VALID_OBSERVATION_TRANSITIONS["open"] == [
            "resolved", "wont_fix", "manual"
        ]

    def test_terminal_states(self) -> None:
        """Test terminal states have no valid transitions."""
        assert VALID_OBSERVATION_TRANSITIONS["resolved"] == []
        assert VALID_OBSERVATION_TRANSITIONS["wont_fix"] == []

    def test_manual_can_be_resolved(self) -> None:
        """Test manual status can transition to resolved or wont_fix."""
        assert "resolved" in VALID_OBSERVATION_TRANSITIONS["manual"]
        assert "wont_fix" in VALID_OBSERVATION_TRANSITIONS["manual"]
