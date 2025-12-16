"""Tests for TypographyAgent.

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation
- Typography issue to observation conversion
- Complexity-based page filtering
- Module function pattern (get_agent, reset_agent, analyze)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents import typography_agent
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import (
    TypographyAnalysisOutput,
    TypographyIssue,
)
from src.agents.typography_agent import TypographyAgent
from src.services.pdf_converter import PageData
from src.shared.models.quality_signals import QualitySignals
from src.shared.models.reasoned import Reasoned
from src.shared.models.remediation import DocumentManifest, PageFeatures


def make_reasoned(value: str, reasoning: str = "Test reasoning for this value.") -> Reasoned:
    """Helper to create Reasoned objects for testing."""
    return Reasoned(reasoning=reasoning, value=value)


# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestTypographyIssue:
    """Tests for TypographyIssue model."""

    def test_valid_typography_issue(self):
        """Test creating a valid TypographyIssue."""
        issue = TypographyIssue(
            issue_type=make_reasoned("emphasis_unmarked", "Bold text not marked as strong."),
            visual_description="Bold text highlighting key term",
            markup_state="Plain text without emphasis",
            semantic_meaning="Term is being emphasized for importance",
            severity=make_reasoned("minor", "Missing emphasis is inconvenience, not barrier."),
            recommended_markup="**important term**",
            model_confidence=0.85,
        )
        assert issue.issue_type.value == "emphasis_unmarked"
        assert issue.severity.value == "minor"
        # Confidence is computed from quality signals + model_confidence
        assert issue.confidence == 0.97  # Default signals + 0.85 model_confidence

    def test_typography_issue_defaults(self):
        """Test TypographyIssue default values."""
        issue = TypographyIssue(
            issue_type=make_reasoned("definition_unmarked", "Italic text suggests definition."),
            visual_description="Italic text",
            markup_state="Plain text",
            semantic_meaning="Term being defined",
            severity=make_reasoned("minor", "Missing definition markup is minor."),
            recommended_markup="*term*",
        )
        # Confidence is computed from quality signals + default model_confidence
        assert issue.confidence == 0.96  # Default signals + default model_confidence

    def test_model_confidence_validation(self):
        """Test model_confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            TypographyIssue(
                issue_type=make_reasoned("emphasis_unmarked", "Test reasoning."),
                visual_description="Test",
                markup_state="Test",
                semantic_meaning="Test",
                severity=make_reasoned("minor", "Test severity reasoning."),
                recommended_markup="Test",
                model_confidence=1.5,
            )

    def test_required_fields(self):
        """Test required fields are enforced."""
        with pytest.raises(ValidationError):
            TypographyIssue(
                issue_type=make_reasoned("emphasis_unmarked", "Test reasoning."),
                # Missing required fields including severity
            )


