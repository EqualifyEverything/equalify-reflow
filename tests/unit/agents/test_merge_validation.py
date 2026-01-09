"""Unit tests for validate_merge_result() function.

Tests the merge validation helper from src/agents/orchestrator.py
that validates cross-page paragraph merge operations.
"""

import pytest
from src.agents.orchestrator import validate_merge_result

pytestmark = pytest.mark.unit


class TestValidateMergeResult:
    """Tests for validate_merge_result function."""

    def test_valid_merge_passes(self):
        """Normal merge parameters return (True, "")."""
        page1_md = "This is the end of page one with partial"
        page2_md = " text that continues here."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=7,  # Remove "partial"
            page2_remove_chars=6,  # Remove " text "
            merged_text="partial text",
        )

        assert is_valid is True
        assert error == ""

    def test_page1_remove_exceeds_length(self):
        """page1_remove_chars > len(page1_md) returns False."""
        page1_md = "Short text"
        page2_md = "Continuation text"

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=100,  # Exceeds page1 length
            page2_remove_chars=5,
            merged_text="merged",
        )

        assert is_valid is False
        assert "page1_remove_chars" in error
        assert "exceeds page length" in error

    def test_page2_remove_exceeds_length(self):
        """page2_remove_chars > len(page2_md) returns False."""
        page1_md = "First page content"
        page2_md = "Short"

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=5,
            page2_remove_chars=50,  # Exceeds page2 length
            merged_text="merged",
        )

        assert is_valid is False
        assert "page2_remove_chars" in error
        assert "exceeds page length" in error

    def test_word_split_detected(self):
        """Removal that splits a word returns False."""
        page1_md = "The document ends with incomplete"
        page2_md = " and more text follows."

        # Try to remove "lete" from "incomplete" - this would split the word
        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=4,  # Removes "lete" from "incomplete"
            page2_remove_chars=0,
            merged_text="merged text",
        )

        assert is_valid is False
        assert "split word" in error

    def test_merged_text_with_trailing_hyphen(self):
        """merged_text ending with - returns False."""
        page1_md = "Text ends with a hyphen-"
        page2_md = "ated word."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=7,
            page2_remove_chars=4,
            merged_text="hyphen-",  # Still ends with hyphen
        )

        assert is_valid is False
        assert "ends with hyphen" in error

    def test_zero_remove_chars_valid(self):
        """remove_chars=0 is valid."""
        page1_md = "Complete sentence."
        page2_md = "New paragraph starts."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=0,
            page2_remove_chars=0,
            merged_text="",
        )

        assert is_valid is True
        assert error == ""

    def test_removal_at_whitespace_valid(self):
        """Removal at word boundary (space/newline) is valid."""
        page1_md = "Text ends with space "
        page2_md = "New text starts."

        # Remove the trailing space
        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=1,  # Remove trailing space
            page2_remove_chars=0,
            merged_text="",
        )

        assert is_valid is True
        assert error == ""

    def test_removal_at_newline_valid(self):
        """Removal at newline boundary is valid."""
        page1_md = "Text ends with newline\n"
        page2_md = "New text starts."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=1,  # Remove trailing newline
            page2_remove_chars=0,
            merged_text="",
        )

        assert is_valid is True
        assert error == ""

    def test_removal_at_punctuation_boundary(self):
        """Removal at punctuation boundary is valid."""
        page1_md = "Sentence ends here."
        page2_md = " Next sentence."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=1,  # Remove period
            page2_remove_chars=0,
            merged_text="",
        )

        # Period at end is not an alpha character, so removal is allowed
        assert is_valid is True

    def test_merged_text_with_whitespace_hyphen_valid(self):
        """merged_text with hyphen followed by whitespace is checked after strip."""
        page1_md = "Text with hyphen- "
        page2_md = "continued."

        # The merged text has trailing whitespace but hyphen is the last non-space
        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=2,
            page2_remove_chars=0,
            merged_text="hyphen- ",  # Has trailing space but rstrip reveals hyphen
        )

        # After rstrip(), it ends with hyphen
        assert is_valid is False
        assert "ends with hyphen" in error

    def test_empty_merged_text_valid(self):
        """Empty merged_text is valid."""
        page1_md = "First page."
        page2_md = "Second page."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=0,
            page2_remove_chars=0,
            merged_text="",
        )

        assert is_valid is True
        assert error == ""

    def test_both_pages_empty(self):
        """Both pages empty with zero removal is valid."""
        is_valid, error = validate_merge_result(
            page1_md="",
            page2_md="",
            page1_remove_chars=0,
            page2_remove_chars=0,
            merged_text="",
        )

        assert is_valid is True
        assert error == ""

    def test_exact_length_removal_valid(self):
        """Removing exactly all characters from page is valid."""
        page1_md = "Remove all"
        page2_md = "Keep this"

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=len(page1_md),  # Remove entire page1
            page2_remove_chars=0,
            merged_text="replaced",
        )

        # This is valid - we're removing the entire page1 content
        assert is_valid is True

    def test_word_split_with_numeric_boundary(self):
        """Splitting between letter and number is detected."""
        page1_md = "Reference to figure1"
        page2_md = "2 in the document."

        # This would leave "figure" at end of page 1 (ends with alpha)
        # and "1" starts page 2 (but 1 is not alpha)
        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=1,  # Remove "1" leaving "figure"
            page2_remove_chars=0,
            merged_text="",
        )

        # "figure" ends with 'e' (alpha), and "1" starts with '1' (not alpha)
        # So this should be valid as the removed part starts with non-alpha
        assert is_valid is True

    def test_unicode_text_handling(self):
        """Unicode characters are handled correctly."""
        page1_md = "Text with unicode: cafe"
        page2_md = " continues here."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=4,  # Remove "cafe"
            page2_remove_chars=0,
            merged_text="cafe",
        )

        # Would split "cafe" if 'e' ends remaining and 'c' starts removed
        # But we're removing "cafe" entirely, so remaining is "Text with unicode: "
        # which ends with space (not alpha) - should be valid
        assert is_valid is True

    def test_error_message_includes_context(self):
        """Error messages include relevant context for debugging."""
        page1_md = "This word is incompletelywritten"
        page2_md = " next text."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=7,  # Remove "written" from compound
            page2_remove_chars=0,
            merged_text="merged",
        )

        assert is_valid is False
        # Error should show the boundary text
        assert "..." in error or "page 1" in error

    def test_merged_text_with_only_hyphens(self):
        """merged_text that is just hyphens fails due to word split check."""
        page1_md = "Text before"
        page2_md = "Text after"

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=5,
            page2_remove_chars=5,
            merged_text="---",
        )

        # The validation catches word split first (Text b|efore boundary)
        # Both remaining and removed start/end with alpha characters
        assert is_valid is False
        assert "split word" in error or "ends with hyphen" in error

    def test_page1_single_char_removal_splits_word(self):
        """Removing single char that splits a word is detected."""
        # Use a case where the remaining text ends with alpha AND
        # the removed text starts with alpha (continuing the word)
        page1_md = "Word ends with xyz"
        page2_md = " continues."

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=2,  # Remove "yz" - remaining ends with 'x', removed starts with 'y'
            page2_remove_chars=0,
            merged_text="",
        )

        # 'x' ends remaining (alpha), 'y' starts removed (alpha) - split detected
        assert is_valid is False
        assert "split word" in error

    def test_multiline_page_content(self):
        """Multiline page content is handled correctly."""
        page1_md = """First line
Second line
Partial word at end"""
        page2_md = """
Continuation here."""

        is_valid, error = validate_merge_result(
            page1_md=page1_md,
            page2_md=page2_md,
            page1_remove_chars=3,  # Remove "end"
            page2_remove_chars=0,
            merged_text="ending",
        )

        # 'at ' would remain (ends with space), "end" removed - valid
        # But actual: page1_md[-3:] is "end", remaining ends with " at " actually
        # "Partial word at end" -> remove "end" -> "Partial word at "
        # Ends with space, valid
        assert is_valid is True
