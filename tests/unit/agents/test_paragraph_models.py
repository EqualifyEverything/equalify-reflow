"""Tests for ParagraphAgent foundation models.

Tests the new types, enums, and models added for the ParagraphAgent:
- JobType.PARAGRAPH
- New TaskTypes
- Issue detection models
- LedgerEntry.needs_review
- SubagentResult and derived types
"""

import pytest
from src.agents import (
    # Subagent types
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_SKIP,
    # Issue models
    CitationIssue,
    CitationResult,
    FootnoteIssue,
    FootnoteResult,
    # Enums
    JobType,
    # Ledger
    LedgerEntry,
    ListIssue,
    ListResult,
    PageArtifactIssue,
    PageArtifactResult,
    ParagraphMergeResult,
    SubagentResult,
    TaskType,
    TypographyIssue,
    TypographyResult,
)


class TestJobType:
    """Tests for JobType enum."""

    def test_paragraph_job_type_exists(self) -> None:
        """JobType.PARAGRAPH should exist."""
        assert JobType.PARAGRAPH.value == "paragraph"

    def test_job_types_are_distinct(self) -> None:
        """All job types should have distinct values."""
        values = [jt.value for jt in JobType]
        assert len(values) == len(set(values))

    def test_existing_job_types_unchanged(self) -> None:
        """Existing job types should not change."""
        assert JobType.STRUCTURE.value == "structure"
        assert JobType.CONTENT.value == "content"


class TestTaskTypes:
    """Tests for new TaskType enum values."""

    def test_paragraph_task_types_exist(self) -> None:
        """All 6 new TaskTypes should exist."""
        assert TaskType.PAGE_ARTIFACT_REMOVAL.value == "page_artifact_removal"
        assert TaskType.FOOTNOTE_CORRECTION.value == "footnote_correction"
        assert TaskType.CITATION_LINKING.value == "citation_linking"
        assert TaskType.LIST_FIX.value == "list_fix"
        assert TaskType.TYPOGRAPHY_FIX.value == "typography_fix"
        assert TaskType.PARAGRAPH_MERGE.value == "paragraph_merge"

    def test_existing_task_types_unchanged(self) -> None:
        """Existing task types should not change."""
        assert TaskType.ALT_TEXT.value == "alt_text"
        assert TaskType.TABLE_TRANSCRIPTION.value == "table_transcription"
        assert TaskType.HEADING_FIX.value == "heading_fix"
        assert TaskType.OCR_FIX.value == "ocr_fix"
        assert TaskType.FORMAT_FIX.value == "format_fix"
        assert TaskType.SPELLING_FIX.value == "spelling_fix"


class TestIssueModels:
    """Tests for issue detection models."""

    def test_page_artifact_issue(self) -> None:
        """PageArtifactIssue should have required fields."""
        issue = PageArtifactIssue(
            text="---",
            artifact_type="page_break",
            line_number=42,
        )
        assert issue.text == "---"
        assert issue.artifact_type == "page_break"
        assert issue.line_number == 42

    def test_footnote_issue(self) -> None:
        """FootnoteIssue should have required fields with defaults."""
        issue = FootnoteIssue(
            marker="[^1]",
            issue_type="missing_definition",
        )
        assert issue.marker == "[^1]"
        assert issue.issue_type == "missing_definition"
        assert issue.line_number == 0  # default

    def test_citation_issue(self) -> None:
        """CitationIssue should have required fields with defaults."""
        issue = CitationIssue(
            marker="[1]",
            issue_type="unlinked",
        )
        assert issue.marker == "[1]"
        assert issue.issue_type == "unlinked"
        assert issue.line_number == 0  # default

    def test_list_issue(self) -> None:
        """ListIssue should have required fields with defaults."""
        issue = ListIssue(
            location="lines 10-15",
            issue_type="nesting",
        )
        assert issue.location == "lines 10-15"
        assert issue.issue_type == "nesting"
        assert issue.description == ""  # default

    def test_typography_issue(self) -> None:
        """TypographyIssue should have required fields with defaults."""
        issue = TypographyIssue(
            text="important",
            formatting_type="bold",
            semantic_purpose="emphasis",
        )
        assert issue.text == "important"
        assert issue.formatting_type == "bold"
        assert issue.semantic_purpose == "emphasis"
        assert issue.line_number == 0  # default


class TestLedgerEntryNeedsReview:
    """Tests for LedgerEntry.needs_review field."""

    def test_needs_review_defaults_to_false(self) -> None:
        """LedgerEntry.needs_review should default to False."""
        entry = LedgerEntry(
            job_id="job-123",
            page=1,
            action=TaskType.ALT_TEXT,
            target="fig:1",
            before="[image]",
            after="A diagram showing...",
            reasoning="Added alt text",
        )
        assert entry.needs_review is False

    def test_needs_review_can_be_set_true(self) -> None:
        """LedgerEntry.needs_review can be set to True."""
        entry = LedgerEntry(
            job_id="job-123",
            page=1,
            action=TaskType.FOOTNOTE_CORRECTION,
            target="footnote:1",
            before="[^1]",
            after="[^1]: Definition",
            reasoning="Added footnote definition",
            needs_review=True,
        )
        assert entry.needs_review is True

    def test_low_confidence_entry_can_be_flagged(self) -> None:
        """Low confidence entries can be flagged for review."""
        entry = LedgerEntry(
            job_id="job-456",
            page=2,
            action=TaskType.PAGE_ARTIFACT_REMOVAL,
            target="artifact:1",
            before="---",
            after="",
            reasoning="Removed page break marker",
            confidence=0.6,
            needs_review=True,
        )
        assert entry.confidence == 0.6
        assert entry.needs_review is True


