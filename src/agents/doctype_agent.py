"""Document type classification agent for chained analysis pipeline.

This agent performs the SECOND step of the chained analysis:
Document type classification based on visual appearance and layout context.
"""

from __future__ import annotations

import base64
import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.layout_agent import PageLayout
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, DocumentTypeOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for classification


# =============================================================================
# Output Models
# =============================================================================

DocumentType = Literal[
    "syllabus",
    "lecture_notes",
    "exam",
    "research_paper",
    "handout",
    "thesis",
    "report",
    "other",
]


class DocumentTypeOutput(BaseModel):
    """Output from document type classification agent."""

    document_type: DocumentType = Field(description="Classified document type")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in classification"
    )
    evidence: str = Field(
        description="Key visual indicators for this classification"
    )


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load doctype agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("doctype.yaml")
    return _prompts


def get_agent() -> Agent[None, DocumentTypeOutput]:
    """Get or create the document type classification agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="doctype.yaml",
            output_type=DocumentTypeOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def classify(
    pages: list[PageData],
    layouts: list[PageLayout],
    job_id: str,
) -> tuple[DocumentTypeOutput, LLMUsage]:
    """Classify the document type.

    Args:
        pages: List of PageData with page images
        layouts: Layout detection results from LayoutAgent
        job_id: Job ID for correlation

    Returns:
        Tuple of (DocumentTypeOutput, LLMUsage)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build layout summary for context
    layout_summary = ", ".join(
        f"Page {layout.page_num}: {layout.layout_type}" for layout in layouts
    )

    # Only need first page (and optionally last) for classification
    page_images = [
        BinaryContent(
            data=base64.b64decode(pages[0].image_base64),
            media_type="image/png",
        )
    ]
    if len(pages) > 1:
        page_images.append(
            BinaryContent(
                data=base64.b64decode(pages[-1].image_base64),
                media_type="image/png",
            )
        )

    user_prompt = prompts["user_prompt"].format(layout_summary=layout_summary)

    prompt = [user_prompt, *page_images]

    logger.info(f"Job {job_id}: Running document type classification")

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="doctype",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: DocumentTypeOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.info(
        f"Job {job_id}: Document type: {output.document_type} "
        f"(conf={output.confidence:.2f}), cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


# =============================================================================
# Backward-compatible class wrapper
# =============================================================================


class DocTypeAgent:
    """Document type classification agent for chained analysis.

    This is a thin wrapper around module-level functions for
    backward compatibility with class-based agent patterns.
    """

    async def classify(
        self,
        pages: list[PageData],
        layouts: list[PageLayout],
        job_id: str,
    ) -> tuple[DocumentTypeOutput, LLMUsage]:
        """Classify the document type."""
        return await classify(pages, layouts, job_id)


__all__ = [
    "DocTypeAgent",
    "DocumentType",
    "DocumentTypeOutput",
    "classify",
    "get_agent",
]
