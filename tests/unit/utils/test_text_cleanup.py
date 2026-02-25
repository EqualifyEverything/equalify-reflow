"""Unit tests for text cleanup utilities.

Tests deterministic text normalization functions used in the v3 cleanup step.
"""

import pytest
from src.utils.text_cleanup import (
    cleanup_markdown,
    collapse_letter_spacing,
    fix_excessive_whitespace,
    fix_url_formatting,
    normalize_quotes,
    normalize_unicode,
    strip_private_use_chars,
    validate_urls,
)

pytestmark = pytest.mark.unit


# ============================================================================
# strip_private_use_chars Tests
# ============================================================================


class TestStripPrivateUseChars:
    """Tests for PUA character replacement/removal."""

    def test_removes_pua_adjacent_to_space(self):
        """PUA chars next to whitespace are removed (decorative)."""
        text = "Content Management System \ue081(CMS)\ue082 which"
        assert strip_private_use_chars(text) == "Content Management System (CMS) which"

    def test_replaces_pua_between_alpha_with_hyphen(self):
        """PUA between alphanumerics becomes a hyphen (likely encoded dash)."""
        text = "AI\ue088Leaders.org"
        assert strip_private_use_chars(text) == "AI-Leaders.org"

    def test_replaces_consecutive_pua_between_alpha_with_single_hyphen(self):
        """Multiple consecutive PUA chars between alphanumerics → one hyphen."""
        text = "foo\ue001\ue002bar"
        assert strip_private_use_chars(text) == "foo-bar"

    def test_mixed_contexts(self):
        """Mix of decorative and hyphen PUA chars in one string."""
        text = "\ue081Hello\ue082 AI\ue088Leaders"
        assert strip_private_use_chars(text) == "Hello AI-Leaders"

    def test_preserves_normal_text(self):
        """Normal ASCII and common Unicode text is untouched."""
        text = "Hello, world! café résumé 你好"
        assert strip_private_use_chars(text) == text

    def test_preserves_emoji(self):
        """Emoji (outside PUA) are preserved."""
        text = "Hello 👋 world 🌍"
        assert strip_private_use_chars(text) == text

    def test_empty_string(self):
        assert strip_private_use_chars("") == ""

    def test_only_pua_chars(self):
        """String of only PUA characters becomes empty."""
        assert strip_private_use_chars("\ue000\ue001\ue002") == ""

    def test_pua_at_start_and_end(self):
        text = "\ue000Hello\uf8ff"
        assert strip_private_use_chars(text) == "Hello"

    def test_pua_between_digit_and_letter(self):
        """PUA between digit and letter → hyphen."""
        text = "v2\ue000beta"
        assert strip_private_use_chars(text) == "v2-beta"

    def test_supplementary_pua_a_between_alpha(self):
        """Supplementary PUA-A between alphanumerics → hyphen."""
        text = "test\U000F0001text"
        assert strip_private_use_chars(text) == "test-text"

    def test_supplementary_pua_b_removed_at_boundary(self):
        """Supplementary PUA-B at start is removed."""
        text = "\U00100001test"
        assert strip_private_use_chars(text) == "test"


# ============================================================================
# normalize_unicode Tests
# ============================================================================


class TestNormalizeUnicode:
    """Tests for NFKC unicode normalization."""

    def test_normalizes_composed_diacritics(self):
        """Combining diacritics are composed into single codepoints."""
        # e + combining acute accent → é
        text = "cafe\u0301"
        assert normalize_unicode(text) == "café"

    def test_normalizes_compatibility_chars(self):
        """NFKC normalizes compatibility characters."""
        # ﬁ ligature → fi
        text = "ﬁnd"
        assert normalize_unicode(text) == "find"

    def test_preserves_normal_text(self):
        text = "Normal text with no issues"
        assert normalize_unicode(text) == text


# ============================================================================
# collapse_letter_spacing Tests
# ============================================================================


class TestCollapseLetterSpacing:
    """Tests for OCR letter-spacing artifact repair."""

    def test_collapses_spaced_word(self):
        text = "the r e q u i r e m e n t s are met"
        assert collapse_letter_spacing(text) == "the requirements are met"

    def test_ignores_short_sequences(self):
        """Sequences of 3 or fewer letters are not collapsed (safety)."""
        text = "a b c stays"
        assert collapse_letter_spacing(text) == "a b c stays"

    def test_preserves_normal_text(self):
        text = "normal text stays unchanged"
        assert collapse_letter_spacing(text) == text


