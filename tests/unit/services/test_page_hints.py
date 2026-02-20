"""Unit tests for page hint generation (running headers/footers, section context)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from src.services.pipeline_viewer import (
    PipelineViewerService,
    _is_running_footer,
    _is_running_header,
)
from src.services.pipeline_viewer_models import (
    FootnoteInfo,
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    SectionEntry,
    SectionMap,
    StepResult,
    StructureResult,
)


# ---------------------------------------------------------------------------
# Running header detection
# ---------------------------------------------------------------------------


class TestRunningHeaderDetection:
    """Test _is_running_header heuristic."""

    def test_title_fuzzy_match(self):
        """Fuzzy match against doc title should detect header."""
        assert _is_running_header("Docling Technical Report", "Docling Technical Report", 5)

    def test_title_with_page_number(self):
        """'{title} {page_num}' pattern should be detected."""
        assert _is_running_header("Docling Technical Report 7", "Docling Technical Report", 7)

    def test_heading_not_detected(self):
        """Markdown headings should never be detected as running headers."""
        assert not _is_running_header("# Docling Technical Report", "Docling Technical Report", 1)
        assert not _is_running_header("## Methods", "Methods", 2)

    def test_short_title_no_false_match(self):
        """Very short unrelated text should not fuzzy-match."""
        assert not _is_running_header("Introduction", "Docling Technical Report", 5)

    def test_no_title_no_match(self):
        """If doc_title is empty, only the pattern match can fire."""
        assert not _is_running_header("Some random text", "", 3)
        # But pattern match still works
        assert _is_running_header("Some Title 3", "", 3)


# ---------------------------------------------------------------------------
# Running footer detection
# ---------------------------------------------------------------------------


class TestRunningFooterDetection:
    """Test _is_running_footer heuristic."""

    def test_bare_page_number(self):
        """Bare digit matching page number should be detected."""
        assert _is_running_footer("7", 7)

    def test_page_number_off_by_one(self):
        """Off-by-one page numbers should still be detected."""
        assert _is_running_footer("6", 7)
        assert _is_running_footer("8", 7)

    def test_page_prefix(self):
        """'Page N' format should be detected."""
        assert _is_running_footer("Page 7", 7)
        assert _is_running_footer("page 12", 12)

    def test_n_of_m(self):
        """'N of M' format should be detected."""
        assert _is_running_footer("7 of 20", 7)

    def test_not_a_footer(self):
        """Regular text should not be detected as footer."""
        assert not _is_running_footer("This is a sentence.", 5)
        assert not _is_running_footer("100", 5)  # too far from page_num


# ---------------------------------------------------------------------------
# Page hint generation
# ---------------------------------------------------------------------------


@pytest.fixture
def hint_service():
    return PipelineViewerService()


@pytest.fixture
def basic_result():
    """3-page result with running header on page 2 and footnote on page 1."""
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=3,
        versions={"v0": "combined"},
        page_images={"1": "AAA", "2": "BBB", "3": "CCC"},
        page_markdowns={
            "v0": {
                "1": "# Docling Technical Report\n\nIntroduction paragraph.",
                "2": "Docling Technical Report 2\n\ncontinuing the discussion about methods.",
                "3": "## Results\n\nThe results show improvement.\n\n3",
            },
        },
        figures=[],
        steps=[],
        stats={},
    )


@pytest.fixture
def basic_structure():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            3: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=1, text="Docling Technical Report", page=1),
            OutlineEntry(level=2, text="Results", page=3),
        ],
        footnotes=[
            FootnoteInfo(number="1", body_text="A footnote.", source_page=1),
        ],
    )


@pytest.fixture
def basic_section_map():
    return SectionMap(sections=[
        SectionEntry(
            index=0,
            heading_text="Docling Technical Report",
            heading_level=1,
            pages=[1, 2],
            markdown="# Docling Technical Report\n\nIntro\n\ncontinuing",
            outline_position=0,
        ),
        SectionEntry(
            index=1,
            heading_text="Results",
            heading_level=2,
            pages=[3],
            markdown="## Results\n\nThe results show improvement.",
            outline_position=1,
        ),
    ])


class TestPageHintGeneration:
    """Test _generate_page_hints method."""

    def test_section_context(self, hint_service, basic_result, basic_structure, basic_section_map):
        """Pages in multi-page sections should get section context hints."""
        hints = hint_service._generate_page_hints(basic_result, basic_structure, basic_section_map)
        # Page 1 is start of a section spanning pages 1-2
        page1_hints = hints.get(1, [])
        assert any("section" in h.lower() and "Docling Technical Report" in h for h in page1_hints)

    def test_multi_page_section_middle(self, hint_service, basic_structure):
        """Middle page of a 3-page section should say 'middle'."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=3,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nStart.",
                    "2": "Middle content.",
                    "3": "End content.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[
            SectionEntry(
                index=0,
                heading_text="Title",
                heading_level=1,
                pages=[1, 2, 3],
                markdown="full",
                outline_position=0,
            ),
        ])
        hints = hint_service._generate_page_hints(result, basic_structure, section_map)
        page2_hints = hints.get(2, [])
        assert any("middle" in h.lower() for h in page2_hints)

    def test_mid_sentence_start(self, hint_service, basic_structure):
        """Page starting with lowercase non-heading → mid-sentence hint."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nEnd of sentence",
                    "2": "continuing from the previous page.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])
        hints = hint_service._generate_page_hints(result, basic_structure, section_map)
        page2_hints = hints.get(2, [])
        assert any("mid-sentence" in h.lower() and "start" in h.lower() for h in page2_hints)

    def test_mid_sentence_end(self, hint_service, basic_structure):
        """Page ending without terminal punctuation → mid-sentence hint."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nThe method involves",
                    "2": "further analysis.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])
        hints = hint_service._generate_page_hints(result, basic_structure, section_map)
        page1_hints = hints.get(1, [])
        assert any("mid-sentence" in h.lower() and "end" in h.lower() for h in page1_hints)

    def test_expected_footnotes(self, hint_service, basic_result, basic_structure, basic_section_map):
        """Footnotes on page 1 should generate a hint."""
        hints = hint_service._generate_page_hints(basic_result, basic_structure, basic_section_map)
        page1_hints = hints.get(1, [])
        assert any("footnote" in h.lower() for h in page1_hints)

    def test_running_header(self, hint_service, basic_result, basic_structure, basic_section_map):
        """Page 2 has 'Docling Technical Report 2' which is a running header."""
        hints = hint_service._generate_page_hints(basic_result, basic_structure, basic_section_map)
        page2_hints = hints.get(2, [])
        assert any("running page header" in h.lower() for h in page2_hints)

    def test_running_footer(self, hint_service, basic_result, basic_structure, basic_section_map):
        """Page 3 has bare '3' as last line → running footer."""
        hints = hint_service._generate_page_hints(basic_result, basic_structure, basic_section_map)
        page3_hints = hints.get(3, [])
        assert any("page number footer" in h.lower() for h in page3_hints)

    def test_no_hints_clean_page(self, hint_service):
        """A clean page should generate no hints."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nA complete sentence.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        # Use a structure with no footnotes
        clean_structure = StructureResult(
            page_attributes={1: PageAttributes(layout=LayoutType.SINGLE_COLUMN)},
            outline=[OutlineEntry(level=1, text="Title", page=1)],
            footnotes=[],
        )
        section_map = SectionMap(sections=[])
        hints = hint_service._generate_page_hints(result, clean_structure, section_map)
        # Page 1 should have no hints (no running header check for page 1,
        # sentence ends with period, no footnotes)
        assert 1 not in hints or len(hints[1]) == 0

    def test_page_1_no_header_check(self, hint_service, basic_structure):
        """Page 1 should never get a running header hint."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "Docling Technical Report\n\nContent.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])
        hints = hint_service._generate_page_hints(result, basic_structure, section_map)
        page1_hints = hints.get(1, [])
        assert not any("running page header" in h.lower() for h in page1_hints)
