"""Tests for vision-based table accuracy verification.

This tests the verify_table_accuracy_vision function that compares
transcribed markdown tables against source PDF images.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.plan_verification import (
    TableMatchResult,
    _extract_nth_table,
    verify_table_accuracy_vision,
)


class TestExtractNthTable:
    """Tests for _extract_nth_table helper function."""

    def test_extract_first_table(self):
        """Test extracting the first table from markdown."""
        markdown = """
Some text

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |

More text

| X | Y | Z |
|---|---|---|
| 1 | 2 | 3 |
"""
        result = _extract_nth_table(markdown, 1)

        assert result is not None
        assert "Column A" in result
        assert "Value 1" in result

    def test_extract_second_table(self):
        """Test extracting the second table from markdown."""
        markdown = """
| A | B |
|---|---|
| 1 | 2 |

Text between

| X | Y | Z |
|---|---|---|
| a | b | c |
"""
        result = _extract_nth_table(markdown, 2)

        assert result is not None
        assert "X | Y | Z" in result
        assert "a | b | c" in result

    def test_table_not_found(self):
        """Test returning None when table doesn't exist."""
        markdown = """
No tables here.
Just regular text.
"""
        result = _extract_nth_table(markdown, 1)

        assert result is None

    def test_index_out_of_range(self):
        """Test returning None when index is beyond available tables."""
        markdown = """
| A | B |
|---|---|
| 1 | 2 |
"""
        result = _extract_nth_table(markdown, 5)

        assert result is None

    def test_extract_multiline_table(self):
        """Test extracting a table with many rows."""
        markdown = """
| Product | Price | Quantity |
|---------|-------|----------|
| Apple   | $1.00 | 10       |
| Banana  | $0.50 | 20       |
| Orange  | $1.50 | 5        |
| Grape   | $3.00 | 15       |
"""
        result = _extract_nth_table(markdown, 1)

        assert result is not None
        assert "Apple" in result
        assert "Grape" in result
        assert result.count("|") >= 20  # 5 rows * 4 pipes each


class TestTableMatchResult:
    """Tests for the TableMatchResult model."""

    def test_matching_table(self):
        """Test result for a matching table."""
        result = TableMatchResult(
            matches=True,
            mismatches=[],
            confidence=0.95,
        )

        assert result.matches is True
        assert len(result.mismatches) == 0

    def test_mismatched_table(self):
        """Test result for a table with errors."""
        result = TableMatchResult(
            matches=False,
            mismatches=[
                "Cell (2,1): Expected '42' but found '24'",
                "Missing row 5",
            ],
            confidence=0.8,
        )

        assert result.matches is False
        assert len(result.mismatches) == 2


