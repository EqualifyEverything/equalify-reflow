"""Reading order validation agent for chained structure analysis.

Step 3 of the structure chain: Validate reading order.
Uses Haiku (EFFICIENT) for cost-effective analysis.

CONDITIONAL: Only runs on pages with two-column layouts.
Single-column pages don't need reading order validation.

Checks:
- Content flows correctly in two-column layout
- Reading order matches visual layout
- No content appears out of sequence
"""

from __future__ import annotations

import base64
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, ReadingOrderOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective analysis


# =============================================================================
# Output Models
# =============================================================================


class ReadingOrderIssue(BaseModel):
    """A reading order issue found."""

    page_num: int = Field(ge=1, description="Page number")
    issue_type: Literal["wrong_order", "split_content", "missing_section"] = Field(
        description="Type of reading order issue"
    )
    description: str = Field(description="Description of the issue")
    affected_content: str = Field(description="Content that is out of order")
    suggested_order: str = Field(description="How content should be ordered")
    severity: Literal["major", "minor"] = Field(description="Issue severity")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in assessment")


class ReadingOrderOutput(BaseModel):
    """Output from reading order check."""

    page_num: int = Field(ge=1, description="Page number analyzed")
    issues: list[ReadingOrderIssue] = Field(default_factory=list)
    reading_order_correct: bool = Field(
        default=True, description="Whether reading order is correct"
    )
    notes: str = Field(default="", description="Additional observations")


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load reading order agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("structure_reading_order.yaml")
    return _prompts


def get_agent() -> Agent[None, ReadingOrderOutput]:
    """Get or create the reading order agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="structure_reading_order.yaml",
            output_type=ReadingOrderOutput,
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


async def check_reading_order(
    page_num: int,
    image_base64: str,
    page_markdown: str,
    layout_type: str,
    job_id: str,
) -> tuple[ReadingOrderOutput, LLMUsage]:
    """Check reading order on a two-column page.

    CONDITIONAL: Should only be called for pages with two-column layouts.
    Single-column pages don't need this check.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded page image
        page_markdown: Markdown content for this page
        layout_type: Layout type (should be "two_column" or "mixed")
        job_id: Job ID for correlation

    Returns:
        Tuple of (reading order output, usage metrics)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Decode image for multimodal input
    image_bytes = base64.b64decode(image_base64)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        layout_type=layout_type,
        page_markdown=page_markdown[:4000],  # More context for reading order
    )

    # Build multimodal prompt
    prompt = [
        user_prompt,
        BinaryContent(data=image_bytes, media_type="image/png"),
    ]

    logger.debug(
        f"Job {job_id}: Checking reading order on page {page_num} ({layout_type})"
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="structure_reading_order",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: ReadingOrderOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} reading order check complete - "
        f"correct={output.reading_order_correct}, "
        f"{len(output.issues)} issues, cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "ReadingOrderIssue",
    "ReadingOrderOutput",
    "check_reading_order",
    "get_agent",
    "reset_agent",
]