@pytest.mark.unit
class TestTypographyAnalysisOutput:
    """Tests for TypographyAnalysisOutput model."""

    def test_valid_output(self):
        """Test creating a valid TypographyAnalysisOutput."""
        output = TypographyAnalysisOutput(
            page_num=1,
            issues=[
                TypographyIssue(
                    issue_type=make_reasoned("emphasis_unmarked", "Bold text not marked."),
                    visual_description="Bold text",
                    markup_state="Plain text",
                    semantic_meaning="Emphasis",
                    severity=make_reasoned("minor", "Missing emphasis is minor barrier."),
                    recommended_markup="**text**",
                )
            ],
            notes="One emphasis issue found",
        )
        assert output.page_num == 1
        assert len(output.issues) == 1

    def test_output_defaults(self):
        """Test TypographyAnalysisOutput default values."""
        output = TypographyAnalysisOutput(page_num=1)
        assert output.issues == []
        assert output.notes == ""


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestTypographyAgentInit:
    """Tests for TypographyAgent initialization and module functions."""

    def teardown_method(self):
        """Reset agent state after each test."""
        typography_agent.reset_agent()

    @patch("src.agents.typography_agent.load_prompts")
    @patch("src.agents.typography_agent.create_agent")
    def test_get_agent_creates_agent(self, mock_create, mock_load):
        """Test get_agent creates and returns agent."""
        mock_load.return_value = {"system_prompt": "test", "user_prompt_template": "test"}
        mock_agent = MagicMock()
        mock_create.return_value = mock_agent

        agent = typography_agent.get_agent()

        assert agent is mock_agent
        mock_load.assert_called_once_with("typography.yaml")
        mock_create.assert_called_once()
        # Verify create_agent was called with correct parameters
        call_args = mock_create.call_args
        assert call_args[0][0] == "typography.yaml"
        assert call_args[0][1] == TypographyAnalysisOutput
        assert call_args[1]["model_tier"] == ModelTier.REASONING
        assert call_args[1]["use_deps"] is True

    @patch("src.agents.typography_agent.load_prompts")
    @patch("src.agents.typography_agent.create_agent")
    def test_get_agent_returns_singleton(self, mock_create, mock_load):
        """Test get_agent returns same instance on multiple calls."""
        mock_load.return_value = {"system_prompt": "test", "user_prompt_template": "test"}
        mock_agent = MagicMock()
        mock_create.return_value = mock_agent

        agent1 = typography_agent.get_agent()
        agent2 = typography_agent.get_agent()

        assert agent1 is agent2
        # Should only create once
        assert mock_create.call_count == 1

    @patch("src.agents.typography_agent.load_prompts")
    @patch("src.agents.typography_agent.create_agent")
    def test_reset_agent_clears_singleton(self, mock_create, mock_load):
        """Test reset_agent clears cached agent."""
        mock_load.return_value = {"system_prompt": "test", "user_prompt_template": "test"}
        mock_agent = MagicMock()
        mock_create.return_value = mock_agent

        typography_agent.get_agent()
        typography_agent.reset_agent()
        typography_agent.get_agent()

        # Should create new instance after reset
        assert mock_create.call_count == 2

    def test_wrapper_class_exists(self):
        """Test TypographyAgent wrapper class can be instantiated."""
        wrapper = TypographyAgent()
        assert hasattr(wrapper, "analyze")
        assert callable(wrapper.analyze)


# =============================================================================
# Issue to Observation Conversion Tests
# =============================================================================


