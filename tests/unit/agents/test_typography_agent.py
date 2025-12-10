"""Tests for TypographyAgent (PRD-014, Issue #23).

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation
- Typography issue to observation conversion
- Complexity-based page filtering
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.agents.specialized_models import (
    TypographyAnalysisOutput,
    TypographyIssue,
)
from src.agents.typography_agent import TypographyAgent
from src.services.pdf_converter import PageData
from src.shared.models.remediation import DocumentManifest, PageFeatures

# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestTypographyIssue:
    """Tests for TypographyIssue model."""

    def test_valid_typography_issue(self):
        """Test creating a valid TypographyIssue."""
        issue = TypographyIssue(
            issue_type="emphasis_unmarked",
            visual_description="Bold text highlighting key term",
            markup_state="Plain text without emphasis",
            semantic_meaning="Term is being emphasized for importance",
            recommended_markup="**important term**",
            confidence=0.85,
        )
        assert issue.issue_type == "emphasis_unmarked"
        assert issue.confidence == 0.85

    def test_typography_issue_defaults(self):
        """Test TypographyIssue default values."""
        issue = TypographyIssue(
            issue_type="definition_unmarked",
            visual_description="Italic text",
            markup_state="Plain text",
            semantic_meaning="Term being defined",
            recommended_markup="*term*",
        )
        assert issue.confidence == 0.8

    def test_confidence_validation(self):
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            TypographyIssue(
                issue_type="emphasis_unmarked",
                visual_description="Test",
                markup_state="Test",
                semantic_meaning="Test",
                recommended_markup="Test",
                confidence=1.5,
            )

    def test_required_fields(self):
        """Test required fields are enforced."""
        with pytest.raises(ValidationError):
            TypographyIssue(
                issue_type="emphasis_unmarked",
                # Missing required fields
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
                    issue_type="emphasis_unmarked",
                    visual_description="Bold text",
                    markup_state="Plain text",
                    semantic_meaning="Emphasis",
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
    """Tests for TypographyAgent initialization."""

    def test_agent_uses_reasoning_tier(self):
        """Test agent uses REASONING (Sonnet) model tier."""
        agent = TypographyAgent()
        assert agent.model_tier == ModelTier.REASONING
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.REASONING]
        assert "sonnet" in agent.model_id.lower()

    def test_agent_has_correct_config(self):
        """Test agent configuration values."""
        agent = TypographyAgent()
        assert agent.config.name == "typography_agent"
        assert agent.config.prompts_file == Path("typography.yaml")
        assert "emphasis" in agent.config.correction_types

    def test_agent_has_prompts(self):
        """Test agent has prompts (from file or defaults)."""
        agent = TypographyAgent()
        assert "system_prompt" in agent.prompts
        # Should have typography-related content
        assert "bold" in agent.prompts["system_prompt"].lower() or "emphasis" in agent.prompts["system_prompt"].lower()


# =============================================================================
# Issue to Observation Conversion Tests
# =============================================================================


@pytest.mark.unit
class TestIssueToObservation:
    """Tests for converting TypographyIssue to Observation."""

    def test_emphasis_issue_creates_minor_observation(self):
        """Test emphasis issues create minor observations."""
        agent = TypographyAgent()

        issues = [
            TypographyIssue(
                issue_type="emphasis_unmarked",
                visual_description="Bold text for emphasis",
                markup_state="Plain text",
                semantic_meaning="Important emphasis",
                recommended_markup="**text**",
                confidence=0.85,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.agent == "typography"
        assert obs.severity == "minor"
        assert obs.route == "auto"

    def test_semantic_color_creates_major_observation(self):
        """Test semantic color issues create major observations."""
        agent = TypographyAgent()

        issues = [
            TypographyIssue(
                issue_type="semantic_color",
                visual_description="Red text indicating error",
                markup_state="Plain text, no error indication",
                semantic_meaning="Error status indicator",
                recommended_markup="**Error:** prefix",
                confidence=0.9,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"

    def test_visual_heading_creates_major_observation(self):
        """Test visual heading issues create major observations."""
        agent = TypographyAgent()

        issues = [
            TypographyIssue(
                issue_type="visual_heading",
                visual_description="Large bold text suggesting section header",
                markup_state="Paragraph text",
                semantic_meaning="Section heading",
                recommended_markup="## Section Title",
                confidence=0.8,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"

    def test_definition_issue_creates_minor_observation(self):
        """Test definition issues create minor observations."""
        agent = TypographyAgent()

        issues = [
            TypographyIssue(
                issue_type="definition_unmarked",
                visual_description="Italic text for term definition",
                markup_state="Plain text",
                semantic_meaning="Term being defined",
                recommended_markup="*term*",
                confidence=0.75,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "minor"

    def test_low_confidence_routes_to_manual(self):
        """Test low confidence issues route to manual review."""
        agent = TypographyAgent()

        issues = [
            TypographyIssue(
                issue_type="emphasis_unmarked",
                visual_description="Possibly bold text",
                markup_state="Plain text",
                semantic_meaning="Unclear emphasis",
                recommended_markup="**text**",
                confidence=0.5,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].route == "manual"
        assert observations[0].manual_reason is not None


# =============================================================================
# Full Analysis Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestTypographyAgentAnalysis:
    """Tests for full analysis workflow."""

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
    async def test_analyze_processes_all_provided_pages(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test typography agent processes all pages provided to it.

        Note: Page filtering by complexity is done by AgentRouter, not the agent.
        The TypographyAgent processes ALL pages it receives.
        """
        agent = TypographyAgent()

        mock_output = TypographyAnalysisOutput(
            page_num=2,
            issues=[],
        )

        with patch.object(agent, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, AsyncMock(estimated_cost_cents=1.0))

            await agent.analyze(
                pages=sample_pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Agent processes ALL provided pages - router is responsible for filtering
            assert mock_run.call_count == len(sample_pages)

    @pytest.mark.asyncio
    async def test_analyze_returns_observations(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis returns observations from agent output."""
        agent = TypographyAgent()

        mock_output = TypographyAnalysisOutput(
            page_num=2,
            issues=[
                TypographyIssue(
                    issue_type="emphasis_unmarked",
                    visual_description="Bold key terms",
                    markup_state="Plain text",
                    semantic_meaning="Key terms emphasized",
                    recommended_markup="**term**",
                    confidence=0.85,
                )
            ],
        )

        mock_usage = AsyncMock()
        mock_usage.estimated_cost_cents = 5.0

        # Only provide pages that would be processed (complexity > 0.5)
        complex_pages = [p for p in sample_pages if p.page_num in [2, 4]]

        with patch.object(agent, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, mock_usage)

            observations = await agent.analyze(
                pages=complex_pages[:1],
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            assert len(observations) == 1
            assert observations[0].agent == "typography"
            assert observations[0].job_id == "test-job"

    @pytest.mark.asyncio
    async def test_analyze_formats_complexity_factors(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis includes complexity factors in prompt."""
        agent = TypographyAgent()

        mock_output = TypographyAnalysisOutput(page_num=2, issues=[])
        mock_usage = AsyncMock()
        mock_usage.estimated_cost_cents = 1.0

        # Only provide page 2 which has complexity factors
        pages = [p for p in sample_pages if p.page_num == 2]

        with patch.object(agent, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, mock_usage)

            await agent.analyze(
                pages=pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Check that the prompt included complexity factors
            call_args = mock_run.call_args
            user_message = call_args[0][0]
            assert "dense text" in user_message or "multiple fonts" in user_message
