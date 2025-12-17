"""Chained consolidation agents.

This package contains the chained agents for observation consolidation:
1. grouping_agent - Observation grouping (Sonnet)
2. conflict_agent - Conflict detection (Haiku, parallel)
3. proposal_agent - Proposal generation (Sonnet, parallel)
4. routing - Pure Python routing logic

The chained_consolidation.py orchestrator coordinates these steps.
"""

from src.agents.consolidation.grouping_agent import (
    ObservationGroup,
    GroupingOutput,
    group_observations,
)
from src.agents.consolidation.conflict_agent import (
    ConflictReport,
    ConflictOutput,
    detect_conflicts,
)
from src.agents.consolidation.proposal_agent import (
    ProposalDraft,
    ProposalOutput,
    generate_proposals,
)
from src.agents.consolidation.routing import route_proposal

__all__ = [
    # Grouping
    "ObservationGroup",
    "GroupingOutput",
    "group_observations",
    # Conflict
    "ConflictReport",
    "ConflictOutput",
    "detect_conflicts",
    # Proposal
    "ProposalDraft",
    "ProposalOutput",
    "generate_proposals",
    # Routing
    "route_proposal",
]
