"""Tests for AnalysisAgent (PRD-012).

Tests cover:
- Model tier selection and cost calculation
- Output model validation
- DocumentManifest generation
- Observation conversion
- Agent routing logic
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.analysis_agent import (
    AnalysisAgent,
    AnalysisAgentConfig,
    AnalysisObservation,
    AnalysisOutput,
    AnalysisPageFeatures,
    HeadingNode,
    HeadingTree,
)
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.pdf_converter import PageData
from src.shared.llm_cost import (
    HAIKU_PRICING,
    SONNET_PRICING,
    calculate_estimated_cost,
    get_pricing_for_tier,
)
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest

# =============================================================================
# Model Tier Tests
# =============================================================================


@pytest.mark.unit
class TestModelTiers:
    """Tests for model tier configuration."""

    def test_model_tier_enum_values(self):
        """Test ModelTier enum has correct values."""
        assert ModelTier.REASONING.value == "reasoning"
        assert ModelTier.EFFICIENT.value == "efficient"

    def test_model_tier_map_contains_all_tiers(self):
        """Test MODEL_TIER_MAP has all tiers mapped."""
        assert ModelTier.REASONING in MODEL_TIER_MAP
        assert ModelTier.EFFICIENT in MODEL_TIER_MAP

    def test_reasoning_tier_uses_sonnet(self):
        """Test REASONING tier maps to Sonnet model."""
        assert "sonnet" in MODEL_TIER_MAP[ModelTier.REASONING].lower()

    def test_efficient_tier_uses_haiku(self):
        """Test EFFICIENT tier maps to Haiku model."""
        assert "haiku" in MODEL_TIER_MAP[ModelTier.EFFICIENT].lower()


@pytest.mark.unit
class TestLLMCostCalculation:
    """Tests for LLM cost calculation with model tiers."""

    def test_get_pricing_for_reasoning_tier(self):
        """Test get_pricing_for_tier returns Sonnet pricing for REASONING."""
        pricing = get_pricing_for_tier(ModelTier.REASONING)
        assert pricing == SONNET_PRICING
        assert pricing.model_name == "Claude Sonnet 4.5 (Bedrock)"

    def test_get_pricing_for_efficient_tier(self):
        """Test get_pricing_for_tier returns Haiku pricing for EFFICIENT."""
        pricing = get_pricing_for_tier(ModelTier.EFFICIENT)
        assert pricing == HAIKU_PRICING
        assert pricing.model_name == "Claude Haiku 4.5 (Bedrock)"

    def test_sonnet_pricing_higher_than_haiku(self):
        """Test Sonnet pricing is higher than Haiku."""
        assert SONNET_PRICING.input_cost_per_token_cents > HAIKU_PRICING.input_cost_per_token_cents
        assert SONNET_PRICING.output_cost_per_token_cents > HAIKU_PRICING.output_cost_per_token_cents

    def test_cost_calculation_with_sonnet(self):
        """Test cost calculation with Sonnet pricing."""
        # 1000 input tokens, 100 output tokens
        cost = calculate_estimated_cost(1000, 100, SONNET_PRICING)
        # $3/1M input = 0.0003 cents/token
        # $15/1M output = 0.0015 cents/token
        expected_input = 1000 * 0.0003  # 0.3 cents
        expected_output = 100 * 0.0015  # 0.15 cents
        assert cost == pytest.approx(expected_input + expected_output, rel=0.01)

    def test_cost_calculation_with_haiku(self):
        """Test cost calculation with Haiku pricing."""
        # 1000 input tokens, 100 output tokens
        cost = calculate_estimated_cost(1000, 100, HAIKU_PRICING)
        # $1/1M input = 0.0001 cents/token
        # $5/1M output = 0.0005 cents/token
        expected_input = 1000 * 0.0001  # 0.1 cents
        expected_output = 100 * 0.0005  # 0.05 cents
        assert cost == pytest.approx(expected_input + expected_output, rel=0.01)


# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestHeadingNode:
    """Tests for HeadingNode model."""

    def test_valid_heading_node(self):
        """Test creating a valid HeadingNode."""
        node = HeadingNode(
            level=2,
            title="Introduction",
            page=1,
            section_number="1.0"
        )
        assert node.level == 2
        assert node.title == "Introduction"
        assert node.page == 1
        assert node.section_number == "1.0"

    def test_heading_node_without_section_number(self):
        """Test HeadingNode with no section number."""
        node = HeadingNode(level=1, title="Title", page=1)
        assert node.section_number is None

    def test_heading_level_validation(self):
        """Test heading level must be 1-6."""
        with pytest.raises(ValidationError):
            HeadingNode(level=0, title="Invalid", page=1)
        with pytest.raises(ValidationError):
            HeadingNode(level=7, title="Invalid", page=1)

    def test_page_validation(self):
        """Test page must be >= 1."""
        with pytest.raises(ValidationError):
            HeadingNode(level=1, title="Test", page=0)


@pytest.mark.unit
class TestHeadingTree:
    """Tests for HeadingTree model."""

    def test_valid_heading_tree(self):
        """Test creating a valid HeadingTree."""
        tree = HeadingTree(
            document_title="Test Document",
            title_page=1,
            sections=[
                HeadingNode(level=2, title="Section 1", page=2),
                HeadingNode(level=2, title="Section 2", page=3),
            ],
            layout_type="single_column",
            confidence=0.9
        )
        assert tree.document_title == "Test Document"
        assert len(tree.sections) == 2
        assert tree.layout_type == "single_column"
        assert tree.confidence == 0.9

    def test_heading_tree_defaults(self):
        """Test HeadingTree default values."""
        tree = HeadingTree(document_title="Test")
        assert tree.title_page == 1
        assert tree.sections == []
        assert tree.layout_type == "single_column"
        assert tree.confidence == 0.9

    def test_layout_type_validation(self):
        """Test layout_type must be valid literal."""
        with pytest.raises(ValidationError):
            HeadingTree(document_title="Test", layout_type="invalid")

    def test_confidence_validation(self):
        """Test confidence must be 0.0-1.0."""
        with pytest.raises(ValidationError):
            HeadingTree(document_title="Test", confidence=1.5)
        with pytest.raises(ValidationError):
            HeadingTree(document_title="Test", confidence=-0.1)


@pytest.mark.unit
class TestAnalysisPageFeatures:
    """Tests for AnalysisPageFeatures model."""

    def test_valid_page_features(self):
        """Test creating valid page features."""
        features = AnalysisPageFeatures(
            page_num=1,
            has_images=True,
            image_count=2,
            has_tables=True,
            table_count=1,
            has_lists=True,
            layout_type="two_column",
            complexity_score=0.8,
            complexity_factors=["dense tables", "images"]
        )
        assert features.page_num == 1
        assert features.has_images is True
        assert features.image_count == 2
        assert features.complexity_score == 0.8

    def test_page_features_defaults(self):
        """Test default values for page features."""
        features = AnalysisPageFeatures(page_num=1)
        assert features.has_images is False
        assert features.image_count == 0
        assert features.has_tables is False
        assert features.layout_type == "single_column"
        assert features.complexity_score == 0.5
        assert features.complexity_factors == []

    def test_page_num_validation(self):
        """Test page_num must be >= 1."""
        with pytest.raises(ValidationError):
            AnalysisPageFeatures(page_num=0)


@pytest.mark.unit
class TestAnalysisObservation:
    """Tests for AnalysisObservation model."""

    def test_valid_observation(self):
        """Test creating a valid observation."""
        obs = AnalysisObservation(
            page_num=3,
            visual_description="Chart showing enrollment trends",
            markup_issue="Image has empty alt text",
            severity="major",
            confidence=0.85
        )
        assert obs.page_num == 3
        assert obs.severity == "major"
        assert obs.confidence == 0.85

    def test_observation_defaults(self):
        """Test default values for observation."""
        obs = AnalysisObservation(
            page_num=1,
            visual_description="Image",
            markup_issue="Missing alt"
        )
        assert obs.severity == "major"
        assert obs.confidence == 0.8

    def test_severity_validation(self):
        """Test severity must be valid literal."""
        with pytest.raises(ValidationError):
            AnalysisObservation(
                page_num=1,
                visual_description="Test",
                markup_issue="Test",
                severity="invalid"
            )


@pytest.mark.unit
class TestAnalysisOutput:
    """Tests for AnalysisOutput model."""

    def test_valid_analysis_output(self):
        """Test creating a valid analysis output."""
        output = AnalysisOutput(
            document_title="CS 101 Syllabus",
            document_type="syllabus",
            heading_tree=HeadingTree(document_title="CS 101 Syllabus"),
            page_features=[AnalysisPageFeatures(page_num=1)],
            required_agents=["figures", "tables"],
            observations=[],
            confidence=0.9,
            notes="Clear structure"
        )
        assert output.document_title == "CS 101 Syllabus"
        assert output.document_type == "syllabus"
        assert len(output.required_agents) == 2

    def test_analysis_output_defaults(self):
        """Test default values for analysis output."""
        output = AnalysisOutput(
            heading_tree=HeadingTree(document_title="Test"),
            page_features=[]
        )
        assert output.document_title == "Untitled"
        assert output.document_type == "unknown"
        assert output.required_agents == []
        assert output.observations == []
        assert output.confidence == 0.8
        assert output.notes == ""


# =============================================================================
# AnalysisAgent Tests
# =============================================================================


@pytest.mark.unit
class TestAnalysisAgentConfig:
    """Tests for AnalysisAgentConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AnalysisAgentConfig()
        assert config.prompts_file == Path("analysis.yaml")
        assert config.max_retries == 2
        assert config.temperature == 0.3
        assert config.max_tokens == 4096

    def test_custom_config(self):
        """Test custom configuration values."""
        config = AnalysisAgentConfig(
            prompts_file=Path("custom.yaml"),
            max_retries=3,
            temperature=0.5,
            max_tokens=8192
        )
        assert config.prompts_file == Path("custom.yaml")
        assert config.max_retries == 3


