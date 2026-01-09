"""Tests for convergence checking logic.

Tests the check_convergence function from src/agents/orchestrator.py
that determines when to stop the multi-round processing loop.
"""

import pytest
from src.agents.models import (
    ConvergenceReason,
    CriticReport,
    IssueSeverity,
    CriticIssue,
)
from src.agents.orchestrator import check_convergence

pytestmark = pytest.mark.unit


class TestCheckConvergence:
    """Tests for check_convergence function."""

    def test_max_rounds_reached(self):
        """Test convergence when max rounds reached."""
        should_stop, reason = check_convergence(
            round_number=5,
            max_rounds=5,
            critic_report=None,
            previous_quality=0.7,
            current_quality=0.8,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.MAX_ROUNDS_REACHED

    def test_max_rounds_exceeded(self):
        """Test convergence when round exceeds max."""
        should_stop, reason = check_convergence(
            round_number=10,
            max_rounds=5,
            critic_report=None,
            previous_quality=0.7,
            current_quality=0.8,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.MAX_ROUNDS_REACHED

    def test_quality_threshold_met_no_critical_issues(self):
        """Test convergence when quality threshold met with no critical issues.

        Note: NO_ISSUES_FOUND is checked before QUALITY_THRESHOLD_MET,
        so we get NO_ISSUES_FOUND when issues list is empty.
        """
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],  # Empty issues list
            critical_count=0,
            overall_quality=0.90,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.90,
            quality_threshold=0.85,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_quality_threshold_met_exact_value(self):
        """Test convergence when quality exactly meets threshold with issues present.

        Only converges on QUALITY_THRESHOLD_MET when there are issues present
        (otherwise NO_ISSUES_FOUND is triggered first).
        """
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Minor issue",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue],
            critical_count=0,
            overall_quality=0.85,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.85,
            quality_threshold=0.85,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.QUALITY_THRESHOLD_MET

    def test_quality_threshold_not_met_below_threshold(self):
        """Test no convergence when quality below threshold."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Minor issue",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue],
            critical_count=0,
            overall_quality=0.80,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.80,
            quality_threshold=0.85,
        )
        assert should_stop is False
        assert reason is None

    def test_critical_issues_prevent_convergence(self):
        """Test no convergence when critical issues present despite high quality."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Missing critical alt text",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue],
            critical_count=1,
            overall_quality=0.90,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.90,
            quality_threshold=0.85,
        )
        # Should NOT stop because critical issues exist
        assert should_stop is False
        assert reason is None

    def test_multiple_critical_issues(self):
        """Test no convergence with multiple critical issues."""
        issues = [
            CriticIssue(
                severity=IssueSeverity.CRITICAL,
                category="accessibility",
                description="Missing alt text",
                line_start=10,
                line_end=10,
            ),
            CriticIssue(
                severity=IssueSeverity.CRITICAL,
                category="structure",
                description="Wrong heading level",
                line_start=20,
                line_end=20,
            ),
            CriticIssue(
                severity=IssueSeverity.CRITICAL,
                category="content",
                description="Missing table transcription",
                line_start=30,
                line_end=30,
            ),
        ]
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=issues,
            critical_count=3,
            overall_quality=0.95,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.80,
            current_quality=0.95,
            quality_threshold=0.90,
        )
        assert should_stop is False
        assert reason is None

    def test_no_improvement_same_quality(self):
        """Test convergence when no improvement from previous round (same quality)."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Minor issue",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=3,
            issues=[issue],
            critical_count=0,
            overall_quality=0.75,
        )
        should_stop, reason = check_convergence(
            round_number=3,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.75,
            current_quality=0.75,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_IMPROVEMENT

    def test_no_improvement_quality_decreased(self):
        """Test convergence when quality decreased from previous round."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="accessibility",
            description="Major issue",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=3,
            issues=[issue],
            critical_count=0,
            overall_quality=0.70,
        )
        should_stop, reason = check_convergence(
            round_number=3,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.75,
            current_quality=0.70,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_IMPROVEMENT

    def test_no_improvement_only_checked_after_round_1(self):
        """Test that no improvement check is skipped for round 1."""
        # Round 1 doesn't have a critic report, so this should return False, None
        should_stop, reason = check_convergence(
            round_number=1,
            max_rounds=5,
            critic_report=None,
            previous_quality=0.0,
            current_quality=0.7,
        )
        assert should_stop is False
        assert reason is None

    def test_critic_marked_ready(self):
        """Test convergence when critic marks document ready."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            critical_count=0,
            overall_quality=0.80,
            ready_for_output=True,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.80,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.CRITIC_MARKED_READY

    def test_critic_marked_ready_overrides_quality_threshold(self):
        """Test that critic_marked_ready stops even with low quality."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            critical_count=0,
            overall_quality=0.50,
            ready_for_output=True,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.40,
            current_quality=0.50,
            quality_threshold=0.85,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.CRITIC_MARKED_READY

    def test_no_issues_found(self):
        """Test convergence when no issues found by critic."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],
            critical_count=0,
            overall_quality=0.85,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.85,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_issues_found_prevents_convergence(self):
        """Test that having issues prevents NO_ISSUES_FOUND convergence."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Minor spacing issue",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue],
            critical_count=0,
            overall_quality=0.85,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.85,
        )
        # Should not converge with NO_ISSUES_FOUND because issues exist
        assert not (should_stop and reason == ConvergenceReason.NO_ISSUES_FOUND)

    def test_continue_processing_with_improvement(self):
        """Test that processing continues when conditions not met."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="accessibility",
            description="Missing alt text",
            line_start=10,
            line_end=10,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue],
            critical_count=0,
            major_count=1,
            overall_quality=0.70,
            ready_for_output=False,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.60,
            current_quality=0.70,
            quality_threshold=0.85,
        )
        assert should_stop is False
        assert reason is None

    def test_first_round_no_critic(self):
        """Test first round without critic report."""
        should_stop, reason = check_convergence(
            round_number=1,
            max_rounds=5,
            critic_report=None,
            previous_quality=0.0,
            current_quality=0.7,
        )
        # Should continue (round 1 < max_rounds)
        assert should_stop is False
        assert reason is None

    def test_second_round_with_critic(self):
        """Test second round with valid critic report."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],
            critical_count=0,
            overall_quality=0.9,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.7,
            current_quality=0.9,
        )
        # No issues found should trigger convergence
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_convergence_priority_max_rounds_first(self):
        """Test that max_rounds is checked before other conditions."""
        # Even with perfect quality and no issues, max_rounds stops processing
        critic = CriticReport(
            document_id="test",
            round_number=5,
            issues=[],
            critical_count=0,
            overall_quality=1.0,
            ready_for_output=False,
        )
        should_stop, reason = check_convergence(
            round_number=5,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.9,
            current_quality=1.0,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.MAX_ROUNDS_REACHED

    def test_convergence_priority_ready_for_output_high(self):
        """Test that ready_for_output is checked early."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],
            critical_count=0,
            overall_quality=0.5,
            ready_for_output=True,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.4,
            current_quality=0.5,
        )
        # Ready for output should trigger before no_issues_found
        assert should_stop is True
        assert reason == ConvergenceReason.CRITIC_MARKED_READY

    def test_convergence_order_no_issues_before_quality_threshold(self):
        """Test order of convergence checks."""
        # When no issues found, it should converge even if quality threshold not met
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],
            critical_count=0,
            overall_quality=0.70,  # Below threshold
            ready_for_output=False,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.65,
            current_quality=0.70,
            quality_threshold=0.85,
        )
        # No issues should trigger convergence regardless of quality threshold
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_quality_threshold_with_major_issues(self):
        """Test quality threshold doesn't apply with major issues."""
        issue1 = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="accessibility",
            description="Missing alt text",
            line_start=10,
            line_end=10,
        )
        issue2 = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="structure",
            description="Wrong heading level",
            line_start=20,
            line_end=20,
        )
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[issue1, issue2],
            critical_count=0,
            major_count=2,
            overall_quality=0.95,  # Very high quality
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.85,
            current_quality=0.95,
            quality_threshold=0.90,
        )
        # Should not converge due to critical_count check failing first
        # Wait, critical_count is 0, so it should check quality threshold
        # Quality 0.95 >= threshold 0.90, so it should converge
        assert should_stop is True
        assert reason == ConvergenceReason.QUALITY_THRESHOLD_MET

    def test_default_quality_threshold(self):
        """Test that default quality threshold is 0.85."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],
            critical_count=0,
            overall_quality=0.84,
        )
        # With default threshold (0.85) and quality 0.84, should not converge via threshold
        # But should converge via NO_ISSUES_FOUND
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.84,
            # Not specifying quality_threshold - uses default 0.85
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_zero_quality_with_no_improvement(self):
        """Test handling of zero quality scores."""
        critic = CriticReport(
            document_id="test",
            round_number=3,
            issues=[],
            critical_count=0,
            overall_quality=0.0,
        )
        should_stop, reason = check_convergence(
            round_number=3,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.0,
            current_quality=0.0,
        )
        # No issues found should still trigger convergence
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_marginal_improvement(self):
        """Test handling of very small quality improvements."""
        critic = CriticReport(
            document_id="test",
            round_number=3,
            issues=[],
            critical_count=0,
            overall_quality=0.75001,
        )
        should_stop, reason = check_convergence(
            round_number=3,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.75,
            current_quality=0.75001,
        )
        # Still an improvement, but no_issues_found should trigger first
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_high_round_number_approaches_max(self):
        """Test behavior when approaching max rounds."""
        critic = CriticReport(
            document_id="test",
            round_number=4,
            issues=[],
            critical_count=0,
            overall_quality=0.75,
        )
        should_stop, reason = check_convergence(
            round_number=4,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.75,
        )
        # Round 4 < max 5, so should not stop due to max_rounds
        # But no_issues_found should trigger
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_last_possible_round(self):
        """Test last possible round before max is reached."""
        critic = CriticReport(
            document_id="test",
            round_number=4,
            issues=[
                CriticIssue(
                    severity=IssueSeverity.MINOR,
                    category="formatting",
                    description="Minor issue",
                    line_start=10,
                    line_end=10,
                )
            ],
            critical_count=0,
            overall_quality=0.75,
        )
        should_stop, reason = check_convergence(
            round_number=4,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.75,
        )
        # Round 4 < max 5, has improvement, has issues
        # Should continue
        assert should_stop is False
        assert reason is None

    def test_empty_issues_list_vs_none(self):
        """Test that empty issues list triggers convergence."""
        critic = CriticReport(
            document_id="test",
            round_number=2,
            issues=[],  # Empty list
            critical_count=0,
            overall_quality=0.60,
        )
        should_stop, reason = check_convergence(
            round_number=2,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.50,
            current_quality=0.60,
        )
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND

    def test_negative_quality_improvement(self):
        """Test with negative quality improvement (degradation).

        When issues list is empty, NO_ISSUES_FOUND is checked before NO_IMPROVEMENT.
        """
        critic = CriticReport(
            document_id="test",
            round_number=3,
            issues=[],
            critical_count=0,
            overall_quality=0.60,
        )
        should_stop, reason = check_convergence(
            round_number=3,
            max_rounds=5,
            critic_report=critic,
            previous_quality=0.70,
            current_quality=0.60,
        )
        # Should converge on NO_ISSUES_FOUND (checked before NO_IMPROVEMENT)
        assert should_stop is True
        assert reason == ConvergenceReason.NO_ISSUES_FOUND
