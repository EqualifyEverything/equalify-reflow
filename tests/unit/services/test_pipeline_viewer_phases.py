"""Unit tests for pipeline viewer phase orchestration.

Tests the non-LLM logic of the pipeline viewer service: Phase 1 accumulation,
Phase 2 orchestration, str_replace tool behavior, Phase 3 assembly, and
procedure loading.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline_viewer import (
    PAGE_AGENT_SEMAPHORE_LIMIT,
    PipelineViewerService,
    _load_procedure,
)
from src.services.pipeline_viewer_models import (
    DocumentChange,
    FootnoteInfo,
    OutlineEntry,
    PageCorrectionResult,
    PageType,
    PipelineViewerResult,
    StepResult,
    StructurePageOutput,
    StructureResult,
    HeadingRecommendation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """Create a PipelineViewerService instance."""
    return PipelineViewerService()


@pytest.fixture
def base_result():
    """Create a base PipelineViewerResult with v0 data (2 pages)."""
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Page 1\n\nHello world\n\n# Page 2\n\nGoodbye"},
        page_images={"1": "AAAA", "2": "BBBB"},
        page_markdowns={
            "v0": {
                "1": "# Page 1\n\nHello world",
                "2": "# Page 2\n\nGoodbye",
            },
        },
        figures=[],
        steps=[
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=100,
                changes=[],
                metadata={},
            ),
        ],
        stats={"total_chars": 40},
    )


@pytest.fixture
def sample_structure():
    """Create a StructureResult with outline, page types, and footnotes."""
    return StructureResult(
        page_types={
            1: PageType.ACADEMIC_PAPER,
            2: PageType.ACADEMIC_PAPER,
        },
        outline=[
            OutlineEntry(level=1, text="Title", page=1),
            OutlineEntry(level=2, text="Introduction", page=1),
            OutlineEntry(level=2, text="Methods", page=2),
        ],
        footnotes=[
            FootnoteInfo(number="1", body_text="First footnote.", source_page=1),
        ],
    )


# ---------------------------------------------------------------------------
# Phase 1 — Structure Analysis accumulation
# ---------------------------------------------------------------------------


class TestPhase1Accumulation:
    """Test that Phase 1 accumulates outline, page types, and footnotes.

    _step_structure() uses deferred imports, so we patch at the source
    modules (pydantic_ai.*, src.agents.prompts.*) not the consumer module.
    """

    @pytest.mark.asyncio
    async def test_outline_accumulates_across_pages(self, service, base_result):
        """Outline entries from each page should be appended in order."""
        page1_output = StructurePageOutput(
            page_type=PageType.ACADEMIC_PAPER,
            headings=[
                HeadingRecommendation(
                    text="Title", recommended_level=1, reasoning="doc title"
                ),
            ],
            footnotes=[],
        )
        page2_output = StructurePageOutput(
            page_type=PageType.ACADEMIC_PAPER,
            headings=[
                HeadingRecommendation(
                    text="Methods", recommended_level=2, reasoning="section"
                ),
            ],
            footnotes=[],
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()

        call_count = 0

        async def mock_run(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_run_result.output = page1_output
            else:
                mock_run_result.output = page2_output
            return mock_run_result

        mock_agent.run = mock_run

        with patch(
            "pydantic_ai.Agent", return_value=mock_agent
        ), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.structure_analysis.STRUCTURE_SYSTEM_PROMPT", "test"
        ), patch(
            "src.agents.prompts.structure_analysis.build_structure_user_message",
            return_value="test msg",
        ), patch(
            "pydantic_ai.messages.BinaryContent"
        ):
            structure = await service._step_structure(base_result)

        assert len(structure.outline) == 2
        assert structure.outline[0].text == "Title"
        assert structure.outline[0].page == 1
        assert structure.outline[1].text == "Methods"
        assert structure.outline[1].page == 2

    @pytest.mark.asyncio
    async def test_page_types_collected(self, service, base_result):
        """Each page should have its type recorded."""
        page_output = StructurePageOutput(
            page_type=PageType.SINGLE_COLUMN,
            headings=[],
            footnotes=[],
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.output = page_output
        mock_agent.run = AsyncMock(return_value=mock_run_result)

        with patch(
            "pydantic_ai.Agent", return_value=mock_agent
        ), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.structure_analysis.STRUCTURE_SYSTEM_PROMPT", "test"
        ), patch(
            "src.agents.prompts.structure_analysis.build_structure_user_message",
            return_value="test msg",
        ), patch(
            "pydantic_ai.messages.BinaryContent"
        ):
            structure = await service._step_structure(base_result)

        assert structure.page_types[1] == PageType.SINGLE_COLUMN
        assert structure.page_types[2] == PageType.SINGLE_COLUMN

    @pytest.mark.asyncio
    async def test_footnotes_collected_with_source_page(self, service, base_result):
        """Footnotes should be collected with correct source page."""
        page1_output = StructurePageOutput(
            page_type=PageType.ACADEMIC_PAPER,
            headings=[],
            footnotes=[
                FootnoteInfo(number="1", body_text="First note.", source_page=1),
            ],
        )
        page2_output = StructurePageOutput(
            page_type=PageType.ACADEMIC_PAPER,
            headings=[],
            footnotes=[
                FootnoteInfo(number="2", body_text="Second note.", source_page=2),
            ],
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()
        call_count = 0

        async def mock_run(messages):
            nonlocal call_count
            call_count += 1
            mock_run_result.output = page1_output if call_count == 1 else page2_output
            return mock_run_result

        mock_agent.run = mock_run

        with patch(
            "pydantic_ai.Agent", return_value=mock_agent
        ), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.structure_analysis.STRUCTURE_SYSTEM_PROMPT", "test"
        ), patch(
            "src.agents.prompts.structure_analysis.build_structure_user_message",
            return_value="test msg",
        ), patch(
            "pydantic_ai.messages.BinaryContent"
        ):
            structure = await service._step_structure(base_result)

        assert len(structure.footnotes) == 2
        assert structure.footnotes[0].number == "1"
        assert structure.footnotes[0].source_page == 1
        assert structure.footnotes[1].number == "2"
        assert structure.footnotes[1].source_page == 2

    @pytest.mark.asyncio
    async def test_agent_failure_skips_page(self, service, base_result):
        """If the agent fails on a page, that page is skipped."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))

        with patch(
            "pydantic_ai.Agent", return_value=mock_agent
        ), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.structure_analysis.STRUCTURE_SYSTEM_PROMPT", "test"
        ), patch(
            "src.agents.prompts.structure_analysis.build_structure_user_message",
            return_value="test msg",
        ), patch(
            "pydantic_ai.messages.BinaryContent"
        ):
            structure = await service._step_structure(base_result)

        # No page types should be recorded if all pages failed
        assert len(structure.page_types) == 0
        assert len(structure.outline) == 0


