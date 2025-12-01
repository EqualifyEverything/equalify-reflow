"""Unit tests for markdown cleanup utilities.

Tests mdformat integration, spell checking, and cleanup pipeline.
"""

import pytest
from unittest.mock import patch, mock_open, MagicMock

from src.utils.markdown_cleanup import (
    format_markdown,
    check_spelling,
    cleanup_markdown,
    format_spelling_flags_for_prompt,
    SpellingFlag,
    MarkdownCleanupResult,
    _load_technical_dictionary,
    _extract_prose_words,
    _create_spell_checker,
)


pytestmark = pytest.mark.unit


# ============================================================================
# format_markdown Tests
# ============================================================================


class TestFormatMarkdown:
    """Tests for format_markdown function."""

    def test_format_adds_trailing_newline(self):
        """Test that mdformat adds MD047 trailing newline."""
        text = "# Heading"
        result, changes = format_markdown(text)
        assert result.endswith("\n")
        assert changes > 0

    def test_format_removes_trailing_spaces(self):
        """Test that mdformat removes MD009 trailing spaces."""
        text = "Some text with spaces   \n"
        result, changes = format_markdown(text)
        assert "   \n" not in result

    def test_format_handles_heading_without_space(self):
        """Test mdformat handling of heading without space after hash.

        Note: mdformat preserves the heading but adds trailing newline.
        Some MD linting fixes are not performed by mdformat.
        """
        text = "#Heading without space\n"
        result, _ = format_markdown(text)
        # mdformat preserves this syntax but ensures trailing newline
        assert result.endswith("\n")

    def test_format_removes_multiple_blank_lines(self):
        """Test that mdformat fixes MD012 (multiple blank lines)."""
        text = "Line 1\n\n\n\nLine 2\n"
        result, _ = format_markdown(text)
        # mdformat collapses multiple blank lines to one
        assert "\n\n\n" not in result

    def test_format_no_changes_for_clean_markdown(self):
        """Test that clean markdown has minimal changes."""
        text = "# Heading\n\nSome paragraph text.\n"
        result, changes = format_markdown(text)
        assert result == text
        assert changes == 0

    def test_format_preserves_code_blocks(self):
        """Test that code blocks are preserved."""
        text = "# Code\n\n```python\nprint('hello')\n```\n"
        result, _ = format_markdown(text)
        assert "```python" in result
        assert "print('hello')" in result

    def test_format_handles_empty_string(self):
        """Test handling of empty input."""
        text = ""
        result, changes = format_markdown(text)
        assert result == ""
        assert changes == 0

    def test_format_handles_exception(self):
        """Test graceful handling of mdformat exceptions."""
        with patch("src.utils.markdown_cleanup.mdformat.text", side_effect=Exception("Parse error")):
            text = "Some text"
            result, changes = format_markdown(text)
            assert result == text
            assert changes == 0


# ============================================================================
# check_spelling Tests
# ============================================================================


