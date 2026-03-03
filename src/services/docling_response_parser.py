"""Parse docling-serve responses into pipeline data structures.

Pure functions that translate the docling-serve JSON response into the same
data shapes the pipeline previously obtained from the in-process Docling
``DoclingDocument`` object.  No network I/O, no side effects — ideal for
unit testing in isolation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Markdown splitting
# ---------------------------------------------------------------------------


def split_markdown_by_page(
    md_content: str,
    placeholder: str = "<!-- PAGE_BREAK -->",
) -> dict[str, str]:
    """Split full-document markdown on page-break placeholders.

    Returns a dict mapping 1-indexed page number strings to per-page markdown.
    If no placeholders are found, the entire content is returned as page "1".
    """
    if not md_content:
        return {}

    pages = md_content.split(placeholder)
    result: dict[str, str] = {}
    for i, page_md in enumerate(pages, start=1):
        stripped = page_md.strip()
        if stripped or i == 1:
            result[str(i)] = stripped
    return result


# ---------------------------------------------------------------------------
# Figure extraction
# ---------------------------------------------------------------------------


def extract_figures_from_json(json_content: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract figure metadata from the DoclingDocument JSON.

    Parses ``pictures`` entries and extracts bounding boxes and page numbers
    from provenance.  Image data is NOT included — callers should crop figure
    regions from the pypdfium2 page renders using the returned bounding boxes.

    Returns:
        List of dicts with keys: ``ref_id``, ``caption``, ``page_number``,
        ``bbox`` (dict with l/t/r/b/coord_origin), ``page_width``, ``page_height``.
        Only pictures with valid provenance (page + bbox) are included.
    """
    pictures = json_content.get("pictures", [])
    if not pictures:
        return []

    # Pre-load page dimensions for bbox→pixel conversion
    pages = json_content.get("pages", {})

    figures: list[dict[str, Any]] = []

    for i, pic in enumerate(pictures):
        # Page number and bbox from provenance
        prov = pic.get("prov", [])
        if not prov:
            continue
        page_no = prov[0].get("page_no", 1)
        bbox = prov[0].get("bbox")
        if not bbox:
            continue

        # Page dimensions (needed for coordinate conversion)
        page_key = str(page_no)
        page_data = pages.get(page_key) or pages.get(page_no, {})
        size = page_data.get("size", {})
        page_width = size.get("width", 612.0)
        page_height = size.get("height", 792.0)

        # Caption text — try multiple approaches
        caption = _extract_caption_text(pic, json_content)

        figures.append({
            "ref_id": f"figure-{i + 1}",
            "caption": caption,
            "page_number": page_no,
            "bbox": bbox,
            "page_width": page_width,
            "page_height": page_height,
        })

    return figures


def _extract_caption_text(pic: dict[str, Any], json_content: dict[str, Any]) -> str:
    """Best-effort caption extraction from a picture item.

    Tries several approaches:
    1. Direct ``text`` field on the picture
    2. ``captions`` list with inline text
    3. Resolving caption ``$ref`` pointers against the document
    """
    # Direct text field
    text: str = pic.get("text", "")
    if text:
        return text

    # Captions list
    captions = pic.get("captions", [])
    caption_texts: list[str] = []
    for cap in captions:
        if isinstance(cap, str):
            caption_texts.append(cap)
        elif isinstance(cap, dict):
            # Inline caption text
            cap_text = cap.get("text", "") or cap.get("orig", "")
            if cap_text:
                caption_texts.append(cap_text)
            # Reference pointer — try to resolve
            ref = cap.get("$ref", "")
            if ref and not cap_text:
                resolved = _resolve_ref(ref, json_content)
                if resolved:
                    caption_texts.append(resolved)

    return " ".join(caption_texts)


