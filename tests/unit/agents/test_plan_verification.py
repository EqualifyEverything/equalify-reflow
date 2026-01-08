"""Unit tests for Plan Verification Module.

Tests for verifying document output against the document plan:
- verify_heading_structure: Check all expected headings are present at correct levels
- verify_figure_completeness: Check all planned figures have alt-text
- verify_table_completeness: Check all planned tables are transcribed
- verify_spelling: Check text uses dictionary words correctly
"""

import pytest
from src.agents.models import (
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    FigureContext,
    OutlineEntry,
    PagePlan,
    TableContext,
)
from src.agents.plan_verification import (
    verify_figure_completeness,
    verify_heading_structure,
    verify_spelling,
    verify_table_completeness,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_plan() -> DocumentPlan:
    """Create a sample DocumentPlan for testing."""
    return DocumentPlan(
        filename="test.pdf",
        total_pages=2,
        structure=DocumentStructure(
            title="Test Document",
            document_type=DocumentType.PAPER,
            outline=[
                OutlineEntry(heading="Introduction", level=1, page_start=1, page_end=1),
                OutlineEntry(heading="Methods", level=1, page_start=2, page_end=2),
            ],
            key_terms=["Docling", "TableFormer"],
        ),
        pages={
            1: PagePlan(
                page_num=1,
                summary="Intro page",
                section_context="Introduction",
                figures=[FigureContext(figure_index=1, location="top", appears_to_be="diagram")],
                tables=[],
            ),
            2: PagePlan(
                page_num=2,
                summary="Methods page",
                section_context="Methods",
                figures=[],
                tables=[TableContext(table_index=1, location="middle", appears_to_be="data table")],
            ),
        },
        full_dictionary=["Docling", "TableFormer", "PDF"],
    )


@pytest.fixture
def plan_with_nested_headings() -> DocumentPlan:
    """Create a DocumentPlan with nested heading structure."""
    return DocumentPlan(
        filename="nested.pdf",
        total_pages=3,
        structure=DocumentStructure(
            title="Nested Document",
            document_type=DocumentType.PAPER,
            outline=[
                OutlineEntry(
                    heading="Chapter 1",
                    level=1,
                    page_start=1,
                    page_end=2,
                    children=[
                        OutlineEntry(heading="Section 1.1", level=2, page_start=1, page_end=1),
                        OutlineEntry(heading="Section 1.2", level=2, page_start=2, page_end=2),
                    ],
                ),
                OutlineEntry(heading="Chapter 2", level=1, page_start=3, page_end=3),
            ],
            key_terms=[],
        ),
        pages={},
        full_dictionary=[],
    )


@pytest.fixture
def plan_with_multiple_figures() -> DocumentPlan:
    """Create a DocumentPlan with multiple figures."""
    return DocumentPlan(
        filename="figures.pdf",
        total_pages=2,
        structure=DocumentStructure(
            title="Figures Document",
            document_type=DocumentType.PAPER,
            outline=[],
            key_terms=[],
        ),
        pages={
            1: PagePlan(
                page_num=1,
                summary="Page with figures",
                section_context="Introduction",
                figures=[
                    FigureContext(figure_index=1, location="top", appears_to_be="chart"),
                    FigureContext(figure_index=2, location="middle", appears_to_be="diagram"),
                ],
                tables=[],
            ),
            2: PagePlan(
                page_num=2,
                summary="Page with more figures",
                section_context="Results",
                figures=[
                    FigureContext(figure_index=1, location="bottom", appears_to_be="graph"),
                ],
                tables=[],
            ),
        },
        full_dictionary=[],
    )


@pytest.fixture
def plan_with_multiple_tables() -> DocumentPlan:
    """Create a DocumentPlan with multiple tables."""
    return DocumentPlan(
        filename="tables.pdf",
        total_pages=2,
        structure=DocumentStructure(
            title="Tables Document",
            document_type=DocumentType.PAPER,
            outline=[],
            key_terms=[],
        ),
        pages={
            1: PagePlan(
                page_num=1,
                summary="Page with tables",
                section_context="Data",
                figures=[],
                tables=[
                    TableContext(table_index=1, location="top", appears_to_be="results table"),
                    TableContext(table_index=2, location="bottom", appears_to_be="comparison"),
                ],
            ),
            2: PagePlan(
                page_num=2,
                summary="Another page with table",
                section_context="Analysis",
                figures=[],
                tables=[
                    TableContext(table_index=1, location="center", appears_to_be="summary"),
                ],
            ),
        },
        full_dictionary=[],
    )


# =============================================================================
# verify_heading_structure Tests
# =============================================================================


class TestVerifyHeadingStructure:
    """Tests for verify_heading_structure function."""

    def test_verify_heading_structure_all_match(self, sample_plan: DocumentPlan):
        """Test that all expected headings present returns no issues."""
        markdown = """# Introduction

This is the introduction section.

# Methods

This describes the methods used.
"""
        issues = verify_heading_structure(markdown, sample_plan)

        assert len(issues) == 0

    def test_verify_heading_structure_missing_heading(self, sample_plan: DocumentPlan):
        """Test that a missing heading is detected."""
        markdown = """# Introduction

This is the introduction section.

The Methods heading is missing from this document.
"""
        issues = verify_heading_structure(markdown, sample_plan)

        assert len(issues) >= 1
        # Should mention the missing "Methods" heading
        assert any("Methods" in issue for issue in issues)

    def test_verify_heading_structure_wrong_level(self, sample_plan: DocumentPlan):
        """Test that a heading at wrong level is detected."""
        markdown = """# Introduction

This is the introduction section.

## Methods

Methods section at wrong level (H2 instead of H1).
"""
        issues = verify_heading_structure(markdown, sample_plan)

        assert len(issues) >= 1
        # Should mention the wrong level for "Methods"
        assert any("Methods" in issue or "level" in issue.lower() for issue in issues)

    def test_verify_heading_structure_nested_all_match(self, plan_with_nested_headings: DocumentPlan):
        """Test nested heading structure with all matches."""
        markdown = """# Chapter 1

Introduction to chapter 1.

## Section 1.1

Content for section 1.1.

## Section 1.2

Content for section 1.2.

# Chapter 2

Content for chapter 2.
"""
        issues = verify_heading_structure(markdown, plan_with_nested_headings)

        assert len(issues) == 0

    def test_verify_heading_structure_nested_missing_subsection(self, plan_with_nested_headings: DocumentPlan):
        """Test nested heading structure with missing subsection."""
        markdown = """# Chapter 1

Introduction to chapter 1.

## Section 1.1

Content for section 1.1.

# Chapter 2

Content for chapter 2.
"""
        # Missing Section 1.2
        issues = verify_heading_structure(markdown, plan_with_nested_headings)

        assert len(issues) >= 1
        assert any("Section 1.2" in issue or "1.2" in issue for issue in issues)

    def test_verify_heading_structure_empty_outline(self, sample_plan: DocumentPlan):
        """Test with empty outline returns no issues."""
        sample_plan.structure.outline = []
        markdown = """Some content without headings."""

        issues = verify_heading_structure(markdown, sample_plan)

        assert len(issues) == 0


# =============================================================================
# verify_figure_completeness Tests
# =============================================================================


class TestVerifyFigureCompleteness:
    """Tests for verify_figure_completeness function."""

    def test_verify_figure_completeness_all_present(self, sample_plan: DocumentPlan):
        """Test that all planned figures have alt-text."""
        page_markdowns = {
            1: """# Introduction

Here is a diagram showing the architecture.

![Architecture diagram showing the system components](image1.png)
""",
            2: """# Methods

Method details here.
""",
        }
        issues = verify_figure_completeness(page_markdowns, sample_plan)

        assert len(issues) == 0

    def test_verify_figure_completeness_missing_alt(self, sample_plan: DocumentPlan):
        """Test that figures missing alt-text are detected."""
        page_markdowns = {
            1: """# Introduction

Here is a diagram showing the architecture.

![](image1.png)
""",
            2: """# Methods

Method details here.
""",
        }
        issues = verify_figure_completeness(page_markdowns, sample_plan)

        assert len(issues) >= 1
        # Should mention missing alt-text or figure issues
        assert any("alt" in issue.lower() or "figure" in issue.lower() for issue in issues)

    def test_verify_figure_completeness_multiple_figures_all_present(self, plan_with_multiple_figures: DocumentPlan):
        """Test multiple figures all with proper alt-text."""
        page_markdowns = {
            1: """# Introduction

![A bar chart showing quarterly results](chart1.png)

![System architecture diagram with three components](diagram1.png)
""",
            2: """# Results

![Line graph depicting growth trends](graph1.png)
""",
        }
        issues = verify_figure_completeness(page_markdowns, plan_with_multiple_figures)

        assert len(issues) == 0

    def test_verify_figure_completeness_multiple_figures_some_missing(self, plan_with_multiple_figures: DocumentPlan):
        """Test multiple figures with some missing alt-text."""
        page_markdowns = {
            1: """# Introduction

![A bar chart showing quarterly results](chart1.png)

![](diagram1.png)
""",
            2: """# Results

![](graph1.png)
""",
        }
        issues = verify_figure_completeness(page_markdowns, plan_with_multiple_figures)

        # Should detect missing alt-text issues
        assert len(issues) >= 1

    def test_verify_figure_completeness_no_figures_in_plan(self, sample_plan: DocumentPlan):
        """Test with no figures in plan returns no issues."""
        sample_plan.pages[1].figures = []
        sample_plan.pages[2].figures = []

        page_markdowns = {
            1: """# Introduction

Just text content here.
""",
            2: """# Methods

More text.
""",
        }
        issues = verify_figure_completeness(page_markdowns, sample_plan)

        assert len(issues) == 0

    def test_verify_figure_completeness_decorative_image(self, sample_plan: DocumentPlan):
        """Test that decorative images (empty alt) marked as such are accepted."""
        # Mark the figure as decorative
        sample_plan.pages[1].figures[0].is_decorative = True

        page_markdowns = {
            1: """# Introduction

![](decorative.png)
""",
            2: """# Methods

Method details.
""",
        }
        issues = verify_figure_completeness(page_markdowns, sample_plan)

        # Decorative images with empty alt are acceptable
        assert len(issues) == 0


# =============================================================================
# verify_table_completeness Tests
# =============================================================================


class TestVerifyTableCompleteness:
    """Tests for verify_table_completeness function."""

    def test_verify_table_completeness_all_transcribed(self, sample_plan: DocumentPlan):
        """Test that all planned tables are in markdown."""
        page_markdowns = {
            1: """# Introduction

Content here.
""",
            2: """# Methods

| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data 1   | Data 2   | Data 3   |
| Data 4   | Data 5   | Data 6   |
""",
        }
        issues = verify_table_completeness(page_markdowns, sample_plan)

        assert len(issues) == 0

    def test_verify_table_completeness_missing(self, sample_plan: DocumentPlan):
        """Test that missing tables are detected."""
        page_markdowns = {
            1: """# Introduction

Content here.
""",
            2: """# Methods

No tables here, just text about methods.
""",
        }
        issues = verify_table_completeness(page_markdowns, sample_plan)

        assert len(issues) >= 1
        # Should mention missing table
        assert any("table" in issue.lower() for issue in issues)

    def test_verify_table_completeness_multiple_tables_all_present(self, plan_with_multiple_tables: DocumentPlan):
        """Test multiple tables all transcribed."""
        page_markdowns = {
            1: """# Data

| Results | Value |
|---------|-------|
| Test 1  | 95    |

| Option A | Option B |
|----------|----------|
| Fast     | Slow     |
""",
            2: """# Analysis

| Summary | Count |
|---------|-------|
| Total   | 100   |
""",
        }
        issues = verify_table_completeness(page_markdowns, plan_with_multiple_tables)

        assert len(issues) == 0

    def test_verify_table_completeness_multiple_tables_some_missing(self, plan_with_multiple_tables: DocumentPlan):
        """Test multiple tables with some not transcribed."""
        page_markdowns = {
            1: """# Data

| Results | Value |
|---------|-------|
| Test 1  | 95    |
""",
            2: """# Analysis

No summary table here.
""",
        }
        # Missing 2 tables (one from page 1, one from page 2)
        issues = verify_table_completeness(page_markdowns, plan_with_multiple_tables)

        assert len(issues) >= 1

    def test_verify_table_completeness_no_tables_in_plan(self, sample_plan: DocumentPlan):
        """Test with no tables in plan returns no issues."""
        sample_plan.pages[2].tables = []

        page_markdowns = {
            1: """# Introduction

Just text.
""",
            2: """# Methods

More text without tables.
""",
        }
        issues = verify_table_completeness(page_markdowns, sample_plan)

        assert len(issues) == 0


# =============================================================================
# verify_spelling Tests
# =============================================================================


class TestVerifySpelling:
    """Tests for verify_spelling function."""

    def test_verify_spelling_no_issues(self, sample_plan: DocumentPlan):
        """Test that text uses dictionary words correctly."""
        text = """The Docling framework uses TableFormer for PDF table extraction.
This is a sample document with correct spelling throughout.
"""
        issues = verify_spelling(text, sample_plan)

        assert len(issues) == 0

    def test_verify_spelling_with_issues(self, sample_plan: DocumentPlan):
        """Test that text has misspelled words detected."""
        text = """The Docling framewrok uses TableFormer for PDF extraction.
This documnet has sevral typos in it.
"""
        issues = verify_spelling(text, sample_plan)

        assert len(issues) >= 1
        # Should detect misspelled words
        issue_text = " ".join(issues).lower()
        assert any(word in issue_text for word in ["framewrok", "documnet", "sevral", "spell", "misspell"])

    def test_verify_spelling_domain_terms_accepted(self, sample_plan: DocumentPlan):
        """Test that domain terms from dictionary are not flagged."""
        sample_plan.full_dictionary = ["Docling", "TableFormer", "PDF", "PyMuPDF", "OCR"]

        text = """Docling uses TableFormer and PyMuPDF for OCR and PDF processing."""

        issues = verify_spelling(text, sample_plan)

        assert len(issues) == 0

    def test_verify_spelling_empty_text(self, sample_plan: DocumentPlan):
        """Test that empty text returns no issues."""
        issues = verify_spelling("", sample_plan)

        assert len(issues) == 0

    def test_verify_spelling_empty_dictionary(self, sample_plan: DocumentPlan):
        """Test spelling check with empty dictionary."""
        sample_plan.full_dictionary = []

        text = """This document discusses the architecture of the system."""

        issues = verify_spelling(text, sample_plan)

        # Standard English words should pass
        assert len(issues) == 0

    def test_verify_spelling_mixed_case_dictionary(self, sample_plan: DocumentPlan):
        """Test that dictionary matching is case-insensitive."""
        sample_plan.full_dictionary = ["Docling", "TableFormer"]

        # Use different cases
        text = """DOCLING uses tableformer for table extraction."""

        issues = verify_spelling(text, sample_plan)

        # Should accept regardless of case
        assert len(issues) == 0

    def test_verify_spelling_short_words_ignored(self, sample_plan: DocumentPlan):
        """Test that very short words are not flagged."""
        text = """A is an AI. PDF."""

        issues = verify_spelling(text, sample_plan)

        # Short words should not cause failures
        assert len(issues) == 0
