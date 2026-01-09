"""Unit tests for ParagraphAgent.

Tests the ParagraphAgent's tools, dependencies, and output models.
Integration tests with actual subagents are in tests/integration/.
"""

from unittest.mock import MagicMock

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
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    PARAGRAPH_AGENT_SYSTEM_PROMPT,
    FindTextResult,
    JobResult,
    ParagraphAgentDeps,
    ParagraphAgentOutput,
    ProposeEditResult,
    ReadResult,
    ViewResult,
    find_text_tool,
    propose_edit_tool,
    read_context_tool,
    view_page_tool,
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
def sample_job(sample_job_context: JobContext, sample_markdown: str) -> Job:
    """Create a sample paragraph job."""
    return Job(
        job_id="test-job-123",
        page=1,
        job_type=JobType.PARAGRAPH,
        context=sample_job_context,
        page_markdown=sample_markdown,
        tasks=[
            Task(
                task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="line:5",
                context="Found page break marker ---",
            ),
            Task(
                task_type=TaskType.FOOTNOTE_CORRECTION,
                target="footnote:1",
                context="Check footnote placement",
            ),
        ],
    )


@pytest.fixture
def sample_deps(
    sample_job: Job,
    sample_image: Image.Image,
    sample_markdown: str,
) -> ParagraphAgentDeps:
    """Create sample dependencies."""
    return ParagraphAgentDeps(
        job=sample_job,
        page_image=sample_image,
        current_markdown=sample_markdown,
    )


@pytest.fixture
def mock_run_context(sample_deps: ParagraphAgentDeps) -> MagicMock:
    """Create a mock RunContext with dependencies."""
    ctx = MagicMock()
    ctx.deps = sample_deps
    return ctx


# =============================================================================
# System Prompt Tests
# =============================================================================


class TestSystemPrompt:
    """Tests for the ParagraphAgent system prompt."""

    def test_system_prompt_exists(self) -> None:
        """System prompt should be defined."""
        assert PARAGRAPH_AGENT_SYSTEM_PROMPT is not None
        assert len(PARAGRAPH_AGENT_SYSTEM_PROMPT) > 100

    def test_system_prompt_describes_domain(self) -> None:
        """System prompt should describe the paragraph domain."""
        assert "page break" in PARAGRAPH_AGENT_SYSTEM_PROMPT.lower()
        assert "footnote" in PARAGRAPH_AGENT_SYSTEM_PROMPT.lower()
        assert "citation" in PARAGRAPH_AGENT_SYSTEM_PROMPT.lower()
        assert "list" in PARAGRAPH_AGENT_SYSTEM_PROMPT.lower()
        assert "typography" in PARAGRAPH_AGENT_SYSTEM_PROMPT.lower()

    def test_system_prompt_describes_tools(self) -> None:
        """System prompt should describe available tools."""
        assert "view_page" in PARAGRAPH_AGENT_SYSTEM_PROMPT
        assert "find_text" in PARAGRAPH_AGENT_SYSTEM_PROMPT
        assert "propose_edit" in PARAGRAPH_AGENT_SYSTEM_PROMPT

    def test_system_prompt_describes_confidence_thresholds(self) -> None:
        """System prompt should describe confidence decision logic."""
        assert "0.8" in PARAGRAPH_AGENT_SYSTEM_PROMPT
        assert "0.5" in PARAGRAPH_AGENT_SYSTEM_PROMPT
        assert "needs_review" in PARAGRAPH_AGENT_SYSTEM_PROMPT


# =============================================================================
# Dependencies Tests
# =============================================================================


