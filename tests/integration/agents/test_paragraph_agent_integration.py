"""Integration tests for ParagraphAgent.

Tests the ParagraphAgent with mock subagents to verify the full workflow:
1. Agent receives job with paragraph tasks
2. Agent calls subagent tools
3. Agent reviews confidence and applies edits
4. Edits go through validation gate
5. Ledger entries are created with appropriate flags
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from src.agents.events import EventBus
from src.agents.models import (
    DocumentType,
    Job,
    JobContext,
    JobType,
    Ledger,
    Task,
    TaskType,
)
from src.agents.paragraph_agent import (
    CONFIDENCE_APPLY_WITH_REVIEW,
    CONFIDENCE_AUTO_APPLY,
    ParagraphAgentDeps,
    execute_with_paragraph_agent,
)
from src.agents.subagents.types import (
    FootnoteResult,
    PageArtifactResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_image() -> Image.Image:
    """Create a sample PIL image for testing."""
    return Image.new("RGB", (800, 600), color="white")


@pytest.fixture
def sample_markdown() -> str:
    """Create sample markdown with paragraph issues."""
    return """# Test Document

This is a paragraph with some content.

---

This paragraph comes after a page break marker that should be removed.

Here is text with a footnote reference[^1].

[^1]: This is the footnote definition at the bottom.
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
def sample_paragraph_job(sample_job_context: JobContext, sample_markdown: str) -> Job:
    """Create a sample paragraph job with multiple tasks."""
    return Job(
        job_id="test-paragraph-job-001",
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
        ],
    )


@pytest.fixture
def sample_ledger() -> Ledger:
    """Create an empty ledger for testing."""
    return Ledger(document_id="test-doc-123", entries=[])


# =============================================================================
# Mock Subagent Results
# =============================================================================


@pytest.fixture
def high_confidence_artifact_result() -> PageArtifactResult:
    """Mock result with high confidence (auto-apply)."""
    return PageArtifactResult(
        confidence=0.92,
        reasoning="Found and removed page break marker ---",
        cleaned_text="This paragraph comes after a page break marker that should be removed.",
        artifacts_removed=["---"],
        words_rejoined=[],
    )


@pytest.fixture
def medium_confidence_footnote_result() -> FootnoteResult:
    """Mock result with medium confidence (needs review)."""
    return FootnoteResult(
        confidence=0.65,
        reasoning="Linked footnote but uncertain about placement",
        corrected_markdown="Text with [^1] properly linked\n\n[^1]: Definition",
        footnotes_fixed=[{"marker": "[^1]", "action": "linked", "definition": "..."}],
    )


@pytest.fixture
def low_confidence_artifact_result() -> PageArtifactResult:
    """Mock result with low confidence (skip)."""
    return PageArtifactResult(
        confidence=0.35,
        reasoning="Uncertain if this is actually a page break marker",
        cleaned_text="---",  # No change
        artifacts_removed=[],
        words_rejoined=[],
    )


# =============================================================================
# ParagraphAgentDeps Tests
# =============================================================================


