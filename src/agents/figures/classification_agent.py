"""Image classification agent for chained figures analysis.

Step 1 of the figures chain: Classify images by type.
Uses Haiku (EFFICIENT) for cost-effective visual classification.

Classification types:
- decorative: Non-informative icons, borders, backgrounds
- informative: Photos, diagrams with meaning
- complex: Charts, schematics, diagrams requiring detailed description
- text: Images containing readable text (OCR needed)
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
_agent: Agent[None, ImageClassificationOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective classification


# =============================================================================
# Output Models
# =============================================================================


ImageType = Literal["decorative", "informative", "complex", "text"]


class ImageClassification(BaseModel):
    """Classification result for a single image."""

    image_index: int = Field(ge=1, description="Image number on this page (1-indexed)")
    image_type: ImageType = Field(description="Classification: decorative, informative, complex, or text")
    confidence: float = Field(ge=0.0, le=1.0, description="Classification confidence")
    visual_description: str = Field(
        min_length=1, description="Brief description of what the image shows"
    )
    decorative_reasoning: str = Field(
        description="Why image is/isn't decorative (empty if clearly informative)"
    )


class ImageClassificationOutput(BaseModel):
    """Output from image classification for a page."""

    page_num: int = Field(ge=1, description="Page number")
    images_found: int = Field(ge=0, description="Number of images detected")
    classifications: list[ImageClassification] = Field(default_factory=list)


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load classification agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("figures_classification.yaml")
    return _prompts


def get_agent() -> Agent[None, ImageClassificationOutput]:
    """Get or create the classification agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="figures_classification.yaml",
            output_type=ImageClassificationOutput,
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


async def classify(
    page_num: int,
    image_base64: str,
    expected_image_count: int,
    page_markdown: str,
    job_id: str,
) -> tuple[ImageClassificationOutput, LLMUsage]:
    """Classify images on a single page.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded page image
        expected_image_count: Number of images expected on this page
        page_markdown: Markdown content for this page
        job_id: Job ID for correlation

    Returns:
        Tuple of (classification output, usage metrics)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Decode image for multimodal input
    image_bytes = base64.b64decode(image_base64)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        expected_image_count=expected_image_count,
        page_markdown=page_markdown[:2000],  # Limit context size
    )

    # Build multimodal prompt
    prompt = [
        user_prompt,
        BinaryContent(data=image_bytes, media_type="image/png"),
    ]

    logger.debug(
        f"Job {job_id}: Classifying images on page {page_num} "
        f"(expecting {expected_image_count} images)"
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="figures_classification",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: ImageClassificationOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} classification complete - "
        f"{len(output.classifications)} images classified, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "ImageClassification",
    "ImageClassificationOutput",
    "ImageType",
    "classify",
    "get_agent",
    "reset_agent",
]
