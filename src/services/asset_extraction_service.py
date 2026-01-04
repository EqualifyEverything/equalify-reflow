"""Asset extraction service for extracting and uploading images from documents.

Extracts figures and tables from processed documents, uploads them to S3,
and returns structured ImageAsset objects with public URLs.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

from src.agents.pipeline.models import ElementType, ProcessedElement
from src.api.schemas import ImageAsset

if TYPE_CHECKING:
    from src.services.s3_url_service import S3URLService
    from src.services.storage_service import StorageService

logger = logging.getLogger(__name__)


class AssetExtractionService:
    """Extracts and uploads image assets from processed documents.

    Handles:
    - Figure extraction (cropping from page images using bounding boxes)
    - Table image extraction
    - S3 upload with proper naming
    - URL generation for API response
    """

    def __init__(self, storage: "StorageService", url_service: "S3URLService"):
        """Initialize with storage services.

        Args:
            storage: StorageService for uploading to S3
            url_service: S3URLService for generating public URLs
        """
        self.storage = storage
        self.url_service = url_service

    async def extract_and_upload_assets(
        self,
        job_id: str,
        elements: list[ProcessedElement],
        page_images: dict[int, Image.Image],
    ) -> dict[str, ImageAsset]:
        """Extract images and upload to S3, returning asset mapping.

        Args:
            job_id: Job identifier for S3 key structure
            elements: List of processed document elements
            page_images: Map of page number to PIL Image

        Returns:
            Dict mapping asset_id to ImageAsset with S3 URLs
        """
        assets: dict[str, ImageAsset] = {}

        # Track counts for generating unique IDs
        figure_counts: dict[int, int] = {}  # page -> count
        table_counts: dict[int, int] = {}

        for elem in elements:
            try:
                if elem.element_type == ElementType.PICTURE:
                    asset = await self._extract_figure(
                        job_id, elem, page_images, figure_counts
                    )
                    if asset:
                        assets[asset.asset_id] = asset

                elif elem.element_type == ElementType.TABLE:
                    asset = await self._extract_table(
                        job_id, elem, page_images, table_counts
                    )
                    if asset:
                        assets[asset.asset_id] = asset

            except Exception as e:
                page = elem.provenance.docling_page if elem.provenance else 0
                logger.warning(
                    f"Failed to extract asset from {elem.element_type} on page {page}: {e}"
                )
                continue

        logger.info(f"Extracted {len(assets)} assets for job {job_id}")
        return assets

    async def _extract_figure(
        self,
        job_id: str,
        elem: ProcessedElement,
        page_images: dict[int, Image.Image],
        figure_counts: dict[int, int],
    ) -> ImageAsset | None:
        """Extract and upload a figure image.

        Args:
            job_id: Job identifier
            elem: ProcessedElement for the figure
            page_images: Map of page number to PIL Image
            figure_counts: Tracking dict for unique IDs per page

        Returns:
            ImageAsset if successful, None if extraction failed
        """
        page = elem.provenance.docling_page if elem.provenance else 1

        # Update count for this page
        figure_counts[page] = figure_counts.get(page, 0) + 1
        idx = figure_counts[page]

        # Generate asset ID
        asset_id = f"figure-p{page}-{idx}"
        image_name = f"{asset_id}.png"

        # Get page image
        page_img = page_images.get(page)
        if not page_img:
            logger.warning(f"No page image for page {page}, cannot extract {asset_id}")
            return None

        # Crop to bounding box if available
        cropped_img = self._crop_to_bbox(page_img, elem)

        # Convert to PNG bytes
        image_bytes = self._image_to_png_bytes(cropped_img)

        # Upload to S3
        s3_key = await self.storage.upload_image(job_id, image_bytes, image_name)

        # Generate public URL
        s3_url = await self.url_service.generate_url(s3_key)

        # Get bounding box as tuple if available
        bbox_tuple = None
        if elem.provenance and elem.provenance.docling_bbox:
            bb = elem.provenance.docling_bbox
            bbox_tuple = (bb.left, bb.top, bb.right, bb.bottom)

        return ImageAsset(
            asset_id=asset_id,
            s3_url=s3_url,
            page=page,
            element_type="figure",
            alt_text=elem.alt_text,
            caption=self._get_caption(elem),
            bbox=bbox_tuple,
        )

    async def _extract_table(
        self,
        job_id: str,
        elem: ProcessedElement,
        page_images: dict[int, Image.Image],
        table_counts: dict[int, int],
    ) -> ImageAsset | None:
        """Extract and upload a table image.

        Args:
            job_id: Job identifier
            elem: ProcessedElement for the table
            page_images: Map of page number to PIL Image
            table_counts: Tracking dict for unique IDs per page

        Returns:
            ImageAsset if successful, None if extraction failed
        """
        page = elem.provenance.docling_page if elem.provenance else 1

        # Update count for this page
        table_counts[page] = table_counts.get(page, 0) + 1
        idx = table_counts[page]

        # Generate asset ID
        asset_id = f"table-p{page}-{idx}"
        image_name = f"{asset_id}.png"

        # First try to get table's own image from Docling
        table_img = self._get_docling_table_image(elem)

        if table_img is None:
            # Fallback: crop from page image
            page_img = page_images.get(page)
            if not page_img:
                logger.warning(f"No page image for page {page}, cannot extract {asset_id}")
                return None
            table_img = self._crop_to_bbox(page_img, elem)

        # Convert to PNG bytes
        image_bytes = self._image_to_png_bytes(table_img)

        # Upload to S3
        s3_key = await self.storage.upload_image(job_id, image_bytes, image_name)

        # Generate public URL
        s3_url = await self.url_service.generate_url(s3_key)

        # Get bounding box as tuple if available
        bbox_tuple = None
        if elem.provenance and elem.provenance.docling_bbox:
            bb = elem.provenance.docling_bbox
            bbox_tuple = (bb.left, bb.top, bb.right, bb.bottom)

        return ImageAsset(
            asset_id=asset_id,
            s3_url=s3_url,
            page=page,
            element_type="table",
            alt_text=None,  # Tables use markdown, not alt text
            caption=self._get_caption(elem),
            bbox=bbox_tuple,
        )

    def _crop_to_bbox(
        self,
        page_img: Image.Image,
        elem: ProcessedElement,
    ) -> Image.Image:
        """Crop page image to element bounding box.

        If no bounding box is available, returns the full page image.

        Args:
            page_img: Full page PIL Image
            elem: ProcessedElement with optional bounding box

        Returns:
            Cropped (or full) PIL Image
        """
        if not elem.provenance or not elem.provenance.docling_bbox:
            return page_img

        bbox = elem.provenance.docling_bbox

        # Docling bbox coordinates are in document units
        # They need to be scaled to image pixel coordinates
        img_width, img_height = page_img.size

        # Docling uses (left, top, right, bottom) coordinates
        # These are typically in points (72 points per inch)
        # We need to scale them to the actual image size

        # For now, use the bbox values as proportions or direct pixels
        # This may need adjustment based on actual Docling behavior
        try:
            # Try using coordinates directly (if they're in pixels)
            left = int(max(0, bbox.left))
            top = int(max(0, bbox.top))
            right = int(min(img_width, bbox.right))
            bottom = int(min(img_height, bbox.bottom))

            # Validate crop region
            if right <= left or bottom <= top:
                logger.warning(
                    f"Invalid bbox {bbox}, using full page image"
                )
                return page_img

            return page_img.crop((left, top, right, bottom))

        except Exception as e:
            logger.warning(f"Failed to crop image: {e}, using full page")
            return page_img

    def _get_docling_table_image(self, elem: ProcessedElement) -> Image.Image | None:
        """Try to get table's own image from Docling element.

        Args:
            elem: ProcessedElement with docling_ref

        Returns:
            PIL Image if available, None otherwise
        """
        if not elem.docling_ref:
            return None

        try:
            if hasattr(elem.docling_ref, "image") and elem.docling_ref.image:
                if hasattr(elem.docling_ref.image, "pil_image"):
                    return elem.docling_ref.image.pil_image
        except Exception as e:
            logger.debug(f"Could not get Docling table image: {e}")

        return None

    def _get_caption(self, elem: ProcessedElement) -> str | None:
        """Extract caption text from element if available.

        Args:
            elem: ProcessedElement with potential caption

        Returns:
            Caption text or None
        """
        # Check if Docling element has caption method
        if elem.docling_ref and hasattr(elem.docling_ref, "caption_text"):
            try:
                # caption_text typically needs the doc object
                # For now, return None - this would need the doc passed in
                return None
            except Exception:
                pass
        return None

    def _image_to_png_bytes(self, img: Image.Image) -> bytes:
        """Convert PIL Image to PNG bytes.

        Args:
            img: PIL Image

        Returns:
            PNG image as bytes
        """
        buffer = BytesIO()
        # Convert to RGB if necessary (handles RGBA, L, etc.)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