class TestVerifyTableAccuracyVision:
    """Tests for verify_table_accuracy_vision function."""

    @pytest.fixture
    def mock_plan(self):
        """Create a mock DocumentPlan with table information."""
        mock_table = MagicMock()
        mock_table.table_index = 1

        mock_page_plan = MagicMock()
        mock_page_plan.tables = [mock_table]

        mock_plan = MagicMock()
        mock_plan.pages = {1: mock_page_plan}

        return mock_plan

    @pytest.fixture
    def mock_page_image(self):
        """Create a mock PIL Image."""
        mock_img = MagicMock()
        mock_img.save = MagicMock()
        return mock_img

    @pytest.mark.asyncio
    async def test_no_tables_returns_empty(self):
        """Test that documents without tables return empty issues."""
        mock_plan = MagicMock()
        mock_plan.pages = {}  # No pages with tables

        issues = await verify_table_accuracy_vision(
            page_markdowns={},
            page_images={},
            element_bboxes={},
            page_width=800.0,
            plan=mock_plan,
        )

        assert issues == []

    @pytest.mark.asyncio
    async def test_missing_page_image_skipped(self, mock_plan):
        """Test that tables without page images are skipped."""
        page_markdowns = {
            1: "| A | B |\n|---|---|\n| 1 | 2 |",
        }

        issues = await verify_table_accuracy_vision(
            page_markdowns=page_markdowns,
            page_images={},  # No images
            element_bboxes={(1, "table", 1): (0, 0, 100, 100)},
            page_width=800.0,
            plan=mock_plan,
        )

        # Should skip verification, not error
        assert issues == []

    @pytest.mark.asyncio
    async def test_missing_bbox_skipped(self, mock_plan, mock_page_image):
        """Test that tables without bounding boxes are skipped."""
        page_markdowns = {
            1: "| A | B |\n|---|---|\n| 1 | 2 |",
        }

        issues = await verify_table_accuracy_vision(
            page_markdowns=page_markdowns,
            page_images={1: mock_page_image},
            element_bboxes={},  # No bboxes
            page_width=800.0,
            plan=mock_plan,
        )

        # Should skip verification, not error
        assert issues == []

    @pytest.mark.asyncio
    async def test_matching_table_no_issues(self, mock_plan, mock_page_image):
        """Test that matching tables produce no issues."""
        page_markdowns = {
            1: "| A | B |\n|---|---|\n| 1 | 2 |",
        }

        # Mock the vision agent to return a match
        mock_result = MagicMock()
        mock_result.output = TableMatchResult(matches=True, mismatches=[])

        with patch("src.utils.image_utils.crop_element") as mock_crop:
            mock_crop.return_value = mock_page_image

            # Need to patch both BedrockConverseModel and Agent
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                with patch("pydantic_ai.Agent") as mock_agent_class:
                    mock_agent = AsyncMock()
                    mock_agent.run = AsyncMock(return_value=mock_result)
                    mock_agent_class.return_value = mock_agent

                    issues = await verify_table_accuracy_vision(
                        page_markdowns=page_markdowns,
                        page_images={1: mock_page_image},
                        element_bboxes={(1, "table", 1): (0, 0, 100, 100)},
                        page_width=800.0,
                        plan=mock_plan,
                    )

        assert issues == []

    @pytest.mark.asyncio
    async def test_mismatched_table_returns_issue(self, mock_plan, mock_page_image):
        """Test that mismatched tables produce issues."""
        page_markdowns = {
            1: "| A | B |\n|---|---|\n| 1 | 2 |",
        }

        # Mock the vision agent to return a mismatch
        mock_result = MagicMock()
        mock_result.output = TableMatchResult(
            matches=False,
            mismatches=["Cell (1,1): Expected '42' but found '1'"],
        )

        with patch("src.utils.image_utils.crop_element") as mock_crop:
            mock_crop.return_value = mock_page_image

            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                with patch("pydantic_ai.Agent") as mock_agent_class:
                    mock_agent = AsyncMock()
                    mock_agent.run = AsyncMock(return_value=mock_result)
                    mock_agent_class.return_value = mock_agent

                    issues = await verify_table_accuracy_vision(
                        page_markdowns=page_markdowns,
                        page_images={1: mock_page_image},
                        element_bboxes={(1, "table", 1): (0, 0, 100, 100)},
                        page_width=800.0,
                        plan=mock_plan,
                    )

        assert len(issues) == 1
        assert "Page 1 table 1" in issues[0]
        assert "data mismatch" in issues[0]

    @pytest.mark.asyncio
    async def test_vision_api_error_handled_gracefully(self, mock_plan, mock_page_image):
        """Test that vision API errors don't crash verification."""
        page_markdowns = {
            1: "| A | B |\n|---|---|\n| 1 | 2 |",
        }

        with patch("src.utils.image_utils.crop_element") as mock_crop:
            mock_crop.return_value = mock_page_image

            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                with patch("pydantic_ai.Agent") as mock_agent_class:
                    mock_agent = AsyncMock()
                    mock_agent.run = AsyncMock(side_effect=Exception("API Error"))
                    mock_agent_class.return_value = mock_agent

                    # Should not raise, just return empty issues
                    issues = await verify_table_accuracy_vision(
                        page_markdowns=page_markdowns,
                        page_images={1: mock_page_image},
                        element_bboxes={(1, "table", 1): (0, 0, 100, 100)},
                        page_width=800.0,
                        plan=mock_plan,
                    )

        # Error is logged but doesn't cause issues
        assert issues == []
