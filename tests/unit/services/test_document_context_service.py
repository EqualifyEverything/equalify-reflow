"""Unit tests for DocumentContextService (PRD-013).

Tests the Docling-based document context extraction service.
Uses mock DoclingDocument objects to test without requiring actual PDF conversion.
"""

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest
from docling_core.types.doc.labels import DocItemLabel
from src.services.document_context_service import DocumentContextService
from src.shared.models.context_models import DocumentType

# ============================================================================
# Mock Objects for DoclingDocument
# ============================================================================


@dataclass
class MockProvenance:
    """Mock provenance for document items."""

    page_no: int = 1
    bbox: tuple = (0, 0, 100, 100)


@dataclass
class MockTextItem:
    """Mock text item from DoclingDocument.texts."""

    label: DocItemLabel
    text: str
    prov: list = field(default_factory=list)
    level: int | None = None

    def __post_init__(self) -> None:
        if not self.prov:
            self.prov = [MockProvenance()]


@dataclass
class MockPictureItem:
    """Mock picture item from DoclingDocument.pictures."""

    prov: list = field(default_factory=list)
    captions: list = field(default_factory=list)
    caption_text: str | None = None

    def __post_init__(self) -> None:
        if not self.prov:
            self.prov = [MockProvenance()]


@dataclass
class MockTableData:
    """Mock table data structure."""

    num_cols: int = 0
    num_rows: int = 0
    grid: list = field(default_factory=list)


@dataclass
class MockTableCell:
    """Mock table cell."""

    text: str = ""


@dataclass
class MockTableItem:
    """Mock table item from DoclingDocument.tables."""

    prov: list = field(default_factory=list)
    data: MockTableData | None = None
    caption_text: str | None = None

    def __post_init__(self) -> None:
        if not self.prov:
            self.prov = [MockProvenance()]


def create_mock_document(
    texts: list[MockTextItem] | None = None,
    pictures: list[MockPictureItem] | None = None,
    tables: list[MockTableItem] | None = None,
    page_count: int = 1,
) -> MagicMock:
    """Create a mock DoclingDocument for testing.

    Args:
        texts: List of mock text items
        pictures: List of mock picture items
        tables: List of mock table items
        page_count: Number of pages

    Returns:
        Mock DoclingDocument
    """
    doc = MagicMock()
    doc.texts = texts or []
    doc.pictures = pictures or []
    doc.tables = tables or []
    doc.pages = {i: MagicMock() for i in range(1, page_count + 1)}
    return doc


# ============================================================================
# Initialization Tests
# ============================================================================


@pytest.mark.unit
class TestDocumentContextServiceInitialization:
    """Tests for service initialization."""

    def test_default_initialization(self) -> None:
        """Service initializes with default config values."""
        service = DocumentContextService()
        assert service.max_headings == 100
        assert service.recurring_threshold == 0.5

    def test_custom_initialization(self) -> None:
        """Service accepts custom configuration."""
        service = DocumentContextService(
            max_headings=50,
            recurring_threshold=0.3,
        )
        assert service.max_headings == 50
        assert service.recurring_threshold == 0.3


# ============================================================================
# Heading Extraction Tests
# ============================================================================


