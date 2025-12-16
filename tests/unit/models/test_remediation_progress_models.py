"""Unit tests for remediation progress models."""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from src.shared.models.remediation_progress import (
    VALID_SUBSTATUSES,
    RemediationProgress,
    SubstatusType,
)


class TestRemediationProgress:
    """Tests for RemediationProgress model."""

    def test_default_values(self) -> None:
        """Test RemediationProgress has correct default values."""
        progress = RemediationProgress()

        assert progress.substatus == "analyzing"
        assert progress.observation_count == 0
        assert progress.proposal_count == 0
        assert progress.pending_proposals == 0
        assert progress.approved_proposals == 0
        assert progress.rejected_proposals == 0
        assert progress.applied_proposals == 0
        assert progress.manual_observations == 0
        assert progress.analysis_model is None
        assert progress.extraction_model is None
        assert progress.analysis_completed_at is None
        assert progress.extraction_completed_at is None
        assert progress.review_started_at is None

    def test_full_progress(self) -> None:
        """Test RemediationProgress with all fields set."""
        now = datetime.now(UTC)
        progress = RemediationProgress(
            substatus="awaiting_review",
            observation_count=12,
            proposal_count=8,
            pending_proposals=6,
            approved_proposals=1,
            rejected_proposals=1,
            applied_proposals=0,
            manual_observations=2,
            analysis_model="claude-sonnet-4-5",
            extraction_model="claude-haiku-4-5",
            analysis_completed_at=now,
            extraction_completed_at=now,
            review_started_at=now
        )

        assert progress.substatus == "awaiting_review"
        assert progress.observation_count == 12
        assert progress.proposal_count == 8
        assert progress.pending_proposals == 6
        assert progress.manual_observations == 2
        assert progress.analysis_model == "claude-sonnet-4-5"
        assert progress.extraction_model == "claude-haiku-4-5"

    def test_substatus_validation(self) -> None:
        """Test substatus accepts only valid values."""
        valid_substatuses: list[SubstatusType] = [
            "analyzing",
            "extracting",
            "specializing",
            "consolidating",
            "awaiting_review",
            "applying",
        ]

        for substatus in valid_substatuses:
            progress = RemediationProgress(substatus=substatus)
            assert progress.substatus == substatus

        with pytest.raises(ValidationError):
            RemediationProgress(substatus="invalid_status")  # type: ignore[arg-type]

    def test_count_fields_non_negative(self) -> None:
        """Test count fields must be >= 0."""
        count_fields = [
            "observation_count",
            "proposal_count",
            "pending_proposals",
            "approved_proposals",
            "rejected_proposals",
            "applied_proposals",
            "manual_observations",
        ]

        for field in count_fields:
            # Valid at 0
            progress = RemediationProgress(**{field: 0})
            assert getattr(progress, field) == 0

            # Invalid below 0
            with pytest.raises(ValidationError):
                RemediationProgress(**{field: -1})

    def test_is_valid_substatus_transition_processing(self) -> None:
        """Test is_valid_substatus_transition for processing status."""
        progress = RemediationProgress()

        # All valid substatuses for "processing"
        for substatus in VALID_SUBSTATUSES["processing"]:
            assert progress.is_valid_substatus_transition("processing", substatus) is True

        # Invalid substatus
        assert progress.is_valid_substatus_transition("processing", "invalid") is False

    def test_is_valid_substatus_transition_other_status(self) -> None:
        """Test is_valid_substatus_transition for non-processing status."""
        progress = RemediationProgress()

        # No valid substatuses for non-processing statuses
        assert progress.is_valid_substatus_transition("completed", "analyzing") is False
        assert progress.is_valid_substatus_transition("failed", "extracting") is False

    def test_mark_analysis_complete(self) -> None:
        """Test mark_analysis_complete updates fields correctly."""
        progress = RemediationProgress(substatus="analyzing")

        before = datetime.now(UTC)
        result = progress.mark_analysis_complete("claude-sonnet-4-5")
        after = datetime.now(UTC)

        assert result is progress  # Returns self
        assert progress.substatus == "extracting"
        assert progress.analysis_model == "claude-sonnet-4-5"
        assert progress.analysis_completed_at is not None
        assert before <= progress.analysis_completed_at <= after

    def test_mark_extraction_complete(self) -> None:
        """Test mark_extraction_complete updates fields correctly."""
        progress = RemediationProgress(substatus="extracting")

        before = datetime.now(UTC)
        result = progress.mark_extraction_complete("claude-haiku-4-5")
        after = datetime.now(UTC)

        assert result is progress
        assert progress.substatus == "specializing"
        assert progress.extraction_model == "claude-haiku-4-5"
        assert progress.extraction_completed_at is not None
        assert before <= progress.extraction_completed_at <= after

    def test_mark_ready_for_review(self) -> None:
        """Test mark_ready_for_review updates fields correctly."""
        progress = RemediationProgress(substatus="consolidating")

        before = datetime.now(UTC)
        result = progress.mark_ready_for_review()
        after = datetime.now(UTC)

        assert result is progress
        assert progress.substatus == "awaiting_review"
        assert progress.review_started_at is not None
        assert before <= progress.review_started_at <= after

    def test_update_counts_from_lists(self) -> None:
        """Test update_counts_from_lists calculates counts correctly."""
        progress = RemediationProgress()

        # Create mock observations
        mock_observations = [
            MagicMock(route="auto"),
            MagicMock(route="auto"),
            MagicMock(route="manual"),
            MagicMock(route="manual"),
            MagicMock(route="auto"),
        ]

        # Create mock proposals
        mock_proposals = [
            MagicMock(status="pending"),
            MagicMock(status="pending"),
            MagicMock(status="approved"),
            MagicMock(status="rejected"),
            MagicMock(status="applied"),
        ]

        result = progress.update_counts_from_lists(mock_observations, mock_proposals)

        assert result is progress
        assert progress.observation_count == 5
        assert progress.manual_observations == 2
        assert progress.proposal_count == 5
        assert progress.pending_proposals == 2
        assert progress.approved_proposals == 1
        assert progress.rejected_proposals == 1
        assert progress.applied_proposals == 1

    def test_update_counts_empty_lists(self) -> None:
        """Test update_counts_from_lists with empty lists."""
        progress = RemediationProgress(
            observation_count=10,  # Pre-existing values
            proposal_count=5
        )

        result = progress.update_counts_from_lists([], [])

        assert result is progress
        assert progress.observation_count == 0
        assert progress.proposal_count == 0
        assert progress.pending_proposals == 0
        assert progress.manual_observations == 0

    def test_json_serialization(self) -> None:
        """Test RemediationProgress serializes to JSON correctly."""
        now = datetime.now(UTC)
        progress = RemediationProgress(
            substatus="awaiting_review",
            observation_count=12,
            proposal_count=8,
            pending_proposals=6,
            analysis_model="claude-sonnet-4-5",
            analysis_completed_at=now
        )

        json_str = progress.model_dump_json()
        data = json.loads(json_str)

        assert data["substatus"] == "awaiting_review"
        assert data["observation_count"] == 12
        assert data["proposal_count"] == 8
        assert data["pending_proposals"] == 6
        assert data["analysis_model"] == "claude-sonnet-4-5"

    def test_json_round_trip(self) -> None:
        """Test RemediationProgress survives JSON round-trip."""
        now = datetime.now(UTC)
        original = RemediationProgress(
            substatus="applying",
            observation_count=15,
            proposal_count=10,
            pending_proposals=0,
            approved_proposals=8,
            rejected_proposals=2,
            applied_proposals=5,
            manual_observations=3,
            analysis_model="claude-sonnet-4-5",
            extraction_model="claude-haiku-4-5",
            analysis_completed_at=now,
            extraction_completed_at=now,
            review_started_at=now
        )

        json_str = original.model_dump_json()
        restored = RemediationProgress.model_validate_json(json_str)

        assert restored.substatus == original.substatus
        assert restored.observation_count == original.observation_count
        assert restored.proposal_count == original.proposal_count
        assert restored.approved_proposals == original.approved_proposals
        assert restored.analysis_model == original.analysis_model


class TestValidSubstatuses:
    """Tests for VALID_SUBSTATUSES constant."""

    def test_processing_substatuses(self) -> None:
        """Test processing status has all expected substatuses."""
        expected = [
            "analyzing",
            "extracting",
            "specializing",
            "consolidating",
            "awaiting_review",
            "applying",
        ]
        assert VALID_SUBSTATUSES["processing"] == expected

    def test_substatus_order(self) -> None:
        """Test substatuses are in logical order."""
        substatuses = VALID_SUBSTATUSES["processing"]

        # These should be in pipeline order
        assert substatuses.index("analyzing") < substatuses.index("extracting")
        assert substatuses.index("extracting") < substatuses.index("specializing")
        assert substatuses.index("specializing") < substatuses.index("consolidating")
        assert substatuses.index("consolidating") < substatuses.index("awaiting_review")
        assert substatuses.index("awaiting_review") < substatuses.index("applying")
