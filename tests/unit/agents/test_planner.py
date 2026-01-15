"""Unit tests for Planner Module helper functions and non-LLM logic.

Tests for Stage 1: Quick Scan functionality:
- _detect_page_type: Page type detection heuristics
- quick_scan_page: Structure extraction from markdown

These tests cover pure logic that doesn't require LLM calls.
"""

import pytest

from src.agents.models import PageSkeleton, PageType
from src.agents.planner import _detect_page_type, quick_scan_page

pytestmark = pytest.mark.unit


# =============================================================================
# Sample Markdown Content Fixtures
# =============================================================================


@pytest.fixture
def toc_markdown() -> str:
    """Table of contents page content."""
    return """# Table of Contents

Introduction .................. 1
Methods ....................... 5
Results ....................... 12
Discussion .................... 18
Conclusion .................... 24
References .................... 28
"""


@pytest.fixture
def toc_markdown_with_dots() -> str:
    """Alternative TOC format with dot leaders."""
    return """# Contents

Chapter 1. Overview . . . . . . . . . . . . . . . 1
Chapter 2. Background . . . . . . . . . . . . . . 5
Chapter 3. Methodology . . . . . . . . . . . . . 10
Chapter 4. Results . . . . . . . . . . . . . . . 15
"""


@pytest.fixture
def title_page_markdown() -> str:
    """Title page content."""
    return """# Course Title

Professor Smith
Spring 2024
"""


@pytest.fixture
def title_page_verbose() -> str:
    """Title page with university information."""
    return """# Introduction to Machine Learning

Dr. Jane Smith
Department of Computer Science
University of Illinois Chicago

Fall 2024
"""


@pytest.fixture
def content_markdown() -> str:
    """Standard content page."""
    return """## Methods

We conducted a study using the following approach.

![](image1.png)

| Column 1 | Column 2 |
|----------|----------|
| Data     | Data     |

Reference [1] suggests that our methodology is sound.
"""


@pytest.fixture
def content_markdown_rich() -> str:
    """Content page with multiple elements."""
    return """## 2.1 Data Collection

The data was collected from multiple sources.

### 2.1.1 Primary Sources

We gathered data from academic databases.

![](figure1.png)

<!-- image:1 -->

| Source | Records | Date |
|--------|---------|------|
| DB1    | 1000    | 2023 |
| DB2    | 500     | 2024 |

<!-- table:1 -->

### 2.1.2 Secondary Sources

Additional data was obtained from:

- Survey responses
- Interview transcripts

References [1], [2], and [3] provide further details.
"""


@pytest.fixture
def references_markdown() -> str:
    """References page content."""
    return """# References

[1] Smith, J. (2023). Machine Learning Basics. Journal of AI, 15(2), 100-120.

[2] Jones, A. & Brown, B. (2024). Deep Learning Methods. Neural Networks, 42(1), 50-75.

[3] Williams, C. (2023). Data Science Fundamentals. Data Journal, 8(4), 200-225.
"""


@pytest.fixture
def bibliography_markdown() -> str:
    """Alternative references format with bibliography heading."""
    return """# Bibliography

[1] Anderson, M. (2022). Statistical Analysis Methods. Statistics Today.

[2] Chen, L. (2023). Computer Vision Advances. CV Review, 12(3), 45-60.

[3] Davidson, R. (2024). Natural Language Processing. NLP Journal.
"""


@pytest.fixture
def appendix_markdown() -> str:
    """Appendix page content."""
    return """# Appendix A: Supplementary Materials

## Additional Data Tables

| Variable | Mean | SD   |
|----------|------|------|
| Age      | 35.2 | 12.1 |
| Score    | 78.5 | 15.3 |

## Survey Questions

1. How satisfied are you with the product?
2. Would you recommend it to others?
"""


@pytest.fixture
def appendix_inline_heading() -> str:
    """Page with appendix as inline heading."""
    return """The main content ends here.

## Appendix: Technical Details

Implementation notes and configuration details follow.
"""


