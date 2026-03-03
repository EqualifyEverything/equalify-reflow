"""Tests for page_image_renderer — PDF to base64 PNG rendering."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest


def _create_test_pdf(num_pages: int = 2) -> bytes:
    """Create a simple multi-page PDF using reportlab for testing."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    for i in range(num_pages):
        c.drawString(100, 700, f"Page {i + 1}")
        c.showPage()

    c.save()
    return buf.getvalue()


class TestRenderPageImages:
    """Tests for the async render_page_images function."""

    @pytest.mark.asyncio
    async def test_renders_correct_page_count(self):
        from src.services.page_image_renderer import render_page_images

        pdf = _create_test_pdf(3)
        images = await render_page_images(pdf, scale=1.0)

        assert len(images) == 3
        assert set(images.keys()) == {"1", "2", "3"}

    @pytest.mark.asyncio
    async def test_one_indexed_keys(self):
        from src.services.page_image_renderer import render_page_images

        pdf = _create_test_pdf(1)
        images = await render_page_images(pdf, scale=1.0)

        assert "1" in images
        assert "0" not in images

    @pytest.mark.asyncio
    async def test_valid_base64_png(self):
        from src.services.page_image_renderer import render_page_images

        pdf = _create_test_pdf(1)
        images = await render_page_images(pdf, scale=1.0)

        raw = base64.b64decode(images["1"])
        # PNG magic bytes
        assert raw[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_scale_affects_image_size(self):
        from src.services.page_image_renderer import render_page_images

        pdf = _create_test_pdf(1)

        small = await render_page_images(pdf, scale=1.0)
        large = await render_page_images(pdf, scale=2.0)

        # Higher scale = more pixels = larger base64
        assert len(large["1"]) > len(small["1"])

    @pytest.mark.asyncio
    async def test_default_scale(self):
        from src.services.page_image_renderer import render_page_images

        pdf = _create_test_pdf(1)
        images = await render_page_images(pdf)

        # Should use default scale of 1.5
        assert len(images) == 1
        assert images["1"]  # non-empty


class TestRenderSync:
    """Tests for the synchronous _render_sync helper."""

    def test_sync_render(self):
        from src.services.page_image_renderer import _render_sync

        pdf = _create_test_pdf(2)
        images = _render_sync(pdf, scale=1.0)

        assert len(images) == 2
        for key in ("1", "2"):
            raw = base64.b64decode(images[key])
            assert raw[:4] == b"\x89PNG"

    def test_single_page_pdf(self):
        """A single-page PDF returns one image."""
        from src.services.page_image_renderer import _render_sync

        pdf = _create_test_pdf(1)
        images = _render_sync(pdf, scale=1.0)
        assert len(images) == 1
        assert "1" in images


class TestCropFigureFromPageImage:
    """Tests for cropping figures from rendered page images."""

    def test_crop_returns_valid_png(self):
        from src.services.page_image_renderer import _render_sync, crop_figure_from_page_image

        pdf = _create_test_pdf(1)
        images = _render_sync(pdf, scale=1.5)
        page_b64 = images["1"]

        # US Letter: 612x792 points. Crop a region in the middle.
        bbox = {"l": 50, "t": 500, "r": 300, "b": 200}
        cropped = crop_figure_from_page_image(page_b64, bbox, 612.0, 792.0)

        raw = base64.b64decode(cropped)
        assert raw[:4] == b"\x89PNG"

    def test_crop_is_smaller_than_full_page(self):
        from src.services.page_image_renderer import _render_sync, crop_figure_from_page_image

        pdf = _create_test_pdf(1)
        images = _render_sync(pdf, scale=1.5)
        page_b64 = images["1"]

        bbox = {"l": 100, "t": 400, "r": 300, "b": 300}
        cropped = crop_figure_from_page_image(page_b64, bbox, 612.0, 792.0)

        # Cropped image should be smaller than the full page
        assert len(cropped) < len(page_b64)

    def test_crop_dimensions_match_bbox(self):
        from PIL import Image

        from src.services.page_image_renderer import _render_sync, crop_figure_from_page_image

        pdf = _create_test_pdf(1)
        images = _render_sync(pdf, scale=1.0)  # 1:1 scale = 1 pixel per point
        page_b64 = images["1"]

        # Crop a 200x300 point region (at scale=1.0, ~200x300 pixels)
        bbox = {"l": 100, "t": 600, "r": 300, "b": 300}
        cropped = crop_figure_from_page_image(page_b64, bbox, 612.0, 792.0)

        raw = base64.b64decode(cropped)
        img = Image.open(BytesIO(raw))
        w, h = img.size

        # Width should be ~200 pixels (+ small padding)
        assert 190 < w < 220
        # Height should be ~300 pixels (+ small padding)
        assert 290 < h < 320
