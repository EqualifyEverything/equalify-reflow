"""Tests for paragraph issue detection in PageChainAgent.

This module tests that:
1. PageAnalysisOutput has all 6 new paragraph issue fields
2. PageChainState stores paragraph issues per page
3. PagePlan receives paragraph issues from chain state
4. System prompt includes paragraph detection instructions
"""

from src.agents.models import (
    CitationIssue,
    FootnoteIssue,
    ListIssue,
    PageArtifactIssue,
    PagePlan,
    TypographyIssue,
)
from src.agents.page_chain import (
    PAGE_ANALYSIS_SYSTEM_PROMPT,
    PageAnalysisOutput,
    PageChainState,
    update_chain_state,
)
from src.agents.planner import convert_chain_state_to_page_plans


class TestPageAnalysisOutputFields:
    """Test that PageAnalysisOutput has all required paragraph issue fields."""

    def test_page_artifacts_field_exists(self) -> None:
        """PageAnalysisOutput should have page_artifacts field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "page_artifacts")
        assert output.page_artifacts == []

    def test_footnote_issues_field_exists(self) -> None:
        """PageAnalysisOutput should have footnote_issues field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "footnote_issues")
        assert output.footnote_issues == []

    def test_citation_issues_field_exists(self) -> None:
        """PageAnalysisOutput should have citation_issues field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "citation_issues")
        assert output.citation_issues == []

    def test_list_issues_field_exists(self) -> None:
        """PageAnalysisOutput should have list_issues field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "list_issues")
        assert output.list_issues == []

    def test_typography_issues_field_exists(self) -> None:
        """PageAnalysisOutput should have typography_issues field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "typography_issues")
        assert output.typography_issues == []

    def test_has_page_continuation_field_exists(self) -> None:
        """PageAnalysisOutput should have has_page_continuation field."""
        output = PageAnalysisOutput(summary="Test summary")
        assert hasattr(output, "has_page_continuation")
        assert output.has_page_continuation is False

    def test_page_analysis_output_with_issues(self) -> None:
        """PageAnalysisOutput can be created with paragraph issues."""
        output = PageAnalysisOutput(
            summary="Test summary",
            page_artifacts=[
                PageArtifactIssue(
                    text="---",
                    artifact_type="page_break",
                    line_number=10,
                )
            ],
            footnote_issues=[
                FootnoteIssue(
                    marker="[^1]",
                    issue_type="missing_definition",
                    line_number=15,
                )
            ],
            citation_issues=[
                CitationIssue(
                    marker="[1]",
                    issue_type="unlinked",
                    line_number=20,
                )
            ],
            list_issues=[
                ListIssue(
                    location="lines 25-30",
                    issue_type="nesting",
                    description="Inconsistent indentation",
                )
            ],
            typography_issues=[
                TypographyIssue(
                    text="Important",
                    formatting_type="bold",
                    semantic_purpose="emphasis",
                    line_number=35,
                )
            ],
            has_page_continuation=True,
        )

        assert len(output.page_artifacts) == 1
        assert len(output.footnote_issues) == 1
        assert len(output.citation_issues) == 1
        assert len(output.list_issues) == 1
        assert len(output.typography_issues) == 1
        assert output.has_page_continuation is True


class TestPageChainStateStorage:
    """Test that PageChainState stores paragraph issues correctly."""

    def test_chain_state_has_paragraph_issue_fields(self) -> None:
        """PageChainState should have fields for storing paragraph issues."""
        state = PageChainState()

        assert hasattr(state, "page_artifacts_by_page")
        assert hasattr(state, "footnote_issues_by_page")
        assert hasattr(state, "citation_issues_by_page")
        assert hasattr(state, "list_issues_by_page")
        assert hasattr(state, "typography_issues_by_page")
        assert hasattr(state, "page_continuations")

    def test_update_chain_state_stores_page_artifacts(self) -> None:
        """update_chain_state should store page artifacts."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            page_artifacts=[
                PageArtifactIssue(
                    text="---",
                    artifact_type="page_break",
                    line_number=10,
                )
            ],
        )

        updated = update_chain_state(state, page_num=1, output=output)

        assert 1 in updated.page_artifacts_by_page
        assert len(updated.page_artifacts_by_page[1]) == 1
        assert updated.page_artifacts_by_page[1][0].text == "---"

    def test_update_chain_state_stores_footnote_issues(self) -> None:
        """update_chain_state should store footnote issues."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            footnote_issues=[
                FootnoteIssue(
                    marker="[^1]",
                    issue_type="missing_definition",
                )
            ],
        )

        updated = update_chain_state(state, page_num=2, output=output)

        assert 2 in updated.footnote_issues_by_page
        assert len(updated.footnote_issues_by_page[2]) == 1

    def test_update_chain_state_stores_citation_issues(self) -> None:
        """update_chain_state should store citation issues."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            citation_issues=[
                CitationIssue(
                    marker="[1]",
                    issue_type="unlinked",
                )
            ],
        )

        updated = update_chain_state(state, page_num=3, output=output)

        assert 3 in updated.citation_issues_by_page

    def test_update_chain_state_stores_list_issues(self) -> None:
        """update_chain_state should store list issues."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            list_issues=[
                ListIssue(
                    location="lines 10-15",
                    issue_type="nesting",
                )
            ],
        )

        updated = update_chain_state(state, page_num=4, output=output)

        assert 4 in updated.list_issues_by_page

    def test_update_chain_state_stores_typography_issues(self) -> None:
        """update_chain_state should store typography issues."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            typography_issues=[
                TypographyIssue(
                    text="Warning",
                    formatting_type="bold",
                    semantic_purpose="warning",
                )
            ],
        )

        updated = update_chain_state(state, page_num=5, output=output)

        assert 5 in updated.typography_issues_by_page

    def test_update_chain_state_stores_page_continuation(self) -> None:
        """update_chain_state should store page continuation flag."""
        state = PageChainState()
        output = PageAnalysisOutput(
            summary="Test page",
            has_page_continuation=True,
        )

        updated = update_chain_state(state, page_num=6, output=output)

        assert updated.page_continuations[6] is True