@pytest.fixture
def blank_page_markdown() -> str:
    """Blank page with minimal content."""
    return """

"""


@pytest.fixture
def near_blank_markdown() -> str:
    """Page with very little content."""
    return """

---

"""


# =============================================================================
# _detect_page_type Tests
# =============================================================================


class TestDetectPageType:
    """Tests for _detect_page_type function."""

    def test_detect_title_page_first_page_short(self, title_page_markdown: str):
        """First page with limited content is detected as TITLE."""
        page_type = _detect_page_type(title_page_markdown, page_num=1, total_pages=10)

        assert page_type == PageType.TITLE

    def test_detect_title_page_first_page_few_headings(self, title_page_verbose: str):
        """First page with few headings is detected as TITLE."""
        page_type = _detect_page_type(title_page_verbose, page_num=1, total_pages=10)

        assert page_type == PageType.TITLE

    def test_detect_title_page_not_first_page(self, title_page_markdown: str):
        """Short content on non-first page is not detected as TITLE."""
        page_type = _detect_page_type(title_page_markdown, page_num=2, total_pages=10)

        # Not first page, so shouldn't be TITLE
        assert page_type != PageType.TITLE

    def test_detect_toc_with_table_of_contents_text(self, toc_markdown: str):
        """Page with 'table of contents' text is detected as TOC."""
        page_type = _detect_page_type(toc_markdown, page_num=2, total_pages=10)

        assert page_type == PageType.TOC

    def test_detect_toc_with_contents_in_first_200_chars(self):
        """Page with 'contents' in first 200 characters is detected as TOC."""
        markdown = """Contents

Chapter 1 ............... 1
Chapter 2 ............... 5
"""
        page_type = _detect_page_type(markdown, page_num=2, total_pages=10)

        assert page_type == PageType.TOC

    def test_detect_toc_with_many_dots(self, toc_markdown_with_dots: str):
        """Page with many '...' patterns is detected as TOC."""
        page_type = _detect_page_type(toc_markdown_with_dots, page_num=2, total_pages=10)

        assert page_type == PageType.TOC

    def test_detect_toc_with_spaced_dots(self):
        """Page with many '. . .' patterns is detected as TOC."""
        markdown = """Index

Item 1 . . . . . . . . . . . 1
Item 2 . . . . . . . . . . . 2
Item 3 . . . . . . . . . . . 3
Item 4 . . . . . . . . . . . 4
Item 5 . . . . . . . . . . . 5
Item 6 . . . . . . . . . . . 6
"""
        page_type = _detect_page_type(markdown, page_num=2, total_pages=10)

        assert page_type == PageType.TOC

    def test_detect_references_with_keyword_and_citations(self, references_markdown: str):
        """Last pages with 'reference' keyword and citation patterns are REFERENCES."""
        # Page 8 out of 10 is > 70% through the document
        page_type = _detect_page_type(references_markdown, page_num=8, total_pages=10)

        assert page_type == PageType.REFERENCES

    def test_detect_references_bibliography_keyword(self, bibliography_markdown: str):
        """Page with 'bibliography' keyword and citations is REFERENCES."""
        page_type = _detect_page_type(bibliography_markdown, page_num=9, total_pages=10)

        assert page_type == PageType.REFERENCES

    def test_references_not_detected_early_in_document(self, references_markdown: str):
        """References content early in document is not detected as REFERENCES."""
        # Page 3 out of 10 is only 30% through
        page_type = _detect_page_type(references_markdown, page_num=3, total_pages=10)

        assert page_type != PageType.REFERENCES

    def test_references_requires_citation_pattern(self):
        """References keyword without [1] patterns isn't REFERENCES."""
        markdown = """# References

Just some text about references without actual citation brackets.
"""
        page_type = _detect_page_type(markdown, page_num=9, total_pages=10)

        # No [1], [2] patterns, so not REFERENCES
        assert page_type != PageType.REFERENCES

    def test_detect_appendix_with_heading(self, appendix_markdown: str):
        """Page with '# Appendix' heading is detected as APPENDIX."""
        page_type = _detect_page_type(appendix_markdown, page_num=5, total_pages=10)

        assert page_type == PageType.APPENDIX

    def test_detect_appendix_inline(self, appendix_inline_heading: str):
        """Page with '## Appendix' heading is detected as APPENDIX."""
        page_type = _detect_page_type(appendix_inline_heading, page_num=5, total_pages=10)

        assert page_type == PageType.APPENDIX

    def test_appendix_case_insensitive(self):
        """Appendix detection is case-insensitive."""
        markdown = """# APPENDIX B

Additional content.
"""
        page_type = _detect_page_type(markdown, page_num=5, total_pages=10)

        assert page_type == PageType.APPENDIX

    def test_detect_blank_page(self, blank_page_markdown: str):
        """Page with < 50 characters stripped is BLANK."""
        page_type = _detect_page_type(blank_page_markdown, page_num=5, total_pages=10)

        assert page_type == PageType.BLANK

    def test_detect_near_blank_page(self, near_blank_markdown: str):
        """Page with minimal content (just ---) is BLANK."""
        page_type = _detect_page_type(near_blank_markdown, page_num=5, total_pages=10)

        assert page_type == PageType.BLANK

    def test_detect_content_default(self, content_markdown: str):
        """Normal content page defaults to CONTENT."""
        page_type = _detect_page_type(content_markdown, page_num=3, total_pages=10)

        assert page_type == PageType.CONTENT

    def test_detect_content_rich_page(self, content_markdown_rich: str):
        """Rich content page with multiple elements is CONTENT."""
        page_type = _detect_page_type(content_markdown_rich, page_num=5, total_pages=10)

        assert page_type == PageType.CONTENT

    def test_title_checked_before_toc_on_first_page(self):
        """TITLE check runs before TOC on first page (order matters)."""
        # On page 1, if content is < 500 chars OR has <= 2 hashes, TITLE is returned
        # before TOC detection runs
        toc_on_first_page = """# Table of Contents

Chapter 1 ............... 1
Chapter 2 ............... 5
"""
        page_type = _detect_page_type(toc_on_first_page, page_num=1, total_pages=10)

        # TITLE check comes first on page 1, content is short so it's TITLE
        assert page_type == PageType.TITLE

    def test_toc_detected_on_non_first_page(self):
        """TOC is properly detected on non-first pages."""
        toc_page = """# Table of Contents

Chapter 1 ............... 1
Chapter 2 ............... 5
"""
        page_type = _detect_page_type(toc_page, page_num=2, total_pages=10)

        assert page_type == PageType.TOC

    def test_appendix_takes_precedence(self):
        """Appendix detection takes precedence over blank check."""
        appendix_short = """# Appendix A
"""
        page_type = _detect_page_type(appendix_short, page_num=5, total_pages=10)

        assert page_type == PageType.APPENDIX


