"""Tests for StructureAgent (PRD-014, Issue #23).

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation
- Structure issue to observation conversion
- Heading tree summarization
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.agents.specialized_models import (
    StructureAnalysisOutput,
    StructureIssue,
)
from src.agents.structure_agent import StructureAgent
from src.services.pdf_converter import PageData
from src.shared.models.remediation import DocumentManifest, PageFeatures

# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestStructureIssue:
    """Tests for StructureIssue model."""

    def test_valid_structure_issue(self):
        """Test creating a valid StructureIssue."""
        issue = StructureIssue(
            issue_type="heading_skip",
            location_description="Section 2 heading",
            visual_evidence="Large bold text after H1",
            markup_state="Marked as H3, skipping H2",
            severity="major",
            recommended_fix="Change H3 to H2",
            confidence=0.9,
        )
        assert issue.issue_type == "heading_skip"
        assert issue.severity == "major"
        assert issue.confidence == 0.9

    def test_structure_issue_defaults(self):
        """Test StructureIssue default values."""
        issue = StructureIssue(
            issue_type="heading_mismatch",
            location_description="Page 1 header",
            visual_evidence="Bold large text",
            markup_state="Marked as paragraph",
            recommended_fix="Add H2 markup",
        )
        assert issue.severity == "major"
        assert issue.confidence == 0.8

    def test_confidence_validation(self):
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            StructureIssue(
                issue_type="heading_skip",
                location_description="Test",
                visual_evidence="Test",
                markup_state="Test",
                recommended_fix="Test",
                confidence=1.5,
            )

    def test_required_fields(self):
        """Test required fields are enforced."""
        with pytest.raises(ValidationError):
            StructureIssue(
                issue_type="heading_skip",
                # Missing required fields
            )


@pytest.mark.unit
class TestStructureAnalysisOutput:
    """Tests for StructureAnalysisOutput model."""

    def test_valid_output(self):
        """Test creating a valid StructureAnalysisOutput."""
        output = StructureAnalysisOutput(
            issues=[
                StructureIssue(
                    issue_type="heading_skip",
                    location_description="Section 2",
                    visual_evidence="Bold text",
                    markup_state="H3 after H1",
                    recommended_fix="Use H2",
                )
            ],
            heading_hierarchy_valid=False,
            reading_order_valid=True,
            notes="One heading skip found",
        )
        assert len(output.issues) == 1
        assert output.heading_hierarchy_valid is False
        assert output.reading_order_valid is True

    def test_output_defaults(self):
        """Test StructureAnalysisOutput default values."""
        output = StructureAnalysisOutput()
        assert output.issues == []
        assert output.heading_hierarchy_valid is True
        assert output.reading_order_valid is True
        assert output.notes == ""


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestStructureAgentInit:
    """Tests for StructureAgent initialization."""

    def test_agent_uses_reasoning_tier(self):
        """Test agent uses REASONING (Sonnet) model tier."""
        agent = StructureAgent()
        assert agent.model_tier == ModelTier.REASONING
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.REASONING]
        assert "sonnet" in agent.model_id.lower()

    def test_agent_has_correct_config(self):
        """Test agent configuration values."""
        agent = StructureAgent()
        assert agent.config.name == "structure_agent"
        assert agent.config.prompts_file == Path("structure.yaml")
        assert "heading_hierarchy" in agent.config.correction_types

    def test_agent_has_prompts(self):
        """Test agent has prompts (from file or defaults)."""
        agent = StructureAgent()
        assert "system_prompt" in agent.prompts
        assert "heading" in agent.prompts["system_prompt"].lower()


# =============================================================================
# Heading Tree Summarization Tests
# =============================================================================


@pytest.mark.unit
class TestHeadingTreeSummarization:
    """Tests for heading tree summarization."""

    def test_summarize_valid_heading_tree(self):
        """Test summarizing a valid heading tree."""
        agent = StructureAgent()

        heading_tree_json = '''{
            "document_title": "Test Document",
            "sections": [
                {"level": 1, "title": "Introduction", "page": 1},
                {"level": 2, "title": "Background", "page": 1},
                {"level": 2, "title": "Methods", "page": 2}
            ]
        }'''

        summary = agent._summarize_heading_tree(heading_tree_json)

        assert "Test Document" in summary
        assert "H1: Introduction" in summary
        assert "H2: Background" in summary
        assert "H2: Methods" in summary

    def test_summarize_empty_heading_tree(self):
        """Test summarizing an empty heading tree."""
        agent = StructureAgent()

        summary = agent._summarize_heading_tree('{}')

        assert "No heading structure available" in summary

    def test_summarize_invalid_json(self):
        """Test handling invalid JSON."""
        agent = StructureAgent()

        summary = agent._summarize_heading_tree('not valid json')

        assert "unavailable" in summary.lower()

    def test_summarize_truncates_long_trees(self):
        """Test summarization truncates very long heading trees."""
        import json

        agent = StructureAgent()

        # Create tree with 30 sections
        sections = [
            {"level": 2, "title": f"Section {i}", "page": i}
            for i in range(30)
        ]
        heading_tree_json = json.dumps({
            "document_title": "Long Doc",
            "sections": sections
        })

        summary = agent._summarize_heading_tree(heading_tree_json)

        # Should mention there are more sections
        assert "more sections" in summary.lower()


# =============================================================================
# Issue to Observation Conversion Tests
# =============================================================================


@pytest.mark.unit
class TestIssueToObservation:
    """Tests for converting StructureIssue to Observation."""

    def test_heading_skip_creates_observation(self):
        """Test heading skip issues create observations."""
        agent = StructureAgent()

        issues = [
            StructureIssue(
                issue_type="heading_skip",
                location_description="Section 2 heading",
                visual_evidence="Large bold text after H1",
                markup_state="Marked as H3",
                severity="critical",
                recommended_fix="Change to H2",
                confidence=0.99,  # Use 0.99 to exceed any reasonable threshold
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.agent == "structure"
        assert obs.severity == "critical"
        assert obs.route == "auto"  # Confidence 0.99 exceeds threshold
        assert obs.location.location_type == "element"

    def test_reading_order_issue_creates_observation(self):
        """Test reading order issues create observations."""
        agent = StructureAgent()

        issues = [
            StructureIssue(
                issue_type="reading_order",
                location_description="Multi-column section",
                visual_evidence="Two columns with related content",
                markup_state="Content interleaved incorrectly",
                severity="major",
                recommended_fix="Reorder to column-by-column",
                confidence=0.8,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=2, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.location.location_type == "region"

    def test_low_confidence_routes_to_manual(self):
        """Test low confidence issues route to manual review."""
        agent = StructureAgent()

        issues = [
            StructureIssue(
                issue_type="heading_mismatch",
                location_description="Ambiguous heading",
                visual_evidence="Medium bold text",
                markup_state="Paragraph",
                severity="minor",
                recommended_fix="Consider H3",
                confidence=0.5,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].route == "manual"
        assert observations[0].manual_reason is not None

    def test_invalid_severity_defaults_to_major(self):
        """Test invalid severity values default to major."""
        agent = StructureAgent()

        issues = [
            StructureIssue(
                issue_type="heading_skip",
                location_description="Test",
                visual_evidence="Test",
                markup_state="Test",
                severity="invalid",  # Not a valid severity
                recommended_fix="Test",
                confidence=0.9,
            )
        ]

        observations = agent._issues_to_observations(issues, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"


# =============================================================================
# Full Analysis Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestStructureAgentAnalysis:
    """Tests for full analysis workflow."""

    @pytest.fixture
    def sample_manifest(self) -> DocumentManifest:
        """Create sample manifest for testing."""
        return DocumentManifest(
            job_id="test-job-123",
            document_title="Test Document",
            document_type="syllabus",
            total_pages=3,
            heading_tree_json='{"document_title": "Test", "sections": []}',
            page_features=[
                PageFeatures(page_num=1, layout_type="single_column"),
                PageFeatures(page_num=2, layout_type="two_column"),
                PageFeatures(page_num=3, layout_type="single_column"),
            ],
            required_agents=["structure"],
        )

    @pytest.fixture
    def sample_pages(self) -> list[PageData]:
        """Create sample pages for testing."""
        return [
            PageData(page_num=1, image_base64="YWJjMTIz"),
            PageData(page_num=2, image_base64="ZGVmNDU2"),
            PageData(page_num=3, image_base64="Z2hpNzg5"),
        ]

    @pytest.mark.asyncio
    async def test_analyze_processes_all_pages(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test structure agent processes all pages."""
        agent = StructureAgent()

        mock_output = StructureAnalysisOutput(
            issues=[],
            heading_hierarchy_valid=True,
            reading_order_valid=True,
        )

        with patch.object(agent, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, AsyncMock(estimated_cost_cents=1.0))

            await agent.analyze(
                pages=sample_pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Should call for all 3 pages
            assert mock_run.call_count == 3

    @pytest.mark.asyncio
    async def test_analyze_returns_observations(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis returns observations from agent output."""
        agent = StructureAgent()

        mock_output = StructureAnalysisOutput(
            issues=[
                StructureIssue(
                    issue_type="heading_skip",
                    location_description="Section header",
                    visual_evidence="Bold text",
                    markup_state="H3 after H1",
                    severity="major",
                    recommended_fix="Use H2",
                    confidence=0.85,
                )
            ],
            heading_hierarchy_valid=False,
            reading_order_valid=True,
        )

        mock_usage = AsyncMock()
        mock_usage.estimated_cost_cents = 5.0

        with patch.object(agent, '_run_agent', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, mock_usage)

            observations, usage = await agent.analyze(
                pages=sample_pages[:1],
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            assert len(observations) == 1
            assert observations[0].agent == "structure"
            assert observations[0].job_id == "test-job"
            assert usage is not None  # Usage should be returned
