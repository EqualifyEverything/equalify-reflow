"""Document context models for multi-agent grounding (PRD-013).

This module defines data models for document-level context extraction,
enabling specialist agents to understand heading hierarchy, figure/table
numbering, and recurring elements across the entire document.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    """Detected document type for context-appropriate processing.

    Document types affect agent behavior:
    - ACADEMIC: Formal structure, citations, abstract expected
    - SYLLABUS: Course info, schedule, policies sections
    - PRESENTATION: Slide-like structure, minimal text per page
    - REPORT: Executive summary, findings, recommendations
    - GENERAL: No specific structure detected
    """

    ACADEMIC = "academic"
    SYLLABUS = "syllabus"
    PRESENTATION = "presentation"
    REPORT = "report"
    GENERAL = "general"


class HeadingInfo(BaseModel):
    """Information about a heading in the document.

    Tracks heading hierarchy for cross-page consistency and proper
    nesting validation by the StructureAgent.

    Attributes:
        level: Heading level (1-6, corresponding to H1-H6)
        text: Heading text content (stripped of markdown syntax)
        page_number: Page where heading appears (1-indexed)
        line_number: Line number within page markdown (1-indexed)
        parent_heading: Text of the parent heading (next level up)
        section_id: Generated section identifier (e.g., "1.2.3")

    Example:
        >>> heading = HeadingInfo(
        ...     level=2,
        ...     text="Course Schedule",
        ...     page_number=1,
        ...     line_number=15,
        ...     parent_heading="Course Information",
        ...     section_id="1.2"
        ... )
    """

    level: int = Field(..., ge=1, le=6, description="Heading level (1-6)")
    text: str = Field(..., min_length=1, max_length=500, description="Heading text")
    page_number: int = Field(..., ge=1, description="Page number (1-indexed)")
    line_number: int = Field(default=1, ge=1, description="Line number in page")
    parent_heading: str | None = Field(
        default=None, description="Parent heading text (next level up)"
    )
    section_id: str | None = Field(
        default=None, description="Section identifier (e.g., '1.2.3')"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "level": 2,
                "text": "Course Schedule",
                "page_number": 1,
                "line_number": 15,
                "parent_heading": "Course Information",
                "section_id": "1.2",
            }
        }
    )


class FigureInfo(BaseModel):
    """Information about a figure in the document.

    Tracks figure numbering sequence and alt text presence for
    accessibility validation by the FiguresAgent.

    Attributes:
        figure_number: Sequential figure number in document
        caption: Figure caption if present
        page_number: Page where figure appears (1-indexed)
        line_number: Line number within page markdown
        has_alt_text: Whether alt text is present
        alt_text: Alt text content if present
        image_path: Image file path or URL from markdown

    Example:
        >>> figure = FigureInfo(
        ...     figure_number=1,
        ...     caption="Figure 1: System Architecture",
        ...     page_number=3,
        ...     has_alt_text=True,
        ...     alt_text="Diagram showing system components"
        ... )
    """

    figure_number: int = Field(..., ge=1, description="Sequential figure number")
    caption: str = Field(default="", max_length=1000, description="Figure caption")
    page_number: int = Field(..., ge=1, description="Page number (1-indexed)")
    line_number: int = Field(default=1, ge=1, description="Line number in page")
    has_alt_text: bool = Field(default=False, description="Whether alt text exists")
    alt_text: str = Field(default="", max_length=2000, description="Alt text content")
    image_path: str = Field(default="", max_length=500, description="Image path/URL")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "figure_number": 1,
                "caption": "Figure 1: System Architecture",
                "page_number": 3,
                "line_number": 42,
                "has_alt_text": True,
                "alt_text": "Diagram showing system components",
                "image_path": "images/architecture.png",
            }
        }
    )


class TableInfo(BaseModel):
    """Information about a table in the document.

    Tracks table structure for accessibility validation by the TablesAgent.

    Attributes:
        table_number: Sequential table number in document
        caption: Table caption if present
        page_number: Page where table appears (1-indexed)
        line_number: Line number where table starts
        column_count: Number of columns detected
        row_count: Number of rows detected (including header)
        has_header_row: Whether first row appears to be a header
        header_cells: List of header cell contents

    Example:
        >>> table = TableInfo(
        ...     table_number=1,
        ...     caption="Table 1: Grading Breakdown",
        ...     page_number=2,
        ...     column_count=3,
        ...     row_count=5,
        ...     has_header_row=True,
        ...     header_cells=["Assignment", "Weight", "Due Date"]
        ... )
    """

    table_number: int = Field(..., ge=1, description="Sequential table number")
    caption: str = Field(default="", max_length=1000, description="Table caption")
    page_number: int = Field(..., ge=1, description="Page number (1-indexed)")
    line_number: int = Field(default=1, ge=1, description="Line number where table starts")
    column_count: int = Field(default=0, ge=0, description="Number of columns")
    row_count: int = Field(default=0, ge=0, description="Number of rows")
    has_header_row: bool = Field(default=False, description="Has header row")
    header_cells: list[str] = Field(default_factory=list, description="Header cell contents")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "table_number": 1,
                "caption": "Table 1: Grading Breakdown",
                "page_number": 2,
                "line_number": 30,
                "column_count": 3,
                "row_count": 5,
                "has_header_row": True,
                "header_cells": ["Assignment", "Weight", "Due Date"],
            }
        }
    )


class RecurringElement(BaseModel):
    """Element that appears on multiple pages (headers, footers, etc.).

    Identifies repeated content for agents to recognize and handle
    consistently across pages.

    Attributes:
        element_type: Type of recurring element (header, footer, page_number)
        content: The repeated content text
        pages_found: List of page numbers where element appears
    """

    element_type: str = Field(
        ...,
        description="Element type: header, footer, page_number, watermark",
    )
    content: str = Field(..., max_length=500, description="Repeated content text")
    pages_found: list[int] = Field(
        default_factory=list, description="Pages containing this element"
    )


class TOCEntry(BaseModel):
    """Table of Contents entry.

    Represents an item in a detected table of contents for
    document structure validation.

    Attributes:
        title: Section title from TOC
        level: Nesting level (1 = top level)
        page_reference: Page number referenced (if detected)
        line_number: Line in TOC where entry appears

    Example:
        >>> entry = TOCEntry(
        ...     title="Introduction",
        ...     level=1,
        ...     page_reference=1
        ... )
    """

    title: str = Field(..., max_length=500, description="Section title")
    level: int = Field(default=1, ge=1, le=6, description="Nesting level")
    page_reference: int | None = Field(
        default=None, ge=1, description="Referenced page number"
    )
    line_number: int = Field(default=1, ge=1, description="Line in TOC")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Introduction",
                "level": 1,
                "page_reference": 1,
                "line_number": 5,
            }
        }
    )


class DocumentContext(BaseModel):
    """Complete document context for agent grounding.

    Aggregates all extracted document-level information including
    heading hierarchy, figures, tables, and recurring elements.

    This context is injected into agent prompts to enable cross-page
    consistency in corrections.

    Attributes:
        document_id: Job ID or document identifier
        document_type: Detected document type
        total_pages: Total number of pages
        title: Document title (from first H1 or metadata)
        headings: All headings in document order
        figures: All figures in document order
        tables: All tables in document order
        toc_entries: Table of contents entries (if detected)
        recurring_elements: Headers, footers, page numbers
        has_single_h1: Whether document has exactly one H1

    Example:
        >>> context = DocumentContext(
        ...     document_id="job-123",
        ...     document_type=DocumentType.SYLLABUS,
        ...     total_pages=5,
        ...     title="CS 101 Course Syllabus",
        ...     headings=[HeadingInfo(level=1, text="CS 101", page_number=1)],
        ...     has_single_h1=True
        ... )
    """

    document_id: str = Field(..., description="Job ID or document identifier")
    document_type: DocumentType = Field(
        default=DocumentType.GENERAL, description="Detected document type"
    )
    total_pages: int = Field(..., ge=1, description="Total pages in document")
    title: str = Field(default="", max_length=500, description="Document title")

    # Structure elements
    headings: list[HeadingInfo] = Field(
        default_factory=list, description="All headings in order"
    )
    figures: list[FigureInfo] = Field(
        default_factory=list, description="All figures in order"
    )
    tables: list[TableInfo] = Field(
        default_factory=list, description="All tables in order"
    )

    # Navigation
    toc_entries: list[TOCEntry] = Field(
        default_factory=list, description="Table of contents entries"
    )
    recurring_elements: list[RecurringElement] = Field(
        default_factory=list, description="Recurring elements (headers/footers)"
    )

    # Metadata
    has_single_h1: bool = Field(
        default=False, description="Document has exactly one H1"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "job-123",
                "document_type": "syllabus",
                "total_pages": 5,
                "title": "CS 101 Course Syllabus",
                "headings": [
                    {
                        "level": 1,
                        "text": "CS 101 Course Syllabus",
                        "page_number": 1,
                        "line_number": 1,
                    }
                ],
                "has_single_h1": True,
            }
        }
    )

    def get_headings_before_page(self, page_number: int) -> list[HeadingInfo]:
        """Get all headings that appear before a given page.

        Useful for understanding document structure up to a specific page.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            List of headings appearing before the target page
        """
        return [h for h in self.headings if h.page_number < page_number]

    def get_headings_on_page(self, page_number: int) -> list[HeadingInfo]:
        """Get all headings on a specific page.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            List of headings on the target page
        """
        return [h for h in self.headings if h.page_number == page_number]

    def get_current_section_context(self, page_number: int) -> dict[str, str | None]:
        """Get the current section context for a page.

        Returns the most recent heading at each level that would apply
        to content on the given page.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            Dictionary with keys 'h1' through 'h6' containing heading text
            or None if no heading at that level precedes this page
        """
        context: dict[str, str | None] = {f"h{i}": None for i in range(1, 7)}

        # Get all headings up to and including this page
        relevant_headings = [h for h in self.headings if h.page_number <= page_number]

        # Track most recent heading at each level
        for heading in relevant_headings:
            level_key = f"h{heading.level}"
            context[level_key] = heading.text

            # Clear lower-level headings when a higher-level heading is encountered
            for lower_level in range(heading.level + 1, 7):
                context[f"h{lower_level}"] = None

        return context

    def get_expected_heading_level(self, page_number: int) -> int:
        """Calculate the expected next heading level for a page.

        Based on the document's heading hierarchy, determines what heading
        level would be appropriate for new content on this page.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            Expected heading level (1-6), defaults to 2 if H1 exists
        """
        section_context = self.get_current_section_context(page_number)

        # Find the deepest level with a heading
        deepest_level = 0
        for level in range(1, 7):
            if section_context[f"h{level}"] is not None:
                deepest_level = level

        if deepest_level == 0:
            # No headings yet, expect H1
            return 1
        elif deepest_level < 6:
            # Can go one level deeper or stay at current level
            return deepest_level + 1
        else:
            # At max depth, stay at H6
            return 6

    def get_figures_before_page(self, page_number: int) -> list[FigureInfo]:
        """Get all figures that appear before a given page.

        Useful for determining the next figure number.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            List of figures appearing before the target page
        """
        return [f for f in self.figures if f.page_number < page_number]

    def get_tables_before_page(self, page_number: int) -> list[TableInfo]:
        """Get all tables that appear before a given page.

        Useful for determining the next table number.

        Args:
            page_number: Target page number (1-indexed)

        Returns:
            List of tables appearing before the target page
        """
        return [t for t in self.tables if t.page_number < page_number]

    def get_next_figure_number(self, page_number: int) -> int:
        """Get the next sequential figure number for a page.

        Args:
            page_number: Current page number (1-indexed)

        Returns:
            Next figure number to use
        """
        figures_before = self.get_figures_before_page(page_number)
        figures_on_page = [f for f in self.figures if f.page_number == page_number]
        return len(figures_before) + len(figures_on_page) + 1

    def get_next_table_number(self, page_number: int) -> int:
        """Get the next sequential table number for a page.

        Args:
            page_number: Current page number (1-indexed)

        Returns:
            Next table number to use
        """
        tables_before = self.get_tables_before_page(page_number)
        tables_on_page = [t for t in self.tables if t.page_number == page_number]
        return len(tables_before) + len(tables_on_page) + 1

    def is_recurring_element(self, text: str) -> bool:
        """Check if text matches a recurring element.

        Useful for agents to identify headers/footers that should
        be handled consistently.

        Args:
            text: Text to check

        Returns:
            True if text matches a recurring element
        """
        text_lower = text.strip().lower()
        for element in self.recurring_elements:
            if element.content.strip().lower() == text_lower:
                return True
        return False

    @property
    def heading_count(self) -> int:
        """Total number of headings in document."""
        return len(self.headings)

    @property
    def figure_count(self) -> int:
        """Total number of figures in document."""
        return len(self.figures)

    @property
    def table_count(self) -> int:
        """Total number of tables in document."""
        return len(self.tables)

    @property
    def h1_count(self) -> int:
        """Count of H1 headings in document."""
        return len([h for h in self.headings if h.level == 1])

    @property
    def figures_missing_alt_text(self) -> list[FigureInfo]:
        """Figures that are missing alt text."""
        return [f for f in self.figures if not f.has_alt_text]

    @property
    def tables_missing_headers(self) -> list[TableInfo]:
        """Tables that are missing header rows."""
        return [t for t in self.tables if not t.has_header_row]


# Re-export for convenience
__all__ = [
    "DocumentType",
    "HeadingInfo",
    "FigureInfo",
    "TableInfo",
    "RecurringElement",
    "TOCEntry",
    "DocumentContext",
]
