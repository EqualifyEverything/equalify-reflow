"""Unit tests for V5 Worker Agent tools.

Tests the find_text tool and view_page tool updates that fix
the "Target text not found" issue.
"""

import pytest
from dataclasses import dataclass
from unittest.mock import MagicMock, AsyncMock

from src.agents.worker import (
    FindTextResult,
    ViewResult,
    WorkerDeps,
    TableMarkdownResult,
    _find_table_blocks,
)
from src.agents.models import Job, JobContext, JobType, Task, TaskType, DocumentType


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_markdown() -> str:
    """Sample markdown with various placeholders."""
    return """# Document Title

This is paragraph text.

<!-- image:1 -->

More text here.

![](figure1.png)

## Section 2

<!-- table:1 -->

Some content after table.
"""


@pytest.fixture
def sample_job(sample_markdown: str) -> Job:
    """Create a sample job for testing."""
    return Job(
        job_id="test-job-123",
        job_type=JobType.CONTENT,
        page=1,
        tasks=[
            Task(
                task_type=TaskType.ALT_TEXT,
                target="fig:1",
                context="Test figure",
            )
        ],
        context=JobContext(
            document_title="Test Doc",
            document_type=DocumentType.PAPER,
            section_context="Section 1",
            dictionary=["term1", "term2"],
        ),
        page_markdown=sample_markdown,
    )


@pytest.fixture
def worker_deps(sample_job: Job) -> WorkerDeps:
    """Create worker dependencies for testing."""
    # Create a mock image
    mock_image = MagicMock()

    return WorkerDeps(
        job=sample_job,
        page_image=mock_image,
        element_bboxes={},
        page_width=612.0,
        current_markdown=sample_job.page_markdown,
        pending_edits=[],
        validated_edits=[],
        dictionary=["term1", "term2"],
        event_bus=None,
    )


# =============================================================================
# FindTextResult Tests
# =============================================================================


class TestFindTextResult:
    """Test the FindTextResult model."""

    def test_found_result(self):
        """Test a successful find result."""
        result = FindTextResult(
            found=True,
            exact_match="<!-- image:1 -->",
            context_before="paragraph text.\n\n",
            context_after="\n\nMore text",
            line_number=5,
        )
        assert result.found is True
        assert result.exact_match == "<!-- image:1 -->"
        assert result.line_number == 5
        assert result.error is None

    def test_not_found_result(self):
        """Test a not found result."""
        result = FindTextResult(
            found=False,
            error="Could not find 'nonexistent' in the markdown.",
        )
        assert result.found is False
        assert result.exact_match is None
        assert "Could not find" in result.error


# =============================================================================
# ViewResult Tests
# =============================================================================


class TestViewResult:
    """Test the ViewResult model."""

    def test_view_result_with_markdown(self):
        """Test that ViewResult can include markdown content."""
        result = ViewResult(
            success=True,
            description="Page 1 image shown above.",
            markdown_content="# Test\n\nContent here.",
        )
        assert result.success is True
        assert result.markdown_content == "# Test\n\nContent here."

    def test_view_result_without_markdown(self):
        """Test ViewResult without markdown (backward compatibility)."""
        result = ViewResult(
            success=True,
            description="Page 1 image shown above.",
        )
        assert result.success is True
        assert result.markdown_content is None


# =============================================================================
# find_text Tool Logic Tests
# =============================================================================


class TestFindTextLogic:
    """Test the find_text tool logic (without actual agent context)."""

    def test_find_exact_comment_placeholder(self, sample_markdown: str):
        """Test finding an HTML comment placeholder."""
        # Simulate what find_text does
        search_pattern = "<!-- image"

        if search_pattern in sample_markdown:
            idx = sample_markdown.find(search_pattern)
            # Find full comment
            comment_end = sample_markdown.find("-->", idx)
            if comment_end > idx:
                full_match = sample_markdown[idx : comment_end + 3]
                assert full_match == "<!-- image:1 -->"

    def test_find_empty_alt_image(self, sample_markdown: str):
        """Test finding an image with empty alt text."""
        search_pattern = "![]("

        if search_pattern in sample_markdown:
            idx = sample_markdown.find(search_pattern)
            # Find full image markdown
            bracket_start = sample_markdown.rfind("![", max(0, idx - 10), idx + 3)
            if bracket_start >= 0:
                paren_end = sample_markdown.find(")", bracket_start)
                if paren_end > bracket_start:
                    full_match = sample_markdown[bracket_start : paren_end + 1]
                    assert full_match == "![](figure1.png)"

    def test_find_nonexistent_text(self, sample_markdown: str):
        """Test searching for text that doesn't exist."""
        search_pattern = "nonexistent placeholder"
        assert search_pattern not in sample_markdown

    def test_find_table_placeholder(self, sample_markdown: str):
        """Test finding a table placeholder."""
        search_pattern = "<!-- table"

        assert search_pattern in sample_markdown
        idx = sample_markdown.find(search_pattern)
        comment_end = sample_markdown.find("-->", idx)
        full_match = sample_markdown[idx : comment_end + 3]
        assert full_match == "<!-- table:1 -->"


# =============================================================================
# Exact Match Validation Tests
# =============================================================================


