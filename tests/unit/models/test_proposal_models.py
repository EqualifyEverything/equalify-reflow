"""Unit tests for proposal models (Proposal, SearchReplaceDiff)."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from src.shared.models.proposal import (
    VALID_PROPOSAL_TRANSITIONS,
    Proposal,
    SearchReplaceDiff,
)


class TestSearchReplaceDiff:
    """Tests for SearchReplaceDiff model."""

    def test_valid_diff(self) -> None:
        """Test creating a valid SearchReplaceDiff."""
        diff = SearchReplaceDiff(
            search="![](figure-1.png)",
            replace="![Flowchart showing process](figure-1.png)"
        )

        assert diff.search == "![](figure-1.png)"
        assert diff.replace == "![Flowchart showing process](figure-1.png)"

    def test_empty_replace_for_deletion(self) -> None:
        """Test empty replace string is valid (for deletion)."""
        diff = SearchReplaceDiff(
            search="<!-- Remove this comment -->",
            replace=""
        )

        assert diff.search == "<!-- Remove this comment -->"
        assert diff.replace == ""

    def test_empty_search_rejected(self) -> None:
        """Test empty search string is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SearchReplaceDiff(search="", replace="new text")

        assert "search" in str(exc_info.value)

    def test_whitespace_only_search_rejected(self) -> None:
        """Test whitespace-only search is valid but not useful."""
        # Whitespace is technically valid (not empty)
        diff = SearchReplaceDiff(search="   ", replace="new text")
        assert diff.search == "   "

    def test_multiline_content(self) -> None:
        """Test multiline search and replace content."""
        diff = SearchReplaceDiff(
            search="## Heading\n\nParagraph text",
            replace="### Heading\n\nUpdated paragraph text"
        )

        assert "\n" in diff.search
        assert "\n" in diff.replace

    def test_json_serialization(self) -> None:
        """Test SearchReplaceDiff serializes to JSON correctly."""
        diff = SearchReplaceDiff(
            search="old text",
            replace="new text"
        )

        json_str = diff.model_dump_json()
        data = json.loads(json_str)

        assert data["search"] == "old text"
        assert data["replace"] == "new text"

    def test_special_characters(self) -> None:
        """Test handling of special characters in diff."""
        diff = SearchReplaceDiff(
            search='![Image "title"](path/to/image.png)',
            replace='![Image "updated title"](path/to/image.png)'
        )

        assert '"' in diff.search
        assert '"' in diff.replace


