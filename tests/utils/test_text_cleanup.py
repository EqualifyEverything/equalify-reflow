"""Tests for text cleanup utilities."""

from src.utils.text_cleanup import (
    cleanup_markdown,
    fix_excessive_whitespace,
    fix_url_formatting,
    normalize_quotes,
    normalize_unicode,
    validate_urls,
)


class TestNormalizeUnicode:
    """Tests for unicode normalization."""

    def test_normalizes_composed_characters(self):
        # Café with combining accent
        text = "cafe\u0301"
        result = normalize_unicode(text)
        assert result == "café"

    def test_handles_already_normalized_text(self):
        text = "Hello World"
        assert normalize_unicode(text) == text

    def test_handles_diacritics(self):
        text = "Prlic"  # Could have various diacritic forms
        result = normalize_unicode(text)
        # Should be normalized to canonical form
        assert isinstance(result, str)


class TestFixExcessiveWhitespace:
    """Tests for whitespace cleanup."""

    def test_removes_multiple_spaces(self):
        text = "hello    world"
        assert fix_excessive_whitespace(text) == "hello world"

    def test_preserves_double_newlines(self):
        text = "para1\n\npara2"
        assert fix_excessive_whitespace(text) == "para1\n\npara2"

    def test_reduces_triple_newlines_to_double(self):
        text = "para1\n\n\npara2"
        assert fix_excessive_whitespace(text) == "para1\n\npara2"

    def test_removes_trailing_whitespace_on_lines(self):
        text = "line1   \nline2  \n"
        result = fix_excessive_whitespace(text)
        assert result == "line1\nline2\n"


class TestNormalizeQuotes:
    """Tests for quote normalization."""

    def test_normalizes_curly_double_quotes(self):
        text = "\u201cHello\u201d"  # "Hello" with smart quotes
        result = normalize_quotes(text)
        # Should be ASCII double quotes
        assert result == '"Hello"'
        assert ord(result[0]) == 34  # ASCII quote

    def test_normalizes_curly_single_quotes(self):
        text = "\u2018Hello\u2019"  # 'Hello' with smart quotes
        result = normalize_quotes(text)
        # Should be ASCII apostrophes
        assert result == "'Hello'"
        assert ord(result[0]) == 39  # ASCII apostrophe

    def test_handles_mixed_quotes(self):
        text = "\u201cShe\u2019s here\u201d"  # "She's here" with smart quotes
        result = normalize_quotes(text)
        # Should all be ASCII
        assert result == "\"She's here\""
        assert ord(result[0]) == 34  # Double quote
        assert ord(result[4]) == 39  # Apostrophe

    def test_handles_guillemets(self):
        text = "«Hello»"
        result = normalize_quotes(text)
        assert result == '"Hello"'
        assert ord(result[0]) == 34  # ASCII quote


class TestValidateUrls:
    """Tests for URL validation."""

    def test_finds_valid_urls(self):
        text = "Visit http://example.com for more"
        broken = validate_urls(text)
        assert len(broken) == 0

    def test_finds_broken_urls(self):
        # URL without netloc (domain) should be flagged as broken
        text = "Visit http:/// for info"
        broken = validate_urls(text)
        # Malformed URL should be flagged
        assert len(broken) >= 1

    def test_handles_https_urls(self):
        text = "Secure site: https://example.com/path"
        broken = validate_urls(text)
        assert len(broken) == 0

    def test_finds_multiple_urls(self):
        text = "Visit http://site1.com and http://site2.com"
        broken = validate_urls(text)
        # Both valid
        assert len(broken) == 0


class TestFixUrlFormatting:
    """Tests for URL formatting fixes."""

    def test_adds_protocol_to_markdown_links(self):
        text = "[Example](example.com)"
        result = fix_url_formatting(text)
        assert "[Example](http://example.com)" in result

    def test_preserves_existing_protocols(self):
        text = "[Example](https://example.com)"
        result = fix_url_formatting(text)
        assert result == text

    def test_handles_multiple_links(self):
        text = "[Site1](site1.com) and [Site2](https://site2.com)"
        result = fix_url_formatting(text)
        assert "http://site1.com" in result
        assert "https://site2.com" in result


class TestCleanupMarkdown:
    """Integration tests for safe cleanup pipeline."""

    def test_applies_safe_fixes_only(self):
        text = """de-
veloping  code with \u201csmart quotes\u201d

Multiple   spaces and \n\n\n triple newlines.

Smith, J. Bibliography entry."""

        result = cleanup_markdown(text, log_warnings=False)

        # Check SAFE fixes were applied
        assert '"smart quotes"' in result  # Quotes normalized (SAFE)
        assert "Multiple spaces" in result  # Whitespace fixed (SAFE)

        # Check RISKY fixes were SKIPPED (LLM will handle these)
        assert "de-\nveloping" in result  # Hyphenation NOT fixed (would break on footnotes)
        assert "Smith, J." in result  # Bibliography NOT indented (could misformat)

    def test_handles_empty_string(self):
        result = cleanup_markdown("", log_warnings=False)
        assert result == ""

    def test_handles_already_clean_text(self):
        text = "Clean text with proper formatting."
        result = cleanup_markdown(text, log_warnings=False)
        assert result == text

    def test_preserves_markdown_structure(self):
        text = """# Header

Paragraph with [link](http://example.com).

- List item 1
- List item 2"""

        result = cleanup_markdown(text, log_warnings=False)
        assert "# Header" in result
        assert "[link]" in result
        assert "- List item" in result


class TestRealWorldExample:
    """Test with actual PDF conversion artifacts."""

    def test_academic_paper_safe_cleanup(self):
        # Based on the real example from result.md
        # Test that we DON'T break things with aggressive hyphenation fixes
        text = """How to Scale a Code in the Human Dimen-
sion

Matthew J. Turk

Abstract: As scientists' needs for computa-
tional techniques grow.

## 1 Why 'Community?'

Astrophysics is dominated by vertically-
integrated, small-
population research."""

        result = cleanup_markdown(text, log_warnings=False)

        # Hyphenation should be PRESERVED (not "fixed" - LLM will handle it)
        # This prevents breaking across footnotes/columns
        assert "Dimen-" in result  # Hyphenation preserved
        assert "computa-" in result  # Hyphenation preserved

    def test_url_and_quote_cleanup(self):
        text = 'Visit http://yt-project.org/ for \u201cmore info\u201d.'
        result = cleanup_markdown(text, log_warnings=False)

        # Quotes should be normalized (SAFE operation)
        assert '"more info"' in result
