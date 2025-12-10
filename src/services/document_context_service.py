"""Document context extraction service (PRD-013).

This module provides extraction of document-level context from Docling's
structured document model to ground specialist agents with heading hierarchy,
figure/table sequences, and recurring elements.
"""

import logging
import re
import time
from collections import Counter, defaultdict

from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.labels import DocItemLabel

from src.config import settings
from src.shared.models.context_models import (
    DocumentContext,
    DocumentType,
    FigureInfo,
    HeadingInfo,
    RecurringElement,
    TableInfo,
    TOCEntry,
)

logger = logging.getLogger(__name__)

# Document type detection patterns (still useful for keyword matching)
ACADEMIC_PATTERNS = [
    re.compile(r"\babstract\b", re.IGNORECASE),
    re.compile(r"\breferences\b", re.IGNORECASE),
    re.compile(r"\bcitation", re.IGNORECASE),
    re.compile(r"\bbibliography\b", re.IGNORECASE),
    re.compile(r"\bintroduction\b", re.IGNORECASE),
    re.compile(r"\bconclusion\b", re.IGNORECASE),
    re.compile(r"\bmethodology\b", re.IGNORECASE),
]

SYLLABUS_PATTERNS = [
    re.compile(r"\bcourse\s+(syllabus|schedule|outline)\b", re.IGNORECASE),
    re.compile(r"\boffice\s+hours\b", re.IGNORECASE),
    re.compile(r"\bgrading\s+(policy|breakdown|criteria)\b", re.IGNORECASE),
    re.compile(r"\bprerequisite", re.IGNORECASE),
    re.compile(r"\blearning\s+(objective|outcome)", re.IGNORECASE),
    re.compile(r"\bassignment", re.IGNORECASE),
    re.compile(r"\bexam\b", re.IGNORECASE),
]

PRESENTATION_PATTERNS = [
    re.compile(r"\bslide\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bagenda\b", re.IGNORECASE),
    re.compile(r"\bquestion", re.IGNORECASE),
]

# TOC detection pattern
TOC_PATTERN = re.compile(
    r"table\s+of\s+contents|contents|toc", re.IGNORECASE
)