# =============================================================================
# quick_scan_page Tests - Heading Extraction
# =============================================================================


class TestQuickScanPageHeadings:
    """Tests for quick_scan_page heading extraction."""

    def test_extracts_single_heading(self):
        """Single heading is extracted correctly."""
        markdown = "# Introduction\n\nSome content here."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == ["Introduction"]
        assert skeleton.heading_levels == [1]

    def test_extracts_multiple_headings(self):
        """Multiple headings at different levels are extracted."""
        markdown = """# Chapter 1

## Section 1.1

Content here.

### Subsection 1.1.1

More content.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == ["Chapter 1", "Section 1.1", "Subsection 1.1.1"]
        assert skeleton.heading_levels == [1, 2, 3]

    def test_extracts_heading_levels_correctly(self):
        """All heading levels (1-6) are extracted with correct levels."""
        markdown = """# H1
## H2
### H3
#### H4
##### H5
###### H6
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == ["H1", "H2", "H3", "H4", "H5", "H6"]
        assert skeleton.heading_levels == [1, 2, 3, 4, 5, 6]

    def test_heading_text_stripped(self):
        """Heading text is stripped of whitespace."""
        markdown = "#   Heading with spaces   \n\nContent."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == ["Heading with spaces"]

    def test_no_headings_found(self):
        """Page without headings returns empty lists."""
        markdown = "Just some plain text without any headings."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == []
        assert skeleton.heading_levels == []

    def test_headings_with_special_characters(self):
        """Headings with special characters are extracted correctly."""
        markdown = """# Section 2.1: Data & Analysis

## "Quoted" Heading (With Parens)

### FAQ / Q&A
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == [
            "Section 2.1: Data & Analysis",
            '"Quoted" Heading (With Parens)',
            "FAQ / Q&A",
        ]

    def test_hash_without_space_not_matched(self):
        """Hash without space after is not matched as heading."""
        markdown = """#NoSpace