class TestCheckSpelling:
    """Tests for check_spelling function."""

    def test_check_finds_misspelled_words(self):
        """Test that misspelled words are flagged."""
        text = "This is a tset of misspeling detection.\n"
        flags, words_checked, words_flagged = check_spelling(
            text, technical_terms=set()
        )
        # Should find at least one misspelling
        assert words_flagged > 0
        misspelled_words = {f.word.lower() for f in flags}
        assert "tset" in misspelled_words or "misspeling" in misspelled_words

    def test_check_respects_technical_dictionary(self):
        """Test that technical terms are not flagged."""
        text = "We use kubernetes and terraform for deployment.\n"
        # Without dictionary - might flag these
        flags_no_dict, _, _ = check_spelling(text, technical_terms=set())

        # With dictionary - should not flag
        technical_terms = {"kubernetes", "terraform"}
        flags_with_dict, _, _ = check_spelling(text, technical_terms=technical_terms)

        # With dictionary should have fewer flags
        assert len(flags_with_dict) <= len(flags_no_dict)

    def test_check_includes_suggestions(self):
        """Test that flagged words include suggestions."""
        text = "This has a definately wrong word.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        # Find the flag for "definately"
        definately_flags = [f for f in flags if f.word.lower() == "definately"]
        if definately_flags:
            assert len(definately_flags[0].suggestions) > 0

    def test_check_includes_line_numbers(self):
        """Test that flags include correct line numbers."""
        text = "Line one is fine.\nLine two has a tset.\nLine three is okay.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        tset_flags = [f for f in flags if f.word.lower() == "tset"]
        if tset_flags:
            assert tset_flags[0].line_number == 2

    def test_check_includes_context(self):
        """Test that flags include context."""
        text = "This context contains a tset word.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        for flag in flags:
            assert flag.context  # Should have context
            assert len(flag.context) <= 80  # Truncated to 80 chars

    def test_check_skips_code_blocks(self):
        """Test that code blocks are not spell-checked."""
        text = """# Heading

```python
def tset_function():
    pass
```

Normal text here.
"""
        flags, _, _ = check_spelling(text, technical_terms=set())

        # Should not flag "tset" from inside code block
        flagged_words = {f.word.lower() for f in flags}
        assert "tset" not in flagged_words or any(
            "def" not in f.context for f in flags if f.word.lower() == "tset"
        )

    def test_check_skips_inline_code(self):
        """Test that inline code is skipped."""
        text = "Use `tset_function()` to run tests.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        flagged_words = {f.word.lower() for f in flags}
        assert "tset" not in flagged_words

    def test_check_skips_urls(self):
        """Test that URLs are skipped."""
        text = "Visit https://example.com/tset/path for info.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        # Should not flag "tset" from URL
        url_flags = [f for f in flags if "example.com" in f.context]
        assert len(url_flags) == 0

    def test_check_respects_max_flags(self):
        """Test that max_flags limit is respected."""
        # Create text with many misspellings
        text = " ".join(["tset"] * 100) + "\n"
        flags, _, _ = check_spelling(text, technical_terms=set(), max_flags=5)

        assert len(flags) <= 5

    def test_check_skips_short_words(self):
        """Test that very short words (< 3 chars) are skipped."""
        text = "I am ok with xy in text.\n"
        flags, _, _ = check_spelling(text, technical_terms=set())

        # "xy" should not be flagged (too short)
        flagged_words = {f.word.lower() for f in flags}
        assert "xy" not in flagged_words

    def test_check_returns_word_counts(self):
        """Test that word counts are returned correctly."""
        text = "One two three four five.\n"
        flags, words_checked, words_flagged = check_spelling(
            text, technical_terms=set()
        )

        assert words_checked >= 5
        assert words_flagged == len(flags)


# ============================================================================
# cleanup_markdown Tests
# ============================================================================


class TestCleanupMarkdown:
    """Tests for cleanup_markdown function."""

    def test_cleanup_returns_result_object(self):
        """Test that cleanup returns MarkdownCleanupResult."""
        result = cleanup_markdown("# Test\n", technical_terms=set())
        assert isinstance(result, MarkdownCleanupResult)

    def test_cleanup_applies_formatting(self):
        """Test that formatting is applied by default."""
        text = "# Heading\n\n\n\nParagraph"  # Multiple blank lines
        result = cleanup_markdown(text, technical_terms=set())
        # mdformat collapses multiple blank lines
        assert "\n\n\n" not in result.cleaned_text
        assert result.formatting_changes > 0

    def test_cleanup_can_disable_formatting(self):
        """Test that formatting can be disabled."""
        text = "#No space"
        result = cleanup_markdown(
            text, enable_formatting=False, technical_terms=set()
        )
        assert result.cleaned_text == text
        assert result.formatting_changes == 0

    def test_cleanup_runs_spell_check(self):
        """Test that spell check runs by default."""
        text = "This has a tset word.\n"
        result = cleanup_markdown(text, technical_terms=set())
        assert result.words_checked > 0

    def test_cleanup_can_disable_spell_check(self):
        """Test that spell check can be disabled."""
        text = "This has a tset word.\n"
        result = cleanup_markdown(
            text, enable_spell_check=False, technical_terms=set()
        )
        assert result.words_checked == 0
        assert result.words_flagged == 0
        assert len(result.spelling_flags) == 0

    def test_cleanup_uses_provided_dictionary(self):
        """Test that provided technical terms are used."""
        text = "We use kubernetes for orchestration.\n"
        result = cleanup_markdown(text, technical_terms={"kubernetes"})

        # kubernetes should not be flagged
        flagged_words = {f.word.lower() for f in result.spelling_flags}
        assert "kubernetes" not in flagged_words


