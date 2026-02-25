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
    replace_pua_hyphens,
    sanitize_extracted_text,
    strip_remaining_pua,
    validate_urls,
)

pytestmark = pytest.mark.unit


# ============================================================================
# replace_pua_hyphens Tests (v0 — only between alphanumerics)
# ============================================================================


class TestReplacePuaHyphens:
    """Tests for PUA-to-hyphen replacement (v0 safe)."""

    def test_replaces_pua_between_alpha_with_hyphen(self):
        text = "AI\ue088Leaders.org"
        assert replace_pua_hyphens(text) == "AI-Leaders.org"

    def test_replaces_consecutive_pua_with_single_hyphen(self):
        text = "foo\ue001\ue002bar"
        assert replace_pua_hyphens(text) == "foo-bar"

    def test_replaces_pua_between_digit_and_letter(self):
        text = "v2\ue000beta"
        assert replace_pua_hyphens(text) == "v2-beta"

    def test_leaves_pua_adjacent_to_space_untouched(self):
        """PUA next to whitespace is NOT replaced — left for LLM."""
        text = "System \ue081CMS\ue082 which"
        # Only between-alpha PUA is replaced; others are preserved
        assert "\ue081" in replace_pua_hyphens(text)
        assert "\ue082" in replace_pua_hyphens(text)

    def test_leaves_pua_at_boundaries_untouched(self):
        """PUA at start/end of string is NOT replaced — left for LLM."""
        text = "\ue000Hello\uf8ff"
        assert "\ue000" in replace_pua_hyphens(text)
        assert "\uf8ff" in replace_pua_hyphens(text)

    def test_preserves_normal_text(self):
        text = "Hello, world! café résumé"
        assert replace_pua_hyphens(text) == text

    def test_empty_string(self):
        assert replace_pua_hyphens("") == ""


# ============================================================================
# strip_remaining_pua Tests (v3 — remove all leftover PUA)
# ============================================================================


class TestStripRemainingPua:
    """Tests for v3 PUA stripping safety net."""

    def test_strips_all_pua(self):
        text = "System \ue081(CMS)\ue082 which"
        assert strip_remaining_pua(text) == "System (CMS) which"

    def test_strips_pua_at_boundaries(self):
        text = "\ue000Hello\uf8ff"
        assert strip_remaining_pua(text) == "Hello"

    def test_strips_only_pua(self):
        assert strip_remaining_pua("\ue000\ue001\ue002") == ""

    def test_preserves_normal_text(self):
        text = "Hello, world! café résumé 你好"
        assert strip_remaining_pua(text) == text

    def test_preserves_emoji(self):
        text = "Hello 👋 world 🌍"
        assert strip_remaining_pua(text) == text

    def test_strips_supplementary_pua(self):
        text = "test\U000F0001more\U00100001end"
        assert strip_remaining_pua(text) == "testmoreend"


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
        """Relative image paths like figures/file.png must NOT get http://."""
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
        """URL without netloc is flagged as broken."""
        broken = validate_urls("Visit http:///path here")
        assert "http:///path" in broken

    def test_passes_valid_url(self):
        broken = validate_urls("http://example.com/page")
        assert broken == []


# ============================================================================
# sanitize_extracted_text Tests
# ============================================================================


class TestSanitizeExtractedText:
    """Tests for the lightweight v0 sanitization."""

    def test_replaces_pua_hyphens_and_normalizes(self):
        """PUA between alpha → hyphen, NFKC applied."""
        text = "AI\ue088Leaders cafe\u0301"
        result = sanitize_extracted_text(text)
        assert result == "AI-Leaders café"

    def test_preserves_non_hyphen_pua_for_llm(self):
        """PUA chars that might be parens/brackets are LEFT for LLM to fix."""
        text = "System \ue081CMS\ue082 which"
        result = sanitize_extracted_text(text)
        # PUA chars adjacent to space are preserved — LLM compares to image
        assert "\ue081" in result
        assert "\ue082" in result

    def test_preserves_whitespace_for_llm(self):
        """Does NOT collapse whitespace — LLM agents need original layout."""
        text = "hello    world\n\n\n\nparagraph"
        assert sanitize_extracted_text(text) == text

    def test_preserves_quotes(self):
        """Does NOT normalize quotes — that's for v3 cleanup."""
        text = "\u201cHello\u201d"
        assert sanitize_extracted_text(text) == text

    def test_real_wordpress_v0(self):
        """Simulates Docling v0 from the WordPress PDF.

        PUA between alpha (AI-Leaders) → hyphen.
        PUA adjacent to space (around CMS) → preserved for LLM.
        """
        text = (
            "Content Management System \ue081CMS\ue082 which\n"
            "AI\ue088Leaders.org"
        )
        result = sanitize_extracted_text(text)
        # Hyphen case fixed
        assert "AI-Leaders.org" in result
        # Non-hyphen PUA preserved for LLM
        assert "\ue081" in result
        assert "\ue082" in result


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
