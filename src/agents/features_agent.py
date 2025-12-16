"""Page features detection agent for chained analysis pipeline.

This agent detects content features on each page:
images, tables, lists, code blocks, math equations.

Runs in parallel with HeadingsAgent in the chained analysis pipeline.
"""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, FeaturesOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for feature detection


# =============================================================================
# Output Models
# =============================================================================


class PageFeatureDetection(BaseModel):
    """Feature detection result for a single page."""

    page_num: int = Field(ge=1, description="1-indexed page number")
    has_images: bool = Field(
        description="Whether page has informative images (not decorative)"
    )
    image_count: int = Field(ge=0, description="Number of informative images")
    has_tables: bool = Field(description="Whether page has data tables")
    table_count: int = Field(ge=0, description="Number of data tables")
    has_lists: bool = Field(description="Whether page has bulleted/numbered lists")
    has_code_blocks: bool = Field(
        description="Whether page has programming code or command-line content"
    )
    has_math: bool = Field(
        description="Whether page has mathematical equations or formulas"
    )


class FeaturesOutput(BaseModel):
    """Output from page features detection agent."""

    pages: list[PageFeatureDetection] = Field(
        description="Feature detection for each page"
    )


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load features agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("features.yaml")
    return _prompts


def get_agent() -> Agent[None, FeaturesOutput]:
    """Get or create the features detection agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="features.yaml",
            output_type=FeaturesOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def detect(
    pages: list[PageData],
    job_id: str,
) -> tuple[list[PageFeatureDetection], LLMUsage]:
    """Detect features on each page.

    Args:
        pages: List of PageData with page images
        job_id: Job ID for correlation

    Returns:
        Tuple of (list of PageFeatureDetection, LLMUsage)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build multimodal prompt with all page images
    page_images = [
        BinaryContent(
            data=base64.b64decode(page.image_base64),
            media_type="image/png",
        )
        for page in pages
    ]

    user_prompt = prompts["user_prompt"].format(total_pages=len(pages))

    prompt = [user_prompt, *page_images]

    logger.info(f"Job {job_id}: Running feature detection on {len(pages)} pages")

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="features",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: FeaturesOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    # Summarize results
    total_images = sum(p.image_count for p in output.pages)
    total_tables = sum(p.table_count for p in output.pages)
    pages_with_code = sum(1 for p in output.pages if p.has_code_blocks)
    pages_with_math = sum(1 for p in output.pages if p.has_math)

    logger.info(
        f"Job {job_id}: Features detected - "
        f"{total_images} images, {total_tables} tables, "
        f"{pages_with_code} pages with code, {pages_with_math} pages with math, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output.pages, usage


# =============================================================================
# Backward-compatible class wrapper
# =============================================================================


class FeaturesAgent:
    """Page features detection agent for chained analysis.

    This is a thin wrapper around module-level functions for
    backward compatibility with class-based agent patterns.
    """

    async def detect(
        self,
        pages: list[PageData],
        job_id: str,
    ) -> tuple[list[PageFeatureDetection], LLMUsage]:
        """Detect features on each page."""
        return await detect(pages, job_id)


__all__ = [
    "FeaturesAgent",
    "FeaturesOutput",
    "PageFeatureDetection",
    "detect",
    "get_agent",
]
