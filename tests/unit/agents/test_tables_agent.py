"""Tests for TablesAgent.

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation
- Table analysis to observation conversion
- Complexity-based routing to manual review
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.agents.specialized_models import (
    TableAnalysis,
    TablesAnalysisOutput,
)
from src.agents.tables_agent import TablesAgent
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
class TestTableAnalysis:
    """Tests for TableAnalysis model."""

    def test_valid_table_analysis(self):
        """Test creating a valid TableAnalysis."""
        analysis = TableAnalysis(
            table_index=1,
            has_headers=True,
            header_structure="single_row",
            complexity=make_reasoned("simple", "Regular grid layout."),
            data_accuracy="accurate",
            visual_description="Grade distribution table",
            markdown_issues=["Missing header separator"],
            recommended_action="add_headers",
            model_confidence=0.85,
        )
        assert analysis.table_index == 1
        assert analysis.has_headers is True
        assert len(analysis.markdown_issues) == 1

    def test_table_analysis_defaults(self):
        """Test TableAnalysis default values."""
        analysis = TableAnalysis(
            table_index=1,
            complexity=make_reasoned("simple", "Standard table."),
            visual_description="Simple table",
        )
        assert analysis.has_headers is True
        assert analysis.header_structure == "single_row"
        assert analysis.complexity.value == "simple"
        assert analysis.data_accuracy == "accurate"
        assert analysis.markdown_issues == []
        assert analysis.recommended_action == "none"
        # Confidence is computed from quality signals
        assert analysis.confidence == 0.96  # Default signals + default model_confidence

    def test_model_confidence_validation(self):
        """Test model_confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            TableAnalysis(
                table_index=1,
                complexity=make_reasoned("simple"),
                visual_description="Test",
                model_confidence=1.5,
            )

    def test_table_index_validation(self):
        """Test table_index must be >= 1."""
        with pytest.raises(ValidationError):
            TableAnalysis(
                table_index=0,
                complexity=make_reasoned("simple"),
                visual_description="Test",
            )


@pytest.mark.unit
class TestTablesAnalysisOutput:
    """Tests for TablesAnalysisOutput model."""

    def test_valid_output(self):
        """Test creating a valid TablesAnalysisOutput."""
        output = TablesAnalysisOutput(
            page_num=1,
            tables_found=2,
            analyses=[
                TableAnalysis(
                    table_index=1,
                    complexity=make_reasoned("simple", "Standard grid."),
                    visual_description="Data table",
                )
            ],
            notes="Two tables found",
        )
        assert output.page_num == 1
        assert output.tables_found == 2
        assert len(output.analyses) == 1

    def test_output_defaults(self):
        """Test TablesAnalysisOutput default values."""
        output = TablesAnalysisOutput(page_num=1)
        assert output.tables_found == 0
        assert output.analyses == []
        assert output.notes == ""


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestTablesAgentInit:
    """Tests for TablesAgent initialization."""

    def test_agent_uses_reasoning_tier(self):
        """Test agent uses REASONING (Sonnet) model tier."""
        agent = TablesAgent()
        assert agent.model_tier == ModelTier.REASONING
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.REASONING]
        assert "sonnet" in agent.model_id.lower()

    def test_agent_has_correct_config(self):
        """Test agent configuration values."""
        agent = TablesAgent()
        assert agent.config.name == "tables_agent"
        assert agent.config.prompts_file == Path("tables.yaml")
        assert "table_headers" in agent.config.correction_types

    def test_agent_has_prompts(self):
        """Test agent has prompts (from file or defaults)."""
        agent = TablesAgent()
        assert "system_prompt" in agent.prompts
        assert "table" in agent.prompts["system_prompt"].lower()


# =============================================================================
# Analysis to Observation Conversion Tests
# =============================================================================


