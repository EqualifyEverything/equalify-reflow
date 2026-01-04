"""Unit tests for V5 Recovery Phase.

Tests for:
- RecoveryAttemptStatus, IssueCategory, ProcessingStatus enums
- categorize_issues() function
- should_attempt_recovery() function
- calculate_pass_threshold() function
- determine_final_status() function
"""

import pytest

from src.agents.v5.models import (
    IssueCategory,
    PageVerification,
    ProcessingStatus,
    RecoveryAttemptStatus,
    RecoveryReport,
    VerificationReport,
)
from src.agents.v5.recovery import (
    calculate_pass_threshold,
    categorize_issues,
    determine_final_status,
    should_attempt_recovery,
)


# =============================================================================
# Enum Tests
# =============================================================================


class TestRecoveryAttemptStatusEnum:
    """Tests for RecoveryAttemptStatus enum."""

    def test_pending_value(self):
        """Test PENDING status value."""
        assert RecoveryAttemptStatus.PENDING.value == "pending"

    def test_in_progress_value(self):
        """Test IN_PROGRESS status value."""
        assert RecoveryAttemptStatus.IN_PROGRESS.value == "in_progress"

    def test_succeeded_value(self):
        """Test SUCCEEDED status value."""
        assert RecoveryAttemptStatus.SUCCEEDED.value == "succeeded"

    def test_failed_value(self):
        """Test FAILED status value."""
        assert RecoveryAttemptStatus.FAILED.value == "failed"

    def test_accepted_with_caveats_value(self):
        """Test ACCEPTED_WITH_CAVEATS status value."""
        assert RecoveryAttemptStatus.ACCEPTED_WITH_CAVEATS.value == "accepted_with_caveats"

    def test_all_values_are_strings(self):
        """Test all enum values are strings."""
        for status in RecoveryAttemptStatus:
            assert isinstance(status.value, str)


class TestIssueCategoryEnum:
    """Tests for IssueCategory enum."""

    def test_unfilled_placeholder_value(self):
        """Test UNFILLED_PLACEHOLDER category value."""
        assert IssueCategory.UNFILLED_PLACEHOLDER.value == "unfilled_placeholder"

    def test_missing_alt_text_value(self):
        """Test MISSING_ALT_TEXT category value."""
        assert IssueCategory.MISSING_ALT_TEXT.value == "missing_alt_text"

    def test_heading_hierarchy_value(self):
        """Test HEADING_HIERARCHY category value."""
        assert IssueCategory.HEADING_HIERARCHY.value == "heading_hierarchy"

    def test_formatting_value(self):
        """Test FORMATTING category value."""
        assert IssueCategory.FORMATTING.value == "formatting"

    def test_cosmetic_value(self):
        """Test COSMETIC category value."""
        assert IssueCategory.COSMETIC.value == "cosmetic"

    def test_all_categories_count(self):
        """Test we have exactly 5 categories."""
        assert len(list(IssueCategory)) == 5


class TestProcessingStatusEnum:
    """Tests for ProcessingStatus enum."""

    def test_success_value(self):
        """Test SUCCESS status value."""
        assert ProcessingStatus.SUCCESS.value == "success"

    def test_partial_success_value(self):
        """Test PARTIAL_SUCCESS status value."""
        assert ProcessingStatus.PARTIAL_SUCCESS.value == "partial_success"

    def test_needs_review_value(self):
        """Test NEEDS_REVIEW status value."""
        assert ProcessingStatus.NEEDS_REVIEW.value == "needs_review"

    def test_failed_value(self):
        """Test FAILED status value."""
        assert ProcessingStatus.FAILED.value == "failed"

    def test_all_statuses_count(self):
        """Test we have exactly 4 statuses."""
        assert len(list(ProcessingStatus)) == 4


# =============================================================================
# categorize_issues Tests
# =============================================================================


