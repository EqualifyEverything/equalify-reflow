"""Unit tests for poster creation mode in the pipeline viewer.

Tests the two-mode system: base_edit for correction (existing documents) and
base_create for composition (poster-type PDFs where Docling extracts mostly
images and very little text).
"""

from __future__ import annotations

import pytest

from src.services.pipeline_viewer import (
    _compose_page_prompt,
    _load_fragment,
)
from src.services.pipeline_viewer_models import (
    LayoutType,
    PageAttributes,
)


# ---------------------------------------------------------------------------
# LayoutType.POSTER enum
# ---------------------------------------------------------------------------


class TestLayoutTypePoster:
    """Test that the POSTER variant is properly defined on LayoutType."""

    def test_poster_value(self):
        """LayoutType.POSTER should have string value 'poster'."""
        assert LayoutType.POSTER.value == "poster"

    def test_poster_from_string(self):
        """LayoutType('poster') should resolve to LayoutType.POSTER."""
        assert LayoutType("poster") == LayoutType.POSTER


# ---------------------------------------------------------------------------
# Fragment loading
# ---------------------------------------------------------------------------


class TestFragmentLoading:
    """Test that new prompt fragments load correctly."""

    def test_load_base_create_fragment(self):
        """base_create.md should load non-empty content with rewrite_page."""
        content = _load_fragment("base_create")
        assert content != ""
        assert "rewrite_page" in content

    def test_load_poster_layout_fragment(self):
        """layout/poster.md should load non-empty content."""
        content = _load_fragment("layout/poster")
        assert content != ""
        assert "Poster" in content

    def test_base_contains_moved_preamble(self):
        """base.md should contain the preamble previously hardcoded in _run_page_agent."""
        content = _load_fragment("base")
        assert "document correction agent" in content

    def test_base_create_contains_creation_procedure(self):
        """base_create.md should contain the creation procedure heading."""
        content = _load_fragment("base_create")
        assert "Page Content Creation Procedure" in content

    def test_base_contains_correction_procedure(self):
        """base.md should still contain the correction procedure heading."""
        content = _load_fragment("base")
        assert "Page Correction Procedure" in content


# ---------------------------------------------------------------------------
# _compose_page_prompt mode selection
# ---------------------------------------------------------------------------


class TestComposePagePromptModeSelection:
    """Test that _compose_page_prompt selects the correct base prompt."""

    def test_poster_selects_base_create(self):
        """Poster layout should produce prompt with 'Page Content Creation Procedure'."""
        attrs = PageAttributes(layout=LayoutType.POSTER)
        prompt = _compose_page_prompt(attrs)
        assert "Page Content Creation Procedure" in prompt

    def test_poster_does_not_include_correction_procedure(self):
        """Poster layout should NOT include the correction procedure."""
        attrs = PageAttributes(layout=LayoutType.POSTER)
        prompt = _compose_page_prompt(attrs)
        assert "Page Correction Procedure" not in prompt

    def test_single_column_selects_base_edit(self):
        """Non-poster layout should produce prompt with 'Page Correction Procedure'."""
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN)
        prompt = _compose_page_prompt(attrs)
        assert "Page Correction Procedure" in prompt
        assert "Page Content Creation Procedure" not in prompt

    def test_double_column_selects_base_edit(self):
        """Double column layout should use edit mode."""
        attrs = PageAttributes(layout=LayoutType.DOUBLE_COLUMN)
        prompt = _compose_page_prompt(attrs)
        assert "Page Correction Procedure" in prompt
        assert "Page Content Creation Procedure" not in prompt

    def test_presentation_selects_base_edit(self):
        """Presentation layout should use edit mode."""
        attrs = PageAttributes(layout=LayoutType.PRESENTATION)
        prompt = _compose_page_prompt(attrs)
        assert "Page Correction Procedure" in prompt
        assert "Page Content Creation Procedure" not in prompt

    def test_poster_stacks_poster_layout_fragment(self):
        """Poster layout should include the poster layout fragment."""
        attrs = PageAttributes(layout=LayoutType.POSTER)
        prompt = _compose_page_prompt(attrs)
        assert "Poster Layout" in prompt

    def test_poster_stacks_content_fragments(self):
        """Poster with has_images should include both create base and image fragment."""
        attrs = PageAttributes(layout=LayoutType.POSTER, has_images=True)
        prompt = _compose_page_prompt(attrs)
        assert "Page Content Creation Procedure" in prompt
        assert "Images" in prompt

    def test_poster_includes_preamble_in_base_create(self):
        """base_create should contain its own preamble (not the edit preamble)."""
        attrs = PageAttributes(layout=LayoutType.POSTER)
        prompt = _compose_page_prompt(attrs)
        # base_create should mention composing/creating, not correcting
        assert "composing markdown" in prompt.lower() or "compose" in prompt.lower()

    def test_edit_mode_includes_preamble_in_base(self):
        """base.md should contain the moved preamble for edit mode."""
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN)
        prompt = _compose_page_prompt(attrs)
        assert "document correction agent" in prompt
