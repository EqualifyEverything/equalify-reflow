"""Tests for round-tracking events."""

from __future__ import annotations

import pytest

from src.agents.events import (
    CriticAnalysisCompleteEvent,
    ConvergenceEvent,
    RoundCompleteEvent,
    RoundStartedEvent,
)


@pytest.mark.unit
class TestRoundStartedEvent:
    """Tests for RoundStartedEvent."""

    def test_event_type(self):
        """Test event type property."""
        event = RoundStartedEvent(
            document_id="test-doc",
            round_number=1,
        )
        assert event.event_type == "round:started"

    def test_with_previous_quality(self):
        """Test event with previous quality score."""
        event = RoundStartedEvent(
            document_id="test-doc",
            round_number=2,
            previous_quality=0.75,
        )
        assert event.round_number == 2
        assert event.previous_quality == 0.75

    def test_default_values(self):
        """Test event with default values."""
        event = RoundStartedEvent(
            document_id="test-doc",
            round_number=1,
        )
        assert event.previous_quality == 0.0
        assert event.max_rounds == 1

    def test_with_max_rounds(self):
        """Test event with max rounds configuration."""
        event = RoundStartedEvent(
            document_id="test-doc",
            round_number=1,
            max_rounds=5,
        )
        assert event.max_rounds == 5

    def test_serialization(self):
        """Test event can be serialized to JSON."""
        event = RoundStartedEvent(
            document_id="test-doc",
            round_number=3,
            previous_quality=0.65,
            max_rounds=4,
        )
        sse_data = event.to_sse()
        assert sse_data["event"] == "round:started"
        assert "data" in sse_data


