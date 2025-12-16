"""Tests for document context models."""

import pytest
from pydantic import ValidationError
from src.shared.models.context_models import (
    DocumentContext,
    DocumentType,
    FigureInfo,
    HeadingInfo,
    RecurringElement,
    TableInfo,
    TOCEntry,
)


@pytest.mark.unit
class TestHeadingInfo:
    """Test HeadingInfo model validation."""

    def test_valid_heading(self):
        """Test creation with valid data."""
        heading = HeadingInfo(
            level=2,
            text="Course Schedule",
            page_number=1,
            line_number=15,
            parent_heading="Course Information",
            section_id="1.2",
        )
        assert heading.level == 2
        assert heading.text == "Course Schedule"
        assert heading.page_number == 1
        assert heading.line_number == 15
        assert heading.parent_heading == "Course Information"
        assert heading.section_id == "1.2"

    def test_minimal_heading(self):
        """Test creation with minimal required data."""
        heading = HeadingInfo(level=1, text="Title", page_number=1)
        assert heading.level == 1
        assert heading.text == "Title"
        assert heading.line_number == 1  # default
        assert heading.parent_heading is None
        assert heading.section_id is None

    def test_level_bounds(self):
        """Test heading level validation (1-6)."""
        # Valid levels
        for level in range(1, 7):
            heading = HeadingInfo(level=level, text="Test", page_number=1)
            assert heading.level == level

        # Invalid levels
        with pytest.raises(ValidationError):
            HeadingInfo(level=0, text="Test", page_number=1)

        with pytest.raises(ValidationError):
            HeadingInfo(level=7, text="Test", page_number=1)

    def test_page_number_must_be_positive(self):
        """Test page number validation."""
        with pytest.raises(ValidationError):
            HeadingInfo(level=1, text="Test", page_number=0)

        with pytest.raises(ValidationError):
            HeadingInfo(level=1, text="Test", page_number=-1)

    def test_text_not_empty(self):
        """Test that heading text cannot be empty."""
        with pytest.raises(ValidationError):
            HeadingInfo(level=1, text="", page_number=1)

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        heading = HeadingInfo(
            level=3,
            text="Test Section",
            page_number=5,
            line_number=20,
            parent_heading="Parent",
            section_id="1.2.3",
        )
        json_data = heading.model_dump_json()
        restored = HeadingInfo.model_validate_json(json_data)
        assert restored.level == heading.level
        assert restored.text == heading.text
        assert restored.section_id == heading.section_id


@pytest.mark.unit
class TestFigureInfo:
    """Test FigureInfo model validation."""

    def test_valid_figure(self):
        """Test creation with valid data."""
        figure = FigureInfo(
            figure_number=1,
            caption="Figure 1: System Architecture",
            page_number=3,
            line_number=42,
            has_alt_text=True,
            alt_text="Diagram showing system components",
            image_path="images/architecture.png",
        )
        assert figure.figure_number == 1
        assert figure.caption == "Figure 1: System Architecture"
        assert figure.has_alt_text is True
        assert figure.alt_text == "Diagram showing system components"

    def test_minimal_figure(self):
        """Test creation with minimal data."""
        figure = FigureInfo(figure_number=1, page_number=1)
        assert figure.figure_number == 1
        assert figure.caption == ""
        assert figure.has_alt_text is False
        assert figure.alt_text == ""
        assert figure.image_path == ""

    def test_figure_number_must_be_positive(self):
        """Test figure number validation."""
        with pytest.raises(ValidationError):
            FigureInfo(figure_number=0, page_number=1)

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        figure = FigureInfo(
            figure_number=5,
            page_number=10,
            has_alt_text=True,
            alt_text="Test alt text",
        )
        json_data = figure.model_dump_json()
        restored = FigureInfo.model_validate_json(json_data)
        assert restored.figure_number == figure.figure_number
        assert restored.has_alt_text == figure.has_alt_text


@pytest.mark.unit
class TestTableInfo:
    """Test TableInfo model validation."""

    def test_valid_table(self):
        """Test creation with valid data."""
        table = TableInfo(
            table_number=1,
            caption="Table 1: Grading Breakdown",
            page_number=2,
            line_number=30,
            column_count=3,
            row_count=5,
            has_header_row=True,
            header_cells=["Assignment", "Weight", "Due Date"],
        )
        assert table.table_number == 1
        assert table.column_count == 3
        assert table.row_count == 5
        assert table.has_header_row is True
        assert len(table.header_cells) == 3

    def test_minimal_table(self):
        """Test creation with minimal data."""
        table = TableInfo(table_number=1, page_number=1)
        assert table.table_number == 1
        assert table.column_count == 0
        assert table.row_count == 0
        assert table.has_header_row is False
        assert table.header_cells == []

    def test_table_number_must_be_positive(self):
        """Test table number validation."""
        with pytest.raises(ValidationError):
            TableInfo(table_number=0, page_number=1)

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        table = TableInfo(
            table_number=2,
            page_number=5,
            column_count=4,
            has_header_row=True,
            header_cells=["A", "B", "C", "D"],
        )
        json_data = table.model_dump_json()
        restored = TableInfo.model_validate_json(json_data)
        assert restored.table_number == table.table_number
        assert restored.header_cells == table.header_cells


