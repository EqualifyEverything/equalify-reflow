"""Unit tests for text cleanup utilities.

Tests deterministic text normalization functions used in the v0 and v3 steps.
"""

import pytest
from src.utils.text_cleanup import (
    cleanup_markdown,
    collapse_letter_spacing,
    fix_excessive_whitespace,
    fix_url_formatting,
    normalize_quotes,
    normalize_unicode,
    replace_pua_chars,
    sanitize_extracted_text,
    strip_replacement_chars,
    validate_urls,
)

pytestmark = pytest.mark.unit


# ============================================================================
# replace_pua_chars Tests (v0)
# ============================================================================


class TestReplacePuaChars:
    """Tests for context-aware PUA replacement at v0."""

    def test_pua_between_alpha_becomes_hyphen(self):
        text = "AI\ue088Leaders.org"
        assert replace_pua_chars(text) == "AI-Leaders.org"

    def test_consecutive_pua_between_alpha_single_hyphen(self):
        text = "foo\ue001\ue002bar"
        assert replace_pua_chars(text) == "foo-bar"

    def test_pua_between_digit_and_letter_becomes_hyphen(self):
        text = "v2\ue000beta"
        assert replace_pua_chars(text) == "v2-beta"

    def test_pua_adjacent_to_space_becomes_fffd(self):
        """PUA next to whitespace → U+FFFD so LLM can see the problem."""
        text = "System \ue081CMS\ue082 which"
        result = replace_pua_chars(text)
        assert result == "System \ufffdCMS\ufffd which"

    def test_pua_at_start_becomes_fffd(self):
        text = "\ue000Hello"
        assert replace_pua_chars(text) == "\ufffdHello"

    def test_pua_at_end_becomes_fffd(self):
        text = "Hello\uf8ff"
        assert replace_pua_chars(text) == "Hello\ufffd"

    def test_mixed_hyphen_and_fffd(self):
        """Mix of inter-alpha and non-inter-alpha PUA."""
        text = "\ue081Hello\ue082 AI\ue088Leaders"
        result = replace_pua_chars(text)
        assert result == "\ufffdHello\ufffd AI-Leaders"

    def test_preserves_normal_text(self):
        text = "Hello, world! café résumé"
        assert replace_pua_chars(text) == text

    def test_preserves_emoji(self):
        text = "Hello 👋 world 🌍"
        assert replace_pua_chars(text) == text

    def test_empty_string(self):
        assert replace_pua_chars("") == ""


# ============================================================================
# strip_replacement_chars Tests (v3)
# ============================================================================


class TestStripReplacementChars:
    """Tests for v3 safety-net stripping of U+FFFD and leftover PUA."""

    def test_strips_fffd(self):
        text = "System \ufffd(CMS)\ufffd which"
        assert strip_replacement_chars(text) == "System (CMS) which"

    def test_strips_leftover_pua(self):
        text = "System \ue081(CMS)\ue082 which"
        assert strip_replacement_chars(text) == "System (CMS) which"

    def test_strips_both_fffd_and_pua(self):
        text = "\ufffdHello\ue082 world"
        assert strip_replacement_chars(text) == "Hello world"

    def test_preserves_normal_text(self):
        text = "Hello, world! café résumé 你好"
        assert strip_replacement_chars(text) == text

    def test_preserves_emoji(self):
        text = "Hello 👋 world 🌍"
        assert strip_replacement_chars(text) == text


# ============================================================================
# normalize_unicode Tests
# ============================================================================


class TestNormalizeUnicode:
    """Tests for NFKC unicode normalization."""

    def test_normalizes_composed_diacritics(self):
        text = "cafe\u0301"
        assert normalize_unicode(text) == "café"

    def test_normalizes_compatibility_chars(self):
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

    def test_adds_protocol_to_bare_domain(self):
        text = "[site](example.com)"
        assert fix_url_formatting(text) == "[site](http://example.com)"

    def test_adds_protocol_to_bare_domain_with_path(self):
        text = "[site](example.com/page)"
        assert fix_url_formatting(text) == "[site](http://example.com/page)"

    def test_preserves_existing_protocol(self):
        text = "[site](https://example.com)"
        assert fix_url_formatting(text) == text

    def test_preserves_mailto(self):
        text = "[email](mailto:a@b.com)"
        assert fix_url_formatting(text) == text

    def test_preserves_relative_image_path(self):
        text = "![alt text](figures/figure-2.png)"
        assert fix_url_formatting(text) == text

    def test_preserves_relative_path_no_extension(self):
        text = "[link](section/page)"
        assert fix_url_formatting(text) == text

    def test_preserves_dot_slash_path(self):
        text = "[link](./local-file.md)"
        assert fix_url_formatting(text) == text

    def test_preserves_parent_relative_path(self):
        text = "[link](../other/file.md)"
        assert fix_url_formatting(text) == text

    def test_preserves_anchor_link(self):
        text = "[link](#section-id)"
        assert fix_url_formatting(text) == text

    def test_preserves_absolute_path(self):
        text = "[link](/root/path/file)"
        assert fix_url_formatting(text) == text


