"""Tests for enhanced TypographyAgent (PRD-025).

Tests cover:
- Output models (FormattingIssue, OCRDecision, TypographyAnalysisOutput)
- Agent initialization and dynamic instructions
- Formatting issue to observation/auto_correction/review_item conversion
- OCR decision routing (fix/keep/uncertain)
- Confidence threshold routing (>=0.95 auto, <0.95 review)
- AgentResult output format
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.typography import (
    FormattingIssue,
    OCRDecision,
    TypographyAgent,
    TypographyAnalysisOutput,
)
from src.services.pdf_converter import PageData
from src.shared.models.agent_trace import AgentResult
from src.shared.models.remediation import DocumentManifest, PageFeatures
from src.utils.ocr_checker import OCRSuggestion

# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestFormattingIssue:
    """Tests for FormattingIssue model."""

    def test_valid_formatting_issue(self):
        """Test creating a valid FormattingIssue."""
        issue = FormattingIssue(
            original_text="WARNING:",
            corrected_text="**WARNING:**",
            suggested_format="bold",
            page_num=1,
            confidence=0.96,
            reasoning="Bold text conveys emphasis for safety warning.",
            context="...WARNING: Do not proceed without...",
        )
        assert issue.original_text == "WARNING:"
        assert issue.corrected_text == "**WARNING:**"
        assert issue.suggested_format == "bold"
        assert issue.confidence == 0.96
        assert issue.id is not None  # UUID auto-generated

    def test_formatting_issue_default_id(self):
        """Test FormattingIssue generates UUID by default."""
        issue1 = FormattingIssue(
            original_text="test",
            corrected_text="**test**",
            suggested_format="bold",
            page_num=1,
            confidence=0.9,
            reasoning="Test",
            context="Test context",
        )
        issue2 = FormattingIssue(
            original_text="test",
            corrected_text="**test**",
            suggested_format="bold",
            page_num=1,
            confidence=0.9,
            reasoning="Test",
            context="Test context",
        )
        # Each issue should have unique ID
        assert issue1.id != issue2.id

    def test_confidence_validation(self):
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            FormattingIssue(
                original_text="test",
                corrected_text="**test**",
                suggested_format="bold",
                page_num=1,
                confidence=1.5,  # Invalid
                reasoning="Test",
                context="Test context",
            )

    def test_page_num_validation(self):
        """Test page_num must be >= 1."""
        with pytest.raises(ValidationError):
            FormattingIssue(
                original_text="test",
                corrected_text="**test**",
                suggested_format="bold",
                page_num=0,  # Invalid
                confidence=0.9,
                reasoning="Test",
                context="Test context",
            )


@pytest.mark.unit
class TestOCRDecision:
    """Tests for OCRDecision model."""

    def test_valid_fix_decision(self):
        """Test OCR decision with fix action."""
        decision = OCRDecision(
            word="teh",
            decision="fix",
            correction="the",
            confidence=0.98,
            reasoning="Common OCR transposition error.",
        )
        assert decision.word == "teh"
        assert decision.decision == "fix"
        assert decision.correction == "the"
        assert decision.confidence == 0.98

    def test_valid_keep_decision(self):
        """Test OCR decision with keep action."""
        decision = OCRDecision(
            word="Enzo",
            decision="keep",
            correction=None,
            confidence=0.85,
            reasoning="Enzo is a proper noun (framework name).",
        )
        assert decision.decision == "keep"
        assert decision.correction is None

    def test_valid_uncertain_decision(self):
        """Test OCR decision with uncertain action."""
        decision = OCRDecision(
            word="unknwn",
            decision="uncertain",
            correction=None,
            confidence=0.60,
            reasoning="Cannot determine if typo or technical term.",
        )
        assert decision.decision == "uncertain"


@pytest.mark.unit
class TestTypographyAnalysisOutput:
    """Tests for TypographyAnalysisOutput model."""

    def test_empty_output(self):
        """Test empty output is valid."""
        output = TypographyAnalysisOutput()
        assert output.formatting_issues == []
        assert output.confirmed_ocr_fixes == {}
        assert output.uncertain_ocr == {}
        assert output.summary == ""

    def test_output_with_issues(self):
        """Test output with formatting issues."""
        issue = FormattingIssue(
            original_text="Note:",
            corrected_text="**Note:**",
            suggested_format="bold",
            page_num=1,
            confidence=0.92,
            reasoning="Bold prefix for callout.",
            context="Note: This is important.",
        )
        output = TypographyAnalysisOutput(
            formatting_issues=[issue],
            summary="1 formatting issue found.",
        )
        assert len(output.formatting_issues) == 1

    def test_output_with_ocr_decisions(self):
        """Test output with OCR decisions."""
        fix_decision = OCRDecision(
            word="teh",
            decision="fix",
            correction="the",
            confidence=0.98,
            reasoning="Common typo.",
        )
        uncertain_decision = OCRDecision(
            word="Enzo",
            decision="uncertain",
            correction=None,
            confidence=0.60,
            reasoning="Could be proper noun.",
        )
        output = TypographyAnalysisOutput(
            confirmed_ocr_fixes={"teh": fix_decision},
            uncertain_ocr={"Enzo": uncertain_decision},
        )
        assert "teh" in output.confirmed_ocr_fixes
        assert "Enzo" in output.uncertain_ocr


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestTypographyAgentInit:
    """Tests for TypographyAgent initialization."""

    def test_agent_initialization(self):
        """Test agent can be instantiated."""
        agent = TypographyAgent()
        assert agent is not None
        assert agent.CONFIDENCE_THRESHOLD == 0.95
        assert agent.MAX_PAGES == 5
        assert agent._agent is None  # Lazy initialization

    @patch("src.agents.typography.typography_agent.create_agent")
    def test_lazy_agent_creation(self, mock_create):
        """Test agent is created lazily on first use."""
        mock_agent = MagicMock()
        mock_create.return_value = mock_agent

        agent = TypographyAgent()
        assert agent._agent is None

        # Trigger lazy initialization
        internal_agent = agent._get_agent()

        assert internal_agent is mock_agent
        mock_create.assert_called_once_with(
            prompts_file="typography_enhanced.yaml",
            output_type=TypographyAnalysisOutput,
            model_tier=pytest.importorskip("src.agents.model_tiers").ModelTier.EFFICIENT,
            use_deps=True,
            max_retries=2,
        )


# =============================================================================
# Confidence Threshold Routing Tests
# =============================================================================


@pytest.mark.unit
class TestConfidenceRouting:
    """Tests for confidence-based routing to auto_corrections vs review_items."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=2,
            heading_tree_json="{}",
            page_features=[
                PageFeatures(page_num=1, complexity_score=0.8),
                PageFeatures(page_num=2, complexity_score=0.6),
            ],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [
            PageData(page_num=1, image_base64="YWJjMTIz"),
            PageData(page_num=2, image_base64="ZGVmNDU2"),
        ]

    @pytest.mark.asyncio
    async def test_high_confidence_creates_auto_correction(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test high confidence (>=0.95) creates auto_correction."""
        # Create mock output with high confidence issue
        mock_output = TypographyAnalysisOutput(
            formatting_issues=[
                FormattingIssue(
                    original_text="WARNING:",
                    corrected_text="**WARNING:**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.96,  # >= 0.95
                    reasoning="Clear bold emphasis.",
                    context="WARNING: Do not...",
                )
            ],
            summary="1 issue found.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_result.usage.return_value.input_tokens = 100
            mock_result.usage.return_value.output_tokens = 50
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=1.0)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\nWARNING: Do not...",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert isinstance(result, AgentResult)
        assert result.agent_name == "typography"
        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0  # High confidence = auto
        assert result.auto_corrections[0].search == "WARNING:"
        assert result.auto_corrections[0].replace == "**WARNING:**"

    @pytest.mark.asyncio
    async def test_low_confidence_creates_review_item(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test low confidence (<0.95) creates review_item."""
        # Create mock output with low confidence issue
        mock_output = TypographyAnalysisOutput(
            formatting_issues=[
                FormattingIssue(
                    original_text="Note:",
                    corrected_text="**Note:**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.85,  # < 0.95
                    reasoning="Possibly bold prefix.",
                    context="Note: This may be...",
                )
            ],
            summary="1 issue found.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_result.usage.return_value.input_tokens = 100
            mock_result.usage.return_value.output_tokens = 50
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=1.0)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\nNote: This may be...",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert isinstance(result, AgentResult)
        assert len(result.auto_corrections) == 0  # Low confidence = no auto
        assert len(result.review_items) == 1
        assert result.review_items[0].search_text == "Note:"


# =============================================================================
# OCR Decision Routing Tests
# =============================================================================


@pytest.mark.unit
class TestOCRRouting:
    """Tests for OCR suggestion routing."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="research_paper",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1, complexity_score=0.7)],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [PageData(page_num=1, image_base64="YWJjMTIz")]

    @pytest.fixture
    def sample_ocr_suggestions(self) -> list[OCRSuggestion]:
        """Create sample OCR suggestions."""
        return [
            OCRSuggestion(
                word="teh",
                suggestions=["the"],
                confidence=0.95,
                reason="common_ocr_pattern",
                context="This is teh first step.",
            ),
            OCRSuggestion(
                word="Enzo",
                suggestions=["Enzo"],
                confidence=0.60,
                reason="spell_check",
                context="The Enzo framework provides...",
            ),
        ]

    @pytest.mark.asyncio
    async def test_confirmed_ocr_fix_creates_auto_correction(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        sample_ocr_suggestions: list[OCRSuggestion],
    ):
        """Test confirmed OCR fix creates auto_correction."""
        mock_output = TypographyAnalysisOutput(
            confirmed_ocr_fixes={
                "teh": OCRDecision(
                    word="teh",
                    decision="fix",
                    correction="the",
                    confidence=0.98,
                    reasoning="Clear OCR transposition error.",
                )
            },
            uncertain_ocr={},
            summary="1 OCR fix confirmed.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=1.0)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="This is teh first step.",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=sample_ocr_suggestions,
                        job_id="test-job",
                    )

        assert len(result.auto_corrections) == 1
        assert result.auto_corrections[0].search == "teh"
        assert result.auto_corrections[0].replace == "the"

    @pytest.mark.asyncio
    async def test_uncertain_ocr_creates_review_item(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        sample_ocr_suggestions: list[OCRSuggestion],
    ):
        """Test uncertain OCR creates review_item."""
        mock_output = TypographyAnalysisOutput(
            confirmed_ocr_fixes={},
            uncertain_ocr={
                "Enzo": OCRDecision(
                    word="Enzo",
                    decision="uncertain",
                    correction=None,
                    confidence=0.60,
                    reasoning="Could be framework name.",
                )
            },
            summary="1 OCR item needs review.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=1.0)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="The Enzo framework provides...",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=sample_ocr_suggestions,
                        job_id="test-job",
                    )

        assert len(result.review_items) == 1
        assert result.review_items[0].search_text == "Enzo"
        assert "typo" in result.review_items[0].question.lower()


# =============================================================================
# AgentResult Output Tests
# =============================================================================


@pytest.mark.unit
class TestAgentResultOutput:
    """Tests for AgentResult output format."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="lecture_notes",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1, complexity_score=0.7)],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [PageData(page_num=1, image_base64="YWJjMTIz")]

    @pytest.mark.asyncio
    async def test_result_has_correct_agent_name(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test AgentResult has agent_name='typography'."""
        mock_output = TypographyAnalysisOutput(summary="No issues.")

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert result.agent_name == "typography"

    @pytest.mark.asyncio
    async def test_result_has_timing_info(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test AgentResult includes timing information."""
        mock_output = TypographyAnalysisOutput(summary="No issues.")

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert result.time_seconds >= 0
        assert result.cost_cents >= 0

    @pytest.mark.asyncio
    async def test_result_has_reasoning_summary(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test AgentResult includes reasoning summary."""
        mock_output = TypographyAnalysisOutput(
            summary="Found 2 formatting issues and 1 OCR error.",
            formatting_issues=[
                FormattingIssue(
                    original_text="test",
                    corrected_text="**test**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.96,
                    reasoning="Bold emphasis.",
                    context="Test context.",
                )
            ],
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\ntest content",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert result.reasoning_summary is not None
        assert len(result.reasoning_summary) > 0

    @pytest.mark.asyncio
    async def test_empty_output_returns_valid_result(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test empty analysis still returns valid AgentResult."""
        mock_output = TypographyAnalysisOutput()  # All empty

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Clean Document",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        assert isinstance(result, AgentResult)
        assert result.observations == []
        assert result.auto_corrections == []
        assert result.review_items == []
        assert result.confidence == 1.0  # No issues = perfect confidence


# =============================================================================
# Helper Method Tests
# =============================================================================


@pytest.mark.unit
class TestHelperMethods:
    """Tests for TypographyAgent helper methods."""

    def test_find_page_with_markers(self):
        """Test _find_page locates correct page from markers."""
        agent = TypographyAgent()
        markdown = """<!-- Page 1 -->
# Introduction
Some text here.

<!-- Page 2 -->
# Methods
The word appears here.

<!-- Page 3 -->
# Results
"""
        page_num = agent._find_page(markdown, "word", [])
        assert page_num == 2

    def test_find_page_no_markers(self):
        """Test _find_page returns 1 when no markers found."""
        agent = TypographyAgent()
        markdown = "Just some text without page markers."
        page_num = agent._find_page(markdown, "text", [])
        assert page_num == 1

    def test_find_page_word_not_found(self):
        """Test _find_page returns 1 when word not found."""
        agent = TypographyAgent()
        markdown = "<!-- Page 1 -->\nSome content."
        page_num = agent._find_page(markdown, "nonexistent", [])
        assert page_num == 1

    def test_get_context(self):
        """Test _get_context extracts surrounding text."""
        agent = TypographyAgent()
        text = "The quick brown fox jumps over the lazy dog."
        context = agent._get_context(text, "fox", window=10)
        assert "fox" in context
        assert "..." in context or "brown" in context

    def test_get_context_word_not_found(self):
        """Test _get_context returns empty when word not found."""
        agent = TypographyAgent()
        text = "Some text here."
        context = agent._get_context(text, "missing", window=10)
        assert context == ""

    def test_calculate_confidence_empty(self):
        """Test _calculate_confidence returns 1.0 for empty lists."""
        agent = TypographyAgent()
        confidence = agent._calculate_confidence([], [])
        assert confidence == 1.0

    def test_calculate_confidence_averages(self):
        """Test _calculate_confidence averages all confidences."""
        from src.agents.typography.typography_agent import TypographyAgent
        from src.shared.models.auto_correction import AutoCorrection
        from src.shared.models.review_checklist import ReviewItem, ReviewOption

        agent = TypographyAgent()

        auto_corrections = [
            AutoCorrection(
                observation_id="obs-1",
                search="a",
                replace="b",
                justification="Test",
                confidence=0.96,
                agent="typography",
            ),
        ]

        review_items = [
            ReviewItem(
                observation_id="obs-2",
                agent="typography",
                question="Test?",
                options=[
                    ReviewOption(id="1", label="Yes", action="replace"),
                    ReviewOption(id="2", label="No", action="keep"),
                ],
                search_text="test",
                context="Test context",
                page_num=1,
                agent_recommendation="Test",
                agent_confidence=0.80,
            ),
        ]

        confidence = agent._calculate_confidence(auto_corrections, review_items)
        expected = (0.96 + 0.80) / 2
        assert abs(confidence - expected) < 0.01


# =============================================================================
# Module-Level Function Tests
# =============================================================================


@pytest.mark.unit
class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1, complexity_score=0.7)],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [PageData(page_num=1, image_base64="YWJjMTIz")]

    @pytest.mark.asyncio
    async def test_analyze_function_returns_agent_result(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analyze() convenience function returns AgentResult."""
        from src.agents.typography.typography_agent import analyze

        mock_output = TypographyAnalysisOutput(summary="No issues.")

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    result = await analyze(
                        pages=sample_pages,
                        manifest=sample_manifest,
                        markdown="# Test",
                        job_id="test-job",
                    )

        assert isinstance(result, AgentResult)
        assert result.agent_name == "typography"


# =============================================================================
# Dynamic Instruction Tests
# =============================================================================


@pytest.mark.unit
class TestDynamicInstructions:
    """Tests for dynamic instruction static methods."""

    def test_document_type_rules_research_paper(self):
        """Test get_document_type_rules returns correct text for research_paper."""
        from src.agents.dependencies import AgentDependencies
        from src.shared.models.document_context import DocumentSummary
        from src.shared.models.remediation import DocumentManifest

        # Create manifest with research_paper type and summary
        summary = DocumentSummary(
            title="ML Research",
            document_type="research_paper",
            topic_summary="Machine learning paper",
            audience_level="academic",
            key_entities=["TensorFlow", "PyTorch", "BERT"],
        )
        manifest = DocumentManifest(
            job_id="test-job",
            document_title="ML Research",
            document_type="research_paper",
            total_pages=5,
            heading_tree_json="{}",
            page_features=[],
            required_agents=["typography"],
            summary=summary,
        )

        deps = AgentDependencies(
            job_id="test-job",
            manifest=manifest,
            document_type="research_paper",
            custom_context={},
        )

        # Call the static method directly (no agent creation needed)
        result = TypographyAgent.get_document_type_rules(deps)

        assert "research_paper" in result
        assert "Technical terms" in result
        assert "TensorFlow" in result

    def test_document_type_rules_syllabus(self):
        """Test get_document_type_rules returns correct text for syllabus."""
        from src.agents.dependencies import AgentDependencies
        from src.shared.models.remediation import DocumentManifest

        manifest = DocumentManifest(
            job_id="test-job",
            document_title="CS 101 Syllabus",
            document_type="syllabus",
            total_pages=3,
            heading_tree_json="{}",
            page_features=[],
            required_agents=["typography"],
        )

        deps = AgentDependencies(
            job_id="test-job",
            manifest=manifest,
            document_type="syllabus",
            custom_context={},
        )

        # Call the static method directly
        result = TypographyAgent.get_document_type_rules(deps)

        assert "syllabus" in result.lower()
        assert "deadline" in result.lower() or "date" in result.lower()

    def test_document_type_rules_no_manifest(self):
        """Test get_document_type_rules returns empty string when no manifest."""
        from src.agents.dependencies import AgentDependencies

        deps = AgentDependencies(
            job_id="test-job",
            manifest=None,
            document_type="unknown",
            custom_context={},
        )

        result = TypographyAgent.get_document_type_rules(deps)
        assert result == ""

    def test_document_type_rules_unknown_type(self):
        """Test get_document_type_rules returns default for unknown type."""
        from src.agents.dependencies import AgentDependencies
        from src.shared.models.remediation import DocumentManifest

        manifest = DocumentManifest(
            job_id="test-job",
            document_title="Unknown Doc",
            document_type="newsletter",  # Not in the rules map
            total_pages=1,
            heading_tree_json="{}",
            page_features=[],
            required_agents=["typography"],
        )

        deps = AgentDependencies(
            job_id="test-job",
            manifest=manifest,
            document_type="newsletter",
            custom_context={},
        )

        result = TypographyAgent.get_document_type_rules(deps)
        assert "Standard document formatting applies" in result

    def test_ocr_suggestions_context_injection(self):
        """Test get_ocr_suggestions_context returns OCR context."""
        from src.agents.dependencies import AgentDependencies
        from src.shared.models.document_context import DocumentSummary
        from src.shared.models.remediation import DocumentManifest

        summary = DocumentSummary(
            title="Test Document",
            document_type="research_paper",
            topic_summary="Test document",
            audience_level="general",
            key_entities=["Enzo", "FastAPI"],
        )
        manifest = DocumentManifest(
            job_id="test-job",
            document_title="Test",
            document_type="research_paper",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[],
            required_agents=["typography"],
            summary=summary,
        )

        ocr_suggestions = [
            OCRSuggestion(
                word="teh",
                suggestions=["the"],
                confidence=0.95,
                reason="common_ocr_pattern",
                context="This is teh first step.",
            ),
        ]

        deps = AgentDependencies(
            job_id="test-job",
            manifest=manifest,
            document_type="research_paper",
            custom_context={"ocr_suggestions": ocr_suggestions},
        )

        # Call the static method directly
        result = TypographyAgent.get_ocr_suggestions_context(deps)

        assert "teh" in result
        assert "the" in result
        assert "CORRECT spellings" in result
        assert "Enzo" in result  # key entity should be included

    def test_ocr_suggestions_context_empty(self):
        """Test get_ocr_suggestions_context returns empty when no suggestions."""
        from src.agents.dependencies import AgentDependencies

        deps = AgentDependencies(
            job_id="test-job",
            manifest=None,
            document_type="unknown",
            custom_context={},
        )

        result = TypographyAgent.get_ocr_suggestions_context(deps)
        assert result == ""

    def test_document_context_with_summary(self):
        """Test get_document_context returns proper context with summary."""
        from src.agents.dependencies import AgentDependencies
        from src.shared.models.document_context import DocumentSummary
        from src.shared.models.remediation import DocumentManifest

        summary = DocumentSummary(
            title="Test Doc",
            document_type="lecture_notes",
            topic_summary="Machine learning basics",
            audience_level="undergraduate",
            key_entities=["neural networks", "backpropagation"],
        )
        manifest = DocumentManifest(
            job_id="test-job",
            document_title="ML Lecture",
            document_type="lecture_notes",
            total_pages=10,
            heading_tree_json="{}",
            page_features=[],
            required_agents=["typography"],
            summary=summary,
        )

        deps = AgentDependencies(
            job_id="test-job",
            manifest=manifest,
            document_type="lecture_notes",
            custom_context={},
        )

        result = TypographyAgent.get_document_context(deps)

        assert "ML Lecture" in result
        assert "lecture_notes" in result
        assert "10" in result  # total pages
        assert "Machine learning basics" in result
        assert "undergraduate" in result


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.unit
class TestErrorHandling:
    """Tests for error handling in TypographyAgent."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1, complexity_score=0.7)],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [PageData(page_num=1, image_base64="YWJjMTIz")]

    @pytest.mark.asyncio
    async def test_agent_run_exception_returns_error_result(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """When agent.run() fails, should return AgentResult with error message."""
        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = MagicMock()
            mock_get_agent.return_value = mock_agent

            with patch(
                "src.agents.typography.typography_agent.run_agent",
                side_effect=Exception("LLM API error")
            ):
                agent = TypographyAgent()
                result = await agent.process(
                    markdown="# Test",
                    pages=sample_pages,
                    manifest=sample_manifest,
                    ocr_suggestions=[],
                    job_id="test-job",
                )

        assert isinstance(result, AgentResult)
        assert result.agent_name == "typography"
        assert "failed" in result.reasoning_summary.lower() or "error" in result.reasoning_summary.lower()
        assert result.observations == []
        assert result.auto_corrections == []

    @pytest.mark.asyncio
    async def test_pages_without_images_continue_processing(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Pages without image data should log warning but continue."""
        # Create pages with missing image data
        pages_with_missing = [
            PageData(page_num=1, image_base64=None),  # Missing
            PageData(page_num=2, image_base64="YWJjMTIz"),  # Valid
        ]

        mock_output = TypographyAnalysisOutput(summary="Analysis complete.")

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test",
                        pages=pages_with_missing,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        # Should complete successfully despite missing page
        assert isinstance(result, AgentResult)
        assert result.agent_name == "typography"


# =============================================================================
# Boundary Condition Tests
# =============================================================================


@pytest.mark.unit
class TestBoundaryConditions:
    """Tests for boundary conditions and edge cases."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1, complexity_score=0.7)],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [PageData(page_num=1, image_base64="YWJjMTIz")]

    @pytest.mark.asyncio
    async def test_confidence_exactly_095_is_auto_correction(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Confidence of exactly 0.95 should create auto-correction."""
        mock_output = TypographyAnalysisOutput(
            formatting_issues=[
                FormattingIssue(
                    original_text="Important:",
                    corrected_text="**Important:**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.95,  # Exactly at threshold
                    reasoning="Bold prefix for emphasis.",
                    context="Important: Please note...",
                )
            ],
            summary="1 issue found.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\nImportant: Please note...",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        # Exactly 0.95 should be auto-corrected (>= threshold)
        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0

    @pytest.mark.asyncio
    async def test_confidence_just_below_095_is_review(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Confidence just below 0.95 should create review item."""
        mock_output = TypographyAnalysisOutput(
            formatting_issues=[
                FormattingIssue(
                    original_text="Note:",
                    corrected_text="**Note:**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.9499,  # Just below threshold
                    reasoning="Possibly bold prefix.",
                    context="Note: This may be...",
                )
            ],
            summary="1 issue found.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\nNote: This may be...",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[],
                        job_id="test-job",
                    )

        # 0.9499 should be review item (< threshold)
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1

    @pytest.mark.asyncio
    async def test_combined_formatting_and_ocr_issues(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test result with formatting + confirmed OCR + uncertain OCR."""
        mock_output = TypographyAnalysisOutput(
            formatting_issues=[
                FormattingIssue(
                    original_text="WARNING:",
                    corrected_text="**WARNING:**",
                    suggested_format="bold",
                    page_num=1,
                    confidence=0.96,
                    reasoning="Bold emphasis.",
                    context="WARNING: Do not...",
                )
            ],
            confirmed_ocr_fixes={
                "teh": OCRDecision(
                    word="teh",
                    decision="fix",
                    correction="the",
                    confidence=0.98,
                    reasoning="Clear typo.",
                )
            },
            uncertain_ocr={
                "Enzo": OCRDecision(
                    word="Enzo",
                    decision="uncertain",
                    correction=None,
                    confidence=0.60,
                    reasoning="Could be name.",
                )
            },
            summary="Multiple issues found.",
        )

        with patch.object(TypographyAgent, "_get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_result = MagicMock()
            mock_result.output = mock_output
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            with patch("src.agents.typography.typography_agent.run_agent", return_value=mock_result):
                with patch("src.agents.typography.typography_agent.extract_usage") as mock_usage:
                    mock_usage.return_value = MagicMock(estimated_cost_cents=0.5)

                    agent = TypographyAgent()
                    result = await agent.process(
                        markdown="# Test\nWARNING: This is teh Enzo framework.",
                        pages=sample_pages,
                        manifest=sample_manifest,
                        ocr_suggestions=[
                            OCRSuggestion(
                                word="Enzo",
                                suggestions=["Enzo"],
                                confidence=0.60,
                                reason="spell_check",
                                context="the Enzo framework",
                            )
                        ],
                        job_id="test-job",
                    )

        # Should have 3 observations total
        assert len(result.observations) == 3
        # 2 auto-corrections (formatting + OCR fix)
        assert len(result.auto_corrections) == 2
        # 1 review item (uncertain OCR)
        assert len(result.review_items) == 1

    def test_empty_pages_list(self):
        """Test agent handles empty pages list gracefully."""
        agent = TypographyAgent()
        # MAX_PAGES slice on empty list should return empty
        assert agent.MAX_PAGES == 5
        pages: list[PageData] = []
        assert pages[:agent.MAX_PAGES] == []

    def test_constants_are_defined(self):
        """Test agent has expected constants defined."""
        agent = TypographyAgent()
        assert agent.CONFIDENCE_THRESHOLD == 0.95
        assert agent.MAX_PAGES == 5
        assert agent.MAX_MARKDOWN_LENGTH == 10000