@pytest.mark.unit
class TestCriticAnalysisCompleteEvent:
    """Tests for CriticAnalysisCompleteEvent."""

    def test_event_type(self):
        """Test event type property."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=2,
        )
        assert event.event_type == "critic:complete"

    def test_with_issues(self):
        """Test event with issue counts."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=2,
            issues_found=5,
            critical_count=1,
            quality_score=0.70,
            ready_for_output=False,
        )
        assert event.issues_found == 5
        assert event.critical_count == 1
        assert event.quality_score == 0.70
        assert event.ready_for_output is False

    def test_default_values(self):
        """Test event with default values."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=1,
        )
        assert event.issues_found == 0
        assert event.critical_count == 0
        assert event.major_count == 0
        assert event.quality_score == 0.0
        assert event.ready_for_output is False
        assert event.summary == ""

    def test_with_major_count(self):
        """Test event with major count."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=2,
            issues_found=10,
            critical_count=2,
            major_count=5,
            quality_score=0.65,
        )
        assert event.major_count == 5

    def test_ready_for_output_true(self):
        """Test event when marked ready for output."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=1,
            issues_found=0,
            quality_score=0.95,
            ready_for_output=True,
        )
        assert event.ready_for_output is True
        assert event.issues_found == 0

    def test_with_summary(self):
        """Test event with summary text."""
        summary = "Document quality improved significantly"
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=2,
            summary=summary,
        )
        assert event.summary == summary

    def test_serialization(self):
        """Test event can be serialized to JSON."""
        event = CriticAnalysisCompleteEvent(
            document_id="test-doc",
            round_number=2,
            issues_found=3,
            critical_count=1,
            quality_score=0.75,
        )
        sse_data = event.to_sse()
        assert sse_data["event"] == "critic:complete"
        assert "data" in sse_data


@pytest.mark.unit
class TestRoundCompleteEvent:
    """Tests for RoundCompleteEvent."""

    def test_event_type(self):
        """Test event type property."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=2,
        )
        assert event.event_type == "round:complete"

    def test_with_stats(self):
        """Test event with execution statistics."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=2,
            jobs_executed=3,
            edits_applied=5,
            quality_score=0.85,
            quality_delta=0.15,
        )
        assert event.jobs_executed == 3
        assert event.edits_applied == 5
        assert event.quality_score == 0.85
        assert event.quality_delta == 0.15

    def test_default_values(self):
        """Test event with default values."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=1,
        )
        assert event.jobs_executed == 0
        assert event.edits_applied == 0
        assert event.quality_score == 0.0
        assert event.quality_delta == 0.0
        assert event.duration_ms == 0

    def test_with_duration(self):
        """Test event with duration."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=2,
            jobs_executed=2,
            edits_applied=3,
            quality_score=0.80,
            quality_delta=0.10,
            duration_ms=5000,
        )
        assert event.duration_ms == 5000

    def test_negative_quality_delta(self):
        """Test event with negative quality delta (regression)."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=3,
            jobs_executed=1,
            edits_applied=2,
            quality_score=0.70,
            quality_delta=-0.05,
        )
        assert event.quality_delta == -0.05

    def test_zero_edits_applied(self):
        """Test event when no edits were applied."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=2,
            jobs_executed=5,
            edits_applied=0,
            quality_score=0.75,
            quality_delta=0.0,
        )
        assert event.edits_applied == 0

    def test_serialization(self):
        """Test event can be serialized to JSON."""
        event = RoundCompleteEvent(
            document_id="test-doc",
            round_number=2,
            jobs_executed=3,
            edits_applied=5,
            quality_score=0.85,
            quality_delta=0.15,
        )
        sse_data = event.to_sse()
        assert sse_data["event"] == "round:complete"
        assert "data" in sse_data


@pytest.mark.unit
class TestConvergenceEvent:
    """Tests for ConvergenceEvent."""

    def test_event_type(self):
        """Test event type property."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="quality_threshold_met",
            total_rounds=3,
        )
        assert event.event_type == "convergence:reached"

    def test_with_quality(self):
        """Test event with final quality score."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="max_rounds_reached",
            total_rounds=5,
            final_quality=0.90,
        )
        assert event.reason == "max_rounds_reached"
        assert event.total_rounds == 5
        assert event.final_quality == 0.90

    def test_all_convergence_reasons(self):
        """Test creating events with all convergence reasons."""
        reasons = [
            "max_rounds_reached",
            "quality_threshold_met",
            "no_improvement",
            "no_issues_found",
            "critic_marked_ready",
            "error",
        ]
        for reason in reasons:
            event = ConvergenceEvent(
                document_id="test",
                reason=reason,
                total_rounds=2,
            )
            assert event.reason == reason

    def test_default_values(self):
        """Test event with default values."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="quality_threshold_met",
            total_rounds=1,
        )
        assert event.final_quality == 0.0
        assert event.total_edits == 0
        assert event.total_duration_ms == 0

    def test_with_total_edits(self):
        """Test event with total edits across all rounds."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="quality_threshold_met",
            total_rounds=3,
            total_edits=15,
        )
        assert event.total_edits == 15

    def test_with_total_duration(self):
        """Test event with total processing time."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="max_rounds_reached",
            total_rounds=4,
            total_duration_ms=30000,
        )
        assert event.total_duration_ms == 30000

    def test_max_rounds_reached_reason(self):
        """Test convergence due to max rounds limit."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="max_rounds_reached",
            total_rounds=5,
            final_quality=0.82,
            total_edits=20,
        )
        assert event.reason == "max_rounds_reached"
        assert event.total_rounds == 5

    def test_no_improvement_reason(self):
        """Test convergence due to no improvement."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="no_improvement",
            total_rounds=2,
            final_quality=0.75,
        )
        assert event.reason == "no_improvement"

    def test_quality_threshold_met_reason(self):
        """Test convergence due to quality threshold."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="quality_threshold_met",
            total_rounds=1,
            final_quality=0.95,
        )
        assert event.reason == "quality_threshold_met"
        assert event.final_quality == 0.95

    def test_no_issues_found_reason(self):
        """Test convergence due to no issues found."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="no_issues_found",
            total_rounds=1,
            final_quality=0.90,
        )
        assert event.reason == "no_issues_found"

    def test_critic_marked_ready_reason(self):
        """Test convergence when critic marked ready."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="critic_marked_ready",
            total_rounds=2,
            final_quality=0.88,
        )
        assert event.reason == "critic_marked_ready"

    def test_error_reason(self):
        """Test convergence due to error."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="error",
            total_rounds=1,
            final_quality=0.50,
        )
        assert event.reason == "error"

    def test_complete_convergence_event(self):
        """Test convergence event with all fields populated."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="quality_threshold_met",
            total_rounds=3,
            final_quality=0.92,
            total_edits=12,
            total_duration_ms=15000,
        )
        assert event.document_id == "test-doc"
        assert event.reason == "quality_threshold_met"
        assert event.total_rounds == 3
        assert event.final_quality == 0.92
        assert event.total_edits == 12
        assert event.total_duration_ms == 15000

    def test_serialization(self):
        """Test event can be serialized to JSON."""
        event = ConvergenceEvent(
            document_id="test-doc",
            reason="max_rounds_reached",
            total_rounds=5,
            final_quality=0.90,
            total_edits=20,
            total_duration_ms=25000,
        )
        sse_data = event.to_sse()
        assert sse_data["event"] == "convergence:reached"
        assert "data" in sse_data