##AlsoNoSpace

### Valid With Space
"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        # Only "### Valid With Space" matches - others lack space after #
        assert skeleton.headings == ["Valid With Space"]
        assert skeleton.heading_levels == [3]

    def test_inline_hash_not_matched(self):
        """Hash symbols in middle of text are not matched."""
        markdown = "Use the # character for comments. Also ## and ###."

        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        # Hashes in middle of line don't match (must be at line start)
        assert skeleton.headings == []


# =============================================================================
# quick_scan_page Tests - Figure Counting
# =============================================================================


class TestQuickScanPageFigures:
    """Tests for quick_scan_page figure counting."""

    def test_counts_image_placeholder_comment(self):
        """Counts <!-- image --> placeholder."""
        markdown = """# Section

<!-- image -->

Some text.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 1

    def test_counts_numbered_image_placeholder(self):
        """Counts <!-- image:1 --> numbered placeholder."""
        markdown = """# Section

<!-- image:1 -->

Description of figure 1.

<!-- image:2 -->

Description of figure 2.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 2

    def test_counts_image_placeholder_variations(self):
        """Counts various image placeholder formats."""
        markdown = """# Images

<!-- image -->
<!--image:1-->
<!--  image:2  -->
<!-- IMAGE:3 -->
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 4

    def test_counts_empty_alt_images(self):
        """Counts images with empty alt text."""
        markdown = """# Section

![](image1.png)

Some text.

![](image2.jpg)
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 2

    def test_combined_placeholders_and_empty_alt(self):
        """Counts both placeholders and empty alt images."""
        markdown = """# Section

<!-- image:1 -->

![](figure.png)

<!-- image:2 -->
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 3

    def test_images_with_alt_not_counted(self):
        """Images with actual alt text are not counted as needing work."""
        markdown = """# Section

![A diagram showing the architecture](diagram.png)

![Chart of results](chart.png)
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        # Images with alt text aren't counted
        assert skeleton.figure_count == 0

    def test_no_figures(self):
        """Page without figures returns count of 0."""
        markdown = "# Section\n\nJust text content."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.figure_count == 0


# =============================================================================
# quick_scan_page Tests - Table Counting
# =============================================================================


class TestQuickScanPageTables:
    """Tests for quick_scan_page table counting."""

    def test_counts_table_placeholder_comment(self):
        """Counts <!-- table --> placeholder."""
        markdown = """# Section

<!-- table -->

Description of the table.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.table_count == 1

    def test_counts_numbered_table_placeholder(self):
        """Counts <!-- table:1 --> numbered placeholder."""
        markdown = """# Section

<!-- table:1 -->

Table 1 description.

<!-- table:2 -->

Table 2 description.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.table_count == 2

    def test_counts_table_placeholder_variations(self):
        """Counts various table placeholder formats."""
        markdown = """# Tables

<!-- table -->
<!--table:1-->
<!--  table:2  -->
<!-- TABLE:3 -->
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.table_count == 4

    def test_actual_markdown_tables_not_counted(self):
        """Actual markdown tables are not counted as placeholders."""
        markdown = """# Section