class TestCategorizeIssues:
    """Tests for categorize_issues function."""

    def test_empty_issues_returns_empty_categories(self):
        """Test empty input returns empty categories."""
        result = categorize_issues([])
        assert all(len(issues) == 0 for issues in result.values())

    def test_placeholder_issue_categorized_correctly(self):
        """Test placeholder issues are categorized."""
        issues = ["Page 1: Unfilled image placeholder"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.UNFILLED_PLACEHOLDER]) == 1
        assert issues[0] in result[IssueCategory.UNFILLED_PLACEHOLDER]

    def test_alt_text_issue_categorized_correctly(self):
        """Test alt-text issues are categorized."""
        issues = ["Page 2: Many images without alt-text (5)"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.MISSING_ALT_TEXT]) == 1

    def test_heading_hierarchy_issue_categorized_correctly(self):
        """Test heading hierarchy issues are categorized."""
        issues = ["Page 3: Heading skip H1 -> H3"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.HEADING_HIERARCHY]) == 1

    def test_formatting_issue_categorized_correctly(self):
        """Test formatting issues are categorized."""
        issues = ["Page 4: table row missing closing pipe"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.FORMATTING]) == 1

    def test_unrecognized_issue_categorized_as_cosmetic(self):
        """Test unrecognized issues default to cosmetic."""
        issues = ["Page 5: Some random issue"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.COSMETIC]) == 1

    def test_multiple_issues_categorized_separately(self):
        """Test multiple issues are categorized correctly."""
        issues = [
            "Page 1: Unfilled image placeholder",
            "Page 2: Images without alt-text",
            "Page 3: Heading skip",
            "Page 4: Invalid syntax",  # Contains "syntax" -> FORMATTING
        ]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.UNFILLED_PLACEHOLDER]) == 1
        assert len(result[IssueCategory.MISSING_ALT_TEXT]) == 1
        assert len(result[IssueCategory.HEADING_HIERARCHY]) == 1
        assert len(result[IssueCategory.FORMATTING]) == 1  # "syntax" matches formatting

    def test_case_insensitive_matching(self):
        """Test issue categorization is case insensitive."""
        issues = ["UNFILLED PLACEHOLDER", "MISSING ALT-TEXT"]
        result = categorize_issues(issues)
        assert len(result[IssueCategory.UNFILLED_PLACEHOLDER]) == 1
        assert len(result[IssueCategory.MISSING_ALT_TEXT]) == 1


# =============================================================================
# should_attempt_recovery Tests
# =============================================================================


class TestShouldAttemptRecovery:
    """Tests for should_attempt_recovery function."""

    def test_passed_page_should_not_recover(self):
        """Test that passed pages don't need recovery."""
        verification = PageVerification(page_num=1, passed=True, issues=[])
        assert not should_attempt_recovery(verification, 1)

    def test_failed_page_with_no_issues_should_not_recover(self):
        """Test that failed pages with no issues don't attempt recovery."""
        verification = PageVerification(page_num=1, passed=False, issues=[])
        assert not should_attempt_recovery(verification, 1)

    def test_exceeded_max_attempts_should_not_recover(self):
        """Test that exceeding max attempts stops recovery."""
        verification = PageVerification(
            page_num=1,
            passed=False,
            issues=["Unfilled placeholder"],
        )
        assert not should_attempt_recovery(verification, 3)  # Max is 2

    def test_recoverable_issue_should_attempt(self):
        """Test that recoverable issues trigger recovery."""
        verification = PageVerification(
            page_num=1,
            passed=False,
            issues=["Unfilled image placeholder"],
        )
        assert should_attempt_recovery(verification, 1)

    def test_multiple_recoverable_issues_should_attempt(self):
        """Test multiple recoverable issues trigger recovery."""
        verification = PageVerification(
            page_num=1,
            passed=False,
            issues=[
                "Unfilled placeholder",
                "Missing alt-text",
                "Heading skip",
            ],
        )
        assert should_attempt_recovery(verification, 1)
        assert should_attempt_recovery(verification, 2)

    def test_only_cosmetic_issues_should_not_recover(self):
        """Test that only cosmetic issues don't trigger recovery."""
        verification = PageVerification(
            page_num=1,
            passed=False,
            issues=["Random cosmetic issue", "Another unrecognized issue"],
        )
        # Only cosmetic issues - shouldn't attempt recovery
        assert not should_attempt_recovery(verification, 1)


