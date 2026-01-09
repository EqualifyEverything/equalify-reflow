"""Unit tests for parallel subagent execution in ParagraphAgent.

Tests the parallelization optimization that runs multiple subagent calls
concurrently before the main agent reasoning loop.
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
    ParagraphAgentDeps,
    _create_failed_result,
    _get_subagent_key,
    _invoke_subagent_for_task,
    _run_subagents_parallel,
)
from src.agents.subagents.types import (
    CitationResult,
    FootnoteResult,
    ListResult,
    PageArtifactResult,
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
    """Tests for _create_failed_result function."""

    def test_creates_page_artifact_result(self, sample_deps: ParagraphAgentDeps) -> None:
        """Should create PageArtifactResult with zero confidence."""
        result = _create_failed_result(
            TaskType.PAGE_ARTIFACT_REMOVAL,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, PageArtifactResult)
        assert result.confidence == 0.0
        assert "Test error" in result.reasoning
        assert result.cleaned_text == sample_deps.current_markdown

    def test_creates_footnote_result(self, sample_deps: ParagraphAgentDeps) -> None:
        """Should create FootnoteResult with zero confidence."""
        result = _create_failed_result(
            TaskType.FOOTNOTE_CORRECTION,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, FootnoteResult)
        assert result.confidence == 0.0
        assert "Test error" in result.reasoning

    def test_creates_citation_result(self, sample_deps: ParagraphAgentDeps) -> None:
        """Should create CitationResult with zero confidence."""
        result = _create_failed_result(
            TaskType.CITATION_LINKING,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, CitationResult)
        assert result.confidence == 0.0
        assert result.bibliography_found is False

    def test_creates_list_result(self, sample_deps: ParagraphAgentDeps) -> None:
        """Should create ListResult with zero confidence."""
        result = _create_failed_result(
            TaskType.LIST_FIX,
            "Test error",
            sample_deps,
        )
        assert isinstance(result, ListResult)
        assert result.confidence == 0.0
        assert result.issues_fixed == []

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
    """Tests for _invoke_subagent_for_task function."""

    @pytest.mark.asyncio
    async def test_invokes_page_artifact_subagent(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke page artifact subagent for PAGE_ARTIFACT_REMOVAL."""
        task = Task(
            task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
            target="line:5",
            context="Found page break marker ---",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_page_artifact_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = PageArtifactResult(
                confidence=0.9,
                reasoning="Removed artifact",
                cleaned_text="cleaned",
                artifacts_removed=["---"],
                words_rejoined=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, PageArtifactResult)
            assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_invokes_footnote_subagent(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke footnote subagent for FOOTNOTE_CORRECTION."""
        task = Task(
            task_type=TaskType.FOOTNOTE_CORRECTION,
            target="footnote:1",
            context="Check footnote placement",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_footnote_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = FootnoteResult(
                confidence=0.85,
                reasoning="Fixed footnote",
                corrected_markdown="fixed",
                footnotes_fixed=[],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, FootnoteResult)

    @pytest.mark.asyncio
    async def test_invokes_citation_subagent(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke citation subagent for CITATION_LINKING."""
        task = Task(
            task_type=TaskType.CITATION_LINKING,
            target="citation:1",
            context="Link citation [1]",
        )

        with patch(
            "src.agents.paragraph_agent.invoke_citation_subagent"
        ) as mock_invoke:
            mock_invoke.return_value = CitationResult(
                confidence=0.7,
                reasoning="Linked citation",
                corrected_markdown="linked",
                citations_linked=[],
                bibliography_found=True,
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, CitationResult)

    @pytest.mark.asyncio
    async def test_invokes_list_subagent(
        self, sample_deps: ParagraphAgentDeps
    ) -> None:
        """Should invoke list subagent for LIST_FIX."""
        task = Task(
            task_type=TaskType.LIST_FIX,
            target="list:1",
            context="1. Item one\n2. Item two",
        )

        with patch("src.agents.paragraph_agent.invoke_list_subagent") as mock_invoke:
            mock_invoke.return_value = ListResult(
                confidence=0.8,
                reasoning="Fixed list",
                corrected_markdown="fixed list",
                issues_fixed=["numbering"],
            )
            result = await _invoke_subagent_for_task(task, sample_deps)

            mock_invoke.assert_called_once()
            assert isinstance(result, ListResult)

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
    """Tests for _run_subagents_parallel function."""

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
    async def test_single_task_skips_parallelization(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Single task should bypass asyncio.gather for efficiency."""
        task = Task(
            task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
            target="line:5",
            context="---",
        )
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=[task],
        )

        with patch(
            "src.agents.paragraph_agent._invoke_subagent_for_task"
        ) as mock_invoke:
            mock_invoke.return_value = PageArtifactResult(
                confidence=0.9,
                reasoning="test",
                cleaned_text="clean",
                artifacts_removed=[],
                words_rejoined=[],
            )
            result = await _run_subagents_parallel(job, sample_deps)

            mock_invoke.assert_called_once_with(task, sample_deps)
            assert len(result) == 1
            key = _get_subagent_key(TaskType.PAGE_ARTIFACT_REMOVAL, "line:5")
            assert key in result

    @pytest.mark.asyncio
    async def test_all_five_subagents_run_in_parallel(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """All 5 subagent types should run concurrently."""
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

        # Track call order to verify parallel execution
        call_times: list[float] = []

        async def mock_subagent_call(task: Task, deps: ParagraphAgentDeps):
            """Mock that records when it was called."""
            import time

            call_times.append(time.time())
            await asyncio.sleep(0.01)  # Small delay to verify concurrency
            if task.task_type == TaskType.PAGE_ARTIFACT_REMOVAL:
                return PageArtifactResult(
                    confidence=0.9,
                    reasoning="test",
                    cleaned_text="clean",
                    artifacts_removed=[],
                    words_rejoined=[],
                )
            elif task.task_type == TaskType.FOOTNOTE_CORRECTION:
                return FootnoteResult(
                    confidence=0.85,
                    reasoning="test",
                    corrected_markdown="fixed",
                    footnotes_fixed=[],
                )
            elif task.task_type == TaskType.CITATION_LINKING:
                return CitationResult(
                    confidence=0.7,
                    reasoning="test",
                    corrected_markdown="linked",
                    citations_linked=[],
                    bibliography_found=True,
                )
            elif task.task_type == TaskType.LIST_FIX:
                return ListResult(
                    confidence=0.8,
                    reasoning="test",
                    corrected_markdown="fixed list",
                    issues_fixed=[],
                )
            elif task.task_type == TaskType.TYPOGRAPHY_FIX:
                return TypographyResult(
                    confidence=0.75,
                    reasoning="test",
                    corrected_markdown="**bold**",
                    formatting_added=[],
                )
            return None

        with patch(
            "src.agents.paragraph_agent._invoke_subagent_for_task",
            side_effect=mock_subagent_call,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # All 5 results should be present
            assert len(result) == 5

            # Verify keys are correct
            assert "page_artifact_removal:artifact:1" in result
            assert "footnote_correction:footnote:1" in result
            assert "citation_linking:citation:1" in result
            assert "list_fix:list:1" in result
            assert "typography_fix:typography:1" in result

            # Verify all calls started within a short window (parallel execution)
            # With sequential execution, calls would be spread apart
            if len(call_times) >= 2:
                time_spread = max(call_times) - min(call_times)
                # Parallel execution should have all calls start nearly simultaneously
                assert time_spread < 0.05  # 50ms tolerance

    @pytest.mark.asyncio
    async def test_one_subagent_fails_others_succeed(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Exception in one subagent should not affect others."""
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="artifact:1",
                context="---",
            ),
            Task(
                task_type=TaskType.FOOTNOTE_CORRECTION,
                target="footnote:1",  # This one will fail
                context="[^1]",
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

        async def mock_with_failure(task: Task, deps: ParagraphAgentDeps):
            """Mock that fails for footnote task."""
            if task.task_type == TaskType.FOOTNOTE_CORRECTION:
                raise ValueError("Simulated footnote subagent failure")
            elif task.task_type == TaskType.PAGE_ARTIFACT_REMOVAL:
                return PageArtifactResult(
                    confidence=0.9,
                    reasoning="success",
                    cleaned_text="clean",
                    artifacts_removed=[],
                    words_rejoined=[],
                )
            elif task.task_type == TaskType.TYPOGRAPHY_FIX:
                return TypographyResult(
                    confidence=0.8,
                    reasoning="success",
                    corrected_markdown="**bold**",
                    formatting_added=[],
                )
            return None

        with patch(
            "src.agents.paragraph_agent._invoke_subagent_for_task",
            side_effect=mock_with_failure,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # All 3 tasks should have results
            assert len(result) == 3

            # Successful tasks should have proper results
            artifact_result = result["page_artifact_removal:artifact:1"]
            assert isinstance(artifact_result, PageArtifactResult)
            assert artifact_result.confidence == 0.9

            typography_result = result["typography_fix:typography:1"]
            assert isinstance(typography_result, TypographyResult)
            assert typography_result.confidence == 0.8

            # Failed task should have zero-confidence result
            footnote_result = result["footnote_correction:footnote:1"]
            assert isinstance(footnote_result, FootnoteResult)
            assert footnote_result.confidence == 0.0
            assert "Simulated footnote subagent failure" in footnote_result.reasoning

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_calls(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Semaphore should limit concurrent calls to MAX_CONCURRENT_SUBAGENTS."""
        # Create more tasks than the semaphore limit
        num_tasks = MAX_CONCURRENT_SUBAGENTS + 3
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target=f"artifact:{i}",
                context=f"Context {i}",
            )
            for i in range(num_tasks)
        ]
        job = Job(
            job_id="test",
            page=1,
            job_type=JobType.PARAGRAPH,
            context=sample_job_context,
            page_markdown="# Test",
            tasks=tasks,
        )

        # Track concurrent execution count
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_with_tracking(task: Task, deps: ParagraphAgentDeps):
            """Mock that tracks concurrent execution count."""
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)

            await asyncio.sleep(0.05)  # Simulate work

            async with lock:
                current_concurrent -= 1

            return PageArtifactResult(
                confidence=0.9,
                reasoning="test",
                cleaned_text="clean",
                artifacts_removed=[],
                words_rejoined=[],
            )

        with patch(
            "src.agents.paragraph_agent._invoke_subagent_for_task",
            side_effect=mock_with_tracking,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # All tasks should complete
            assert len(result) == num_tasks

            # Max concurrent should not exceed semaphore limit
            assert max_concurrent <= MAX_CONCURRENT_SUBAGENTS

    @pytest.mark.asyncio
    async def test_results_correctly_mapped_to_tasks(
        self, sample_deps: ParagraphAgentDeps, sample_job_context: JobContext
    ) -> None:
        """Results should be mapped to correct task identifiers."""
        tasks = [
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="unique_target_A",
                context="context A",
            ),
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="unique_target_B",
                context="context B",
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

        results_by_target = {}

        async def mock_capture_target(task: Task, deps: ParagraphAgentDeps):
            """Mock that returns result tied to target."""
            result = PageArtifactResult(
                confidence=0.9,
                reasoning=f"Result for {task.target}",
                cleaned_text=f"cleaned_{task.target}",
                artifacts_removed=[],
                words_rejoined=[],
            )
            results_by_target[task.target] = result
            return result

        with patch(
            "src.agents.paragraph_agent._invoke_subagent_for_task",
            side_effect=mock_capture_target,
        ):
            result = await _run_subagents_parallel(job, sample_deps)

            # Verify correct mapping
            key_a = "page_artifact_removal:unique_target_A"
            key_b = "page_artifact_removal:unique_target_B"

            assert result[key_a].reasoning == "Result for unique_target_A"
            assert result[key_b].reasoning == "Result for unique_target_B"


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
