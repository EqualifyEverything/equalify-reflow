"""Agent router for specialized analysis agents (PRD-014).

Routes specialized agents based on DocumentManifest.required_agents and
filters pages by content type to ensure efficient processing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation
from src.shared.models.remediation import DocumentManifest, PageFeatures

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SpecializedAgent(Protocol):
    """Protocol for specialized analysis agents.

    All specialized agents must implement the analyze method that takes
    page data and document context to produce observations.
    """

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Analyze pages and generate observations.

        Args:
            pages: List of page images to analyze
            manifest: Document manifest with structure info
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            List of observations from this agent
        """
        ...


class AgentRouter:
    """Routes specialized agents based on document manifest.

    The router:
    1. Determines which agents to run based on manifest.required_agents
    2. Filters pages to only those relevant for each agent
    3. Collects and aggregates observations from all agents
    """

    def __init__(self) -> None:
        """Initialize the agent router."""
        self._agents: dict[str, Any] = {}
        logger.debug("AgentRouter initialized")

    def register_agent(self, name: str, agent: SpecializedAgent) -> None:
        """Register an agent by name.

        Args:
            name: Agent identifier (figures, tables, structure, typography)
            agent: Agent instance implementing SpecializedAgent protocol
        """
        self._agents[name] = agent
        logger.debug(f"Registered agent: {name}")

    def get_agent(self, name: str) -> SpecializedAgent | None:
        """Get a registered agent by name.

        Args:
            name: Agent identifier

        Returns:
            Agent instance or None if not registered
        """
        return self._agents.get(name)

    @property
    def registered_agents(self) -> list[str]:
        """Get list of registered agent names."""
        return list(self._agents.keys())

    async def run_required_agents(
        self,
        manifest: DocumentManifest,
        pages: list[PageData],
        markdown: str,
        job_id: str,
    ) -> list[Observation]:
        """Run all required agents and collect observations.

        Routes agents based on manifest.required_agents and filters pages
        to only process those relevant for each agent type.

        Args:
            manifest: DocumentManifest with required_agents list
            pages: All page images from PDF conversion
            markdown: Current markdown content
            job_id: Job identifier for observations

        Returns:
            Combined list of observations from all agents
        """
        all_observations: list[Observation] = []

        logger.info(
            f"Job {job_id}: Running specialized agents. "
            f"Required: {manifest.required_agents}, "
            f"Registered: {self.registered_agents}"
        )

        for agent_name in manifest.required_agents:
            if agent_name not in self._agents:
                logger.warning(
                    f"Job {job_id}: Agent '{agent_name}' required but not registered, skipping"
                )
                continue

            agent = self._agents[agent_name]

            # Determine which pages this agent should process
            relevant_pages = self._get_relevant_pages(agent_name, manifest, pages)

            if not relevant_pages:
                logger.info(
                    f"Job {job_id}: Agent '{agent_name}' has no relevant pages, skipping"
                )
                continue

            logger.info(
                f"Job {job_id}: Agent '{agent_name}' processing {len(relevant_pages)} pages"
            )

            try:
                # Run agent on relevant pages
                observations = await agent.analyze(
                    pages=relevant_pages,
                    manifest=manifest,
                    markdown=markdown,
                    job_id=job_id,
                )

                all_observations.extend(observations)

                logger.info(
                    f"Job {job_id}: Agent '{agent_name}' generated {len(observations)} observations"
                )
            except Exception as e:
                logger.error(
                    f"Job {job_id}: Agent '{agent_name}' failed: {e}",
                    exc_info=True,
                )
                # Continue with other agents even if one fails

        logger.info(
            f"Job {job_id}: Specialized analysis complete. "
            f"Total observations: {len(all_observations)}"
        )

        return all_observations

    def _get_relevant_pages(
        self,
        agent_name: str,
        manifest: DocumentManifest,
        pages: list[PageData],
    ) -> list[PageData]:
        """Filter pages relevant to a specific agent.

        Each agent type processes different subsets of pages based on
        PageFeatures from the manifest:
        - figures: Pages with has_images=True
        - tables: Pages with has_tables=True
        - structure: All pages (validates full document structure)
        - typography: Pages with complexity_score > 0.5

        Args:
            agent_name: Agent identifier
            manifest: Document manifest with page features
            pages: All page images

        Returns:
            Filtered list of pages for this agent
        """
        relevant_page_nums: set[int] = set()

        for pf in manifest.page_features:
            if agent_name == "figures" and pf.has_images:
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "tables" and pf.has_tables:
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "structure":
                # Structure agent processes all pages to validate hierarchy
                relevant_page_nums.add(pf.page_num)
            elif agent_name == "typography" and pf.complexity_score > 0.5:
                # Typography agent focuses on complex pages
                relevant_page_nums.add(pf.page_num)

        # Filter pages to only those with relevant content
        filtered = [p for p in pages if p.page_num in relevant_page_nums]

        logger.debug(
            f"Agent '{agent_name}': {len(filtered)}/{len(pages)} pages relevant "
            f"(page nums: {sorted(relevant_page_nums)})"
        )

        return filtered

    def _get_page_features(
        self,
        manifest: DocumentManifest,
        page_num: int,
    ) -> PageFeatures | None:
        """Get PageFeatures for a specific page number.

        Args:
            manifest: Document manifest
            page_num: Page number to look up

        Returns:
            PageFeatures for the page or None if not found
        """
        for pf in manifest.page_features:
            if pf.page_num == page_num:
                return pf
        return None


__all__ = [
    "AgentRouter",
    "SpecializedAgent",
]
