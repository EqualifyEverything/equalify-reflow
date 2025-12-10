"""Unit tests for ConsolidationService (PRD-015).

Tests cover:
- consolidate_observations workflow
- reconsolidate_observation for human edits
- Manual status updates
- Storage integration
- Error handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agents.consolidation_agent import ConsolidationAgent
from src.services.consolidation_service import ConsolidationService
from src.services.remediation_storage_service import RemediationStorageService
from src.shared.models.agent_models import LLMUsage
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.proposal import Proposal, SearchReplaceDiff

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage() -> MagicMock:
    """Create a mock RemediationStorageService."""
    storage = MagicMock(spec=RemediationStorageService)
    storage.load_observations = AsyncMock(return_value=[])
    storage.save_observations = AsyncMock()
    storage.load_proposals = AsyncMock(return_value=[])
    storage.save_proposals = AsyncMock()
    return storage


@pytest.fixture
def mock_agent() -> MagicMock:
    """Create a mock ConsolidationAgent."""
    agent = MagicMock(spec=ConsolidationAgent)
    agent.consolidate = AsyncMock(return_value=(
        [],  # proposals
        [],  # manual_ids
        LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0),
    ))
    return agent


@pytest.fixture
def service(mock_storage: MagicMock, mock_agent: MagicMock) -> ConsolidationService:
    """Create a ConsolidationService with mocked dependencies."""
    return ConsolidationService(storage=mock_storage, consolidation_agent=mock_agent)


@pytest.fixture
def sample_observations() -> list[Observation]:
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
            status="open",
        ),
    ]


@pytest.fixture
def sample_proposals() -> list[Proposal]:
    """Create sample proposals for testing."""
    return [
        Proposal(
            id="prop-1",
            job_id="job-123",
            resolves=["obs-1"],
            diff=SearchReplaceDiff(
                search="![](image-page-1-1.png)",
                replace="![Flowchart description](image-page-1-1.png)",
            ),
            justification="Adding alt text to image",
            page_nums=[1],
            route="auto",
            status="pending",
        ),
        Proposal(
            id="prop-2",
            job_id="job-123",
            resolves=["obs-2"],
            diff=SearchReplaceDiff(
                search="### Section Title",
                replace="## Section Title",
            ),
            justification="Fixing heading level",
            page_nums=[2],
            route="manual",
            status="pending",
        ),
    ]


# =============================================================================
# consolidate_observations Tests
# =============================================================================


@pytest.mark.unit
class TestConsolidateObservations:
    """Tests for consolidate_observations method."""

    @pytest.mark.asyncio
    async def test_no_observations_returns_empty(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
    ):
        """Test consolidating with no observations returns empty results."""
        mock_storage.load_observations.return_value = []

        proposals, auto_count, manual_count, usage = await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        assert proposals == []
        assert auto_count == 0
        assert manual_count == 0
        assert usage.input_tokens == 0

        # Should not call agent or save if no observations
        mock_storage.load_observations.assert_called_once_with("job-123")

    @pytest.mark.asyncio
    async def test_loads_observations_from_storage(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
    ):
        """Test observations are loaded from storage."""
        mock_storage.load_observations.return_value = sample_observations

        await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        mock_storage.load_observations.assert_called_once_with("job-123")
        mock_agent.consolidate.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_agent_with_observations(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
    ):
        """Test agent is called with observations and markdown."""
        mock_storage.load_observations.return_value = sample_observations

        await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test Content",
        )

        mock_agent.consolidate.assert_called_once_with(
            observations=sample_observations,
            markdown="# Test Content",
            job_id="job-123",
        )

    @pytest.mark.asyncio
    async def test_saves_proposals_to_storage(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
        sample_proposals: list[Proposal],
    ):
        """Test proposals are saved to storage."""
        mock_storage.load_observations.return_value = sample_observations
        mock_agent.consolidate.return_value = (
            sample_proposals,
            [],
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        mock_storage.save_proposals.assert_called_once_with("job-123", sample_proposals)

    @pytest.mark.asyncio
    async def test_saves_updated_observations(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
    ):
        """Test updated observations are saved to storage."""
        mock_storage.load_observations.return_value = sample_observations
        mock_agent.consolidate.return_value = (
            [],
            ["obs-2"],  # obs-2 flagged for manual
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        # Should have saved observations
        mock_storage.save_observations.assert_called_once()
        saved_obs = mock_storage.save_observations.call_args[0][1]

        # obs-2 should be marked as manual
        obs_2 = next(o for o in saved_obs if o.id == "obs-2")
        assert obs_2.status == "manual"

    @pytest.mark.asyncio
    async def test_returns_correct_counts(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
        sample_proposals: list[Proposal],
    ):
        """Test correct auto/manual counts are returned."""
        mock_storage.load_observations.return_value = sample_observations
        mock_agent.consolidate.return_value = (
            sample_proposals,  # 1 auto, 1 manual
            [],
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        proposals, auto_count, manual_count, usage = await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        assert len(proposals) == 2
        assert auto_count == 1
        assert manual_count == 1

    @pytest.mark.asyncio
    async def test_returns_usage_metrics(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
    ):
        """Test LLM usage metrics are returned."""
        mock_storage.load_observations.return_value = sample_observations
        expected_usage = LLMUsage(
            input_tokens=500,
            output_tokens=200,
            total_tokens=700,
            estimated_cost_cents=10.5,
        )
        mock_agent.consolidate.return_value = ([], [], expected_usage)

        _, _, _, usage = await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        assert usage.input_tokens == 500
        assert usage.output_tokens == 200
        assert usage.total_tokens == 700
        assert usage.estimated_cost_cents == 10.5

    @pytest.mark.asyncio
    async def test_does_not_save_proposals_if_none(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_observations: list[Observation],
    ):
        """Test proposals are not saved if empty."""
        mock_storage.load_observations.return_value = sample_observations
        mock_agent.consolidate.return_value = (
            [],  # No proposals
            [],
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        await service.consolidate_observations(
            job_id="job-123",
            markdown="# Test",
        )

        # Should not call save_proposals with empty list
        mock_storage.save_proposals.assert_not_called()


# =============================================================================
# reconsolidate_observation Tests
# =============================================================================


@pytest.mark.unit
class TestReconsolidateObservation:
    """Tests for reconsolidate_observation method."""

    @pytest.mark.asyncio
    async def test_calls_agent_with_single_observation(
        self,
        service: ConsolidationService,
        mock_agent: MagicMock,
    ):
        """Test agent is called with the single observation."""
        observation = Observation(
            id="obs-new",
            job_id="job-123",
            agent="human",
            source="human",
            visual_description="User noticed issue",
            markup_description="Needs fix",
            location=ObservationLocation(
                location_type="region",
                value="Paragraph 3",
                page_num=1,
            ),
            status="open",
        )

        mock_agent.consolidate.return_value = (
            [],
            [],
            LLMUsage(input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0),
        )

        await service.reconsolidate_observation(
            job_id="job-123",
            observation=observation,
            markdown="# Test",
        )

        mock_agent.consolidate.assert_called_once_with(
            observations=[observation],
            markdown="# Test",
            job_id="job-123",
        )

    @pytest.mark.asyncio
    async def test_appends_proposal_to_existing(
        self,
        service: ConsolidationService,
        mock_storage: MagicMock,
        mock_agent: MagicMock,
        sample_proposals: list[Proposal],
    ):
        """Test new proposal is appended to existing proposals."""
        observation = Observation(
            id="obs-new",
            job_id="job-123",
            agent="human",
            visual_description="User noticed issue",
            markup_description="Needs fix",
            location=ObservationLocation(
                location_type="region",
                value="Paragraph 3",
                page_num=1,
            ),
            status="open",
        )

        new_proposal = Proposal(
            id="prop-new",
            job_id="job-123",
            resolves=["obs-new"],
            diff=SearchReplaceDiff(search="old", replace="new"),
            justification="Human edit",
        )

        mock_storage.load_proposals.return_value = sample_proposals
        mock_agent.consolidate.return_value = (
            [new_proposal],
            [],
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        result = await service.reconsolidate_observation(
            job_id="job-123",
            observation=observation,
            markdown="# Test",
        )

        assert result == new_proposal

        # Should have saved all proposals (existing + new)
        mock_storage.save_proposals.assert_called_once()
        saved_proposals = mock_storage.save_proposals.call_args[0][1]
        assert len(saved_proposals) == len(sample_proposals) + 1

    @pytest.mark.asyncio
    async def test_returns_none_if_no_proposal_generated(
        self,
        service: ConsolidationService,
        mock_agent: MagicMock,
    ):
        """Test returns None if agent doesn't generate a proposal."""
        observation = Observation(
            id="obs-new",
            job_id="job-123",
            agent="human",
            visual_description="Unclear issue",
            markup_description="Unknown fix",
            location=ObservationLocation(
                location_type="region",
                value="Somewhere",
                page_num=1,
            ),
            status="open",
        )

        mock_agent.consolidate.return_value = (
            [],  # No proposals generated
            [],
            LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=5.0),
        )

        result = await service.reconsolidate_observation(
            job_id="job-123",
            observation=observation,
            markdown="# Test",
        )

        assert result is None


