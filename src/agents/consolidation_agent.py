"""ConsolidationAgent for transforming observations into proposals.

The consolidation phase is the bridge between AI analysis and human review:
1. Groups related observations (same region → one proposal)
2. Generates search-replace diffs (exact text operations)
3. Writes justifications (explains grouping rationale)
4. Routes to appropriate queue (high-confidence → auto, low-confidence → manual)

NOTE: This agent uses enhanced prompts with:
- XML structure tags for semantic sections
- Concrete examples for each scenario type
- Validation checklist for output quality
- Confidence calibration guidance
- Common mistakes to avoid
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.config import settings
from src.shared.models.observation import Observation
from src.shared.models.processing import LLMUsage
from src.shared.models.proposal import Proposal, SearchReplaceDiff
from src.shared.models.remediation import DocumentManifest
from src.utils.diff_utils import validate_search_replace

logger = logging.getLogger(__name__)

# Module-level state for lazy initialization

_agent: Agent[None, Any] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.REASONING

# Maximum markdown length to include in prompt (to manage tokens)
MAX_MARKDOWN_LENGTH = 8000


class ProposalDraft(BaseModel):
    """Draft proposal from consolidation agent.

    The agent generates these drafts which are then validated and converted
    to full Proposal objects.

    Enhanced fields support the improved consolidation prompt which requires:
    - Explicit grouping rationale for multi-observation proposals
    - Route reasoning explaining confidence and risk factors
    - Calibrated confidence (not defaulted to 0.8)
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
        description="Text to substitute (fixes all grouped observations)",
    )
    justification: str = Field(
        ...,
        description="Why observations are grouped AND how the edit addresses each one",
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
        ...,  # Required, not defaulted - must be calibrated
        ge=0.0,
        le=1.0,
        description=(
            "Calibrated confidence (0.0-1.0). DO NOT default to 0.8. "
            "Consider: observation confidence, search text uniqueness, fix complexity."
        ),
    )
    grouping_rationale: str = Field(
        default="Single observation, no grouping needed.",
        description=(
            "Why these observations are grouped together. "
            "For single observations: 'Single observation, no grouping needed.' "
            "For multiple: explain what connects them and why single edit addresses all."
        ),
    )
    route: Literal["auto", "manual"] = Field(
        ...,
        description=(
            "Routing decision: 'auto' for batch approval (confidence >= 0.75), "
            "'manual' for individual review (confidence < 0.75 or structural changes)."
        ),
    )
    route_reasoning: str = Field(
        ...,
        description=(
            "Explanation of routing decision. Include: confidence level, "
            "risk factors, what reviewer should verify (for manual routes)."
        ),
    )


class ConflictRecord(BaseModel):
    """Record of conflicting observations that need human resolution."""

    observation_ids: list[str] = Field(
        ...,
        description="IDs of the conflicting observations",
    )
    conflict_type: str = Field(
        ...,
        description=(
            "Type of conflict: 'contradictory_classification', 'incompatible_fixes', "
            "'ambiguous_element', 'overlapping_regions'"
        ),
    )
    description: str = Field(
        ...,
        description="Human-readable description of what the conflict is",
    )
    page_num: int = Field(
        ...,
        ge=1,
        description="Page number where conflict occurs",
    )
    element: str = Field(
        default="",
        description="What element is affected (e.g., 'image 1', 'heading Course Schedule')",
    )


class ConsolidationOutput(BaseModel):
    """Structured output from consolidation agent.

    Enhanced to support:
    - Detailed conflict records (not just dicts)
    - Explicit tracking of all observation dispositions
    """

    proposals: list[ProposalDraft] = Field(
        default_factory=list,
        description="List of proposal drafts to create",
    )
    manual_observations: list[str] = Field(
        default_factory=list,
        description=(
            "Observation IDs that need manual handling. "
            "Use for: confidence < 0.5, requires human judgment, outside scope."
        ),
    )
    conflicts: list[ConflictRecord] = Field(
        default_factory=list,
        description=(
            "Conflicting observation groups that need human resolution. "
            "Do NOT create proposals for conflicting observations."
        ),
    )
    notes: str = Field(
        default="",
        description="Additional context for human reviewers",
    )


def get_agent() -> Agent[None, ConsolidationOutput]:
    """Get or create the consolidation agent (lazy initialization)."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("consolidation.yaml")
        _agent = create_agent(
            "consolidation.yaml",
            ConsolidationOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
        logger.info(f"ConsolidationAgent initialized with model tier {_MODEL_TIER.value}")

    return _agent


def reset_agent() -> None:
    """Reset the agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


