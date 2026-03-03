"""Render PDF pages to base64 PNG images using pypdfium2.

Replaces Docling's ``generate_page_images`` with a lightweight, CPU-only
renderer that runs in a thread pool.  At ~50ms/page this overlaps entirely
with the docling-serve network call when used with ``asyncio.gather()``.

Usage::

    images = await render_page_images(pdf_bytes, scale=1.5)
    # images == {"1": "<base64-png>", "2": "<base64-png>", ...}
"""

from __future__ import annotations

import asyncio
import base64
import logging
from io import BytesIO

logger = logging.getLogger(__name__)


def _render_sync(file_content: bytes, scale: float) -> dict[str, str]:
    """Synchronous page rendering (runs in thread pool).

    Args:
        file_content: Raw PDF bytes.
        scale: Render scale factor (1.5 = 108 DPI, 2.0 = 144 DPI).

    Returns:
        Dict mapping 1-indexed page number strings to base64-encoded PNGs.
    """
    import pypdfium2  # type: ignore[import-untyped]

    pdf = pypdfium2.PdfDocument(file_content)
    page_images: dict[str, str] = {}

    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()

            buf = BytesIO()
            pil_image.save(buf, format="PNG")
            page_images[str(i + 1)] = base64.b64encode(buf.getvalue()).decode("ascii")

            page.close()
    finally:
        pdf.close()

    return page_images


async def render_page_images(
    file_content: bytes,
    *,
    scale: float = 1.5,
) -> dict[str, str]:
    """Render all pages of a PDF to base64-encoded PNG images.

    Runs the CPU-bound pypdfium2 rendering in a thread pool so it doesn't
    block the event loop.

    Args:
        file_content: Raw PDF bytes.
        scale: Render scale factor.  1.5 (108 DPI) matches the existing
            ``pdf_images_scale`` default and is optimal for Claude vision.

    Returns:
        Dict mapping 1-indexed page number strings (``"1"``, ``"2"``, ...)
        to base64-encoded PNG data.
    """
    return await asyncio.to_thread(_render_sync, file_content, scale)


def crop_figure_from_page_image(
    page_image_b64: str,
    bbox: dict[str, float],
    page_width: float,
    page_height: float,
) -> str:
    """Crop a figure region from a rendered page image using PDF bounding box.

    Converts docling-serve's PDF-coordinate bbox (BOTTOMLEFT origin, in points)
    to pixel coordinates on the rendered page image, crops, and returns base64 PNG.

    Args:
        page_image_b64: Base64-encoded PNG of the full page.
        bbox: Bounding box dict with ``l``, ``t``, ``r``, ``b`` keys
              (PDF points, BOTTOMLEFT origin).
        page_width: PDF page width in points (e.g. 612.0 for US Letter).
        page_height: PDF page height in points (e.g. 792.0 for US Letter).

    Returns:
        Base64-encoded PNG of the cropped figure region.
    """
    from PIL import Image

    # Decode the full page image
    img_bytes = base64.b64decode(page_image_b64)
    img = Image.open(BytesIO(img_bytes))
    img_w, img_h = img.size

    # Scale factors: PDF points → rendered pixels
    sx = img_w / page_width
    sy = img_h / page_height

    # Convert BOTTOMLEFT coords to PIL pixel coords (top-left origin)
    left = bbox.get("l", 0) * sx
    right = bbox.get("r", 0) * sx
    # In BOTTOMLEFT: "t" is higher (larger y), "b" is lower (smaller y)
    # PIL uses top-left origin, so flip: pixel_top = page_height - pdf_top
    top = (page_height - bbox.get("t", 0)) * sy
    bottom = (page_height - bbox.get("b", 0)) * sy

    # Add small padding (2% of dimension) for visual breathing room
    pad_x = (right - left) * 0.02
    pad_y = (bottom - top) * 0.02
    crop_box = (
        max(0, int(left - pad_x)),
        max(0, int(top - pad_y)),
        min(img_w, int(right + pad_x)),
        min(img_h, int(bottom + pad_y)),
    )

    cropped = img.crop(crop_box)

    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")
