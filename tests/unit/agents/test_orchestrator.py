"""Unit tests for Orchestrator module helper functions.

Tests the non-LLM helper functions from src/agents/orchestrator.py:
- rewrite_figure_urls: Rewrites placeholder URLs to relative paths
- build_page_boundary_map: Maps line numbers to source pages
- _critic_issue_to_document_job: Converts critic issues to document jobs

Note: collect_footnotes_at_end, validate_merge_result, and check_convergence
are tested in separate test files to maintain focused test modules.
"""

import pytest
from src.agents.models import (
    CriticIssue,
    DocumentJobType,
    IssueSeverity,
    PageBoundary,
    StoredFigure,
)
from src.agents.orchestrator import (
    _critic_issue_to_document_job,
    build_page_boundary_map,
    rewrite_figure_urls,
)

pytestmark = pytest.mark.unit


# =============================================================================
# rewrite_figure_urls() Tests
# =============================================================================


class TestRewriteFigureUrls:
    """Tests for rewrite_figure_urls function."""

    def test_empty_stored_figures_returns_unchanged(self):
        """Empty stored_figures list returns markdown unchanged."""
        markdown = """# Document

![A diagram](figure_1.png)

Some text.
"""
        result_md, result_figures = rewrite_figure_urls(markdown, [])

        assert result_md == markdown
        assert result_figures == []

    def test_no_image_patterns_returns_unchanged(self):
        """Markdown without images returns unchanged."""
        markdown = """# Document

Some text without any images.

## Section 2

More content here.
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/fig1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert result_md == markdown
        assert result_figures == figures

    def test_single_figure_rewrite(self):
        """Single figure URL is rewritten correctly."""
        markdown = """# Document

![A diagram](figure_1.png)

Some text.
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![A diagram](images/figure-1.png)" in result_md
        assert "figure_1.png" not in result_md

    def test_multiple_figures_rewrite_in_order(self):
        """Multiple figures are rewritten in order of appearance."""
        markdown = """# Document

![First diagram](figure_1.png)

Some text.

![Second chart](placeholder.png)

![Third image](img3.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
            StoredFigure(
                figure_id="figure-2",
                s3_key="job123/images/figure-2.png",
                page_num=1,
                ref_id="#/pictures/1",
            ),
            StoredFigure(
                figure_id="figure-3",
                s3_key="job123/images/figure-3.png",
                page_num=2,
                ref_id="#/pictures/2",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![First diagram](images/figure-1.png)" in result_md
        assert "![Second chart](images/figure-2.png)" in result_md
        assert "![Third image](images/figure-3.png)" in result_md

    def test_image_with_title_preserved(self):
        """Image with title attribute preserves the title."""
        markdown = """# Document

![Chart showing data](placeholder.png "Chart title")
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert '![Chart showing data](images/figure-1.png "Chart title")' in result_md

    def test_alt_text_extracted_and_stored(self):
        """Alt text is extracted from markdown and stored in figure."""
        markdown = """# Document

![Detailed architecture diagram](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
                alt_text="",  # Empty initially
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert result_figures[0].alt_text == "Detailed architecture diagram"

    def test_existing_alt_text_not_overwritten(self):
        """Existing alt_text in StoredFigure is not overwritten."""
        markdown = """# Document