# ============================================================================
# validate_urls Tests
# ============================================================================


class TestValidateUrls:
    """Tests for URL validation (logging only)."""

    def test_finds_broken_url(self):
        broken = validate_urls("Visit http:///path here")
        assert "http:///path" in broken

    def test_passes_valid_url(self):
        broken = validate_urls("http://example.com/page")
        assert broken == []


# ============================================================================
# sanitize_extracted_text Tests (v0)
# ============================================================================


class TestSanitizeExtractedText:
    """Tests for the lightweight v0 sanitization."""

    def test_pua_hyphen_and_nfkc(self):
        text = "AI\ue088Leaders cafe\u0301"
        result = sanitize_extracted_text(text)
        assert result == "AI-Leaders café"

    def test_non_hyphen_pua_becomes_fffd(self):
        """PUA chars that might be parens → U+FFFD for LLM visibility."""
        text = "System \ue081CMS\ue082 which"
        result = sanitize_extracted_text(text)
        assert "\ue081" not in result
        assert "\ue082" not in result
        assert "\ufffd" in result
        assert "System \ufffdCMS\ufffd which" == result

    def test_preserves_whitespace_for_llm(self):
        text = "hello    world\n\n\n\nparagraph"
        assert sanitize_extracted_text(text) == text

    def test_preserves_quotes(self):
        text = "\u201cHello\u201d"
        assert sanitize_extracted_text(text) == text

    def test_real_wordpress_v0(self):
        """Simulates Docling v0 from the WordPress PDF."""
        text = (
            "Content Management System \ue081CMS\ue082 which\n"
            "AI\ue088Leaders.org"
        )
        result = sanitize_extracted_text(text)
        assert "AI-Leaders.org" in result
        assert "\ufffdCMS\ufffd" in result
        assert "\ue081" not in result


# ============================================================================
# cleanup_markdown Integration Tests (v3)
# ============================================================================


class TestCleanupMarkdown:
    """Tests for the full v3 cleanup pipeline."""

    def test_strips_fffd_and_pua(self):
        """Full pipeline removes U+FFFD and any leftover PUA."""
        text = "System \ufffd(CMS)\ufffd which"
        result = cleanup_markdown(text, log_warnings=False)
        assert "\ufffd" not in result
        assert "(CMS)" in result

    def test_real_world_after_llm_fixed_parens(self):
        """Simulates v2 where LLM replaced U+FFFD with real parens."""
        text = (
            "Content Management System (CMS) which "
            "\u201csimplifies managing dynamic\u201d sites.\n\n\n\n"
            "AI-Leaders.org - Workforce"
        )
        result = cleanup_markdown(text, log_warnings=False)
        assert "(CMS)" in result
        assert '"simplifies managing dynamic"' in result
        assert "\n\n\n" not in result
        assert "AI-Leaders.org" in result

    def test_real_world_llm_didnt_fix(self):
        """Simulates v2 where LLM left U+FFFD — v3 strips them."""
        text = (
            "Content Management System \ufffdCMS\ufffd which "
            "\u201csimplifies managing dynamic\u201d sites.\n\n\n\n"
            "AI-Leaders.org - Workforce"
        )
        result = cleanup_markdown(text, log_warnings=False)
        assert "\ufffd" not in result
        assert "System CMS which" in result
        assert "AI-Leaders.org" in result

    def test_idempotent_on_clean_text(self):
        text = "# Heading\n\nClean paragraph with normal text."
        assert cleanup_markdown(text, log_warnings=False) == text

    def test_all_fixes_applied_together(self):
        text = (
            "\ue000\u201cHello\u201d  world\n\n\n"
            "[link](example.com)"
        )
        result = cleanup_markdown(text, log_warnings=False)
        assert result == '"Hello" world\n\n[link](http://example.com)'
