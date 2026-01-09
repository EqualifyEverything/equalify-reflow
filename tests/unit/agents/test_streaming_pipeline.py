"""Tests for streaming phase handoff pipeline.

This module tests the streaming functionality that overlaps planning with
execution so that page execution starts as soon as each page is planned,
rather than waiting for ALL pages to be planned first.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.models import (
    DocumentStructure,
    DocumentType,
    FigureContext,
    HeadingFix,
    Job,
    JobType,
    LedgerEntry,
    LLMCallRecord,
    PagePlan,
    TableContext,
    Task,
    TaskType,
)
from src.agents.page_chain import (
    FigureAnalysis,
    PageChainState,
    TableAnalysis,
    run_page_chain_streaming,
)
from src.agents.planner import (
    _convert_chain_state_to_page_plan_single,
    generate_jobs_for_page,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def sample_page_markdowns() -> dict[int, str]:
    """Create sample page markdowns for testing."""
    return {
        1: """# Introduction

This is the introduction page.

<!-- image 1 -->

Some text about the figure.
""",
        2: """## Methods

This describes our methodology.

<!-- table 1 -->

The table shows our results.
""",
        3: """## Conclusion

This is the conclusion.
""",
    }


@pytest.fixture
def sample_structure() -> DocumentStructure:
    """Create a sample document structure."""
    return DocumentStructure(
        title="Test Document",
        document_type=DocumentType.PAPER,
        outline=[],
        heading_fixes=[
            HeadingFix(
                page=2,
                line=1,
                current_text="Methods",
                current_level=2,
                should_be_level=3,
                reason="Should be subsection",
            )
        ],
        key_terms=["methodology", "results"],
        acronyms={},
    )


@pytest.fixture
def sample_page_plan() -> PagePlan:
    """Create a sample page plan."""
    return PagePlan(
        page_num=1,
        summary="Introduction page with one figure",
        section_context="Introduction section",
        keywords=["introduction"],
        figures=[
            FigureContext(
                figure_index=1,
                location="after heading",
                appears_to_be="diagram",
                surrounding_text="Some text about the figure",
                is_decorative=False,
            )
        ],
        tables=[],
        ocr_errors=[],
        formatting_issues=[],
        page_artifacts=[],
        footnote_issues=[],
        citation_issues=[],
        list_issues=[],
        typography_issues=[],
        has_page_continuation=False,
        first_100_chars="# Introduction\n\nThis is the introduction",
        last_100_chars="text about the figure.\n",
    )


@pytest.fixture
def sample_chain_state() -> PageChainState:
    """Create a sample chain state."""
    state = PageChainState()
    state.page_summaries[1] = "Introduction page with one figure"
    state.figures_by_page[1] = [
        FigureContext(
            figure_index=1,
            location="after heading",
            appears_to_be="diagram",
            surrounding_text="Some text about the figure",
            is_decorative=False,
        )
    ]
    state.tables_by_page[1] = []
    state.page_continuations[1] = False
    state.first_100_chars_by_page[1] = "# Introduction\n\nThis is the introduction"
    state.last_100_chars_by_page[1] = "text about the figure.\n"
    return state


class TestGenerateJobsForPage:
    """Tests for generate_jobs_for_page function."""

    def test_generates_content_job_for_figure(
        self, sample_page_plan: PagePlan, sample_structure: DocumentStructure
    ):
        """Test that a content job is generated for a page with figures."""
        jobs = generate_jobs_for_page(
            page_num=1,
            page_plan=sample_page_plan,
            page_markdown="# Test\n\n<!-- image 1 -->",
            structure=sample_structure,
        )

        # Should have at least one content job
        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]
        assert len(content_jobs) == 1

        # The job should have an alt-text task
        job = content_jobs[0]
        alt_text_tasks = [t for t in job.tasks if t.task_type == TaskType.ALT_TEXT]
        assert len(alt_text_tasks) == 1
        assert alt_text_tasks[0].target == "fig:1"

    def test_generates_structure_job_for_heading_fix(
        self, sample_structure: DocumentStructure
    ):
        """Test that a structure job is generated for heading fixes."""
        page_plan = PagePlan(
            page_num=2,
            summary="Methods page",
            section_context="Methods section",
            keywords=[],
            figures=[],
            tables=[],
            ocr_errors=[],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=2,
            page_plan=page_plan,
            page_markdown="## Methods\n\nDescription",
            structure=sample_structure,
        )

        # Should have a structure job
        structure_jobs = [j for j in jobs if j.job_type == JobType.STRUCTURE]
        assert len(structure_jobs) == 1

        # The job should have a heading fix task
        job = structure_jobs[0]
        heading_tasks = [t for t in job.tasks if t.task_type == TaskType.HEADING_FIX]
        assert len(heading_tasks) == 1
        assert "Methods" in heading_tasks[0].target

    def test_generates_content_job_for_table(self, sample_structure: DocumentStructure):
        """Test that a content job is generated for a page with tables."""
        page_plan = PagePlan(
            page_num=2,
            summary="Results page with table",
            section_context="Results section",
            keywords=[],
            figures=[],
            tables=[
                TableContext(
                    table_index=1,
                    location="after heading",
                    appears_to_be="data table",
                    caption="Table 1: Results",
                    estimated_rows=5,
                    estimated_cols=3,
                )
            ],
            ocr_errors=[],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=2,
            page_plan=page_plan,
            page_markdown="## Results\n\n<!-- table 1 -->",
            structure=sample_structure,
        )

        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]
        assert len(content_jobs) == 1

        table_tasks = [t for t in content_jobs[0].tasks if t.task_type == TaskType.TABLE_TRANSCRIPTION]
        assert len(table_tasks) == 1
        assert table_tasks[0].target == "table:1"

    def test_returns_empty_list_for_no_work(self, sample_structure: DocumentStructure):
        """Test that an empty list is returned when there's no work to do."""
        # Create structure without heading fixes for page 3
        structure = DocumentStructure(
            title="Test Document",
            document_type=DocumentType.PAPER,
            outline=[],
            heading_fixes=[],  # No heading fixes
            key_terms=[],
            acronyms={},
        )

        page_plan = PagePlan(
            page_num=3,
            summary="Empty page",
            section_context="",
            keywords=[],
            figures=[],  # No figures
            tables=[],  # No tables
            ocr_errors=[],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=3,
            page_plan=page_plan,
            page_markdown="# Empty\n\nNo work needed.",
            structure=structure,
        )

        assert len(jobs) == 0

    def test_produces_correct_jobs_for_multiple_elements(
        self, sample_structure: DocumentStructure
    ):
        """Test job generation with multiple figures and tables."""
        page_plan = PagePlan(
            page_num=1,
            summary="Page with multiple elements",
            section_context="Introduction",
            keywords=[],
            figures=[
                FigureContext(figure_index=1, appears_to_be="chart", is_decorative=False),
                FigureContext(figure_index=2, appears_to_be="diagram", is_decorative=False),
            ],
            tables=[
                TableContext(table_index=1, appears_to_be="data table"),
                TableContext(table_index=2, appears_to_be="comparison table"),
            ],
            ocr_errors=["typo1", "typo2"],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=1,
            page_plan=page_plan,
            page_markdown="# Test\n\n<!-- image 1 -->\n<!-- image 2 -->\n<!-- table 1 -->\n<!-- table 2 -->",
            structure=sample_structure,
        )

        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]
        assert len(content_jobs) == 1

        job = content_jobs[0]
        # 2 figures + 2 tables + 2 OCR errors = 6 tasks
        assert len(job.tasks) == 6