class TestPagePlanPropagation:
    """Test that paragraph issues propagate to PagePlan."""

    def test_page_plan_has_paragraph_issue_fields(self) -> None:
        """PagePlan should have all paragraph issue fields."""
        plan = PagePlan(
            page_num=1,
            summary="Test",
            section_context="Section 1",
        )

        assert hasattr(plan, "page_artifacts")
        assert hasattr(plan, "footnote_issues")
        assert hasattr(plan, "citation_issues")
        assert hasattr(plan, "list_issues")
        assert hasattr(plan, "typography_issues")
        assert hasattr(plan, "has_page_continuation")

    def test_convert_chain_state_propagates_page_artifacts(self) -> None:
        """convert_chain_state_to_page_plans should propagate page artifacts."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.page_artifacts_by_page[1] = [
            PageArtifactIssue(
                text="---",
                artifact_type="page_break",
                line_number=10,
            )
        ]

        page_plans = convert_chain_state_to_page_plans(state)

        assert 1 in page_plans
        assert len(page_plans[1].page_artifacts) == 1
        assert page_plans[1].page_artifacts[0].text == "---"

    def test_convert_chain_state_propagates_footnote_issues(self) -> None:
        """convert_chain_state_to_page_plans should propagate footnote issues."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.footnote_issues_by_page[1] = [
            FootnoteIssue(
                marker="[^1]",
                issue_type="missing_definition",
            )
        ]

        page_plans = convert_chain_state_to_page_plans(state)

        assert len(page_plans[1].footnote_issues) == 1

    def test_convert_chain_state_propagates_citation_issues(self) -> None:
        """convert_chain_state_to_page_plans should propagate citation issues."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.citation_issues_by_page[1] = [
            CitationIssue(
                marker="[1]",
                issue_type="unlinked",
            )
        ]

        page_plans = convert_chain_state_to_page_plans(state)

        assert len(page_plans[1].citation_issues) == 1

    def test_convert_chain_state_propagates_list_issues(self) -> None:
        """convert_chain_state_to_page_plans should propagate list issues."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.list_issues_by_page[1] = [
            ListIssue(
                location="lines 10-15",
                issue_type="nesting",
            )
        ]

        page_plans = convert_chain_state_to_page_plans(state)

        assert len(page_plans[1].list_issues) == 1

    def test_convert_chain_state_propagates_typography_issues(self) -> None:
        """convert_chain_state_to_page_plans should propagate typography issues."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.typography_issues_by_page[1] = [
            TypographyIssue(
                text="Important",
                formatting_type="bold",
                semantic_purpose="emphasis",
            )
        ]

        page_plans = convert_chain_state_to_page_plans(state)

        assert len(page_plans[1].typography_issues) == 1

    def test_convert_chain_state_propagates_page_continuation(self) -> None:
        """convert_chain_state_to_page_plans should propagate page continuation."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.page_continuations[1] = True

        page_plans = convert_chain_state_to_page_plans(state)

        assert page_plans[1].has_page_continuation is True

    def test_convert_chain_state_defaults_missing_issues(self) -> None:
        """Pages without issues should have empty lists."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        # No issues added for page 1

        page_plans = convert_chain_state_to_page_plans(state)

        assert page_plans[1].page_artifacts == []
        assert page_plans[1].footnote_issues == []
        assert page_plans[1].citation_issues == []
        assert page_plans[1].list_issues == []
        assert page_plans[1].typography_issues == []
        assert page_plans[1].has_page_continuation is False


class TestSystemPromptInstructions:
    """Test that system prompt includes paragraph detection instructions."""

    def test_prompt_includes_page_artifacts_instruction(self) -> None:
        """System prompt should include page artifacts detection."""
        assert "Page Artifacts" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "---" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "split" in PAGE_ANALYSIS_SYSTEM_PROMPT.lower()

    def test_prompt_includes_footnotes_instruction(self) -> None:
        """System prompt should include footnote detection."""
        assert "Footnotes" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "[^1]" in PAGE_ANALYSIS_SYSTEM_PROMPT

    def test_prompt_includes_citations_instruction(self) -> None:
        """System prompt should include citation detection."""
        assert "Citations" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "[1]" in PAGE_ANALYSIS_SYSTEM_PROMPT

    def test_prompt_includes_list_structure_instruction(self) -> None:
        """System prompt should include list structure detection."""
        assert "List Structure" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "nesting" in PAGE_ANALYSIS_SYSTEM_PROMPT.lower()

    def test_prompt_includes_typography_instruction(self) -> None:
        """System prompt should include typography detection."""
        assert "Typography" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "Bold" in PAGE_ANALYSIS_SYSTEM_PROMPT or "bold" in PAGE_ANALYSIS_SYSTEM_PROMPT

    def test_prompt_includes_page_continuity_instruction(self) -> None:
        """System prompt should include page continuity detection."""
        assert "Page Continuity" in PAGE_ANALYSIS_SYSTEM_PROMPT
        assert "has_page_continuation" in PAGE_ANALYSIS_SYSTEM_PROMPT
