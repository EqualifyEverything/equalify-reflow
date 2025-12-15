"""Service for consolidating observations into proposals.

The ConsolidationService wraps the ConsolidationAgent and handles:
- Loading observations from storage
- Running the consolidation agent
- Updating observation statuses
- Saving proposals to storage
- Supporting re-consolidation for human edits
"""

import logging
from typing import TYPE_CHECKING

from src.agents.consolidation_agent import ConsolidationAgent
from src.shared.models.agent_models import LLMUsage
from src.shared.models.observation import Observation
from src.shared.models.proposal import Proposal

if TYPE_CHECKING:
    from src.services.remediation_storage_service import RemediationStorageService

logger = logging.getLogger(__name__)


class ConsolidationService:
    """Service for consolidating observations into proposals.

    This service:
    1. Loads observations from storage
    2. Runs the ConsolidationAgent
    3. Updates observation statuses for manual items
    4. Saves proposals to storage
    5. Supports re-consolidation for human edits

    Example:
        >>> storage = RemediationStorageService(s3_service)
        >>> service = ConsolidationService(storage)
        >>> proposals, auto_count, manual_count, usage = await service.consolidate_observations(
        ...     job_id="job-123",
        ...     markdown="# Document content...",
        ... )
    """

    def __init__(
        self,
        storage: "RemediationStorageService",
        consolidation_agent: ConsolidationAgent | None = None,
    ) -> None:
        """Initialize ConsolidationService.

        Args:
            storage: RemediationStorageService for loading/saving artifacts
            consolidation_agent: Optional agent instance (created if not provided)
        """
        self.storage = storage
        self.agent = consolidation_agent or ConsolidationAgent()

    async def consolidate_observations(
        self,
        job_id: str,
        markdown: str,
    ) -> tuple[list[Proposal], int, int, LLMUsage]:
        """Consolidate all observations for a job into proposals.

        Loads observations from storage, runs consolidation, updates statuses,
        and saves proposals.

        Args:
            job_id: Job identifier
            markdown: Current markdown content

        Returns:
            Tuple of (proposals, auto_count, manual_count, llm_usage)
        """
        # Load observations
        observations = await self.storage.load_observations(job_id)

        if not observations:
            logger.info(f"Job {job_id}: No observations to consolidate")
            return [], 0, 0, LLMUsage(
                input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0
            )

        logger.info(
            f"Job {job_id}: Consolidating {len(observations)} observations"
        )

        # Run consolidation
        proposals, manual_ids, usage = await self.agent.consolidate(
            observations=observations,
            markdown=markdown,
            job_id=job_id,
        )

        # Update observation statuses for manual items
        updated_observations = self._update_manual_statuses(observations, manual_ids)

        # Save updated observations
        await self.storage.save_observations(job_id, updated_observations)

        # Save proposals
        if proposals:
            await self.storage.save_proposals(job_id, proposals)

        # Count by route
        auto_count = sum(1 for p in proposals if p.route == "auto")
        manual_count = sum(1 for p in proposals if p.route == "manual")

        logger.info(
            f"Job {job_id}: Generated {len(proposals)} proposals "
            f"({auto_count} auto, {manual_count} manual), "
            f"{len(manual_ids)} observations flagged for manual handling"
        )

        return proposals, auto_count, manual_count, usage

    async def reconsolidate_observation(
        self,
        job_id: str,
        observation: Observation,
        markdown: str,
    ) -> Proposal | None:
        """Re-consolidate a single observation into a proposal.

        Used when a human submits a new observation via the edit dialog
        without providing the "after" text. The agent generates the
        proposal from the observation.

        Args:
            job_id: Job identifier
            observation: Single observation to consolidate
            markdown: Current markdown

        Returns:
            Generated proposal or None if failed
        """
        logger.info(
            f"Job {job_id}: Re-consolidating observation {observation.id}"
        )

        proposals, _, _ = await self.agent.consolidate(
            observations=[observation],
            markdown=markdown,
            job_id=job_id,
        )

        if proposals:
            # Add to existing proposals (create new list to avoid mutating cached data)
            existing = await self.storage.load_proposals(job_id)
            all_proposals = existing + proposals
            await self.storage.save_proposals(job_id, all_proposals)

            logger.info(
                f"Job {job_id}: Re-consolidation created proposal {proposals[0].id}"
            )
            return proposals[0]

        logger.warning(
            f"Job {job_id}: Re-consolidation failed for observation {observation.id}"
        )
        return None

    def _update_manual_statuses(
        self,
        observations: list[Observation],
        manual_ids: list[str],
    ) -> list[Observation]:
        """Update observation statuses for manual items.

        Observations that the agent flagged for manual handling get their
        status updated to 'manual'.

        Args:
            observations: All observations
            manual_ids: IDs flagged for manual handling

        Returns:
            Updated observation list
        """
        manual_id_set = set(manual_ids)

        for obs in observations:
            if obs.id in manual_id_set and obs.status == "open":
                obs.status = "manual"
                logger.debug(
                    f"Observation {obs.id} status updated to 'manual'"
                )

        return observations


__all__ = ["ConsolidationService"]
