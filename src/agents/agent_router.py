"""Agent router for specialized analysis agents.

Routes specialized agents based on DocumentManifest.required_agents and
filters pages by content type to ensure efficient processing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from src.config import settings
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Result from running a single specialized agent."""
    agent_name: str
    observations: list[Observation]
    usage: LLMUsage | None
    error: Exception | None
    pages_processed: int

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class CombinedAgentUsage:
    """Combined usage metrics from all parallel agent runs."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_estimated_cost_cents: float = 0.0
    per_agent_usage: dict[str, LLMUsage] | None = None

    def __post_init__(self) -> None:
        if self.per_agent_usage is None:
            self.per_agent_usage = {}

    def add_usage(self, agent_name: str, usage: LLMUsage) -> None:
        if self.per_agent_usage is not None:
            self.per_agent_usage[agent_name] = usage
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens
        self.total_estimated_cost_cents += usage.estimated_cost_cents

    def to_llm_usage(self) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.total_input_tokens,
            output_tokens=self.total_output_tokens,
            total_tokens=self.total_tokens,
            estimated_cost_cents=self.total_estimated_cost_cents,
        )


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
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze pages and generate observations.

        Args:
            pages: List of page images to analyze
            manifest: Document manifest with structure info
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            Tuple of (observations from this agent, usage metrics)
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
    ) -> tuple[list[Observation], CombinedAgentUsage]:
        """Run all required agents concurrently and collect observations.

        Routes agents based on manifest.required_agents and filters pages
        to only process those relevant for each agent type. Agents run in
        parallel with configurable concurrency limit.

        Args:
            manifest: DocumentManifest with required_agents list
            pages: All page images from PDF conversion
            markdown: Current markdown content
            job_id: Job identifier for observations

        Returns:
            Tuple of (combined observations, combined usage metrics)
        """
        logger.info(
            f"Job {job_id}: Running specialized agents in parallel "
            f"(max concurrency: {settings.max_concurrent_agents}). "
            f"Required: {manifest.required_agents}, "
            f"Registered: {self.registered_agents}"
        )

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(settings.max_concurrent_agents)

        # Build tasks for all required agents
        tasks = []
        for agent_name in manifest.required_agents:
            if agent_name not in self._agents:
                logger.warning(
                    f"Job {job_id}: Agent '{agent_name}' required but not registered, skipping"
                )
                continue

            # Determine which pages this agent should process
            relevant_pages = self._get_relevant_pages(agent_name, manifest, pages)

            if not relevant_pages:
                logger.info(
                    f"Job {job_id}: Agent '{agent_name}' has no relevant pages, skipping"
                )
                continue

            logger.info(
                f"Job {job_id}: Agent '{agent_name}' queued to process {len(relevant_pages)} pages"
            )

            # Create task for this agent
            task = self._run_single_agent_with_semaphore(
                semaphore=semaphore,
                agent_name=agent_name,
                agent=self._agents[agent_name],
                relevant_pages=relevant_pages,
                manifest=manifest,
                markdown=markdown,
                job_id=job_id,
            )
            tasks.append(task)

        # Run all agents concurrently
        if not tasks:
            logger.info(f"Job {job_id}: No agents to run")
            return [], CombinedAgentUsage()

        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Combine results
        all_observations: list[Observation] = []
        combined_usage = CombinedAgentUsage()

        for result in results:
            if result.error:
                logger.error(
                    f"Job {job_id}: Agent '{result.agent_name}' failed: {result.error}",
                    exc_info=result.error,
                )
                # Continue processing other results
                continue

            all_observations.extend(result.observations)

            if result.usage:
                combined_usage.add_usage(result.agent_name, result.usage)

            cost = result.usage.estimated_cost_cents / 100 if result.usage else 0.0
            logger.info(
                f"Job {job_id}: Agent '{result.agent_name}' generated "
                f"{len(result.observations)} observations "
                f"(processed {result.pages_processed} pages, cost: ${cost:.4f})"
            )

        logger.info(
            f"Job {job_id}: Specialized analysis complete. "
            f"Total observations: {len(all_observations)}, "
            f"Total cost: ${combined_usage.total_estimated_cost_cents/100:.4f}"
        )

        return all_observations, combined_usage

    async def _run_single_agent_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        agent_name: str,
        agent: SpecializedAgent,
        relevant_pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> AgentRunResult:
        """Run a single agent with semaphore-based concurrency control.

        Args:
            semaphore: Semaphore for limiting concurrent agents
            agent_name: Name of the agent
            agent: Agent instance
            relevant_pages: Pages for this agent to process
            manifest: Document manifest
            markdown: Current markdown
            job_id: Job identifier

        Returns:
            AgentRunResult with observations and usage or error
        """
        async with semaphore:
            logger.info(
                f"Job {job_id}: Agent '{agent_name}' starting (processing {len(relevant_pages)} pages)"
            )
            try:
                observations, usage = await agent.analyze(
                    pages=relevant_pages,
                    manifest=manifest,
                    markdown=markdown,
                    job_id=job_id,
                )

                return AgentRunResult(
                    agent_name=agent_name,
                    observations=observations,
                    usage=usage,
                    error=None,
                    pages_processed=len(relevant_pages),
                )

            except Exception as e:
                logger.error(
                    f"Job {job_id}: Agent '{agent_name}' encountered error: {e}",
                    exc_info=True,
                )
                return AgentRunResult(
                    agent_name=agent_name,
                    observations=[],
                    usage=None,
                    error=e,
                    pages_processed=len(relevant_pages),
                )

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