| Column 1 | Column 2 |
|----------|----------|
| Data 1   | Data 2   |
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        # Markdown tables don't match the placeholder pattern
        assert skeleton.table_count == 0

    def test_no_tables(self):
        """Page without table placeholders returns count of 0."""
        markdown = "# Section\n\nJust text content."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.table_count == 0


# =============================================================================
# quick_scan_page Tests - Word Count
# =============================================================================


class TestQuickScanPageWordCount:
    """Tests for quick_scan_page word counting."""

    def test_word_count_simple_text(self):
        """Word count for simple text."""
        markdown = "This is a simple sentence with eight words."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.word_count == 8

    def test_word_count_with_headings(self):
        """Word count includes heading text."""
        markdown = """# Introduction

This is the content.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        # "Introduction" + "This is the content" = 1 + 4 = 5 words
        assert skeleton.word_count == 5

    def test_word_count_ignores_markdown_syntax(self):
        """Word count ignores markdown syntax characters."""
        markdown = """## **Bold** and *italic* text

| Col | Col |
|-----|-----|

Some more text.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        # "Bold", "and", "italic", "text", "Col", "Col", "Some", "more", "text"
        assert skeleton.word_count >= 9

    def test_word_count_empty_page(self):
        """Empty page has word count of 0."""
        markdown = ""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.word_count == 0

    def test_word_count_only_whitespace(self):
        """Page with only whitespace has word count of 0."""
        markdown = "   \n\n   \t   "
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.word_count == 0


# =============================================================================
# quick_scan_page Tests - Citation Detection
# =============================================================================


class TestQuickScanPageCitations:
    """Tests for quick_scan_page citation detection."""

    def test_detects_bracketed_citations(self):
        """Detects [1], [2] style citations."""
        markdown = """According to [1], the method works well.

Further evidence from [2] and [3] supports this.
"""
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.has_citations is True

    def test_detects_single_citation(self):
        """Detects single citation reference."""
        markdown = "This is supported by previous research [1]."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.has_citations is True

    def test_no_citations(self):
        """Page without citations returns False."""
        markdown = "This is just regular text without any citations."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.has_citations is False

    def test_square_brackets_non_citation(self):
        """Non-numeric square brackets don't trigger citation detection."""
        markdown = "See [this link] for more information."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.has_citations is False

    def test_multi_digit_citations(self):
        """Multi-digit citations like [12] are detected."""
        markdown = "References [12] and [123] confirm this."
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.has_citations is True


# =============================================================================
# quick_scan_page Tests - Page Type Integration
# =============================================================================


class TestQuickScanPageType:
    """Tests for quick_scan_page page type detection integration."""

    def test_returns_correct_page_type_title(self, title_page_markdown: str):
        """Returns TITLE page type for first page."""
        skeleton = quick_scan_page(page_num=1, markdown=title_page_markdown, total_pages=10)

        assert skeleton.page_type == PageType.TITLE

    def test_returns_correct_page_type_toc(self, toc_markdown: str):
        """Returns TOC page type for table of contents."""
        skeleton = quick_scan_page(page_num=2, markdown=toc_markdown, total_pages=10)

        assert skeleton.page_type == PageType.TOC

    def test_returns_correct_page_type_content(self, content_markdown: str):
        """Returns CONTENT page type for regular content."""
        skeleton = quick_scan_page(page_num=3, markdown=content_markdown, total_pages=10)

        assert skeleton.page_type == PageType.CONTENT

    def test_returns_correct_page_type_references(self, references_markdown: str):
        """Returns REFERENCES page type for references section."""
        skeleton = quick_scan_page(page_num=9, markdown=references_markdown, total_pages=10)

        assert skeleton.page_type == PageType.REFERENCES

    def test_returns_correct_page_type_appendix(self, appendix_markdown: str):
        """Returns APPENDIX page type for appendix."""
        skeleton = quick_scan_page(page_num=7, markdown=appendix_markdown, total_pages=10)

        assert skeleton.page_type == PageType.APPENDIX

    def test_returns_correct_page_type_blank(self, blank_page_markdown: str):
        """Returns BLANK page type for blank pages."""
        skeleton = quick_scan_page(page_num=5, markdown=blank_page_markdown, total_pages=10)

        assert skeleton.page_type == PageType.BLANK