# =============================================================================
# calculate_pass_threshold Tests
# =============================================================================


class TestCalculatePassThreshold:
    """Tests for calculate_pass_threshold function."""

    def test_small_document_threshold(self):
        """Test small documents (<=5 pages) have 80% threshold."""
        assert calculate_pass_threshold(1) == 0.80
        assert calculate_pass_threshold(3) == 0.80
        assert calculate_pass_threshold(5) == 0.80

    def test_medium_document_threshold(self):
        """Test medium documents (6-20 pages) have 75% threshold."""
        assert calculate_pass_threshold(6) == 0.75
        assert calculate_pass_threshold(10) == 0.75
        assert calculate_pass_threshold(20) == 0.75

    def test_large_document_threshold(self):
        """Test large documents (21-50 pages) have 70% threshold."""
        assert calculate_pass_threshold(21) == 0.70
        assert calculate_pass_threshold(30) == 0.70
        assert calculate_pass_threshold(50) == 0.70

    def test_very_large_document_threshold(self):
        """Test very large documents (>50 pages) have 65% threshold."""
        assert calculate_pass_threshold(51) == 0.65
        assert calculate_pass_threshold(100) == 0.65
        assert calculate_pass_threshold(500) == 0.65

    def test_threshold_decreases_with_size(self):
        """Test thresholds decrease as document size increases."""
        small = calculate_pass_threshold(5)
        medium = calculate_pass_threshold(15)
        large = calculate_pass_threshold(40)
        very_large = calculate_pass_threshold(100)

        assert small > medium > large > very_large


# =============================================================================
# determine_final_status Tests
# =============================================================================


class TestDetermineFinalStatus:
    """Tests for determine_final_status function."""

    def test_passed_verification_returns_success(self):
        """Test passed verification returns SUCCESS."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=True,
            pages_passed=10,
            pages_failed=0,
        )
        status = determine_final_status(verification, None, 10)
        assert status == ProcessingStatus.SUCCESS

    def test_no_recovery_high_pass_rate_returns_partial_success(self):
        """Test high pass rate without recovery returns PARTIAL_SUCCESS."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=9,
            pages_failed=1,
        )
        # 9/10 = 90% > 80% threshold for 10 pages
        status = determine_final_status(verification, None, 10)
        assert status == ProcessingStatus.PARTIAL_SUCCESS

    def test_no_recovery_medium_pass_rate_returns_needs_review(self):
        """Test medium pass rate returns NEEDS_REVIEW."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=6,
            pages_failed=4,
        )
        # 6/10 = 60% - below threshold but >= 50%
        status = determine_final_status(verification, None, 10)
        assert status == ProcessingStatus.NEEDS_REVIEW

    def test_no_recovery_low_pass_rate_returns_failed(self):
        """Test low pass rate returns FAILED."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=3,
            pages_failed=7,
        )
        # 3/10 = 30% < 50%
        status = determine_final_status(verification, None, 10)
        assert status == ProcessingStatus.FAILED

    def test_recovery_all_pages_recovered_returns_success(self):
        """Test full recovery returns SUCCESS."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=8,
            pages_failed=2,
        )
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[9, 10],
            pages_accepted_with_caveats=[],
            pages_unrecoverable=[],
        )
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.SUCCESS

    def test_recovery_with_caveats_returns_partial_success(self):
        """Test recovery with caveats returns PARTIAL_SUCCESS."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=7,
            pages_failed=3,
        )
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[8],
            pages_accepted_with_caveats=[9, 10],
            pages_unrecoverable=[],
        )
        # 7 passed + 1 recovered + 2 with caveats = 10/10 = 100%
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.PARTIAL_SUCCESS

    def test_recovery_with_unrecoverable_pages(self):
        """Test recovery with unrecoverable pages."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=5,
            pages_failed=5,
        )
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[6, 7],
            pages_accepted_with_caveats=[8],
            pages_unrecoverable=[9, 10],
        )
        # Effective: 5 + 2 + 1 = 8/10 = 80% - meets threshold for 10 pages
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.PARTIAL_SUCCESS

    def test_recovery_below_threshold_returns_needs_review(self):
        """Test recovery below threshold returns NEEDS_REVIEW."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=4,
            pages_failed=6,
        )
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[5],
            pages_accepted_with_caveats=[],
            pages_unrecoverable=[6, 7, 8, 9, 10],
        )
        # Effective: 4 + 1 = 5/10 = 50% - below 80% but >= 50%
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.NEEDS_REVIEW

    def test_recovery_very_low_returns_failed(self):
        """Test recovery with very low success rate returns FAILED."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=2,
            pages_failed=8,
        )
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[3],
            pages_accepted_with_caveats=[4],
            pages_unrecoverable=[5, 6, 7, 8, 9, 10],
        )
        # Effective: 2 + 1 + 1 = 4/10 = 40% < 50%
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.FAILED

    def test_zero_pages_returns_failed(self):
        """Test zero pages edge case."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages_passed=0,
            pages_failed=0,
        )
        status = determine_final_status(verification, None, 0)
        assert status == ProcessingStatus.FAILED


