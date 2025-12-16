"""Heading structure extraction agent for chained analysis pipeline.

This agent extracts document heading structure using layout and doc type context.
Runs in parallel with FeaturesAgent in the chained analysis pipeline.

CRITICAL: Uses layout info to determine correct reading order for two-column pages.
"""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.doctype_agent import DocumentTypeOutput
from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.layout_agent import PageLayout
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, HeadingsOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for heading extraction


# =============================================================================
# Output Models
# =============================================================================


class Heading(BaseModel):
    """A heading in the document."""

    level: int = Field(ge=1, le=6, description="Heading level 1-6 (H1-H6)")
    text: str = Field(description="Exact heading text as it appears")
    page_num: int = Field(ge=1, description="1-indexed page number")
    section_number: str | None = Field(
        default=None, description="Section number if present (e.g., '1.1', 'Chapter 2')"
    )


class HeadingsOutput(BaseModel):
    """Output from heading structure extraction agent."""

    headings: list[Heading] = Field(description="All headings in reading order")
    reading_order_notes: str = Field(
        description="Notes about reading order decisions for multi-column pages"
    )


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load headings agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("headings.yaml")
    return _prompts


def get_agent() -> Agent[None, HeadingsOutput]:
    """Get or create the headings extraction agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="headings.yaml",
            output_type=HeadingsOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def extract(
    pages: list[PageData],
    layouts: list[PageLayout],
    doc_type: DocumentTypeOutput,
    job_id: str,
) -> tuple[HeadingsOutput, LLMUsage]:
    """Extract heading structure from the document.

    Args:
        pages: List of PageData with page images
        layouts: Layout detection results from LayoutAgent
        doc_type: Document type classification from DocTypeAgent
        job_id: Job ID for correlation

    Returns:
        Tuple of (HeadingsOutput, LLMUsage)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build layout info for context
    layout_info = "\n".join(
        f"- Page {layout.page_num}: {layout.layout_type}" for layout in layouts
    )

    # Build reading order guidance for two-column pages
    two_col_pages = [layout.page_num for layout in layouts if layout.layout_type == "two_column"]
    if two_col_pages:
        reading_order_guidance = (
            f"CRITICAL: Pages {two_col_pages} are two-column. "
            "Read each column top-to-bottom, left column first, then right column."
        )
    else:
        reading_order_guidance = "All pages are single-column. Read top to bottom."

    # Build multimodal prompt with all page images
    page_images = [
        BinaryContent(
            data=base64.b64decode(page.image_base64),
            media_type="image/png",
        )
        for page in pages
    ]

    user_prompt = prompts["user_prompt"].format(
        document_type=doc_type.document_type,
        layout_info=layout_info,
        reading_order_guidance=reading_order_guidance,
    )

    prompt = [user_prompt, *page_images]

    logger.info(
        f"Job {job_id}: Extracting headings from {len(pages)} pages "
        f"(doc_type={doc_type.document_type}, two_col_pages={two_col_pages})"
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="headings",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: HeadingsOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    # Log summary
    max_level = max((h.level for h in output.headings), default=1)
    logger.info(
        f"Job {job_id}: Extracted {len(output.headings)} headings "
        f"(max depth H{max_level}), cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


# =============================================================================
# Backward-compatible class wrapper
# =============================================================================


class HeadingsAgent:
    """Heading structure extraction agent for chained analysis.

    This is a thin wrapper around module-level functions for
    backward compatibility with class-based agent patterns.
    """

    async def extract(
        self,
        pages: list[PageData],
        layouts: list[PageLayout],
        doc_type: DocumentTypeOutput,
        job_id: str,
    ) -> tuple[HeadingsOutput, LLMUsage]:
        """Extract heading structure from the document."""
        return await extract(pages, layouts, doc_type, job_id)


__all__ = [
    "Heading",
    "HeadingsAgent",
    "HeadingsOutput",
    "extract",
    "get_agent",
]
