"""Tests for TablesAgent (Phase 3b: Refine).

Tests cover:
- Table placeholder detection in markdown
- Table validation logic (column consistency, header separator, empty tables)
- High-confidence tables (auto-correction)
- Low-confidence tables (review items)
- Validation loop behavior
- AgentResult output format

Reference: PRD-024 - Tables Agent Merge & Validation Loop
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.tables.tables_agent import (
    TableEnhanceOutput,
    TablePlaceholder,
    TablesAgent,
    TableValidationIssue,
)
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_manifest() -> DocumentManifest:
    """Create sample manifest for testing."""
    return DocumentManifest(
        job_id="test-job-123",
        document_title="Test Document",
        document_type="syllabus",
        total_pages=3,
        heading_tree_json="{}",
        page_features=[
            PageFeatures(page_num=1, has_tables=True, table_count=2),
            PageFeatures(page_num=2, has_tables=False, table_count=0),
            PageFeatures(page_num=3, has_tables=True, table_count=1),
        ],
        required_agents=["tables"],
    )


@pytest.fixture
def sample_pages() -> list[PageData]:
    """Create sample pages for testing."""
    return [
        PageData(page_num=1, image_base64="YWJjMTIz"),  # "abc123" in base64
        PageData(page_num=2, image_base64="ZGVmNDU2"),
        PageData(page_num=3, image_base64="Z2hpNzg5"),
    ]


@pytest.fixture
def mock_usage() -> LLMUsage:
    """Create mock LLM usage."""
    return LLMUsage(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost_cents=1.0,
    )


# =============================================================================
# TablePlaceholder Tests
# =============================================================================


@pytest.mark.unit
class TestTablePlaceholder:
    """Tests for TablePlaceholder model."""

    def test_valid_placeholder(self):
        """Test creating a valid TablePlaceholder."""
        placeholder = TablePlaceholder(
            id="p1:t1",
            full_match="<!-- TABLE:p1:t1 -->\n| A | B |\n|---|---|\n| 1 | 2 |\n<!-- /TABLE:p1:t1 -->",
            start_marker="<!-- TABLE:p1:t1 -->",
            end_marker="<!-- /TABLE:p1:t1 -->",
            content="| A | B |\n|---|---|\n| 1 | 2 |",
            page_num=1,
            table_index=1,
        )
        assert placeholder.id == "p1:t1"
        assert placeholder.page_num == 1
        assert placeholder.table_index == 1

    def test_placeholder_with_complex_content(self):
        """Test placeholder with complex table content."""
        content = (
            "| Header 1 | Header 2 | Header 3 |\n"
            "|----------|----------|----------|\n"
            "| Cell 1 | Cell 2 | Cell 3 |\n"
            "| Cell 4 | Cell 5 | Cell 6 |"
        )
        placeholder = TablePlaceholder(
            id="p2:t3",
            full_match=f"<!-- TABLE:p2:t3 -->\n{content}\n<!-- /TABLE:p2:t3 -->",
            start_marker="<!-- TABLE:p2:t3 -->",
            end_marker="<!-- /TABLE:p2:t3 -->",
            content=content,
            page_num=2,
            table_index=3,
        )
        assert placeholder.page_num == 2
        assert placeholder.table_index == 3
        assert "Header 1" in placeholder.content


# =============================================================================
# Placeholder Detection Tests
# =============================================================================


@pytest.mark.unit
class TestPlaceholderDetection:
    """Tests for _find_table_placeholders method."""

    def test_find_single_placeholder(self):
        """Test finding a single table placeholder."""
        agent = TablesAgent()
        markdown = """# Test

<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->

Some text.
"""

        placeholders = agent._find_table_placeholders(markdown)

        assert len(placeholders) == 1
        assert placeholders[0].page_num == 1
        assert placeholders[0].table_index == 1
        assert "| A | B |" in placeholders[0].content

    def test_find_multiple_placeholders(self):
        """Test finding multiple table placeholders."""
        agent = TablesAgent()
        markdown = """# Test

<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->

Some text.