@pytest.mark.unit
class TestIssueToObservation:
    """Tests for converting TypographyIssue to Observation."""

    def test_emphasis_issue_creates_minor_observation(self):
        """Test emphasis issues create observations with model-determined severity."""
        issues = [
            TypographyIssue(
                issue_type=make_reasoned("emphasis_unmarked", "Bold text not marked as strong."),
                visual_description="Bold text for emphasis",
                markup_state="Plain text",
                semantic_meaning="Important emphasis",
                severity=make_reasoned("minor", "Missing emphasis is inconvenience, not barrier."),
                recommended_markup="**text**",
                model_confidence=0.99,  # Use 0.99 to exceed any reasonable threshold
            )
        ]

        observations = typography_agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.agent == "typography"
        assert obs.severity == "minor"  # From Reasoned[Severity]
        assert obs.route == "auto"  # Confidence 0.99 exceeds threshold

    def test_semantic_color_creates_major_observation(self):
        """Test semantic color issues create observations with model-determined severity."""
        issues = [
            TypographyIssue(
                issue_type=make_reasoned("semantic_color", "Red text indicates error without text alternative."),
                visual_description="Red text indicating error",
                markup_state="Plain text, no error indication",
                semantic_meaning="Error status indicator",
                severity=make_reasoned("major", "Color-only indication is significant accessibility barrier."),
                recommended_markup="**Error:** prefix",
                model_confidence=0.9,
            )
        ]

        observations = typography_agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"  # From Reasoned[Severity]

    def test_visual_heading_creates_major_observation(self):
        """Test visual heading issues create observations with model-determined severity."""
        issues = [
            TypographyIssue(
                issue_type=make_reasoned("visual_heading", "Large bold text functions as section header."),
                visual_description="Large bold text suggesting section header",
                markup_state="Paragraph text",
                semantic_meaning="Section heading",
                severity=make_reasoned("major", "Missing heading breaks document structure for screen readers."),
                recommended_markup="## Section Title",
                model_confidence=0.85,
            )
        ]

        observations = typography_agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"  # From Reasoned[Severity]

    def test_definition_issue_creates_minor_observation(self):
        """Test definition issues create observations with model-determined severity."""
        issues = [
            TypographyIssue(
                issue_type=make_reasoned("definition_unmarked", "Italic text is a term definition."),
                visual_description="Italic text for term definition",
                markup_state="Plain text",
                semantic_meaning="Term being defined",
                severity=make_reasoned("minor", "Missing definition markup is minor inconvenience."),
                recommended_markup="*term*",
                model_confidence=0.75,
            )
        ]

        observations = typography_agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "minor"  # From Reasoned[Severity]

    def test_low_confidence_routes_to_manual(self):
        """Test low confidence issues route to manual review."""
        issues = [
            TypographyIssue(
                issue_type=make_reasoned("emphasis_unmarked", "Unclear if this is intentional emphasis."),
                visual_description="Possibly bold text",
                markup_state="Plain text",
                semantic_meaning="Unclear emphasis",
                severity=make_reasoned("minor", "Unclear styling is low priority."),
                recommended_markup="**text**",
                model_confidence=0.3,  # Low model confidence
                quality_signals=QualitySignals(
                    image_clarity="blurry",
                    content_ambiguity="highly_ambiguous",
                ),
            )
        ]

        observations = typography_agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].route == "manual"
        assert observations[0].manual_reason is not None


