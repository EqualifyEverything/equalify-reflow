"""Proposal generation agent for chained consolidation.

Step 3 of the consolidation chain: Generate proposals for groups.
Uses Sonnet (REASONING) - complex diff generation requires reasoning.

PARALLELIZED: Each group generates proposals independently.

Generates:
- Search/replace diffs
- Justifications
- Confidence scores
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.agents.consolidation.grouping_agent import ObservationGroup
from src.agents.consolidation.conflict_agent import ConflictReport
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, ProposalOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku - testing cost reduction


# =============================================================================
# Output Models
# =============================================================================


class ProposalDraft(BaseModel):
    """A draft proposal for a group."""

    group_id: str = Field(description="Group ID this proposal is for")
    search_text: str = Field(
        min_length=1, description="Exact text to search for in markdown"
    )
    replace_text: str = Field(
        description="Text to replace with (may be empty for deletion)"
    )
    justification: str = Field(
        description="Why this change is appropriate"
    )
    observation_ids: list[str] = Field(
        description="IDs of observations resolved by this proposal"
    )
    page_nums: list[int] = Field(
        description="Page numbers affected"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this proposal"
    )
    is_structural: bool = Field(
        default=False, description="Whether this is a structural change"
    )


class ProposalOutput(BaseModel):
    """Output from proposal generation for a single group."""

    proposals: list[ProposalDraft] = Field(default_factory=list)
    skipped: bool = Field(
        default=False, description="Whether generation was skipped (conflict)"
    )
    skip_reason: str = Field(
        default="", description="Reason for skipping if applicable"
    )


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load proposal agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("consolidation_proposal.yaml")
    return _prompts


def get_agent() -> Agent[None, ProposalOutput]:
    """Get or create the proposal agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="consolidation_proposal.yaml",
            output_type=ProposalOutput,
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


async def generate_proposals_for_group(
    group: ObservationGroup,
    conflict: ConflictReport | None,
    observations: list[Observation],
    markdown: str,
    job_id: str,
) -> tuple[ProposalOutput, LLMUsage]:
    """Generate proposals for a single observation group.

    Skips generation if group has unresolved conflicts.

    Args:
        group: The observation group
        conflict: Conflict report for this group (if any)
        observations: All observations (for lookup)
        markdown: Document markdown
        job_id: Job ID for correlation

    Returns:
        Tuple of (proposal output, usage metrics)
    """
    # Skip if group has conflicts
    if conflict and conflict.has_conflict:
        logger.debug(
            f"Job {job_id}: Skipping proposal generation for group {group.group_id} "
            f"due to conflict: {conflict.conflict_type}"
        )
        return (
            ProposalOutput(
                proposals=[],
                skipped=True,
                skip_reason=f"Conflict detected: {conflict.description}",
            ),
            LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            ),
        )

    agent = get_agent()
    prompts = get_prompts()

    # Get observations for this group
    obs_by_id = {obs.id: obs for obs in observations}
    group_observations = [obs_by_id[oid] for oid in group.observation_ids if oid in obs_by_id]

    # Format observations for prompt
    obs_context = "\n".join(
        f"- [{obs.id[:8]}] Page {obs.location.page_num}, {obs.agent}:\n"
        f"  Visual: {obs.visual_description}\n"
        f"  Markup: {obs.markup_description}\n"
        f"  Location: {obs.location.value}"
        for obs in group_observations
    )

    # Extract relevant markdown section
    page_nums = list(set(obs.location.page_num for obs in group_observations))
    markdown_section = _extract_relevant_markdown(markdown, page_nums)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        group_id=group.group_id,
        grouping_rationale=group.grouping_rationale,
        observation_count=len(group_observations),
        observation_context=obs_context,
        page_nums=page_nums,
        markdown_section=markdown_section,
    )

    result = await run_agent(
        agent=agent,
        prompt=user_prompt,
        job_id=job_id,
        agent_name="consolidation_proposal",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: ProposalOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    return output, usage


async def generate_proposals(
    groups: list[ObservationGroup],
    conflicts: list[ConflictReport],
    observations: list[Observation],
    markdown: str,
    job_id: str,
    max_concurrent: int = 3,
) -> tuple[list[ProposalOutput], LLMUsage]:
    """Generate proposals for all groups in parallel.

    Args:
        groups: All observation groups
        conflicts: Conflict reports for each group
        observations: All observations
        markdown: Document markdown
        job_id: Job ID for correlation
        max_concurrent: Maximum concurrent tasks (lower for Sonnet)

    Returns:
        Tuple of (proposal outputs, aggregated usage)
    """
    if not groups:
        return [], LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0,
        )

    # Build conflict lookup
    conflict_by_group = {c.group_id: c for c in conflicts}

    logger.debug(
        f"Job {job_id}: Generating proposals for {len(groups)} groups "
        f"(max concurrent: {max_concurrent})"
    )

    # Use semaphore to limit concurrency
    semaphore = asyncio.Semaphore(max_concurrent)

    async def generate_with_semaphore(
        group: ObservationGroup,
    ) -> tuple[ProposalOutput, LLMUsage]:
        async with semaphore:
            conflict = conflict_by_group.get(group.group_id)
            return await generate_proposals_for_group(
                group, conflict, observations, markdown, job_id
            )

    # Run all in parallel
    results = await asyncio.gather(*[generate_with_semaphore(g) for g in groups])

    # Separate outputs and usages
    outputs = [r[0] for r in results]
    usages = [r[1] for r in results]

    # Aggregate usage
    total_usage = LLMUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        total_tokens=sum(u.total_tokens for u in usages),
        estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
    )

    proposals_count = sum(len(o.proposals) for o in outputs)
    skipped_count = sum(1 for o in outputs if o.skipped)
    logger.debug(
        f"Job {job_id}: Proposal generation complete - "
        f"{proposals_count} proposals, {skipped_count} skipped, "
        f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
    )

    return outputs, total_usage


def _extract_relevant_markdown(markdown: str, page_nums: list[int]) -> str:
    """Extract markdown sections for relevant pages."""
    import re

    # Try to extract page sections using markers
    sections = []
    page_pattern = re.compile(r"<!-- Page (\d+) -->(.*?)(?=<!-- Page \d+ -->|$)", re.DOTALL)

    for match in page_pattern.finditer(markdown):
        page = int(match.group(1))
        if page in page_nums:
            sections.append(match.group(2).strip()[:2000])

    if sections:
        return "\n\n---\n\n".join(sections)

    # Fallback: return first portion of markdown
    return markdown[:4000]


__all__ = [
    "ProposalDraft",
    "ProposalOutput",
    "generate_proposals",
    "generate_proposals_for_group",
    "get_agent",
    "reset_agent",
]