@pytest.mark.unit
class TestAnalysisAgentInitialization:
    """Tests for AnalysisAgent initialization."""

    @patch("src.agents.analysis_agent.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_agent_initialization(self, mock_open, mock_yaml):
        """Test agent initializes correctly."""
        mock_yaml.return_value = {
            "system_prompt": "Test system prompt",
            "user_prompt": "Test user prompt"
        }
        agent = AnalysisAgent()

        assert agent.model_tier == ModelTier.REASONING
        assert "sonnet" in agent.model_id.lower()
        assert agent.prompts is not None

    def test_agent_uses_default_prompts_when_file_not_found(self):
        """Test agent uses default prompts when file not found."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        assert "system_prompt" in agent.prompts
        assert "user_prompt" in agent.prompts

    def test_all_agents_constant(self):
        """Test ALL_AGENTS constant contains expected agents."""
        expected = {"figures", "tables", "structure", "typography"}
        assert AnalysisAgent.ALL_AGENTS == expected


@pytest.mark.unit
class TestAnalysisAgentHelperMethods:
    """Tests for AnalysisAgent helper methods."""

    def test_determine_skip_agents_all_required(self):
        """Test skip agents when all are required."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        required = ["figures", "tables", "structure", "typography"]
        skip = agent._determine_skip_agents(required)

        assert skip == []

    def test_determine_skip_agents_some_required(self):
        """Test skip agents when some are required."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        required = ["figures", "tables"]
        skip = agent._determine_skip_agents(required)

        assert set(skip) == {"structure", "typography"}

    def test_determine_skip_agents_none_required(self):
        """Test skip agents when none are required."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        required = []
        skip = agent._determine_skip_agents(required)

        assert set(skip) == {"figures", "tables", "structure", "typography"}

    def test_build_image_messages(self):
        """Test building image messages from pages."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        # Create mock pages
        pages = [
            PageData(page_num=1, image_base64="dGVzdDE="),  # "test1" in base64
            PageData(page_num=2, image_base64="dGVzdDI="),  # "test2" in base64
        ]

        messages = agent._build_image_messages(pages)

        # Should have alternating text and binary content
        assert len(messages) == 4  # 2 pages * (1 text + 1 image)
        assert messages[0] == "[Page 1]"
        assert messages[2] == "[Page 2]"


@pytest.mark.unit
class TestAnalysisAgentManifestCreation:
    """Tests for manifest creation from analysis output."""

    def test_create_manifest_basic(self):
        """Test basic manifest creation."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        output = AnalysisOutput(
            document_title="Test Document",
            document_type="lecture_notes",
            heading_tree=HeadingTree(
                document_title="Test Document",
                layout_type="single_column",
                confidence=0.9
            ),
            page_features=[
                AnalysisPageFeatures(page_num=1, has_images=True, image_count=2),
                AnalysisPageFeatures(page_num=2, has_tables=True, table_count=1),
            ],
            required_agents=["figures", "tables"],
            confidence=0.85,
            notes="Test notes"
        )

        manifest = agent._create_manifest(output, "job-123", 2)

        assert manifest.job_id == "job-123"
        assert manifest.document_title == "Test Document"
        assert manifest.document_type == "lecture_notes"
        assert manifest.total_pages == 2
        assert len(manifest.page_features) == 2
        assert manifest.required_agents == ["figures", "tables"]
        assert set(manifest.skip_agents) == {"structure", "typography"}
        assert manifest.analysis_confidence == 0.85
        assert manifest.analysis_notes == "Test notes"
        assert "sonnet" in manifest.analysis_model.lower()

    def test_create_manifest_fills_missing_pages(self):
        """Test manifest creation fills missing page features."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        # Only provide features for page 2
        output = AnalysisOutput(
            heading_tree=HeadingTree(document_title="Test"),
            page_features=[
                AnalysisPageFeatures(page_num=2, has_images=True)
            ]
        )

        manifest = agent._create_manifest(output, "job-123", 3)

        # Should have features for all 3 pages
        assert len(manifest.page_features) == 3
        page_nums = [pf.page_num for pf in manifest.page_features]
        assert page_nums == [1, 2, 3]

        # Page 1 and 3 should have defaults
        page1 = manifest.page_features[0]
        assert page1.has_images is False

        # Page 2 should have our data
        page2 = manifest.page_features[1]
        assert page2.has_images is True


@pytest.mark.unit
class TestAnalysisAgentObservationConversion:
    """Tests for observation conversion."""

    def test_convert_observations_basic(self):
        """Test basic observation conversion."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        analysis_obs = [
            AnalysisObservation(
                page_num=3,
                visual_description="Chart showing trends",
                markup_issue="Empty alt text",
                severity="major",
                confidence=0.9
            )
        ]

        observations = agent._convert_observations(analysis_obs, "job-123")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.job_id == "job-123"
        assert obs.agent == "analysis"
        assert obs.source == "agent"
        assert obs.visual_description == "Chart showing trends"
        assert obs.markup_description == "Empty alt text"
        assert obs.severity == "major"
        assert obs.confidence == 0.9
        assert obs.location.page_num == 3
        assert obs.route == "auto"  # confidence >= 0.7

    def test_convert_observations_low_confidence_routes_manual(self):
        """Test low confidence observations route to manual."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        analysis_obs = [
            AnalysisObservation(
                page_num=1,
                visual_description="Unclear image",
                markup_issue="May need description",
                confidence=0.5  # Below 0.7 threshold
            )
        ]

        observations = agent._convert_observations(analysis_obs, "job-123")

        assert len(observations) == 1
        assert observations[0].route == "manual"

    def test_convert_observations_empty_list(self):
        """Test converting empty observation list."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        observations = agent._convert_observations([], "job-123")

        assert observations == []


