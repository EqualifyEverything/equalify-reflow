"""Unit tests for the feedback service methods on PipelineViewerService.

Tests resolve_selector, apply_direct_edits, apply_candidate_changes, and
process_feedback_batch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    CandidateChange,
    DocumentChange,
    FeedbackItem,
    FeedbackItemType,
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    RevisionTask,
    RevisionTaskCategory,
    StepResult,
    StructureResult,
    TaskDecompositionResult,
    TextSelector,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def base_result():
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={
            "v0": "# Page 1\n\nHello world, this is a test.\n\n# Page 2\n\nGoodbye world."
        },
        page_markdowns={
            "v0": {
                "1": "# Page 1\n\nHello world, this is a test.",
                "2": "# Page 2\n\nGoodbye world.",
            },
        },
        page_images={"1": "AAAA", "2": "BBBB"},
        steps=[
            StepResult(
                name="docling",
                display_name="Docling",
                version_after="v0",
                elapsed_ms=100,
            )
        ],
    )


@pytest.fixture
def sample_structure():
    return StructureResult(
        page_attributes={1: PageAttributes(layout=LayoutType.SINGLE_COLUMN)},
        outline=[OutlineEntry(level=1, text="Page 1", page=1)],
    )


# ---------------------------------------------------------------------------
# TestResolveSelector
# ---------------------------------------------------------------------------


class TestResolveSelector:
    def test_exact_match(self, service, base_result):
        selector = TextSelector(exact="Hello world")
        page_mds = base_result.page_markdowns["v0"]
        full_md = base_result.versions["v0"]
        result = service.resolve_selector(selector, full_md, page_mds)
        assert result is not None
        page, start, end = result
        assert page == 1
        assert page_mds["1"][start:end] == "Hello world"

    def test_prefix_suffix_disambiguation(self, service):
        page_mds = {
            "1": "foo bar baz",
            "2": "qux bar quux",
        }
        selector = TextSelector(exact="bar", prefix="foo ", suffix=" baz")
        result = service.resolve_selector(selector, "", page_mds)
        assert result is not None
        page, start, end = result
        assert page == 1

    def test_not_found_returns_none(self, service, base_result):
        selector = TextSelector(exact="NONEXISTENT_TEXT_xyz")
        page_mds = base_result.page_markdowns["v0"]
        full_md = base_result.versions["v0"]
        result = service.resolve_selector(selector, full_md, page_mds)
        assert result is None

    def test_fuzzy_fallback(self, service):
        page_mds = {"1": "Hello world\nGoodbye world"}
        selector = TextSelector(exact="Hello wrold")  # typo
        result = service.resolve_selector(selector, "", page_mds)
        # Should fuzzy match "Hello world"
        assert result is not None
        assert result[0] == 1

    def test_empty_exact_returns_none(self, service):
        selector = TextSelector(exact="")
        result = service.resolve_selector(selector, "", {"1": "some text"})
        assert result is None


# ---------------------------------------------------------------------------
# TestApplyDirectEdits
# ---------------------------------------------------------------------------


class TestApplyDirectEdits:
    def test_single_edit(self, service, base_result):
        edit = FeedbackItem(
            id="edit1",
            type=FeedbackItemType.EDIT,
            selector=TextSelector(exact="Hello world"),
            new_text="Hello everyone",
            description="Fix greeting",
        )
        candidates = service.apply_direct_edits([edit], base_result, None)
        assert len(candidates) == 1
        assert candidates[0].source_type == "direct_edit"
        assert candidates[0].change.old_text == "Hello world"
        assert candidates[0].change.new_text == "Hello everyone"
        assert candidates[0].source_feedback_id == "edit1"

    def test_unresolvable_selector_skipped(self, service, base_result):
        edit = FeedbackItem(
            id="edit1",
            type=FeedbackItemType.EDIT,
            selector=TextSelector(exact="DOES_NOT_EXIST"),
            new_text="replacement",
        )
        candidates = service.apply_direct_edits([edit], base_result, None)
        assert len(candidates) == 0

    def test_missing_selector_skipped(self, service, base_result):
        edit = FeedbackItem(
            id="edit1",
            type=FeedbackItemType.EDIT,
            new_text="replacement",
        )
        candidates = service.apply_direct_edits([edit], base_result, None)
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# TestApplyCandidateChanges
# ---------------------------------------------------------------------------


class TestApplyCandidateChanges:
    def test_new_version_produced(self, service, base_result):
        candidate = CandidateChange(
            id="c1",
            change=DocumentChange(
                page=1,
                old_text="Hello world",
                new_text="Hello everyone",
                reasoning="test",
                stage="revision",
            ),
            source_type="direct_edit",
            source_feedback_id="f1",
            source_description="test edit",
        )
        step = service.apply_candidate_changes([candidate], base_result, 1)
        assert step.version_after == "v1"
        assert "Hello everyone" in base_result.versions["v1"]
        assert "Hello world" not in base_result.versions["v1"]

    def test_step_result_recorded(self, service, base_result):
        candidate = CandidateChange(
            id="c1",
            change=DocumentChange(
                page=1,
                old_text="Hello world",
                new_text="Hi",
                reasoning="shorten",
                stage="revision",
            ),
            source_type="direct_edit",
            source_feedback_id="f1",
            source_description="shorten",
        )
        step = service.apply_candidate_changes([candidate], base_result, 1)
        assert step.name == "feedback_1"
        assert step.metadata["applied_count"] == 1
        assert len(base_result.steps) == 2  # docling + feedback

    def test_multi_page_changes(self, service, base_result):
        candidates = [
            CandidateChange(
                id="c1",
                change=DocumentChange(
                    page=1, old_text="Hello world", new_text="Hi",
                    reasoning="r", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="f1",
                source_description="d",
            ),
            CandidateChange(
                id="c2",
                change=DocumentChange(
                    page=2, old_text="Goodbye world", new_text="Bye",
                    reasoning="r", stage="revision",
                ),
                source_type="direct_edit",
                source_feedback_id="f2",
                source_description="d",
            ),
        ]
        step = service.apply_candidate_changes(candidates, base_result, 1)
        assert step.metadata["applied_count"] == 2
        assert "Hi" in base_result.versions["v1"]
        assert "Bye" in base_result.versions["v1"]

    def test_old_text_not_found_skipped(self, service, base_result):
        candidate = CandidateChange(
            id="c1",
            change=DocumentChange(
                page=1, old_text="NONEXISTENT", new_text="x",
                reasoning="r", stage="revision",
            ),
            source_type="direct_edit",
            source_feedback_id="f1",
            source_description="d",
        )
        step = service.apply_candidate_changes([candidate], base_result, 1)
        assert step.metadata["applied_count"] == 0


# ---------------------------------------------------------------------------
# TestProcessFeedbackBatch
# ---------------------------------------------------------------------------


class TestProcessFeedbackBatch:
    @pytest.mark.asyncio
    async def test_edits_only_no_llm(self, service, base_result):
        """Edits-only batch should not call the LLM."""
        edit = FeedbackItem(
            id="e1",
            type=FeedbackItemType.EDIT,
            selector=TextSelector(exact="Hello world"),
            new_text="Hello everyone",
            description="Fix greeting",
        )
        with patch.object(service, "decompose_feedback") as mock_decompose:
            candidates = await service.process_feedback_batch(
                items=[edit],
                result=base_result,
                structure=None,
                section_map=None,
            )
            mock_decompose.assert_not_called()
        assert len(candidates) == 1
        assert candidates[0].source_type == "direct_edit"

    @pytest.mark.asyncio
    async def test_comments_only_calls_llm(self, service, base_result, sample_structure):
        """Comments should trigger decompose_feedback + run_revision."""
        comment = FeedbackItem(
            id="c1",
            type=FeedbackItemType.COMMENT,
            page=1,
            description="The heading should be larger",
            feedback_type=RevisionTaskCategory.FORMATTING,
        )

        mock_decomp = TaskDecompositionResult(
            tasks=[
                RevisionTask(
                    id=1,
                    description="Make heading larger",
                    affected_pages=[1],
                    category=RevisionTaskCategory.FORMATTING,
                )
            ],
            reasoning="Single task",
        )

        mock_step = StepResult(
            name="revision_1",
            display_name="Revision 1",
            version_before="v0",
            version_after="v1",
            elapsed_ms=100,
            changes=[
                DocumentChange(
                    page=1,
                    old_text="# Page 1",
                    new_text="## Page 1",
                    reasoning="Made heading larger",
                    stage="revision",
                )
            ],
        )

        with patch.object(
            service, "decompose_feedback", new_callable=AsyncMock, return_value=mock_decomp
        ), patch.object(
            service, "run_revision", new_callable=AsyncMock, return_value=mock_step
        ):
            candidates = await service.process_feedback_batch(
                items=[comment],
                result=base_result,
                structure=sample_structure,
                section_map=None,
            )

        assert len(candidates) == 1
        assert candidates[0].source_type == "ai_revision"

    @pytest.mark.asyncio
    async def test_mixed_batch(self, service, base_result, sample_structure):
        """Mixed batch produces both direct_edit and ai_revision candidates."""
        edit = FeedbackItem(
            id="e1",
            type=FeedbackItemType.EDIT,
            selector=TextSelector(exact="Hello world"),
            new_text="Hello everyone",
        )
        comment = FeedbackItem(
            id="c1",
            type=FeedbackItemType.COMMENT,
            page=2,
            description="Fix the goodbye text",
        )

        mock_decomp = TaskDecompositionResult(
            tasks=[
                RevisionTask(
                    id=1,
                    description="Fix goodbye",
                    affected_pages=[2],
                )
            ],
            reasoning="Single task",
        )

        mock_step = StepResult(
            name="revision_1",
            display_name="Revision 1",
            version_before="v0",
            version_after="v1",
            elapsed_ms=50,
            changes=[
                DocumentChange(
                    page=2,
                    old_text="Goodbye world.",
                    new_text="Farewell world.",
                    reasoning="Fixed",
                    stage="revision",
                )
            ],
        )

        with patch.object(
            service, "decompose_feedback", new_callable=AsyncMock, return_value=mock_decomp
        ), patch.object(
            service, "run_revision", new_callable=AsyncMock, return_value=mock_step
        ):
            candidates = await service.process_feedback_batch(
                items=[edit, comment],
                result=base_result,
                structure=sample_structure,
                section_map=None,
            )

        direct = [c for c in candidates if c.source_type == "direct_edit"]
        ai = [c for c in candidates if c.source_type == "ai_revision"]
        assert len(direct) == 1
        assert len(ai) == 1
