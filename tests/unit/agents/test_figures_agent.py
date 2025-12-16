"""Tests for FiguresAgent.

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation
- Image analysis to observation conversion
- Page filtering by image presence
- Confidence-based routing
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.figures_agent import FiguresAgent
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.agents.specialized_models import (
    FiguresAnalysisOutput,
    ImageAnalysis,
)
from src.services.pdf_converter import PageData
from src.shared.models.reasoned import Reasoned
from src.shared.models.remediation import DocumentManifest, PageFeatures


def make_reasoned(value: str, reasoning: str = "Test reasoning for this value.") -> Reasoned:
    """Helper to create Reasoned objects for testing."""
    return Reasoned(reasoning=reasoning, value=value)

# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestImageAnalysis:
    """Tests for ImageAnalysis model."""

    def test_valid_image_analysis(self):
        """Test creating a valid ImageAnalysis."""
        analysis = ImageAnalysis(
            image_index=1,
            image_type=make_reasoned("informative", "Chart with data points visible."),
            visual_description="A flowchart showing process steps",
            current_alt_status="empty",
            recommended_action=make_reasoned("add_alt", "Image lacks alt text."),
            suggested_alt="Flowchart showing 5 process steps",
            model_confidence=0.9,
        )
        assert analysis.image_index == 1
        assert analysis.image_type.value == "informative"
        assert analysis.recommended_action.value == "add_alt"

    def test_image_analysis_defaults(self):
        """Test ImageAnalysis default values."""
        analysis = ImageAnalysis(
            image_index=1,
            image_type=make_reasoned("decorative", "Background pattern with no content."),
            visual_description="Background pattern",
            current_alt_status="empty",
            recommended_action=make_reasoned("mark_decorative", "Decorative image."),
        )
        assert analysis.suggested_alt is None
        # Confidence is now a computed property from quality signals
        assert analysis.confidence == 0.96  # Default signals + default model_confidence

    def test_model_confidence_validation(self):
        """Test model_confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("informative"),
                visual_description="Test",
                current_alt_status="empty",
                recommended_action=make_reasoned("add_alt"),
                model_confidence=1.5,
            )

    def test_image_index_validation(self):
        """Test image_index must be >= 1."""
        with pytest.raises(ValidationError):
            ImageAnalysis(
                image_index=0,
                image_type=make_reasoned("informative"),
                visual_description="Test",
                current_alt_status="empty",
                recommended_action=make_reasoned("add_alt"),
            )


@pytest.mark.unit
class TestFiguresAnalysisOutput:
    """Tests for FiguresAnalysisOutput model."""

    def test_valid_output(self):
        """Test creating a valid FiguresAnalysisOutput."""
        output = FiguresAnalysisOutput(
            page_num=1,
            images_found=2,
            analyses=[
                ImageAnalysis(
                    image_index=1,
                    image_type=make_reasoned("informative", "Chart with data."),
                    visual_description="Chart",
                    current_alt_status="empty",
                    recommended_action=make_reasoned("add_alt", "Missing alt text."),
                )
            ],
            notes="Two images found",
        )
        assert output.page_num == 1
        assert output.images_found == 2
        assert len(output.analyses) == 1

    def test_output_defaults(self):
        """Test FiguresAnalysisOutput default values."""
        output = FiguresAnalysisOutput(page_num=1)
        assert output.images_found == 0
        assert output.analyses == []
        assert output.notes == ""


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestFiguresAgentInit:
    """Tests for FiguresAgent initialization."""

    def test_agent_uses_reasoning_tier(self):
        """Test agent uses REASONING (Sonnet) model tier."""
        # Agent uses lazy initialization - no AWS connection needed
        agent = FiguresAgent()
        assert agent.model_tier == ModelTier.REASONING
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.REASONING]
        assert "sonnet" in agent.model_id.lower()

    def test_agent_has_correct_config(self):
        """Test agent configuration values."""
        agent = FiguresAgent()
        assert agent.config.name == "figures_agent"
        assert agent.config.prompts_file == Path("figures.yaml")
        assert "alt_text" in agent.config.correction_types

    def test_agent_has_prompts(self):
        """Test agent has prompts (from file or defaults)."""
        agent = FiguresAgent()
        assert "system_prompt" in agent.prompts
        # Should have image-related content
        assert "image" in agent.prompts["system_prompt"].lower()


