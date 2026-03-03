"""Unit tests for SSE init payload splitting.

Tests the _slim_init_payload helper that strips binary images from
the init event so they can be streamed individually.
"""

import pytest

from src.api.pipeline_viewer import _slim_init_payload
from src.services.pipeline_viewer_models import FigureData, PipelineViewerResult

pytestmark = pytest.mark.unit


def _make_result(
    *,
    page_images: dict[str, str] | None = None,
    figures: list[FigureData] | None = None,
) -> PipelineViewerResult:
    """Build a minimal PipelineViewerResult for testing."""
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Page 1\nHello\n\n# Page 2\nWorld"},
        page_images=page_images or {"1": "AAAA", "2": "BBBB"},
        page_markdowns={"v0": {"1": "# Page 1\nHello", "2": "# Page 2\nWorld"}},
        figures=figures or [],
        stats={"total_chars": 100, "chars_per_page": 50},
        warnings=["Test warning"],
    )


class TestSlimInitPayload:
    """Tests for _slim_init_payload."""

    def test_strips_page_images(self):
        """page_images should be an empty dict in the slim payload."""
        result = _make_result(page_images={"1": "x" * 1000, "2": "y" * 1000})
        slim = _slim_init_payload(result)

        assert slim["page_images"] == {}

    def test_strips_figure_image_base64(self):
        """Figure image_base64 should be empty string, but ref_id preserved."""
        figures = [
            FigureData(ref_id="fig-1", caption="A chart", page_number=1, image_base64="IMGDATA1"),
            FigureData(ref_id="fig-2", caption="A photo", page_number=2, image_base64="IMGDATA2"),
        ]
        result = _make_result(figures=figures)
        slim = _slim_init_payload(result)

        assert len(slim["figures"]) == 2
        for fig in slim["figures"]:
            assert fig["image_base64"] == ""
        assert slim["figures"][0]["ref_id"] == "fig-1"
        assert slim["figures"][1]["ref_id"] == "fig-2"

    def test_preserves_figure_metadata(self):
        """Figure caption and page_number should be intact."""
        figures = [
            FigureData(ref_id="fig-1", caption="A chart", page_number=3, image_base64="DATA"),
        ]
        result = _make_result(figures=figures)
        slim = _slim_init_payload(result)

        fig = slim["figures"][0]
        assert fig["caption"] == "A chart"
        assert fig["page_number"] == 3

    def test_preserves_metadata(self):
        """filename, versions, stats, warnings should be unchanged."""
        result = _make_result()
        slim = _slim_init_payload(result)

        assert slim["filename"] == "test.pdf"
        assert slim["total_pages"] == 2
        assert slim["versions"] == {"v0": "# Page 1\nHello\n\n# Page 2\nWorld"}
        assert slim["stats"]["total_chars"] == 100
        assert slim["warnings"] == ["Test warning"]

    def test_does_not_mutate_original(self):
        """The original result object should not be modified."""
        result = _make_result(
            page_images={"1": "IMG1"},
            figures=[FigureData(ref_id="f1", caption="c", page_number=1, image_base64="DATA")],
        )
        _slim_init_payload(result)

        assert result.page_images == {"1": "IMG1"}
        assert result.figures[0].image_base64 == "DATA"

    def test_empty_figures_and_pages(self):
        """Works with no figures and no page images."""
        result = _make_result(page_images={}, figures=[])
        slim = _slim_init_payload(result)

        assert slim["page_images"] == {}
        assert slim["figures"] == []
