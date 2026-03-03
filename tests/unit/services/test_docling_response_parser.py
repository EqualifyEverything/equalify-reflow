"""Tests for docling_response_parser — pure functions for parsing docling-serve output."""

from __future__ import annotations

import pytest

from src.services.docling_response_parser import (
    _extract_caption_text,
    _resolve_ref,
    detect_columns_from_json,
    extract_figures_from_json,
    get_page_info,
    split_markdown_by_page,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_json_content(
    *,
    pages: dict | None = None,
    pictures: list | None = None,
    body: dict | None = None,
    texts: list | None = None,
) -> dict:
    """Build a minimal DoclingDocument JSON structure."""
    doc: dict = {}
    if pages is not None:
        doc["pages"] = pages
    else:
        doc["pages"] = {
            "1": {"size": {"width": 612.0, "height": 792.0}},
            "2": {"size": {"width": 612.0, "height": 792.0}},
        }
    if pictures is not None:
        doc["pictures"] = pictures
    if body is not None:
        doc["body"] = body
    if texts is not None:
        doc["texts"] = texts
    return doc


def _make_picture(
    *,
    page_no: int = 1,
    caption_text: str = "",
    bbox: dict | None = None,
) -> dict:
    """Build a minimal PictureItem JSON entry."""
    pic: dict = {
        "prov": [
            {
                "page_no": page_no,
                "bbox": bbox or {"l": 50, "t": 500, "r": 300, "b": 200, "coord_origin": "BOTTOMLEFT"},
            }
        ],
    }
    if caption_text:
        pic["text"] = caption_text
    return pic


def _make_embedded_png_uri() -> str:
    """Create a valid data:image/png;base64,... URI with minimal PNG data."""
    # 1x1 transparent PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# split_markdown_by_page
# ---------------------------------------------------------------------------


class TestSplitMarkdownByPage:
    def test_empty_content(self):
        assert split_markdown_by_page("") == {}

    def test_no_placeholders(self):
        result = split_markdown_by_page("# Hello\n\nWorld")
        assert result == {"1": "# Hello\n\nWorld"}

    def test_two_pages(self):
        md = "# Page 1\n\nContent A\n<!-- PAGE_BREAK -->\n# Page 2\n\nContent B"
        result = split_markdown_by_page(md)
        assert len(result) == 2
        assert "Page 1" in result["1"]
        assert "Page 2" in result["2"]

    def test_three_pages(self):
        md = "A<!-- PAGE_BREAK -->B<!-- PAGE_BREAK -->C"
        result = split_markdown_by_page(md)
        assert len(result) == 3
        assert result["1"] == "A"
        assert result["2"] == "B"
        assert result["3"] == "C"

    def test_custom_placeholder(self):
        md = "A---BREAK---B"
        result = split_markdown_by_page(md, placeholder="---BREAK---")
        assert len(result) == 2

    def test_trailing_placeholder_no_empty_page(self):
        md = "Content<!-- PAGE_BREAK -->"
        result = split_markdown_by_page(md)
        # Empty trailing page is stripped
        assert len(result) == 1
        assert result["1"] == "Content"

    def test_empty_first_page_still_included(self):
        md = "<!-- PAGE_BREAK -->Second page"
        result = split_markdown_by_page(md)
        assert "1" in result  # Empty first page still included
        assert "2" in result


# ---------------------------------------------------------------------------
# extract_figures_from_json
# ---------------------------------------------------------------------------


class TestExtractFiguresFromJson:
    def test_no_pictures(self):
        doc = _make_json_content(pictures=[])
        assert extract_figures_from_json(doc) == []

    def test_no_pictures_key(self):
        assert extract_figures_from_json({}) == []

    def test_single_figure_with_bbox(self):
        bbox = {"l": 100, "t": 500, "r": 400, "b": 200, "coord_origin": "BOTTOMLEFT"}
        doc = _make_json_content(
            pictures=[_make_picture(page_no=2, caption_text="A chart", bbox=bbox)]
        )
        figures = extract_figures_from_json(doc)

        assert len(figures) == 1
        fig = figures[0]
        assert fig["ref_id"] == "figure-1"
        assert fig["caption"] == "A chart"
        assert fig["page_number"] == 2
        assert fig["bbox"] == bbox
        assert fig["page_width"] == 612.0
        assert fig["page_height"] == 792.0

    def test_multiple_figures(self):
        doc = _make_json_content(
            pictures=[
                _make_picture(page_no=1),
                _make_picture(page_no=3),
            ],
            pages={
                "1": {"size": {"width": 612.0, "height": 792.0}},
                "3": {"size": {"width": 612.0, "height": 792.0}},
            },
        )
        figures = extract_figures_from_json(doc)

        assert len(figures) == 2
        assert figures[0]["ref_id"] == "figure-1"
        assert figures[1]["ref_id"] == "figure-2"
        assert figures[0]["page_number"] == 1
        assert figures[1]["page_number"] == 3

    def test_skips_picture_without_provenance(self):
        pic = {"prov": []}
        doc = _make_json_content(pictures=[pic])
        assert extract_figures_from_json(doc) == []

    def test_skips_picture_without_bbox(self):
        pic = {"prov": [{"page_no": 1}]}
        doc = _make_json_content(pictures=[pic])
        assert extract_figures_from_json(doc) == []

    def test_uses_default_page_dimensions(self):
        """When page size is missing, defaults to US Letter (612x792)."""
        doc = _make_json_content(
            pictures=[_make_picture(page_no=5)],
            pages={},
        )
        figures = extract_figures_from_json(doc)
        assert len(figures) == 1
        assert figures[0]["page_width"] == 612.0
        assert figures[0]["page_height"] == 792.0


# ---------------------------------------------------------------------------
# _extract_caption_text
# ---------------------------------------------------------------------------


class TestExtractCaptionText:
    def test_direct_text_field(self):
        pic = {"text": "Figure 1: Results"}
        assert _extract_caption_text(pic, {}) == "Figure 1: Results"

    def test_captions_list_with_inline_text(self):
        pic = {"captions": [{"text": "Caption A"}, {"text": "Caption B"}]}
        result = _extract_caption_text(pic, {})
        assert "Caption A" in result
        assert "Caption B" in result

    def test_captions_list_with_ref(self):
        pic = {"captions": [{"$ref": "#/texts/0"}]}
        doc = {"texts": [{"text": "Referenced caption"}]}
        assert _extract_caption_text(pic, doc) == "Referenced caption"

    def test_no_caption(self):
        assert _extract_caption_text({}, {}) == ""


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------


class TestResolveRef:
    def test_simple_ref(self):
        doc = {"texts": [{"text": "hello"}]}
        assert _resolve_ref("#/texts/0", doc) == "hello"

    def test_nested_ref(self):
        doc = {"body": {"children": [{"text": "nested"}]}}
        assert _resolve_ref("#/body/children/0", doc) == "nested"

    def test_invalid_ref(self):
        assert _resolve_ref("#/nonexistent/0", {}) == ""

    def test_non_hash_ref(self):
        assert _resolve_ref("external/ref", {}) == ""


# ---------------------------------------------------------------------------
# detect_columns_from_json
# ---------------------------------------------------------------------------


class TestDetectColumnsFromJson:
    def test_single_column(self):
        """Full-width items → single column."""
        body = {
            "children": [
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 100, "r": 562, "b": 200}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 200, "r": 562, "b": 300}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 300, "r": 562, "b": 400}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 400, "r": 562, "b": 500}}]},
            ]
        }
        doc = _make_json_content(body=body)
        assert detect_columns_from_json(doc, 1) == "single_column"

    def test_double_column(self):
        """Narrow items in both halves → double column."""
        body = {
            "children": [
                # Left column items
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 100, "r": 280, "b": 200}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 200, "r": 280, "b": 300}}]},
                # Right column items
                {"prov": [{"page_no": 1, "bbox": {"l": 330, "t": 100, "r": 562, "b": 200}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 330, "t": 200, "r": 562, "b": 300}}]},
            ]
        }
        doc = _make_json_content(body=body)
        assert detect_columns_from_json(doc, 1) == "double_column"

    def test_too_few_items(self):
        """Fewer than 4 items → unknown."""
        body = {
            "children": [
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 100, "r": 562, "b": 200}}]},
            ]
        }
        doc = _make_json_content(body=body)
        assert detect_columns_from_json(doc, 1) == "unknown"

    def test_missing_page(self):
        doc = _make_json_content(pages={"1": {"size": {"width": 612, "height": 792}}})
        assert detect_columns_from_json(doc, 99) == "unknown"

    def test_items_from_collections(self):
        """Items in top-level collections (texts, tables) are also considered."""
        doc = _make_json_content(
            texts=[
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 100, "r": 280, "b": 200}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 50, "t": 200, "r": 280, "b": 300}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 330, "t": 100, "r": 562, "b": 200}}]},
                {"prov": [{"page_no": 1, "bbox": {"l": 330, "t": 200, "r": 562, "b": 300}}]},
            ],
        )
        assert detect_columns_from_json(doc, 1) == "double_column"


# ---------------------------------------------------------------------------
# get_page_info
# ---------------------------------------------------------------------------


class TestGetPageInfo:
    def test_two_pages(self):
        doc = _make_json_content()
        info = get_page_info(doc)

        assert info["page_count"] == 2
        assert info["pages"]["1"]["width"] == 612.0
        assert info["pages"]["2"]["height"] == 792.0

    def test_empty_pages(self):
        info = get_page_info({"pages": {}})
        assert info["page_count"] == 0
        assert info["pages"] == {}

    def test_no_pages_key(self):
        info = get_page_info({})
        assert info["page_count"] == 0