@pytest.mark.unit
class TestTableAnalysisToObservation:
    """Tests for converting TableAnalysis to Observation."""

    def test_missing_data_creates_critical_observation(self):
        """Test missing data creates critical severity observation."""
        agent = TablesAgent()

        analyses = [
            TableAnalysis(
                table_index=1,
                has_headers=True,
                complexity=make_reasoned("simple", "Regular structure."),
                data_accuracy="missing_data",
                visual_description="Complex table with missing cells",
                recommended_action="verify_data",
                model_confidence=0.7,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        obs = observations[0]
        assert obs.severity == "critical"

    def test_structural_loss_creates_critical_observation(self):
        """Test structural loss creates critical severity observation."""
        agent = TablesAgent()

        analyses = [
            TableAnalysis(
                table_index=1,
                complexity=make_reasoned("merged_cells", "Cells spanning columns."),
                data_accuracy="structural_loss",
                visual_description="Table with merged cells",
                recommended_action="restructure",
                model_confidence=0.75,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "critical"

    def test_complex_table_routes_to_manual(self):
        """Test complex tables route to manual review even with high confidence."""
        agent = TablesAgent()

        for complexity in ["merged_cells", "nested", "irregular"]:
            analyses = [
                TableAnalysis(
                    table_index=1,
                    complexity=make_reasoned(complexity, f"Table is {complexity}."),
                    visual_description="Complex table",
                    recommended_action="restructure",
                    model_confidence=0.99,  # High confidence, but complex table still routes to manual
                )
            ]

            observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

            assert len(observations) == 1
            assert observations[0].route == "manual"
            assert "Complex table structure" in observations[0].manual_reason

    def test_missing_headers_creates_major_observation(self):
        """Test tables without headers create major observations."""
        agent = TablesAgent()

        analyses = [
            TableAnalysis(
                table_index=1,
                has_headers=False,
                header_structure="none",
                complexity=make_reasoned("simple", "Regular layout."),
                data_accuracy="accurate",
                visual_description="Data table without headers",
                recommended_action="add_headers",
                model_confidence=0.85,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 1
        assert observations[0].severity == "major"

    def test_none_action_creates_no_observation(self):
        """Test analyses with action=none don't create observations."""
        agent = TablesAgent()

        analyses = [
            TableAnalysis(
                table_index=1,
                complexity=make_reasoned("simple", "Well-structured table."),
                visual_description="Well-formatted table",
                recommended_action="none",
                model_confidence=0.95,
            )
        ]

        observations = agent._analyses_to_observations(analyses, page_num=1, job_id="test-job")

        assert len(observations) == 0

    def test_low_confidence_routes_to_manual(self):
        """Test low confidence analyses route to manual review."""
        agent = TablesAgent()

        analyses = [
            TableAnalysis(
                table_index=1,
                complexity=make_reasoned("simple", "Uncertain structure."),
                visual_description="Unclear table",
                recommended_action="add_headers",
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
        assert "Low confidence" in observations[0].manual_reason


# =============================================================================
# Full Analysis Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestTablesAgentAnalysis:
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
                PageFeatures(page_num=1, has_tables=True, table_count=1),
                PageFeatures(page_num=2, has_tables=False, table_count=0),
                PageFeatures(page_num=3, has_tables=True, table_count=2),
            ],
            required_agents=["tables"],
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
    async def test_analyze_skips_pages_without_tables(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis skips pages without tables."""
        agent = TablesAgent()

        mock_output = TablesAnalysisOutput(
            page_num=1,
            tables_found=1,
            analyses=[],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=1.0)

        with patch.object(agent, '_run_with_deps', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, mock_usage)

            await agent.analyze(
                pages=sample_pages,
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            # Should only call for pages 1 and 3 (have tables)
            assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_returns_observations(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test analysis returns observations from agent output."""
        agent = TablesAgent()

        mock_output = TablesAnalysisOutput(
            page_num=1,
            tables_found=1,
            analyses=[
                TableAnalysis(
                    table_index=1,
                    has_headers=False,
                    complexity=make_reasoned("simple", "Standard table."),
                    visual_description="Data table",
                    recommended_action="add_headers",
                    model_confidence=0.85,
                ),
            ],
        )

        from src.shared.models.processing import LLMUsage
        mock_usage = LLMUsage(input_tokens=200, output_tokens=100, total_tokens=300, estimated_cost_cents=5.0)

        with patch.object(agent, '_run_with_deps', new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (mock_output, mock_usage)

            observations, usage = await agent.analyze(
                pages=sample_pages[:1],
                manifest=sample_manifest,
                markdown="# Test",
                job_id="test-job",
            )

            assert len(observations) == 1
            assert observations[0].agent == "tables"
            assert observations[0].job_id == "test-job"
            assert usage is not None  # Usage should be returned
