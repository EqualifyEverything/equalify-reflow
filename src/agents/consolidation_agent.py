"""ConsolidationAgent for transforming observations into proposals.

The consolidation phase is the bridge between AI analysis and human review:
1. Groups related observations (same region → one proposal)
2. Generates search-replace diffs (exact text operations)
3. Writes justifications (explains grouping rationale)
4. Routes to appropriate queue (high-confidence → auto, low-confidence → manual)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.model_tiers import ModelTier
from src.config import settings
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage
from src.shared.models.proposal import Proposal, SearchReplaceDiff
from src.utils.diff_utils import validate_search_replace

logger = logging.getLogger(__name__)


class ProposalDraft(BaseModel):
    """Draft proposal from consolidation agent.

    The agent generates these drafts which are then validated and converted
    to full Proposal objects.
    """

    observation_ids: list[str] = Field(
        ...,
        description="IDs of observations this proposal resolves",
    )
    search_text: str = Field(
        ...,
        description="Exact text to find in markdown (must be unique)",
    )
    replace_text: str = Field(
        ...,
        description="Text to substitute",
    )
    justification: str = Field(
        ...,
        description="Why these observations are grouped and how edit addresses them",
    )
    page_nums: list[int] = Field(
        default_factory=list,
        description="Pages affected by this proposal",
    )
    estimated_impact: str = Field(
        default="",
        description="Human-readable impact summary (e.g., 'Adds alt text to 1 image')",
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in this proposal (0.0-1.0)",
    )


class ConsolidationOutput(BaseModel):
    """Structured output from consolidation agent."""

    proposals: list[ProposalDraft] = Field(
        default_factory=list,
        description="List of proposal drafts to create",
    )
    manual_observations: list[str] = Field(
        default_factory=list,
        description="Observation IDs that need manual handling (cannot be auto-consolidated)",
    )
    conflicts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Conflicting observation groups that need human resolution",
    )
    notes: str = Field(
        default="",
        description="Additional notes about the consolidation process",
    )


class ConsolidationAgent(BaseDocumentAgent[ConsolidationOutput]):
    """Agent that consolidates observations into proposals.

    This agent:
    1. Groups related observations by location and type
    2. Generates minimal search-replace diffs
    3. Writes justifications for human reviewers
    4. Identifies conflicts and manual items

    Uses Sonnet 4.5 (REASONING tier) for complex reasoning about
    groupings and edit generation.

    Example:
        >>> agent = ConsolidationAgent()
        >>> proposals, manual_ids = await agent.consolidate(
        ...     observations=observations,
        ...     markdown=markdown,
        ...     job_id="job-123",
        ... )
    """

    # Maximum markdown length to include in prompt (to manage tokens)
    MAX_MARKDOWN_LENGTH = 8000

    def __init__(self, prompts_file: Path | None = None) -> None:
        """Initialize ConsolidationAgent.

        Args:
            prompts_file: Optional path to YAML prompts file (uses default if not provided)
        """
        config = AgentConfig(
            name="consolidation_agent",
            prompts_file=prompts_file or Path("consolidation.yaml"),
            output_type=ConsolidationOutput,
            model_tier=ModelTier.REASONING,
            max_retries=2,
            temperature=0.3,
            max_tokens=16000,
        )
        super().__init__(config)
        logger.info(
            f"ConsolidationAgent initialized with model tier {self.model_tier.value} "
            f"({self.model_id})"
        )

    async def process(self, input_data: Any) -> ConsolidationOutput:
        """Process method required by BaseDocumentAgent.

        For ConsolidationAgent, use consolidate() method instead.
        """
        raise NotImplementedError("ConsolidationAgent uses consolidate() method, not process()")

    async def consolidate(
        self,
        observations: list[Observation],
        markdown: str,
        job_id: str,
    ) -> tuple[list[Proposal], list[str], LLMUsage]:
        """Consolidate observations into proposals.

        Args:
            observations: List of observations from all agents
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            Tuple of (proposals, manual_observation_ids, llm_usage)
        """
        # Filter to open observations only
        open_observations = [o for o in observations if o.status == "open"]

        if not open_observations:
            logger.info(f"Job {job_id}: No open observations to consolidate")
            return [], [], LLMUsage(
                input_tokens=0, output_tokens=0, total_tokens=0, estimated_cost_cents=0.0
            )

        logger.info(
            f"Job {job_id}: Consolidating {len(open_observations)} open observations"
        )

        # Build user message with observations and markdown
        obs_summary = self._format_observations(open_observations)
        markdown_excerpt = self._truncate_markdown(markdown)

        user_message = (
            f"Consolidate these {len(open_observations)} observations into proposals:\n\n"
            f"{obs_summary}\n\n"
            f"Current markdown ({len(markdown)} chars):\n```markdown\n{markdown_excerpt}\n```\n\n"
            f"Generate proposals with exact search-replace diffs."
        )

        # Execute agent (using inherited _get_agent)
        agent = self._get_agent()

        result = await agent.run(
            user_message,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )

        output = result.output

        # Use inherited usage tracking
        llm_usage = self._core.create_llm_usage(result)

        logger.debug(
            f"Job {job_id}: Consolidation agent returned {len(output.proposals)} drafts, "
            f"{len(output.manual_observations)} manual, {len(output.conflicts)} conflicts"
        )

        # Convert drafts to validated Proposal objects
        proposals = self._validate_and_convert_proposals(
            drafts=output.proposals,
            markdown=markdown,
            job_id=job_id,
        )

        # Collect manual observation IDs
        manual_ids = list(output.manual_observations)

        logger.info(
            f"Job {job_id}: Consolidation complete - "
            f"{len(proposals)} valid proposals, {len(manual_ids)} manual, "
            f"cost: ${llm_usage.estimated_cost_cents/100:.4f}"
        )

        return proposals, manual_ids, llm_usage

    def _format_observations(self, observations: list[Observation]) -> str:
        """Format observations for the consolidation prompt.

        Groups observations by page for clearer context.
        """
        lines: list[str] = []

        # Group by page for organization
        obs_by_page: dict[int, list[Observation]] = {}
        for obs in observations:
            page = obs.location.page_num
            if page not in obs_by_page:
                obs_by_page[page] = []
            obs_by_page[page].append(obs)

        for page_num in sorted(obs_by_page.keys()):
            page_obs = obs_by_page[page_num]
            lines.append(f"<page number=\"{page_num}\" observations=\"{len(page_obs)}\">")

            for obs in page_obs:
                lines.append(f"""
