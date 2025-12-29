# PRD-015: Consolidation Service

## Overview
**Epic**: Accessibility Remediation Pipeline
**Phase**: 4 - Remediation
**Estimated Effort**: 3 days
**Dependencies**: PRD-011 (Data Models), PRD-014 (Specialized Agents)
**Reference**: [Accessibility Remediation Pipeline](../../../docs/features/accessibility-remediation-pipeline.md)
**GitHub Issues**: [#23](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/23), [#24](https://github.com/EqualifyEverything/equalify-pdf-converter/issues/24)

## Problem Statement

After specialized agents generate observations, these raw discrepancies need to be transformed into actionable proposals that humans can review. The consolidation phase:

1. **Groups related observations** - Multiple observations about the same region become one proposal
2. **Generates search-replace diffs** - Exact text operations for applying changes
3. **Writes justifications** - Explains why observations are grouped and how the edit addresses them
4. **Routes to appropriate queue** - High-confidence to auto, low-confidence to manual

This is the bridge between AI analysis and human review.

## Success Criteria

- [ ] ConsolidationAgent groups related observations effectively
- [ ] Search-replace diffs are minimal and unique
- [ ] Justifications explain grouping rationale
- [ ] Low-confidence observations route to manual queue
- [ ] Conflicting observations flagged for human resolution
- [ ] Consolidation completes in <30 seconds typical

## Technical Requirements

### Consolidation Agent

```python
# src/agents/consolidation_agent.py

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.shared.models.observation import Observation
from src.shared.models.proposal import Proposal, SearchReplaceDiff


class ProposalDraft(BaseModel):
    """Draft proposal from consolidation agent."""

    observation_ids: list[str] = Field(
        ...,
        description="IDs of observations this proposal resolves"
    )
    search_text: str = Field(
        ...,
        description="Exact text to find in markdown"
    )
    replace_text: str = Field(
        ...,
        description="Text to substitute"
    )
    justification: str = Field(
        ...,
        description="Why these observations are grouped and how edit addresses them"
    )
    page_nums: list[int] = Field(
        default_factory=list,
        description="Pages affected"
    )
    estimated_impact: str = Field(
        default="",
        description="Human-readable impact summary"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0
    )


class ConsolidationOutput(BaseModel):
    """Output from consolidation agent."""

    proposals: list[ProposalDraft]
    manual_observations: list[str] = Field(
        default_factory=list,
        description="Observation IDs that need manual handling"
    )
    conflicts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Conflicting observation groups"
    )
    notes: str = ""


class ConsolidationAgent:
    """Agent that consolidates observations into proposals.

    This agent:
    1. Groups related observations by location and type
    2. Generates minimal search-replace diffs
    3. Writes justifications for human reviewers
    4. Identifies conflicts and manual items

    Uses Sonnet 4.5 for reasoning about groupings and edits.
    """

    def __init__(self) -> None:
        self._agent: Agent[None, ConsolidationOutput] | None = None

    def _get_agent(self) -> Agent[None, ConsolidationOutput]:
        """Get or create the consolidation agent."""
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            model = BedrockConverseModel(
                model_name=MODEL_TIER_MAP[ModelTier.REASONING]
            )
            self._agent = Agent(
                model,
                output_type=ConsolidationOutput,
                system_prompt=CONSOLIDATION_SYSTEM_PROMPT,
                retries=2,
            )
        return self._agent

    async def consolidate(
        self,
        observations: list[Observation],
        markdown: str,
        job_id: str,
    ) -> tuple[list[Proposal], list[str]]:
        """Consolidate observations into proposals.

        Args:
            observations: List of observations from all agents
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            Tuple of (proposals, manual_observation_ids)
        """
        # Filter to open observations only
        open_observations = [o for o in observations if o.status == "open"]

        if not open_observations:
            return [], []

        # Pre-group observations by page for context
        obs_by_page = self._group_by_page(open_observations)

        # Build consolidation prompt
        obs_summary = self._format_observations(open_observations)

        agent = self._get_agent()

        messages = [
            f"Consolidate these {len(open_observations)} observations into proposals:\n\n",
            obs_summary,
            f"\n\nCurrent markdown:\n```markdown\n{markdown[:8000]}\n```\n",  # Truncate if needed
            "\nGenerate proposals with exact search-replace diffs.",
        ]

        result = await agent.run(messages)
        output = result.output

        # Convert drafts to full Proposal objects
        proposals = []
        for draft in output.proposals:
            # Validate search text exists in markdown
            if draft.search_text not in markdown:
                # Try to find approximate match or skip
                continue

            route = "auto" if draft.confidence >= 0.7 else "manual"

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

        # Collect manual observation IDs
        manual_ids = output.manual_observations

        return proposals, manual_ids

    def _group_by_page(
        self,
        observations: list[Observation]
    ) -> dict[int, list[Observation]]:
        """Group observations by page number."""
        groups: dict[int, list[Observation]] = {}
        for obs in observations:
            page = obs.location.page_num
            if page not in groups:
                groups[page] = []
            groups[page].append(obs)
        return groups

    def _format_observations(self, observations: list[Observation]) -> str:
        """Format observations for the consolidation prompt."""
        lines = []
        for obs in observations:
            lines.append(f"""
OBSERVATION {obs.id}:
  Agent: {obs.agent}
  Page: {obs.location.page_num}
  Location: {obs.location.value}
  Visual: {obs.visual_description}
  Markup: {obs.markup_description}
  Severity: {obs.severity}
  Confidence: {obs.confidence}
  Route: {obs.route}
""")
        return "\n".join(lines)


CONSOLIDATION_SYSTEM_PROMPT = """You are an accessibility remediation coordinator.
Your task is to consolidate observations into actionable proposals.

CONSOLIDATION RULES:

1. GROUP RELATED OBSERVATIONS
   - Same page region → combine into one proposal
   - Same element (e.g., one image) → one proposal
   - Related fixes (heading + content below) → may combine
   - Don't over-group: keep proposals focused

2. GENERATE MINIMAL SEARCH TEXT
   - Use smallest unique string that identifies location
   - Include enough context to avoid false matches
   - Preserve whitespace and formatting exactly

3. GENERATE ACCURATE REPLACE TEXT
   - Fix all issues addressed by grouped observations
   - Maintain document style and formatting
   - Don't introduce new issues

4. WRITE CLEAR JUSTIFICATIONS
   - Explain why observations are grouped
   - Describe how the edit resolves each observation
   - Note any tradeoffs or assumptions

5. ROUTE APPROPRIATELY
   - confidence >= 0.7: route "auto" (can be batch-approved)
   - confidence < 0.7: route "manual" (needs individual review)
   - conflicting observations: flag as conflict

6. FLAG FOR MANUAL HANDLING
   - Observations with confidence < 0.5
   - Conflicting recommendations
   - Complex structural changes
   - Ambiguous image classifications

OUTPUT:
- List of proposals with search/replace diffs
- List of observation IDs needing manual handling
- Any conflicts detected
"""
```

### Consolidation Service

```python
# src/services/consolidation_service.py

import logging
from typing import Any

from src.agents.consolidation_agent import ConsolidationAgent
from src.services.remediation_storage_service import RemediationStorageService
from src.shared.models.observation import Observation
from src.shared.models.proposal import Proposal

logger = logging.getLogger(__name__)


class ConsolidationService:
    """Service for consolidating observations into proposals."""

    def __init__(
        self,
        storage: RemediationStorageService,
        consolidation_agent: ConsolidationAgent | None = None,
    ) -> None:
        self.storage = storage
        self.agent = consolidation_agent or ConsolidationAgent()

    async def consolidate_observations(
        self,
        job_id: str,
        markdown: str,
    ) -> tuple[list[Proposal], int, int]:
        """Consolidate all observations for a job into proposals.

        Args:
            job_id: Job identifier
            markdown: Current markdown content

        Returns:
            Tuple of (proposals, auto_count, manual_count)
        """
        # Load observations
        observations = await self.storage.load_observations(job_id)

        if not observations:
            logger.info(f"Job {job_id}: No observations to consolidate")
            return [], 0, 0

        logger.info(
            f"Job {job_id}: Consolidating {len(observations)} observations"
        )

        # Run consolidation
        proposals, manual_ids = await self.agent.consolidate(
            observations=observations,
            markdown=markdown,
            job_id=job_id,
        )

        # Update observation statuses for manual items
        for obs in observations:
            if obs.id in manual_ids:
                obs.status = "manual"

        # Save updated observations
        await self.storage.save_observations(job_id, observations)

        # Save proposals
        await self.storage.save_proposals(job_id, proposals)

        # Count by route
        auto_count = sum(1 for p in proposals if p.route == "auto")
        manual_count = sum(1 for p in proposals if p.route == "manual")

        logger.info(
            f"Job {job_id}: Generated {len(proposals)} proposals "
            f"({auto_count} auto, {manual_count} manual)"
        )

        return proposals, auto_count, manual_count

    async def reconsolidate_observation(
        self,
        job_id: str,
        observation: Observation,
        markdown: str,
    ) -> Proposal | None:
        """Re-consolidate a single observation (e.g., from human edit).

        Used when human submits a new observation via edit dialog
        without providing the "after" text.

        Args:
            job_id: Job identifier
            observation: Single observation to consolidate
            markdown: Current markdown

        Returns:
            Generated proposal or None if failed
        """
        proposals, _ = await self.agent.consolidate(
            observations=[observation],
            markdown=markdown,
            job_id=job_id,
        )

        if proposals:
            # Add to existing proposals
            existing = await self.storage.load_proposals(job_id)
            existing.extend(proposals)
            await self.storage.save_proposals(job_id, existing)
            return proposals[0]

        return None
```

### Integration with Processing Service

```python
# src/services/processing_service.py - Phase 4

async def process_document(
    self,
    job: ProcessingQueuePayload,
) -> ProcessingResult:
    """Process PDF using full remediation pipeline."""

    # ... Phases 1-3 from previous PRDs ...

    # Phase 4: Consolidation
    logger.info(f"Job {job.job_id}: Starting consolidation")

    await self.job.update_job_status(
        job.job_id, "processing", substatus="consolidating"
    )

    consolidation_service = ConsolidationService(self.remediation_storage)

    proposals, auto_count, manual_count = await consolidation_service.consolidate_observations(
        job_id=job.job_id,
        markdown=markdown,
    )

    logger.info(
        f"Job {job.job_id}: Consolidation complete - "
        f"{len(proposals)} proposals ({auto_count} auto, {manual_count} manual)"
    )

    # Update job for review
    await self.job.update_job_status(
        job.job_id, "processing",
        substatus="awaiting_review",
        proposal_count=len(proposals),
        pending_proposals=len(proposals),
        auto_proposals=auto_count,
        manual_observations=manual_count,
    )

    # Job now waits for human review (PRD-016)
    # Processing continues in review API

    return ProcessingResult(
        job_id=job.job_id,
        markdown_url=result_url,
        confidence_score=extraction_confidence,
        processing_time_seconds=int(time.time() - start_time),
    )
```

### Search-Replace Validation

```python
# src/utils/diff_utils.py

def validate_search_replace(
    markdown: str,
    search: str,
    replace: str
) -> tuple[bool, str | None]:
    """Validate a search-replace operation.

    Args:
        markdown: Current markdown content
        search: Text to find
        replace: Text to substitute

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not search:
        return False, "Search text cannot be empty"

    if search == replace:
        return False, "Search and replace text are identical"

    # Check uniqueness
    count = markdown.count(search)
    if count == 0:
        return False, "Search text not found in document"
    if count > 1:
        return False, f"Search text matches {count} locations (must be unique)"

    # Check for nested issues
    if search in replace and replace != search:
        # Could cause infinite expansion, but usually ok
        pass

    return True, None


def find_unique_context(
    markdown: str,
    target: str,
    min_context: int = 20,
    max_context: int = 100
) -> str | None:
    """Find minimal unique context around target text.

    Args:
        markdown: Document content
        target: Text to make unique
        min_context: Minimum characters of context
        max_context: Maximum characters of context

    Returns:
        Unique search string or None if not found
    """
    if target not in markdown:
        return None

    if markdown.count(target) == 1:
        return target

    # Expand context until unique
    idx = markdown.find(target)
    for ctx in range(min_context, max_context + 1, 10):
        start = max(0, idx - ctx)
        end = min(len(markdown), idx + len(target) + ctx)
        candidate = markdown[start:end]

        if markdown.count(candidate) == 1:
            return candidate

    return None
```

## Acceptance Criteria

### 1. Observation Grouping
- [ ] Related observations (same region) grouped
- [ ] Observations on same element combined
- [ ] Grouping logic documented and testable
- [ ] Over-grouping avoided

### 2. Search-Replace Generation
- [ ] Search text is unique in document
- [ ] Search text is minimal but unambiguous
- [ ] Replace text fixes all grouped observations
- [ ] Whitespace and formatting preserved

### 3. Justifications
- [ ] Explain why observations are grouped
- [ ] Describe how edit resolves issues
- [ ] Note any assumptions or tradeoffs
- [ ] Written for human comprehension

### 4. Routing
- [ ] High-confidence (>=0.7) routes to auto
- [ ] Low-confidence (<0.7) routes to manual
- [ ] Conflicts flagged appropriately
- [ ] Manual IDs tracked separately

### 5. Re-consolidation
- [ ] Single observation can be reconsolidated
- [ ] Human edits trigger reconsolidation
- [ ] New proposals added to existing set

### 6. Performance
- [ ] Consolidation <30 seconds typical
- [ ] Handles large observation sets
- [ ] Memory efficient

## Deliverables

### Files to Create

```
src/agents/
└── consolidation_agent.py

src/services/
└── consolidation_service.py

src/utils/
└── diff_utils.py

tests/agents/
└── test_consolidation_agent.py

tests/services/
└── test_consolidation_service.py

tests/utils/
└── test_diff_utils.py
```

## Technical Notes

### Grouping Heuristics

```python
# Observations should be grouped when:
# 1. Same page AND within 50 characters in markdown
# 2. Same element identifier (e.g., same image placeholder)
# 3. Agent explicitly indicates relationship
# 4. Fixing one requires changing the other
```

### Conflict Detection

```python
# Conflicts occur when:
# 1. Two observations recommend different fixes for same location
# 2. Observations have contradictory assessments
# 3. Grouped observations have significantly different confidence
```

### Cost Estimate

Consolidation is a single Sonnet call:
- Input: ~5K tokens (observations + markdown excerpt)
- Output: ~2K tokens (proposals)
- Cost: ~$0.05 per document

## Definition of Done

- [ ] ConsolidationAgent groups observations correctly
- [ ] Search-replace diffs are valid and unique
- [ ] Justifications are clear and helpful
- [ ] Routing works correctly by confidence
- [ ] Re-consolidation for human edits works
- [ ] Validation utilities implemented
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Documentation complete
