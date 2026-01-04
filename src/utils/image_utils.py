"""Image manipulation utilities for PDF element extraction.

Provides functions for cropping and highlighting elements on page images
using bounding box coordinates from Docling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

# Type alias for bounding box (left, top, right, bottom)
BBox = tuple[float, float, float, float]


def crop_element(
    page_image: Image.Image,
    bbox: BBox,
    page_width: float,
    padding: int = 10,
) -> Image.Image:
    """Crop element from page image using document coordinates.

    Converts Docling document coordinates to pixel coordinates and crops
    the element with optional padding.

    Args:
        page_image: Full page image (PIL Image)
        bbox: Bounding box in document coordinates (l, t, r, b)
        page_width: Document page width in points (for coordinate scaling)
        padding: Pixels to add around crop (default: 10)

    Returns:
        Cropped PIL Image containing just the element
    """
    from PIL import Image as PILImage  # noqa: F811

    l, t, r, b = bbox
    scale = page_image.width / page_width

    # Normalize coordinates (handle inverted Y from PDF coordinate system)
    left = min(l, r)
    right = max(l, r)
    top = min(t, b)
    bottom = max(t, b)

    # Convert document coords to pixel coords with padding
    pixel_left = max(0, int(left * scale - padding))
    pixel_top = max(0, int(top * scale - padding))
    pixel_right = min(page_image.width, int(right * scale + padding))
    pixel_bottom = min(page_image.height, int(bottom * scale + padding))

    # Final validation: ensure coordinates are valid after clamping
    # This can happen when bbox is partially/fully outside the image bounds
    if pixel_right <= pixel_left or pixel_bottom <= pixel_top:
        import logging
        logging.getLogger(__name__).warning(
            f"Invalid crop box after clamping: ({pixel_left}, {pixel_top}, {pixel_right}, {pixel_bottom}), "
            f"returning full page image"
        )
        return page_image

    return page_image.crop((pixel_left, pixel_top, pixel_right, pixel_bottom))


def highlight_element(
    page_image: Image.Image,
    bbox: BBox,
    page_width: float,
    color: str = "red",
    width: int = 4,
) -> Image.Image:
    """Draw highlight box around element on page image.

    Creates a copy of the page image with a colored rectangle drawn
    around the specified bounding box. Does not modify the original.

    Args:
        page_image: Full page image (PIL Image)
        bbox: Bounding box in document coordinates (l, t, r, b)
        page_width: Document page width in points (for coordinate scaling)
        color: Rectangle color (default: "red")
        width: Line width in pixels (default: 4)

    Returns:
        Copy of page image with highlight box drawn
    """
    from PIL import ImageDraw

    l, t, r, b = bbox
    img_copy = page_image.copy()
    draw = ImageDraw.Draw(img_copy)

    scale = img_copy.width / page_width

    # Normalize coordinates (handle inverted Y from PDF coordinate system)
    left = min(l, r)
    right = max(l, r)
    top = min(t, b)
    bottom = max(t, b)

    pixel_bbox = (
        int(left * scale),
        int(top * scale),
        int(right * scale),
        int(bottom * scale),
    )

    draw.rectangle(pixel_bbox, outline=color, width=width)
    return img_copy


def crop_and_highlight(
    page_image: Image.Image,
    bbox: BBox,
    page_width: float,
    padding: int = 10,
    highlight_color: str = "red",
    highlight_width: int = 4,
) -> tuple[Image.Image, Image.Image]:
    """Get both cropped element and highlighted page in one call.

    Convenience function for subagents that need both views:
    - Cropped element for detail analysis
    - Full page with highlight for context

    Args:
        page_image: Full page image (PIL Image)
        bbox: Bounding box in document coordinates (l, t, r, b)
        page_width: Document page width in points (for coordinate scaling)
        padding: Pixels to add around crop (default: 10)
        highlight_color: Rectangle color (default: "red")
        highlight_width: Line width in pixels (default: 4)

    Returns:
        Tuple of (cropped_element, highlighted_page)
    """
    cropped = crop_element(page_image, bbox, page_width, padding)
    highlighted = highlight_element(page_image, bbox, page_width, highlight_color, highlight_width)
    return cropped, highlighted
