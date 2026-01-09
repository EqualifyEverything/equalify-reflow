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
    CITATION_SYSTEM_PROMPT,
    FOOTNOTE_SYSTEM_PROMPT,
    LIST_SEMANTICS_SYSTEM_PROMPT,
    PAGE_ARTIFACT_SYSTEM_PROMPT,
    PARAGRAPH_MERGE_SYSTEM_PROMPT,
    TYPOGRAPHY_SYSTEM_PROMPT,
    invoke_citation_subagent,
    invoke_footnote_subagent,
    invoke_list_subagent,
    invoke_page_artifact_subagent,
    invoke_paragraph_merge_subagent,
    invoke_typography_subagent,
)
from src.agents.subagents.types import (
    CitationResult,
    FootnoteResult,
    ListResult,
    PageArtifactResult,
    ParagraphMergeResult,
    TypographyResult,
)

pytestmark = pytest.mark.unit


class TestSystemPrompts:
    """Verify system prompts are defined and contain expected content."""

    def test_page_artifact_prompt_exists(self):
        """Page artifact prompt is defined with key content."""
        assert PAGE_ARTIFACT_SYSTEM_PROMPT
        assert "page artifact" in PAGE_ARTIFACT_SYSTEM_PROMPT.lower()
        assert "---" in PAGE_ARTIFACT_SYSTEM_PROMPT
        assert "confidence" in PAGE_ARTIFACT_SYSTEM_PROMPT.lower()

    def test_footnote_prompt_exists(self):
        """Footnote prompt is defined with key content."""
        assert FOOTNOTE_SYSTEM_PROMPT
        assert "footnote" in FOOTNOTE_SYSTEM_PROMPT.lower()
        assert "[^1]" in FOOTNOTE_SYSTEM_PROMPT
        assert "confidence" in FOOTNOTE_SYSTEM_PROMPT.lower()

    def test_citation_prompt_exists(self):
        """Citation prompt is defined with key content."""
        assert CITATION_SYSTEM_PROMPT
        assert "citation" in CITATION_SYSTEM_PROMPT.lower()
        assert "bibliography" in CITATION_SYSTEM_PROMPT.lower()
        assert "confidence" in CITATION_SYSTEM_PROMPT.lower()

    def test_list_prompt_exists(self):
        """List prompt is defined with key content."""
        assert LIST_SEMANTICS_SYSTEM_PROMPT
        assert "list" in LIST_SEMANTICS_SYSTEM_PROMPT.lower()
        assert "nesting" in LIST_SEMANTICS_SYSTEM_PROMPT.lower()
        assert "confidence" in LIST_SEMANTICS_SYSTEM_PROMPT.lower()

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

    def test_page_artifact_result_schema(self):
        """PageArtifactResult has expected fields."""
        result = PageArtifactResult(
            confidence=0.9,
            reasoning="Test reasoning",
            cleaned_text="cleaned text",
            artifacts_removed=["---"],
            words_rejoined=["information"],
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert result.cleaned_text == "cleaned text"
        assert "---" in result.artifacts_removed
        assert "information" in result.words_rejoined

    def test_footnote_result_schema(self):
        """FootnoteResult has expected fields."""
        result = FootnoteResult(
            confidence=0.85,
            reasoning="Test reasoning",
            corrected_markdown="# Test",
            footnotes_fixed=[{"marker": "[^1]", "action": "linked", "definition": "test"}],
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert result.corrected_markdown
        assert len(result.footnotes_fixed) == 1

    def test_citation_result_schema(self):
        """CitationResult has expected fields."""
        result = CitationResult(
            confidence=0.7,
            reasoning="Test reasoning",
            corrected_markdown="# Test",
            citations_linked=[{"marker": "[1]", "linked_to": "Smith 2023", "status": "linked"}],
            bibliography_found=True,
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert result.bibliography_found is True
        assert len(result.citations_linked) == 1

    def test_list_result_schema(self):
        """ListResult has expected fields."""
        result = ListResult(
            confidence=0.95,
            reasoning="Test reasoning",
            corrected_markdown="- item 1\n- item 2",
            issues_fixed=["Fixed numbering"],
        )
        assert 0.0 <= result.confidence <= 1.0
        assert result.reasoning
        assert result.corrected_markdown
        assert "Fixed numbering" in result.issues_fixed

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
            PageArtifactResult(
                confidence=-0.1,
                reasoning="Test",
                cleaned_text="",
            )

    def test_confidence_max_bound(self):
        """Confidence cannot be above 1.0."""
        with pytest.raises(ValueError):
            PageArtifactResult(
                confidence=1.1,
                reasoning="Test",
                cleaned_text="",
            )

    def test_confidence_at_bounds(self):
        """Confidence at exact bounds is valid."""
        result_min = PageArtifactResult(
            confidence=0.0,
            reasoning="Test",
            cleaned_text="",
        )
        result_max = PageArtifactResult(
            confidence=1.0,
            reasoning="Test",
            cleaned_text="",
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


class TestPageArtifactSubagent:
    """Tests for page artifact removal subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_page_artifact_subagent returns PageArtifactResult."""
        expected_output = PageArtifactResult(
            confidence=0.9,
            reasoning="Removed page break marker",
            cleaned_text="cleaned text",
            artifacts_removed=["---"],
            words_rejoined=[],
        )

        with patch(
            "src.agents.subagents.page_artifacts._get_page_artifact_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_page_artifact_subagent("test---text", mock_image)

            assert isinstance(result, PageArtifactResult)
            assert result.confidence == 0.9
            assert result.cleaned_text == "cleaned text"

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch(
            "src.agents.subagents.page_artifacts._get_page_artifact_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_page_artifact_subagent("test text", mock_image)

            assert isinstance(result, PageArtifactResult)
            assert result.confidence == 0.0
            assert "error" in result.reasoning.lower()
            assert result.cleaned_text == "test text"  # Returns original on error


class TestFootnoteSubagent:
    """Tests for footnote correction subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_footnote_subagent returns FootnoteResult."""
        expected_output = FootnoteResult(
            confidence=0.85,
            reasoning="Fixed footnote linking",
            corrected_markdown="text[^1]\n\n[^1]: definition",
            footnotes_fixed=[{"marker": "[^1]", "action": "linked", "definition": "definition"}],
        )

        with patch("src.agents.subagents.footnotes._get_footnote_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_footnote_subagent("text[^1]", mock_image)

            assert isinstance(result, FootnoteResult)
            assert result.confidence == 0.85
            assert len(result.footnotes_fixed) == 1

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch("src.agents.subagents.footnotes._get_footnote_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_footnote_subagent("test markdown", mock_image)

            assert isinstance(result, FootnoteResult)
            assert result.confidence == 0.0
            assert "error" in result.reasoning.lower()
            assert result.corrected_markdown == "test markdown"


class TestCitationSubagent:
    """Tests for citation linking subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_citation_subagent returns CitationResult."""
        expected_output = CitationResult(
            confidence=0.8,
            reasoning="Linked citations to bibliography",
            corrected_markdown="text [1]",
            citations_linked=[{"marker": "[1]", "linked_to": "Smith 2023", "status": "linked"}],
            bibliography_found=True,
        )

        with patch("src.agents.subagents.citations._get_citation_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_citation_subagent("text [1]", mock_image)

            assert isinstance(result, CitationResult)
            assert result.bibliography_found is True
            assert len(result.citations_linked) == 1

    @pytest.mark.asyncio
    async def test_accepts_full_document(self, mock_image, mock_agent_result):
        """Subagent accepts optional full_document parameter."""
        expected_output = CitationResult(
            confidence=0.9,
            reasoning="Found bibliography in full document",
            corrected_markdown="text [1]",
            citations_linked=[],
            bibliography_found=True,
        )

        with patch("src.agents.subagents.citations._get_citation_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_citation_subagent(
                "text [1]",
                mock_image,
                full_document="# References\n[1] Smith 2023",
            )

            assert isinstance(result, CitationResult)
            assert result.bibliography_found is True

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch("src.agents.subagents.citations._get_citation_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_citation_subagent("test markdown", mock_image)

            assert isinstance(result, CitationResult)
            assert result.confidence == 0.0
            assert result.bibliography_found is False


class TestListSubagent:
    """Tests for list semantics subagent."""

    @pytest.mark.asyncio
    async def test_invoke_returns_correct_type(self, mock_image, mock_agent_result):
        """invoke_list_subagent returns ListResult."""
        expected_output = ListResult(
            confidence=0.95,
            reasoning="Fixed list indentation",
            corrected_markdown="- item 1\n  - nested",
            issues_fixed=["Fixed nesting from 3 to 2 spaces"],
        )

        with patch("src.agents.subagents.lists._get_list_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            result = await invoke_list_subagent("- item 1\n   - nested", mock_image)

            assert isinstance(result, ListResult)
            assert result.confidence == 0.95
            assert len(result.issues_fixed) == 1

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, mock_image):
        """Subagent returns default result on error."""
        with patch("src.agents.subagents.lists._get_list_subagent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("API Error"))
            mock_get.return_value = mock_agent

            result = await invoke_list_subagent("- item", mock_image)

            assert isinstance(result, ListResult)
            assert result.confidence == 0.0
            assert result.corrected_markdown == "- item"


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

    def test_page_artifact_lazy_loading(self):
        """Page artifact subagent is created only on first call."""
        # Reset global
        import src.agents.subagents.page_artifacts as pa_module

        pa_module._page_artifact_subagent = None

        with patch.object(pa_module, "BedrockConverseModel"):
            with patch.object(pa_module, "Agent") as mock_agent_class:
                # First call creates agent
                pa_module._get_page_artifact_subagent()
                assert mock_agent_class.called

                # Second call reuses
                mock_agent_class.reset_mock()
                pa_module._get_page_artifact_subagent()
                assert not mock_agent_class.called

    def test_footnote_lazy_loading(self):
        """Footnote subagent is created only on first call."""
        import src.agents.subagents.footnotes as fn_module

        fn_module._footnote_subagent = None

        with patch.object(fn_module, "BedrockConverseModel"):
            with patch.object(fn_module, "Agent") as mock_agent_class:
                fn_module._get_footnote_subagent()
                assert mock_agent_class.called

                mock_agent_class.reset_mock()
                fn_module._get_footnote_subagent()
                assert not mock_agent_class.called

    def test_citation_lazy_loading(self):
        """Citation subagent is created only on first call."""
        import src.agents.subagents.citations as ct_module

        ct_module._citation_subagent = None

        with patch.object(ct_module, "BedrockConverseModel"):
            with patch.object(ct_module, "Agent") as mock_agent_class:
                ct_module._get_citation_subagent()
                assert mock_agent_class.called

                mock_agent_class.reset_mock()
                ct_module._get_citation_subagent()
                assert not mock_agent_class.called

    def test_list_lazy_loading(self):
        """List subagent is created only on first call."""
        import src.agents.subagents.lists as ls_module

        ls_module._list_subagent = None

        with patch.object(ls_module, "BedrockConverseModel"):
            with patch.object(ls_module, "Agent") as mock_agent_class:
                ls_module._get_list_subagent()
                assert mock_agent_class.called

                mock_agent_class.reset_mock()
                ls_module._get_list_subagent()
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