# =============================================================================
# Full Analysis Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestTypographyAgentAnalysis:
    """Tests for full analysis workflow."""

    def teardown_method(self):
        """Reset agent state after each test."""
        typography_agent.reset_agent()

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=4,
            heading_tree_json='{}',
            page_features=[
                PageFeatures(
                    page_num=1,
                    complexity_score=0.3,
                    complexity_factors=[],
                ),
                PageFeatures(
                    page_num=2,
                    complexity_score=0.7,
                    complexity_factors=["dense text", "multiple fonts"],
                ),
                PageFeatures(
                    page_num=3,
                    complexity_score=0.4,
                    complexity_factors=[],
                ),
                PageFeatures(
                    page_num=4,
                    complexity_score=0.8,
                    complexity_factors=["color coding", "emphasis"],
                ),
            ],
            required_agents=["typography"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [
            PageData(page_num=1, image_base64="YWJjMTIz"),
            PageData(page_num=2, image_base64="ZGVmNDU2"),
            PageData(page_num=3, image_base64="Z2hpNzg5"),
            PageData(page_num=4, image_base64="amtsMTIz"),
        ]

    @pytest.mark.asyncio
    @patch("src.agents.factory.load_prompts")
    async def test_analyze_processes_all_provided_pages(
        self,
        mock_load_prompts,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test typography agent processes all pages provided to it.

        Note: Page filtering by complexity is done by AgentRouter, not the agent.
        The TypographyAgent processes ALL pages it receives.
        """
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{complexity_score}{complexity_factors}{page_markdown}"
        }

        mock_output = TypographyAnalysisOutput(
            page_num=2,
            issues=[],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)

        # Mock the agent's run method
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        with patch.object(typography_agent, "get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            # Mock extract_usage to return our mock usage
            with patch("src.agents.typography_agent.extract_usage", return_value=mock_usage):
                observations, usage = await typography_agent.analyze(
                    pages=sample_pages,
                    manifest=sample_manifest,
                    markdown="# Test",
                    job_id="test-job",
                )

            # Agent processes ALL provided pages - router is responsible for filtering
            assert mock_agent.run.call_count == len(sample_pages)

    @pytest.mark.asyncio
    @patch("src.agents.factory.load_prompts")
    async def test_analyze_returns_observations(
        self,
        mock_load_prompts,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis returns observations from agent output."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{complexity_score}{complexity_factors}{page_markdown}"
        }

        mock_output = TypographyAnalysisOutput(
            page_num=2,
            issues=[
                TypographyIssue(
                    issue_type=make_reasoned("emphasis_unmarked", "Bold text not marked as strong."),
                    visual_description="Bold key terms",
                    markup_state="Plain text",
                    semantic_meaning="Key terms emphasized",
                    severity=make_reasoned("minor", "Missing emphasis is minor barrier."),
                    recommended_markup="**term**",
                    model_confidence=0.85,
                )
            ],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=200, output_tokens=100, total_tokens=300, estimated_cost_cents=5.0)

        # Only provide pages that would be processed (complexity > 0.5)
        complex_pages = [p for p in sample_pages if p.page_num in [2, 4]]

        # Mock the agent's run method
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        with patch.object(typography_agent, "get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            # Mock extract_usage to return our mock usage
            with patch("src.agents.typography_agent.extract_usage", return_value=mock_usage):
                observations, usage = await typography_agent.analyze(
                    pages=complex_pages[:1],
                    manifest=sample_manifest,
                    markdown="# Test",
                    job_id="test-job",
                )

        assert len(observations) == 1
        assert observations[0].agent == "typography"
        assert observations[0].job_id == "test-job"
        assert usage is not None  # Usage should be returned

    @pytest.mark.asyncio
    @patch("src.agents.factory.load_prompts")
    async def test_analyze_formats_complexity_factors(
        self,
        mock_load_prompts,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis includes complexity factors in prompt."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{complexity_score}{complexity_factors}{page_markdown}"
        }

        mock_output = TypographyAnalysisOutput(page_num=2, issues=[])

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)

        # Only provide page 2 which has complexity factors
        pages = [p for p in sample_pages if p.page_num == 2]

        # Mock the agent's run method
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        with patch.object(typography_agent, "get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            # Mock extract_usage to return our mock usage
            with patch("src.agents.typography_agent.extract_usage", return_value=mock_usage):
                observations, usage = await typography_agent.analyze(
                    pages=pages,
                    manifest=sample_manifest,
                    markdown="# Test",
                    job_id="test-job",
                )

        # Check that the agent.run was called (format checking happens in actual implementation)
        assert mock_agent.run.call_count == 1
        call_args = mock_agent.run.call_args
        user_message = call_args[0][0]
        # Check that complexity factors were included in the message
        assert "dense text" in user_message or "multiple fonts" in user_message
        assert usage is not None  # Usage should be returned

    @pytest.mark.asyncio
    @patch("src.agents.factory.load_prompts")
    async def test_wrapper_class_delegates_to_module_function(
        self,
        mock_load_prompts,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test TypographyAgent wrapper class delegates to module analyze function."""
        # Setup mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "test",
            "user_prompt_template": "{page_num}{document_title}{complexity_score}{complexity_factors}{page_markdown}"
        }

        mock_output = TypographyAnalysisOutput(page_num=1, issues=[])

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)

        # Mock the agent's run method
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value.total_tokens = mock_usage.total_tokens

        with patch.object(typography_agent, "get_agent") as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            # Mock extract_usage to return our mock usage
            with patch("src.agents.typography_agent.extract_usage", return_value=mock_usage):
                wrapper = TypographyAgent()
                observations, usage = await wrapper.analyze(
                    pages=sample_pages[:1],
                    manifest=sample_manifest,
                    markdown="# Test",
                    job_id="test-job",
                )

        # Should have called the module function
        assert usage is not None