async def consolidate(
    observations: list[Observation],
    markdown: str,
    job_id: str,
    manifest: DocumentManifest | None = None,
) -> tuple[list[Proposal], list[str], LLMUsage]:
    """Consolidate observations into proposals.

    Args:
        observations: List of observations from all agents
        markdown: Current markdown content
        job_id: Job identifier
        manifest: Optional document manifest for context (title, type, pages)

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

    # Get prompts and agent
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("consolidation.yaml")

    agent = get_agent()

    # Build user message using YAML template
    obs_summary = _format_observations(open_observations)
    markdown_excerpt = _truncate_markdown(markdown)

    # Use YAML user_prompt template with placeholders
    user_prompt_template = _prompts.get("user_prompt", "")
    if user_prompt_template:
        user_message = user_prompt_template.format(
            observation_count=len(open_observations),
            document_title=manifest.document_title if manifest else "Unknown",
            document_type=manifest.document_type if manifest else "unknown",
            total_pages=manifest.total_pages if manifest else 0,
            observations_summary=obs_summary,
            markdown_length=len(markdown),
            markdown_content=markdown_excerpt,
        )
    else:
        # Fallback if user_prompt not in YAML (backwards compatibility)
        user_message = (
            f"Consolidate these {len(open_observations)} observations into proposals:\n\n"
            f"{obs_summary}\n\n"
            f"Current markdown ({len(markdown)} chars):\n```markdown\n{markdown_excerpt}\n```\n\n"
            f"Generate proposals with exact search-replace diffs."
        )

    # Execute agent with tracing
    result = await run_agent(
        agent=agent,
        prompt=user_message,
        job_id=job_id,
        agent_name="consolidation_agent",
        model_tier=_MODEL_TIER,
        model_settings={
            "max_tokens": 16000,
            "temperature": 0.3,
        },
    )

    output = result.output

    # Extract usage with model tier
    llm_usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Consolidation agent returned {len(output.proposals)} drafts, "
        f"{len(output.manual_observations)} manual, {len(output.conflicts)} conflicts"
    )

    # Convert drafts to validated Proposal objects
    proposals = _validate_and_convert_proposals(
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


def _format_observations(observations: list[Observation]) -> str:
    """Format observations for the consolidation prompt.

    Groups observations by page using XML structure for clearer context.
    Format matches the examples in consolidation.yaml.
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
        lines.append(f"<page number=\"{page_num}\" observation_count=\"{len(page_obs)}\">")

        for obs in page_obs:
            # Format matches the examples in consolidation.yaml
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


def _truncate_markdown(markdown: str) -> str:
    """Truncate markdown if too long for prompt."""
    if len(markdown) <= MAX_MARKDOWN_LENGTH:
        return markdown

    # Truncate with indicator
    return markdown[:MAX_MARKDOWN_LENGTH] + "\n\n[... TRUNCATED ...]"


