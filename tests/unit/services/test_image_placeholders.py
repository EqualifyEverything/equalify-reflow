"""Unit tests for _replace_image_placeholders in pipeline_viewer."""

from __future__ import annotations

import pytest

from src.services.pipeline_viewer import _replace_image_placeholders

pytestmark = pytest.mark.unit
from src.services.pipeline_viewer_models import FigureData


def _fig(ref_id: str, caption: str = "", page: int = 1) -> FigureData:
    """Helper to create a FigureData with minimal fields."""
    return FigureData(
        ref_id=ref_id,
        caption=caption,
        page_number=page,
        image_base64="AAAA",  # not used by replacement
    )


class TestReplaceImagePlaceholders:
    """Tests for sequential <!-- image --> replacement."""

    def test_single_placeholder_single_figure(self):
        md = "Before\n\n<!-- image -->\n\nAfter"
        figures = [_fig("figure-1", "A chart")]
        result = _replace_image_placeholders(md, figures)
        assert "![A chart](figures/figure-1.png)" in result
        assert "<!-- image -->" not in result

    def test_multiple_placeholders_match_figure_order(self):
        md = "First\n\n<!-- image -->\n\nSecond\n\n<!-- image -->\n\nThird"
        figures = [
            _fig("figure-1", "First fig"),
            _fig("figure-2", "Second fig"),
        ]
        result = _replace_image_placeholders(md, figures)
        assert "![First fig](figures/figure-1.png)" in result
        assert "![Second fig](figures/figure-2.png)" in result
        assert "<!-- image -->" not in result

    def test_more_placeholders_than_figures_leaves_extras(self):
        md = "<!-- image -->\n\n<!-- image -->\n\n<!-- image -->"
        figures = [_fig("figure-1", "Only one")]
        result = _replace_image_placeholders(md, figures)
        assert "![Only one](figures/figure-1.png)" in result
        assert result.count("<!-- image -->") == 2

    def test_more_figures_than_placeholders_ignores_extras(self):
        md = "<!-- image -->"
        figures = [
            _fig("figure-1", "Used"),
            _fig("figure-2", "Ignored"),
        ]
        result = _replace_image_placeholders(md, figures)
        assert "![Used](figures/figure-1.png)" in result
        assert "figure-2" not in result

    def test_no_figures_returns_unchanged(self):
        md = "Some text\n\n<!-- image -->\n\nMore text"
        result = _replace_image_placeholders(md, [])
        assert result == md

    def test_no_placeholders_returns_unchanged(self):
        md = "Some text without any images"
        figures = [_fig("figure-1", "Orphan")]
        result = _replace_image_placeholders(md, figures)
        assert result == md

    def test_empty_caption(self):
        md = "<!-- image -->"
        figures = [_fig("figure-1", "")]
        result = _replace_image_placeholders(md, figures)
        assert result == "![](figures/figure-1.png)"

    def test_caption_with_brackets_escaped(self):
        md = "<!-- image -->"
        figures = [_fig("figure-1", "Figure [1]")]
        result = _replace_image_placeholders(md, figures)
        assert "![Figure [1\\]](figures/figure-1.png)" in result

    def test_preserves_surrounding_content(self):
        md = "# Heading\n\nParagraph\n\n<!-- image -->\n\n## Next section"
        figures = [_fig("figure-1", "Diagram")]
        result = _replace_image_placeholders(md, figures)
        assert result.startswith("# Heading\n\nParagraph\n\n")
        assert result.endswith("\n\n## Next section")
        assert "![Diagram](figures/figure-1.png)" in result

    def test_empty_markdown(self):
        result = _replace_image_placeholders("", [_fig("figure-1")])
        assert result == ""

    def test_placeholder_inline_with_text(self):
        """Placeholder embedded in a line (unusual but possible)."""
        md = "See <!-- image --> above"
        figures = [_fig("figure-1", "chart")]
        result = _replace_image_placeholders(md, figures)
        assert result == "See ![chart](figures/figure-1.png) above"
