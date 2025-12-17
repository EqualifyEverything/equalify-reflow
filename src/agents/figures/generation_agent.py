"""Alt text generation agent for chained figures analysis.

Step 2 of the figures chain: Generate alt text for non-decorative images.
Uses Haiku (EFFICIENT) for cost-effective alt text generation.

This step is SKIPPED for decorative images (Step 1 classification determines this).

Alt text generation strategies by image type:
- informative: Concise functional description (1-2 sentences)
- complex: Detailed description + flag for long description
- text: OCR-style transcription of text in image
"""

from __future__ import annotations

import base64
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.figures.classification_agent import ImageClassification, ImageType
from src.agents.model_tiers import ModelTier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, AltTextGenerationOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective generation


# =============================================================================
# Output Models
# =============================================================================


class AltTextGeneration(BaseModel):
    """Alt text generation result for a single image."""

    image_index: int = Field(ge=1, description="Image number on this page (1-indexed)")
    image_type: ImageType = Field(description="Image type from classification step")
    alt_text: str = Field(
        min_length=1,
        description="Generated alt text appropriate for image type",
    )
    long_description_needed: bool = Field(
        default=False,
        description="Whether image requires extended description (complex images)",
    )
    long_description: str | None = Field(
        default=None,
        description="Extended description for complex images (if needed)",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in generated alt text quality"
    )
    generation_notes: str = Field(
        default="",
        description="Notes about generation strategy or limitations",
    )


class AltTextGenerationOutput(BaseModel):
    """Output from alt text generation for a page."""

    page_num: int = Field(ge=1, description="Page number")
    generations: list[AltTextGeneration] = Field(default_factory=list)


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load generation agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("figures_generation.yaml")
    return _prompts


def get_agent() -> Agent[None, AltTextGenerationOutput]:
    """Get or create the generation agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="figures_generation.yaml",
            output_type=AltTextGenerationOutput,
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


async def generate(
    page_num: int,
    image_base64: str,
    classifications: list[ImageClassification],
    page_markdown: str,
    document_context: str,
    job_id: str,
) -> tuple[AltTextGenerationOutput, LLMUsage]:
    """Generate alt text for classified images on a page.

    Only generates alt text for non-decorative images.
    Decorative images are filtered out before calling LLM.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded page image
        classifications: Classification results from Step 1
        page_markdown: Markdown content for this page
        document_context: Document title and type for context
        job_id: Job ID for correlation

    Returns:
        Tuple of (generation output, usage metrics)
    """
    # Filter to non-decorative images only
    non_decorative = [c for c in classifications if c.image_type != "decorative"]

    if not non_decorative:
        # All images are decorative - return empty result with zero usage
        logger.debug(
            f"Job {job_id}: Page {page_num} - all {len(classifications)} images decorative, skipping generation"
        )
        return (
            AltTextGenerationOutput(page_num=page_num, generations=[]),
            LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            ),
        )

    agent = get_agent()
    prompts = get_prompts()

    # Decode image for multimodal input
    image_bytes = base64.b64decode(image_base64)

    # Format classification context for prompt
    classification_context = "\n".join(
        f"- Image {c.image_index}: {c.image_type} - {c.visual_description}"
        for c in non_decorative
    )

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        document_context=document_context,
        classification_context=classification_context,
        image_count=len(non_decorative),
        page_markdown=page_markdown[:2000],  # Limit context size
    )

    # Build multimodal prompt
    prompt = [
        user_prompt,
        BinaryContent(data=image_bytes, media_type="image/png"),
    ]

    logger.debug(
        f"Job {job_id}: Generating alt text for {len(non_decorative)} images on page {page_num}"
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="figures_generation",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: AltTextGenerationOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} generation complete - "
        f"{len(output.generations)} alt texts generated, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "AltTextGeneration",
    "AltTextGenerationOutput",
    "generate",
    "get_agent",
    "reset_agent",
]
