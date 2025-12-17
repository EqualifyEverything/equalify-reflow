"""Table structure detection agent for chained tables analysis.

Step 1 of the tables chain: Detect table structure visually.
Uses Haiku (EFFICIENT) for cost-effective visual detection.

Detects:
- Complexity (simple, merged_cells, nested, irregular)
- Header presence and type
- Row/column counts
- Merged cell locations
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
_agent: Agent[None, TableStructureOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective detection


# =============================================================================
# Output Models
# =============================================================================


TableComplexity = Literal["simple", "merged_cells", "nested", "irregular"]
HeaderStructure = Literal["single_row", "multi_row", "column_headers", "both", "none"]


class TableStructure(BaseModel):
    """Structure detection result for a single table."""

    table_index: int = Field(ge=1, description="Table number on this page (1-indexed)")
    complexity: TableComplexity = Field(
        description="Complexity: simple, merged_cells, nested, or irregular"
    )
    complexity_reasoning: str = Field(
        description="Brief explanation of complexity assessment"
    )
    has_headers: bool = Field(description="Whether table has identifiable headers")
    header_structure: HeaderStructure = Field(
        description="Type of header structure detected"
    )
    row_count: int = Field(ge=0, description="Approximate number of data rows")
    column_count: int = Field(ge=0, description="Number of columns")
    has_merged_cells: bool = Field(default=False, description="Whether merged cells exist")
    merged_cell_locations: list[str] = Field(
        default_factory=list,
        description="Descriptions of merged cell locations (e.g., 'row 1 spans all columns')",
    )
    visual_description: str = Field(
        min_length=1, description="Brief description of what the table shows"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Detection confidence")


class TableStructureOutput(BaseModel):
    """Output from table structure detection for a page."""

    page_num: int = Field(ge=1, description="Page number")
    tables_found: int = Field(ge=0, description="Number of tables detected")
    structures: list[TableStructure] = Field(default_factory=list)


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load structure agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("tables_structure.yaml")
    return _prompts


def get_agent() -> Agent[None, TableStructureOutput]:
    """Get or create the structure detection agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="tables_structure.yaml",
            output_type=TableStructureOutput,
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


async def detect_structure(
    page_num: int,
    image_base64: str,
    expected_table_count: int,
    page_markdown: str,
    job_id: str,
) -> tuple[TableStructureOutput, LLMUsage]:
    """Detect table structure on a single page.

    Args:
        page_num: Page number (1-indexed)
        image_base64: Base64-encoded page image
        expected_table_count: Number of tables expected on this page
        page_markdown: Markdown content for this page
        job_id: Job ID for correlation

    Returns:
        Tuple of (structure output, usage metrics)
    """
    agent = get_agent()
    prompts = get_prompts()

    # Decode image for multimodal input
    image_bytes = base64.b64decode(image_base64)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        expected_table_count=expected_table_count,
        page_markdown=page_markdown[:3000],  # Tables need more context
    )

    # Build multimodal prompt
    prompt = [
        user_prompt,
        BinaryContent(data=image_bytes, media_type="image/png"),
    ]

    logger.debug(
        f"Job {job_id}: Detecting table structure on page {page_num} "
        f"(expecting {expected_table_count} tables)"
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        job_id=job_id,
        agent_name="tables_structure",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: TableStructureOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} structure detection complete - "
        f"{len(output.structures)} tables analyzed, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "HeaderStructure",
    "TableComplexity",
    "TableStructure",
    "TableStructureOutput",
    "detect_structure",
    "get_agent",
    "reset_agent",
]
