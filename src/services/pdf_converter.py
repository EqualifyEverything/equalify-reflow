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

from ..utils.markdown_cleanup import format_markdown

logger = logging.getLogger(__name__)


@dataclass
class PageData:
    """Data for a single PDF page including markdown and image."""

    page_num: int
    markdown: str
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
    full_markdown: str
    has_page_images: bool
    extracted_images: list[ExtractedImage]  # Embedded figures/tables from PDF


class PDFConverter:
    """Convert PDF documents to markdown with page images using Docling."""

    def __init__(self) -> None:
        """Initialize PDF converter with Docling pipeline."""
        # Configure pipeline for page image generation
        pipeline_options = PdfPipelineOptions(
            generate_page_images=True,  # CRITICAL: Generate full page renders
            generate_picture_images=True,  # Also generate embedded picture images
            images_scale=2.0,  # High resolution (2x = 144 DPI)
            do_ocr=True,  # Enable OCR for scanned documents
            do_table_structure=True,  # Extract table structure
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
        """Convert PDF to markdown with base64-encoded page images.

        Args:
            pdf_content: Raw PDF file content as bytes

        Returns:
            PDFConversionResult containing per-page data with images

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

            # Extract full markdown
            raw_markdown = doc.export_to_markdown()

            # Apply mdformat to fix common markdown formatting issues
            full_markdown, _ = format_markdown(raw_markdown)

            # Extract per-page data with images
            # CRITICAL: Access via doc.pages dict, not result.pages (cache cleared)
            pages: list[PageData] = []
            has_page_images = False

            # doc.pages is a dict[int, PageItem] where keys are 1-indexed page numbers
            for page_no in sorted(doc.pages.keys()):
                page = doc.pages[page_no]

                # Extract markdown for this page (0-indexed for internal method)
                raw_page_markdown = self._extract_page_markdown(doc, page_no - 1)

                # Apply mdformat to per-page markdown as well
                page_markdown, _ = format_markdown(raw_page_markdown)

                # Extract page image if available
                # Access via doc.pages[page_no].image.pil_image (transferred from result.pages)
                page_image_base64 = ""
                if page.image and hasattr(page.image, "pil_image"):
                    has_page_images = True
                    # Convert PIL Image to base64 PNG
                    page_image_base64 = self._image_to_base64(page.image.pil_image)
                    logger.debug(
                        f"Page {page_no}: extracted {len(page_markdown)} chars markdown, "
                        f"{len(page_image_base64)} chars base64 image"
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
                        markdown=page_markdown,
                        image_base64=page_image_base64,
                    )
                )

            # CRITICAL CHECK: Verify page images were generated
            if not has_page_images:
                raise RuntimeError(
                    "Docling failed to generate page images. "
                    "This is required for visual comparison. "
                    "Check that generate_page_images=True is working correctly."
                )

            # Step 5: Extract embedded images (figures, tables) from document
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
                full_markdown=full_markdown,
                has_page_images=has_page_images,
                extracted_images=extracted_images,
            )

        except Exception as e:
            logger.error(f"PDF conversion failed: {e}", exc_info=True)
            raise ValueError(f"Failed to convert PDF: {str(e)}") from e

    def _extract_page_markdown(self, doc, page_index: int) -> str:  # type: ignore[no-untyped-def]
        """Extract markdown content for a specific page.

        Args:
            doc: Docling document
            page_index: Zero-based page index

        Returns:
            Markdown string for the page
        """
        from docling_core.types.doc import TableItem, TextItem

        page_texts = []

        # Iterate through document items and filter by page
        for item, level in doc.iterate_items():
            # Check if item has provenance info for this page
            if hasattr(item, 'prov') and item.prov:
                # Get first provenance item (there can be multiple for items spanning pages)
                first_prov = item.prov[0]

                # Check if this item is on the current page
                if hasattr(first_prov, 'page_no') and first_prov.page_no == page_index + 1:
                    if isinstance(item, TextItem):
                        # Add indentation based on hierarchy level
                        indent = "  " * level
                        page_texts.append(f"{indent}{item.text}")
                    elif isinstance(item, TableItem):
                        # Export table to markdown
                        try:
                            table_df = item.export_to_dataframe()
                            page_texts.append(table_df.to_markdown(index=False))
                        except Exception:
                            page_texts.append("[Table content]")

        # If no items found for this page, fall back to full document
        # (this can happen with complex layouts or spanning elements)
        if not page_texts:
            logger.warning(
                f"No page-specific items found for page {page_index}, "
                "using full document markdown"
            )
            return doc.export_to_markdown()

        return "\n\n".join(page_texts)

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
