"""Image manipulation utilities for PDF element extraction.

Provides functions for cropping and highlighting elements on page images
using bounding box coordinates from Docling.

Also provides image compression utilities for optimizing LLM token usage
when sending images to subagents.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# =============================================================================
# Image Compression Settings for LLM Subagents
# =============================================================================

# Maximum width for text verification tasks (page artifacts, footnotes, citations, lists)
# These tasks only need to verify text layout, not pixel-perfect visual formatting
TEXT_VERIFICATION_MAX_WIDTH = 800

# Maximum width for visual analysis tasks (typography)
# These tasks need higher resolution to detect bold/italic formatting
VISUAL_ANALYSIS_MAX_WIDTH = 1200

# JPEG quality for compressed images (balance between size and readability)
COMPRESSION_QUALITY = 85

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

    x0, y0, x1, y1 = bbox
    scale = page_image.width / page_width

    # Normalize coordinates (handle inverted Y from PDF coordinate system)
    left = min(x0, x1)
    right = max(x0, x1)
    top = min(y0, y1)
    bottom = max(y0, y1)

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

    x0, y0, x1, y1 = bbox
    img_copy = page_image.copy()
    draw = ImageDraw.Draw(img_copy)

    scale = img_copy.width / page_width

    # Normalize coordinates (handle inverted Y from PDF coordinate system)
    left = min(x0, x1)
    right = max(x0, x1)
    top = min(y0, y1)
    bottom = max(y0, y1)

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


# =============================================================================
# Image Compression for LLM Token Optimization
# =============================================================================


def compress_for_text_verification(
    image: "Image.Image",
    max_width: int = TEXT_VERIFICATION_MAX_WIDTH,
) -> "Image.Image":
    """Compress image for text verification tasks.

    Used by subagents that need to verify text layout (page artifacts,
    footnotes, citations, lists) but don't need pixel-perfect visual detail.

    Reduces image width to max_width while maintaining aspect ratio.
    This significantly reduces token count when images are sent to LLMs.

    Args:
        image: Original PIL Image
        max_width: Maximum width in pixels (default: 800)

    Returns:
        Resized PIL Image (or original if already smaller)

    Example:
        >>> from PIL import Image
        >>> img = Image.new("RGB", (2400, 3200))
        >>> compressed = compress_for_text_verification(img)
        >>> compressed.size
        (800, 1066)  # Maintains aspect ratio
    """
    if image.width <= max_width:
        return image

    ratio = max_width / image.width
    new_height = int(image.height * ratio)
    new_size = (max_width, new_height)

    # Import here to avoid circular imports at module level
    from PIL import Image as PILImage

    resized = image.resize(new_size, PILImage.Resampling.LANCZOS)

    logger.debug(
        f"Compressed image for text verification: {image.size} -> {resized.size} "
        f"({100 * resized.width * resized.height / (image.width * image.height):.1f}% of original pixels)"
    )

    return resized


def compress_for_visual_analysis(
    image: "Image.Image",
    max_width: int = VISUAL_ANALYSIS_MAX_WIDTH,
) -> "Image.Image":
    """Compress image for visual analysis tasks.

    Used by subagents that need to detect visual formatting (typography)
    where higher resolution helps identify bold/italic text.

    Args:
        image: Original PIL Image
        max_width: Maximum width in pixels (default: 1200)

    Returns:
        Resized PIL Image (or original if already smaller)
    """
    if image.width <= max_width:
        return image

    ratio = max_width / image.width
    new_height = int(image.height * ratio)
    new_size = (max_width, new_height)

    from PIL import Image as PILImage

    resized = image.resize(new_size, PILImage.Resampling.LANCZOS)

    logger.debug(
        f"Compressed image for visual analysis: {image.size} -> {resized.size} "
        f"({100 * resized.width * resized.height / (image.width * image.height):.1f}% of original pixels)"
    )

    return resized


def image_to_compressed_bytes(
    image: "Image.Image",
    for_visual_analysis: bool = False,
    format: str = "PNG",
) -> bytes:
    """Convert PIL Image to compressed bytes for LLM consumption.

    Combines compression and serialization in one call. Use this when
    preparing images for LLM subagents.

    Args:
        image: Original PIL Image
        for_visual_analysis: If True, uses higher resolution (typography tasks).
                            If False, uses lower resolution (text verification).
        format: Output format ("PNG" or "JPEG"). PNG is lossless, JPEG is smaller.

    Returns:
        Compressed image as bytes

    Example:
        >>> img = Image.open("page.png")
        >>> # For footnote/citation/list/artifact detection (text verification)
        >>> bytes_data = image_to_compressed_bytes(img, for_visual_analysis=False)
        >>> # For typography detection (visual analysis)
        >>> bytes_data = image_to_compressed_bytes(img, for_visual_analysis=True)
    """
    if for_visual_analysis:
        compressed = compress_for_visual_analysis(image)
    else:
        compressed = compress_for_text_verification(image)

    buffer = BytesIO()
    if format.upper() == "JPEG":
        # Convert to RGB if necessary (JPEG doesn't support transparency)
        if compressed.mode in ("RGBA", "LA", "P"):
            compressed = compressed.convert("RGB")
        compressed.save(buffer, format="JPEG", quality=COMPRESSION_QUALITY)
    else:
        compressed.save(buffer, format="PNG")

    return buffer.getvalue()