# ============================================================================
# normalize_quotes Tests
# ============================================================================


class TestNormalizeQuotes:
    """Tests for smart quote normalization."""

    def test_normalizes_double_curly_quotes(self):
        assert normalize_quotes("\u201cHello\u201d") == '"Hello"'

    def test_normalizes_single_curly_quotes(self):
        assert normalize_quotes("\u2018it\u2019s") == "'it's"

    def test_normalizes_guillemets(self):
        assert normalize_quotes("\u00abquote\u00bb") == '"quote"'

    def test_preserves_ascii_quotes(self):
        text = '"hello" and \'world\''
        assert normalize_quotes(text) == text


# ============================================================================
# fix_excessive_whitespace Tests
# ============================================================================


class TestFixExcessiveWhitespace:
    """Tests for whitespace cleanup."""

    def test_collapses_multiple_spaces(self):
        assert fix_excessive_whitespace("hello    world") == "hello world"

    def test_collapses_triple_newlines(self):
        assert fix_excessive_whitespace("para1\n\n\npara2") == "para1\n\npara2"

    def test_preserves_double_newlines(self):
        text = "para1\n\npara2"
        assert fix_excessive_whitespace(text) == text

    def test_strips_trailing_whitespace(self):
        assert fix_excessive_whitespace("hello   \nworld  ") == "hello\nworld"


# ============================================================================
# fix_url_formatting Tests
# ============================================================================


class TestFixUrlFormatting:
    """Tests for URL protocol insertion."""

    def test_adds_protocol_to_bare_url(self):
        text = "[site](example.com)"
        assert fix_url_formatting(text) == "[site](http://example.com)"

    def test_preserves_existing_protocol(self):
        text = "[site](https://example.com)"
        assert fix_url_formatting(text) == text

    def test_preserves_mailto(self):
        text = "[email](mailto:a@b.com)"
        assert fix_url_formatting(text) == text


# ============================================================================
# validate_urls Tests
# ============================================================================


class TestValidateUrls:
    """Tests for URL validation (logging only)."""

    def test_finds_broken_url(self):
        """URL without netloc is flagged as broken."""
        broken = validate_urls("Visit http:///path here")
        assert "http:///path" in broken

    def test_passes_valid_url(self):
        broken = validate_urls("http://example.com/page")
        assert broken == []


# ============================================================================
# cleanup_markdown Integration Tests
# ============================================================================


class TestCleanupMarkdown:
    """Tests for the full cleanup pipeline."""

    def test_strips_pua_and_normalizes(self):
        """Full pipeline removes PUA chars and normalizes text."""
        text = "System \ue081(CMS)\ue082 which"
        result = cleanup_markdown(text, log_warnings=False)
        assert "\ue081" not in result
        assert "\ue082" not in result
        assert "(CMS)" in result

    def test_real_world_pdf_artifacts(self):
        """Simulates the WordPress PDF output with PUA chars."""
        text = (
            "Content Management System \ue081(CMS)\ue082 which "
            "\u201csimplifies managing dynamic\u201d sites.\n\n\n\n"
            "AI\ue088Leaders.org - Workforce"
        )
        result = cleanup_markdown(text, log_warnings=False)
        # PUA stripped/replaced
        assert "\ue081" not in result
        assert "\ue082" not in result
        assert "\ue088" not in result
        # Quotes normalised
        assert "\u201c" not in result
        assert "\u201d" not in result
        assert '"simplifies managing dynamic"' in result
        # Whitespace collapsed
        assert "\n\n\n" not in result
        # PUA between alpha → hyphen preserves domain
        assert "AI-Leaders.org" in result

    def test_idempotent_on_clean_text(self):
        """Running cleanup on already-clean text is a no-op."""
        text = "# Heading\n\nClean paragraph with normal text."
        assert cleanup_markdown(text, log_warnings=False) == text

    def test_all_fixes_applied_together(self):
        """Verify ordering: PUA strip → quotes → NFKC → spacing → whitespace → URLs."""
        text = (
            "\ue000\u201cHello\u201d  world\n\n\n"
            "[link](example.com)"
        )
        result = cleanup_markdown(text, log_warnings=False)
        assert result == '"Hello" world\n\n[link](http://example.com)'