class TestConfidenceConstants:
    """Tests for confidence threshold constants."""

    def test_confidence_constants_values(self) -> None:
        """Confidence constants should have expected values."""
        assert CONFIDENCE_AUTO_APPLY == 0.8
        assert CONFIDENCE_APPLY_WITH_REVIEW == 0.5
        assert CONFIDENCE_SKIP == 0.5

    def test_confidence_thresholds_ordering(self) -> None:
        """Auto apply threshold should be highest."""
        assert CONFIDENCE_AUTO_APPLY > CONFIDENCE_APPLY_WITH_REVIEW
        assert CONFIDENCE_APPLY_WITH_REVIEW >= CONFIDENCE_SKIP


class TestSubagentResult:
    """Tests for SubagentResult base class."""

    def test_subagent_result_has_required_fields(self) -> None:
        """SubagentResult should have confidence and reasoning."""
        result = SubagentResult(
            confidence=0.85,
            reasoning="Found and removed page break",
        )
        assert result.confidence == 0.85
        assert result.reasoning == "Found and removed page break"

    def test_confidence_must_be_in_range(self) -> None:
        """Confidence must be between 0.0 and 1.0."""
        with pytest.raises(ValueError):
            SubagentResult(confidence=1.5, reasoning="Invalid")

        with pytest.raises(ValueError):
            SubagentResult(confidence=-0.1, reasoning="Invalid")


class TestSubagentResultTypes:
    """Tests for specific subagent result types."""

    def test_page_artifact_result(self) -> None:
        """PageArtifactResult should have all fields."""
        result = PageArtifactResult(
            confidence=0.92,
            reasoning="Found and removed page break markers",
            cleaned_text="Clean paragraph text",
            artifacts_removed=["---", "==="],
            words_rejoined=["deprecate"],
        )
        assert result.confidence == 0.92
        assert result.cleaned_text == "Clean paragraph text"
        assert len(result.artifacts_removed) == 2
        assert "deprecate" in result.words_rejoined

    def test_footnote_result(self) -> None:
        """FootnoteResult should have all fields."""
        result = FootnoteResult(
            confidence=0.78,
            reasoning="Linked footnote to definition",
            corrected_markdown="Text with [^1] linked",
            footnotes_fixed=[{"marker": "[^1]", "action": "linked", "definition": "..."}],
        )
        assert result.confidence == 0.78
        assert len(result.footnotes_fixed) == 1

    def test_citation_result(self) -> None:
        """CitationResult should have all fields."""
        result = CitationResult(
            confidence=0.85,
            reasoning="Linked citations to bibliography",
            corrected_markdown="As shown in [1]",
            citations_linked=[{"marker": "[1]", "linked_to": "#ref-1"}],
            bibliography_found=True,
        )
        assert result.bibliography_found is True
        assert len(result.citations_linked) == 1

    def test_list_result(self) -> None:
        """ListResult should have all fields."""
        result = ListResult(
            confidence=0.90,
            reasoning="Fixed list numbering",
            corrected_markdown="1. First\n2. Second\n3. Third",
            issues_fixed=["Corrected numbering from 1,2,4,5 to 1,2,3,4"],
        )
        assert len(result.issues_fixed) == 1

    def test_typography_result(self) -> None:
        """TypographyResult should have all fields."""
        result = TypographyResult(
            confidence=0.75,
            reasoning="Added emphasis formatting",
            corrected_markdown="This is **important**",
            formatting_added=[{"text": "important", "type": "bold", "purpose": "emphasis"}],
        )
        assert len(result.formatting_added) == 1

    def test_paragraph_merge_result(self) -> None:
        """ParagraphMergeResult should have all fields."""
        result = ParagraphMergeResult(
            confidence=0.88,
            reasoning="Detected hyphenated word split across pages",
            should_merge=True,
            merged_text="The algorithm uses a greedy approach",
            join_method="hyphen_removal",
            page1_remove_chars=3,
            page2_remove_chars=0,
        )
        assert result.should_merge is True
        assert result.join_method == "hyphen_removal"
        assert result.page1_remove_chars == 3

    def test_paragraph_merge_result_no_merge(self) -> None:
        """ParagraphMergeResult can indicate no merge needed."""
        result = ParagraphMergeResult(
            confidence=0.95,
            reasoning="Pages end/start at sentence boundaries",
            should_merge=False,
        )
        assert result.should_merge is False
        assert result.merged_text == ""  # default
        assert result.join_method == "space"  # default


class TestSubagentResultInheritance:
    """Tests that all result types inherit from SubagentResult."""

    @pytest.mark.parametrize(
        "result_class",
        [
            PageArtifactResult,
            FootnoteResult,
            CitationResult,
            ListResult,
            TypographyResult,
            ParagraphMergeResult,
        ],
    )
    def test_inherits_from_subagent_result(
        self, result_class: type[SubagentResult]
    ) -> None:
        """All result types should inherit from SubagentResult."""
        assert issubclass(result_class, SubagentResult)