class TestExactMatchValidation:
    """Test that exact matches work for propose_edit."""

    def test_exact_match_exists_in_markdown(self, sample_markdown: str):
        """Test that exact match text can be found in markdown."""
        # These are the exact strings that should be found
        exact_matches = [
            "<!-- image:1 -->",
            "![](figure1.png)",
            "<!-- table:1 -->",
        ]

        for match in exact_matches:
            assert match in sample_markdown, f"'{match}' should exist in markdown"

    def test_whitespace_sensitivity(self, sample_markdown: str):
        """Test that whitespace matters for exact match."""
        # Extra space should NOT match
        assert "<!-- image:1  -->" not in sample_markdown
        # But correct version should
        assert "<!-- image:1 -->" in sample_markdown

    def test_case_sensitivity(self, sample_markdown: str):
        """Test that case matters for exact match."""
        # Wrong case should NOT match
        assert "<!-- IMAGE:1 -->" not in sample_markdown
        # But correct case should
        assert "<!-- image:1 -->" in sample_markdown


# =============================================================================
# TableMarkdownResult Tests
# =============================================================================


class TestTableMarkdownResult:
    """Test the TableMarkdownResult model."""

    def test_successful_table_result(self):
        """Test a successful table markdown result."""
        result = TableMarkdownResult(
            success=True,
            markdown="| Col 1 | Col 2 |\n|-------|-------|\n| A     | B     |",
            start_line=5,
            end_line=7,
            format_type="markdown_table",
        )
        assert result.success is True
        assert "| Col 1 | Col 2 |" in result.markdown
        assert result.start_line == 5
        assert result.end_line == 7
        assert result.format_type == "markdown_table"
        assert result.error is None

    def test_comment_placeholder_result(self):
        """Test a table found as comment placeholder."""
        result = TableMarkdownResult(
            success=True,
            markdown="<!-- table:1 -->",
            start_line=10,
            end_line=10,
            format_type="comment",
        )
        assert result.success is True
        assert result.format_type == "comment"
        assert result.start_line == result.end_line

    def test_table_not_found(self):
        """Test when table is not found."""
        result = TableMarkdownResult(
            success=False,
            error="Table 5 not found in markdown.",
        )
        assert result.success is False
        assert "not found" in result.error


# =============================================================================
# _find_table_blocks Tests
# =============================================================================


class TestFindTableBlocks:
    """Test the _find_table_blocks helper function."""

    def test_find_simple_table(self):
        """Test finding a simple markdown table."""
        markdown = """# Header

| Name | Value |
|------|-------|
| A    | 1     |
| B    | 2     |

Some text after.
"""
        blocks = _find_table_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0]["start_line"] == 3
        assert blocks[0]["end_line"] == 6
        assert "| Name | Value |" in blocks[0]["content"]

    def test_find_multiple_tables(self):
        """Test finding multiple tables."""
        markdown = """# Doc

| Table 1 |
|---------|
| A       |

Separator text.

| Table 2 | Col 2 |
|---------|-------|
| X       | Y     |
"""
        blocks = _find_table_blocks(markdown)
        assert len(blocks) == 2
        assert "Table 1" in blocks[0]["content"]
        assert "Table 2" in blocks[1]["content"]

    def test_no_tables(self):
        """Test when no tables exist."""
        markdown = """# Just Text

Some paragraph.

More text.
"""
        blocks = _find_table_blocks(markdown)
        assert len(blocks) == 0

    def test_table_at_end_of_document(self):
        """Test table at end of document (no trailing newline)."""
        markdown = """# Header

| Final |
|-------|
| Data  |"""
        blocks = _find_table_blocks(markdown)
        assert len(blocks) == 1
        assert blocks[0]["content"].endswith("| Data  |")


# =============================================================================
# Line-Range Replacement Tests
# =============================================================================


class TestLineRangeReplacement:
    """Test line-range replacement logic for multi-line content."""

    def test_replace_table_by_lines(self):
        """Test replacing a table using line numbers."""
        original = """Line 1
Line 2
| Old | Table |
|-----|-------|
| A   | B     |
Line 6
Line 7"""
        lines = original.split("\n")

        # Replace lines 3-5 (the table)
        start_line = 3
        end_line = 5
        new_content = "| New | Table | Col3 |\n|-----|-------|------|\n| X   | Y     | Z    |"

        new_lines = (
            lines[: start_line - 1]
            + new_content.split("\n")
            + lines[end_line:]
        )
        result = "\n".join(new_lines)

        assert "| Old | Table |" not in result
        assert "| New | Table | Col3 |" in result
        assert "Line 1" in result
        assert "Line 6" in result
        assert "Line 7" in result

    def test_line_numbers_are_1_indexed(self):
        """Verify that line numbers are 1-indexed."""
        markdown = "Line 1\nLine 2\nLine 3"
        lines = markdown.split("\n")

        # Line 1 is at index 0
        assert lines[0] == "Line 1"
        # Line 2 is at index 1
        assert lines[1] == "Line 2"

        # To replace line 2 (1-indexed), we use [start-1:end]
        start_line = 2
        end_line = 2
        before_content = lines[start_line - 1]
        assert before_content == "Line 2"

    def test_replace_single_line_table(self):
        """Test replacing a single-line table placeholder."""
        original = """Some text
<!-- table:1 -->
More text"""
        lines = original.split("\n")

        start_line = 2
        end_line = 2
        new_table = "| Header |\n|--------|\n| Data   |"

        new_lines = (
            lines[: start_line - 1]
            + new_table.split("\n")
            + lines[end_line:]
        )
        result = "\n".join(new_lines)

        assert "<!-- table:1 -->" not in result
        assert "| Header |" in result
        assert "Some text" in result
        assert "More text" in result
