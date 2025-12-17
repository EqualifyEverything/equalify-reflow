"""Observation grouping agent for chained consolidation.

Step 1 of the consolidation chain: Group related observations.
Uses Sonnet (REASONING) - complex semantic clustering requires reasoning.

Groups observations by:
- Location proximity (same page, nearby elements)
- Semantic similarity (related issues)
- Resolution compatibility (can be fixed with single edit)
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, GroupingOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku - testing cost reduction


# =============================================================================
# Output Models
# =============================================================================


class ObservationGroup(BaseModel):
    """A group of related observations."""

    group_id: str = Field(description="Unique identifier for this group")
    observation_ids: list[str] = Field(
        min_length=1, description="IDs of observations in this group"
    )
    grouping_rationale: str = Field(
        description="Why these observations are grouped together"
    )
    primary_page: int = Field(ge=1, description="Primary page number for this group")
    group_type: str = Field(
        description="Type of grouping: location, semantic, or resolution"
    )
    estimated_complexity: str = Field(
        description="Estimated resolution complexity: simple, moderate, complex"
    )


class GroupingOutput(BaseModel):
    """Output from observation grouping."""

    groups: list[ObservationGroup] = Field(default_factory=list)
    ungrouped_ids: list[str] = Field(
        default_factory=list,
        description="Observation IDs that couldn't be grouped",
    )
    notes: str = Field(default="", description="Grouping notes")


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load grouping agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("consolidation_grouping.yaml")
    return _prompts


def get_agent() -> Agent[None, GroupingOutput]:
    """Get or create the grouping agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="consolidation_grouping.yaml",
            output_type=GroupingOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


def reset_agent() -> None:
    """Reset the agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


async def group_observations(
    observations: list[Observation],
    markdown: str,
    job_id: str,
) -> tuple[GroupingOutput, LLMUsage]:
    """Group related observations for efficient consolidation.

    Args:
        observations: All observations to group
        markdown: Document markdown for context
        job_id: Job ID for correlation

    Returns:
        Tuple of (grouping output, usage metrics)
    """
    if not observations:
        return (
            GroupingOutput(groups=[], ungrouped_ids=[], notes="No observations to group"),
            LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            ),
        )

    agent = get_agent()
    prompts = get_prompts()

    # Format observations for prompt
    obs_context = _format_observations(observations)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        observation_count=len(observations),
        observation_context=obs_context,
        markdown_preview=markdown[:3000],
    )

    logger.debug(f"Job {job_id}: Grouping {len(observations)} observations")

    result = await run_agent(
        agent=agent,
        prompt=user_prompt,
        job_id=job_id,
        agent_name="consolidation_grouping",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: GroupingOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Grouped into {len(output.groups)} groups, "
        f"{len(output.ungrouped_ids)} ungrouped, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


def _format_observations(observations: list[Observation]) -> str:
    """Format observations for the grouping prompt."""
    lines = []
    for obs in observations:
        lines.append(
            f"- [{obs.id[:8]}] Page {obs.location.page_num}, {obs.agent}: "
            f"{obs.markup_description[:100]}"
        )
    return "\n".join(lines)


__all__ = [
    "GroupingOutput",
    "ObservationGroup",
    "get_agent",
    "group_observations",
    "reset_agent",
]