def _validate_and_convert_proposals(
    drafts: list[ProposalDraft],
    markdown: str,
    job_id: str,
) -> list[Proposal]:
    """Validate draft proposals and convert to full Proposal objects.

    Filters out drafts with invalid search text (not found or not unique).
    Uses the route from the draft (agent-determined) with confidence override.
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

        # Use route from draft, but override to manual if confidence is below threshold
        # This ensures the LLM's routing decision is respected, but we enforce minimums
        route: Literal["auto", "manual"] = draft.route
        if draft.confidence < settings.min_confidence_for_auto_approval and route == "auto":
            logger.info(
                f"Job {job_id}: Overriding route to manual (confidence {draft.confidence} "
                f"< threshold {settings.min_confidence_for_auto_approval})"
            )
            route = "manual"

        # Build enhanced justification including grouping rationale and route reasoning
        full_justification = draft.justification
        if draft.grouping_rationale and draft.grouping_rationale != "Single observation, no grouping needed.":
            full_justification = f"{draft.justification}\n\nGrouping: {draft.grouping_rationale}"

        proposal = Proposal(
            id=str(uuid.uuid4()),
            job_id=job_id,
            resolves=draft.observation_ids,
            diff=SearchReplaceDiff(
                search=draft.search_text,
                replace=draft.replace_text,
            ),
            justification=full_justification,
            page_nums=draft.page_nums,
            estimated_impact=draft.estimated_impact,
            route=route,
            status="pending",
        )
        proposals.append(proposal)

    return proposals


# Wrapper class for backward compatibility
class ConsolidationAgent:
    """Wrapper class for backward compatibility.

    This thin wrapper allows the module to be used with existing code
    that expects a class with a consolidate() method.
    """

    # Expose constants for tests
    MAX_MARKDOWN_LENGTH = MAX_MARKDOWN_LENGTH

    @property
    def model_tier(self) -> ModelTier:
        """Return the model tier for backward compatibility."""
        return _MODEL_TIER

    @property
    def model_id(self) -> str:
        """Return the model ID for backward compatibility."""
        from src.agents.model_tiers import MODEL_TIER_MAP
        return MODEL_TIER_MAP[_MODEL_TIER]

    @property
    def prompts(self) -> dict[str, Any]:
        """Return prompts for backward compatibility."""
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("consolidation.yaml")
        return _prompts

    @property
    def _core(self) -> Any:
        """Mock _core property for test compatibility."""
        # Create a mock object with _agent attribute
        class MockCore:
            def __init__(self) -> None:
                self._agent = None

        return MockCore()

    def _get_agent(self) -> Agent[None, ConsolidationOutput]:
        """Get the agent instance (delegates to module function)."""
        return get_agent()

    def _format_observations(self, observations: list[Observation]) -> str:
        """Format observations (delegates to module function)."""
        return _format_observations(observations)

    def _truncate_markdown(self, markdown: str) -> str:
        """Truncate markdown (delegates to module function)."""
        return _truncate_markdown(markdown)

    def _validate_and_convert_proposals(
        self,
        drafts: list[ProposalDraft],
        markdown: str,
        job_id: str,
    ) -> list[Proposal]:
        """Validate and convert proposals (delegates to module function)."""
        return _validate_and_convert_proposals(drafts, markdown, job_id)

    async def consolidate(
        self,
        observations: list[Observation],
        markdown: str,
        job_id: str,
        manifest: DocumentManifest | None = None,
    ) -> tuple[list[Proposal], list[str], LLMUsage]:
        """Consolidate observations into proposals (wrapper delegates to module function).

        This method uses self._get_agent() to allow test mocking while delegating
        the actual work to the module-level consolidate function.
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

        # Get prompts and agent (using self._get_agent for test mocking)
        global _prompts
        if _prompts is None:
            _prompts = load_prompts("consolidation.yaml")

        agent = self._get_agent()

        # Build user message using YAML template
        obs_summary = _format_observations(open_observations)
        markdown_excerpt = _truncate_markdown(markdown)

        # Use YAML user_prompt template with placeholders
        user_prompt_template = _prompts.get("user_prompt", "")
        if user_prompt_template:
            user_message = user_prompt_template.format(
                observation_count=len(open_observations),
                document_title=manifest.document_title if manifest else "Unknown",
                document_type=manifest.document_type if manifest else "unknown",
                total_pages=manifest.total_pages if manifest else 0,
                observations_summary=obs_summary,
                markdown_length=len(markdown),
                markdown_content=markdown_excerpt,
            )
        else:
            # Fallback if user_prompt not in YAML (backwards compatibility)
            user_message = (
                f"Consolidate these {len(open_observations)} observations into proposals:\n\n"
                f"{obs_summary}\n\n"
                f"Current markdown ({len(markdown)} chars):\n```markdown\n{markdown_excerpt}\n```\n\n"
                f"Generate proposals with exact search-replace diffs."
            )

        # Execute agent with tracing
        result = await run_agent(
            agent=agent,
            prompt=user_message,
            job_id=job_id,
            agent_name="consolidation_agent",
            model_tier=_MODEL_TIER,
            model_settings={
                "max_tokens": 16000,
                "temperature": 0.3,
            },
        )

        # Support both .output (old) and .data (new) for backward compatibility
        output = result.output

        # Extract usage with model tier
        llm_usage = extract_usage(result, _MODEL_TIER)

        logger.debug(
            f"Job {job_id}: Consolidation agent returned {len(output.proposals)} drafts, "
            f"{len(output.manual_observations)} manual, {len(output.conflicts)} conflicts"
        )

        # Convert drafts to validated Proposal objects
        proposals = _validate_and_convert_proposals(
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


__all__ = [
    "ConsolidationAgent",
    "ConsolidationOutput",
    "ConflictRecord",
    "ProposalDraft",
    "consolidate",
    "get_agent",
    "reset_agent",
]
