"""Unit tests for parallel subagent execution in ParagraphAgent.

Tests the parallelization optimization that runs multiple subagent calls
concurrently before the main agent reasoning loop.

With the consolidated TextStructure subagent, text structure tasks (page
artifacts, footnotes, citations, lists) are handled in a SINGLE LLM call.
Typography tasks are batched separately. This reduces LLM calls from 6/page
to 2-3/page.
"""

import asyncio
from unittest.mock import patch

import pytest
from PIL import Image
from src.agents.models import (
    DocumentType,
    Job,
    JobContext,
    JobType,
    Task,
    TaskType,
)
from src.agents.paragraph_agent import (
    MAX_CONCURRENT_SUBAGENTS,
    TEXT_STRUCTURE_TASK_TYPES,
    ParagraphAgentDeps,
    _create_failed_result,
    _get_subagent_key,
    _invoke_subagent_for_task,
    _run_subagents_parallel,
)
from src.agents.subagents.types import (
    TextStructureCorrection,
    TextStructureResult,
    TypographyResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_image() -> Image.Image:
    """Create a sample PIL image for testing."""
    return Image.new("RGB", (100, 100), color="white")


@pytest.fixture
def sample_markdown() -> str:
    """Create sample markdown content."""
    return """# Document Title

This is a paragraph with some text.

---

Another paragraph after a page break marker.

1. First item
2. Second item
3. Third item

Some text with a footnote[^1].

[^1]: This is the footnote definition.
"""


@pytest.fixture
def sample_job_context() -> JobContext:
    """Create a sample job context."""
    return JobContext(
        document_title="Test Document",
        document_type=DocumentType.PAPER,
        section_context="Page 1 of test document",
        dictionary=[],
    )


@pytest.fixture
def sample_deps(
    sample_image: Image.Image,
    sample_markdown: str,
    sample_job_context: JobContext,
) -> ParagraphAgentDeps:
    """Create sample dependencies for testing."""
    job = Job(
        job_id="test-job",
        page=1,
        job_type=JobType.PARAGRAPH,
        context=sample_job_context,
        page_markdown=sample_markdown,
        tasks=[],
    )
    return ParagraphAgentDeps(
        job=job,
        page_image=sample_image,
        current_markdown=sample_markdown,
    )


# =============================================================================
# _get_subagent_key Tests
# =============================================================================


class TestGetSubagentKey:
    """Tests for _get_subagent_key function."""

    def test_generates_key_with_task_type_and_target(self) -> None:
        """Key should combine task type value and target."""
        key = _get_subagent_key(TaskType.PAGE_ARTIFACT_REMOVAL, "line:5")
        assert key == "page_artifact_removal:line:5"

    def test_different_task_types_generate_different_keys(self) -> None:
        """Different task types should generate different keys."""
        key1 = _get_subagent_key(TaskType.PAGE_ARTIFACT_REMOVAL, "target1")
        key2 = _get_subagent_key(TaskType.FOOTNOTE_CORRECTION, "target1")
        assert key1 != key2

    def test_different_targets_generate_different_keys(self) -> None:
        """Different targets should generate different keys."""
        key1 = _get_subagent_key(TaskType.TYPOGRAPHY_FIX, "target1")
        key2 = _get_subagent_key(TaskType.TYPOGRAPHY_FIX, "target2")
        assert key1 != key2


# =============================================================================
# _create_failed_result Tests
# =============================================================================


class TestCreateFailedResult:
    """Tests for _create_failed_result function.

    With the consolidated TextStructure subagent, all text structure task
    types (page artifacts, footnotes, citations, lists) return TextStructureResult.
    Typography tasks return TypographyResult.
    """

    def test_creates_text_structure_result_for_page_artifact(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should create TextStructureResult for page artifact tasks."""
        result = _create_failed_result(
            TaskType.PAGE_ARTIFACT_REMOVAL,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, TextStructureResult)
        assert result.confidence == 0.0
        assert "Test error" in result.reasoning
        assert result.corrections == []

    def test_creates_text_structure_result_for_footnote(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should create TextStructureResult for footnote tasks."""
        result = _create_failed_result(
            TaskType.FOOTNOTE_CORRECTION,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, TextStructureResult)
        assert result.confidence == 0.0
        assert "Test error" in result.reasoning

    def test_creates_text_structure_result_for_citation(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should create TextStructureResult for citation tasks."""
        result = _create_failed_result(
            TaskType.CITATION_LINKING,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, TextStructureResult)
        assert result.confidence == 0.0

    def test_creates_text_structure_result_for_list(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should create TextStructureResult for list tasks."""
        result = _create_failed_result(
            TaskType.LIST_FIX,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, TextStructureResult)
        assert result.confidence == 0.0
        assert result.corrections == []

    def test_creates_typography_result(self, sample_deps: ParagraphAgentDeps) -> None:
        """Should create TypographyResult with zero confidence."""
        result = _create_failed_result(
            TaskType.TYPOGRAPHY_FIX,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, TypographyResult)
        assert result.confidence == 0.0
        assert result.formatting_added == []

    def test_returns_none_for_unsupported_type(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should return None for unsupported task types."""
        result = _create_failed_result(
            TaskType.ALT_TEXT,  # Not a paragraph task type
            "Test error",
            sample_deps,
        )
        assert result is None


# =============================================================================
# _invoke_subagent_for_task Tests
# =============================================================================


class TestInvokeSubagentForTask:
    """Tests for _invoke_subagent_for_task function.

    With the consolidated TextStructure subagent, all text structure tasks
    invoke the same consolidated subagent.
    """

    @pytest.mark.asyncio
    async def test_invokes_text_structure_subagent_for_page_artifact(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke text structure subagent for PAGE_ARTIFACT_REMOVAL."""
        task = Task(
            task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
            target="line:5",
            context="Found page break marker ---",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = TextStructureResult(
                confidence=0.9,
                reasoning="Removed artifact",
                corrections=[
                    TextStructureCorrection(
                        task_type="page_artifact",
                        before="---",
                        after="",
                        confidence=0.9,
                        reasoning="Page break marker",
                    )
                ],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, TextStructureResult)
            assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_invokes_text_structure_subagent_for_footnote(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke text structure subagent for FOOTNOTE_CORRECTION."""
        task = Task(
            task_type=TaskType.FOOTNOTE_CORRECTION,
            target="footnote:1",
            context="Check footnote placement",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = TextStructureResult(
                confidence=0.85,
                reasoning="Fixed footnote",
                corrections=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, TextStructureResult)

    @pytest.mark.asyncio
    async def test_invokes_text_structure_subagent_for_citation(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke text structure subagent for CITATION_LINKING."""
        task = Task(
            task_type=TaskType.CITATION_LINKING,
            target="citation:1",
            context="Link citation [1]",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = TextStructureResult(
                confidence=0.7,
                reasoning="Linked citation",
                corrections=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, TextStructureResult)

    @pytest.mark.asyncio
    async def test_invokes_text_structure_subagent_for_list(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke text structure subagent for LIST_FIX."""
        task = Task(
            task_type=TaskType.LIST_FIX,
            target="list:1",
            context="1. Item one\n2. Item two",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = TextStructureResult(
                confidence=0.8,
                reasoning="Fixed list",
                corrections=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, TextStructureResult)

    @pytest.mark.asyncio
    async def test_invokes_typography_subagent(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke typography subagent for TYPOGRAPHY_FIX."""
        task = Task(
            task_type=TaskType.TYPOGRAPHY_FIX,
            target="typography:1",
            context="Important text that needs bold",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_typography_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = TypographyResult(
                confidence=0.75,
                reasoning="Added bold",
                corrected_markdown="**Important** text",
                formatting_added=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, TypographyResult)

    @pytest.mark.asyncio
    async def test_returns_none_for_unsupported_task(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should return None for unsupported task types."""
        task = Task(
            task_type=TaskType.ALT_TEXT,  # Not a paragraph task
            target="fig:1",
            context="Generate alt text",
        )
        result = await _invoke_subagent_for_task(task, sample_deps)
        assert result is None


# =============================================================================
# _run_subagents_parallel Tests
# =============================================================================


class TestRunSubagentsParallel:
    """Tests for _run_subagents_parallel function.

    The consolidated approach:
    - All text structure tasks (page artifacts, footnotes, citations, lists)
      are handled by ONE TextStructure subagent call
    - Typography tasks are batched separately
    - Maximum of 2 LLM calls per page (TextStructure + Typography)
    """

    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_no_tasks(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Should return empty dict when job has no tasks."""
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=[],
        )
        result = await _run_subagents_parallel(job, sample_deps)
        assert result == {}

    @pytest.mark.asyncio
    async def test_text_structure_tasks_consolidated(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Multiple text structure tasks should result in single LLM call."""
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="artifact:1",
                context="---",
            ),
            Task(
                task_type=TaskType.FOOTNOTE_CORRECTION,
                target="footnote:1",
                context="[^1]",
            ),
            Task(
                task_type=TaskType.CITATION_LINKING,
                target="citation:1",
                context="[1]",
            ),
            Task(
                task_type=TaskType.LIST_FIX,
                target="list:1",
                context="1. Item",
            ),
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent"
        ) as mock_text_structure:
            mock_text_structure.return_value = TextStructureResult(
                confidence=0.9,
                reasoning="Fixed all",
                corrections=[
                    TextStructureCorrection(
                        task_type="page_artifact",
                        before="---",
                        after="",
                        confidence=0.9,
                        reasoning="Removed artifact",
                    )
                ],
            )
            result = await _run_subagents_parallel(job, sample_deps)

            # Only ONE call to text structure subagent
            assert mock_text_structure.call_count == 1

            # Result stored under single key
            assert "text_structure:page" in result
            assert isinstance(result["text_structure:page"], TextStructureResult)

    @pytest.mark.asyncio
    async def test_typography_tasks_batched_separately(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Typography tasks should be batched into single call."""
        tasks = [
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:1",
                context="bold text 1",
            ),
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:2",
                context="bold text 2",
            ),
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        with patch(
            "src.agents.paragraph_agent.invoke_typography_subagent_batch"
        ) as mock_batch:
            mock_batch.return_value = [
                (
                    "typography:1",
                    TypographyResult(
                        confidence=0.9,
                        reasoning="Added bold",
                        corrected_markdown="**bold text 1**",
                        formatting_added=[],
                    ),
                ),
                (
                    "typography:2",
                    TypographyResult(
                        confidence=0.85,
                        reasoning="Added bold",
                        corrected_markdown="**bold text 2**",
                        formatting_added=[],
                    ),
                ),
            ]
            result = await _run_subagents_parallel(job, sample_deps)

            # Only ONE call to typography batch
            assert mock_batch.call_count == 1

            # Results mapped to task keys
            assert "typography_fix:typography:1" in result
            assert "typography_fix:typography:2" in result

    @pytest.mark.asyncio
    async def test_mixed_tasks_run_in_parallel(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Text structure and typography should run in parallel (2 calls max)."""
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="artifact:1",
                context="---",
            ),
            Task(
                task_type=TaskType.FOOTNOTE_CORRECTION,
                target="footnote:1",
                context="[^1]",
            ),
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:1",
                context="bold text",
            ),
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        call_times: list[float] = []

        async def mock_text_structure(*args, **kwargs):
            import time

            call_times.append(time.time())
            await asyncio.sleep(0.01)
            return TextStructureResult(
                confidence=0.9,
                reasoning="Fixed",
                corrections=[],
            )

        async def mock_typography_batch(text_regions, page_image):
            import time

            call_times.append(time.time())
            await asyncio.sleep(0.01)
            return [
                (
                    target,
                    TypographyResult(
                        confidence=0.9,
                        reasoning="Fixed",
                        corrected_markdown="**bold**",
                        formatting_added=[],
                    ),
                )
                for target, _ in text_regions
            ]

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent",
            side_effect=mock_text_structure,
        ), patch(
            "src.agents.paragraph_agent.invoke_typography_subagent_batch",
            side_effect=mock_typography_batch,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # Should have results for both
            assert "text_structure:page" in result
            assert "typography_fix:typography:1" in result

            # Both calls should start near-simultaneously (parallel execution)
            if len(call_times) >= 2:
                time_spread = max(call_times) - min(call_times)
                assert time_spread < 0.05  # 50ms tolerance

    @pytest.mark.asyncio
    async def test_text_structure_failure_handled_gracefully(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Exception in text structure subagent should create failed result."""
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="artifact:1",
                context="---",
            ),
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:1",
                context="bold",
            ),
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        async def mock_typography_batch(text_regions, page_image):
            return [
                (
                    target,
                    TypographyResult(
                        confidence=0.9,
                        reasoning="success",
                        corrected_markdown="**bold**",
                        formatting_added=[],
                    ),
                )
                for target, _ in text_regions
            ]

        with patch(
            "src.agents.paragraph_agent.invoke_text_structure_subagent",
            side_effect=RuntimeError("Connection error"),
        ), patch(
            "src.agents.paragraph_agent.invoke_typography_subagent_batch",
            side_effect=mock_typography_batch,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # Text structure should have failed result
            text_result = result["text_structure:page"]
            assert isinstance(text_result, TextStructureResult)
            assert text_result.confidence == 0.0
            assert "Connection error" in text_result.reasoning

            # Typography should have succeeded
            typo_result = result["typography_fix:typography:1"]
            assert isinstance(typo_result, TypographyResult)
            assert typo_result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_typography_batch_failure_handled_gracefully(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Exception in typography batch should create failed results for each task."""
        tasks = [
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:1",
                context="bold 1",
            ),
            Task(
                task_type=TaskType.TYPOGRAPHY_FIX,
                target="typography:2",
                context="bold 2",
            ),
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        with patch(
            "src.agents.paragraph_agent.invoke_typography_subagent_batch",
            side_effect=RuntimeError("Batch error"),
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # Both typography tasks should have failed results
            for key in ["typography_fix:typography:1", "typography_fix:typography:2"]:
                assert key in result
                typo_result = result[key]
                assert isinstance(typo_result, TypographyResult)
                assert typo_result.confidence == 0.0
                assert "Batch error" in typo_result.reasoning


# =============================================================================
# Text Structure Task Types Tests
# =============================================================================


class TestTextStructureTaskTypes:
    """Tests for TEXT_STRUCTURE_TASK_TYPES constant."""

    def test_includes_all_text_structure_types(self) -> None:
        """Should include all text structure task types."""
        expected = {
            TaskType.PAGE_ARTIFACT_REMOVAL,
            TaskType.FOOTNOTE_CORRECTION,
            TaskType.CITATION_LINKING,
            TaskType.LIST_FIX,
        }
        assert TEXT_STRUCTURE_TASK_TYPES == expected

    def test_does_not_include_typography(self) -> None:
        """Typography should NOT be in text structure types."""
        assert TaskType.TYPOGRAPHY_FIX not in TEXT_STRUCTURE_TASK_TYPES


# =============================================================================
# Constant Tests
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_max_concurrent_subagents_is_five(self) -> None:
        """MAX_CONCURRENT_SUBAGENTS should be 5 for rate limiting."""
        assert MAX_CONCURRENT_SUBAGENTS == 5

    def test_max_concurrent_subagents_is_positive(self) -> None:
        """MAX_CONCURRENT_SUBAGENTS should be a positive integer."""
        assert MAX_CONCURRENT_SUBAGENTS > 0
        assert isinstance(MAX_CONCURRENT_SUBAGENTS, int)
