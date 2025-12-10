"""Tests for diff utility functions."""


from src.utils.diff_utils import (
    apply_diff,
    count_diff_changes,
    find_unique_context,
    validate_search_replace,
)


class TestValidateSearchReplace:
    """Tests for validate_search_replace function."""

    def test_valid_unique_search(self):
        """Valid when search text is unique."""
        markdown = "# Hello World\n\nThis is content."
        valid, err = validate_search_replace(markdown, "Hello World", "Hi There")
        assert valid is True
        assert err is None

    def test_empty_search_invalid(self):
        """Empty search text is invalid."""
        valid, err = validate_search_replace("# Hello", "", "replacement")
        assert valid is False
        assert err == "Search text cannot be empty"

    def test_identical_search_replace_invalid(self):
        """Search and replace being identical is invalid."""
        valid, err = validate_search_replace("# Hello", "Hello", "Hello")
        assert valid is False
        assert err == "Search and replace text are identical"

    def test_search_not_found_invalid(self):
        """Search text not in document is invalid."""
        valid, err = validate_search_replace("# Hello", "World", "Earth")
        assert valid is False
        assert err == "Search text not found in document"

    def test_search_not_unique_invalid(self):
        """Search text matching multiple times is invalid."""
        markdown = "# Hello Hello Hello"
        valid, err = validate_search_replace(markdown, "Hello", "Hi")
        assert valid is False
        assert "matches 3 locations" in err

    def test_empty_replace_valid(self):
        """Empty replace text is valid (deletion)."""
        markdown = "# Hello World"
        valid, err = validate_search_replace(markdown, " World", "")
        assert valid is True
        assert err is None

    def test_whitespace_preserved(self):
        """Whitespace differences matter."""
        markdown = "# Hello  World"  # Double space
        valid, err = validate_search_replace(markdown, "Hello World", "Hi")
        assert valid is False  # Single space won't match double

    def test_newlines_in_search(self):
        """Search text can include newlines."""
        markdown = "# Hello\n\nWorld"
        valid, err = validate_search_replace(markdown, "Hello\n\nWorld", "Hi There")
        assert valid is True

    def test_special_characters(self):
        """Special regex characters are treated as literals."""
        markdown = "Price: $10.00 (50% off)"
        valid, err = validate_search_replace(markdown, "$10.00", "$5.00")
        assert valid is True

    def test_unicode_content(self):
        """Unicode content is handled correctly."""
        markdown = "# Café résumé"
        valid, err = validate_search_replace(markdown, "Café", "Coffee")
        assert valid is True


class TestFindUniqueContext:
    """Tests for find_unique_context function."""

    def test_already_unique(self):
        """Returns target if already unique."""
        markdown = "The cat sat on the mat."
        result = find_unique_context(markdown, "sat")
        assert result == "sat"

    def test_expands_context_to_make_unique(self):
        """Expands context until unique."""
        markdown = "The cat sat. The cat ran."
        result = find_unique_context(markdown, "cat", min_context=5, max_context=20)
        assert result is not None
        assert "cat" in result
        assert markdown.count(result) == 1

    def test_target_not_found(self):
        """Returns None if target not in document."""
        markdown = "The dog sat."
        result = find_unique_context(markdown, "cat")
        assert result is None

    def test_cannot_make_unique(self):
        """Returns None if cannot make unique within max_context."""
        # Create a document where context expansion won't help
        markdown = "cat " * 100
        result = find_unique_context(markdown, "cat", min_context=5, max_context=10)
        assert result is None

    def test_context_includes_target(self):
        """Result always includes the original target."""
        markdown = "First cat here. Second cat there."
        result = find_unique_context(markdown, "cat", min_context=5, max_context=50)
        assert result is not None
        assert "cat" in result

    def test_context_at_start_of_document(self):
        """Handles target at start of document."""
        markdown = "cat sat. The cat ran."
        result = find_unique_context(markdown, "cat", min_context=5, max_context=20)
        # Should expand right to include unique context
        assert result is not None
        assert markdown.count(result) == 1

    def test_context_at_end_of_document(self):
        """Handles target at end of document."""
        markdown = "The cat ran. A cat"
        result = find_unique_context(markdown, "cat", min_context=5, max_context=20)
        assert result is not None
        assert markdown.count(result) == 1

    def test_empty_document(self):
        """Handles empty document."""
        result = find_unique_context("", "cat")
        assert result is None