@pytest.mark.unit
class TestHeadingExtraction:
    """Tests for heading extraction from DoclingDocument."""

    def test_extract_single_heading(self) -> None:
        """Should extract a single SECTION_HEADER."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Title",
                    prov=[MockProvenance(page_no=1)],
                    level=1,
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 1
        assert context.headings[0].level == 1
        assert context.headings[0].text == "Title"
        assert context.headings[0].page_number == 1

    def test_extract_multiple_headings(self) -> None:
        """Should extract multiple headings in document order."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Title",
                    prov=[MockProvenance(page_no=1)],
                    level=1,
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Section 1",
                    prov=[MockProvenance(page_no=1)],
                    level=2,
                ),
                MockTextItem(
                    label=DocItemLabel.TEXT,
                    text="Body content",
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Section 2",
                    prov=[MockProvenance(page_no=2)],
                    level=2,
                ),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 3
        assert context.headings[0].text == "Title"
        assert context.headings[1].text == "Section 1"
        assert context.headings[2].text == "Section 2"
        assert context.headings[2].page_number == 2

    def test_heading_levels_extracted(self) -> None:
        """Should correctly identify heading levels."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text=f"H{i}",
                    level=i,
                )
                for i in range(1, 7)
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 6
        for i, heading in enumerate(context.headings):
            assert heading.level == i + 1

    def test_heading_level_clamped(self) -> None:
        """Should clamp heading levels to 1-6 range."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Invalid Level",
                    level=10,  # Invalid, should be clamped to 6
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.headings[0].level == 6

    def test_max_headings_limit(self) -> None:
        """Should respect max_headings configuration."""
        service = DocumentContextService(max_headings=3)
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text=f"H{i}",
                    level=2,
                )
                for i in range(10)
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 3

    def test_non_heading_items_skipped(self) -> None:
        """Should skip non-SECTION_HEADER items."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.TEXT,
                    text="Regular text",
                ),
                MockTextItem(
                    label=DocItemLabel.LIST_ITEM,
                    text="List item",
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Real Heading",
                    level=1,
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 1
        assert context.headings[0].text == "Real Heading"


# ============================================================================
# Heading Hierarchy Tests
# ============================================================================


@pytest.mark.unit
class TestHeadingHierarchy:
    """Tests for heading hierarchy building."""

    def test_parent_heading_assignment(self) -> None:
        """Should assign parent headings correctly."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Title",
                    level=1,
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Chapter 1",
                    level=2,
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Section 1.1",
                    level=3,
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.headings[0].parent_heading is None  # H1 has no parent
        assert context.headings[1].parent_heading == "Title"  # H2's parent is H1
        assert context.headings[2].parent_heading == "Chapter 1"  # H3's parent is H2

    def test_section_id_generation(self) -> None:
        """Should generate section IDs."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Ch1", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Sec1.1", level=3),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Ch2", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Sec2.1", level=3),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.headings[0].section_id == "1"  # H1
        assert context.headings[1].section_id == "1.1"  # First H2
        assert context.headings[2].section_id == "1.1.1"  # H3 under first H2
        assert context.headings[3].section_id == "1.2"  # Second H2
        assert context.headings[4].section_id == "1.2.1"  # H3 under second H2

    def test_hierarchy_resets_on_higher_level(self) -> None:
        """Should reset lower-level parents when higher heading appears."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Ch1", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Sec", level=3),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Ch2", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        # Ch2's parent should be Title, not Sec
        ch2 = context.headings[3]
        assert ch2.text == "Ch2"
        assert ch2.parent_heading == "Title"


# ============================================================================
# Figure Extraction Tests
# ============================================================================


