"""Tests for AgentTrace and AgentResult models."""

from datetime import UTC, datetime

import pytest
from src.shared.models.agent_trace import AgentResult, AgentTrace
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation


class TestAgentTrace:
    """Tests for the AgentTrace model."""

    def test_create_agent_trace(self):
        """Test creating an agent trace."""
        now = datetime.now(UTC)
        trace = AgentTrace(
            agent_name="figures",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Processed 3 images. 2 auto-corrected.",
            confidence=0.88,
            cost_cents=12.5,
            time_seconds=25.0,
            iterations=1,
            started_at=now,
            completed_at=now,
        )

        assert trace.agent_name == "figures"
        assert trace.confidence == 0.88
        assert trace.cost_cents == 12.5

    def test_valid_agent_names(self):
        """Test that only valid agent names are accepted."""
        now = datetime.now(UTC)

        for name in ["figures", "tables", "structure", "typography"]:
            trace = AgentTrace(
                agent_name=name,
                reasoning_summary="test",
                confidence=0.9,
                started_at=now,
                completed_at=now,
            )
            assert trace.agent_name == name

    def test_with_observations(self):
        """Test trace with observations."""
        obs = Observation(
            job_id="job-123",
            agent="figures",
            visual_description="Image shows chart",
            markup_description="Empty alt text",
            location=ObservationLocation(value="img", page_num=1),
        )

        now = datetime.now(UTC)
        trace = AgentTrace(
            agent_name="figures",
            observations=[obs],
            reasoning_summary="Found 1 issue",
            confidence=0.9,
            started_at=now,
            completed_at=now,
        )

        assert len(trace.observations) == 1

    def test_with_auto_corrections(self):
        """Test trace with auto corrections."""
        correction = AutoCorrection(
            observation_id="obs-1",
            search="old",
            replace="new",
            justification="test",
            confidence=0.98,
            agent="figures",
        )

        now = datetime.now(UTC)
        trace = AgentTrace(
            agent_name="figures",
            auto_corrections=[correction],
            reasoning_summary="Applied 1 correction",
            confidence=0.9,
            started_at=now,
            completed_at=now,
        )

        assert len(trace.auto_corrections) == 1


class TestAgentResult:
    """Tests for the AgentResult model."""

    def test_create_agent_result(self):
        """Test creating an agent result."""
        result = AgentResult(
            agent_name="figures",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Processed 3 images",
            confidence=0.85,
        )

        assert result.agent_name == "figures"
        assert result.confidence == 0.85
        assert result.enhanced_content is None

    def test_with_enhanced_content(self):
        """Test agent result with enhanced content."""
        result = AgentResult(
            agent_name="figures",
            reasoning_summary="Generated alt text",
            confidence=0.9,
            enhanced_content={
                "fig-1": "![Chart showing growth](fig-1.png)",
                "fig-2": "![Table diagram](fig-2.png)",
            },
        )

        assert result.enhanced_content is not None
        assert "fig-1" in result.enhanced_content

    def test_to_trace_conversion(self):
        """Test converting AgentResult to AgentTrace."""
        result = AgentResult(
            agent_name="figures",
            reasoning_summary="test",
            confidence=0.9,
            cost_cents=5.0,
            time_seconds=10.0,
            iterations=2,
        )

        started = datetime.now(UTC)
        completed = datetime.now(UTC)

        trace = result.to_trace(started_at=started, completed_at=completed)

        assert isinstance(trace, AgentTrace)
        assert trace.agent_name == "figures"
        assert trace.confidence == 0.9
        assert trace.cost_cents == 5.0
        assert trace.time_seconds == 10.0
        assert trace.iterations == 2
        assert trace.started_at == started
        assert trace.completed_at == completed

    def test_to_trace_invalid_agent_raises(self):
        """Test that to_trace raises for invalid agent name."""
        result = AgentResult(
            agent_name="invalid_agent",
            reasoning_summary="test",
            confidence=0.9,
        )

        with pytest.raises(ValueError, match="Invalid agent_name"):
            result.to_trace(
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )

    def test_json_serialization(self):
        """Test JSON serialization."""
        result = AgentResult(
            agent_name="tables",
            reasoning_summary="Processed tables",
            confidence=0.85,
            enhanced_content={"tbl-1": "enhanced table"},
        )

        json_str = result.model_dump_json()
        restored = AgentResult.model_validate_json(json_str)

        assert restored.agent_name == result.agent_name
        assert restored.enhanced_content == result.enhanced_content