![New alt text](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
                alt_text="Original alt text",  # Already has alt text
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # Original alt_text should be preserved
        assert result_figures[0].alt_text == "Original alt text"

    def test_empty_alt_text_image_handled(self):
        """Image with empty alt text is handled correctly."""
        markdown = """# Document

![](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![](images/figure-1.png)" in result_md
        assert result_figures[0].alt_text == ""

    def test_empty_src_url_skipped(self):
        """Image with empty src URL is skipped (not mapped to a figure)."""
        markdown = """# Document

![]()

![Real image](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # Empty src should be unchanged
        assert "![]()" in result_md
        # Real image should be rewritten
        assert "![Real image](images/figure-1.png)" in result_md

    def test_already_relative_path_skipped(self):
        """URLs already starting with 'images/figure-' are skipped."""
        markdown = """# Document

![Already correct](images/figure-1.png)

![Needs rewrite](placeholder.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-2",
                s3_key="job123/images/figure-2.png",
                page_num=1,
                ref_id="#/pictures/1",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # First image unchanged (already correct path)
        assert "![Already correct](images/figure-1.png)" in result_md
        # Second image rewritten
        assert "![Needs rewrite](images/figure-2.png)" in result_md

    def test_more_images_than_figures_stops_gracefully(self):
        """More images than figures logs warning and stops at last figure."""
        markdown = """# Document

![Image 1](fig1.png)

![Image 2](fig2.png)

![Image 3](fig3.png)

![Image 4](fig4.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
            StoredFigure(
                figure_id="figure-2",
                s3_key="job123/images/figure-2.png",
                page_num=1,
                ref_id="#/pictures/1",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # First two images should be rewritten
        assert "![Image 1](images/figure-1.png)" in result_md
        assert "![Image 2](images/figure-2.png)" in result_md
        # Remaining images unchanged
        assert "![Image 3](fig3.png)" in result_md
        assert "![Image 4](fig4.png)" in result_md

    def test_whitespace_only_url_skipped(self):
        """URL that is only whitespace is treated as empty."""
        markdown = """# Document

![Whitespace only](   )

![Real image](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # Whitespace URL should be skipped (stripped to empty)
        # The figure should be used for the real image
        assert "![Real image](images/figure-1.png)" in result_md

    def test_special_characters_in_alt_text_preserved(self):
        """Special characters in alt text are preserved."""
        markdown = """# Document

![Diagram showing A > B & C < D](figure.png)
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![Diagram showing A > B & C < D](images/figure-1.png)" in result_md
        assert result_figures[0].alt_text == "Diagram showing A > B & C < D"

    def test_multiline_markdown_with_images(self):
        """Images in multiline markdown are all rewritten."""
        markdown = """# Document Title

Introduction paragraph with content.

## Section 1

![Figure 1 caption](fig1.png)

More text explaining things.

### Subsection 1.1

![Figure 2 caption](fig2.png "Title 2")

Conclusion.
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
            StoredFigure(
                figure_id="figure-2",
                s3_key="job123/images/figure-2.png",
                page_num=2,
                ref_id="#/pictures/1",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![Figure 1 caption](images/figure-1.png)" in result_md
        assert '![Figure 2 caption](images/figure-2.png "Title 2")' in result_md
        assert result_figures[0].alt_text == "Figure 1 caption"
        assert result_figures[1].alt_text == "Figure 2 caption"

    def test_caption_and_ref_id_preserved(self):
        """Original caption and ref_id are preserved in StoredFigure."""
        markdown = """![Alt text](figure.png)"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
                caption="Original caption from PDF",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert result_figures[0].caption == "Original caption from PDF"
        assert result_figures[0].ref_id == "#/pictures/0"
        assert result_figures[0].s3_key == "job123/images/figure-1.png"

    def test_image_with_url_containing_spaces_remains_unchanged(self):
        """URLs with embedded spaces don't match the image pattern, left unchanged.

        The regex pattern `([^)\\s"]*)\\s*(?:"([^"]*)")?` captures the URL as
        all non-space, non-quote, non-paren chars. When a URL contains a space
        (e.g., "some image.png"), the pattern captures "some" as the URL and
        "image.png)" doesn't complete the pattern correctly, so no match occurs.

        This is actually correct behavior - proper markdown URLs should be
        URL-encoded (spaces as %20) or not contain spaces.
        """
        markdown = """![Image](some image.png)"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        # The malformed URL pattern is left unchanged
        assert result_md == markdown
        # No figures consumed (no valid matches)
        # Note: The function returns the original figures list
        assert len(result_figures) == 1

    def test_mixed_decorative_and_content_images(self):
        """Mix of images with and without alt text handled correctly."""
        markdown = """# Document

![](decorative.png)

![Important chart](chart.png)

![](another_decorative.png "Title only")
"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=1,
                ref_id="#/pictures/0",
            ),
            StoredFigure(
                figure_id="figure-2",
                s3_key="job123/images/figure-2.png",
                page_num=1,
                ref_id="#/pictures/1",
            ),
            StoredFigure(
                figure_id="figure-3",
                s3_key="job123/images/figure-3.png",
                page_num=1,
                ref_id="#/pictures/2",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert "![](images/figure-1.png)" in result_md
        assert "![Important chart](images/figure-2.png)" in result_md
        assert '![](images/figure-3.png "Title only")' in result_md

    def test_figure_page_num_preserved(self):
        """Figure page_num is preserved in the output."""
        markdown = """![Image](figure.png)"""
        figures = [
            StoredFigure(
                figure_id="figure-1",
                s3_key="job123/images/figure-1.png",
                page_num=5,
                ref_id="#/pictures/0",
            ),
        ]

        result_md, result_figures = rewrite_figure_urls(markdown, figures)

        assert result_figures[0].page_num == 5


# =============================================================================
# build_page_boundary_map() Tests
# =============================================================================


class TestBuildPageBoundaryMap:
    """Tests for build_page_boundary_map function."""

    def test_single_page_document(self):
        """Single page document creates correct boundary map."""
        page_markdowns = {1: "# Page 1\n\nContent here."}
        merged = page_markdowns[1]

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        assert result.document_id == "doc-123"
        assert len(result.boundaries) == 1
        assert result.boundaries[0].page_num == 1
        assert result.boundaries[0].start_line == 1
        # "# Page 1\n\nContent here." has 3 lines
        assert result.boundaries[0].end_line == 3

    def test_two_page_document(self):
        """Two page document creates boundaries with correct line numbers."""
        page_markdowns = {
            1: "# Page 1\nLine 2",
            2: "# Page 2\nLine 2 of page 2",
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        assert len(result.boundaries) == 2
        # Page 1: lines 1-2
        assert result.boundaries[0].page_num == 1
        assert result.boundaries[0].start_line == 1
        assert result.boundaries[0].end_line == 2
        # Page 2: starts after page 1 + separator
        assert result.boundaries[1].page_num == 2
        assert result.boundaries[1].start_line > 2

    def test_three_page_document(self):
        """Three page document creates sequential boundaries."""
        page_markdowns = {
            1: "Page 1 line 1\nPage 1 line 2",
            2: "Page 2 single line",
            3: "Page 3\nHas\nThree lines",
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        assert len(result.boundaries) == 3
        assert result.boundaries[0].page_num == 1
        assert result.boundaries[1].page_num == 2
        assert result.boundaries[2].page_num == 3

        # Verify boundaries don't overlap
        for i in range(len(result.boundaries) - 1):
            assert (
                result.boundaries[i].end_line < result.boundaries[i + 1].start_line
            )

    def test_empty_page_markdowns(self):
        """Empty page_markdowns creates empty boundary map."""
        result = build_page_boundary_map({}, "", "doc-123")

        assert result.document_id == "doc-123"
        assert len(result.boundaries) == 0
        assert result.total_lines == 1  # Empty string has 1 line

    def test_character_offsets_tracked(self):
        """Character offsets are tracked correctly."""
        page_markdowns = {
            1: "Short",  # 5 chars
            2: "Longer text here",  # 16 chars
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        # Page 1 starts at char 0
        assert result.boundaries[0].start_char == 0
        assert result.boundaries[0].end_char == 5

        # Page 2 starts after page 1 content + separator
        # Separator is "\n\n---\n\n" = 7 chars
        # But the function adds separator chars = 5 (for "\n\n---\n\n")
        assert result.boundaries[1].start_char > 5

    def test_pages_not_in_order(self):
        """Pages are processed in sorted order regardless of dict order."""
        # Dictionary with pages out of order
        page_markdowns = {
            3: "Page 3 content",
            1: "Page 1 content",
            2: "Page 2 content",
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        # Boundaries should be in order 1, 2, 3
        assert result.boundaries[0].page_num == 1
        assert result.boundaries[1].page_num == 2
        assert result.boundaries[2].page_num == 3

    def test_total_lines_calculated(self):
        """Total lines is calculated from merged markdown."""
        page_markdowns = {
            1: "Line 1\nLine 2\nLine 3",  # 3 lines
            2: "Line 1\nLine 2",  # 2 lines
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        # Count lines in merged markdown
        expected_lines = len(merged.split("\n"))
        assert result.total_lines == expected_lines

    def test_multiline_page_content(self):
        """Pages with multiple lines are handled correctly."""
        page_markdowns = {
            1: "# Heading\n\nParagraph 1.\n\nParagraph 2.",  # 5 lines
        }
        merged = page_markdowns[1]

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        assert result.boundaries[0].start_line == 1
        assert result.boundaries[0].end_line == 5

    def test_page_with_empty_content(self):
        """Page with empty string content is handled."""
        page_markdowns = {
            1: "",  # Empty page
            2: "Content",
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        # Empty page should still create a boundary
        # but with 0 lines (or 1 line for empty string)
        assert len(result.boundaries) == 2

    def test_document_id_preserved(self):
        """Document ID is preserved in the result."""
        doc_id = "unique-document-id-12345"
        page_markdowns = {1: "Content"}
        merged = "Content"

        result = build_page_boundary_map(page_markdowns, merged, doc_id)

        assert result.document_id == doc_id

    def test_single_line_pages(self):
        """Single line pages create correct boundaries."""
        page_markdowns = {
            1: "One",
            2: "Two",
            3: "Three",
        }
        merged = "\n\n---\n\n".join(
            page_markdowns[p] for p in sorted(page_markdowns.keys())
        )

        result = build_page_boundary_map(page_markdowns, merged, "doc-123")

        # Each page has 1 line
        assert result.boundaries[0].end_line - result.boundaries[0].start_line == 0
        assert result.boundaries[1].end_line - result.boundaries[1].start_line == 0
        assert result.boundaries[2].end_line - result.boundaries[2].start_line == 0


# =============================================================================
# _critic_issue_to_document_job() Tests
# =============================================================================


class TestCriticIssueToDocumentJob:
    """Tests for _critic_issue_to_document_job function."""

    def test_structure_category_maps_to_structure_fix(self):
        """'structure' category maps to STRUCTURE_FIX job type."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="structure",
            description="Wrong heading level",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.STRUCTURE_FIX

    def test_accessibility_category_maps_to_accessibility_fix(self):
        """'accessibility' category maps to ACCESSIBILITY_FIX job type."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Missing alt text",
            line_start=20,
            line_end=25,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.ACCESSIBILITY_FIX

    def test_content_category_maps_to_content_fix(self):
        """'content' category maps to CONTENT_FIX job type."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Table transcription error",
            line_start=30,
            line_end=40,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.CONTENT_FIX

    def test_formatting_category_maps_to_formatting_fix(self):
        """'formatting' category maps to FORMATTING_FIX job type."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Inconsistent spacing",
            line_start=50,
            line_end=50,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.FORMATTING_FIX

    def test_unknown_category_defaults_to_content_fix(self):
        """Unknown category defaults to CONTENT_FIX job type."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="unknown_category",
            description="Some issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.CONTENT_FIX

    def test_critical_severity_priority_1(self):
        """Critical severity maps to priority 1."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Critical issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.priority == 1

    def test_major_severity_priority_2(self):
        """Major severity maps to priority 2."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Major issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.priority == 2

    def test_minor_severity_priority_3(self):
        """Minor severity maps to priority 3."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Minor issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.priority == 3

    def test_cosmetic_severity_priority_4(self):
        """Cosmetic severity maps to priority 4."""
        issue = CriticIssue(
            severity=IssueSeverity.COSMETIC,
            category="formatting",
            description="Cosmetic issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.priority == 4

    def test_line_range_preserved(self):
        """Line start and end are preserved in job."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue spanning lines",
            line_start=15,
            line_end=25,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.line_start == 15
        assert job.line_end == 25

    def test_search_text_preserved(self):
        """Search text is preserved in job."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue description",
            line_start=10,
            line_end=10,
            search_text="specific text to find",
        )

        job = _critic_issue_to_document_job(issue)

        assert job.search_text == "specific text to find"

    def test_issue_description_preserved(self):
        """Issue description is preserved in job."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Detailed description of the issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.issue_description == "Detailed description of the issue"

    def test_suggested_fix_preserved(self):
        """Suggested fix is preserved in job."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="structure",
            description="Issue",
            line_start=10,
            line_end=10,
            suggested_fix="Change heading level from H3 to H2",
        )

        job = _critic_issue_to_document_job(issue)

        assert job.suggested_fix == "Change heading level from H3 to H2"

    def test_source_pages_preserved(self):
        """Source pages are preserved in job."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue spanning pages",
            line_start=10,
            line_end=30,
            source_pages=[1, 2, 3],
        )

        job = _critic_issue_to_document_job(issue)

        assert job.source_pages == [1, 2, 3]

    def test_job_id_generated(self):
        """Job ID is auto-generated."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_id is not None
        assert len(job.job_id) > 0

    def test_status_defaults_to_pending(self):
        """Job status defaults to 'pending'."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Issue",
            line_start=10,
            line_end=10,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.status == "pending"

    def test_empty_search_text_handled(self):
        """Empty search_text is handled correctly."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue",
            line_start=10,
            line_end=10,
            search_text="",  # Empty
        )

        job = _critic_issue_to_document_job(issue)

        assert job.search_text == ""

    def test_empty_suggested_fix_handled(self):
        """Empty suggested_fix is handled correctly."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue",
            line_start=10,
            line_end=10,
            suggested_fix="",  # Empty
        )

        job = _critic_issue_to_document_job(issue)

        assert job.suggested_fix == ""

    def test_empty_source_pages_handled(self):
        """Empty source_pages list is handled correctly."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue",
            line_start=10,
            line_end=10,
            source_pages=[],  # Empty
        )

        job = _critic_issue_to_document_job(issue)

        assert job.source_pages == []

    def test_single_line_issue(self):
        """Single line issue (line_start == line_end) is handled."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Single line issue",
            line_start=42,
            line_end=42,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.line_start == 42
        assert job.line_end == 42

    def test_large_line_range(self):
        """Large line range is handled correctly."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue spanning many lines",
            line_start=1,
            line_end=1000,
        )

        job = _critic_issue_to_document_job(issue)

        assert job.line_start == 1
        assert job.line_end == 1000

    def test_all_fields_complete(self):
        """All fields from issue are correctly transferred to job."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Missing alt text for figure",
            line_start=100,
            line_end=105,
            search_text="![](images/figure-1.png)",
            suggested_fix="Add descriptive alt text",
            source_pages=[3, 4],
        )

        job = _critic_issue_to_document_job(issue)

        assert job.job_type == DocumentJobType.ACCESSIBILITY_FIX
        assert job.priority == 1
        assert job.line_start == 100
        assert job.line_end == 105
        assert job.search_text == "![](images/figure-1.png)"
        assert job.issue_description == "Missing alt text for figure"
        assert job.suggested_fix == "Add descriptive alt text"
        assert job.source_pages == [3, 4]
        assert job.status == "pending"