class TestParagraphAgentDeps:
    """Tests for ParagraphAgentDeps dataclass."""

    def test_deps_has_required_fields(
        self,
        sample_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should have all required fields."""
        deps = ParagraphAgentDeps(
            job=sample_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )
        assert deps.job == sample_job
        assert deps.page_image == sample_image
        assert deps.current_markdown == sample_markdown

    def test_deps_has_default_values(
        self,
        sample_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should have sensible defaults."""
        deps = ParagraphAgentDeps(
            job=sample_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )
        assert deps.pending_edits == []
        assert deps.validated_edits == []
        assert deps.full_document_markdown is None
        assert deps.dictionary == []
        assert deps.event_bus is None

    def test_deps_supports_full_document(
        self,
        sample_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should support full document for citations."""
        full_doc = "Page 1\n\nPage 2\n\n## References\n1. Author (2024)"
        deps = ParagraphAgentDeps(
            job=sample_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
            full_document_markdown=full_doc,
        )
        assert deps.full_document_markdown == full_doc


# =============================================================================
# Output Model Tests
# =============================================================================


class TestParagraphAgentOutput:
    """Tests for ParagraphAgentOutput model."""

    def test_output_has_default_values(self) -> None:
        """Output should have sensible defaults."""
        output = ParagraphAgentOutput()
        assert output.tasks_applied_auto == []
        assert output.tasks_applied_review == []
        assert output.tasks_skipped == []
        assert output.tasks_failed == []
        assert output.summary == ""

    def test_output_tracks_different_outcomes(self) -> None:
        """Output should track different task outcomes."""
        output = ParagraphAgentOutput(
            tasks_applied_auto=["PAGE_ARTIFACT_REMOVAL: Removed ---"],
            tasks_applied_review=["FOOTNOTE_CORRECTION: Linked [^1]"],
            tasks_skipped=["CITATION_LINKING: Low confidence"],
            tasks_failed=["LIST_FIX: Error occurred"],
            summary="2 applied, 1 skipped, 1 failed",
        )
        assert len(output.tasks_applied_auto) == 1
        assert len(output.tasks_applied_review) == 1
        assert len(output.tasks_skipped) == 1
        assert len(output.tasks_failed) == 1


# =============================================================================
# Tool Result Types Tests
# =============================================================================


class TestToolResultTypes:
    """Tests for tool result types."""

    def test_view_result(self) -> None:
        """ViewResult should have all fields."""
        result = ViewResult(
            success=True,
            description="Page 1 image shown",
            markdown_content="# Title\n\nParagraph",
        )
        assert result.success is True
        assert result.description == "Page 1 image shown"
        assert result.markdown_content == "# Title\n\nParagraph"

    def test_read_result(self) -> None:
        """ReadResult should have all fields."""
        result = ReadResult(
            content="Line content",
            start_line=10,
            end_line=15,
            total_lines=100,
        )
        assert result.content == "Line content"
        assert result.start_line == 10
        assert result.end_line == 15
        assert result.total_lines == 100

    def test_find_text_result_found(self) -> None:
        """FindTextResult should handle found case."""
        result = FindTextResult(
            found=True,
            exact_match="the quick brown fox",
            context_before="Previous line",
            context_after="Next line",
            line_number=42,
        )
        assert result.found is True
        assert result.exact_match == "the quick brown fox"
        assert result.line_number == 42

    def test_find_text_result_not_found(self) -> None:
        """FindTextResult should handle not found case."""
        result = FindTextResult(
            found=False,
            error="Pattern not found: xyz...",
        )
        assert result.found is False
        assert result.error is not None

    def test_propose_edit_result_accepted(self) -> None:
        """ProposeEditResult should handle accepted case."""
        result = ProposeEditResult(
            accepted=True,
            applied=True,
        )
        assert result.accepted is True
        assert result.applied is True

    def test_propose_edit_result_rejected(self) -> None:
        """ProposeEditResult should handle rejected case."""
        result = ProposeEditResult(
            accepted=False,
            feedback="Target text not found",
        )
        assert result.accepted is False
        assert result.feedback is not None


# =============================================================================
# View Tool Tests
# =============================================================================


class TestViewPageTool:
    """Tests for view_page_tool."""

    @pytest.mark.asyncio
    async def test_view_page_returns_markdown(
        self, mock_run_context: MagicMock
    ) -> None:
        """view_page should return current markdown."""
        result = await view_page_tool(mock_run_context)

        assert result.success is True
        assert result.markdown_content == mock_run_context.deps.current_markdown
        assert "Page 1" in result.description

    @pytest.mark.asyncio
    async def test_view_page_includes_page_number(
        self, mock_run_context: MagicMock
    ) -> None:
        """view_page description should include page number."""
        result = await view_page_tool(mock_run_context)

        assert str(mock_run_context.deps.job.page) in result.description


# =============================================================================
# Analysis Tools Tests
# =============================================================================


class TestFindTextTool:
    """Tests for find_text_tool."""

    @pytest.mark.asyncio
    async def test_find_exact_text(self, mock_run_context: MagicMock) -> None:
        """find_text should find exact text matches."""
        result = await find_text_tool(mock_run_context, "---")

        assert result.found is True
        assert result.exact_match == "---"
        assert result.line_number is not None

    @pytest.mark.asyncio
    async def test_find_text_not_found(self, mock_run_context: MagicMock) -> None:
        """find_text should handle not found case."""
        result = await find_text_tool(mock_run_context, "nonexistent text xyz")

        assert result.found is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_find_text_regex(self, mock_run_context: MagicMock) -> None:
        """find_text should support regex patterns."""
        result = await find_text_tool(mock_run_context, r"\[\^1\]")

        assert result.found is True
        assert "[^1]" in (result.exact_match or "")

    @pytest.mark.asyncio
    async def test_find_text_invalid_regex(self, mock_run_context: MagicMock) -> None:
        """find_text should handle invalid regex gracefully."""
        result = await find_text_tool(mock_run_context, "[invalid regex")

        assert result.found is False
        assert "Invalid regex" in (result.error or "")


class TestReadContextTool:
    """Tests for read_context_tool."""

    @pytest.mark.asyncio
    async def test_read_specific_lines(self, mock_run_context: MagicMock) -> None:
        """read_context should return specific lines."""
        result = await read_context_tool(mock_run_context, 1, 3)

        assert result.start_line == 1
        assert result.end_line == 3
        assert "Document Title" in result.content

    @pytest.mark.asyncio
    async def test_read_context_clamps_to_bounds(
        self, mock_run_context: MagicMock
    ) -> None:
        """read_context should clamp line numbers to valid range."""
        result = await read_context_tool(mock_run_context, -10, 1000)

        assert result.start_line >= 1
        assert result.end_line <= result.total_lines

    @pytest.mark.asyncio
    async def test_read_context_total_lines(self, mock_run_context: MagicMock) -> None:
        """read_context should report total line count."""
        result = await read_context_tool(mock_run_context, 1, 5)

        assert result.total_lines > 0


# =============================================================================
# Propose Edit Tool Tests
# =============================================================================


class TestProposeEditTool:
    """Tests for propose_edit_tool."""

    @pytest.mark.asyncio
    async def test_propose_edit_applies_valid_edit(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit should apply valid edits."""
        result = await propose_edit_tool(
            mock_run_context,
            before="---",
            after="",
            reasoning="Remove page break marker",
            needs_review=False,
            task_type="page_artifact_removal",
        )

        assert result.accepted is True
        assert result.applied is True
        assert "---" not in mock_run_context.deps.current_markdown
        assert len(mock_run_context.deps.validated_edits) == 1

    @pytest.mark.asyncio
    async def test_propose_edit_sets_needs_review(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit should set needs_review flag on ledger entry."""
        result = await propose_edit_tool(
            mock_run_context,
            before="---",
            after="",
            reasoning="Remove page break marker",
            needs_review=True,
            task_type="page_artifact_removal",
        )

        assert result.accepted is True
        assert result.applied is True

        entry = mock_run_context.deps.validated_edits[0]
        assert entry.needs_review is True
        assert entry.confidence == 0.6  # Lower confidence for needs_review

    @pytest.mark.asyncio
    async def test_propose_edit_high_confidence(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit with needs_review=False should set high confidence."""
        await propose_edit_tool(
            mock_run_context,
            before="---",
            after="",
            reasoning="Remove page break marker",
            needs_review=False,
            task_type="page_artifact_removal",
        )

        entry = mock_run_context.deps.validated_edits[0]
        assert entry.needs_review is False
        assert entry.confidence == 0.9

    @pytest.mark.asyncio
    async def test_propose_edit_rejects_not_found(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit should reject when target text not found."""
        result = await propose_edit_tool(
            mock_run_context,
            before="nonexistent text that does not exist anywhere",
            after="replacement",
            reasoning="Test",
        )

        assert result.accepted is False
        assert "not found" in (result.feedback or "").lower()

    @pytest.mark.asyncio
    async def test_propose_edit_parses_task_type(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit should parse valid task types."""
        await propose_edit_tool(
            mock_run_context,
            before="---",
            after="",
            reasoning="Test",
            task_type="footnote_correction",
        )

        entry = mock_run_context.deps.validated_edits[0]
        assert entry.action == TaskType.FOOTNOTE_CORRECTION

    @pytest.mark.asyncio
    async def test_propose_edit_fallback_task_type(
        self, mock_run_context: MagicMock
    ) -> None:
        """propose_edit should fallback to FORMAT_FIX for invalid task types."""
        await propose_edit_tool(
            mock_run_context,
            before="---",
            after="",
            reasoning="Test",
            task_type="invalid_type",
        )

        entry = mock_run_context.deps.validated_edits[0]
        assert entry.action == TaskType.FORMAT_FIX


# =============================================================================
# JobResult Tests
# =============================================================================


class TestJobResult:
    """Tests for JobResult dataclass."""

    def test_job_result_has_all_fields(self) -> None:
        """JobResult should have all required fields."""
        result = JobResult(
            job_id="test-job",
            success=True,
            updated_markdown="# Updated",
            ledger_entries=[],
            tasks_completed=2,
            tasks_failed=0,
            input_tokens=100,
            output_tokens=50,
            duration_ms=1500,
        )
        assert result.job_id == "test-job"
        assert result.success is True
        assert result.tasks_completed == 2
        assert result.duration_ms == 1500

    def test_job_result_error_field(self) -> None:
        """JobResult should support error field."""
        result = JobResult(
            job_id="test-job",
            success=False,
            updated_markdown="",
            ledger_entries=[],
            tasks_completed=0,
            tasks_failed=1,
            input_tokens=0,
            output_tokens=0,
            duration_ms=100,
            error="Agent failed to respond",
        )
        assert result.success is False
        assert result.error == "Agent failed to respond"


# =============================================================================
# Confidence Constants Tests
# =============================================================================


class TestConfidenceConstants:
    """Tests that confidence constants are properly imported."""

    def test_confidence_constants_available(self) -> None:
        """Confidence constants should be imported from subagents."""
        assert CONFIDENCE_AUTO_APPLY == 0.8
        assert CONFIDENCE_APPLY_WITH_REVIEW == 0.5