# ============================================================================
# format_spelling_flags_for_prompt Tests
# ============================================================================


class TestFormatSpellingFlagsForPrompt:
    """Tests for format_spelling_flags_for_prompt function."""

    def test_format_empty_flags_returns_empty_string(self):
        """Test that empty flags list returns empty string."""
        result = format_spelling_flags_for_prompt([])
        assert result == ""

    def test_format_includes_header(self):
        """Test that output includes header."""
        flags = [
            SpellingFlag(
                word="tset",
                suggestions=["test", "set"],
                line_number=5,
                context="This is a tset",
            )
        ]
        result = format_spelling_flags_for_prompt(flags)
        assert "**Flagged potential misspellings**" in result
        assert "verify against page image" in result

    def test_format_includes_line_numbers(self):
        """Test that line numbers are included."""
        flags = [
            SpellingFlag(
                word="tset",
                suggestions=["test"],
                line_number=42,
                context="context",
            )
        ]
        result = format_spelling_flags_for_prompt(flags)
        assert "Line 42" in result

    def test_format_includes_suggestions(self):
        """Test that suggestions are included."""
        flags = [
            SpellingFlag(
                word="definately",
                suggestions=["definitely", "definite"],
                line_number=1,
                context="context",
            )
        ]
        result = format_spelling_flags_for_prompt(flags)
        assert '"definitely"' in result
        assert '"definite"' in result

    def test_format_includes_word(self):
        """Test that flagged word is included."""
        flags = [
            SpellingFlag(
                word="misspeled",
                suggestions=["misspelled"],
                line_number=1,
                context="context",
            )
        ]
        result = format_spelling_flags_for_prompt(flags)
        assert '"misspeled"' in result

    def test_format_multiple_flags(self):
        """Test formatting of multiple flags."""
        flags = [
            SpellingFlag("tset", ["test"], 1, "context 1"),
            SpellingFlag("wrond", ["wrong", "wring"], 5, "context 2"),
        ]
        result = format_spelling_flags_for_prompt(flags)

        assert "tset" in result
        assert "wrond" in result
        assert "Line 1" in result
        assert "Line 5" in result


# ============================================================================
# Helper Function Tests
# ============================================================================


class TestLoadTechnicalDictionary:
    """Tests for _load_technical_dictionary function."""

    def test_load_returns_set(self):
        """Test that loading returns a set."""
        # Use real file if it exists
        result = _load_technical_dictionary()
        assert isinstance(result, set)

    def test_load_skips_comments(self):
        """Test that comment lines are skipped."""
        mock_content = "# This is a comment\nkubernetes\n# Another comment\nterraform\n"
        with patch("builtins.open", mock_open(read_data=mock_content)):
            with patch("src.utils.markdown_cleanup.TECHNICAL_DICT_PATH") as mock_path:
                mock_path.exists.return_value = True
                mock_path.__str__ = lambda x: "config/technical_dictionary.txt"
                result = _load_technical_dictionary()

        # Comments should not be in result
        assert "#" not in "".join(result)

    def test_load_lowercases_terms(self):
        """Test that terms are lowercased."""
        mock_content = "Kubernetes\nTERRAFORM\nDocker\n"
        with patch("builtins.open", mock_open(read_data=mock_content)):
            with patch("src.utils.markdown_cleanup.TECHNICAL_DICT_PATH") as mock_path:
                mock_path.exists.return_value = True
                result = _load_technical_dictionary()

        assert "kubernetes" in result
        assert "Kubernetes" not in result

    def test_load_handles_missing_file(self):
        """Test graceful handling of missing file."""
        with patch("src.utils.markdown_cleanup.TECHNICAL_DICT_PATH") as mock_path:
            mock_path.exists.return_value = False
            result = _load_technical_dictionary()

        assert result == set()

    def test_load_handles_read_error(self):
        """Test graceful handling of read errors."""
        with patch("src.utils.markdown_cleanup.TECHNICAL_DICT_PATH") as mock_path:
            mock_path.exists.return_value = True
            with patch("builtins.open", side_effect=IOError("Read error")):
                result = _load_technical_dictionary()

        assert result == set()


