"""PDF to Markdown conversion service using Docling with page images."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger(__name__)


@dataclass
class PageData:
    """Data for a single PDF page with its rendered image."""

    page_num: int
    image_base64: str  # PNG image encoded as base64 string


@dataclass
class ExtractedImage:
    """Metadata for extracted figure/table image from PDF."""

    ref_id: str  # e.g., "#/pictures/0" from Docling document
    image_type: str  # "picture", "table"
    caption: str  # Extracted caption text
    page_num: int  # Source page number
    pil_image: Image.Image  # type: ignore[name-defined]


@dataclass
class PDFConversionResult:
    """Result of PDF conversion with per-page data."""

    pages: list[PageData]
    total_pages: int
    has_page_images: bool
    extracted_images: list[ExtractedImage]  # Embedded figures/tables from PDF


class PDFConverter:
    """Convert PDF documents to page images using Docling."""

    def __init__(self) -> None:
        """Initialize PDF converter with Docling pipeline."""
        # Configure pipeline for page image generation
        pipeline_options = PdfPipelineOptions(
            generate_page_images=True,  # CRITICAL: Generate full page renders
            generate_picture_images=True,  # Also generate embedded picture images
            images_scale=2.0,  # High resolution (2x = 144 DPI)
            do_ocr=False,  # OCR not needed - we send images directly to AI
            do_table_structure=False,  # Table structure not needed - AI handles this
        )

        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        logger.info("PDF converter initialized with page image generation enabled")

    async def convert_with_page_images(
        self, pdf_content: bytes
    ) -> PDFConversionResult:
        """Convert PDF to page images for AI processing.

        Args:
            pdf_content: Raw PDF file content as bytes

        Returns:
            PDFConversionResult containing per-page image data

        Raises:
            RuntimeError: If Docling fails to generate page images
            ValueError: If PDF conversion fails
        """
        logger.info(f"Converting PDF ({len(pdf_content)} bytes) with page images")

        try:
            # Create document stream from bytes
            pdf_stream = BytesIO(pdf_content)
            doc_stream = DocumentStream(
                name="document.pdf",
                stream=pdf_stream,
            )

            # Convert document
            result = self.converter.convert(source=doc_stream)
            doc = result.document

            logger.info(f"Docling conversion complete: {len(doc.pages)} pages")

            # Extract per-page data with images
            # CRITICAL: Access via doc.pages dict, not result.pages (cache cleared)
            pages: list[PageData] = []
            has_page_images = False

            # doc.pages is a dict[int, PageItem] where keys are 1-indexed page numbers
            for page_no in sorted(doc.pages.keys()):
                page = doc.pages[page_no]

                # Extract page image if available
                page_image_base64 = ""
                if page.image and hasattr(page.image, "pil_image"):
                    has_page_images = True
                    # Convert PIL Image to base64 PNG
                    page_image_base64 = self._image_to_base64(page.image.pil_image)
                    logger.debug(
                        f"Page {page_no}: extracted {len(page_image_base64)} chars base64 image"
                    )
                else:
                    has_pil = hasattr(page.image, "pil_image") if page.image else False
                    logger.warning(
                        f"Page {page_no}: no image available "
                        f"(image={page.image}, has pil_image={has_pil})"
                    )

                pages.append(
                    PageData(
                        page_num=page_no,
                        image_base64=page_image_base64,
                    )
                )

            # CRITICAL CHECK: Verify page images were generated
            if not has_page_images:
                raise RuntimeError(
                    "Docling failed to generate page images. "
                    "This is required for AI processing. "
                    "Check that generate_page_images=True is working correctly."
                )

            # Extract embedded images (figures, tables) from document
            extracted_images: list[ExtractedImage] = []

            # Extract pictures (diagrams, photos, illustrations)
            for pic in doc.pictures:
                if pic.image and hasattr(pic.image, "pil_image"):
                    extracted_images.append(
                        ExtractedImage(
                            ref_id=pic.self_ref,
                            image_type="picture",
                            caption=pic.caption_text(doc=doc) or "",
                            page_num=pic.prov[0].page_no if pic.prov else 1,
                            pil_image=pic.image.pil_image,
                        )
                    )
                    logger.debug(
                        f"Extracted picture {pic.self_ref} from page "
                        f"{pic.prov[0].page_no if pic.prov else 'unknown'}: "
                        f"{pic.caption_text(doc=doc)}"
                    )

            # Extract tables (if they have image representations)
            for table in doc.tables:
                if table.image and hasattr(table.image, "pil_image"):
                    extracted_images.append(
                        ExtractedImage(
                            ref_id=table.self_ref,
                            image_type="table",
                            caption=table.caption_text(doc=doc) or "",
                            page_num=table.prov[0].page_no if table.prov else 1,
                            pil_image=table.image.pil_image,
                        )
                    )
                    logger.debug(
                        f"Extracted table {table.self_ref} from page "
                        f"{table.prov[0].page_no if table.prov else 'unknown'}: "
                        f"{table.caption_text(doc=doc)}"
                    )

            logger.info(
                f"PDF conversion successful: {len(pages)} pages with images extracted, "
                f"{len(extracted_images)} embedded images (pictures/tables)"
            )

            return PDFConversionResult(
                pages=pages,
                total_pages=len(pages),
                has_page_images=has_page_images,
                extracted_images=extracted_images,
            )

        except Exception as e:
            logger.error(f"PDF conversion failed: {e}", exc_info=True)
            raise ValueError(f"Failed to convert PDF: {str(e)}") from e

    def _image_to_base64(self, image: Image.Image) -> str:  # type: ignore[name-defined]
        """Convert PIL Image to base64-encoded PNG string.

        Args:
            image: PIL Image object

        Returns:
            Base64-encoded PNG image as string
        """
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        return base64.b64encode(image_bytes).decode("utf-8")
