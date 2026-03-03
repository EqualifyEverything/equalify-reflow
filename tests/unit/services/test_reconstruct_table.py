"""Unit tests for the reconstruct_table tool in page correction agents."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.services.pipeline_viewer import (
    PipelineViewerService,
    _extract_tables_from_markdown,
)
from src.services.pipeline_viewer_models import (
    DocumentChange,
    FigureData,
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PageCorrectionResult,
    PipelineViewerResult,
    StructureResult,
    TableData,
    TableReconstructionResult,
)


# Patch targets — these are imported lazily inside methods
_BEDROCK_PATCH = "pydantic_ai.models.bedrock.BedrockConverseModel"
_AGENT_PATCH = "pydantic_ai.Agent"


# ---------------------------------------------------------------------------
# Sample markdown tables for testing
# ---------------------------------------------------------------------------

SIMPLE_TABLE_MD = """\
| Name | Age | City |
|------|-----|------|
| Alice | 30 | NYC |
| Bob | 25 | LA |"""

TWO_TABLES_MD = """\
Some text before

| Col A | Col B |
|-------|-------|
| 1 | 2 |

Some text between

| X | Y | Z |
|---|---|---|
| a | b | c |
| d | e | f |

Some text after"""

NO_TABLE_MD = """\
# Heading

Just a paragraph with no tables at all.