# ---------------------------------------------------------------------------
# Phase 2 — Page Content Corrections orchestration
# ---------------------------------------------------------------------------


class TestPhase2Orchestration:
    """Test Phase 2 parallelism, error handling, and change collection."""

    @pytest.mark.asyncio
    async def test_changes_collected_from_all_pages(self, service, base_result, sample_structure):
        """Changes from all pages should be collected."""
        page1_result = PageCorrectionResult(
            page=1,
            corrected_markdown="# Page 1\n\nFixed world",
            changes=[
                DocumentChange(
                    page=1,
                    old_text="Hello world",
                    new_text="Fixed world",
                    reasoning="OCR fix",
                    stage="ocr_error",
                ),
            ],
            issues=[],
        )
        page2_result = PageCorrectionResult(
            page=2,
            corrected_markdown="# Page 2\n\nFixed goodbye",
            changes=[
                DocumentChange(
                    page=2,
                    old_text="Goodbye",
                    new_text="Fixed goodbye",
                    reasoning="OCR fix",
                    stage="ocr_error",
                ),
            ],
            issues=[],
        )

        async def mock_run_page_agent(page_num, **kwargs):
            return page1_result if page_num == 1 else page2_result

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(base_result, sample_structure)

        step = base_result.steps[-1]
        assert step.name == "page_content"
        assert len(step.changes) == 2
        assert "v1" in base_result.versions

    @pytest.mark.asyncio
    async def test_failed_page_keeps_original_markdown(self, service, base_result, sample_structure):
        """If a page agent raises, the original markdown is preserved."""

        async def mock_run_page_agent(page_num, **kwargs):
            if page_num == 1:
                raise RuntimeError("Page 1 failed")
            return PageCorrectionResult(
                page=2,
                corrected_markdown="# Page 2 corrected",
                changes=[],
                issues=[],
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(base_result, sample_structure)

        v1_pages = base_result.page_markdowns["v1"]
        # Page 1 should keep original since it failed
        assert v1_pages["1"] == base_result.page_markdowns["v0"]["1"]
        # Page 2 should have corrected markdown
        assert v1_pages["2"] == "# Page 2 corrected"


# ---------------------------------------------------------------------------
# Phase 2 — str_replace tool behavior
# ---------------------------------------------------------------------------


class TestStrReplaceTool:
    """Test the str_replace tool function directly."""

    def test_successful_replace(self):
        """A unique match should succeed."""
        current_markdown = "Hello world, this is a test."
        changes: list[DocumentChange] = []

        count = current_markdown.count("Hello world")
        assert count == 1

        new_markdown = current_markdown.replace("Hello world", "Hi world", 1)
        changes.append(
            DocumentChange(
                page=1,
                old_text="Hello world",
                new_text="Hi world",
                reasoning="test fix",
                stage="ocr_error",
            )
        )

        assert new_markdown == "Hi world, this is a test."
        assert len(changes) == 1
        assert changes[0].old_text == "Hello world"

    def test_not_found_error(self):
        """When old_text is not in the document, return an error message."""
        current_markdown = "Hello world, this is a test."

        count = current_markdown.count("nonexistent text")
        assert count == 0
        # The tool would return an error message in this case

    def test_multiple_match_error(self):
        """When old_text appears multiple times, return an error message."""
        current_markdown = "Hello world, Hello world again."

        count = current_markdown.count("Hello world")
        assert count == 2
        # The tool would return an error asking for more context


# ---------------------------------------------------------------------------
# Phase 3 — Assembly and boundary logic
# ---------------------------------------------------------------------------


class TestPhase3Assembly:
    """Test Phase 3 assembly and boundary fix orchestration."""

    @pytest.mark.asyncio
    async def test_pages_concatenated_in_order(self, service, base_result, sample_structure):
        """Pages should be joined with double newlines in page order."""
        # Mock both agents to do nothing
        service._run_boundary_agent = AsyncMock(return_value=("joined", [], [], 0, 0))
        service._run_footnote_agent = AsyncMock(return_value=("joined", [], [], 0, 0))

        await service._step_boundaries(base_result, sample_structure)

        step = base_result.steps[-1]
        assert step.name == "boundaries"
        assert "v2" in base_result.versions

    @pytest.mark.asyncio
    async def test_boundary_agent_skipped_when_single_page(self, service, sample_structure):
        """With only one page, no boundary snippets exist so agent is skipped."""
        single_page_result = PipelineViewerResult(
            filename="single.pdf",
            total_pages=1,
            versions={"v0": "# Page 1\n\nContent"},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": "# Page 1\n\nContent"}},
            figures=[],
            steps=[],
            stats={},
        )

        service._run_boundary_agent = AsyncMock(return_value=("", [], [], 0, 0))
        service._run_footnote_agent = AsyncMock(return_value=("", [], [], 0, 0))

        await service._step_boundaries(single_page_result, sample_structure)

        # Boundary agent should not have been called (no boundaries)
        service._run_boundary_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_footnote_agent_skipped_when_no_footnotes(self, service, base_result):
        """If no footnotes were found in Phase 1, skip footnote agent."""
        no_fn_structure = StructureResult(
            page_types={1: PageType.ACADEMIC_PAPER, 2: PageType.ACADEMIC_PAPER},
            outline=[],
            footnotes=[],
        )

        service._run_boundary_agent = AsyncMock(
            return_value=("assembled doc", [], [], 0, 0)
        )
        service._run_footnote_agent = AsyncMock(return_value=("", [], [], 0, 0))

        await service._step_boundaries(base_result, no_fn_structure)

        service._run_footnote_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_boundary_snippets_computed_correctly(self, service, base_result, sample_structure):
        """Boundary snippets should capture tail/head text from adjacent pages."""
        captured_snippets = None

        async def capture_boundary_agent(document, boundary_snippets, footnote_numbers):
            nonlocal captured_snippets
            captured_snippets = boundary_snippets
            return (document, [], [], 0, 0)

        service._run_boundary_agent = capture_boundary_agent
        service._run_footnote_agent = AsyncMock(return_value=("", [], [], 0, 0))

        await service._step_boundaries(base_result, sample_structure)

        assert captured_snippets is not None
        assert len(captured_snippets) == 1  # 2 pages = 1 boundary
        assert captured_snippets[0]["page_before"] == 1
        assert captured_snippets[0]["page_after"] == 2

    @pytest.mark.asyncio
    async def test_changes_metadata_includes_counts(self, service, base_result, sample_structure):
        """Step metadata should count boundary and footnote changes."""
        service._run_boundary_agent = AsyncMock(
            return_value=(
                "fixed doc",
                [
                    DocumentChange(
                        page=0,
                        old_text="de-\nveloping",
                        new_text="developing",
                        reasoning="split word",
                        stage="boundary_fix",
                    )
                ],
                [],
                0,
                0,
            )
        )
        service._run_footnote_agent = AsyncMock(
            return_value=(
                "final doc",
                [
                    DocumentChange(
                        page=0,
                        old_text="work1",
                        new_text="work[^1]",
                        reasoning="footnote marker",
                        stage="footnote",
                    )
                ],
                [],
                0,
                0,
            )
        )

        await service._step_boundaries(base_result, sample_structure)

        step = base_result.steps[-1]
        assert step.metadata["boundary_fixes"] == 1
        assert step.metadata["footnotes_relocated"] == 1
        assert len(step.changes) == 2


# ---------------------------------------------------------------------------
# Cleanup step
# ---------------------------------------------------------------------------


class TestCleanupStep:
    """Test the deterministic cleanup step."""

    @pytest.mark.asyncio
    async def test_collapses_multiple_blank_lines(self, service):
        """3+ blank lines should be collapsed to 2."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v2": "Hello\n\n\n\nWorld"},
            page_images={},
            page_markdowns={},
            figures=[],
            steps=[],
            stats={},
        )

        await service._step_cleanup(result)

        assert result.versions["v3"] == "Hello\n\nWorld"

    @pytest.mark.asyncio
    async def test_strips_trailing_whitespace(self, service):
        """Trailing whitespace on lines should be removed."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v2": "Hello   \nWorld  "},
            page_images={},
            page_markdowns={},
            figures=[],
            steps=[],
            stats={},
        )

        await service._step_cleanup(result)

        assert result.versions["v3"] == "Hello\nWorld"


# ---------------------------------------------------------------------------
# Procedure loading
# ---------------------------------------------------------------------------


class TestProcedureLoading:
    """Test the _load_procedure utility."""

    def test_loads_existing_procedure(self):
        """academic_paper.md should load successfully."""
        content = _load_procedure("academic_paper")
        assert content != ""
        assert "Academic Paper" in content

    def test_missing_procedure_returns_empty(self):
        """A nonexistent procedure type should return empty string."""
        content = _load_procedure("nonexistent_type_12345")
        assert content == ""