# =============================================================================
# Manual Status Update Tests
# =============================================================================


@pytest.mark.unit
class TestManualStatusUpdates:
    """Tests for _update_manual_statuses method."""

    def test_updates_manual_observations(
        self,
        service: ConsolidationService,
        sample_observations: list[Observation],
    ):
        """Test observations flagged for manual get status updated."""
        manual_ids = ["obs-2"]

        updated = service._update_manual_statuses(sample_observations, manual_ids)

        obs_1 = next(o for o in updated if o.id == "obs-1")
        obs_2 = next(o for o in updated if o.id == "obs-2")

        assert obs_1.status == "open"  # Not in manual list
        assert obs_2.status == "manual"  # In manual list

    def test_does_not_update_non_open_observations(
        self,
        service: ConsolidationService,
    ):
        """Test observations not in 'open' status are not updated."""
        observations = [
            Observation(
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
                status="resolved",  # Already resolved
            )
        ]

        updated = service._update_manual_statuses(observations, ["obs-1"])

        assert updated[0].status == "resolved"  # Still resolved, not manual

    def test_empty_manual_ids_no_changes(
        self,
        service: ConsolidationService,
        sample_observations: list[Observation],
    ):
        """Test empty manual_ids list doesn't change any statuses."""
        updated = service._update_manual_statuses(sample_observations, [])

        for obs in updated:
            assert obs.status == "open"


# =============================================================================
# Service Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestServiceInitialization:
    """Tests for ConsolidationService initialization."""

    def test_creates_default_agent(self, mock_storage: MagicMock):
        """Test service creates agent if not provided."""
        service = ConsolidationService(storage=mock_storage)

        assert service.agent is not None
        assert isinstance(service.agent, ConsolidationAgent)

    def test_uses_provided_agent(
        self, mock_storage: MagicMock, mock_agent: MagicMock
    ):
        """Test service uses provided agent."""
        service = ConsolidationService(
            storage=mock_storage,
            consolidation_agent=mock_agent,
        )

        assert service.agent is mock_agent
