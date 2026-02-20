"""Unit tests for heading reconciliation step."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    HeadingReconciliationOutput,
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    StepResult,
    StructureResult,
)


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def base_result():
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=3,
        versions={"v0": "# Title\n\nIntro text\n\n## Methods\n\nSome text\n\n### Sub\n\nMore"},
        page_images={"1": "AAAA", "2": "BBBB", "3": "CCCC"},
        page_markdowns={
            "v0": {
                "1": "# Title\n\nIntro text",
                "2": "## Methods\n\nSome text",
                "3": "### Sub\n\nMore",
            },
        },
        figures=[],
        steps=[],
        stats={},
    )


@pytest.fixture
def structure_with_headings():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            3: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=1, text="Title", page=1),
            OutlineEntry(level=2, text="Methods", page=2),
            OutlineEntry(level=4, text="Sub", page=3),  # wrong level — should be h3
        ],
        footnotes=[],
    )


class TestHeadingReconciliation:
    """Test the heading reconciliation step."""

    @pytest.mark.asyncio
    async def test_skipped_when_no_outline(self, service, base_result):
        """Reconciliation should be skipped when outline is empty."""
        empty_structure = StructureResult()

        result = await service._step_heading_reconciliation(base_result, empty_structure)

        assert result is empty_structure
        step = base_result.steps[-1]
        assert step.name == "heading_reconciliation"
        assert step.skipped is True
        assert step.metadata["headings_reviewed"] == 0

    @pytest.mark.asyncio
    async def test_reconciles_heading_levels(self, service, base_result, structure_with_headings):
        """Reconciliation should update outline when agent corrects levels."""
        reconciled_output = HeadingReconciliationOutput(
            outline=[
                OutlineEntry(level=1, text="Title", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
                OutlineEntry(level=3, text="Sub", page=3),  # fixed from h4 to h3
            ],
            reasoning="Fixed h4 to h3 — no h3 gap allowed.",
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.output = reconciled_output
        mock_agent.run = AsyncMock(return_value=mock_run_result)

        with patch("pydantic_ai.Agent", return_value=mock_agent), \
             patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
            result = await service._step_heading_reconciliation(
                base_result, structure_with_headings
            )

        # Outline should be updated
        assert result.outline[2].level == 3  # was 4, now 3
        assert result.outline[0].level == 1  # unchanged
        assert result.outline[1].level == 2  # unchanged

        # Changes should be recorded
        step = base_result.steps[-1]
        assert step.name == "heading_reconciliation"
        assert step.metadata["headings_changed"] == 1
        assert len(step.changes) == 1
        assert "h4" in step.changes[0].old_text
        assert "h3" in step.changes[0].new_text

    @pytest.mark.asyncio
    async def test_no_changes_when_all_correct(self, service, base_result):
        """When all levels are correct, no changes should be recorded."""
        correct_structure = StructureResult(
            outline=[
                OutlineEntry(level=1, text="Title", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
        )

        reconciled_output = HeadingReconciliationOutput(
            outline=[
                OutlineEntry(level=1, text="Title", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
            reasoning="All levels correct.",
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.output = reconciled_output
        mock_agent.run = AsyncMock(return_value=mock_run_result)

        with patch("pydantic_ai.Agent", return_value=mock_agent), \
             patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
            result = await service._step_heading_reconciliation(
                base_result, correct_structure
            )

        step = base_result.steps[-1]
        assert step.metadata["headings_changed"] == 0
        assert len(step.changes) == 0

    @pytest.mark.asyncio
    async def test_agent_failure_preserves_original(self, service, base_result, structure_with_headings):
        """If the agent fails, the original outline should be preserved."""
        original_levels = [e.level for e in structure_with_headings.outline]

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))

        with patch("pydantic_ai.Agent", return_value=mock_agent), \
             patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
            result = await service._step_heading_reconciliation(
                base_result, structure_with_headings
            )

        # Original outline should be preserved
        assert [e.level for e in result.outline] == original_levels
        step = base_result.steps[-1]
        assert step.metadata["headings_changed"] == 0

    @pytest.mark.asyncio
    async def test_token_tracking(self, service, base_result, structure_with_headings):
        """Input/output tokens should be tracked."""
        reconciled_output = HeadingReconciliationOutput(
            outline=structure_with_headings.outline,
            reasoning="No changes.",
        )

        mock_agent = MagicMock()
        mock_run_result = MagicMock()
        mock_run_result.output = reconciled_output
        mock_usage = MagicMock()
        mock_usage.request_tokens = 500
        mock_usage.response_tokens = 100
        mock_run_result.usage.return_value = mock_usage
        mock_agent.run = AsyncMock(return_value=mock_run_result)

        with patch("pydantic_ai.Agent", return_value=mock_agent), \
             patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
            await service._step_heading_reconciliation(
                base_result, structure_with_headings
            )

        step = base_result.steps[-1]
        assert step.input_tokens == 500
        assert step.output_tokens == 100