def _resolve_ref(ref: str, json_content: dict[str, Any]) -> str:
    """Resolve a JSON ``$ref`` pointer like ``#/texts/42`` to its text content."""
    if not ref.startswith("#/"):
        return ""

    parts = ref[2:].split("/")
    current: Any = json_content
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
        if current is None:
            return ""

    if isinstance(current, dict):
        return str(current.get("text", "") or current.get("orig", ""))
    if isinstance(current, str):
        return current
    return ""


# ---------------------------------------------------------------------------
# Column detection (from JSON provenance)
# ---------------------------------------------------------------------------


def detect_columns_from_json(json_content: dict[str, Any], page_no: int) -> str:
    """Infer column layout from JSON provenance bounding boxes.

    Mirrors ``_detect_page_columns()`` from pipeline_viewer.py but walks
    the JSON body tree instead of Docling Python objects.

    Returns:
        ``"double_column"``, ``"single_column"``, or ``"unknown"``.
    """
    # Get page width
    pages = json_content.get("pages", {})
    page_key = str(page_no)
    page_info = pages.get(page_key) or pages.get(page_no)
    if not page_info:
        return "unknown"

    size = page_info.get("size", {})
    page_width = size.get("width", 0)
    if page_width <= 0:
        return "unknown"

    # Collect bounding box data from items on this page
    item_widths: list[float] = []
    item_centers: list[float] = []

    _collect_bbox_data(json_content.get("body", {}), page_no, page_width, item_widths, item_centers)

    # Also check top-level element collections (texts, tables, pictures, etc.)
    for collection_key in ("texts", "tables", "pictures", "lists", "key_value_items"):
        for item in json_content.get(collection_key, []):
            _extract_bbox_from_item(item, page_no, page_width, item_widths, item_centers)

    if len(item_widths) < 4:
        return "unknown"

    sorted_widths = sorted(item_widths)
    median_width = sorted_widths[len(sorted_widths) // 2]

    if median_width < 0.55:
        left_count = sum(1 for c in item_centers if c < 0.45)
        right_count = sum(1 for c in item_centers if c > 0.55)
        if left_count >= 2 and right_count >= 2:
            return "double_column"

    return "single_column"


def _collect_bbox_data(
    node: dict[str, Any],
    page_no: int,
    page_width: float,
    item_widths: list[float],
    item_centers: list[float],
) -> None:
    """Recursively walk a JSON body tree collecting bbox data for a page."""
    # Check this node's provenance
    _extract_bbox_from_item(node, page_no, page_width, item_widths, item_centers)

    # Recurse into children
    for child in node.get("children", []):
        if isinstance(child, dict):
            _collect_bbox_data(child, page_no, page_width, item_widths, item_centers)


def _extract_bbox_from_item(
    item: dict[str, Any],
    page_no: int,
    page_width: float,
    item_widths: list[float],
    item_centers: list[float],
) -> None:
    """Extract bbox data from a single item's provenance entries."""
    for prov in item.get("prov", []):
        if prov.get("page_no") != page_no:
            continue
        bbox = prov.get("bbox", {})
        left = bbox.get("l", 0)
        right = bbox.get("r", 0)
        width = abs(right - left)
        if width < page_width * 0.1:
            continue
        item_widths.append(width / page_width)
        item_centers.append(((left + right) / 2) / page_width)


# ---------------------------------------------------------------------------
# Page info
# ---------------------------------------------------------------------------


def get_page_info(json_content: dict[str, Any]) -> dict[str, Any]:
    """Extract page count and dimensions from the JSON document.

    Returns:
        Dict with ``page_count`` (int) and ``pages`` mapping page number
        strings to ``{"width": float, "height": float}`` dicts.
    """
    pages = json_content.get("pages", {})
    page_dims: dict[str, dict[str, float]] = {}

    for page_key, page_data in pages.items():
        size = page_data.get("size", {})
        page_dims[str(page_key)] = {
            "width": size.get("width", 0.0),
            "height": size.get("height", 0.0),
        }

    return {
        "page_count": len(pages),
        "pages": page_dims,
    }