<!-- TABLE:p1:t2 -->
| C | D |
|---|---|
| 3 | 4 |
<!-- /TABLE:p1:t2 -->

<!-- TABLE:p2:t1 -->
| E | F |
|---|---|
| 5 | 6 |
<!-- /TABLE:p2:t1 -->
"""

        placeholders = agent._find_table_placeholders(markdown)

        assert len(placeholders) == 3
        assert placeholders[0].id == "p1:t1"
        assert placeholders[1].id == "p1:t2"
        assert placeholders[2].id == "p2:t1"

    def test_find_no_placeholders(self):
        """Test with no table placeholders."""
        agent = TablesAgent()
        markdown = "# Test\n\nNo tables here."

        placeholders = agent._find_table_placeholders(markdown)

        assert len(placeholders) == 0

    def test_find_placeholder_with_empty_table(self):
        """Test finding placeholder with empty table content."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->

<!-- /TABLE:p1:t1 -->"""

        placeholders = agent._find_table_placeholders(markdown)

        assert len(placeholders) == 1
        assert placeholders[0].content == ""


# =============================================================================
# Table Validation Tests
# =============================================================================


@pytest.mark.unit
class TestTableValidation:
    """Tests for _validate_table method."""

    def test_valid_table_no_issues(self):
        """Test valid table passes validation."""
        agent = TablesAgent()
        table = """| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |
| Cell 3   | Cell 4   |"""

        issues = agent._validate_table(table)

        assert len(issues) == 0

    def test_too_few_rows_detected(self):
        """Test table with fewer than 2 rows fails."""
        agent = TablesAgent()
        table = "| A | B |"

        issues = agent._validate_table(table)

        assert len(issues) == 1
        assert issues[0].type == "too_few_rows"
        assert issues[0].severity == "critical"

    def test_inconsistent_columns_detected(self):
        """Test inconsistent column counts are detected."""
        agent = TablesAgent()
        table = """| A | B | C |
|---|---|---|
| 1 | 2 |
| 3 | 4 | 5 | 6 |"""

        issues = agent._validate_table(table)

        # Should detect inconsistent columns
        column_issue = next(
            (i for i in issues if i.type == "inconsistent_columns"), None
        )
        assert column_issue is not None
        assert column_issue.severity == "major"

    def test_missing_header_separator_detected(self):
        """Test missing header separator is detected."""
        agent = TablesAgent()
        table = """| Header 1 | Header 2 |
| Cell 1   | Cell 2   |
| Cell 3   | Cell 4   |"""

        issues = agent._validate_table(table)

        separator_issue = next(
            (i for i in issues if i.type == "missing_header_separator"), None
        )
        assert separator_issue is not None
        assert separator_issue.severity == "major"

    def test_single_row_table_fails_validation(self):
        """Test table with only one row fails validation (too_few_rows).

        Tables must have at least 2 rows (header + data).
        """
        agent = TablesAgent()
        table = """| A | B |"""

        issues = agent._validate_table(table)

        # Single row should be caught as too_few_rows
        assert len(issues) > 0
        row_issue = next((i for i in issues if i.type == "too_few_rows"), None)
        assert row_issue is not None
        assert row_issue.severity == "critical"

    def test_valid_table_with_alignment(self):
        """Test valid table with alignment markers passes."""
        agent = TablesAgent()
        table = """| Left | Center | Right |
|:-----|:------:|------:|
| L    |   C    |     R |"""

        issues = agent._validate_table(table)

        # Should pass - alignment markers are valid
        assert len(issues) == 0


# =============================================================================
# Row Count Tests
# =============================================================================


@pytest.mark.unit
class TestRowCount:
    """Tests for _count_rows method."""

    def test_count_rows_simple_table(self):
        """Test counting rows in simple table."""
        agent = TablesAgent()
        table = """| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |
| 5 | 6 |"""

        count = agent._count_rows(table)

        # 5 lines with pipes, minus 1 for separator = 4 (header + 3 data rows)
        assert count == 4

    def test_count_rows_header_only(self):
        """Test counting rows with header only."""
        agent = TablesAgent()
        table = """| A | B |
