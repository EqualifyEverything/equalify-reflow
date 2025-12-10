"""Tests for AgentRouter (PRD-014).

Tests cover:
- Agent registration and retrieval
- Page filtering based on manifest features
- Required agent routing
- Observation collection from multiple agents
- Error handling for missing/failing agents
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from src.agents.agent_router import AgentRouter
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest, PageFeatures

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_manifest() -> DocumentManifest:
    """Create a sample DocumentManifest for testing."""
    return DocumentManifest(
        job_id="test-job-123",
        document_title="Test Document",
        document_type="syllabus",
        total_pages=5,
        heading_tree_json='{"document_title": "Test", "sections": []}',
        page_features=[
            PageFeatures(
                page_num=1,
                has_images=True,
                image_count=2,
                has_tables=False,
                complexity_score=0.3,
            ),
            PageFeatures(
                page_num=2,
                has_images=False,
                has_tables=True,
                table_count=1,
                complexity_score=0.4,
            ),
            PageFeatures(
                page_num=3,
                has_images=True,
                image_count=1,
                has_tables=True,
                table_count=2,
                complexity_score=0.7,
            ),
            PageFeatures(
                page_num=4,
                has_images=False,
                has_tables=False,
                complexity_score=0.2,
            ),
            PageFeatures(
                page_num=5,
                has_images=False,
                has_tables=False,
                complexity_score=0.8,
            ),
        ],
        required_agents=["figures", "tables", "structure", "typography"],
        analysis_confidence=0.9,
    )


@pytest.fixture
def sample_pages() -> list[PageData]:
    """Create sample page data for testing."""
    return [
        PageData(page_num=1, image_base64="base64data1"),
        PageData(page_num=2, image_base64="base64data2"),
        PageData(page_num=3, image_base64="base64data3"),
        PageData(page_num=4, image_base64="base64data4"),
        PageData(page_num=5, image_base64="base64data5"),
    ]


@pytest.fixture
def mock_observation() -> Observation:
    """Create a sample observation for testing."""
    return Observation(
        id="obs-123",
        job_id="test-job-123",
        agent="figures",
        visual_description="Test image description",
        markup_description="Image has empty alt",
        location=ObservationLocation(
            location_type="element",
            value="![](image.png)",
            page_num=1,
        ),
        confidence=0.9,
        severity="major",
    )


# =============================================================================
# Agent Registration Tests
# =============================================================================


@pytest.mark.unit
class TestAgentRegistration:
    """Tests for agent registration functionality."""

    def test_register_single_agent(self):
        """Test registering a single agent."""
        router = AgentRouter()
        mock_agent = MagicMock()

        router.register_agent("figures", mock_agent)

        assert "figures" in router.registered_agents
        assert router.get_agent("figures") is mock_agent

    def test_register_multiple_agents(self):
        """Test registering multiple agents."""
        router = AgentRouter()

        router.register_agent("figures", MagicMock())
        router.register_agent("tables", MagicMock())
        router.register_agent("structure", MagicMock())

        assert len(router.registered_agents) == 3
        assert "figures" in router.registered_agents
        assert "tables" in router.registered_agents
        assert "structure" in router.registered_agents

    def test_get_unregistered_agent_returns_none(self):
        """Test getting an unregistered agent returns None."""
        router = AgentRouter()
        assert router.get_agent("nonexistent") is None

    def test_register_overwrites_existing(self):
        """Test registering same name overwrites existing agent."""
        router = AgentRouter()
        agent1 = MagicMock()
        agent2 = MagicMock()

        router.register_agent("figures", agent1)
        router.register_agent("figures", agent2)

        assert router.get_agent("figures") is agent2


# =============================================================================
# Page Filtering Tests
# =============================================================================


@pytest.mark.unit
class TestPageFiltering:
    """Tests for page filtering based on manifest features."""

    def test_figures_agent_gets_pages_with_images(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test figures agent only processes pages with images."""
        router = AgentRouter()

        relevant = router._get_relevant_pages("figures", sample_manifest, sample_pages)

        page_nums = [p.page_num for p in relevant]
        assert page_nums == [1, 3]  # Pages with has_images=True

    def test_tables_agent_gets_pages_with_tables(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test tables agent only processes pages with tables."""
        router = AgentRouter()

        relevant = router._get_relevant_pages("tables", sample_manifest, sample_pages)

        page_nums = [p.page_num for p in relevant]
        assert page_nums == [2, 3]  # Pages with has_tables=True

    def test_structure_agent_gets_all_pages(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test structure agent processes all pages."""
        router = AgentRouter()

        relevant = router._get_relevant_pages("structure", sample_manifest, sample_pages)

        assert len(relevant) == 5  # All pages

    def test_typography_agent_gets_complex_pages(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test typography agent only processes pages with complexity > 0.5."""
        router = AgentRouter()

        relevant = router._get_relevant_pages("typography", sample_manifest, sample_pages)

        page_nums = [p.page_num for p in relevant]
        assert page_nums == [3, 5]  # Pages with complexity_score > 0.5

    def test_unknown_agent_gets_no_pages(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test unknown agent type gets no pages."""
        router = AgentRouter()

        relevant = router._get_relevant_pages("unknown", sample_manifest, sample_pages)

        assert len(relevant) == 0


# =============================================================================
# Agent Routing Tests
# =============================================================================


@pytest.mark.unit
class TestAgentRouting:
    """Tests for running required agents."""

    @pytest.mark.asyncio
    async def test_runs_all_required_agents(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_observation: Observation,
    ):
        """Test all required agents are called."""
        router = AgentRouter()

        # Create mock agents
        mock_figures = AsyncMock()
        mock_figures.analyze.return_value = [mock_observation]

        mock_tables = AsyncMock()
        mock_tables.analyze.return_value = []

        mock_structure = AsyncMock()
        mock_structure.analyze.return_value = []

        mock_typography = AsyncMock()
        mock_typography.analyze.return_value = []

        router.register_agent("figures", mock_figures)
        router.register_agent("tables", mock_tables)
        router.register_agent("structure", mock_structure)
        router.register_agent("typography", mock_typography)

        # Run agents
        observations = await router.run_required_agents(
            manifest=sample_manifest,
            pages=sample_pages,
            markdown="# Test\n\nContent",
            job_id="test-job-123",
        )

        # All agents should have been called
        mock_figures.analyze.assert_called_once()
        mock_tables.analyze.assert_called_once()
        mock_structure.analyze.assert_called_once()
        mock_typography.analyze.assert_called_once()

        # Should have collected the observation
        assert len(observations) == 1
        assert observations[0].id == "obs-123"

    @pytest.mark.asyncio
    async def test_skips_unregistered_agents(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test unregistered agents are skipped with warning."""
        router = AgentRouter()

        # Only register figures agent
        mock_figures = AsyncMock()
        mock_figures.analyze.return_value = []
        router.register_agent("figures", mock_figures)

        # Run with manifest requiring all agents
        observations = await router.run_required_agents(
            manifest=sample_manifest,
            pages=sample_pages,
            markdown="# Test",
            job_id="test-job-123",
        )

        # Should complete without error
        assert observations == []
        mock_figures.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_agents_with_no_relevant_pages(
        self, sample_pages: list[PageData]
    ):
        """Test agents are skipped if no relevant pages."""
        # Manifest with no images or tables
        manifest = DocumentManifest(
            job_id="test-job",
            total_pages=2,
            heading_tree_json="{}",
            page_features=[
                PageFeatures(page_num=1, has_images=False, has_tables=False, complexity_score=0.3),
                PageFeatures(page_num=2, has_images=False, has_tables=False, complexity_score=0.2),
            ],
            required_agents=["figures", "tables"],
        )

        router = AgentRouter()

        mock_figures = AsyncMock()
        mock_tables = AsyncMock()
        router.register_agent("figures", mock_figures)
        router.register_agent("tables", mock_tables)

        observations = await router.run_required_agents(
            manifest=manifest,
            pages=sample_pages[:2],
            markdown="# Test",
            job_id="test-job",
        )

        # Neither should be called since no relevant pages
        mock_figures.analyze.assert_not_called()
        mock_tables.analyze.assert_not_called()
        assert observations == []

    @pytest.mark.asyncio
    async def test_continues_on_agent_failure(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_observation: Observation,
    ):
        """Test router continues if one agent fails."""
        router = AgentRouter()

        # Figures agent fails
        mock_figures = AsyncMock()
        mock_figures.analyze.side_effect = Exception("Agent failed")

        # Tables agent succeeds
        mock_tables = AsyncMock()
        mock_tables.analyze.return_value = [mock_observation]

        router.register_agent("figures", mock_figures)
        router.register_agent("tables", mock_tables)

        # Modify manifest to only require these two
        sample_manifest.required_agents = ["figures", "tables"]

        observations = await router.run_required_agents(
            manifest=sample_manifest,
            pages=sample_pages,
            markdown="# Test",
            job_id="test-job-123",
        )

        # Should have observation from tables despite figures failure
        assert len(observations) == 1

    @pytest.mark.asyncio
    async def test_collects_observations_from_all_agents(
        self, sample_manifest: DocumentManifest, sample_pages: list[PageData]
    ):
        """Test observations from all agents are collected."""
        router = AgentRouter()

        obs1 = Observation(
            id="obs-1", job_id="test", agent="figures",
            visual_description="Image 1", markup_description="Alt empty",
            location=ObservationLocation(location_type="element", value="img1", page_num=1),
        )
        obs2 = Observation(
            id="obs-2", job_id="test", agent="tables",
            visual_description="Table 1", markup_description="No headers",
            location=ObservationLocation(location_type="region", value="table1", page_num=2),
        )
        obs3 = Observation(
            id="obs-3", job_id="test", agent="structure",
            visual_description="Heading skip", markup_description="H1 -> H3",
            location=ObservationLocation(location_type="element", value="h3", page_num=1),
        )

        mock_figures = AsyncMock()
        mock_figures.analyze.return_value = [obs1]

        mock_tables = AsyncMock()
        mock_tables.analyze.return_value = [obs2]

        mock_structure = AsyncMock()
        mock_structure.analyze.return_value = [obs3]

        mock_typography = AsyncMock()
        mock_typography.analyze.return_value = []

        router.register_agent("figures", mock_figures)
        router.register_agent("tables", mock_tables)
        router.register_agent("structure", mock_structure)
        router.register_agent("typography", mock_typography)

        observations = await router.run_required_agents(
            manifest=sample_manifest,
            pages=sample_pages,
            markdown="# Test",
            job_id="test",
        )

        assert len(observations) == 3
        agent_names = {obs.agent for obs in observations}
        assert agent_names == {"figures", "tables", "structure"}


# =============================================================================
# Edge Cases
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_required_agents(self, sample_pages: list[PageData]):
        """Test handling empty required_agents list."""
        manifest = DocumentManifest(
            job_id="test",
            total_pages=1,
            heading_tree_json="{}",
            page_features=[PageFeatures(page_num=1)],
            required_agents=[],  # No agents required
        )

        router = AgentRouter()
        router.register_agent("figures", AsyncMock())

        observations = await router.run_required_agents(
            manifest=manifest,
            pages=sample_pages[:1],
            markdown="# Test",
            job_id="test",
        )

        assert observations == []

    @pytest.mark.asyncio
    async def test_empty_pages_list(self, sample_manifest: DocumentManifest):
        """Test handling empty pages list."""
        router = AgentRouter()

        mock_agent = AsyncMock()
        router.register_agent("figures", mock_agent)

        sample_manifest.required_agents = ["figures"]

        observations = await router.run_required_agents(
            manifest=sample_manifest,
            pages=[],
            markdown="# Test",
            job_id="test",
        )

        # Agent should not be called with empty pages
        assert observations == []

    def test_page_features_helper(self, sample_manifest: DocumentManifest):
        """Test _get_page_features helper method."""
        router = AgentRouter()

        # Get existing page
        features = router._get_page_features(sample_manifest, 3)
        assert features is not None
        assert features.page_num == 3
        assert features.has_images is True
        assert features.has_tables is True

        # Get non-existing page
        features = router._get_page_features(sample_manifest, 99)
        assert features is None
