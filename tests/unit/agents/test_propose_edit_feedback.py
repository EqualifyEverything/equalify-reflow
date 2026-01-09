"""Unit tests for ProposeEdit feedback enhancement.

Tests the improved feedback when 'before' text is not found in propose_edit_tool.
This validates the helpful error messages that guide the agent to retry.
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
    ParagraphAgentDeps,
    propose_edit_tool,
)

pytestmark = pytest.mark.unit


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_image() -> Image.Image:
    """Create a sample PIL image for testing."""
    return Image.new("RGB", (100, 100), color="white")


@pytest.fixture
def sample_markdown() -> str:
    """Create sample markdown content for testing propose_edit."""
    return """# Document Title

This is a paragraph with some text.

Another paragraph after a page break marker.

1. First item in a list
2. Second item
3. Third item

Some text with a footnote reference.

The document continues with more content here.
Additional lines follow this one.
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
def sample_job(sample_job_context: JobContext) -> Job:
    """Create a sample paragraph job."""
    return Job(
        job_id="test-job-123",
        page=1,
        job_type=JobType.PARAGRAPH,
        context=sample_job_context,
        page_markdown="",  # Set in deps
        tasks=[
            Task(
                task_type=TaskType.FORMAT_FIX,
                target="test",
                context="Test task",
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
# ProposeEdit Feedback Enhancement Tests
# =============================================================================


class TestProposeEditFeedback:
    """Tests for improved feedback when 'before' text not found."""

    @pytest.mark.asyncio
    async def test_edit_not_found_includes_similar_text(
        self, mock_run_context: MagicMock
    ) -> None:
        """When before text not exact, feedback includes similar lines."""
        # Try to find text that's similar but not exact
        result = await propose_edit_tool(
            mock_run_context,
            before="This is a paragraph with text",  # Missing "some"
            after="Replacement text",
            reasoning="Test edit",
        )

        # Edit should not be applied
        assert result.applied is False

        # Feedback should exist and include helpful information
        assert result.feedback is not None
        feedback = result.feedback.lower()

        # Should mention the text was not found
        assert "not found" in feedback or "not applied" in feedback

        # Should show similar text or context
        assert "similar" in feedback or "lines" in feedback or "markdown" in feedback

    @pytest.mark.asyncio
    async def test_edit_not_found_shows_markdown_preview(
        self, mock_run_context: MagicMock
    ) -> None:
        """When no similar text found, feedback shows markdown start."""
        # Try completely unrelated text
        result = await propose_edit_tool(
            mock_run_context,
            before="xyzzy completely unrelated nonexistent text 12345",
            after="replacement",
            reasoning="Test edit",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should show some markdown content to help the agent
        # Either shows similar lines or first 500 chars of markdown
        feedback = result.feedback
        assert "markdown" in feedback.lower() or "# Document" in feedback

    @pytest.mark.asyncio
    async def test_edit_found_applies_successfully(
        self, mock_run_context: MagicMock
    ) -> None:
        """When before text found, applied=True."""
        result = await propose_edit_tool(
            mock_run_context,
            before="This is a paragraph with some text.",
            after="This is a modified paragraph.",
            reasoning="Improving clarity",
        )

        assert result.accepted is True
        assert result.applied is True

        # Verify the change was made
        assert "This is a modified paragraph." in mock_run_context.deps.current_markdown
        assert "This is a paragraph with some text." not in mock_run_context.deps.current_markdown

    @pytest.mark.asyncio
    async def test_feedback_truncates_long_similar_text(
        self, mock_run_context: MagicMock
    ) -> None:
        """Similar text preview is limited to reasonable length."""
        # Create a longer markdown with repeated similar content
        long_markdown = "# Document Title\n\n" + "This is repeated content. " * 100
        mock_run_context.deps.current_markdown = long_markdown

        result = await propose_edit_tool(
            mock_run_context,
            before="This is repeated content that does not exist exactly",
            after="replacement",
            reasoning="Test",
        )

        assert result.applied is False
        assert result.feedback is not None

        # The feedback should be reasonably sized (not show 100 copies)
        # Check it's not excessively long
        assert len(result.feedback) < 1000

    @pytest.mark.asyncio
    async def test_feedback_suggests_using_find_text(
        self, mock_run_context: MagicMock
    ) -> None:
        """Feedback suggests using find_text() to locate exact text."""
        result = await propose_edit_tool(
            mock_run_context,
            before="nonexistent text pattern xyz",
            after="replacement",
            reasoning="Test",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should suggest the agent use find_text
        assert "find_text" in result.feedback

    @pytest.mark.asyncio
    async def test_partial_match_not_exact_shows_context(
        self, mock_run_context: MagicMock
    ) -> None:
        """Partial match that isn't exact shows context in feedback."""
        # Note: "# Document" actually exists as substring of "# Document Title"
        # so we use a truly non-existent partial match
        result = await propose_edit_tool(
            mock_run_context,
            before="# Dokument",  # Typo - doesn't exist
            after="# New Title",
            reasoning="Change title",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should show the actual line or similar content
        feedback = result.feedback
        assert "Document" in feedback or "markdown" in feedback.lower()

    @pytest.mark.asyncio
    async def test_whitespace_mismatch_provides_helpful_feedback(
        self, mock_run_context: MagicMock
    ) -> None:
        """Whitespace differences provide helpful feedback."""
        # Try text with extra whitespace
        result = await propose_edit_tool(
            mock_run_context,
            before="1.  First item in a list",  # Extra space after period
            after="1. Updated first item",
            reasoning="Update list",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should help identify the issue
        assert "not found" in result.feedback.lower() or "not applied" in result.feedback.lower()

    @pytest.mark.asyncio
    async def test_feedback_shows_line_numbers(
        self, mock_run_context: MagicMock
    ) -> None:
        """Feedback includes line numbers when showing similar text."""
        result = await propose_edit_tool(
            mock_run_context,
            before="First item",  # Partial match
            after="Updated item",
            reasoning="Test",
        )

        if not result.applied and result.feedback:
            # If similar text is found, line numbers should be shown
            feedback = result.feedback
            # The feedback format includes "Lines X-Y:" when similar text found
            if "Similar" in feedback or "Lines" in feedback:
                assert "Line" in feedback

    @pytest.mark.asyncio
    async def test_validation_rejects_when_text_not_found(
        self, mock_run_context: MagicMock
    ) -> None:
        """Validation fails (accepted=False) when target text doesn't exist."""
        # The validation gate checks if text exists BEFORE accepting
        result = await propose_edit_tool(
            mock_run_context,
            before="text that does not exist anywhere",
            after="replacement text",
            reasoning="Valid reasoning for the edit",
        )

        # The validation rejects when target text isn't found
        # This is to prevent agents from submitting edits for non-existent text
        assert result.accepted is False
        assert result.applied is False
        assert result.feedback is not None
        assert "not found" in result.feedback.lower() or "target text" in result.feedback.lower()

    @pytest.mark.asyncio
    async def test_multiline_before_text_not_found(
        self, mock_run_context: MagicMock
    ) -> None:
        """Multiline before text that doesn't match provides feedback."""
        result = await propose_edit_tool(
            mock_run_context,
            before="""1. First item
2. Second item
3. Third item wrong""",  # "wrong" doesn't match
            after="Updated list",
            reasoning="Fix list",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should mention text not found
        assert "not found" in result.feedback.lower() or "not applied" in result.feedback.lower()

    @pytest.mark.asyncio
    async def test_empty_before_text_handled(
        self, mock_run_context: MagicMock
    ) -> None:
        """Empty before text is handled gracefully."""
        result = await propose_edit_tool(
            mock_run_context,
            before="",
            after="New content",
            reasoning="Add content",
        )

        # Empty string technically exists everywhere, but validation may reject
        # or it may be handled specially
        assert result.feedback is not None or result.applied is True

    @pytest.mark.asyncio
    async def test_case_sensitive_mismatch(
        self, mock_run_context: MagicMock
    ) -> None:
        """Case sensitivity issues are handled with helpful feedback."""
        result = await propose_edit_tool(
            mock_run_context,
            before="document title",  # Wrong case
            after="New Title",
            reasoning="Fix title",
        )

        assert result.applied is False
        assert result.feedback is not None

        # Should show actual content to help agent see the case difference
        feedback = result.feedback
        assert "Document" in feedback or "markdown" in feedback.lower()