# =============================================================================
# quick_scan_page Tests - Complete PageSkeleton
# =============================================================================


class TestQuickScanPageComplete:
    """Tests for complete quick_scan_page output."""

    def test_returns_page_skeleton_instance(self, content_markdown: str):
        """Returns a PageSkeleton instance."""
        skeleton = quick_scan_page(page_num=3, markdown=content_markdown, total_pages=10)

        assert isinstance(skeleton, PageSkeleton)

    def test_page_num_preserved(self):
        """Page number is preserved in skeleton."""
        markdown = "# Test"
        skeleton = quick_scan_page(page_num=7, markdown=markdown, total_pages=10)

        assert skeleton.page_num == 7

    def test_complete_extraction(self, content_markdown_rich: str):
        """Complete extraction from rich content page."""
        skeleton = quick_scan_page(page_num=5, markdown=content_markdown_rich, total_pages=10)

        # Verify all fields are populated appropriately
        assert skeleton.page_num == 5
        assert len(skeleton.headings) >= 3  # At least 3 headings
        assert len(skeleton.heading_levels) == len(skeleton.headings)
        assert skeleton.figure_count >= 2  # At least 2 figures
        assert skeleton.table_count >= 1  # At least 1 table
        assert skeleton.word_count > 0
        assert skeleton.has_citations is True  # Has [1], [2], [3]
        assert skeleton.page_type == PageType.CONTENT

    def test_all_fields_populated(self):
        """All PageSkeleton fields are populated."""
        markdown = """# Heading One

## Heading Two

![](image.png)

<!-- table:1 -->

Reference [1] citation.
"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        # All fields should be set
        assert skeleton.page_num == 3
        assert skeleton.headings == ["Heading One", "Heading Two"]
        assert skeleton.heading_levels == [1, 2]
        assert skeleton.figure_count == 1
        assert skeleton.table_count == 1
        assert skeleton.word_count > 0
        assert skeleton.has_citations is True
        assert skeleton.page_type == PageType.CONTENT


# =============================================================================
# Edge Cases and Boundary Tests
# =============================================================================


class TestQuickScanPageEdgeCases:
    """Edge cases and boundary tests for quick_scan_page."""

    def test_empty_markdown(self):
        """Handles empty markdown gracefully."""
        # Use page 5 to avoid TITLE detection on page 1
        skeleton = quick_scan_page(page_num=5, markdown="", total_pages=10)

        assert skeleton.headings == []
        assert skeleton.heading_levels == []
        assert skeleton.figure_count == 0
        assert skeleton.table_count == 0
        assert skeleton.word_count == 0
        assert skeleton.has_citations is False
        assert skeleton.page_type == PageType.BLANK

    def test_empty_markdown_on_first_page(self):
        """Empty markdown on first page is detected as TITLE (not BLANK).

        This is because the TITLE check runs before BLANK check,
        and empty string has len < 500.
        """
        skeleton = quick_scan_page(page_num=1, markdown="", total_pages=5)

        assert skeleton.headings == []
        assert skeleton.heading_levels == []
        assert skeleton.figure_count == 0
        assert skeleton.table_count == 0
        assert skeleton.word_count == 0
        assert skeleton.has_citations is False
        # TITLE is detected because page_num == 1 and len("") < 500
        assert skeleton.page_type == PageType.TITLE

    def test_single_page_document(self):
        """Handles single-page document."""
        markdown = "# Single Page\n\nContent"
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=1)

        assert skeleton.page_num == 1
        assert skeleton.page_type == PageType.TITLE  # First page with limited content

    def test_very_long_heading(self):
        """Handles very long heading text."""
        long_title = "A" * 500
        markdown = f"# {long_title}\n\nContent"
        skeleton = quick_scan_page(page_num=1, markdown=markdown, total_pages=5)

        assert skeleton.headings == [long_title]

    def test_unicode_content(self):
        """Handles unicode characters in content."""
        markdown = """# Introduccion