@pytest.mark.unit
class TestRecurringElement:
    """Test RecurringElement model validation."""

    def test_valid_recurring_element(self):
        """Test creation with valid data."""
        element = RecurringElement(
            element_type="footer",
            content="CS 101 - Fall 2024",
            pages_found=[1, 2, 3, 4, 5],
        )
        assert element.element_type == "footer"
        assert element.content == "CS 101 - Fall 2024"
        assert len(element.pages_found) == 5


@pytest.mark.unit
class TestTOCEntry:
    """Test TOCEntry model validation."""

    def test_valid_toc_entry(self):
        """Test creation with valid data."""
        entry = TOCEntry(
            title="Introduction",
            level=1,
            page_reference=1,
            line_number=5,
        )
        assert entry.title == "Introduction"
        assert entry.level == 1
        assert entry.page_reference == 1

    def test_minimal_toc_entry(self):
        """Test creation with minimal data."""
        entry = TOCEntry(title="Test")
        assert entry.title == "Test"
        assert entry.level == 1
        assert entry.page_reference is None

    def test_level_bounds(self):
        """Test TOC level validation."""
        with pytest.raises(ValidationError):
            TOCEntry(title="Test", level=0)

        with pytest.raises(ValidationError):
            TOCEntry(title="Test", level=7)


