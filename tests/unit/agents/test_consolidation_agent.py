"""Tests for ConsolidationAgent.

Tests cover:
- Agent configuration and initialization
- Model tier selection (REASONING = Sonnet)
- Output model validation (ProposalDraft, ConsolidationOutput, ConflictRecord)
- Observation formatting (XML structure)
- Proposal validation and conversion
- Confidence-based routing with route reasoning
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.consolidation_agent import (
    ConflictRecord,
    ConsolidationAgent,
    ConsolidationOutput,
    ProposalDraft,
)
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.shared.models.observation import Observation, ObservationLocation

# =============================================================================
# Output Model Tests
# =============================================================================


@pytest.mark.unit
class TestProposalDraft:
    """Tests for ProposalDraft model."""

    def test_valid_proposal_draft(self) -> None:
        """Test creating a valid ProposalDraft with all fields."""
        draft = ProposalDraft(
            observation_ids=["obs-1", "obs-2"],
            search_text="![](image.png)",
            replace_text="![Alt text](image.png)",
            justification="Adding alt text to image",
            page_nums=[1],
            estimated_impact="Adds alt text to 1 image",
            confidence=0.9,
            grouping_rationale="Both observations about same image on page 1",
            route="auto",
            route_reasoning="High confidence (0.9), simple image fix",
        )
        assert len(draft.observation_ids) == 2
        assert draft.confidence == 0.9
        assert draft.page_nums == [1]
        assert draft.route == "auto"
        assert "High confidence" in draft.route_reasoning

    def test_proposal_draft_required_fields(self) -> None:
        """Test ProposalDraft with only required fields."""
        draft = ProposalDraft(
            observation_ids=["obs-1"],
            search_text="old",
            replace_text="new",
            justification="Test justification",
            confidence=0.85,
            route="auto",
            route_reasoning="Simple fix with high confidence",
        )
        # Optional fields should have defaults
        assert draft.page_nums == []
        assert draft.estimated_impact == ""
        assert draft.grouping_rationale == "Single observation, no grouping needed."

    def test_confidence_is_required(self) -> None:
        """Test confidence must be provided (not defaulted to 0.8)."""
        with pytest.raises(ValidationError) as exc_info:
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                route="auto",
                route_reasoning="Test",
                # confidence omitted - should fail
            )
        assert "confidence" in str(exc_info.value)

    def test_route_is_required(self) -> None:
        """Test route must be provided."""
        with pytest.raises(ValidationError) as exc_info:
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                confidence=0.8,
                route_reasoning="Test",
                # route omitted - should fail
            )
        assert "route" in str(exc_info.value)

    def test_route_reasoning_is_required(self) -> None:
        """Test route_reasoning must be provided."""
        with pytest.raises(ValidationError) as exc_info:
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                confidence=0.8,
                route="auto",
                # route_reasoning omitted - should fail
            )
        assert "route_reasoning" in str(exc_info.value)

    def test_confidence_validation_too_high(self) -> None:
        """Test confidence must be <= 1.0."""
        with pytest.raises(ValidationError):
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                confidence=1.5,
                route="auto",
                route_reasoning="Test",
            )

    def test_confidence_validation_too_low(self) -> None:
        """Test confidence must be >= 0.0."""
        with pytest.raises(ValidationError):
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                confidence=-0.1,
                route="manual",
                route_reasoning="Test",
            )

    def test_route_must_be_auto_or_manual(self) -> None:
        """Test route must be 'auto' or 'manual'."""
        with pytest.raises(ValidationError):
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="old",
                replace_text="new",
                justification="Test",
                confidence=0.8,
                route="invalid",  # type: ignore
                route_reasoning="Test",
            )

    def test_empty_observation_ids_allowed(self) -> None:
        """Test empty observation_ids is allowed (per model definition)."""
        # This might fail validation in real usage, but model allows it
        draft = ProposalDraft(
            observation_ids=[],
            search_text="old",
            replace_text="new",
            justification="Test",
            confidence=0.8,
            route="manual",
            route_reasoning="No observations linked",
        )
        assert draft.observation_ids == []


@pytest.mark.unit
class TestConflictRecord:
    """Tests for ConflictRecord model."""

    def test_valid_conflict_record(self) -> None:
        """Test creating a valid ConflictRecord."""
        conflict = ConflictRecord(
            observation_ids=["obs-1", "obs-2"],
            conflict_type="contradictory_classification",
            description="Image classified as both decorative and informative",
            page_num=3,
            element="image 1 (university logo)",
        )
        assert len(conflict.observation_ids) == 2
        assert conflict.conflict_type == "contradictory_classification"
        assert conflict.page_num == 3

    def test_conflict_record_required_fields(self) -> None:
        """Test ConflictRecord with only required fields."""
        conflict = ConflictRecord(
            observation_ids=["obs-1"],
            conflict_type="ambiguous_element",
            description="Could not determine element type",
            page_num=1,
        )
        # element has default
        assert conflict.element == ""

    def test_page_num_must_be_positive(self) -> None:
        """Test page_num must be >= 1."""
        with pytest.raises(ValidationError):
            ConflictRecord(
                observation_ids=["obs-1"],
                conflict_type="test",
                description="test",
                page_num=0,  # Must be >= 1
            )


@pytest.mark.unit
class TestConsolidationOutput:
    """Tests for ConsolidationOutput model."""

    def test_valid_output(self) -> None:
        """Test creating a valid ConsolidationOutput."""
        output = ConsolidationOutput(
            proposals=[
                ProposalDraft(
                    observation_ids=["obs-1"],
                    search_text="# Old",
                    replace_text="# New",
                    justification="Fix heading",
                    confidence=0.85,
                    route="manual",
                    route_reasoning="Structural heading change needs review",
                )
            ],
            manual_observations=["obs-2"],
            conflicts=[
                ConflictRecord(
                    observation_ids=["obs-3", "obs-4"],
                    conflict_type="contradictory_classification",
                    description="Conflict between recommendations",
                    page_num=2,
                )
            ],
            notes="Some observations were conflicting",
        )
        assert len(output.proposals) == 1
        assert len(output.manual_observations) == 1
        assert len(output.conflicts) == 1

    def test_output_defaults(self) -> None:
        """Test ConsolidationOutput default values."""
        output = ConsolidationOutput()
        assert output.proposals == []
        assert output.manual_observations == []
        assert output.conflicts == []
        assert output.notes == ""


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestConsolidationAgentInit:
    """Tests for ConsolidationAgent initialization."""

    def test_agent_uses_reasoning_tier(self) -> None:
        """Test agent uses REASONING (Sonnet) model tier."""
        agent = ConsolidationAgent()
        assert agent.model_tier == ModelTier.REASONING
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.REASONING]
        assert "sonnet" in agent.model_id.lower()

    def test_agent_has_prompts(self) -> None:
        """Test agent has prompts (from file or defaults)."""
        agent = ConsolidationAgent()
        assert "system_prompt" in agent.prompts
        # Should have consolidation-related content
        prompt = agent.prompts["system_prompt"].lower()
        assert "consolidat" in prompt or "observation" in prompt

    def test_agent_lazy_initialization(self) -> None:
        """Test agent does not create PydanticAI agent until needed."""
        agent = ConsolidationAgent()
        # _agent should be None until first use (stored in _core)
        assert agent._core._agent is None

    def test_auto_route_threshold_in_settings(self) -> None:
        """Test confidence threshold is available via settings."""
        from src.config import settings
        # Threshold is centralized in settings, not a class constant
        assert 0.0 <= settings.min_confidence_for_auto_approval <= 1.0

    def test_max_markdown_length(self) -> None:
        """Test agent has max markdown length constant."""
        agent = ConsolidationAgent()
        assert agent.MAX_MARKDOWN_LENGTH == 8000


# =============================================================================
# Observation Formatting Tests
# =============================================================================


@pytest.mark.unit
class TestObservationFormatting:
    """Tests for formatting observations for the prompt."""

    @pytest.fixture
    def sample_observations(self) -> list[Observation]:
        """Create sample observations for testing."""
        return [
            Observation(
                id="obs-1",
                job_id="job-123",
                agent="figures",
                visual_description="Image shows a flowchart",
                markup_description="Image has empty alt text",
                location=ObservationLocation(
                    location_type="element",
                    value="![](image-page-1-1.png)",
                    page_num=1,
                ),
                confidence=0.9,
                severity="major",
                route="auto",
            ),
            Observation(
                id="obs-2",
                job_id="job-123",
                agent="structure",
                visual_description="Visually large heading",
                markup_description="Marked as H3, should be H2",
                location=ObservationLocation(
                    location_type="element",
                    value="### Section Title",
                    page_num=2,
                ),
                confidence=0.7,
                severity="major",
                route="auto",
            ),
        ]

    def test_format_observations(self, sample_observations: list[Observation]) -> None:
        """Test formatting observations for prompt."""
        agent = ConsolidationAgent()
        formatted = agent._format_observations(sample_observations)

        # Should include observation details
        assert "obs-1" in formatted
        assert "obs-2" in formatted
        assert "figures" in formatted
        assert "structure" in formatted
        # Uses XML tag format: <page number="1" ...>
        assert '<page number="1"' in formatted
        assert '<page number="2"' in formatted

    def test_format_observations_groups_by_page(
        self, sample_observations: list[Observation]
    ) -> None:
        """Test observations are grouped by page with XML tags."""
        agent = ConsolidationAgent()
        formatted = agent._format_observations(sample_observations)

        # Page sections should appear in order using XML tag format
        page1_idx = formatted.index('<page number="1"')
        page2_idx = formatted.index('<page number="2"')
        assert page1_idx < page2_idx

    def test_format_observations_includes_manual_reason(self) -> None:
        """Test manual_reason is included when present."""
        agent = ConsolidationAgent()
        obs = Observation(
            id="obs-1",
            job_id="job-123",
            agent="figures",
            visual_description="Ambiguous image",
            markup_description="Image classification unclear",
            location=ObservationLocation(
                location_type="element",
                value="![](image.png)",
                page_num=1,
            ),
            confidence=0.4,
            severity="major",
            route="manual",
            manual_reason="Low confidence in image classification",
        )
        formatted = agent._format_observations([obs])

        assert "Manual Reason" in formatted
        assert "Low confidence" in formatted


# =============================================================================
# Markdown Truncation Tests
# =============================================================================


@pytest.mark.unit
class TestMarkdownTruncation:
    """Tests for markdown truncation."""

    def test_short_markdown_unchanged(self) -> None:
        """Test short markdown is not truncated."""
        agent = ConsolidationAgent()
        markdown = "# Hello World\n\nShort content."
        result = agent._truncate_markdown(markdown)
        assert result == markdown

    def test_long_markdown_truncated(self) -> None:
        """Test long markdown is truncated with indicator."""
        agent = ConsolidationAgent()
        markdown = "x" * 10000
        result = agent._truncate_markdown(markdown)

        assert len(result) < len(markdown)
        assert "[... TRUNCATED ...]" in result
        assert result.startswith("x" * 100)


# =============================================================================
# Proposal Validation Tests
# =============================================================================


@pytest.mark.unit
class TestProposalValidation:
    """Tests for proposal validation and conversion."""

    def test_valid_proposal_converted(self) -> None:
        """Test valid proposal draft is converted to Proposal."""
        agent = ConsolidationAgent()
        markdown = "# Hello World\n\nThis is content."

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Hello World",
                replace_text="Hi There",
                justification="Test change",
                page_nums=[1],
                confidence=0.99,  # Use 0.99 to exceed any reasonable threshold
                route="auto",
                route_reasoning="High confidence fix",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 1
        assert proposals[0].job_id == "test-job"
        assert proposals[0].resolves == ["obs-1"]
        assert proposals[0].diff.search == "Hello World"
        assert proposals[0].diff.replace == "Hi There"
        assert proposals[0].route == "auto"  # Confidence 0.99 exceeds threshold
        assert proposals[0].status == "pending"

    def test_invalid_search_text_skipped(self) -> None:
        """Test proposals with invalid search text are skipped."""
        agent = ConsolidationAgent()
        markdown = "# Hello World"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Not Found",  # Doesn't exist
                replace_text="Replacement",
                justification="Test",
                confidence=0.9,
                route="auto",
                route_reasoning="Test",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 0

    def test_non_unique_search_text_skipped(self) -> None:
        """Test proposals with non-unique search text are skipped."""
        agent = ConsolidationAgent()
        markdown = "Hello Hello Hello"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Hello",  # Matches 3 times
                replace_text="Hi",
                justification="Test",
                confidence=0.9,
                route="auto",
                route_reasoning="Test",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 0

    def test_low_confidence_overrides_route_to_manual(self) -> None:
        """Test low confidence proposals override route to manual."""
        agent = ConsolidationAgent()
        markdown = "# Hello World"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Hello World",
                replace_text="Hi There",
                justification="Test",
                confidence=0.5,  # Below threshold
                route="auto",  # LLM said auto, but confidence too low
                route_reasoning="LLM routed auto, but should be overridden",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 1
        assert proposals[0].route == "manual"  # Overridden due to low confidence

    def test_manual_route_respected(self) -> None:
        """Test manual route from LLM is respected even with high confidence."""
        agent = ConsolidationAgent()
        markdown = "# Hello World"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Hello World",
                replace_text="Hi There",
                justification="Structural change",
                confidence=0.95,  # High confidence
                route="manual",  # But LLM says manual (structural change)
                route_reasoning="Heading change affects document structure",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 1
        assert proposals[0].route == "manual"  # Respected

    def test_boundary_confidence_routes_auto(self) -> None:
        """Test confidence exactly at threshold routes to auto when LLM says auto."""
        from src.config import settings
        agent = ConsolidationAgent()
        markdown = "# Hello World"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1"],
                search_text="Hello World",
                replace_text="Hi There",
                justification="Test",
                confidence=settings.min_confidence_for_auto_approval,  # Exactly at threshold
                route="auto",
                route_reasoning="At threshold",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 1
        assert proposals[0].route == "auto"

    def test_grouping_rationale_included_in_justification(self) -> None:
        """Test grouping rationale is added to justification for multi-observation proposals."""
        agent = ConsolidationAgent()
        markdown = "# Hello World"

        drafts = [
            ProposalDraft(
                observation_ids=["obs-1", "obs-2"],
                search_text="Hello World",
                replace_text="Hi There",
                justification="Fixes both observations",
                grouping_rationale="Both observations concern the same heading element",
                confidence=0.85,
                route="auto",
                route_reasoning="Simple fix",
            )
        ]

        proposals = agent._validate_and_convert_proposals(
            drafts=drafts,
            markdown=markdown,
            job_id="test-job",
        )

        assert len(proposals) == 1
        assert "Grouping:" in proposals[0].justification
        assert "Both observations concern the same heading element" in proposals[0].justification


# =============================================================================
# Full Consolidation Tests (with mocked LLM)
# =============================================================================


@pytest.mark.unit
class TestConsolidationAgentConsolidate:
    """Tests for full consolidation workflow."""

    @pytest.fixture
    def sample_observations(self) -> list[Observation]:
        """Create sample observations for testing."""
        return [
            Observation(
                id="obs-1",
                job_id="job-123",
                agent="figures",
                visual_description="Image shows a flowchart",
                markup_description="Image has empty alt text",
                location=ObservationLocation(
                    location_type="element",
                    value="![](image-page-1-1.png)",
                    page_num=1,
                ),
                confidence=0.9,
                severity="major",
                route="auto",
                status="open",
            ),
        ]

    @pytest.mark.asyncio
    async def test_empty_observations_returns_empty(self) -> None:
        """Test consolidating empty observations returns empty results."""
        agent = ConsolidationAgent()

        proposals, manual_ids, usage = await agent.consolidate(
            observations=[],
            markdown="# Test",
            job_id="test-job",
        )

        assert proposals == []
        assert manual_ids == []
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    @pytest.mark.asyncio
    async def test_non_open_observations_filtered(self) -> None:
        """Test only open observations are consolidated."""
        agent = ConsolidationAgent()

        closed_obs = Observation(
            id="obs-1",
            job_id="job-123",
            agent="figures",
            visual_description="Test",
            markup_description="Test",
            location=ObservationLocation(
                location_type="element",
                value="test",
                page_num=1,
            ),
            status="resolved",  # Not open
        )

        proposals, manual_ids, usage = await agent.consolidate(
            observations=[closed_obs],
            markdown="# Test",
            job_id="test-job",
        )

        assert proposals == []
        assert manual_ids == []

    @pytest.mark.asyncio
    async def test_consolidate_calls_agent(
        self, sample_observations: list[Observation]
    ) -> None:
        """Test consolidate calls the PydanticAI agent."""
        agent = ConsolidationAgent()

        # Create mock output with all required fields
        mock_output = ConsolidationOutput(
            proposals=[
                ProposalDraft(
                    observation_ids=["obs-1"],
                    search_text="![](image-page-1-1.png)",
                    replace_text="![Flowchart](image-page-1-1.png)",
                    justification="Adding alt text to flowchart image",
                    confidence=0.9,
                    route="auto",
                    route_reasoning="High confidence, simple image fix",
                )
            ],
            manual_observations=[],
            conflicts=[],
        )

        # Mock the result
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_usage = MagicMock()
        mock_usage.request_tokens = 1000
        mock_usage.response_tokens = 200
        mock_result.usage.return_value = mock_usage

        # Mock agent creation and run
        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        markdown = "# Test\n\n![](image-page-1-1.png)\n\nContent"

        with patch.object(agent, '_get_agent', return_value=mock_agent):
            proposals, manual_ids, usage = await agent.consolidate(
                observations=sample_observations,
                markdown=markdown,
                job_id="test-job",
            )

            # Should have called the agent
            mock_agent.run.assert_called_once()

            # Should have created valid proposals
            assert len(proposals) == 1
            assert proposals[0].diff.search == "![](image-page-1-1.png)"

    @pytest.mark.asyncio
    async def test_consolidate_returns_usage_metrics(
        self, sample_observations: list[Observation]
    ) -> None:
        """Test consolidate returns LLM usage metrics."""
        agent = ConsolidationAgent()

        mock_output = ConsolidationOutput(proposals=[], manual_observations=[], conflicts=[])
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_usage = MagicMock()
        mock_usage.request_tokens = 500
        mock_usage.response_tokens = 100
        mock_result.usage.return_value = mock_usage

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch.object(agent, '_get_agent', return_value=mock_agent):
            _, _, usage = await agent.consolidate(
                observations=sample_observations,
                markdown="# Test",
                job_id="test-job",
            )

            assert usage.input_tokens == 500
            assert usage.output_tokens == 100
            assert usage.total_tokens == 600
            assert usage.estimated_cost_cents > 0

    @pytest.mark.asyncio
    async def test_consolidate_returns_manual_ids(
        self, sample_observations: list[Observation]
    ) -> None:
        """Test consolidate returns manual observation IDs."""
        agent = ConsolidationAgent()

        mock_output = ConsolidationOutput(
            proposals=[],
            manual_observations=["obs-1"],
            conflicts=[],
        )
        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_result.usage.return_value = mock_usage

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=mock_result)

        with patch.object(agent, '_get_agent', return_value=mock_agent):
            _, manual_ids, _ = await agent.consolidate(
                observations=sample_observations,
                markdown="# Test",
                job_id="test-job",
            )

            assert manual_ids == ["obs-1"]