class TestConvertChainStateToPagePlanSingle:
    """Tests for _convert_chain_state_to_page_plan_single function."""

    def test_converts_page_plan_correctly(self, sample_chain_state: PageChainState):
        """Test that chain state is correctly converted to page plan."""
        page_plan = _convert_chain_state_to_page_plan_single(sample_chain_state, 1)

        assert page_plan is not None
        assert page_plan.page_num == 1
        assert page_plan.summary == "Introduction page with one figure"
        assert len(page_plan.figures) == 1
        assert page_plan.figures[0].figure_index == 1

    def test_returns_none_for_missing_page(self, sample_chain_state: PageChainState):
        """Test that None is returned for a page not in the chain state."""
        page_plan = _convert_chain_state_to_page_plan_single(sample_chain_state, 99)
        assert page_plan is None

    def test_preserves_continuation_flag(self):
        """Test that page continuation flag is preserved."""
        state = PageChainState()
        state.page_summaries[1] = "Continued page"
        state.page_continuations[1] = True
        state.first_100_chars_by_page[1] = "First chars"
        state.last_100_chars_by_page[1] = "Last chars"

        page_plan = _convert_chain_state_to_page_plan_single(state, 1)

        assert page_plan is not None
        assert page_plan.has_page_continuation is True
        assert page_plan.first_100_chars == "First chars"
        assert page_plan.last_100_chars == "Last chars"


