"""Document summary agent for generating DocumentSummary.

This agent performs document summarization as part of the chained analysis:
Layout → DocType → [Headings | Features | Summary] parallel → Assemble

It extracts:
- Topic understanding (what the document is about)
- Key entities (names, projects, technical terms for OCR detection)
- Domain vocabulary (helps catch OCR errors)
- Expected elements (abstract, references, figures)
- Audience level (academic, student, general)

This context is passed to all downstream agents to improve their decisions.
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
from src.shared.models.document_context import DocumentSummary
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, DocumentSummaryOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost efficiency


# =============================================================================
# Output Models
# =============================================================================


class DocumentSummaryOutput(BaseModel):
    """Output from summary agent (internal, converted to DocumentSummary)."""

    title: str = Field(description="Document title")
    topic_summary: str = Field(
        description="1-2 sentences describing what the document is about"
    )
    key_entities: list[str] = Field(
        default_factory=list,
        description="Important names, projects, technical terms (max 10)"
    )
    domain_terms: list[str] = Field(
        default_factory=list,
        description="Domain-specific vocabulary (max 10)"
    )
    expected_elements: list[str] = Field(
        default_factory=list,
        description="Expected elements: abstract, references, figures, etc."
    )
    audience_level: Literal["academic", "student", "general"] = Field(
        default="general",
        description="Target audience"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in summary")

    def to_document_summary(self, document_type: str, structure_summary: str) -> DocumentSummary:
        """Convert to DocumentSummary model.

        Args:
            document_type: Document type from doctype agent
            structure_summary: Layout description (e.g., "9 pages, two-column")

        Returns:
            DocumentSummary for downstream agents
        """
        return DocumentSummary(
            title=self.title,
            document_type=document_type,
            topic_summary=self.topic_summary,
            structure_summary=structure_summary,
            key_entities=self.key_entities[:10],  # Limit to 10
            domain_terms=self.domain_terms[:10],   # Limit to 10
            expected_elements=self.expected_elements,
            audience_level=self.audience_level,
        )


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load summary agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("summary.yaml")
    return _prompts


def get_agent() -> Agent[None, DocumentSummaryOutput]:
    """Get or create the document summary agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="summary.yaml",
            output_type=DocumentSummaryOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def summarize(
    pages: list[PageData],
    document_type: str,
    layouts: list[PageLayout],
    job_id: str,
) -> tuple[DocumentSummaryOutput, LLMUsage]:
    """Generate document summary for downstream agents.

    Args:
        pages: List of PageData with page images
        document_type: Document type from doctype classification
        layouts: Layout detection results
        job_id: Job ID for correlation

    Returns:
        Tuple of (DocumentSummaryOutput, LLMUsage)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build layout summary for context
    layout_summary = ", ".join(
        f"Page {layout.page_num}: {layout.layout_type}" for layout in layouts
    )

    # Only process first 2-3 pages for summary (cost efficiency)
    max_pages = min(3, len(pages))
    page_images = [
        BinaryContent(
            data=base64.b64decode(pages[i].image_base64),
            media_type="image/png",
        )
        for i in range(max_pages)
    ]

    user_prompt = prompts["user_prompt"].format(
        document_type=document_type,
        layout_summary=layout_summary,
        total_pages=len(pages),
    )

    prompt = [user_prompt, *page_images]

    logger.info(f"Job {job_id}: Running document summary extraction")

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="summary",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: DocumentSummaryOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.info(
        f"Job {job_id}: Summary extracted - title='{output.title[:50]}...', "
        f"entities={len(output.key_entities)}, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


def build_structure_summary(layouts: list[PageLayout], total_pages: int) -> str:
    """Build structure summary string from layouts.

    Args:
        layouts: Layout detection results
        total_pages: Total number of pages

    Returns:
        Structure summary string (e.g., "9 pages, two-column layout")
    """
    # Determine predominant layout
    two_col_count = sum(1 for layout in layouts if layout.layout_type == "two_column")
    single_col_count = sum(1 for layout in layouts if layout.layout_type == "single_column")

    if two_col_count > single_col_count:
        layout_desc = "two-column layout"
    elif two_col_count > 0:
        layout_desc = "mixed layout"
    else:
        layout_desc = "single-column layout"

    return f"{total_pages} pages, {layout_desc}"


# =============================================================================
# Backward-compatible class wrapper
# =============================================================================


class SummaryAgent:
    """Document summary agent for chained analysis.

    This is a thin wrapper around module-level functions for
    backward compatibility with class-based agent patterns.
    """

    async def summarize(
        self,
        pages: list[PageData],
        document_type: str,
        layouts: list[PageLayout],
        job_id: str,
    ) -> tuple[DocumentSummaryOutput, LLMUsage]:
        """Generate document summary."""
        return await summarize(pages, document_type, layouts, job_id)


__all__ = [
    "SummaryAgent",
    "DocumentSummaryOutput",
    "summarize",
    "get_agent",
    "build_structure_summary",
]