- List item 1
- List item 2
"""

MALFORMED_TABLE_MD = """\
| Name | Age
Alice | 30
Bob | 25
"""


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def sample_table():
    return TableData(
        ref_id="table-1",
        page_number=1,
        markdown_content=SIMPLE_TABLE_MD,
    )


@pytest.fixture
def sample_table_2():
    return TableData(
        ref_id="table-2",
        page_number=2,
        markdown_content="| X | Y |\n|---|---|\n| 1 | 2 |",
    )


def _make_mock_agent():
    """Create a mock Agent that tracks tool_plain registrations."""
    mock_agent = MagicMock()
    mock_agent._registered_tools = []

    def tool_plain(func=None, **kwargs):
        if func is not None:
            mock_agent._registered_tools.append(func.__name__)
            return func

        def decorator(f):
            mock_agent._registered_tools.append(f.__name__)
            return f
        return decorator

    mock_agent.tool_plain = tool_plain

    async def mock_run(user_parts, **kwargs):
        result = MagicMock()
        result.output = None
        usage = MagicMock()
        usage.request_tokens = 100
        usage.response_tokens = 50
        result.usage.return_value = usage
        return result

    mock_agent.run = AsyncMock(side_effect=mock_run)
    return mock_agent


# ---------------------------------------------------------------------------
# _extract_tables_from_markdown tests
# ---------------------------------------------------------------------------


class TestExtractTablesFromMarkdown:
    """Test the _extract_tables_from_markdown helper function."""

    def test_single_table(self):
        """Parse a markdown string with one table — returns 1 TableData."""
        tables = _extract_tables_from_markdown(SIMPLE_TABLE_MD, page_number=1)
        assert len(tables) == 1
        assert tables[0].ref_id == "table-1"
        assert tables[0].page_number == 1
        assert "Alice" in tables[0].markdown_content
        assert "Bob" in tables[0].markdown_content

    def test_two_tables(self):
        """Parse with two tables — returns 2 TableData with correct ref_ids."""
        tables = _extract_tables_from_markdown(TWO_TABLES_MD, page_number=3)
        assert len(tables) == 2
        assert tables[0].ref_id == "table-1"
        assert tables[1].ref_id == "table-2"
        assert tables[0].page_number == 3
        assert tables[1].page_number == 3
        assert "Col A" in tables[0].markdown_content
        assert "X" in tables[1].markdown_content

    def test_no_tables(self):
        """Parse with no tables — returns empty list."""
        tables = _extract_tables_from_markdown(NO_TABLE_MD, page_number=1)
        assert tables == []

    def test_malformed_table_skipped(self):
        """Malformed table (no separator row) — not matched."""
        tables = _extract_tables_from_markdown(MALFORMED_TABLE_MD, page_number=1)
        assert tables == []

    def test_empty_string(self):
        """Empty input — returns empty list."""
        tables = _extract_tables_from_markdown("", page_number=1)
        assert tables == []

    def test_table_content_stripped(self):
        """Table markdown_content should have trailing newlines stripped."""
        md = "| A |\n|---|\n| 1 |\n\nSome text"
        tables = _extract_tables_from_markdown(md, page_number=1)
        assert len(tables) == 1
        assert not tables[0].markdown_content.endswith("\n")


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


class TestReconstructTableToolRegistration:
    """Test that reconstruct_table is conditionally registered in page agent."""

    @pytest.mark.asyncio
    async def test_tool_registered_when_has_tables_and_tables_found(
        self, service, sample_table
    ):
        """reconstruct_table should be registered when has_tables=True and tables exist."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                structure=structure,
                page_tables=[sample_table],
            )

        assert "reconstruct_table" in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_has_tables_false(self, service):
        """reconstruct_table should NOT be registered when has_tables=False."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=False),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Hello\n\nNo tables here",
                page_image_b64="AAAA",
                structure=structure,
            )

        assert "reconstruct_table" not in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_has_tables_but_no_page_tables(
        self, service
    ):
        """reconstruct_table should NOT be registered when has_tables=True but page_tables is None."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Hello\n\nText only",
                page_image_b64="AAAA",
                structure=structure,
                page_tables=None,
            )

        assert "reconstruct_table" not in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_table_reconstructor_token_fields_on_result(
        self, service, sample_table
    ):
        """Result should include table_reconstructor token fields (0 when no actual calls)."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_page_agent(
                page_num=1,
                page_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                structure=structure,
                page_tables=[sample_table],
            )

        assert result.table_reconstructor_input_tokens == 0
        assert result.table_reconstructor_output_tokens == 0


# ---------------------------------------------------------------------------
# Tool error handling tests
# ---------------------------------------------------------------------------


class TestReconstructTableToolErrors:
    """Test error handling in the reconstruct_table tool."""

    @pytest.mark.asyncio
    async def test_invalid_ref_id(self, service, sample_table):
        """Invalid ref_id should return error message with available refs."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        # We need to capture the registered tool function so we can call it directly
        registered_tools = {}

        def tool_plain(func=None, **kwargs):
            if func is not None:
                mock_agent._registered_tools.append(func.__name__)
                registered_tools[func.__name__] = func
                return func

            def decorator(f):
                mock_agent._registered_tools.append(f.__name__)
                registered_tools[f.__name__] = f
                return f
            return decorator

        mock_agent.tool_plain = tool_plain

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                structure=structure,
                page_tables=[sample_table],
            )

        assert "reconstruct_table" in registered_tools
        result = await registered_tools["reconstruct_table"]("nonexistent-table")
        assert "ERROR" in result
        assert "nonexistent-table" in result
        assert "table-1" in result  # available ref

    @pytest.mark.asyncio
    async def test_max_calls_exceeded(self, service, sample_table):
        """Max calls exceeded should return error message."""
        mock_agent = _make_mock_agent()

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        registered_tools = {}

        def tool_plain(func=None, **kwargs):
            if func is not None:
                mock_agent._registered_tools.append(func.__name__)
                registered_tools[func.__name__] = func
                return func

            def decorator(f):
                mock_agent._registered_tools.append(f.__name__)
                registered_tools[f.__name__] = f
                return f
            return decorator

        mock_agent.tool_plain = tool_plain

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                structure=structure,
                page_tables=[sample_table],
            )

        # Simulate exceeding the max calls by calling 11 times
        # The tool increments the counter before checking the max
        # The max is 10, so calls 1-10 are allowed, call 11 should fail
        reconstructor_mock_result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| A | B |\n|---|---|\n| 1 | 2 |",
            caption="Test table",
            confidence="high",
        )

        mock_subagent_result = MagicMock()
        mock_subagent_result.output = reconstructor_mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_subagent_result.usage.return_value = mock_usage

        mock_sub_agent = MagicMock()
        mock_sub_agent.run = AsyncMock(return_value=mock_subagent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_sub_agent):
            for _ in range(10):
                await registered_tools["reconstruct_table"]("table-1")

        # 11th call should hit the limit
        result = await registered_tools["reconstruct_table"]("table-1")
        assert "ERROR" in result
        assert "Maximum" in result


