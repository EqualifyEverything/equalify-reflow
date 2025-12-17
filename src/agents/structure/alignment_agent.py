"""Visual-semantic alignment agent for chained structure analysis.

Step 2 of the structure chain: Check visual-semantic alignment.
Uses Haiku (EFFICIENT) for cost-effective visual analysis.

CONDITIONAL: Only runs on pages with heading issues or complex layouts.

Checks:
- Visual heading prominence matches semantic level
- Visually emphasized text is properly marked
- Heading levels align with visual hierarchy
"""

from __future__ import annotations

import base64
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.agents.structure.hierarchy_validator import HierarchyIssue
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, AlignmentOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective analysis


# =============================================================================
# Output Models
# =============================================================================


class AlignmentIssue(BaseModel):
    """A visual-semantic alignment issue."""

    page_num: int = Field(ge=1, description="Page number")
    heading_text: str = Field(description="The heading text")
    visual_level: int = Field(ge=1, le=6, description="Visual prominence level (1=most prominent)")
    semantic_level: int = Field(ge=1, le=6, description="Current semantic level (h1-h6)")
    suggested_level: int = Field(ge=1, le=6, description="Suggested semantic level")
    severity: Literal["major", "minor"] = Field(description="Issue severity")
    description: str = Field(description="Description of the mismatch")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in assessment")


class AlignmentOutput(BaseModel):
    """Output from alignment check."""

    page_num: int = Field(ge=1, description="Page number analyzed")
    issues: list[AlignmentIssue] = Field(default_factory=list)
    notes: str = Field(default="", description="Additional observations")


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load alignment agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("structure_alignment.yaml")
    return _prompts


def get_agent() -> Agent[None, AlignmentOutput]:
    """Get or create the alignment agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="structure_alignment.yaml",
            output_type=AlignmentOutput,
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


async def check_alignment(
    page_num: int,
    image_base64: str,
    page_markdown: str,
    hierarchy_issues: list[HierarchyIssue],
    job_id: str,
) -> tuple[AlignmentOutput, LLMUsage]:
    """Check visual-semantic alignment on a page.

    CONDITIONAL: Should only be called for pages with hierarchy issues
    or complex layouts.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded page image
        page_markdown: Markdown content for this page
        hierarchy_issues: Issues found by hierarchy validator for context
        job_id: Job ID for correlation

    Returns:
        Tuple of (alignment output, usage metrics)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Decode image for multimodal input
    image_bytes = base64.b64decode(image_base64)

    # Format hierarchy issues for context
    issue_context = ""
    if hierarchy_issues:
        issue_context = "Known hierarchy issues on this page:\n" + "\n".join(
            f"- {issue.issue_type}: {issue.description}" for issue in hierarchy_issues
        )

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        issue_context=issue_context,
        page_markdown=page_markdown[:3000],
    )

    # Build multimodal prompt
    prompt = [
        user_prompt,
        BinaryContent(data=image_bytes, media_type="image/png"),
    ]

    logger.debug(f"Job {job_id}: Checking alignment on page {page_num}")

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="structure_alignment",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: AlignmentOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} alignment check complete - "
        f"{len(output.issues)} issues, cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "AlignmentIssue",
    "AlignmentOutput",
    "check_alignment",
    "get_agent",
    "reset_agent",
]