class TestParagraphAgentDepsIntegration:
    """Integration tests for ParagraphAgentDeps."""

    def test_deps_tracks_validated_edits(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should track validated edits as they are applied."""
        deps = ParagraphAgentDeps(
            job=sample_paragraph_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )

        # Initially empty
        assert deps.validated_edits == []

        # After adding an edit, it should be tracked
        from src.agents.models import LedgerEntry

        entry = LedgerEntry(
            job_id=sample_paragraph_job.job_id,
            page=1,
            action=TaskType.PAGE_ARTIFACT_REMOVAL,
            target="test",
            before="---",
            after="",
            reasoning="Test",
        )
        deps.validated_edits.append(entry)

        assert len(deps.validated_edits) == 1

    def test_deps_updates_markdown(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should allow markdown updates."""
        deps = ParagraphAgentDeps(
            job=sample_paragraph_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )

        original = deps.current_markdown
        deps.current_markdown = deps.current_markdown.replace("---", "")

        assert deps.current_markdown != original
        assert "---" not in deps.current_markdown


# =============================================================================
# Confidence Threshold Tests
# =============================================================================


class TestConfidenceThresholds:
    """Tests for confidence-based decision making."""

    def test_high_confidence_auto_applies(self) -> None:
        """High confidence (>= 0.8) should auto-apply without review flag."""
        assert 0.92 >= CONFIDENCE_AUTO_APPLY
        assert 0.85 >= CONFIDENCE_AUTO_APPLY
        assert 0.80 >= CONFIDENCE_AUTO_APPLY

    def test_medium_confidence_needs_review(self) -> None:
        """Medium confidence (0.5-0.8) should apply with review flag."""
        assert 0.65 >= CONFIDENCE_APPLY_WITH_REVIEW
        assert 0.65 < CONFIDENCE_AUTO_APPLY
        assert 0.50 >= CONFIDENCE_APPLY_WITH_REVIEW

    def test_low_confidence_skipped(self) -> None:
        """Low confidence (< 0.5) should be skipped."""
        assert 0.35 < CONFIDENCE_APPLY_WITH_REVIEW
        assert 0.49 < CONFIDENCE_APPLY_WITH_REVIEW


# =============================================================================
# Execute Function Tests (with mocked agent)
# =============================================================================


class TestExecuteWithParagraphAgentMocked:
    """Tests for execute_with_paragraph_agent with mocked agent."""

    @pytest.mark.asyncio
    async def test_execute_creates_job_result(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
        sample_ledger: Ledger,
    ) -> None:
        """Execute should return a JobResult."""
        # Mock the agent to return immediately
        from src.agents.paragraph_agent import ParagraphAgentOutput

        mock_output = ParagraphAgentOutput(
            tasks_applied_auto=["PAGE_ARTIFACT_REMOVAL: Removed ---"],
            tasks_applied_review=[],
            tasks_skipped=[],
            tasks_failed=[],
            summary="1 task applied",
        )

        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)

        with patch(
            "src.agents.paragraph_agent._get_paragraph_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            result = await execute_with_paragraph_agent(
                job=sample_paragraph_job,
                page_image=sample_image,
                current_markdown=sample_markdown,
                full_document_markdown=sample_markdown,
                ledger=sample_ledger,
            )

        assert result.job_id == sample_paragraph_job.job_id
        assert result.tasks_completed == 1
        assert result.tasks_failed == 0
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    @pytest.mark.asyncio
    async def test_execute_handles_agent_failure(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
        sample_ledger: Ledger,
    ) -> None:
        """Execute should handle agent failures gracefully."""
        with patch(
            "src.agents.paragraph_agent._get_paragraph_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=Exception("Agent error"))
            mock_get_agent.return_value = mock_agent

            result = await execute_with_paragraph_agent(
                job=sample_paragraph_job,
                page_image=sample_image,
                current_markdown=sample_markdown,
                full_document_markdown=sample_markdown,
                ledger=sample_ledger,
            )

        assert result.success is False
        assert result.error == "Agent error"
        assert result.tasks_failed == len(sample_paragraph_job.tasks)

    @pytest.mark.asyncio
    async def test_execute_emits_events(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
        sample_ledger: Ledger,
    ) -> None:
        """Execute should emit events to event bus."""
        from src.agents.paragraph_agent import ParagraphAgentOutput

        mock_output = ParagraphAgentOutput(
            tasks_applied_auto=[],
            tasks_applied_review=[],
            tasks_skipped=[],
            tasks_failed=[],
            summary="",
        )

        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value = MagicMock(input_tokens=0, output_tokens=0)

        # Create mock event bus
        mock_event_bus = MagicMock(spec=EventBus)
        mock_event_bus.document_id = "test-doc-id"

        with patch(
            "src.agents.paragraph_agent._get_paragraph_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            await execute_with_paragraph_agent(
                job=sample_paragraph_job,
                page_image=sample_image,
                current_markdown=sample_markdown,
                full_document_markdown=sample_markdown,
                ledger=sample_ledger,
                event_bus=mock_event_bus,
            )

        # Should emit JobStartedEvent and JobCompletedEvent
        assert mock_event_bus.emit.call_count >= 2

    @pytest.mark.asyncio
    async def test_execute_appends_to_ledger(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
        sample_ledger: Ledger,
    ) -> None:
        """Execute should append validated edits to ledger."""
        from src.agents.models import LedgerEntry
        from src.agents.paragraph_agent import ParagraphAgentOutput

        # Create a mock that simulates the agent adding edits
        mock_output = ParagraphAgentOutput(
            tasks_applied_auto=["PAGE_ARTIFACT_REMOVAL: Removed ---"],
            tasks_applied_review=[],
            tasks_skipped=[],
            tasks_failed=[],
            summary="1 task applied",
        )

        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value = MagicMock(input_tokens=0, output_tokens=0)

        # We need to simulate the deps.validated_edits being populated
        def mock_run_side_effect(prompt, deps):
            # Simulate adding an edit
            entry = LedgerEntry(
                job_id=sample_paragraph_job.job_id,
                page=1,
                action=TaskType.PAGE_ARTIFACT_REMOVAL,
                target="paragraph:1",
                before="---",
                after="",
                reasoning="Removed page break",
                confidence=0.9,
                validated=True,
                needs_review=False,
            )
            deps.validated_edits.append(entry)
            return mock_result

        with patch(
            "src.agents.paragraph_agent._get_paragraph_agent"
        ) as mock_get_agent:
            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(side_effect=mock_run_side_effect)
            mock_get_agent.return_value = mock_agent

            await execute_with_paragraph_agent(
                job=sample_paragraph_job,
                page_image=sample_image,
                current_markdown=sample_markdown,
                full_document_markdown=sample_markdown,
                ledger=sample_ledger,
            )

        # Ledger should have the entry
        assert len(sample_ledger.entries) == 1
        assert sample_ledger.entries[0].action == TaskType.PAGE_ARTIFACT_REMOVAL


# =============================================================================
# Tool Integration Tests
# =============================================================================


class TestToolIntegration:
    """Tests for tool interactions."""

    @pytest.mark.asyncio
    async def test_view_and_find_workflow(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Test the view -> find -> edit workflow."""
        from src.agents.paragraph_agent import (
            ParagraphAgentDeps,
            find_text_tool,
            propose_edit_tool,
            view_page_tool,
        )

        deps = ParagraphAgentDeps(
            job=sample_paragraph_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )

        ctx = MagicMock()
        ctx.deps = deps

        # Step 1: View page
        view_result = await view_page_tool(ctx)
        assert view_result.success is True
        assert "---" in view_result.markdown_content

        # Step 2: Find the artifact
        find_result = await find_text_tool(ctx, "---")
        assert find_result.found is True
        assert find_result.exact_match == "---"

        # Step 3: Propose edit to remove it
        edit_result = await propose_edit_tool(
            ctx,
            before="---",
            after="",
            reasoning="Removed page break marker",
            needs_review=False,
            task_type="page_artifact_removal",
        )
        assert edit_result.accepted is True
        assert edit_result.applied is True

        # Verify the edit was applied
        assert "---" not in deps.current_markdown
        assert len(deps.validated_edits) == 1
        assert deps.validated_edits[0].needs_review is False

    @pytest.mark.asyncio
    async def test_needs_review_flag_propagates(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Test that needs_review flag propagates to ledger entry."""
        from src.agents.paragraph_agent import ParagraphAgentDeps, propose_edit_tool

        deps = ParagraphAgentDeps(
            job=sample_paragraph_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
        )

        ctx = MagicMock()
        ctx.deps = deps

        # Apply edit with needs_review=True
        edit_result = await propose_edit_tool(
            ctx,
            before="---",
            after="",
            reasoning="Uncertain about this edit",
            needs_review=True,
            task_type="page_artifact_removal",
        )

        assert edit_result.accepted is True
        assert deps.validated_edits[0].needs_review is True
        assert deps.validated_edits[0].confidence == 0.6  # Lower confidence


# =============================================================================
# Full Document Context Tests
# =============================================================================


class TestFullDocumentContext:
    """Tests for full document context support (for citations)."""

    def test_deps_stores_full_document(
        self,
        sample_paragraph_job: Job,
        sample_image: Image.Image,
        sample_markdown: str,
    ) -> None:
        """Dependencies should store full document for citation linking."""
        full_doc = """Page 1 content

Page 2 content

## References

1. Author A (2024). Title of work.
2. Author B (2023). Another work.
"""
        deps = ParagraphAgentDeps(
            job=sample_paragraph_job,
            page_image=sample_image,
            current_markdown=sample_markdown,
            full_document_markdown=full_doc,
        )

        assert deps.full_document_markdown is not None
        assert "References" in deps.full_document_markdown
        assert "Author A" in deps.full_document_markdown