class TestProposal:
    """Tests for Proposal model."""

    def test_minimal_valid_proposal(self) -> None:
        """Test creating Proposal with required fields only."""
        proposal = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(
                search="old",
                replace="new"
            ),
            justification="Fixing accessibility issue"
        )

        assert proposal.job_id == "job-123"
        assert proposal.resolves == ["obs-1"]
        assert proposal.diff.search == "old"
        assert proposal.diff.replace == "new"
        assert proposal.justification == "Fixing accessibility issue"
        assert proposal.status == "pending"  # default
        assert proposal.route == "auto"  # default
        assert proposal.page_nums == []  # default
        assert proposal.estimated_impact == ""  # default
        # UUID should be auto-generated
        assert len(proposal.id) == 36

    def test_full_proposal(self) -> None:
        """Test creating Proposal with all fields."""
        reviewed_at = datetime.now(UTC)
        proposal = Proposal(
            id="proposal-456",
            job_id="job-789",
            resolves=["obs-1", "obs-2", "obs-3"],
            diff=SearchReplaceDiff(
                search="![](image.png)",
                replace="![Descriptive alt text](image.png)"
            ),
            justification="Combining related observations for image accessibility",
            page_nums=[3, 4],
            estimated_impact="Adds alt text to 3 images",
            route="auto",
            status="approved",
            reviewed_by="reviewer@example.com",
            reviewed_at=reviewed_at,
            review_notes="Looks good, approved as-is"
        )

        assert proposal.id == "proposal-456"
        assert len(proposal.resolves) == 3
        assert proposal.page_nums == [3, 4]
        assert proposal.estimated_impact == "Adds alt text to 3 images"
        assert proposal.status == "approved"
        assert proposal.reviewed_by == "reviewer@example.com"
        assert proposal.reviewed_at == reviewed_at
        assert proposal.review_notes == "Looks good, approved as-is"

    def test_auto_generated_id(self) -> None:
        """Test that ID is auto-generated if not provided."""
        prop1 = Proposal(
            job_id="job-1",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test"
        )
        prop2 = Proposal(
            job_id="job-2",
            resolves=["obs-2"],
            diff=SearchReplaceDiff(search="c", replace="d"),
            justification="test"
        )

        # IDs should be unique
        assert prop1.id != prop2.id
        # IDs should be UUID format
        assert len(prop1.id) == 36

    def test_resolves_can_be_empty_for_human_edits(self) -> None:
        """Test resolves can be empty for human-initiated edits."""
        # Human-initiated proposals don't resolve agent observations
        prop = Proposal(
            job_id="job-123",
            resolves=[],  # Empty list allowed for human edits
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="Human edit: fixing typo"
        )

        assert prop.resolves == []

    def test_justification_must_not_be_empty(self) -> None:
        """Test justification must not be empty."""
        with pytest.raises(ValidationError):
            Proposal(
                job_id="job-123",
                resolves=["obs-1"],
                diff=SearchReplaceDiff(search="a", replace="b"),
                justification=""
            )

    def test_status_validation(self) -> None:
        """Test status accepts only valid values."""
        for status in ["pending", "approved", "rejected", "applied", "failed"]:
            prop = Proposal(
                job_id="job-123",
                resolves=["obs-1"],
                diff=SearchReplaceDiff(search="a", replace="b"),
                justification="test",
                status=status  # type: ignore[arg-type]
            )
            assert prop.status == status

        with pytest.raises(ValidationError):
            Proposal(
                job_id="job-123",
                resolves=["obs-1"],
                diff=SearchReplaceDiff(search="a", replace="b"),
                justification="test",
                status="invalid"  # type: ignore[arg-type]
            )

    def test_route_validation(self) -> None:
        """Test route accepts only auto or manual."""
        for route in ["auto", "manual"]:
            prop = Proposal(
                job_id="job-123",
                resolves=["obs-1"],
                diff=SearchReplaceDiff(search="a", replace="b"),
                justification="test",
                route=route  # type: ignore[arg-type]
            )
            assert prop.route == route

    def test_created_at_default(self) -> None:
        """Test created_at is set to current UTC time by default."""
        before = datetime.now(UTC)
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test"
        )
        after = datetime.now(UTC)

        assert before <= prop.created_at <= after

    def test_can_transition_to_valid(self) -> None:
        """Test can_transition_to returns True for valid transitions."""
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="pending"
        )

        # From pending, can transition to approved or rejected
        assert prop.can_transition_to("approved") is True
        assert prop.can_transition_to("rejected") is True

    def test_can_transition_to_invalid(self) -> None:
        """Test can_transition_to returns False for invalid transitions."""
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="rejected"  # Terminal state
        )

        # From rejected, cannot transition to anything
        assert prop.can_transition_to("pending") is False
        assert prop.can_transition_to("approved") is False
        assert prop.can_transition_to("applied") is False

    def test_approved_to_applied_transition(self) -> None:
        """Test approved can transition to applied or failed."""
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="approved"
        )

        assert prop.can_transition_to("applied") is True
        assert prop.can_transition_to("failed") is True

    def test_failed_can_retry(self) -> None:
        """Test failed status can transition back to pending."""
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="failed"
        )

        assert prop.can_transition_to("pending") is True

    def test_failure_reason_field(self) -> None:
        """Test failure_reason field is set on failed proposals."""
        prop = Proposal(
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(search="a", replace="b"),
            justification="test",
            status="failed",
            failure_reason="Search text not found in document"
        )

        assert prop.failure_reason == "Search text not found in document"

    def test_json_serialization(self) -> None:
        """Test Proposal serializes to JSON correctly."""
        prop = Proposal(
            id="prop-123",
            job_id="job-456",
            resolves=["obs-1", "obs-2"],
            diff=SearchReplaceDiff(
                search="old text",
                replace="new text"
            ),
            justification="Improving accessibility",
            page_nums=[1, 2],
            estimated_impact="Fixes 2 issues"
        )

        json_str = prop.model_dump_json()
        data = json.loads(json_str)

        assert data["id"] == "prop-123"
        assert data["job_id"] == "job-456"
        assert data["resolves"] == ["obs-1", "obs-2"]
        assert data["diff"]["search"] == "old text"
        assert data["diff"]["replace"] == "new text"
        assert data["page_nums"] == [1, 2]

    def test_json_round_trip(self) -> None:
        """Test Proposal survives JSON round-trip."""
        original = Proposal(
            job_id="job-789",
            resolves=["obs-a", "obs-b", "obs-c"],
            diff=SearchReplaceDiff(
                search="## Wrong Heading",
                replace="### Correct Heading"
            ),
            justification="Fixing heading hierarchy",
            page_nums=[5, 6, 7],
            estimated_impact="Corrects heading levels on 3 pages",
            route="manual",
            status="approved",
            reviewed_by="admin@test.com",
            review_notes="Verified correct"
        )

        json_str = original.model_dump_json()
        restored = Proposal.model_validate_json(json_str)

        assert restored.id == original.id
        assert restored.job_id == original.job_id
        assert restored.resolves == original.resolves
        assert restored.diff.search == original.diff.search
        assert restored.diff.replace == original.diff.replace
        assert restored.page_nums == original.page_nums
        assert restored.reviewed_by == original.reviewed_by


class TestProposalTransitions:
    """Tests for proposal status transition rules."""

    def test_valid_transitions_from_pending(self) -> None:
        """Test valid transitions from pending status."""
        assert VALID_PROPOSAL_TRANSITIONS["pending"] == ["approved", "rejected"]

    def test_valid_transitions_from_approved(self) -> None:
        """Test valid transitions from approved status."""
        assert VALID_PROPOSAL_TRANSITIONS["approved"] == ["applied", "failed"]

    def test_terminal_states(self) -> None:
        """Test terminal states have no valid transitions."""
        assert VALID_PROPOSAL_TRANSITIONS["rejected"] == []
        assert VALID_PROPOSAL_TRANSITIONS["applied"] == []

    def test_failed_can_be_retried(self) -> None:
        """Test failed status can transition to pending for retry."""
        assert VALID_PROPOSAL_TRANSITIONS["failed"] == ["pending"]