class DocumentContextService:
    """Service for extracting document-level context from Docling documents.

    Provides extraction of:
    - Heading hierarchy with parent tracking (from SECTION_HEADER items)
    - Figure sequences with captions (from pictures collection)
    - Table sequences with structure (from tables collection)
    - Table of contents detection
    - Recurring elements (PAGE_HEADER, PAGE_FOOTER items)
    - Document type classification

    Uses Docling's structured document model for accurate extraction
    without regex parsing of markdown.

    Attributes:
        max_headings: Maximum headings to track (from config)
        recurring_threshold: Minimum occurrence rate for recurring elements

    Example:
        >>> from docling.document_converter import DocumentConverter
        >>> converter = DocumentConverter()
        >>> result = converter.convert("document.pdf")
        >>> service = DocumentContextService()
        >>> context = service.extract_context("job-123", result.document)
        >>> print(f"Found {context.heading_count} headings")
    """

    def __init__(
        self,
        max_headings: int | None = None,
        recurring_threshold: float | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            max_headings: Maximum headings to track (default from config)
            recurring_threshold: Minimum occurrence rate for recurring elements
        """
        self.max_headings = max_headings or settings.context_max_headings
        self.recurring_threshold = (
            recurring_threshold or settings.context_recurring_threshold
        )
        logger.debug(
            f"DocumentContextService initialized "
            f"(max_headings={self.max_headings}, threshold={self.recurring_threshold})"
        )

    def extract_context(
        self,
        document_id: str,
        docling_document: DoclingDocument,
    ) -> DocumentContext:
        """Extract document-level context from a Docling document.

        Analyzes the document's structured content to extract headings,
        figures, tables, and recurring elements.

        Args:
            document_id: Job ID or document identifier
            docling_document: Docling's structured document model

        Returns:
            DocumentContext with all extracted information
        """
        start_time = time.monotonic()

        # Get total pages
        total_pages = len(docling_document.pages) if docling_document.pages else 1

        logger.info(
            f"Extracting context for document {document_id} ({total_pages} pages)"
        )

        # Extract all elements from Docling's structured model
        headings = self._extract_headings(docling_document)
        figures = self._extract_figures(docling_document)
        tables = self._extract_tables(docling_document)
        toc_entries = self._detect_toc(docling_document)
        recurring = self._detect_recurring_elements(docling_document, total_pages)

        # Detect document type from combined text
        combined_text = self._get_combined_text(docling_document)
        doc_type = self._detect_document_type(combined_text)

        # Extract title
        title = self._extract_title(docling_document, headings)

        # Build heading hierarchy
        headings = self._build_heading_hierarchy(headings)

        # Check for single H1
        h1_count = len([h for h in headings if h.level == 1])

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        context = DocumentContext(
            document_id=document_id,
            document_type=doc_type,
            total_pages=total_pages,
            title=title,
            headings=headings[: self.max_headings],
            figures=figures,
            tables=tables,
            toc_entries=toc_entries,
            recurring_elements=recurring,
            has_single_h1=(h1_count == 1),
        )

        logger.info(
            f"Context extracted: {context.heading_count} headings, "
            f"{context.figure_count} figures, {context.table_count} tables, "
            f"type={doc_type.value}, time={elapsed_ms}ms"
        )

        return context

    def format_context_for_prompt(
        self,
        context: DocumentContext,
        page_number: int,
        max_length: int = 2000,
    ) -> str:
        """Format context for injection into agent prompts.

        Creates a concise summary of document context relevant to a
        specific page, suitable for LLM prompt injection.

        Args:
            context: DocumentContext to format
            page_number: Current page being processed
            max_length: Maximum output length in characters

        Returns:
            Formatted string for prompt injection
        """
        lines: list[str] = ["**Document Context:**"]

        # Document metadata
        lines.append(f"- Type: {context.document_type.value}")
        if context.title:
            lines.append(f"- Title: {context.title}")
        lines.append(f"- Total pages: {context.total_pages}")
        lines.append(f"- Page: {page_number} of {context.total_pages}")

        # Current section
        section = context.get_current_section_context(page_number)
        active_sections = [
            (level, text)
            for level, text in section.items()
            if text is not None
        ]
        if active_sections:
            lines.append("\n**Current Section:**")
            for level, text in active_sections:
                indent = "  " * (int(level[1]) - 1)
                lines.append(f"{indent}- {level.upper()}: {text}")

        # Expected heading level
        expected_level = context.get_expected_heading_level(page_number)
        lines.append(f"\n**Expected heading level**: H{expected_level}")

        # Figure/table numbering
        next_fig = context.get_next_figure_number(page_number)
        next_table = context.get_next_table_number(page_number)
        lines.append(f"**Next figure number**: {next_fig}")
        lines.append(f"**Next table number**: {next_table}")

        # Single H1 warning
        if context.has_single_h1:
            lines.append("\n*Note: Document has single H1 - maintain hierarchy*")

        # Recurring elements
        if context.recurring_elements:
            lines.append("\n**Recurring elements (ignore in corrections):**")
            for elem in context.recurring_elements[:3]:
                lines.append(f"- {elem.element_type}: \"{elem.content[:50]}...\"")

        result = "\n".join(lines)

        # Truncate if too long
        if len(result) > max_length:
            result = result[: max_length - 3] + "..."

        return result

    def _extract_headings(
        self,
        doc: DoclingDocument,
    ) -> list[HeadingInfo]:
        """Extract headings from Docling's text items.

        Uses SECTION_HEADER label to identify headings.

        Args:
            doc: Docling document

        Returns:
            List of HeadingInfo in document order
        """
        headings: list[HeadingInfo] = []

        for text_item in doc.texts:
            if text_item.label == DocItemLabel.SECTION_HEADER:
                # Skip empty text items
                text = text_item.text.strip() if text_item.text else ""
                if not text:
                    continue

                # Get page number from provenance
                page_no = 1
                line_no = 1
                if text_item.prov:
                    page_no = text_item.prov[0].page_no

                # Get heading level (Docling provides this for section headers)
                level = getattr(text_item, "level", 1) or 1
                # Ensure level is within valid range
                level = max(1, min(6, level))

                headings.append(
                    HeadingInfo(
                        level=level,
                        text=text,
                        page_number=page_no,
                        line_number=line_no,
                    )
                )

        return headings

    def _extract_figures(
        self,
        doc: DoclingDocument,
    ) -> list[FigureInfo]:
        """Extract figures from Docling's pictures collection.

        Args:
            doc: Docling document

        Returns:
            List of FigureInfo in document order
        """
        figures: list[FigureInfo] = []

        for i, pic in enumerate(doc.pictures, 1):
            # Get page number from provenance
            page_no = 1
            line_no = 1
            if pic.prov:
                page_no = pic.prov[0].page_no

            # Get caption if available
            caption = ""
            alt_text = ""
            has_alt = False

            if pic.captions:
                # Captions are typically RefItems pointing to text
                has_alt = True
                caption_texts = []
                for cap_ref in pic.captions:
                    # Try to resolve caption reference
                    if hasattr(cap_ref, "text"):
                        caption_texts.append(cap_ref.text)
                if caption_texts:
                    caption = " ".join(caption_texts)
                    alt_text = caption

            # Check for caption_text property
            if hasattr(pic, "caption_text"):
                cap_text_attr = pic.caption_text
                if callable(cap_text_attr):
                    try:
                        cap_text_result = cap_text_attr(doc)
                    except Exception:
                        cap_text_result = ""
                else:
                    cap_text_result = cap_text_attr if isinstance(cap_text_attr, str) else ""
                if cap_text_result:
                    has_alt = True
                    alt_text = cap_text_result
                    if not caption:
                        caption = cap_text_result

            figures.append(
                FigureInfo(
                    figure_number=i,
                    page_number=page_no,
                    line_number=line_no,
                    has_alt_text=has_alt,
                    alt_text=alt_text[:2000] if alt_text else "",
                    caption=caption[:1000] if caption else "",
                )
            )

        return figures

    def _extract_tables(
        self,
        doc: DoclingDocument,
    ) -> list[TableInfo]:
        """Extract tables from Docling's tables collection.

        Args:
            doc: Docling document

        Returns:
            List of TableInfo in document order
        """
        tables: list[TableInfo] = []

        for i, tbl in enumerate(doc.tables, 1):
            # Get page number from provenance
            page_no = 1
            line_no = 1
            if tbl.prov:
                page_no = tbl.prov[0].page_no

            # Get table structure if available
            col_count = 0
            row_count = 0
            header_cells: list[str] = []

            # Try to get table data structure
            if hasattr(tbl, "data") and tbl.data:
                data = tbl.data
                if hasattr(data, "num_cols"):
                    col_count = data.num_cols
                if hasattr(data, "num_rows"):
                    row_count = data.num_rows
                # Try to extract header cells
                if hasattr(data, "grid") and data.grid:
                    first_row = data.grid[0] if data.grid else []
                    header_cells = [
                        str(cell.text) if hasattr(cell, "text") else str(cell)
                        for cell in first_row
                    ][:10]  # Limit to 10 columns

            # Get caption
            caption = ""
            if hasattr(tbl, "caption_text"):
                cap_text_attr = tbl.caption_text
                if callable(cap_text_attr):
                    try:
                        cap_text_result = cap_text_attr(doc)
                    except Exception:
                        cap_text_result = ""
                else:
                    cap_text_result = cap_text_attr if isinstance(cap_text_attr, str) else ""
                if cap_text_result:
                    caption = cap_text_result

            tables.append(
                TableInfo(
                    table_number=i,
                    page_number=page_no,
                    line_number=line_no,
                    column_count=col_count,
                    row_count=row_count,
                    has_header_row=bool(header_cells),
                    header_cells=header_cells,
                    caption=caption[:1000] if caption else "",
                )
            )

        return tables

    def _detect_toc(
        self,
        doc: DoclingDocument,
    ) -> list[TOCEntry]:
        """Detect table of contents entries.

        Looks for DOCUMENT_INDEX items or headings containing TOC keywords.

        Args:
            doc: Docling document

        Returns:
            List of TOCEntry if TOC detected
        """
        entries: list[TOCEntry] = []

        # Check for DOCUMENT_INDEX items (Docling's explicit TOC)
        for text_item in doc.texts:
            if text_item.label == DocItemLabel.DOCUMENT_INDEX:  # type: ignore[comparison-overlap]
                line_no = 1
                if text_item.prov:
                    # Use bbox to estimate line number
                    line_no = 1

                entries.append(
                    TOCEntry(
                        title=text_item.text.strip() if text_item.text else "",
                        level=1,
                        line_number=line_no,
                    )
                )

        # Also check for section headers that look like TOC
        if not entries:
            for text_item in doc.texts:
                if text_item.label == DocItemLabel.SECTION_HEADER:
                    text = text_item.text or ""
                    if TOC_PATTERN.search(text):
                        # Found TOC header - entries would follow
                        # but we don't have a reliable way to extract them
                        # without more sophisticated parsing
                        break

        return entries

    def _detect_recurring_elements(
        self,
        doc: DoclingDocument,
        total_pages: int,
    ) -> list[RecurringElement]:
        """Detect recurring elements using Docling's PAGE_HEADER/PAGE_FOOTER.

        Args:
            doc: Docling document
            total_pages: Total number of pages

        Returns:
            List of RecurringElement
        """
        recurring: list[RecurringElement] = []

        # Group by element type and content
        headers: Counter[str] = Counter()
        footers: Counter[str] = Counter()
        header_pages: dict[str, list[int]] = defaultdict(list)
        footer_pages: dict[str, list[int]] = defaultdict(list)

        for text_item in doc.texts:
            if not text_item.text:
                continue

            content = text_item.text.strip()
            if len(content) < 3:
                continue

            page_no = text_item.prov[0].page_no if text_item.prov else 1

            if text_item.label == DocItemLabel.PAGE_HEADER:
                headers[content] += 1
                header_pages[content].append(page_no)
            elif text_item.label == DocItemLabel.PAGE_FOOTER:
                footers[content] += 1
                footer_pages[content].append(page_no)

        # Add headers meeting threshold
        for content, count in headers.items():
            rate = count / total_pages if total_pages > 0 else 0
            if rate >= self.recurring_threshold:
                recurring.append(
                    RecurringElement(
                        element_type="header",
                        content=content[:500],
                        pages_found=sorted(header_pages[content]),
                    )
                )

        # Add footers meeting threshold
        for content, count in footers.items():
            rate = count / total_pages if total_pages > 0 else 0
            if rate >= self.recurring_threshold:
                # Check if it looks like a page number
                is_page_num = bool(
                    re.match(r"^\d+$|^page\s*\d+", content, re.IGNORECASE)
                )
                recurring.append(
                    RecurringElement(
                        element_type="page_number" if is_page_num else "footer",
                        content=content[:500],
                        pages_found=sorted(footer_pages[content]),
                    )
                )

        return recurring

    def _get_combined_text(self, doc: DoclingDocument) -> str:
        """Get all text content combined for document type detection.

        Args:
            doc: Docling document

        Returns:
            Combined text content
        """
        texts: list[str] = []
        for text_item in doc.texts:
            if text_item.text:
                texts.append(text_item.text)
        return "\n".join(texts)

    def _detect_document_type(self, combined_text: str) -> DocumentType:
        """Detect document type from content patterns.

        Uses keyword matching to classify document type.

        Args:
            combined_text: All text content combined

        Returns:
            Detected DocumentType
        """
        # Count pattern matches for each type
        academic_score = sum(
            1 for pattern in ACADEMIC_PATTERNS if pattern.search(combined_text)
        )
        syllabus_score = sum(
            1 for pattern in SYLLABUS_PATTERNS if pattern.search(combined_text)
        )
        presentation_score = sum(
            1 for pattern in PRESENTATION_PATTERNS if pattern.search(combined_text)
        )

        # Require minimum matches
        min_matches = 2

        # Syllabus patterns are most specific
        if syllabus_score >= min_matches:
            return DocumentType.SYLLABUS

        # Academic paper patterns
        if academic_score >= min_matches + 1:
            return DocumentType.ACADEMIC

        # Presentation patterns
        if presentation_score >= min_matches:
            return DocumentType.PRESENTATION

        return DocumentType.GENERAL

    def _extract_title(
        self,
        doc: DoclingDocument,
        headings: list[HeadingInfo],
    ) -> str:
        """Extract document title.

        Looks for TITLE label first, then falls back to first H1.

        Args:
            doc: Docling document
            headings: Extracted headings

        Returns:
            Title text or empty string
        """
        # First check for explicit TITLE item
        for text_item in doc.texts:
            if text_item.label == DocItemLabel.TITLE:
                return text_item.text.strip() if text_item.text else ""

        # Fall back to first H1
        for heading in headings:
            if heading.level == 1:
                return heading.text

        return ""

    def _build_heading_hierarchy(
        self,
        headings: list[HeadingInfo],
    ) -> list[HeadingInfo]:
        """Build parent relationships for headings.

        Assigns parent_heading and section_id to each heading based
        on the document's heading structure.

        Args:
            headings: List of headings in document order

        Returns:
            Same list with parent_heading and section_id populated
        """
        if not headings:
            return headings

        # Track current heading at each level
        parent_by_level: dict[int, str] = {}
        section_counters: dict[int, int] = {}

        for heading in headings:
            level = heading.level

            # Reset lower levels when encountering a heading at this level
            for lower_level in list(parent_by_level.keys()):
                if lower_level >= level:
                    del parent_by_level[lower_level]
            for lower_level in list(section_counters.keys()):
                if lower_level > level:
                    del section_counters[lower_level]

            # Increment counter for this level
            section_counters[level] = section_counters.get(level, 0) + 1

            # Build section ID (e.g., "1.2.3")
            section_parts = []
            for lvl in range(1, level + 1):
                if lvl in section_counters:
                    section_parts.append(str(section_counters[lvl]))
            heading.section_id = ".".join(section_parts) if section_parts else None

            # Assign parent (the most recent heading one level up)
            if level > 1 and (level - 1) in parent_by_level:
                heading.parent_heading = parent_by_level[level - 1]

            # Register this heading as potential parent for lower levels
            parent_by_level[level] = heading.text

        return headings


# Export for convenience
__all__ = ["DocumentContextService"]
