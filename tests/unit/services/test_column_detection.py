"""Unit tests for programmatic column detection from Docling bounding boxes.

Tests the _detect_page_columns helper function that infers column layout
from item provenance bounding boxes, and the layout hint integration in
the structure analysis user message.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.agents.prompts.structure_analysis import build_structure_user_message
from src.services.pipeline_viewer import _detect_page_columns

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers to build mock Docling objects
# ---------------------------------------------------------------------------


def _make_bbox(left: float, t: float, r: float, b: float) -> MagicMock:
    """Create a mock BoundingBox."""
    bbox = MagicMock()
    bbox.l = left
    bbox.t = t
    bbox.r = r
    bbox.b = b
    return bbox


def _make_prov(page_no: int, bbox: MagicMock) -> MagicMock:
    """Create a mock ProvenanceItem."""
    prov = MagicMock()
    prov.page_no = page_no
    prov.bbox = bbox
    return prov


def _make_item(provs: list[MagicMock]) -> MagicMock:
    """Create a mock DocItem with provenance."""
    item = MagicMock()
    item.prov = provs
    return item


def _make_doc(
    page_no: int,
    page_width: float,
    page_height: float,
    items: list[MagicMock],
) -> MagicMock:
    """Create a mock DoclingDocument with pages and iterate_items.

    All items are returned for the given page_no.
    """
    page = MagicMock()
    page.size = MagicMock()
    page.size.width = page_width
    page.size.height = page_height

    doc = MagicMock()
    doc.pages = {page_no: page}
    doc.iterate_items.return_value = [(item, 1) for item in items]
    return doc


# ---------------------------------------------------------------------------
# _detect_page_columns tests
# ---------------------------------------------------------------------------


class TestDetectPageColumns:
    """Tests for the _detect_page_columns helper."""

    def test_double_column_detected(self):
        """Two groups of narrow items on left and right halves -> double_column."""
        # Page is 612 pts wide (standard US Letter)
        # Left column items: l=72, r=290 (width ~218, 35.6% of page)
        # Right column items: l=322, r=540 (width ~218, 35.6% of page)
        items = [
            # Left column items
            _make_item([_make_prov(1, _make_bbox(72, 100, 290, 120))]),
            _make_item([_make_prov(1, _make_bbox(72, 130, 290, 150))]),
            _make_item([_make_prov(1, _make_bbox(72, 160, 290, 180))]),
            # Right column items
            _make_item([_make_prov(1, _make_bbox(322, 100, 540, 120))]),
            _make_item([_make_prov(1, _make_bbox(322, 130, 540, 150))]),
            _make_item([_make_prov(1, _make_bbox(322, 160, 540, 180))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "double_column"

    def test_single_column_detected(self):
        """Full-width items -> single_column."""
        # Items span ~80% of page width
        items = [
            _make_item([_make_prov(1, _make_bbox(72, 100, 540, 120))]),
            _make_item([_make_prov(1, _make_bbox(72, 130, 540, 150))]),
            _make_item([_make_prov(1, _make_bbox(72, 160, 540, 180))]),
            _make_item([_make_prov(1, _make_bbox(72, 190, 540, 210))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "single_column"

    def test_double_column_with_spanning_title(self):
        """Double-column page with a full-width title still detected as double_column.

        The median width should be below the threshold even though one item
        spans the full page.
        """
        items = [
            # Full-width title
            _make_item([_make_prov(1, _make_bbox(72, 50, 540, 70))]),
            # Left column
            _make_item([_make_prov(1, _make_bbox(72, 100, 290, 120))]),
            _make_item([_make_prov(1, _make_bbox(72, 130, 290, 150))]),
            _make_item([_make_prov(1, _make_bbox(72, 160, 290, 180))]),
            # Right column
            _make_item([_make_prov(1, _make_bbox(322, 100, 540, 120))]),
            _make_item([_make_prov(1, _make_bbox(322, 130, 540, 150))]),
            _make_item([_make_prov(1, _make_bbox(322, 160, 540, 180))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "double_column"

    def test_too_few_items_returns_unknown(self):
        """Fewer than 4 items -> unknown (not enough data to decide)."""
        items = [
            _make_item([_make_prov(1, _make_bbox(72, 100, 290, 120))]),
            _make_item([_make_prov(1, _make_bbox(322, 100, 540, 120))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "unknown"

    def test_missing_page_returns_unknown(self):
        """Page not in doc.pages -> unknown."""
        doc = MagicMock()
        doc.pages = {}

        assert _detect_page_columns(doc, 1) == "unknown"

    def test_zero_width_page_returns_unknown(self):
        """Zero page width -> unknown."""
        doc = _make_doc(1, 0, 792, [])

        assert _detect_page_columns(doc, 1) == "unknown"

    def test_narrow_items_only_in_left_half(self):
        """Narrow items all on the left side -> single_column.

        The width check passes but the left/right distribution check fails.
        """
        items = [
            _make_item([_make_prov(1, _make_bbox(72, 100, 290, 120))]),
            _make_item([_make_prov(1, _make_bbox(72, 130, 290, 150))]),
            _make_item([_make_prov(1, _make_bbox(72, 160, 290, 180))]),
            _make_item([_make_prov(1, _make_bbox(72, 190, 290, 210))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "single_column"

    def test_very_narrow_items_skipped(self):
        """Items narrower than 10% of page width are excluded."""
        # All items are very narrow (< 10% page width)
        items = [
            _make_item([_make_prov(1, _make_bbox(50, 100, 80, 120))]),  # width 30/612 = 4.9%
            _make_item([_make_prov(1, _make_bbox(300, 100, 330, 120))]),
            _make_item([_make_prov(1, _make_bbox(50, 130, 80, 150))]),
            _make_item([_make_prov(1, _make_bbox(300, 130, 330, 150))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        # All items filtered out as too narrow -> unknown
        assert _detect_page_columns(doc, 1) == "unknown"

    def test_items_without_prov_ignored(self):
        """Items with no provenance are gracefully skipped."""
        item_no_prov = MagicMock()
        item_no_prov.prov = []

        items = [
            item_no_prov,
            _make_item([_make_prov(1, _make_bbox(72, 100, 540, 120))]),
            _make_item([_make_prov(1, _make_bbox(72, 130, 540, 150))]),
            _make_item([_make_prov(1, _make_bbox(72, 160, 540, 180))]),
            _make_item([_make_prov(1, _make_bbox(72, 190, 540, 210))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        assert _detect_page_columns(doc, 1) == "single_column"

    def test_prov_on_different_page_ignored(self):
        """Provenances for other pages are filtered out."""
        items = [
            # Prov points to page 2, not page 1
            _make_item([_make_prov(2, _make_bbox(72, 100, 290, 120))]),
            _make_item([_make_prov(2, _make_bbox(322, 100, 540, 120))]),
            _make_item([_make_prov(2, _make_bbox(72, 130, 290, 150))]),
            _make_item([_make_prov(2, _make_bbox(322, 130, 540, 150))]),
        ]
        doc = _make_doc(1, 612, 792, items)

        # All provs are for page 2, analyzing page 1 -> unknown
        assert _detect_page_columns(doc, 1) == "unknown"


# ---------------------------------------------------------------------------
# build_structure_user_message layout hint tests
# ---------------------------------------------------------------------------


class TestLayoutHintInUserMessage:
    """Tests that layout hints are correctly included in the user message."""

    def test_hint_included_when_provided(self):
        """Layout hint appears in message when it's a valid value."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[],
            page_number=1,
            total_pages=5,
            layout_hint="double_column",
        )

        assert "### Layout hint (from bounding-box analysis)" in msg
        assert "**double_column**" in msg

    def test_hint_excluded_when_unknown(self):
        """Layout hint section omitted when hint is 'unknown'."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[],
            page_number=1,
            total_pages=5,
            layout_hint="unknown",
        )

        assert "Layout hint" not in msg

    def test_hint_excluded_when_none(self):
        """Layout hint section omitted when hint is None."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[],
            page_number=1,
            total_pages=5,
            layout_hint=None,
        )

        assert "Layout hint" not in msg

    def test_hint_excluded_when_not_provided(self):
        """Layout hint section omitted when parameter is not passed."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[],
            page_number=1,
            total_pages=5,
        )

        assert "Layout hint" not in msg

    def test_single_column_hint(self):
        """Single column hint is rendered correctly."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[],
            page_number=1,
            total_pages=5,
            layout_hint="single_column",
        )

        assert "**single_column**" in msg

    def test_hint_appears_before_outline(self):
        """Layout hint appears before the outline section."""
        msg = build_structure_user_message(
            page_markdown="Hello",
            outline_so_far=[{"level": 1, "text": "Title", "page": 1}],
            page_number=2,
            total_pages=5,
            layout_hint="double_column",
        )

        hint_pos = msg.index("Layout hint")
        outline_pos = msg.index("Document outline")
        assert hint_pos < outline_pos