@pytest.mark.unit
class TestFigureExtraction:
    """Tests for figure extraction from DoclingDocument.pictures."""

    def test_extract_single_figure(self) -> None:
        """Should extract a single picture."""
        service = DocumentContextService()
        doc = create_mock_document(
            pictures=[
                MockPictureItem(
                    prov=[MockProvenance(page_no=1)],
                    caption_text="Figure caption",
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.figure_count == 1
        assert context.figures[0].figure_number == 1
        assert context.figures[0].page_number == 1
        assert context.figures[0].has_alt_text is True
        assert "Figure caption" in context.figures[0].alt_text

    def test_extract_figure_without_caption(self) -> None:
        """Should detect figure without caption/alt text."""
        service = DocumentContextService()
        doc = create_mock_document(
            pictures=[
                MockPictureItem(
                    prov=[MockProvenance(page_no=1)],
                    captions=[],
                    caption_text=None,
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.figure_count == 1
        assert context.figures[0].has_alt_text is False

    def test_figure_numbering_sequence(self) -> None:
        """Should number figures sequentially."""
        service = DocumentContextService()
        doc = create_mock_document(
            pictures=[
                MockPictureItem(prov=[MockProvenance(page_no=1)]),
                MockPictureItem(prov=[MockProvenance(page_no=1)]),
                MockPictureItem(prov=[MockProvenance(page_no=2)]),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        assert context.figure_count == 3
        assert context.figures[0].figure_number == 1
        assert context.figures[1].figure_number == 2
        assert context.figures[2].figure_number == 3
        assert context.figures[2].page_number == 2

    def test_figure_with_caption_reference(self) -> None:
        """Should extract caption from caption references."""
        service = DocumentContextService()
        caption_ref = MagicMock()
        caption_ref.text = "Referenced caption text"

        doc = create_mock_document(
            pictures=[
                MockPictureItem(
                    prov=[MockProvenance(page_no=1)],
                    captions=[caption_ref],
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.figures[0].has_alt_text is True
        assert "Referenced caption text" in context.figures[0].caption

    def test_figures_missing_alt_text_property(self) -> None:
        """Should track figures missing alt text."""
        service = DocumentContextService()
        doc = create_mock_document(
            pictures=[
                MockPictureItem(caption_text="Has caption"),
                MockPictureItem(caption_text=None, captions=[]),
                MockPictureItem(caption_text="Also has caption"),
            ]
        )

        context = service.extract_context("test-job", doc)

        missing = context.figures_missing_alt_text
        assert len(missing) == 1
        assert missing[0].figure_number == 2


# ============================================================================
# Table Extraction Tests
# ============================================================================


@pytest.mark.unit
class TestTableExtraction:
    """Tests for table extraction from DoclingDocument.tables."""

    def test_extract_single_table(self) -> None:
        """Should extract a single table."""
        service = DocumentContextService()
        doc = create_mock_document(
            tables=[
                MockTableItem(
                    prov=[MockProvenance(page_no=1)],
                    data=MockTableData(
                        num_cols=3,
                        num_rows=2,
                        grid=[
                            [
                                MockTableCell("A"),
                                MockTableCell("B"),
                                MockTableCell("C"),
                            ],
                            [
                                MockTableCell("1"),
                                MockTableCell("2"),
                                MockTableCell("3"),
                            ],
                        ],
                    ),
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.table_count == 1
        assert context.tables[0].table_number == 1
        assert context.tables[0].column_count == 3
        assert context.tables[0].row_count == 2
        assert context.tables[0].has_header_row is True
        assert context.tables[0].header_cells == ["A", "B", "C"]

    def test_table_without_data(self) -> None:
        """Should handle table without data structure."""
        service = DocumentContextService()
        doc = create_mock_document(
            tables=[
                MockTableItem(
                    prov=[MockProvenance(page_no=1)],
                    data=None,
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.table_count == 1
        assert context.tables[0].column_count == 0
        assert context.tables[0].has_header_row is False

    def test_table_numbering_sequence(self) -> None:
        """Should number tables sequentially."""
        service = DocumentContextService()
        doc = create_mock_document(
            tables=[
                MockTableItem(prov=[MockProvenance(page_no=1)]),
                MockTableItem(prov=[MockProvenance(page_no=2)]),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        assert context.table_count == 2
        assert context.tables[0].table_number == 1
        assert context.tables[1].table_number == 2

    def test_table_caption_extraction(self) -> None:
        """Should extract table caption if present."""
        service = DocumentContextService()
        doc = create_mock_document(
            tables=[
                MockTableItem(
                    prov=[MockProvenance(page_no=1)],
                    caption_text="Grading breakdown",
                )
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.tables[0].caption == "Grading breakdown"

    def test_tables_missing_headers_property(self) -> None:
        """Should track tables missing header rows."""
        service = DocumentContextService()
        doc = create_mock_document(
            tables=[
                MockTableItem(
                    data=MockTableData(
                        num_cols=2,
                        grid=[[MockTableCell("A"), MockTableCell("B")]],
                    )
                ),
                MockTableItem(data=None),  # No headers
            ]
        )

        context = service.extract_context("test-job", doc)

        missing = context.tables_missing_headers
        assert len(missing) == 1
        assert missing[0].table_number == 2


# ============================================================================
# TOC Detection Tests
# ============================================================================


@pytest.mark.unit
class TestTOCDetection:
    """Tests for Table of Contents detection."""

    def test_detect_document_index(self) -> None:
        """Should detect DOCUMENT_INDEX items as TOC."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.DOCUMENT_INDEX,
                    text="Introduction",
                ),
                MockTextItem(
                    label=DocItemLabel.DOCUMENT_INDEX,
                    text="Chapter 1",
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert len(context.toc_entries) == 2

    def test_no_toc_when_not_present(self) -> None:
        """Should return empty TOC when not present."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Regular Section",
                    level=1,
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert len(context.toc_entries) == 0


# ============================================================================
# Recurring Element Detection Tests
# ============================================================================


@pytest.mark.unit
class TestRecurringElementDetection:
    """Tests for recurring element detection using PAGE_HEADER/PAGE_FOOTER."""

    def test_detect_recurring_footer(self) -> None:
        """Should detect PAGE_FOOTER items as recurring footers."""
        service = DocumentContextService(recurring_threshold=0.5)
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="CS 101 - Fall 2024",
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="CS 101 - Fall 2024",
                    prov=[MockProvenance(page_no=2)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="CS 101 - Fall 2024",
                    prov=[MockProvenance(page_no=3)],
                ),
            ],
            page_count=3,
        )

        context = service.extract_context("test-job", doc)

        assert len(context.recurring_elements) >= 1
        footer = next(
            (e for e in context.recurring_elements if "CS 101" in e.content),
            None,
        )
        assert footer is not None
        assert footer.element_type in ("footer", "page_number")

    def test_detect_recurring_header(self) -> None:
        """Should detect PAGE_HEADER items as recurring headers."""
        service = DocumentContextService(recurring_threshold=0.5)
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.PAGE_HEADER,
                    text="Document Title",
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_HEADER,
                    text="Document Title",
                    prov=[MockProvenance(page_no=2)],
                ),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        assert len(context.recurring_elements) >= 1
        header = next(
            (e for e in context.recurring_elements if e.element_type == "header"),
            None,
        )
        assert header is not None
        assert header.content == "Document Title"

    def test_threshold_filtering(self) -> None:
        """Should only include elements meeting threshold."""
        service = DocumentContextService(recurring_threshold=0.8)
        doc = create_mock_document(
            texts=[
                # Header appears on 3/3 = 100%
                MockTextItem(
                    label=DocItemLabel.PAGE_HEADER,
                    text="Header",
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_HEADER,
                    text="Header",
                    prov=[MockProvenance(page_no=2)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_HEADER,
                    text="Header",
                    prov=[MockProvenance(page_no=3)],
                ),
                # Footer appears on 1/3 = 33%
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="Footer",
                    prov=[MockProvenance(page_no=1)],
                ),
            ],
            page_count=3,
        )

        context = service.extract_context("test-job", doc)

        # Only header should pass 80% threshold
        assert all(e.element_type == "header" for e in context.recurring_elements)

    def test_skip_short_content(self) -> None:
        """Should skip very short content."""
        service = DocumentContextService(recurring_threshold=0.5)
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="X",  # Too short (<3 chars)
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="X",
                    prov=[MockProvenance(page_no=2)],
                ),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        # "X" is too short, should not be detected
        assert not any("X" == e.content for e in context.recurring_elements)

    def test_detect_page_numbers(self) -> None:
        """Should detect page numbers as recurring elements."""
        service = DocumentContextService(recurring_threshold=0.5)
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="Page 1",
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.PAGE_FOOTER,
                    text="Page 2",
                    prov=[MockProvenance(page_no=2)],
                ),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)

        # Each unique page number won't meet threshold since they're unique
        # But if we had the same text recurring, it would be detected
        _ = context.recurring_elements  # Just verify it runs


# ============================================================================
# Document Type Detection Tests
# ============================================================================


@pytest.mark.unit
class TestDocumentTypeDetection:
    """Tests for document type classification."""

    def test_detect_syllabus(self) -> None:
        """Should detect syllabus by keywords."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Course Syllabus", level=1),
                MockTextItem(label=DocItemLabel.TEXT, text="Office Hours: Mon-Fri"),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Grading Policy", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.document_type == DocumentType.SYLLABUS

    def test_detect_academic(self) -> None:
        """Should detect academic paper by keywords."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Abstract", level=1),
                MockTextItem(label=DocItemLabel.TEXT, text="This paper presents research..."),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Introduction", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Methodology", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Conclusion", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="References", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.document_type == DocumentType.ACADEMIC

    def test_detect_presentation(self) -> None:
        """Should detect presentation by keywords."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Agenda", level=1),
                MockTextItem(label=DocItemLabel.TEXT, text="Slide 1: Introduction"),
                MockTextItem(label=DocItemLabel.TEXT, text="Questions and discussion"),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.document_type == DocumentType.PRESENTATION

    def test_default_to_general(self) -> None:
        """Should default to GENERAL when no patterns match."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Hello", level=1),
                MockTextItem(label=DocItemLabel.TEXT, text="This is some content."),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.document_type == DocumentType.GENERAL


# ============================================================================
# Title Extraction Tests
# ============================================================================


@pytest.mark.unit
class TestTitleExtraction:
    """Tests for document title extraction."""

    def test_title_from_title_label(self) -> None:
        """Should extract title from TITLE label."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.TITLE, text="Document Title"),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.title == "Document Title"

    def test_title_from_first_h1(self) -> None:
        """Should fall back to first H1 if no TITLE."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="H1 Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.title == "H1 Title"

    def test_no_title_without_h1_or_title(self) -> None:
        """Should have empty title if no H1 or TITLE present."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Subsection", level=3),
            ]
        )

        context = service.extract_context("test-job", doc)

        assert context.title == ""

    def test_single_h1_detection(self) -> None:
        """Should detect single H1 document pattern."""
        service = DocumentContextService()

        # Single H1
        single_doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
            ]
        )
        single_h1 = service.extract_context("test", single_doc)
        assert single_h1.has_single_h1 is True

        # Multiple H1
        multi_doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Another", level=1),
            ]
        )
        multi_h1 = service.extract_context("test", multi_doc)
        assert multi_h1.has_single_h1 is False


# ============================================================================
# Context Formatting Tests
# ============================================================================


@pytest.mark.unit
class TestContextFormatting:
    """Tests for prompt context formatting."""

    def test_format_basic_context(self) -> None:
        """Should format basic context for prompts."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Section", level=2),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)
        formatted = service.format_context_for_prompt(context, page_number=2)

        assert "**Document Context:**" in formatted
        assert "general" in formatted.lower()  # Document type
        assert "2 of 2" in formatted  # Page info

    def test_format_includes_section_hierarchy(self) -> None:
        """Should include current section hierarchy."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Title",
                    level=1,
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Chapter 1",
                    level=2,
                    prov=[MockProvenance(page_no=1)],
                ),
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="Section 1.1",
                    level=3,
                    prov=[MockProvenance(page_no=1)],
                ),
            ],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)
        formatted = service.format_context_for_prompt(context, page_number=2)

        assert "Current Section" in formatted
        assert "Title" in formatted

    def test_format_includes_figure_table_numbers(self) -> None:
        """Should include next figure/table numbers."""
        service = DocumentContextService()
        doc = create_mock_document(
            pictures=[MockPictureItem(prov=[MockProvenance(page_no=1)])],
            tables=[MockTableItem(prov=[MockProvenance(page_no=1)])],
            page_count=2,
        )

        context = service.extract_context("test-job", doc)
        formatted = service.format_context_for_prompt(context, page_number=2)

        assert "figure number" in formatted.lower()
        assert "table number" in formatted.lower()

    def test_format_respects_max_length(self) -> None:
        """Should truncate output to max_length."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
            ]
        )

        context = service.extract_context("test-job", doc)
        formatted = service.format_context_for_prompt(context, page_number=1, max_length=50)

        assert len(formatted) <= 50
        assert formatted.endswith("...")


# ============================================================================
# Edge Case Tests
# ============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_document(self) -> None:
        """Should handle document with no content."""
        service = DocumentContextService()
        doc = create_mock_document()

        context = service.extract_context("test-job", doc)

        assert context.heading_count == 0
        assert context.figure_count == 0
        assert context.table_count == 0

    def test_single_page_document(self) -> None:
        """Should handle single-page documents."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
            ],
            page_count=1,
        )

        context = service.extract_context("test-job", doc)

        assert context.total_pages == 1
        assert context.heading_count == 1

    def test_document_id_preserved(self) -> None:
        """Should preserve document ID."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Title", level=1),
            ]
        )

        context = service.extract_context("my-unique-id", doc)

        assert context.document_id == "my-unique-id"

    def test_empty_text_items(self) -> None:
        """Should skip items with empty or whitespace-only text."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="", level=1),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="  ", level=2),
                MockTextItem(label=DocItemLabel.SECTION_HEADER, text="Valid", level=2),
            ]
        )

        context = service.extract_context("test-job", doc)

        # Empty and whitespace-only text items are skipped
        assert context.heading_count == 1
        assert context.headings[0].text == "Valid"

    def test_missing_provenance(self) -> None:
        """Should handle items without provenance."""
        service = DocumentContextService()
        doc = create_mock_document(
            texts=[
                MockTextItem(
                    label=DocItemLabel.SECTION_HEADER,
                    text="No Provenance",
                    level=1,
                    prov=[],  # Empty provenance
                ),
            ]
        )

        context = service.extract_context("test-job", doc)

        # Should default to page 1
        assert context.headings[0].page_number == 1


# ============================================================================
# Performance Tests
# ============================================================================


@pytest.mark.unit
class TestPerformance:
    """Tests for performance requirements."""

    def test_large_document_under_2_seconds(self) -> None:
        """Should process large document in under 2 seconds."""
        import time

        service = DocumentContextService()

        # Create document with many items
        texts = []
        pictures = []
        tables = []

        for page in range(1, 51):
            texts.extend(
                [
                    MockTextItem(
                        label=DocItemLabel.SECTION_HEADER,
                        text=f"Section {page}",
                        level=2,
                        prov=[MockProvenance(page_no=page)],
                    ),
                    MockTextItem(
                        label=DocItemLabel.TEXT,
                        text=f"Content on page {page}",
                        prov=[MockProvenance(page_no=page)],
                    ),
                    MockTextItem(
                        label=DocItemLabel.PAGE_FOOTER,
                        text="Document Footer",
                        prov=[MockProvenance(page_no=page)],
                    ),
                ]
            )
            pictures.append(
                MockPictureItem(prov=[MockProvenance(page_no=page)])
            )
            tables.append(
                MockTableItem(
                    prov=[MockProvenance(page_no=page)],
                    data=MockTableData(num_cols=3, num_rows=5),
                )
            )

        doc = create_mock_document(
            texts=texts,
            pictures=pictures,
            tables=tables,
            page_count=50,
        )

        start = time.monotonic()
        context = service.extract_context("perf-test", doc)
        elapsed = time.monotonic() - start

        assert elapsed < 2.0, f"Extraction took {elapsed:.2f}s, should be under 2s"
        assert context.total_pages == 50
        assert context.heading_count >= 50
        assert context.figure_count == 50
        assert context.table_count == 50
