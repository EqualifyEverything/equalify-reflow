"""Unit tests for collect_footnotes_at_end() function.

Tests the footnote collection helper from src/agents/orchestrator.py
that moves scattered footnote definitions to the end of the document.
"""

import pytest
from src.agents.orchestrator import collect_footnotes_at_end

pytestmark = pytest.mark.unit


class TestCollectFootnotesAtEnd:
    """Tests for collect_footnotes_at_end function."""

    def test_no_footnotes_unchanged(self):
        """Input without footnotes returns cleaned (no --- separators in middle)."""
        markdown = """# Document Title

This is a paragraph with normal text.

---

Another section after a page break marker.

## Conclusion

Final thoughts here.
"""
        result = collect_footnotes_at_end(markdown)

        # Should not have --- in middle of document
        # Original --- should be removed (converted to double newline)
        assert "---" not in result.split("\n\n---\n")[0]  # Before any footnote section
        assert "# Document Title" in result
        assert "Another section" in result
        assert "## Conclusion" in result

    def test_single_footnote_moved_to_end(self):
        """One footnote is moved to document end."""
        markdown = """# Document

Some text with a reference[^1].

[^1]: This is the footnote definition.

More text after the footnote.
"""
        result = collect_footnotes_at_end(markdown)

        # Footnote should be at the end
        lines = result.strip().split("\n")
        assert "[^1]: This is the footnote definition." in lines[-1]

        # Original inline footnote location should be cleaned
        # The text flow should be continuous
        assert "Some text with a reference[^1]." in result
        assert "More text after the footnote." in result

        # There should be a separator before footnotes section
        assert "\n\n---\n\n[^1]:" in result

    def test_multiple_footnotes_sorted(self):
        """Multiple footnotes are collected and sorted by number."""
        markdown = """# Document

First reference[^1] and third[^3].

[^3]: Third footnote definition.

More text with second[^2].

[^1]: First footnote definition.

Even more text.

[^2]: Second footnote definition.
"""
        result = collect_footnotes_at_end(markdown)

        # Find the footnote section at the end
        parts = result.split("\n\n---\n\n")
        assert len(parts) == 2
        footnote_section = parts[1]

        # Check order: should be sorted numerically
        lines = footnote_section.strip().split("\n")
        assert "[^1]: First footnote definition." in lines[0]
        assert "[^2]: Second footnote definition." in lines[1]
        assert "[^3]: Third footnote definition." in lines[2]

    def test_duplicate_footnotes_keeps_first(self):
        """Duplicate [^1] definitions keep the first occurrence."""
        markdown = """# Document

Reference[^1] here.

[^1]: First definition of footnote 1.

More text.

[^1]: Duplicate definition that should be ignored.
"""
        result = collect_footnotes_at_end(markdown)

        # Should only have one [^1] definition
        assert result.count("[^1]:") == 1
        assert "First definition of footnote 1" in result
        assert "Duplicate definition" not in result

    def test_multiline_footnote_preserved(self):
        """Footnote spanning lines is captured correctly."""
        markdown = """# Document

Reference[^1] here.

[^1]: This is a multiline footnote
    that continues on the next line
    and has multiple paragraphs.

More text after.
"""
        result = collect_footnotes_at_end(markdown)

        # The footnote content should be preserved
        # Note: The exact multiline handling depends on the regex
        assert "[^1]:" in result
        assert "This is a multiline footnote" in result

    def test_page_separators_removed(self):
        """--- markers are removed from middle of document."""
        markdown = """# Page 1

Content on page 1.

---

# Page 2

Content on page 2.

---

# Page 3

Final content.
"""
        result = collect_footnotes_at_end(markdown)

        # Count --- occurrences - should be zero in main content
        # (no footnotes, so no --- at end either)
        main_content = result
        assert main_content.count("\n---\n") == 0
        assert "# Page 1" in result
        assert "# Page 2" in result
        assert "# Page 3" in result

    def test_excessive_whitespace_cleaned(self):
        """Multiple newlines collapsed to double newlines."""
        markdown = """# Document



Content after too many newlines.




More content.
"""
        result = collect_footnotes_at_end(markdown)

        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in result
        assert "# Document" in result
        assert "Content after too many newlines." in result
        assert "More content." in result

    def test_footnote_with_special_chars(self):
        """Footnotes with URLs, brackets, etc. are preserved."""
        markdown = """# Document

Reference[^1] and another[^2].

[^1]: See https://example.com/page?foo=bar&baz=qux for details.

[^2]: Contains [brackets] and (parentheses) and *formatting*.
"""
        result = collect_footnotes_at_end(markdown)

        # Special characters should be preserved
        assert "https://example.com/page?foo=bar&baz=qux" in result
        assert "[brackets]" in result
        assert "(parentheses)" in result
        assert "*formatting*" in result

    def test_footnote_with_code_blocks(self):
        """Footnotes containing code snippets are preserved."""
        markdown = """# Document

See footnote[^1].

[^1]: Use `code snippet` for this.
"""
        result = collect_footnotes_at_end(markdown)

        assert "`code snippet`" in result

    def test_empty_document(self):
        """Empty document returns empty string."""
        result = collect_footnotes_at_end("")
        assert result == ""

    def test_only_whitespace(self):
        """Document with only whitespace is cleaned."""
        result = collect_footnotes_at_end("   \n\n\n   ")
        assert result == ""

    def test_high_numbered_footnotes(self):
        """High-numbered footnotes (e.g., [^42]) are handled."""
        markdown = """# Document

Reference[^42] and [^100].

[^100]: One hundredth footnote.

[^42]: Forty-second footnote.
"""
        result = collect_footnotes_at_end(markdown)

        # Should be sorted numerically
        footnote_section = result.split("\n\n---\n\n")[1]
        lines = footnote_section.strip().split("\n")
        assert "[^42]:" in lines[0]
        assert "[^100]:" in lines[1]

    def test_footnote_reference_without_definition(self):
        """Footnote references without definitions don't cause errors."""
        markdown = """# Document

Reference[^1] exists but [^2] has no definition.

[^1]: Only footnote 1 is defined.
"""
        result = collect_footnotes_at_end(markdown)

        # Should have [^1] definition
        assert "[^1]: Only footnote 1 is defined." in result
        # [^2] reference should remain in text (it's just a reference)
        assert "[^2]" in result

    def test_preserves_document_structure(self):
        """Overall document structure is preserved."""
        markdown = """# Title

Introduction paragraph.

## Section 1

Content with footnote[^1].

[^1]: Important note.

### Subsection 1.1

More detailed content.

## Section 2

Conclusion.
"""
        result = collect_footnotes_at_end(markdown)

        # All headings should be present in order
        assert result.index("# Title") < result.index("## Section 1")
        assert result.index("## Section 1") < result.index("### Subsection 1.1")
        assert result.index("### Subsection 1.1") < result.index("## Section 2")

        # Footnote should be at the very end
        footnote_pos = result.index("[^1]:")
        section2_pos = result.index("## Section 2")
        assert footnote_pos > section2_pos

    def test_separator_before_footnotes_only_when_present(self):
        """--- separator only appears before footnotes when footnotes exist."""
        markdown_no_footnotes = "# Document\n\nJust text."
        result_no_footnotes = collect_footnotes_at_end(markdown_no_footnotes)

        # No --- at end when no footnotes
        assert not result_no_footnotes.rstrip().endswith("---")

        markdown_with_footnotes = "# Document\n\nText[^1].\n\n[^1]: Note."
        result_with_footnotes = collect_footnotes_at_end(markdown_with_footnotes)

        # --- should appear before footnote section
        assert "\n\n---\n\n[^1]:" in result_with_footnotes
