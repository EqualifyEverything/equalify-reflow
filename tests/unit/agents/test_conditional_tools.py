"""Tests for conditional tool registration via prepare functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.agents.models import (
    TEXT_ONLY_TASK_TYPES,
    VISION_REQUIRED_TASK_TYPES,
    Task,
    TaskType,
)

# =============================================================================
# Task Type Classification Tests
# =============================================================================


class TestTaskTypeClassification:
    """Test the task type sets are correctly defined."""

    def test_vision_required_types(self):
        assert TaskType.ALT_TEXT in VISION_REQUIRED_TASK_TYPES
        assert TaskType.TABLE_TRANSCRIPTION in VISION_REQUIRED_TASK_TYPES
        assert TaskType.TYPOGRAPHY_FIX in VISION_REQUIRED_TASK_TYPES
        assert len(VISION_REQUIRED_TASK_TYPES) == 3

    def test_text_only_types(self):
        assert TaskType.HEADING_FIX in TEXT_ONLY_TASK_TYPES
        assert TaskType.OCR_FIX in TEXT_ONLY_TASK_TYPES
        assert TaskType.FORMAT_FIX in TEXT_ONLY_TASK_TYPES
        assert TaskType.SPELLING_FIX in TEXT_ONLY_TASK_TYPES
        assert TaskType.PAGE_ARTIFACT_REMOVAL in TEXT_ONLY_TASK_TYPES
        assert TaskType.FOOTNOTE_CORRECTION in TEXT_ONLY_TASK_TYPES
        assert TaskType.CITATION_LINKING in TEXT_ONLY_TASK_TYPES
        assert TaskType.LIST_FIX in TEXT_ONLY_TASK_TYPES
        assert len(TEXT_ONLY_TASK_TYPES) == 8

    def test_no_overlap(self):
        """Vision and text-only sets should not overlap."""
        overlap = VISION_REQUIRED_TASK_TYPES & TEXT_ONLY_TASK_TYPES
        assert overlap == frozenset(), f"Overlapping types: {overlap}"

    def test_paragraph_merge_not_in_either(self):
        """PARAGRAPH_MERGE is a special task not in either set."""
        assert TaskType.PARAGRAPH_MERGE not in VISION_REQUIRED_TASK_TYPES
        assert TaskType.PARAGRAPH_MERGE not in TEXT_ONLY_TASK_TYPES


# =============================================================================
# Worker Vision Tool Prepare Function Tests
# =============================================================================


class TestWorkerPrepareVisionTool:
    """Test _prepare_vision_tool returns None for text-only jobs."""

    @pytest.fixture()
    def tool_def(self) -> MagicMock:
        return MagicMock(name="view_figure_tool")

    def _make_ctx(self, task_types: list[TaskType]) -> MagicMock:
        """Create a mock RunContext with specified task types."""
        tasks = [Task(task_type=tt, target="t", context="c") for tt in task_types]
        job = MagicMock()
        job.tasks = tasks
        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.job = job
        return ctx

    @pytest.mark.asyncio
    async def test_returns_none_for_heading_fix(self, tool_def):
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.HEADING_FIX])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_ocr_fix(self, tool_def):
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.OCR_FIX])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_spelling_fix(self, tool_def):
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.SPELLING_FIX])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tool_for_alt_text(self, tool_def):
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.ALT_TEXT])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is tool_def

    @pytest.mark.asyncio
    async def test_returns_tool_for_table_transcription(self, tool_def):
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.TABLE_TRANSCRIPTION])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is tool_def

    @pytest.mark.asyncio
    async def test_returns_tool_for_mixed_tasks(self, tool_def):
        """If any task needs vision, tool is included."""
        from src.agents.worker import _prepare_vision_tool

        ctx = self._make_ctx([TaskType.HEADING_FIX, TaskType.ALT_TEXT])
        result = await _prepare_vision_tool(ctx, tool_def)
        assert result is tool_def


# =============================================================================
# ParagraphAgent Prepare Function Tests
# =============================================================================


class TestParagraphAgentPrepareTypographyTool:
    """Test _prepare_typography_tool returns None when no typography tasks."""

    @pytest.fixture()
    def tool_def(self) -> MagicMock:
        return MagicMock(name="fix_typography_tool")

    def _make_ctx(self, task_types: list[TaskType]) -> MagicMock:
        tasks = [Task(task_type=tt, target="t", context="c") for tt in task_types]
        job = MagicMock()
        job.tasks = tasks
        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.job = job
        return ctx

    @pytest.mark.asyncio
    async def test_returns_none_when_no_typography(self, tool_def):
        from src.agents.paragraph_agent import _prepare_typography_tool

        ctx = self._make_ctx([TaskType.PAGE_ARTIFACT_REMOVAL, TaskType.FOOTNOTE_CORRECTION])
        result = await _prepare_typography_tool(ctx, tool_def)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tool_when_typography_present(self, tool_def):
        from src.agents.paragraph_agent import _prepare_typography_tool

        ctx = self._make_ctx([TaskType.TYPOGRAPHY_FIX])
        result = await _prepare_typography_tool(ctx, tool_def)
        assert result is tool_def

    @pytest.mark.asyncio
    async def test_returns_tool_when_mixed_with_typography(self, tool_def):
        from src.agents.paragraph_agent import _prepare_typography_tool

        ctx = self._make_ctx([TaskType.PAGE_ARTIFACT_REMOVAL, TaskType.TYPOGRAPHY_FIX])
        result = await _prepare_typography_tool(ctx, tool_def)
        assert result is tool_def


class TestParagraphAgentPrepareTextStructureTool:
    """Test _prepare_text_structure_tool returns None when no text tasks."""

    @pytest.fixture()
    def tool_def(self) -> MagicMock:
        return MagicMock(name="fix_text_structure_tool")

    def _make_ctx(self, task_types: list[TaskType]) -> MagicMock:
        tasks = [Task(task_type=tt, target="t", context="c") for tt in task_types]
        job = MagicMock()
        job.tasks = tasks
        ctx = MagicMock()
        ctx.deps = MagicMock()
        ctx.deps.job = job
        return ctx

    @pytest.mark.asyncio
    async def test_returns_none_when_only_typography(self, tool_def):
        from src.agents.paragraph_agent import _prepare_text_structure_tool

        ctx = self._make_ctx([TaskType.TYPOGRAPHY_FIX])
        result = await _prepare_text_structure_tool(ctx, tool_def)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_tool_for_page_artifacts(self, tool_def):
        from src.agents.paragraph_agent import _prepare_text_structure_tool

        ctx = self._make_ctx([TaskType.PAGE_ARTIFACT_REMOVAL])
        result = await _prepare_text_structure_tool(ctx, tool_def)
        assert result is tool_def

    @pytest.mark.asyncio
    async def test_returns_tool_for_footnotes(self, tool_def):
        from src.agents.paragraph_agent import _prepare_text_structure_tool

        ctx = self._make_ctx([TaskType.FOOTNOTE_CORRECTION])
        result = await _prepare_text_structure_tool(ctx, tool_def)
        assert result is tool_def

    @pytest.mark.asyncio
    async def test_returns_tool_for_list_fix(self, tool_def):
        from src.agents.paragraph_agent import _prepare_text_structure_tool

        ctx = self._make_ctx([TaskType.LIST_FIX])
        result = await _prepare_text_structure_tool(ctx, tool_def)
        assert result is tool_def
