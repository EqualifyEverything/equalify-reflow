"""Pure Python routing logic for consolidation.

Step 4 of the consolidation chain: Determine proposal routing.
This is pure Python - NO LLM calls, zero cost.

Routing rules:
- confidence >= 0.75 → auto
- structural_change → manual
- multiple_observations → manual (unless high confidence)
- conflict detected → manual
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.agents.consolidation.proposal_agent import ProposalDraft
from src.agents.consolidation.conflict_agent import ConflictReport
from src.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Routing Types
# =============================================================================


Route = Literal["auto", "manual"]


@dataclass
class RoutedProposal:
    """A proposal with routing decision."""

    proposal: ProposalDraft
    route: Route
    manual_reason: str | None
    final_confidence: float


# =============================================================================
# Routing Logic (Pure Python)
# =============================================================================


def route_proposal(
    proposal: ProposalDraft,
    conflict: ConflictReport | None,
) -> RoutedProposal:
    """Route a single proposal based on rules.

    This is pure Python logic - no LLM calls, zero cost.

    Args:
        proposal: The proposal draft
        conflict: Conflict report for this group (if any)

    Returns:
        RoutedProposal with routing decision
    """
    confidence = proposal.confidence
    min_confidence = settings.min_confidence_for_auto_approval

    # Rule 1: Conflicts always go to manual
    if conflict and conflict.has_conflict:
        return RoutedProposal(
            proposal=proposal,
            route="manual",
            manual_reason=f"Unresolved conflict: {conflict.conflict_type}",
            final_confidence=min(confidence, conflict.confidence),
        )

    # Rule 2: Structural changes go to manual
    if proposal.is_structural:
        return RoutedProposal(
            proposal=proposal,
            route="manual",
            manual_reason="Structural change requires human review",
            final_confidence=confidence,
        )

    # Rule 3: Multiple observations need higher confidence
    if len(proposal.observation_ids) > 2:
        # Require higher confidence for complex proposals
        adjusted_threshold = min(0.85, min_confidence + 0.1)
        if confidence < adjusted_threshold:
            return RoutedProposal(
                proposal=proposal,
                route="manual",
                manual_reason=f"Multi-observation proposal needs higher confidence ({confidence:.2f} < {adjusted_threshold:.2f})",
                final_confidence=confidence,
            )

    # Rule 4: Low confidence goes to manual
    if confidence < min_confidence:
        return RoutedProposal(
            proposal=proposal,
            route="manual",
            manual_reason=f"Low confidence ({confidence:.2f} < {min_confidence})",
            final_confidence=confidence,
        )

    # All checks passed - auto route
    return RoutedProposal(
        proposal=proposal,
        route="auto",
        manual_reason=None,
        final_confidence=confidence,
    )


def route_all_proposals(
    proposals: list[ProposalDraft],
    conflicts: list[ConflictReport],
) -> list[RoutedProposal]:
    """Route all proposals.

    Args:
        proposals: All proposal drafts
        conflicts: All conflict reports

    Returns:
        List of routed proposals
    """
    # Build conflict lookup
    conflict_by_group = {c.group_id: c for c in conflicts}

    results = []
    for proposal in proposals:
        conflict = conflict_by_group.get(proposal.group_id)
        routed = route_proposal(proposal, conflict)
        results.append(routed)

    auto_count = sum(1 for r in results if r.route == "auto")
    manual_count = sum(1 for r in results if r.route == "manual")
    logger.debug(
        f"Routed {len(results)} proposals: {auto_count} auto, {manual_count} manual"
    )

    return results


__all__ = [
    "Route",
    "RoutedProposal",
    "route_all_proposals",
    "route_proposal",
]