@pytest.mark.unit
class TestAnalysisAgentAnalyze:
    """Tests for the analyze method."""

    @pytest.mark.asyncio
    async def test_analyze_raises_on_empty_pages(self):
        """Test analyze raises ValueError on empty pages."""
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)

        with pytest.raises(ValueError, match="No pages provided"):
            await agent.analyze([], "job-123")

    @pytest.mark.asyncio
    async def test_analyze_returns_correct_types(self):
        """Test analyze returns correct types."""
        # Create mock agent with run method
        mock_pydantic_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output = AnalysisOutput(
            document_title="Test",
            heading_tree=HeadingTree(document_title="Test"),
            page_features=[AnalysisPageFeatures(page_num=1)],
            required_agents=["figures"],
            observations=[
                AnalysisObservation(
                    page_num=1,
                    visual_description="Test image",
                    markup_issue="No alt text"
                )
            ]
        )
        mock_result.usage.return_value = MagicMock(
            request_tokens=1000,
            response_tokens=500
        )
        mock_pydantic_agent.run = AsyncMock(return_value=mock_result)

        # Create agent and replace _get_agent
        config = AnalysisAgentConfig(prompts_file=Path("nonexistent.yaml"))
        agent = AnalysisAgent(config)
        agent._get_agent = MagicMock(return_value=mock_pydantic_agent)

        pages = [PageData(page_num=1, image_base64="dGVzdA==")]
        manifest, observations, usage = await agent.analyze(pages, "job-123")

        # Verify types
        assert isinstance(manifest, DocumentManifest)
        assert isinstance(observations, list)
        assert all(isinstance(o, Observation) for o in observations)
        assert isinstance(usage, LLMUsage)

        # Verify content
        assert manifest.job_id == "job-123"
        assert manifest.required_agents == ["figures"]
        assert len(observations) == 1
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