class TestExtractProseWords:
    """Tests for _extract_prose_words function."""

    def test_extract_basic_words(self):
        """Test extraction of basic prose words."""
        text = "Hello world from test.\n"
        words = _extract_prose_words(text)
        word_list = [w for w, _, _ in words]

        assert "Hello" in word_list
        assert "world" in word_list
        assert "from" in word_list
        assert "test" in word_list

    def test_extract_skips_code_blocks(self):
        """Test that fenced code blocks are skipped."""
        text = """Normal text.

```python
code_variable = value
```

More text.
"""
        words = _extract_prose_words(text)
        word_list = [w.lower() for w, _, _ in words]

        assert "normal" in word_list
        assert "more" in word_list
        assert "code" not in word_list
        assert "variable" not in word_list

    def test_extract_skips_indented_code(self):
        """Test that indented code is skipped."""
        text = "Normal text.\n    indented_code = value\nMore text.\n"
        words = _extract_prose_words(text)
        word_list = [w.lower() for w, _, _ in words]

        assert "normal" in word_list
        assert "indented" not in word_list

    def test_extract_skips_inline_code(self):
        """Test that inline code is skipped."""
        text = "Use `some_function()` to do things.\n"
        words = _extract_prose_words(text)
        word_list = [w.lower() for w, _, _ in words]

        assert "use" in word_list
        assert "some" not in word_list
        assert "function" not in word_list

    def test_extract_removes_urls(self):
        """Test that URLs are removed."""
        text = "Visit https://example.com/path for info.\n"
        words = _extract_prose_words(text)
        word_list = [w.lower() for w, _, _ in words]

        assert "visit" in word_list
        assert "info" in word_list
        # URL parts should not be extracted
        assert "example" not in word_list
        assert "path" not in word_list

    def test_extract_keeps_link_text(self):
        """Test that markdown link text is preserved."""
        text = "Click [here](https://example.com) for more.\n"
        words = _extract_prose_words(text)
        word_list = [w.lower() for w, _, _ in words]

        assert "click" in word_list
        assert "here" in word_list
        assert "more" in word_list

    def test_extract_includes_line_numbers(self):
        """Test that correct line numbers are included."""
        text = "First line.\nSecond line.\nThird line.\n"
        words = _extract_prose_words(text)

        # Find words from each line
        first_line_words = [(w, ln) for w, ln, _ in words if ln == 1]
        second_line_words = [(w, ln) for w, ln, _ in words if ln == 2]

        assert len(first_line_words) > 0
        assert len(second_line_words) > 0

    def test_extract_includes_context(self):
        """Test that context is included."""
        text = "This is a test line with context.\n"
        words = _extract_prose_words(text)

        for word, line_num, context in words:
            assert context  # Should have context
            assert word in context or word.lower() in context.lower()

    def test_extract_filters_short_words(self):
        """Test that very short words (1 char) are filtered."""
        text = "I am a test.\n"
        words = _extract_prose_words(text)
        word_list = [w for w, _, _ in words]

        # Single letter words should not be included
        assert "I" not in word_list
        assert "a" not in word_list


class TestCreateSpellChecker:
    """Tests for _create_spell_checker function."""

    def test_create_returns_spell_checker(self):
        """Test that spell checker is returned."""
        spell = _create_spell_checker(set())
        assert spell is not None

    def test_create_loads_technical_terms(self):
        """Test that technical terms are loaded."""
        technical_terms = {"kubernetes", "terraform", "ansible"}
        spell = _create_spell_checker(technical_terms)

        # Technical terms should be known
        unknown = spell.unknown(technical_terms)
        assert len(unknown) == 0

    def test_create_without_terms(self):
        """Test creation without technical terms."""
        spell = _create_spell_checker(set())

        # Common words should be known
        assert len(spell.unknown({"hello", "world"})) == 0