|---|---|"""

        count = agent._count_rows(table)

        assert count == 1  # Header separator line

    def test_count_rows_empty(self):
        """Test counting rows in empty content."""
        agent = TablesAgent()

        count = agent._count_rows("")

        assert count == 0


# =============================================================================
# Full Processing Tests
# =============================================================================


@pytest.mark.unit
class TestTablesAgentProcess:
    """Tests for full process() method."""

    @pytest.mark.asyncio
    async def test_no_placeholders_returns_empty_result(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test processing with no table placeholders."""
        agent = TablesAgent()
        markdown = "# No tables\n\nJust text content."

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert result.agent_name == "tables"
        assert len(result.observations) == 0
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 0
        assert result.confidence == 1.0
        assert "No table placeholders found" in result.reasoning_summary

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_high_confidence_valid_table_auto_corrects(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test high-confidence valid tables get auto-corrected."""
        # Mock enhancement - valid table, high confidence
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Table structure verified",
                confidence=0.98,
                changes_made=["Verified structure"],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0

        correction = result.auto_corrections[0]
        assert correction.confidence >= 0.95
        assert correction.agent == "tables"

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_low_confidence_creates_review_item(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test low-confidence tables create review items."""
        # Mock enhancement - valid table but low confidence
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Uncertain about content",
                confidence=0.75,  # Below threshold
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1

        review_item = result.review_items[0]
        assert review_item.agent == "tables"
        assert review_item.agent_confidence < 0.95
        assert len(review_item.options) >= 2

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_validation_issues_create_review_item(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test tables with validation issues go to review even with high confidence."""
        # Mock enhancement - high confidence but invalid table
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B | C |\n|---|---|\n| 1 | 2 |",  # Inconsistent columns
                reasoning="Enhanced table",
                confidence=0.98,  # High confidence
                changes_made=["Restructured"],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should go to review due to validation issues
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert "Validation issues" in result.review_items[0].context or \
               "inconsistent" in result.review_items[0].agent_recommendation.lower()

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_validation_loop_retries_on_issues(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test validation loop retries when issues found."""
        # First call returns table with inconsistent columns (validation issue)
        # Second call returns valid table
        mock_enhance.side_effect = [
            (
                TableEnhanceOutput(
                    # Invalid: 3 columns in header, 2 columns in data row
                    table_markdown="| A | B | C |\n|---|---|---|\n| 1 | 2 |",
                    reasoning="First attempt - has issues",
                    confidence=0.90,
                    changes_made=[],
                ),
                mock_usage,
            ),
            (
                TableEnhanceOutput(
                    table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                    reasoning="Fixed after loop",
                    confidence=0.96,
                    changes_made=["Fixed structure"],
                ),
                mock_usage,
            ),
        ]

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should have called enhance twice (validation loop due to column issues)
        assert mock_enhance.call_count == 2
        # Final result should be auto-correction with the valid table
        assert len(result.auto_corrections) == 1


# =============================================================================
# Summary and Confidence Calculation Tests
# =============================================================================


@pytest.mark.unit
class TestSummaryAndConfidence:
    """Tests for summary and confidence calculation."""

    def test_build_summary(self):
        """Test summary generation."""
        agent = TablesAgent()
        placeholders = [
            TablePlaceholder(
                id="1",
                full_match="",
                start_marker="",
                end_marker="",
                content="",
                page_num=1,
                table_index=1,
            ),
            TablePlaceholder(
                id="2",
                full_match="",
                start_marker="",
                end_marker="",
                content="",
                page_num=1,
                table_index=2,
            ),
        ]
        auto_corrections = [MagicMock()]
        review_items = [MagicMock()]

        summary = agent._build_summary(placeholders, auto_corrections, review_items)

        assert "2 tables" in summary
        assert "1 auto-corrected" in summary
        assert "1 need review" in summary

    def test_calculate_confidence_empty(self):
        """Test confidence calculation with no items."""
        agent = TablesAgent()
        confidence = agent._calculate_confidence([], [])
        assert confidence == 1.0

    def test_calculate_confidence_with_items(self):
        """Test confidence calculation with items."""
        agent = TablesAgent()

        auto_correction = MagicMock()
        auto_correction.confidence = 0.98

        review_item = MagicMock()
        review_item.agent_confidence = 0.80

        confidence = agent._calculate_confidence([auto_correction], [review_item])

        # Average of 0.98 and 0.80 = 0.89
        assert 0.88 < confidence < 0.90


# =============================================================================
# Review Item Context Tests
# =============================================================================


@pytest.mark.unit
class TestReviewItemContext:
    """Tests for _build_table_context method."""

    def test_build_context_with_issues(self):
        """Test context includes original, enhanced, and issues."""
        agent = TablesAgent()
        placeholder = TablePlaceholder(
            id="p1:t1",
            full_match="",
            start_marker="<!-- TABLE:p1:t1 -->",
            end_marker="<!-- /TABLE:p1:t1 -->",
            content="| A | B |\n|---|",
            page_num=1,
            table_index=1,
        )
        enhanced = "| A | B |\n|---|---|"
        issues = [
            TableValidationIssue(
                type="inconsistent_columns",
                description="Column counts vary",
                severity="major",
            )
        ]

        context = agent._build_table_context(placeholder, enhanced, issues)

        assert "Original table" in context
        assert "Enhanced table" in context
        assert "Validation issues" in context
        assert "inconsistent_columns" in context

    def test_build_context_no_issues(self):
        """Test context without validation issues."""
        agent = TablesAgent()
        placeholder = TablePlaceholder(
            id="p1:t1",
            full_match="",
            start_marker="<!-- TABLE:p1:t1 -->",
            end_marker="<!-- /TABLE:p1:t1 -->",
            content="| A | B |",
            page_num=1,
            table_index=1,
        )
        enhanced = "| A | B |"
        issues: list[TableValidationIssue] = []

        context = agent._build_table_context(placeholder, enhanced, issues)

        assert "Original table" in context
        assert "Enhanced table" in context
        assert "Validation issues" not in context


# =============================================================================
# Page Data Lookup Tests
# =============================================================================


@pytest.mark.unit
class TestPageDataLookup:
    """Tests for _get_page_data method."""

    def test_get_page_data_found(self):
        """Test getting page data for existing page."""
        agent = TablesAgent()
        pages = [
            PageData(page_num=1, image_base64="abc"),
            PageData(page_num=2, image_base64="def"),
        ]

        result = agent._get_page_data(pages, 2)

        assert result is not None
        assert result.page_num == 2
        assert result.image_base64 == "def"

    def test_get_page_data_not_found(self):
        """Test getting page data for missing page."""
        agent = TablesAgent()
        pages = [
            PageData(page_num=1, image_base64="abc"),
        ]

        result = agent._get_page_data(pages, 5)

        assert result is None


# =============================================================================
# Confidence Boundary Tests
# =============================================================================


@pytest.mark.unit
class TestConfidenceBoundaries:
    """Tests for confidence threshold boundary conditions."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_confidence_exactly_threshold_auto_corrects(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that confidence exactly at 0.95 results in auto-correction."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Valid table",
                confidence=0.95,  # Exactly at threshold
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should auto-correct (>= threshold)
        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0
        assert result.auto_corrections[0].confidence == 0.95

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_confidence_below_threshold_creates_review(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that confidence at 0.9499 (just below 0.95) creates review item."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Slightly uncertain",
                confidence=0.9499,  # Just below threshold
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should create review item (< threshold)
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert result.review_items[0].agent_confidence < 0.95


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.unit
class TestErrorHandling:
    """Tests for error handling in process() method."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_enhancement_fails_creates_review_item(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test that enhancement failure creates review item for manual processing."""
        mock_enhance.side_effect = Exception("Enhancement failed")

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should complete without crashing
        assert result.agent_name == "tables"
        # Should have created review item for failed table
        assert len(result.review_items) == 1
        assert "failed" in result.review_items[0].question.lower()

    @pytest.mark.asyncio
    async def test_missing_page_skips_table(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Test that missing page data skips table processing."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p5:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p5:t1 -->"""

        # Only pages 1-3 provided, but placeholder references page 5
        pages = [
            PageData(page_num=1, image_base64="YWJjMTIz"),
            PageData(page_num=2, image_base64="ZGVmNDU2"),
            PageData(page_num=3, image_base64="Z2hpNzg5"),
        ]

        result = await agent.process(
            markdown=markdown,
            pages=pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Table should be skipped - no observations/corrections/reviews
        assert result.agent_name == "tables"
        assert len(result.observations) == 0


# =============================================================================
# Multiple Tables Tests
# =============================================================================


@pytest.mark.unit
class TestMultipleTables:
    """Tests for processing multiple tables."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_multiple_tables_processed(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test processing multiple tables on same page."""
        # All tables high confidence
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Valid",
                confidence=0.98,
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->

<!-- TABLE:p1:t2 -->
| C | D |
|---|---|
| 3 | 4 |
<!-- /TABLE:p1:t2 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 2
        assert len(result.observations) == 2
        assert "2 tables" in result.reasoning_summary

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_mixed_confidence_tables(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test processing tables with mixed confidence levels."""
        # First table high confidence, second low confidence
        mock_enhance.side_effect = [
            (
                TableEnhanceOutput(
                    table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                    reasoning="High confidence",
                    confidence=0.98,
                    changes_made=[],
                ),
                mock_usage,
            ),
            (
                TableEnhanceOutput(
                    table_markdown="| C | D |\n|---|---|\n| 3 | 4 |",
                    reasoning="Low confidence",
                    confidence=0.70,
                    changes_made=[],
                ),
                mock_usage,
            ),
        ]

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->

<!-- TABLE:p1:t2 -->
| C | D |
|---|---|
| 3 | 4 |
<!-- /TABLE:p1:t2 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 1
        assert "1 auto-corrected" in result.reasoning_summary
        assert "1 need review" in result.reasoning_summary


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestAgentInitialization:
    """Tests for agent initialization and caching."""

    def test_prompts_cached_after_first_call(self):
        """Test prompts are cached after first load."""
        agent = TablesAgent()
        prompts1 = agent._get_prompts()
        prompts2 = agent._get_prompts()

        # Should be the same object reference (cached)
        assert prompts1 is prompts2
        assert "system_prompt" in prompts1
        assert "user_prompt" in prompts1

    @patch("src.agents.tables.tables_agent.create_agent")
    def test_agent_cached_after_first_call(self, mock_create_agent: MagicMock):
        """Test agent instance is cached."""
        mock_create_agent.return_value = MagicMock()

        agent = TablesAgent()
        ai_agent1 = agent._get_agent()
        ai_agent2 = agent._get_agent()

        # Should be the same object reference (cached)
        assert ai_agent1 is ai_agent2
        # create_agent should only be called once (cached on second call)
        assert mock_create_agent.call_count == 1

    def test_agent_has_correct_constants(self):
        """Test agent has correct configuration constants."""
        agent = TablesAgent()

        assert agent.MAX_ITERATIONS == 2
        assert agent.CONFIDENCE_THRESHOLD == 0.95


# =============================================================================
# Validation Loop Exhaustion Tests
# =============================================================================


@pytest.mark.unit
class TestValidationLoopExhaustion:
    """Tests for MAX_ITERATIONS boundary conditions."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_max_iterations_exhausted_with_persistent_issues(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test behavior when validation fails after MAX_ITERATIONS."""
        # Both iterations return invalid tables (inconsistent columns)
        mock_enhance.side_effect = [
            (
                TableEnhanceOutput(
                    table_markdown="| A | B | C |\n|---|---|\n| 1 | 2 |",
                    reasoning="First attempt",
                    confidence=0.90,
                    changes_made=[],
                ),
                mock_usage,
            ),
            (
                TableEnhanceOutput(
                    table_markdown="| A | B |\n|---|\n| 1 | 2 |",  # Still invalid
                    reasoning="Second attempt",
                    confidence=0.85,
                    changes_made=[],
                ),
                mock_usage,
            ),
        ]

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should have called enhance exactly MAX_ITERATIONS times
        assert mock_enhance.call_count == 2
        # Should create review item since validation never passed
        assert len(result.review_items) == 1
        assert len(result.auto_corrections) == 0


# =============================================================================
# Cost and Timing Accumulation Tests
# =============================================================================


@pytest.mark.unit
class TestCostAndTimingAccumulation:
    """Tests for cost and timing tracking."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_cost_accumulates_across_tables(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test costs are summed across multiple tables."""
        # Different costs for each table
        mock_enhance.side_effect = [
            (
                TableEnhanceOutput(
                    table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                    reasoning="Valid",
                    confidence=0.98,
                    changes_made=[],
                ),
                LLMUsage(
                    input_tokens=100,
                    output_tokens=50,
                    total_tokens=150,
                    estimated_cost_cents=1.5,
                ),
            ),
            (
                TableEnhanceOutput(
                    table_markdown="| C | D |\n|---|---|\n| 3 | 4 |",
                    reasoning="Valid",
                    confidence=0.96,
                    changes_made=[],
                ),
                LLMUsage(
                    input_tokens=120,
                    output_tokens=60,
                    total_tokens=180,
                    estimated_cost_cents=2.3,
                ),
            ),
        ]

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->

<!-- TABLE:p1:t2 -->
| C | D |
|---|---|
| 3 | 4 |
<!-- /TABLE:p1:t2 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Costs should be summed
        assert result.cost_cents == 3.8  # 1.5 + 2.3
        # Time should be positive
        assert result.time_seconds > 0

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_time_is_tracked(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that processing time is tracked."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Valid",
                confidence=0.98,
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Time should be positive (processing took some time)
        assert result.time_seconds >= 0


# =============================================================================
# Enhanced Content Tracking Tests
# =============================================================================


@pytest.mark.unit
class TestEnhancedContentTracking:
    """Tests for enhanced_content dictionary in AgentResult."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_enhanced_content_stored_for_auto_corrections(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test enhanced content is stored with table ID as key."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| Enhanced A | Enhanced B |\n|---|---|\n| 1 | 2 |",
                reasoning="Valid",
                confidence=0.98,
                changes_made=["Enhanced headers"],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Verify enhanced content is stored
        assert result.enhanced_content is not None
        assert "p1:t1" in result.enhanced_content
        assert "Enhanced A" in result.enhanced_content["p1:t1"]

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_enhanced_content_not_stored_for_review_items(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test enhanced content is NOT stored for review items."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Low confidence",
                confidence=0.70,  # Below threshold
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should have review item, not auto-correction
        assert len(result.review_items) == 1
        assert len(result.auto_corrections) == 0
        # Enhanced content should be None or not include this table
        assert result.enhanced_content is None or "p1:t1" not in result.enhanced_content


# =============================================================================
# Observation Creation Tests
# =============================================================================


@pytest.mark.unit
class TestObservationCreation:
    """Tests for _create_observation method."""

    def test_create_observation_fields(self):
        """Test observation is created with correct fields."""
        agent = TablesAgent()
        placeholder = TablePlaceholder(
            id="p2:t3",
            full_match="<!-- TABLE:p2:t3 -->\n| A | B |\n|---|---|\n| 1 | 2 |\n<!-- /TABLE:p2:t3 -->",
            start_marker="<!-- TABLE:p2:t3 -->",
            end_marker="<!-- /TABLE:p2:t3 -->",
            content="| A | B |\n|---|---|\n| 1 | 2 |",
            page_num=2,
            table_index=3,
        )

        obs = agent._create_observation(
            placeholder=placeholder,
            confidence=0.87,
            job_id="test-job-456",
        )

        assert obs.job_id == "test-job-456"
        assert obs.agent == "tables"
        assert obs.source == "agent"
        assert obs.confidence == 0.87
        assert obs.severity == "major"
        assert obs.category == "table"
        assert "Table on page 2" in obs.visual_description
        assert obs.location.location_type == "element"
        assert obs.location.page_num == 2
        assert "p2:t3" in obs.location.value

    def test_observation_includes_row_count(self):
        """Test observation markup_description includes row count."""
        agent = TablesAgent()
        placeholder = TablePlaceholder(
            id="p1:t1",
            full_match="",
            start_marker="<!-- TABLE:p1:t1 -->",
            end_marker="<!-- /TABLE:p1:t1 -->",
            content="| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |",
            page_num=1,
            table_index=1,
        )

        obs = agent._create_observation(
            placeholder=placeholder,
            confidence=0.9,
            job_id="test-job",
        )

        # Should mention row count (4 rows: header + 3 data rows)
        assert "4 rows" in obs.markup_description


# =============================================================================
# Review Item Structure Tests
# =============================================================================


@pytest.mark.unit
class TestReviewItemStructure:
    """Tests for review item structure and options."""

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_review_item_has_all_options(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test review items include all three options."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
                reasoning="Uncertain about content",
                confidence=0.70,
                changes_made=[],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.review_items) == 1
        review = result.review_items[0]

        # Verify all three options exist
        assert len(review.options) == 3
        option_ids = [opt.id for opt in review.options]
        assert "accept" in option_ids
        assert "keep_original" in option_ids
        assert "other" in option_ids

        # Verify accept option has replacement text with markers
        accept_opt = next(o for o in review.options if o.id == "accept")
        assert accept_opt.action == "replace"
        assert accept_opt.replacement_text is not None
        assert "<!-- TABLE:p1:t1 -->" in accept_opt.replacement_text
        assert "<!-- /TABLE:p1:t1 -->" in accept_opt.replacement_text

    @pytest.mark.asyncio
    @patch.object(TablesAgent, "_enhance_table")
    async def test_review_item_context_includes_original_and_enhanced(
        self,
        mock_enhance: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test review item context shows both original and enhanced tables."""
        mock_enhance.return_value = (
            TableEnhanceOutput(
                table_markdown="| Enhanced A | Enhanced B |\n|---|---|\n| 1 | 2 |",
                reasoning="Made improvements",
                confidence=0.80,
                changes_made=["Enhanced headers"],
            ),
            mock_usage,
        )

        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| Original A | Original B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.review_items) == 1
        review = result.review_items[0]

        # Context should include both versions
        assert "Original table" in review.context
        assert "Enhanced table" in review.context
        assert "Original A" in review.context
        assert "Enhanced A" in review.context


# =============================================================================
# Placeholder Regex Edge Cases
# =============================================================================


@pytest.mark.unit
class TestPlaceholderRegexEdgeCases:
    """Tests for placeholder regex edge cases."""

    def test_mismatched_placeholder_markers(self):
        """Test behavior with mismatched open/close markers."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
<!-- /TABLE:p1:t2 -->"""  # Wrong closing marker (t2 instead of t1)

        placeholders = agent._find_table_placeholders(markdown)
        # Should not match (IDs don't match due to backreference)
        assert len(placeholders) == 0

    def test_unclosed_placeholder(self):
        """Test handling of unclosed placeholder."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
No closing marker here"""

        placeholders = agent._find_table_placeholders(markdown)
        assert len(placeholders) == 0

    def test_placeholder_with_extra_whitespace(self):
        """Test placeholder with whitespace in content is handled."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->

| A | B |
|---|---|
| 1 | 2 |

<!-- /TABLE:p1:t1 -->"""

        placeholders = agent._find_table_placeholders(markdown)
        assert len(placeholders) == 1
        # Content should be stripped
        assert "| A | B |" in placeholders[0].content


# =============================================================================
# Page Image Edge Cases
# =============================================================================


@pytest.mark.unit
class TestPageImageEdgeCases:
    """Tests for page image data edge cases."""

    @pytest.mark.asyncio
    async def test_page_exists_but_empty_image_data(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Test handling when page exists but image_base64 is empty string."""
        agent = TablesAgent()
        markdown = """<!-- TABLE:p1:t1 -->
| A | B |
|---|---|
| 1 | 2 |
<!-- /TABLE:p1:t1 -->"""

        # Page exists but empty image data
        pages = [PageData(page_num=1, image_base64="")]

        result = await agent.process(
            markdown=markdown,
            pages=pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should skip processing (empty image_base64 is falsy)
        assert len(result.observations) == 0
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 0