class TestApplyDiff:
    """Tests for apply_diff function."""

    def test_successful_replacement(self):
        """Applies replacement when valid."""
        markdown = "# Hello World"
        result = apply_diff(markdown, "Hello", "Hi")
        assert result == "# Hi World"

    def test_returns_none_when_invalid(self):
        """Returns None when validation fails."""
        markdown = "# Hello Hello"  # Not unique
        result = apply_diff(markdown, "Hello", "Hi")
        assert result is None

    def test_empty_replace_deletes(self):
        """Empty replace performs deletion."""
        markdown = "# Hello World"
        result = apply_diff(markdown, " World", "")
        assert result == "# Hello"

    def test_only_replaces_first_occurrence(self):
        """Uses count=1 to replace only first match (though validation requires uniqueness)."""
        # This is tested by the validation, but verify behavior
        markdown = "# Unique Title"
        result = apply_diff(markdown, "Unique", "Special")
        assert result == "# Special Title"

    def test_multiline_replacement(self):
        """Handles multi-line search/replace."""
        markdown = "# Title\n\nOld content\n\n## Section"
        result = apply_diff(markdown, "Old content", "New content")
        assert result == "# Title\n\nNew content\n\n## Section"


class TestCountDiffChanges:
    """Tests for count_diff_changes function."""

    def test_addition_only(self):
        """Counts addition (replace longer than search)."""
        result = count_diff_changes("text", "Hello", "Hello World")
        assert result["chars_removed"] == 5
        assert result["chars_added"] == 11
        assert result["net_change"] == 6

    def test_deletion_only(self):
        """Counts deletion (replace shorter than search)."""
        result = count_diff_changes("text", "Hello World", "Hello")
        assert result["chars_removed"] == 11
        assert result["chars_added"] == 5
        assert result["net_change"] == -6

    def test_same_length(self):
        """Zero net change when same length."""
        result = count_diff_changes("text", "Hello", "World")
        assert result["chars_removed"] == 5
        assert result["chars_added"] == 5
        assert result["net_change"] == 0

    def test_complete_deletion(self):
        """Empty replace is complete deletion."""
        result = count_diff_changes("text", "Hello", "")
        assert result["chars_removed"] == 5
        assert result["chars_added"] == 0
        assert result["net_change"] == -5


class TestEdgeCases:
    """Edge case tests for diff utilities."""

    def test_very_long_document(self):
        """Handles large documents."""
        markdown = "# Title\n\n" + ("Content paragraph. " * 1000)
        valid, _ = validate_search_replace(markdown, "# Title", "# New Title")
        assert valid is True

    def test_markdown_with_code_blocks(self):
        """Handles markdown code blocks."""
        markdown = """# Code Example

```python
def hello():
    print("Hello")
```

More text."""
        valid, _ = validate_search_replace(markdown, 'print("Hello")', 'print("Hi")')
        assert valid is True

    def test_markdown_with_images(self):
        """Handles markdown images."""
        markdown = "# Doc\n\n![](image.png)\n\nText after."
        valid, _ = validate_search_replace(
            markdown,
            "![](image.png)",
            "![Alt text description](image.png)"
        )
        assert valid is True

    def test_markdown_with_tables(self):
        """Handles markdown tables."""
        markdown = """# Table

| Col A | Col B |
|-------|-------|
| 1     | 2     |
"""
        valid, _ = validate_search_replace(markdown, "| Col A |", "| Column A |")
        assert valid is True

    def test_search_with_pipe_characters(self):
        """Pipe characters don't cause regex issues."""
        markdown = "| A | B |\n|---|---|\n| 1 | 2 |"
        valid, _ = validate_search_replace(markdown, "| A | B |", "| X | Y |")
        assert valid is True

    def test_empty_markdown(self):
        """Handles empty markdown document."""
        valid, err = validate_search_replace("", "anything", "something")
        assert valid is False
        assert err == "Search text not found in document"