@pytest.mark.unit
class TestDocumentContext:
    """Test DocumentContext model validation."""

    def test_valid_document_context(self):
        """Test creation with valid data."""
        context = DocumentContext(
            document_id="job-123",
            document_type=DocumentType.SYLLABUS,
            total_pages=5,
            title="CS 101 Course Syllabus",
            has_single_h1=True,
        )
        assert context.document_id == "job-123"
        assert context.document_type == DocumentType.SYLLABUS
        assert context.total_pages == 5
        assert context.title == "CS 101 Course Syllabus"
        assert context.has_single_h1 is True

    def test_minimal_context(self):
        """Test creation with minimal data."""
        context = DocumentContext(document_id="test", total_pages=1)
        assert context.document_type == DocumentType.GENERAL
        assert context.title == ""
        assert context.headings == []
        assert context.figures == []
        assert context.tables == []

    def test_document_type_enum(self):
        """Test all document types."""
        for doc_type in DocumentType:
            context = DocumentContext(
                document_id="test",
                total_pages=1,
                document_type=doc_type,
            )
            assert context.document_type == doc_type

    def test_get_headings_before_page(self):
        """Test filtering headings before a page."""
        headings = [
            HeadingInfo(level=1, text="Title", page_number=1),
            HeadingInfo(level=2, text="Section 1", page_number=1),
            HeadingInfo(level=2, text="Section 2", page_number=2),
            HeadingInfo(level=3, text="Subsection", page_number=3),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=3,
            headings=headings,
        )

        # Before page 1 - none
        before_1 = context.get_headings_before_page(1)
        assert len(before_1) == 0

        # Before page 2 - two from page 1
        before_2 = context.get_headings_before_page(2)
        assert len(before_2) == 2
        assert all(h.page_number == 1 for h in before_2)

        # Before page 3 - three from pages 1 and 2
        before_3 = context.get_headings_before_page(3)
        assert len(before_3) == 3

    def test_get_headings_on_page(self):
        """Test filtering headings on a specific page."""
        headings = [
            HeadingInfo(level=1, text="Title", page_number=1),
            HeadingInfo(level=2, text="Section 1", page_number=1),
            HeadingInfo(level=2, text="Section 2", page_number=2),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=2,
            headings=headings,
        )

        on_page_1 = context.get_headings_on_page(1)
        assert len(on_page_1) == 2

        on_page_2 = context.get_headings_on_page(2)
        assert len(on_page_2) == 1
        assert on_page_2[0].text == "Section 2"

        on_page_3 = context.get_headings_on_page(3)
        assert len(on_page_3) == 0

    def test_get_current_section_context(self):
        """Test section context calculation."""
        headings = [
            HeadingInfo(level=1, text="Document Title", page_number=1),
            HeadingInfo(level=2, text="Chapter 1", page_number=1),
            HeadingInfo(level=3, text="Section 1.1", page_number=2),
            HeadingInfo(level=2, text="Chapter 2", page_number=3),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=3,
            headings=headings,
        )

        # Page 1: Should have h1 and h2
        section_1 = context.get_current_section_context(1)
        assert section_1["h1"] == "Document Title"
        assert section_1["h2"] == "Chapter 1"
        assert section_1["h3"] is None

        # Page 2: Should have h1, h2, h3
        section_2 = context.get_current_section_context(2)
        assert section_2["h1"] == "Document Title"
        assert section_2["h2"] == "Chapter 1"
        assert section_2["h3"] == "Section 1.1"

        # Page 3: h3 should be cleared when new h2 appears
        section_3 = context.get_current_section_context(3)
        assert section_3["h1"] == "Document Title"
        assert section_3["h2"] == "Chapter 2"
        assert section_3["h3"] is None  # Reset by Chapter 2

    def test_get_expected_heading_level(self):
        """Test expected heading level calculation."""
        # Empty document - expect H1
        empty_context = DocumentContext(document_id="test", total_pages=1)
        assert empty_context.get_expected_heading_level(1) == 1

        # Document with H1 - expect H2
        with_h1 = DocumentContext(
            document_id="test",
            total_pages=2,
            headings=[HeadingInfo(level=1, text="Title", page_number=1)],
        )
        assert with_h1.get_expected_heading_level(2) == 2

        # Document with H1 and H2 - expect H3
        with_h2 = DocumentContext(
            document_id="test",
            total_pages=2,
            headings=[
                HeadingInfo(level=1, text="Title", page_number=1),
                HeadingInfo(level=2, text="Chapter", page_number=1),
            ],
        )
        assert with_h2.get_expected_heading_level(2) == 3

    def test_get_next_figure_number(self):
        """Test next figure number calculation."""
        figures = [
            FigureInfo(figure_number=1, page_number=1),
            FigureInfo(figure_number=2, page_number=1),
            FigureInfo(figure_number=3, page_number=2),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=3,
            figures=figures,
        )

        # Page 1: 2 figures on page, next is 3
        assert context.get_next_figure_number(1) == 3

        # Page 2: 2 before + 1 on page, next is 4
        assert context.get_next_figure_number(2) == 4

        # Page 3: 3 before, next is 4
        assert context.get_next_figure_number(3) == 4

    def test_get_next_table_number(self):
        """Test next table number calculation."""
        tables = [
            TableInfo(table_number=1, page_number=1),
            TableInfo(table_number=2, page_number=2),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=3,
            tables=tables,
        )

        assert context.get_next_table_number(1) == 2
        assert context.get_next_table_number(2) == 3
        assert context.get_next_table_number(3) == 3

    def test_is_recurring_element(self):
        """Test recurring element detection."""
        recurring = [
            RecurringElement(
                element_type="footer",
                content="Page Footer Text",
                pages_found=[1, 2, 3],
            ),
        ]
        context = DocumentContext(
            document_id="test",
            total_pages=3,
            recurring_elements=recurring,
        )

        assert context.is_recurring_element("Page Footer Text") is True
        assert context.is_recurring_element("page footer text") is True  # Case insensitive
        assert context.is_recurring_element("  Page Footer Text  ") is True  # Whitespace
        assert context.is_recurring_element("Other Text") is False

    def test_property_shortcuts(self):
        """Test convenience properties."""
        context = DocumentContext(
            document_id="test",
            total_pages=5,
            headings=[
                HeadingInfo(level=1, text="Title", page_number=1),
                HeadingInfo(level=2, text="Ch1", page_number=1),
                HeadingInfo(level=1, text="Appendix", page_number=5),
            ],
            figures=[
                FigureInfo(figure_number=1, page_number=1, has_alt_text=True, alt_text="desc"),
                FigureInfo(figure_number=2, page_number=2, has_alt_text=False),
            ],
            tables=[
                TableInfo(table_number=1, page_number=1, has_header_row=True),
                TableInfo(table_number=2, page_number=2, has_header_row=False),
            ],
        )

        assert context.heading_count == 3
        assert context.figure_count == 2
        assert context.table_count == 2
        assert context.h1_count == 2
        assert len(context.figures_missing_alt_text) == 1
        assert len(context.tables_missing_headers) == 1

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        context = DocumentContext(
            document_id="job-456",
            document_type=DocumentType.ACADEMIC,
            total_pages=10,
            title="Research Paper",
            headings=[HeadingInfo(level=1, text="Title", page_number=1)],
            figures=[FigureInfo(figure_number=1, page_number=2)],
            tables=[TableInfo(table_number=1, page_number=3)],
            has_single_h1=True,
        )

        json_data = context.model_dump_json()
        restored = DocumentContext.model_validate_json(json_data)

        assert restored.document_id == context.document_id
        assert restored.document_type == context.document_type
        assert restored.total_pages == context.total_pages
        assert len(restored.headings) == 1
        assert len(restored.figures) == 1
        assert len(restored.tables) == 1
        assert restored.has_single_h1 is True
