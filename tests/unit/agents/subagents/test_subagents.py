"""Tests for subagent implementations.

Tests verify:
1. Output schema matches expected type
2. Confidence is within 0.0-1.0 range
3. Reasoning is non-empty
4. Subagents handle errors gracefully
5. Lazy loading pattern works correctly
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from src.agents.subagents import (
    PARAGRAPH_MERGE_SYSTEM_PROMPT,
    TEXT_STRUCTURE_SYSTEM_PROMPT,
    TYPOGRAPHY_SYSTEM_PROMPT,
    invoke_paragraph_merge_subagent,
    invoke_text_structure_subagent,
    invoke_typography_subagent,
)
from src.agents.subagents.types import (
    ParagraphMergeResult,
    TextStructureCorrection,
    TextStructureResult,
    TypographyResult,
)

pytestmark = pytest.mark.unit


class TestSystemPrompts:
    """Verify system prompts are defined and contain expected content."""

    def test_text_structure_prompt_exists(self):
        """Text structure prompt is defined with key content."""
        assert TEXT_STRUCTURE_SYSTEM_PROMPT
        assert "page artifact" in TEXT_STRUCTURE_SYSTEM_PROMPT.lower()
        assert "footnote" in TEXT_STRUCTURE_SYSTEM_PROMPT.lower()
        assert "citation" in TEXT_STRUCTURE_SYSTEM_PROMPT.lower()
        assert "list" in TEXT_STRUCTURE_SYSTEM_PROMPT.lower()
        assert "confidence" in TEXT_STRUCTURE_SYSTEM_PROMPT.lower()

    def test_typography_prompt_exists(self):
        """Typography prompt is defined with key content."""
        assert TYPOGRAPHY_SYSTEM_PROMPT
        assert "typography" in TYPOGRAPHY_SYSTEM_PROMPT.lower()
        assert "bold" in TYPOGRAPHY_SYSTEM_PROMPT.lower()
        assert "italic" in TYPOGRAPHY_SYSTEM_PROMPT.lower()
        assert "confidence" in TYPOGRAPHY_SYSTEM_PROMPT.lower()

    def test_paragraph_merge_prompt_exists(self):
        """Paragraph merge prompt is defined with key content."""
        assert PARAGRAPH_MERGE_SYSTEM_PROMPT
        assert "paragraph" in PARAGRAPH_MERGE_SYSTEM_PROMPT.lower()
        assert "merge" in PARAGRAPH_MERGE_SYSTEM_PROMPT.lower()
        assert "confidence" in PARAGRAPH_MERGE_SYSTEM_PROMPT.lower()


class TestResultTypes:
    """Verify result types have correct schema."""

    def test_text_structure_result_schema(self):
        """TextStructureResult has expected fields."""
        correction = TextStructureCorrection(
            task_type="page_artifact",
            before="---",
            after="",
            confidence=0.9,
            reasoning="Removed page break marker",
        )
        result = TextStructureResult(
            confidence=0.9,
            reasoning="Test reasoning",
            corrections=[correction],
            artifacts_removed=1,
            footnotes_fixed=0,
            citations_linked=0,
            lists_fixed=0,
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert len(result.corrections) == 1
        assert result.artifacts_removed == 1

    def test_text_structure_correction_schema(self):
        """TextStructureCorrection has expected fields."""
        correction = TextStructureCorrection(
            task_type="footnote",
            before="¹",
            after="[^1]",
            confidence=0.85,
            reasoning="Converted superscript to markdown footnote",
        )
        assert correction.task_type == "footnote"
        assert correction.before == "¹"
        assert correction.after == "[^1]"
        assert 0.0 <= correction.confidence <= 1.0

    def test_typography_result_schema(self):
        """TypographyResult has expected fields."""
        result = TypographyResult(
            confidence=0.88,
            reasoning="Test reasoning",
            corrected_markdown="**bold** text",
            formatting_added=[{"text": "bold", "type": "bold", "purpose": "emphasis"}],
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert "**bold**" in result.corrected_markdown
        assert len(result.formatting_added) == 1

    def test_paragraph_merge_result_schema(self):
        """ParagraphMergeResult has expected fields."""
        result = ParagraphMergeResult(
            confidence=0.92,
            reasoning="Test reasoning",
            should_merge=True,
            merged_text="merged paragraph text",
            join_method="hyphen_removal",
            page1_remove_chars=7,
            page2_remove_chars=6,
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert result.should_merge is True
        assert result.join_method == "hyphen_removal"
        assert result.page1_remove_chars == 7
        assert result.page2_remove_chars == 6


class TestConfidenceBounds:
    """Verify confidence field validation."""

    def test_confidence_min_bound(self):
        """Confidence cannot be below 0.0."""
        with pytest.raises(ValueError):
            TextStructureResult(
                confidence=-0.1,
                reasoning="Test",
                corrections=[],
            )

    def test_confidence_max_bound(self):
        """Confidence cannot be above 1.0."""
        with pytest.raises(ValueError):
            TextStructureResult(
                confidence=1.1,
                reasoning="Test",
                corrections=[],
            )

    def test_confidence_at_bounds(self):
        """Confidence at exact bounds is valid."""
        result_min = TextStructureResult(
            confidence=0.0,
            reasoning="Test",
            corrections=[],
        )
        result_max = TextStructureResult(
            confidence=1.0,
            reasoning="Test",
            corrections=[],
        )
        assert result_min.confidence == 0.0
        assert result_max.confidence == 1.0


@pytest.fixture
def mock_image() -> Image.Image:
    """Create a mock PIL Image for testing."""
    return Image.new("RGB", (100, 100), color="white")


@pytest.fixture
def mock_agent_result():
    """Create a mock agent run result."""

    def _create_result(output):
        mock_result = MagicMock()
        mock_result.output = output
        return mock_result

    return _create_result


class TestTextStructureSubagent:
    """Tests for consolidated text structure subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_text_structure_subagent returns TextStructureResult."""
        expected_output = TextStructureResult(
            confidence=0.9,
            reasoning="Fixed page artifact and footnote",
            corrections=[
                TextStructureCorrection(
                    task_type="page_artifact",
                    before="---",
                    after="",
                    confidence=0.95,
                    reasoning="Removed page break marker",
                ),
                TextStructureCorrection(
                    task_type="footnote",
                    before="¹",
                    after="[^1]",
                    confidence=0.85,
                    reasoning="Converted superscript",
                ),
            ],
            artifacts_removed=1,
            footnotes_fixed=1,
            citations_linked=0,
            lists_fixed=0,
        )

        with patch(
            "src.agents.subagents.text_structure._get_text_structure_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_text_structure_subagent("test---text¹", mock_image)

            assert isinstance(result, TextStructureResult)
            assert result.confidence == 0.9
            assert len(result.corrections) == 2

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch(
            "src.agents.subagents.text_structure._get_text_structure_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_text_structure_subagent("test text", mock_image)

            assert isinstance(result, TextStructureResult)
            assert result.confidence == 0.0
            assert "error" in result.reasoning.lower()
            assert result.corrections == []

    @pytest.mark.asyncio
    async def test_auto_calculates_summary_counts(self, mock_image, mock_agent_result):
        """Summary counts are auto-calculated from corrections."""
        # Return corrections but leave summary counts at 0
        expected_output = TextStructureResult(
            confidence=0.9,
            reasoning="Found issues",
            corrections=[
                TextStructureCorrection(
                    task_type="page_artifact",
                    before="---",
                    after="",
                    confidence=0.95,
                    reasoning="Removed",
                ),
                TextStructureCorrection(
                    task_type="footnote",
                    before="¹",
                    after="[^1]",
                    confidence=0.85,
                    reasoning="Fixed",
                ),
                TextStructureCorrection(
                    task_type="footnote",
                    before="²",
                    after="[^2]",
                    confidence=0.85,
                    reasoning="Fixed",
                ),
                TextStructureCorrection(
                    task_type="list",
                    before="1.",
                    after="1.",
                    confidence=0.8,
                    reasoning="Fixed indent",
                ),
            ],
            artifacts_removed=0,  # Will be auto-calculated
            footnotes_fixed=0,
            citations_linked=0,
            lists_fixed=0,
        )

        with patch(
            "src.agents.subagents.text_structure._get_text_structure_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_text_structure_subagent("test", mock_image)

            # Verify counts are auto-calculated
            assert result.artifacts_removed == 1
            assert result.footnotes_fixed == 2
            assert result.citations_linked == 0
            assert result.lists_fixed == 1


class TestTypographySubagent:
    """Tests for typography semantics subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_typography_subagent returns TypographyResult."""
        expected_output = TypographyResult(
            confidence=0.88,
            reasoning="Added bold for emphasis",
            corrected_markdown="This is **important**",
            formatting_added=[{"text": "important", "type": "bold", "purpose": "emphasis"}],
        )

        with patch("src.agents.subagents.typography._get_typography_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_typography_subagent("This is important", mock_image)

            assert isinstance(result, TypographyResult)
            assert result.confidence == 0.88
            assert "**important**" in result.corrected_markdown

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch("src.agents.subagents.typography._get_typography_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_typography_subagent("test text", mock_image)

            assert isinstance(result, TypographyResult)
            assert result.confidence == 0.0
            assert result.corrected_markdown == "test text"


class TestParagraphMergeSubagent:
    """Tests for paragraph merge subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_paragraph_merge_subagent returns ParagraphMergeResult."""
        expected_output = ParagraphMergeResult(
            confidence=0.92,
            reasoning="Word 'information' split across pages",
            should_merge=True,
            merged_text="information is key",
            join_method="hyphen_removal",
            page1_remove_chars=6,
            page2_remove_chars=7,
        )

        with patch(
            "src.agents.subagents.paragraph_merge._get_paragraph_merge_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_paragraph_merge_subagent(
                "infor-",
                "mation is key",
                mock_image,
                mock_image,
            )

            assert isinstance(result, ParagraphMergeResult)
            assert result.should_merge is True
            assert result.join_method == "hyphen_removal"

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch(
            "src.agents.subagents.paragraph_merge._get_paragraph_merge_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_paragraph_merge_subagent(
                "page 1 end",
                "page 2 start",
                mock_image,
                mock_image,
            )

            assert isinstance(result, ParagraphMergeResult)
            assert result.confidence == 0.0
            assert result.should_merge is False  # Safe default


class TestLazyLoading:
    """Verify lazy loading pattern for subagents."""

    def test_text_structure_lazy_loading(self):
        """Text structure subagent is created only on first call."""
        import src.agents.subagents.text_structure as ts_module

        ts_module._text_structure_subagent = None

        with patch.object(ts_module, "BedrockConverseModel"):
            with patch.object(ts_module, "Agent") as mock_agent_class:
                # First call creates agent
                ts_module._get_text_structure_subagent()
                assert mock_agent_class.called

                # Second call reuses
                mock_agent_class.reset_mock()
                ts_module._get_text_structure_subagent()
                assert not mock_agent_class.called

    def test_typography_lazy_loading(self):
        """Typography subagent is created only on first call."""
        import src.agents.subagents.typography as ty_module

        ty_module._typography_subagent = None

        with patch.object(ty_module, "BedrockConverseModel"):
            with patch.object(ty_module, "Agent") as mock_agent_class:
                ty_module._get_typography_subagent()
                assert mock_agent_class.called

                mock_agent_class.reset_mock()
                ty_module._get_typography_subagent()
                assert not mock_agent_class.called

    def test_paragraph_merge_lazy_loading(self):
        """Paragraph merge subagent is created only on first call."""
        import src.agents.subagents.paragraph_merge as pm_module

        pm_module._paragraph_merge_subagent = None

        with patch.object(pm_module, "BedrockConverseModel"):
            with patch.object(pm_module, "Agent") as mock_agent_class:
                pm_module._get_paragraph_merge_subagent()
                assert mock_agent_class.called

                mock_agent_class.reset_mock()
                pm_module._get_paragraph_merge_subagent()
                assert not mock_agent_class.called