# =============================================================================
# Integration-style Tests
# =============================================================================


class TestRecoveryIntegration:
    """Integration-style tests for recovery logic flow."""

    def test_recovery_flow_typical_scenario(self):
        """Test typical recovery flow scenario."""
        # 8/10 pages pass initially
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,  # Didn't pass overall because of critical issues
            pages=[
                PageVerification(page_num=i, passed=True, issues=[])
                for i in range(1, 9)
            ] + [
                PageVerification(
                    page_num=9,
                    passed=False,
                    issues=["Unfilled image placeholder"],
                ),
                PageVerification(
                    page_num=10,
                    passed=False,
                    issues=["Heading skip H1 -> H3"],
                ),
            ],
            pages_passed=8,
            pages_failed=2,
        )

        # Check which pages should attempt recovery
        for pv in verification.pages:
            if pv.passed:
                assert not should_attempt_recovery(pv, 1)
            else:
                # Both failed pages have recoverable issues
                assert should_attempt_recovery(pv, 1)

        # After successful recovery
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[9, 10],
            pages_accepted_with_caveats=[],
            pages_unrecoverable=[],
        )

        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.SUCCESS

    def test_recovery_flow_partial_success_scenario(self):
        """Test recovery flow with partial success."""
        verification = VerificationReport(
            document_id="doc-1",
            passed=False,
            pages=[
                PageVerification(page_num=i, passed=True, issues=[])
                for i in range(1, 8)
            ] + [
                PageVerification(
                    page_num=8,
                    passed=False,
                    issues=["Unfilled placeholder"],
                ),
                PageVerification(
                    page_num=9,
                    passed=False,
                    issues=["Complex formatting issue"],  # Less clear
                ),
                PageVerification(
                    page_num=10,
                    passed=False,
                    issues=["Missing alt-text for diagram"],
                ),
            ],
            pages_passed=7,
            pages_failed=3,
        )

        # Recovery: 1 recovered, 1 with caveats, 1 unrecoverable
        recovery = RecoveryReport(
            document_id="doc-1",
            recovery_attempted=True,
            pages_recovered=[8],
            pages_accepted_with_caveats=[9],
            pages_unrecoverable=[10],
        )

        # Effective: 7 + 1 + 1 = 9/10 = 90%
        status = determine_final_status(verification, recovery, 10)
        assert status == ProcessingStatus.PARTIAL_SUCCESS
