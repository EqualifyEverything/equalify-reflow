"""Tests for edge text extraction for cross-page merge detection.

This module tests that:
1. _extract_edge_text correctly extracts first/last 100 chars
2. Markdown formatting is stripped from edge text
3. Short pages (< 100 chars) return full page content
4. PagePlan has edge text fields
5. PageChainState stores edge text per page
"""


from src.agents.models import PagePlan
from src.agents.page_chain import (
    PageAnalysisOutput,
    PageChainState,
    _extract_edge_text,
    update_chain_state,
)
from src.agents.planner import convert_chain_state_to_page_plans


class TestExtractEdgeTextFirstChars:
    """Test _extract_edge_text for first 100 characters."""

    def test_extract_edge_text_first_100_chars(self) -> None:
        """Should extract first 100 characters from plain text."""
        text = "A" * 150
        result = _extract_edge_text(text, "first")
        assert len(result) == 100
        assert result == "A" * 100

    def test_extract_edge_text_first_strips_header(self) -> None:
        """Should strip markdown headers from first chars."""
        text = "## Heading\n\nThis is the actual content that follows the heading."
        result = _extract_edge_text(text, "first")
        assert not result.startswith("##")
        assert "Heading" in result
        assert "actual content" in result

    def test_extract_edge_text_first_preserves_words(self) -> None:
        """Should preserve readable words in first chars."""
        text = "The quick brown fox jumps over the lazy dog."
        result = _extract_edge_text(text, "first")
        assert result == "The quick brown fox jumps over the lazy dog."


class TestExtractEdgeTextLastChars:
    """Test _extract_edge_text for last 100 characters."""

    def test_extract_edge_text_last_100_chars(self) -> None:
        """Should extract last 100 characters from plain text."""
        text = "A" * 150
        result = _extract_edge_text(text, "last")
        assert len(result) == 100
        assert result == "A" * 100

    def test_extract_edge_text_last_strips_formatting(self) -> None:
        """Should strip markdown formatting from last chars."""
        text = "Some content **with bold** and *italic* at the end."
        result = _extract_edge_text(text, "last")
        assert "**" not in result
        assert "*italic*" not in result
        assert "with bold" in result
        assert "italic" in result

    def test_extract_edge_text_last_preserves_ending_words(self) -> None:
        """Should capture the ending words for merge detection."""
        text = "Content at the start. This sentence ends without a period and might continue on the next page de-"
        result = _extract_edge_text(text, "last")
        assert "de-" in result


class TestExtractEdgeTextShortPage:
    """Test _extract_edge_text for pages shorter than 100 characters."""

    def test_extract_edge_text_short_page_first(self) -> None:
        """Short page should return full content for first."""
        text = "Short page content."
        result = _extract_edge_text(text, "first")
        assert result == "Short page content."

    def test_extract_edge_text_short_page_last(self) -> None:
        """Short page should return full content for last."""
        text = "Short page content."
        result = _extract_edge_text(text, "last")
        assert result == "Short page content."

    def test_extract_edge_text_very_short_page(self) -> None:
        """Very short page (< 10 chars) should work correctly."""
        text = "Hi!"
        result = _extract_edge_text(text, "first")
        assert result == "Hi!"
        result = _extract_edge_text(text, "last")
        assert result == "Hi!"

    def test_extract_edge_text_empty_page(self) -> None:
        """Empty page should return empty string."""
        result = _extract_edge_text("", "first")
        assert result == ""
        result = _extract_edge_text("", "last")
        assert result == ""


class TestExtractEdgeTextStripsMarkdown:
    """Test that _extract_edge_text properly strips markdown formatting."""

    def test_strips_bold_formatting(self) -> None:
        """Should strip **bold** and __bold__ formatting."""
        text = "This is **bold text** and __also bold__."
        result = _extract_edge_text(text, "first")
        assert "**" not in result
        assert "__" not in result
        assert "bold text" in result
        assert "also bold" in result

    def test_strips_italic_formatting(self) -> None:
        """Should strip *italic* and _italic_ formatting."""
        text = "This is *italic* and _also italic_."
        result = _extract_edge_text(text, "first")
        assert "*italic*" not in result
        assert "_also italic_" not in result
        assert "italic" in result
        assert "also italic" in result

    def test_strips_links(self) -> None:
        """Should strip [text](url) links, keeping text."""
        text = "Check out [this link](https://example.com) for more."
        result = _extract_edge_text(text, "first")
        assert "[this link]" not in result
        assert "(https://example.com)" not in result
        assert "this link" in result

    def test_strips_inline_code(self) -> None:
        """Should strip `code` backticks."""
        text = "Use the `print()` function."
        result = _extract_edge_text(text, "first")
        assert "`" not in result
        assert "print()" in result

    def test_strips_headers(self) -> None:
        """Should strip # header markers."""
        text = "# Title\n\n## Section\n\nContent here."
        result = _extract_edge_text(text, "first")
        assert result.startswith("Title")

    def test_strips_horizontal_rules(self) -> None:
        """Should strip ---, ***, ___ horizontal rules."""
        text = "Content above\n\n---\n\nContent below"
        result = _extract_edge_text(text, "first")
        assert "---" not in result

    def test_strips_list_markers(self) -> None:
        """Should strip list markers (-, *, +, numbers)."""
        text = "- Item 1\n- Item 2\n1. First\n2. Second"
        result = _extract_edge_text(text, "first")
        assert result.startswith("Item")

    def test_strips_html_comments(self) -> None:
        """Should strip HTML comments."""
        text = "Content <!-- image:1 --> more content"
        result = _extract_edge_text(text, "first")
        assert "<!--" not in result
        assert "-->" not in result

    def test_strips_image_references(self) -> None:
        """Should strip ![alt](url) image references."""
        text = "Here is an image ![alt text](image.png) in the text."
        result = _extract_edge_text(text, "first")
        assert "![" not in result
        assert "](image.png)" not in result

    def test_normalizes_whitespace(self) -> None:
        """Should collapse multiple spaces/newlines to single space."""
        text = "Word1    Word2\n\n\nWord3"
        result = _extract_edge_text(text, "first")
        assert "  " not in result
        assert "\n" not in result
        assert result == "Word1 Word2 Word3"


