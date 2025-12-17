"""Conflict detection agent for chained consolidation.

Step 2 of the consolidation chain: Detect conflicts within groups.
Uses Haiku (EFFICIENT) - simple contradiction analysis.

PARALLELIZED: Each group is analyzed independently.

Detects:
- Contradictory recommendations
- Overlapping fixes
- Mutually exclusive actions
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.agents.consolidation.grouping_agent import ObservationGroup
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, ConflictOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for simple analysis


# =============================================================================
# Output Models
# =============================================================================


ConflictType = Literal["contradiction", "overlap", "mutual_exclusion"]


class ConflictReport(BaseModel):
    """Conflict report for a single group."""

    group_id: str = Field(description="Group ID this report is for")
    has_conflict: bool = Field(description="Whether a conflict was detected")
    conflict_type: ConflictType | None = Field(
        default=None, description="Type of conflict if any"
    )
    conflicting_observation_ids: list[str] = Field(
        default_factory=list, description="IDs of conflicting observations"
    )
    description: str = Field(
        default="", description="Description of the conflict"
    )
    resolution_suggestion: str = Field(
        default="", description="Suggested way to resolve the conflict"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, default=0.9, description="Confidence in conflict detection"
    )


class ConflictOutput(BaseModel):
    """Output from conflict detection for a single group."""

    report: ConflictReport


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load conflict agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("consolidation_conflict.yaml")
    return _prompts


def get_agent() -> Agent[None, ConflictOutput]:
    """Get or create the conflict agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="consolidation_conflict.yaml",
            output_type=ConflictOutput,
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


async def detect_conflicts_for_group(
    group: ObservationGroup,
    observations: list[Observation],
    job_id: str,
) -> tuple[ConflictReport, LLMUsage]:
    """Detect conflicts within a single observation group.

    Args:
        group: The observation group
        observations: All observations (for lookup)
        job_id: Job ID for correlation

    Returns:
        Tuple of (conflict report, usage metrics)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Get observations for this group
    obs_by_id = {obs.id: obs for obs in observations}
    group_observations = [obs_by_id[oid] for oid in group.observation_ids if oid in obs_by_id]

    if len(group_observations) < 2:
        # Single observation can't have conflicts
        return (
            ConflictReport(
                group_id=group.group_id,
                has_conflict=False,
                confidence=1.0,
            ),
            LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            ),
        )

    # Format observations for prompt
    obs_context = "\n".join(
        f"- [{obs.id[:8]}] {obs.agent}: {obs.markup_description}"
        for obs in group_observations
    )

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        group_id=group.group_id,
        grouping_rationale=group.grouping_rationale,
        observation_count=len(group_observations),
        observation_context=obs_context,
    )

    result = await run_agent(
        agent=agent,
        prompt=user_prompt,
        job_id=job_id,
        agent_name="consolidation_conflict",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: ConflictOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    return output.report, usage


async def detect_conflicts(
    groups: list[ObservationGroup],
    observations: list[Observation],
    job_id: str,
    max_concurrent: int = 5,
) -> tuple[list[ConflictReport], LLMUsage]:
    """Detect conflicts for all groups in parallel.

    Args:
        groups: All observation groups
        observations: All observations
        job_id: Job ID for correlation
        max_concurrent: Maximum concurrent tasks

    Returns:
        Tuple of (conflict reports, aggregated usage)
    """
    if not groups:
        return [], LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0,
        )

    logger.debug(
        f"Job {job_id}: Detecting conflicts in {len(groups)} groups "
        f"(max concurrent: {max_concurrent})"
    )

    # Use semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    async def detect_with_semaphore(group: ObservationGroup) -> tuple[ConflictReport, LLMUsage]:
        async with semaphore:
            return await detect_conflicts_for_group(group, observations, job_id)

    # Run all in parallel
    results = await asyncio.gather(*[detect_with_semaphore(g) for g in groups])

    # Separate reports and usages
    reports = [r[0] for r in results]
    usages = [r[1] for r in results]

    # Aggregate usage
    total_usage = LLMUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        total_tokens=sum(u.total_tokens for u in usages),
        estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
    )

    conflicts_found = sum(1 for r in reports if r.has_conflict)
    logger.debug(
        f"Job {job_id}: Conflict detection complete - "
        f"{conflicts_found}/{len(groups)} groups have conflicts, "
        f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
    )

    return reports, total_usage


__all__ = [
    "ConflictOutput",
    "ConflictReport",
    "ConflictType",
    "detect_conflicts",
    "detect_conflicts_for_group",
    "get_agent",
    "reset_agent",
]