OBSERVATION {obs.id}:
  Agent: {obs.agent}
  Location: {obs.location.value}
  Visual: {obs.visual_description}
  Markup: {obs.markup_description}
  Severity: {obs.severity}
  Confidence: {obs.confidence}
  Route: {obs.route}""")
                if obs.manual_reason:
                    lines.append(f"  Manual Reason: {obs.manual_reason}")

            lines.append("</page>")
            lines.append("")

        return "\n".join(lines)

    def _truncate_markdown(self, markdown: str) -> str:
        """Truncate markdown if too long for prompt."""
        if len(markdown) <= self.MAX_MARKDOWN_LENGTH:
            return markdown

        # Truncate with indicator
        return markdown[: self.MAX_MARKDOWN_LENGTH] + "\n\n[... TRUNCATED ...]"

    def _validate_and_convert_proposals(
        self,
        drafts: list[ProposalDraft],
        markdown: str,
        job_id: str,
    ) -> list[Proposal]:
        """Validate draft proposals and convert to full Proposal objects.

        Filters out drafts with invalid search text (not found or not unique).
        """
        proposals: list[Proposal] = []

        for draft in drafts:
            # Validate search text exists and is unique
            valid, error = validate_search_replace(
                markdown, draft.search_text, draft.replace_text
            )

            if not valid:
                logger.warning(
                    f"Job {job_id}: Skipping invalid proposal - {error} "
                    f"(search: {draft.search_text[:50]}...)"
                )
                continue

            # Determine route based on confidence
            route: Literal["auto", "manual"] = (
                "auto" if draft.confidence >= settings.min_confidence_for_auto_approval else "manual"
            )

            proposal = Proposal(
                id=str(uuid.uuid4()),
                job_id=job_id,
                resolves=draft.observation_ids,
                diff=SearchReplaceDiff(
                    search=draft.search_text,
                    replace=draft.replace_text,
                ),
                justification=draft.justification,
                page_nums=draft.page_nums,
                estimated_impact=draft.estimated_impact,
                route=route,
                status="pending",
            )
            proposals.append(proposal)

        return proposals


__all__ = [
    "ConsolidationAgent",
    "ConsolidationOutput",
    "ProposalDraft",
]