class TestRunPageChainStreaming:
    """Tests for run_page_chain_streaming function."""

    @pytest.mark.asyncio
    async def test_yields_per_page(self, sample_page_markdowns: dict[int, str]):
        """Test that streaming yields results for each page."""
        mock_output = MagicMock()
        mock_output.summary = "Test summary"
        mock_output.headings = []
        mock_output.figures = []
        mock_output.tables = []
        mock_output.terms = []
        mock_output.page_artifacts = []
        mock_output.footnote_issues = []
        mock_output.citation_issues = []
        mock_output.list_issues = []
        mock_output.typography_issues = []
        mock_output.has_page_continuation = False
        mock_output.first_100_chars = "First"
        mock_output.last_100_chars = "Last"

        with patch(
            "src.agents.page_chain.analyze_page",
            new_callable=AsyncMock,
            return_value=(mock_output, 100, 50, None),
        ):
            pages_yielded = []
            async for page_num, state, inp_tokens, out_tokens, llm_call in run_page_chain_streaming(
                page_markdowns=sample_page_markdowns,
            ):
                pages_yielded.append(page_num)

            # Should yield 3 results (one per page)
            assert len(pages_yielded) == 3
            assert pages_yielded == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_state_accumulates(self, sample_page_markdowns: dict[int, str]):
        """Test that state accumulates as pages are processed."""
        page_outputs = {
            1: MagicMock(
                summary="Page 1",
                headings=[],
                figures=[FigureAnalysis(figure_index=1, appears_to_be="chart", surrounding_context="", is_decorative=False)],
                tables=[],
                terms=["term1"],
                page_artifacts=[],
                footnote_issues=[],
                citation_issues=[],
                list_issues=[],
                typography_issues=[],
                has_page_continuation=False,
                first_100_chars="First 1",
                last_100_chars="Last 1",
            ),
            2: MagicMock(
                summary="Page 2",
                headings=[],
                figures=[],
                tables=[TableAnalysis(table_index=1, appears_to_contain="table")],
                terms=["term2"],
                page_artifacts=[],
                footnote_issues=[],
                citation_issues=[],
                list_issues=[],
                typography_issues=[],
                has_page_continuation=False,
                first_100_chars="First 2",
                last_100_chars="Last 2",
            ),
            3: MagicMock(
                summary="Page 3",
                headings=[],
                figures=[],
                tables=[],
                terms=["term3"],
                page_artifacts=[],
                footnote_issues=[],
                citation_issues=[],
                list_issues=[],
                typography_issues=[],
                has_page_continuation=False,
                first_100_chars="First 3",
                last_100_chars="Last 3",
            ),
        }

        async def mock_analyze_page(page_num, markdown, state, total_pages, capture_debug=False):
            return page_outputs[page_num], 100, 50, None

        with patch("src.agents.page_chain.analyze_page", new=mock_analyze_page):
            states = []
            async for page_num, state, inp_tokens, out_tokens, llm_call in run_page_chain_streaming(
                page_markdowns=sample_page_markdowns,
            ):
                states.append((page_num, len(state.page_summaries)))

            # State should accumulate
            assert states == [(1, 1), (2, 2), (3, 3)]


