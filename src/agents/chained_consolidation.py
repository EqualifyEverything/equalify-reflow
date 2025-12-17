"""Chained consolidation orchestrator.

Orchestrates the multi-step consolidation pipeline:
1. Observation Grouping (Sonnet) - Semantic clustering
2. Conflict Detection (Haiku, PARALLEL) - Per-group contradiction analysis
3. Proposal Generation (Sonnet, PARALLEL) - Per-group diff generation
4. Routing (Pure Python) - Determine auto/manual routing

Cost/Speed improvements vs monolithic approach:
- Step 2 uses Haiku (3x cheaper) + parallelization
- Step 3 parallelizes Sonnet calls (5-10x speed improvement)
- Step 4 is pure Python (no LLM cost)
- Estimated: 35-50% cost reduction, 5-10x speed improvement
"""

from __future__ import annotations

import logging
from typing import Literal, cast
from uuid import uuid4

from opentelemetry import trace

from src.agents.factory import aggregate_usage, get_tracer
from src.agents.consolidation.grouping_agent import (
    GroupingOutput,
    ObservationGroup,
    group_observations,
)
from src.agents.consolidation.conflict_agent import (
    ConflictReport,
    detect_conflicts,
)
from src.agents.consolidation.proposal_agent import (
    ProposalDraft,
    ProposalOutput,
    generate_proposals,
)
from src.agents.consolidation.routing import (
    RoutedProposal,
    route_all_proposals,
)
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage
from src.shared.models.proposal import Proposal, SearchReplaceDiff

logger = logging.getLogger(__name__)


# =============================================================================
# Main Entry Point
# =============================================================================


async def consolidate_observations(
    observations: list[Observation],
    markdown: str,
    job_id: str,
    max_concurrent_conflicts: int = 5,
    max_concurrent_proposals: int = 3,
) -> tuple[list[Proposal], list[str], LLMUsage]:
    """Consolidate observations into proposals using chained pipeline.

    Pipeline steps:
    1. Grouping (Sonnet) - Semantic clustering
    2. Conflict Detection (Haiku, parallel) - Find contradictions
    3. Proposal Generation (Sonnet, parallel) - Generate diffs
    4. Routing (Pure Python) - Determine auto/manual

    Args:
        observations: All observations to consolidate
        markdown: Document markdown
        job_id: Job identifier
        max_concurrent_conflicts: Max parallel conflict detection tasks
        max_concurrent_proposals: Max parallel proposal generation tasks

    Returns:
        Tuple of (proposals, manual_observation_ids, combined usage)
    """
    tracer = get_tracer()
    usages: list[LLMUsage] = []

    with tracer.start_as_current_span(
        "chained_consolidation.consolidate_observations",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("observation_count", len(observations))

        if not observations:
            logger.info(f"Job {job_id}: No observations to consolidate")
            return [], [], LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            )

        logger.info(
            f"Job {job_id}: Starting chained consolidation of {len(observations)} observations"
        )

        # Step 1: Observation Grouping (Sonnet)
        with tracer.start_as_current_span("step.grouping"):
            grouping_output, grouping_usage = await group_observations(
                observations, markdown, job_id
            )
            usages.append(grouping_usage)
            span.set_attribute("groups_count", len(grouping_output.groups))

        # Step 2: Conflict Detection (Haiku, parallel)
        with tracer.start_as_current_span("step.conflict_detection"):
            conflict_reports, conflict_usage = await detect_conflicts(
                grouping_output.groups,
                observations,
                job_id,
                max_concurrent=max_concurrent_conflicts,
            )
            usages.append(conflict_usage)
            conflicts_found = sum(1 for r in conflict_reports if r.has_conflict)
            span.set_attribute("conflicts_found", conflicts_found)

        # Step 3: Proposal Generation (Sonnet, parallel)
        with tracer.start_as_current_span("step.proposal_generation"):
            proposal_outputs, proposal_usage = await generate_proposals(
                grouping_output.groups,
                conflict_reports,
                observations,
                markdown,
                job_id,
                max_concurrent=max_concurrent_proposals,
            )
            usages.append(proposal_usage)

        # Flatten proposals from all outputs
        all_proposal_drafts: list[ProposalDraft] = []
        for output in proposal_outputs:
            all_proposal_drafts.extend(output.proposals)

        span.set_attribute("proposals_generated", len(all_proposal_drafts))

        # Step 4: Routing (Pure Python - zero cost)
        with tracer.start_as_current_span("step.routing"):
            routed_proposals = route_all_proposals(all_proposal_drafts, conflict_reports)

        # Convert to final Proposal objects
        proposals = _create_final_proposals(routed_proposals, job_id)

        # Identify manual observations (from conflicts or low confidence)
        manual_observation_ids = _identify_manual_observations(
            grouping_output, conflict_reports, routed_proposals
        )

        # Aggregate usage
        total_usage = aggregate_usage(usages)

        # Record final metrics
        auto_count = sum(1 for p in proposals if p.route == "auto")
        manual_count = sum(1 for p in proposals if p.route == "manual")
        span.set_attribute("proposals_auto", auto_count)
        span.set_attribute("proposals_manual", manual_count)
        span.set_attribute("manual_observations", len(manual_observation_ids))
        span.set_attribute("tokens.total", total_usage.total_tokens)
        span.set_attribute("cost.cents", total_usage.estimated_cost_cents)

        logger.info(
            f"Job {job_id}: Chained consolidation complete - "
            f"{len(proposals)} proposals ({auto_count} auto, {manual_count} manual), "
            f"{len(manual_observation_ids)} manual observations, "
            f"cost: ${total_usage.estimated_cost_cents/100:.4f}"
        )

        return proposals, manual_observation_ids, total_usage


