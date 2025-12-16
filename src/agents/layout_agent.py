"""Layout detection agent for chained analysis pipeline.

This agent performs the FIRST step of the chained analysis:
Visual layout detection for each page (single_column, two_column, mixed).

Layout detection is critical because it controls reading order for
all subsequent analysis (headings, extraction, etc.).
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, LayoutOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for visual detection


# =============================================================================
# Output Models
# =============================================================================


class PageLayout(BaseModel):
    """Layout detection result for a single page."""

    page_num: int = Field(ge=1, description="1-indexed page number")
    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        description="Detected layout type"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in layout detection"
    )
    evidence: str = Field(
        description="One sentence describing visual evidence for this layout"
    )


class LayoutOutput(BaseModel):
    """Output from layout detection agent."""

    pages: list[PageLayout] = Field(description="Layout detection for each page")


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load layout agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("layout.yaml")
    return _prompts


def get_agent() -> Agent[None, LayoutOutput]:
    """Get or create the layout detection agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="layout.yaml",
            output_type=LayoutOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def detect(
    pages: list[PageData],
    job_id: str,
) -> tuple[list[PageLayout], LLMUsage]:
    """Detect layout for each page.

    Args:
        pages: List of PageData with page images
        job_id: Job ID for correlation

    Returns:
        Tuple of (list of PageLayout results, LLMUsage)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build multimodal prompt with all page images
    import base64

    page_images = [
        BinaryContent(
            data=base64.b64decode(page.image_base64),
            media_type="image/png",
        )
        for page in pages
    ]

    user_prompt = prompts["user_prompt"].format(total_pages=len(pages))

    prompt = [user_prompt, *page_images]

    logger.info(f"Job {job_id}: Running layout detection on {len(pages)} pages")

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="layout",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: LayoutOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    # Log results
    for pl in output.pages:
        logger.debug(
            f"Job {job_id}: Page {pl.page_num} layout={pl.layout_type} "
            f"conf={pl.confidence:.2f}"
        )

    logger.info(
        f"Job {job_id}: Layout detection complete - "
        f"{sum(1 for p in output.pages if p.layout_type == 'two_column')} two-column pages, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output.pages, usage


# =============================================================================
# Backward-compatible class wrapper
# =============================================================================


class LayoutAgent:
    """Layout detection agent for chained analysis.

    This is a thin wrapper around module-level functions for
    backward compatibility with class-based agent patterns.
    """

    async def detect(
        self,
        pages: list[PageData],
        job_id: str,
    ) -> tuple[list[PageLayout], LLMUsage]:
        """Detect layout for each page."""
        return await detect(pages, job_id)


__all__ = [
    "LayoutAgent",
    "LayoutOutput",
    "PageLayout",
    "detect",
    "get_agent",
]