El metodo utiliza tecnicas de aprendizaje automatico.

## Resultados

Los datos muestran mejorias significativas.
"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        assert "Introduccion" in skeleton.headings
        assert "Resultados" in skeleton.headings
        assert skeleton.word_count > 0

    def test_mixed_language_content(self):
        """Handles mixed language content."""
        markdown = """# Research Findings

This paper presents findings from our study.

## 研究结果

结果显示显著改善。
"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        assert len(skeleton.headings) == 2
        assert skeleton.word_count > 0

    def test_malformed_markdown(self):
        """Handles malformed markdown gracefully."""
        markdown = """#Not a heading because no space

##Also not valid

# Valid Heading

[
Unclosed bracket

| Broken | Table
| Without | Proper |
Syntax

"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        # Should extract valid heading only
        assert skeleton.headings == ["Valid Heading"]
        assert skeleton.page_type == PageType.CONTENT

    def test_page_at_70_percent_boundary(self):
        """Tests behavior at exactly 70% through document (references threshold)."""
        references_content = """# References

[1] First reference.
[2] Second reference.
"""
        # Page 7 of 10 is exactly 70%
        skeleton = quick_scan_page(page_num=7, markdown=references_content, total_pages=10)

        # At exactly 70%, might or might not trigger (> 70% check)
        # Page 7 / 10 = 0.7, but check is > 0.7, so page 7 shouldn't be REFERENCES
        assert skeleton.page_type == PageType.CONTENT

        # Page 8 of 10 is 80%
        skeleton_later = quick_scan_page(page_num=8, markdown=references_content, total_pages=10)
        assert skeleton_later.page_type == PageType.REFERENCES

    def test_toc_detection_boundary_five_dots(self):
        """Tests TOC detection with exactly 5 '...' patterns (boundary)."""
        markdown = """Index

Item 1 ... 1
Item 2 ... 2
Item 3 ... 3
Item 4 ... 4
Item 5 ... 5
"""
        skeleton = quick_scan_page(page_num=2, markdown=markdown, total_pages=10)

        # Need > 5, so exactly 5 shouldn't trigger
        assert skeleton.page_type == PageType.CONTENT

    def test_toc_detection_six_dots(self):
        """Tests TOC detection with 6 '...' patterns (above threshold)."""
        markdown = """Index

Item 1 ... 1
Item 2 ... 2
Item 3 ... 3
Item 4 ... 4
Item 5 ... 5
Item 6 ... 6
"""
        skeleton = quick_scan_page(page_num=2, markdown=markdown, total_pages=10)

        assert skeleton.page_type == PageType.TOC

    def test_blank_page_boundary_49_chars(self):
        """Page with 49 characters stripped is BLANK."""
        # 49 characters after strip
        markdown = "x" * 49
        skeleton = quick_scan_page(page_num=5, markdown=markdown, total_pages=10)

        assert skeleton.page_type == PageType.BLANK

    def test_blank_page_boundary_50_chars(self):
        """Page with exactly 50 characters is not BLANK."""
        # 50 characters after strip
        markdown = "x" * 50
        skeleton = quick_scan_page(page_num=5, markdown=markdown, total_pages=10)

        assert skeleton.page_type == PageType.CONTENT

    def test_heading_at_line_start_only(self):
        """Only headings at line start are matched."""
        markdown = """# Valid Heading

Some text with # inline hash.

Another line with ## double hash.
"""
        skeleton = quick_scan_page(page_num=3, markdown=markdown, total_pages=10)

        assert skeleton.headings == ["Valid Heading"]
        assert skeleton.heading_levels == [1]