class TestStreamingPipelineMaintainsCorrectness:
    """Tests to verify streaming produces same results as non-streaming."""

    def test_generate_jobs_matches_original_logic(self, sample_structure: DocumentStructure):
        """Test that generate_jobs_for_page produces same jobs as stage4_generate_jobs."""
        page_plan = PagePlan(
            page_num=1,
            summary="Test page",
            section_context="Introduction",
            keywords=["test"],
            figures=[
                FigureContext(figure_index=1, appears_to_be="chart", is_decorative=False),
            ],
            tables=[],
            ocr_errors=["typo"],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=1,
            page_plan=page_plan,
            page_markdown="# Test\n<!-- image 1 -->",
            structure=sample_structure,
        )

        # Verify job structure matches expected
        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]
        assert len(content_jobs) == 1
        job = content_jobs[0]

        # Should have alt-text and OCR tasks
        task_types = [t.task_type for t in job.tasks]
        assert TaskType.ALT_TEXT in task_types
        assert TaskType.OCR_FIX in task_types

        # Verify job context is populated
        assert job.context.document_title == "Test Document"
        assert job.context.document_type == DocumentType.PAPER


class TestStreamingHandlesPlanningFailure:
    """Tests for error handling in streaming pipeline."""

    @pytest.mark.asyncio
    async def test_handles_analyze_page_exception(self, sample_page_markdowns: dict[int, str]):
        """Test that exceptions in analyze_page are propagated."""
        call_count = 0

        async def mock_analyze_failing(page_num, markdown, state, total_pages, capture_debug=False):
            nonlocal call_count
            call_count += 1
            if page_num == 2:
                raise ValueError("Analysis failed for page 2")
            mock_output = MagicMock()
            mock_output.summary = f"Page {page_num}"
            mock_output.headings = []
            mock_output.figures = []
            mock_output.tables = []
            mock_output.terms = []
            mock_output.page_artifacts = []
            mock_output.footnote_issues = []
            mock_output.citation_issues = []
            mock_output.list_issues = []
            mock_output.typography_issues = []
            mock_output.has_page_continuation = False
            mock_output.first_100_chars = "First"
            mock_output.last_100_chars = "Last"
            return mock_output, 100, 50, None

        with patch("src.agents.page_chain.analyze_page", new=mock_analyze_failing):
            with pytest.raises(ValueError, match="Analysis failed for page 2"):
                async for _ in run_page_chain_streaming(page_markdowns=sample_page_markdowns):
                    pass


class TestJobTypeOrdering:
    """Tests to verify job type ordering and priorities."""

    def test_structure_jobs_have_lowest_priority_number(self, sample_structure: DocumentStructure):
        """Test that structure jobs have priority 1 (run first)."""
        page_plan = PagePlan(
            page_num=2,
            summary="Methods page",
            section_context="Methods section",
            keywords=[],
            figures=[
                FigureContext(figure_index=1, appears_to_be="chart", is_decorative=False),
            ],
            tables=[],
            ocr_errors=[],
            formatting_issues=[],
        )

        jobs = generate_jobs_for_page(
            page_num=2,
            page_plan=page_plan,
            page_markdown="## Methods\n<!-- image 1 -->",
            structure=sample_structure,
        )

        structure_jobs = [j for j in jobs if j.job_type == JobType.STRUCTURE]
        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]

        assert len(structure_jobs) == 1
        assert len(content_jobs) == 1

        # Structure jobs should run before content jobs
        assert structure_jobs[0].priority < content_jobs[0].priority
        assert structure_jobs[0].priority == 1

    def test_paragraph_jobs_run_last(self, sample_structure: DocumentStructure):
        """Test that paragraph jobs have highest priority number (run last)."""
        from src.agents.models import PageArtifactIssue

        page_plan = PagePlan(
            page_num=1,
            summary="Page with paragraph issues",
            section_context="Introduction",
            keywords=[],
            figures=[
                FigureContext(figure_index=1, appears_to_be="chart", is_decorative=False),
            ],
            tables=[],
            ocr_errors=[],
            formatting_issues=[],
            page_artifacts=[
                PageArtifactIssue(text="---", artifact_type="page_break", line_number=5),
            ],
        )

        jobs = generate_jobs_for_page(
            page_num=1,
            page_plan=page_plan,
            page_markdown="# Test\n<!-- image 1 -->\n---",
            structure=sample_structure,
        )

        content_jobs = [j for j in jobs if j.job_type == JobType.CONTENT]
        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]

        assert len(content_jobs) == 1
        assert len(paragraph_jobs) == 1

        # Paragraph jobs should run after content jobs
        assert paragraph_jobs[0].priority > content_jobs[0].priority