# ---------------------------------------------------------------------------
# Subagent call tests
# ---------------------------------------------------------------------------


class TestTableReconstructorSubagent:
    """Test the _run_table_reconstructor method."""

    @pytest.mark.asyncio
    async def test_successful_markdown_reconstruction(self, service, sample_table):
        """Should return formatted markdown result string."""
        mock_result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| Name | Age | City |\n|------|-----|------|\n| Alice | 30 | NYC |\n| Bob | 25 | LA |",
            caption="People and their ages and cities",
            confidence="high",
            reasoning="Table is simple with single header row",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 500
        mock_usage.response_tokens = 100
        mock_agent_result.usage.return_value = mock_usage

        token_updates = []

        def track_tokens(inp, out):
            token_updates.append((inp, out))

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=f"# Page\n\n{SIMPLE_TABLE_MD}\n\nMore text",
                page_image_b64="AAAA",
                update_tokens=track_tokens,
            )

        assert "table-1" in result
        assert "markdown" in result.lower()
        assert "People and their ages and cities" in result
        assert token_updates == [(500, 100)]

    @pytest.mark.asyncio
    async def test_successful_html_reconstruction(self, service, sample_table):
        """Complex table should return HTML result string with caption mention."""
        html_content = (
            '<table>\n<caption>Student enrollment by department</caption>\n'
            '<thead><tr><th scope="col">Dept</th><th scope="col">Count</th></tr></thead>\n'
            '<tbody><tr><th scope="row">CS</th><td>450</td></tr></tbody>\n</table>'
        )
        mock_result = TableReconstructionResult(
            table_format="html",
            reconstructed_content=html_content,
            caption="Student enrollment by department",
            has_row_headers=True,
            has_merged_cells=False,
            confidence="high",
            reasoning="Table has row headers in first column",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 600
        mock_usage.response_tokens = 200
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=f"# Page\n\n{SIMPLE_TABLE_MD}\n\nMore text",
                page_image_b64="AAAA",
                update_tokens=lambda i, o: None,
            )

        assert "table-1" in result
        assert "HTML" in result
        assert "row headers" in result
        assert "Student enrollment by department" in result
        assert "<table>" in result

    @pytest.mark.asyncio
    async def test_subagent_failure_returns_error(self, service, sample_table):
        """If the reconstructor agent raises, should return error string."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Model timeout"))

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                update_tokens=lambda i, o: None,
            )

        assert "failed" in result.lower()
        assert "table-1" in result

    @pytest.mark.asyncio
    async def test_page_image_passed_as_binary_content(self, service, sample_table):
        """Verify BinaryContent is passed with page image."""
        from pydantic_ai.messages import BinaryContent

        captured_user_parts = []

        mock_result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| A |\n|---|\n| 1 |",
            caption="Test",
            confidence="high",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        page_b64 = base64.b64encode(b"page-image-data").decode()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=f"# Page\n\n{SIMPLE_TABLE_MD}\n\nMore text",
                page_image_b64=page_b64,
                update_tokens=lambda i, o: None,
            )

        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 1

    @pytest.mark.asyncio
    async def test_no_image_when_page_image_none(self, service, sample_table):
        """When page_image_b64 is None, no BinaryContent should be sent."""
        from pydantic_ai.messages import BinaryContent

        captured_user_parts = []

        mock_result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| A |\n|---|\n| 1 |",
            caption="Test",
            confidence="high",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=SIMPLE_TABLE_MD,
                page_image_b64=None,
                update_tokens=lambda i, o: None,
            )

        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 0

    @pytest.mark.asyncio
    async def test_user_message_contains_table_ref_id(self, service, sample_table):
        """The user message should contain the table ref_id."""
        captured_user_parts = []

        mock_result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| A |\n|---|\n| 1 |",
            caption="Test",
            confidence="high",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=f"# Page\n\n{SIMPLE_TABLE_MD}\n\nMore text",
                page_image_b64="AAAA",
                update_tokens=lambda i, o: None,
            )

        text_parts = [p for p in captured_user_parts if isinstance(p, str)]
        assert any("table-1" in p for p in text_parts)

    @pytest.mark.asyncio
    async def test_html_result_mentions_merged_cells(self, service, sample_table):
        """HTML result with merged cells should mention that in the output."""
        mock_result = TableReconstructionResult(
            table_format="html",
            reconstructed_content='<table><caption>Test</caption><tbody><tr><td colspan="2">merged</td></tr></tbody></table>',
            caption="Test table with merged cells",
            has_row_headers=False,
            has_merged_cells=True,
            confidence="medium",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_table_reconstructor(
                table=sample_table,
                current_markdown=SIMPLE_TABLE_MD,
                page_image_b64="AAAA",
                update_tokens=lambda i, o: None,
            )

        assert "merged cells" in result


# ---------------------------------------------------------------------------
# Token accumulation tests
# ---------------------------------------------------------------------------


class TestTableReconstructorTokenTracking:
    """Test table reconstructor token aggregation in _step_page_content."""

    @pytest.mark.asyncio
    async def test_table_tokens_aggregated(self, service):
        """Table reconstructor tokens should be included in step totals."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": f"# Intro\n\n{SIMPLE_TABLE_MD}\n\n# Methods\n\nGoodbye"},
            page_images={"1": "AAAA", "2": "BBBB"},
            page_markdowns={
                "v0": {
                    "1": f"# Intro\n\n{SIMPLE_TABLE_MD}",
                    "2": "# Methods\n\nGoodbye",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
                2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            },
            outline=[
                OutlineEntry(level=2, text="Intro", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
            footnotes=[],
        )

        async def mock_run_page_agent(page_num, **kwargs):
            table_in = 400 if page_num == 1 else 0
            table_out = 80 if page_num == 1 else 0
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=200,
                output_tokens=50,
                table_reconstructor_input_tokens=table_in,
                table_reconstructor_output_tokens=table_out,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        step = result.steps[-1]
        # Page agent tokens: 200*2=400 input, 50*2=100 output
        # Table reconstructor tokens: 400 input, 80 output (only page 1)
        # Combined: 800 input, 180 output
        assert step.input_tokens == 800
        assert step.output_tokens == 180
        assert step.metadata["table_reconstructor_input_tokens"] == 400
        assert step.metadata["table_reconstructor_output_tokens"] == 80

    @pytest.mark.asyncio
    async def test_table_tokens_zero_when_no_tables(self, service):
        """Table reconstructor tokens should be 0 when no tables in document."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Intro\n\nHello world"},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": "# Intro\n\nHello world"}},
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        async def mock_run_page_agent(page_num, **kwargs):
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=200,
                output_tokens=50,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        step = result.steps[-1]
        assert step.metadata["table_reconstructor_input_tokens"] == 0
        assert step.metadata["table_reconstructor_output_tokens"] == 0


# ---------------------------------------------------------------------------
# Model default tests
# ---------------------------------------------------------------------------


class TestTableReconstructionModelDefaults:
    """Test model defaults for table reconstruction fields."""

    def test_page_correction_result_defaults(self):
        """PageCorrectionResult should default table reconstructor tokens to 0."""
        result = PageCorrectionResult(
            page=1,
            corrected_markdown="test",
        )
        assert result.table_reconstructor_input_tokens == 0
        assert result.table_reconstructor_output_tokens == 0

    def test_table_reconstruction_result_defaults(self):
        """TableReconstructionResult should have sensible defaults."""
        result = TableReconstructionResult(
            table_format="markdown",
            reconstructed_content="| A |\n|---|\n| 1 |",
            caption="Test table",
        )
        assert result.has_row_headers is False
        assert result.has_merged_cells is False
        assert result.confidence == "high"
        assert result.reasoning == ""

    def test_table_data_model(self):
        """TableData should store ref_id, page_number, and markdown_content."""
        td = TableData(
            ref_id="table-1",
            page_number=5,
            markdown_content="| A |\n|---|\n| 1 |",
        )
        assert td.ref_id == "table-1"
        assert td.page_number == 5
        assert "| A |" in td.markdown_content

    def test_table_reconstruction_result_html_format(self):
        """TableReconstructionResult should accept html format."""
        result = TableReconstructionResult(
            table_format="html",
            reconstructed_content="<table><tr><td>1</td></tr></table>",
            caption="A table",
            has_row_headers=True,
            has_merged_cells=True,
            confidence="medium",
            reasoning="Complex table with merged cells",
        )
        assert result.table_format == "html"
        assert result.has_row_headers is True
        assert result.has_merged_cells is True


# ---------------------------------------------------------------------------
# Table filtering in _step_page_content tests
# ---------------------------------------------------------------------------


class TestPageTableFiltering:
    """Test that tables are correctly extracted and passed per page."""

    @pytest.mark.asyncio
    async def test_tables_passed_to_page_agent(self, service):
        """Pages with tables should get page_tables argument."""
        table_md = "| Col A | Col B |\n|-------|-------|\n| 1 | 2 |"
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": f"# Intro\n\n{table_md}\n\n# Methods\n\nGoodbye"},
            page_images={"1": "AAAA", "2": "BBBB"},
            page_markdowns={
                "v0": {
                    "1": f"# Intro\n\n{table_md}",
                    "2": "# Methods\n\nGoodbye",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True),
                2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            },
            outline=[
                OutlineEntry(level=2, text="Intro", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
            footnotes=[],
        )

        captured_tables: dict[int, list | None] = {}

        async def mock_run_page_agent(page_num, **kwargs):
            pt = kwargs.get("page_tables")
            if pt:
                captured_tables[page_num] = [t.ref_id for t in pt]
            else:
                captured_tables[page_num] = None
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        assert captured_tables[1] == ["table-1"]
        assert captured_tables[2] is None

    @pytest.mark.asyncio
    async def test_no_tables_passes_none(self, service):
        """Pages with no tables should get None for page_tables."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Intro\n\nHello"},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": "# Intro\n\nHello"}},
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        captured_tables = {}

        async def mock_run_page_agent(page_num, **kwargs):
            captured_tables[page_num] = kwargs.get("page_tables")
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        assert captured_tables[1] is None


# ---------------------------------------------------------------------------
# User message builder tests
# ---------------------------------------------------------------------------


class TestBuildTableUserMessage:
    """Test the build_table_user_message helper."""

    def test_standard_message(self):
        from src.agents.prompts.table_reconstruction import (
            build_table_user_message,
        )

        msg = build_table_user_message(
            table_markdown="| A | B |\n|---|---|\n| 1 | 2 |",
            surrounding_text="some context around the table",
            ref_id="table-1",
        )
        assert "table-1" in msg
        assert "| A | B |" in msg
        assert "Surrounding text" in msg
        assert "Classify this table" in msg

    def test_no_surrounding_text(self):
        from src.agents.prompts.table_reconstruction import (
            build_table_user_message,
        )

        msg = build_table_user_message(
            table_markdown="| A |\n|---|\n| 1 |",
            surrounding_text="",
            ref_id="table-2",
        )
        assert "table-2" in msg
        assert "Surrounding text" not in msg

    def test_message_contains_instructions(self):
        from src.agents.prompts.table_reconstruction import (
            build_table_user_message,
        )

        msg = build_table_user_message(
            table_markdown="| X |\n|---|\n| y |",
            surrounding_text="context",
            ref_id="table-3",
        )
        assert "Compare the table" in msg
        assert "simple or complex" in msg
        assert "Preserve all cell content" in msg