# =============================================================================
# Analysis to Observation Conversion Tests
# =============================================================================


@pytest.mark.unit
class TestAnalysisToObservation:
    """Tests for converting ImageAnalysis to Observation."""

    def test_informative_image_creates_major_observation(self):
        """Test informative images create major severity observations."""
        agent = FiguresAgent()

        analyses = [
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("informative", "Chart with data."),
                visual_description="Chart showing data",
                current_alt_status="empty",
                recommended_action=make_reasoned("add_alt", "Missing alt."),
                model_confidence=0.99,  # Use 0.99 to exceed any reasonable threshold
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.agent == "figures"
        assert obs.severity == "major"
        assert obs.route == "auto"  # Confidence exceeds threshold
        assert obs.location.page_num == 1

    def test_decorative_image_creates_minor_observation(self):
        """Test decorative images create minor severity observations."""
        agent = FiguresAgent()

        analyses = [
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("decorative", "Background pattern."),
                visual_description="Background pattern",
                current_alt_status="has description",
                recommended_action=make_reasoned("mark_decorative", "Decorative image."),
                model_confidence=0.85,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=2, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "minor"

    def test_low_confidence_routes_to_manual(self):
        """Test low confidence analyses route to manual review."""
        from src.shared.models.quality_signals import QualitySignals
        agent = FiguresAgent()

        # Use poor quality signals to get low confidence
        analyses = [
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("informative", "Unclear classification."),
                visual_description="Unclear image",
                current_alt_status="empty",
                recommended_action=make_reasoned("add_alt", "Missing alt."),
                model_confidence=0.3,  # Low model confidence
                quality_signals=QualitySignals(
                    image_clarity="blurry",
                    content_ambiguity="highly_ambiguous",
                ),
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].route == "manual"
        assert observations[0].manual_reason is not None

    def test_none_action_creates_no_observation(self):
        """Test analyses with action=none don't create observations."""
        agent = FiguresAgent()

        analyses = [
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("informative", "Good existing alt."),
                visual_description="Well-described image",
                current_alt_status="has description",
                recommended_action=make_reasoned("none", "Alt text is adequate."),
                model_confidence=0.95,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 0

    def test_complex_image_creates_major_observation(self):
        """Test complex images (charts, diagrams) create major observations."""
        agent = FiguresAgent()

        analyses = [
            ImageAnalysis(
                image_index=1,
                image_type=make_reasoned("complex", "Detailed infographic."),
                visual_description="Detailed infographic",
                current_alt_status="TODO placeholder",
                recommended_action=make_reasoned("add_long_desc", "Needs long description."),
                model_confidence=0.8,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"


# =============================================================================
# Page Markdown Extraction Tests
# =============================================================================


@pytest.mark.unit
class TestPageMarkdownExtraction:
    """Tests for extracting page-specific markdown."""

    def test_extract_page_markdown(self):
        """Test extracting markdown for specific page."""
        agent = FiguresAgent()

        # Create markdown with 100 lines
        markdown = "\n".join([f"Line {i}" for i in range(100)])

        page_md = agent._extract_page_markdown(markdown, page_num=1)

        # Should get a chunk of the markdown
        assert len(page_md) > 0
        assert "Line 0" in page_md

    def test_extract_empty_markdown(self):
        """Test extracting from empty markdown."""
        agent = FiguresAgent()

        page_md = agent._extract_page_markdown("", page_num=1)

        assert page_md == ""


# =============================================================================
# Full Analysis Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestFiguresAgentAnalysis:
    """Tests for full analysis workflow."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=3,
            heading_tree_json='{}',
            page_features=[
                PageFeatures(page_num=1, has_images=True, image_count=2),
                PageFeatures(page_num=2, has_images=False, image_count=0),
                PageFeatures(page_num=3, has_images=True, image_count=1),
            ],
            required_agents=["figures"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [
            PageData(page_num=1, image_base64="YWJjMTIz"),  # "abc123" in base64
            PageData(page_num=2, image_base64="ZGVmNDU2"),
            PageData(page_num=3, image_base64="Z2hpNzg5"),
        ]

    @pytest.mark.asyncio
    @patch("src.agents.figures_agent.get_agent")
    @patch("src.agents.figures_agent.load_prompts")
    async def test_analyze_skips_pages_without_images(
        self,
        mock_load_prompts,
        mock_get_agent,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis skips pages without images."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{expected_image_count}{page_markdown}"
        }

        # Mock the agent's run method
        mock_output = FiguresAnalysisOutput(
            page_num=1,
            images_found=1,
            analyses=[],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)

        mock_result = MagicMock()
        mock_result.data = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_get_agent.return_value = mock_agent

        # Mock extract_usage to return our mock usage
        with patch("src.agents.figures_agent.extract_usage", return_value=mock_usage):
            agent = FiguresAgent()

            await agent.analyze(
                pages=sample_pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Should only call for pages 1 and 3 (have images)
            assert mock_agent.run.call_count == 2

    @pytest.mark.asyncio
    @patch("src.agents.figures_agent.get_agent")
    @patch("src.agents.figures_agent.load_prompts")
    async def test_analyze_continues_on_page_error(
        self,
        mock_load_prompts,
        mock_get_agent,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis continues if one page fails."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{expected_image_count}{page_markdown}"
        }

        call_count = 0

        from src.shared.models.processing import LLMUsage

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("LLM error")
            # Second call succeeds
            mock_result = MagicMock()
            mock_result.data = FiguresAnalysisOutput(page_num=3, images_found=0, analyses=[])
            mock_result.usage.return_value.total_tokens = 150
            return mock_result

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=mock_run)
        mock_get_agent.return_value = mock_agent

        # Mock extract_usage to return our mock usage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)
        with patch("src.agents.figures_agent.extract_usage", return_value=mock_usage):
            agent = FiguresAgent()

            observations, usage = await agent.analyze(
                pages=sample_pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Should have tried both pages despite error
            assert call_count == 2
            assert observations == []  # No observations from successful page
            assert usage is not None  # Usage should be returned

    @pytest.mark.asyncio
    @patch("src.agents.figures_agent.get_agent")
    @patch("src.agents.figures_agent.load_prompts")
    async def test_analyze_returns_observations(
        self,
        mock_load_prompts,
        mock_get_agent,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis returns observations from agent output."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{expected_image_count}{page_markdown}"
        }

        mock_output = FiguresAnalysisOutput(
            page_num=1,
            images_found=2,
            analyses=[
                ImageAnalysis(
                    image_index=1,
                    image_type=make_reasoned("informative", "Test chart."),
                    visual_description="Test image",
                    current_alt_status="empty",
                    recommended_action=make_reasoned("add_alt", "Missing alt."),
                    model_confidence=0.9,
                ),
                ImageAnalysis(
                    image_index=2,
                    image_type=make_reasoned("decorative", "Simple border."),
                    visual_description="Border",
                    current_alt_status="has description",
                    recommended_action=make_reasoned("none", "Already has alt."),
                    model_confidence=0.95,
                ),
            ],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=200, output_tokens=100, total_tokens=300, estimated_cost_cents=5.0)

        mock_result = MagicMock()
        mock_result.data = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_get_agent.return_value = mock_agent

        # Mock extract_usage to return our mock usage
        with patch("src.agents.figures_agent.extract_usage", return_value=mock_usage):
            agent = FiguresAgent()

            observations, usage = await agent.analyze(
                pages=sample_pages[:1],  # Just page 1
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Should have 1 observation (second analysis has action=none)
            assert len(observations) == 1
            assert observations[0].agent == "figures"
            assert observations[0].job_id == "test-job"
            assert usage is not None  # Usage should be returned