class TestPagePlanHasEdgeTextFields:
    """Test that PagePlan model has edge text fields."""

    def test_page_plan_has_first_100_chars_field(self) -> None:
        """PagePlan should have first_100_chars field."""
        plan = PagePlan(page_num=1, summary="Test", section_context="Test section")
        assert hasattr(plan, "first_100_chars")
        assert plan.first_100_chars == ""

    def test_page_plan_has_last_100_chars_field(self) -> None:
        """PagePlan should have last_100_chars field."""
        plan = PagePlan(page_num=1, summary="Test", section_context="Test section")
        assert hasattr(plan, "last_100_chars")
        assert plan.last_100_chars == ""

    def test_page_plan_can_be_created_with_edge_text(self) -> None:
        """PagePlan can be created with edge text values."""
        plan = PagePlan(
            page_num=1,
            summary="Test",
            section_context="Test section",
            first_100_chars="First chars of page",
            last_100_chars="Last chars of page",
        )
        assert plan.first_100_chars == "First chars of page"
        assert plan.last_100_chars == "Last chars of page"


class TestPageChainStateEdgeText:
    """Test that PageChainState stores edge text per page."""

    def test_chain_state_has_edge_text_dicts(self) -> None:
        """PageChainState should have dictionaries for edge text."""
        state = PageChainState()
        assert hasattr(state, "first_100_chars_by_page")
        assert hasattr(state, "last_100_chars_by_page")
        assert state.first_100_chars_by_page == {}
        assert state.last_100_chars_by_page == {}

    def test_update_chain_state_populates_edge_text(self) -> None:
        """update_chain_state should populate edge text from markdown."""
        state = PageChainState()
        output = PageAnalysisOutput(summary="Test summary")
        markdown = "Start of page content that goes on for a while. " * 5 + "End of page."

        state = update_chain_state(state, 1, output, markdown)

        assert 1 in state.first_100_chars_by_page
        assert 1 in state.last_100_chars_by_page
        assert len(state.first_100_chars_by_page[1]) <= 100
        assert len(state.last_100_chars_by_page[1]) <= 100
        assert "Start of page" in state.first_100_chars_by_page[1]
        assert "End of page" in state.last_100_chars_by_page[1]


class TestPlannerEdgeTextConversion:
    """Test that planner correctly converts edge text to PagePlan."""

    def test_convert_chain_state_includes_edge_text(self) -> None:
        """convert_chain_state_to_page_plans should include edge text."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        state.first_100_chars_by_page[1] = "First text"
        state.last_100_chars_by_page[1] = "Last text"

        page_plans = convert_chain_state_to_page_plans(state)

        assert 1 in page_plans
        assert page_plans[1].first_100_chars == "First text"
        assert page_plans[1].last_100_chars == "Last text"

    def test_convert_chain_state_handles_missing_edge_text(self) -> None:
        """convert_chain_state_to_page_plans handles missing edge text gracefully."""
        state = PageChainState()
        state.page_summaries[1] = "Test summary"
        # Don't set edge text

        page_plans = convert_chain_state_to_page_plans(state)

        assert 1 in page_plans
        assert page_plans[1].first_100_chars == ""
        assert page_plans[1].last_100_chars == ""


class TestEdgeTextInvalidPosition:
    """Test _extract_edge_text with invalid position argument."""

    def test_invalid_position_returns_empty(self) -> None:
        """Invalid position argument should return empty string."""
        text = "Some content here."
        result = _extract_edge_text(text, "middle")
        assert result == ""

    def test_none_position_returns_empty(self) -> None:
        """None position should return empty string."""
        text = "Some content here."
        # Type ignore since we're testing edge case
        result = _extract_edge_text(text, "")  # type: ignore
        assert result == ""