# =============================================================================
# Helpers
# =============================================================================


def _create_final_proposals(
    routed_proposals: list[RoutedProposal],
    job_id: str,
) -> list[Proposal]:
    """Convert routed proposals to final Proposal objects."""
    proposals = []

    for routed in routed_proposals:
        draft = routed.proposal

        # Build estimated impact summary
        obs_count = len(draft.observation_ids)
        impact = f"Resolves {obs_count} observation{'s' if obs_count > 1 else ''}"
        if routed.manual_reason:
            impact += f" (needs review: {routed.manual_reason})"

        proposal = Proposal(
            id=str(uuid4()),
            job_id=job_id,
            resolves=draft.observation_ids,
            diff=SearchReplaceDiff(
                search=draft.search_text,
                replace=draft.replace_text,
            ),
            justification=draft.justification,
            page_nums=draft.page_nums,
            estimated_impact=impact,
            route=cast(Literal["auto", "manual"], routed.route),
            confidence=routed.final_confidence,
        )
        proposals.append(proposal)

    return proposals


def _identify_manual_observations(
    grouping: GroupingOutput,
    conflicts: list[ConflictReport],
    routed_proposals: list[RoutedProposal],
) -> list[str]:
    """Identify observation IDs that need manual review.

    Manual observations include:
    - Ungrouped observations
    - Observations in conflicting groups
    - Observations in manually-routed proposals
    """
    manual_ids: set[str] = set()

    # Ungrouped observations
    manual_ids.update(grouping.ungrouped_ids)

    # Build conflict lookup
    conflict_by_group = {c.group_id: c for c in conflicts}

    # Check each group
    group_by_id = {g.group_id: g for g in grouping.groups}

    for conflict in conflicts:
        if conflict.has_conflict:
            group = group_by_id.get(conflict.group_id)
            if group:
                manual_ids.update(group.observation_ids)

    # Check routed proposals
    for routed in routed_proposals:
        if routed.route == "manual":
            manual_ids.update(routed.proposal.observation_ids)

    return list(manual_ids)


__all__ = [
    "consolidate_observations",
]
