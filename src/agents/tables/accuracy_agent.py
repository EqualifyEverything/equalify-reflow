"""Data accuracy assessment agent for chained tables analysis.

Step 2 of the tables chain: Assess markdown accuracy.
Uses Haiku (EFFICIENT) for cost-effective assessment.

IMPORTANT: This is TEXT-ONLY - no image tokens!
Structure detection (Step 1) provides visual context.
This step only analyzes the markdown representation.

Assesses:
- Data completeness
- Structural preservation
- Missing or corrupted data
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.tables.structure_agent import TableStructure
from src.agents.model_tiers import ModelTier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, DataAccuracyOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective assessment


# =============================================================================
# Output Models
# =============================================================================


DataAccuracyLevel = Literal["accurate", "partial", "missing_data", "structural_loss"]


class DataAccuracy(BaseModel):
    """Accuracy assessment result for a single table."""

    table_index: int = Field(ge=1, description="Table number on this page (1-indexed)")
    accuracy: DataAccuracyLevel = Field(
        description="Accuracy level: accurate, partial, missing_data, or structural_loss"
    )
    completeness_score: float = Field(
        ge=0.0, le=1.0, description="Estimated data completeness (0-1)"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Specific issues found in the markdown representation",
    )
    missing_headers: bool = Field(
        default=False, description="Whether header row is missing from markdown"
    )
    malformed_cells: int = Field(
        default=0, ge=0, description="Count of cells that appear malformed"
    )
    assessment_notes: str = Field(
        default="", description="Additional notes about the assessment"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Assessment confidence")


class DataAccuracyOutput(BaseModel):
    """Output from accuracy assessment for a page."""

    page_num: int = Field(ge=1, description="Page number")
    assessments: list[DataAccuracy] = Field(default_factory=list)


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load accuracy agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("tables_accuracy.yaml")
    return _prompts


def get_agent() -> Agent[None, DataAccuracyOutput]:
    """Get or create the accuracy assessment agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="tables_accuracy.yaml",
            output_type=DataAccuracyOutput,
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


async def assess_accuracy(
    page_num: int,
    structures: list[TableStructure],
    page_markdown: str,
    job_id: str,
) -> tuple[DataAccuracyOutput, LLMUsage]:
    """Assess markdown accuracy for tables on a page.

    NOTE: This is TEXT-ONLY - no image tokens are used.
    Structure detection provides visual context via the structures parameter.

    Skips assessment for simple tables with headers (they're usually fine).

    Args:
        page_num: Page number (1-indexed)
        structures: Structure detection results from Step 1
        page_markdown: Markdown content for this page
        job_id: Job ID for correlation

    Returns:
        Tuple of (accuracy output, usage metrics)
    """
    # Filter to tables that need accuracy assessment
    # Simple tables with headers are usually fine - skip them
    tables_needing_assessment = [
        s for s in structures
        if s.complexity != "simple" or not s.has_headers
    ]

    if not tables_needing_assessment:
        # All tables are simple with headers - return empty result
        logger.debug(
            f"Job {job_id}: Page {page_num} - all {len(structures)} tables are simple with headers, skipping accuracy assessment"
        )
        return (
            DataAccuracyOutput(page_num=page_num, assessments=[]),
            LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_cents=0.0,
            ),
        )

    agent = get_agent()
    prompts = get_prompts()

    # Format structure context for prompt (from Step 1)
    structure_context = "\n".join(
        f"- Table {s.table_index}: {s.complexity}, "
        f"headers={s.header_structure}, "
        f"{s.row_count} rows x {s.column_count} cols, "
        f"merged={s.has_merged_cells}"
        for s in tables_needing_assessment
    )

    # Build user prompt (TEXT-ONLY - no image!)
    user_prompt = prompts["user_prompt"].format(
        page_num=page_num,
        structure_context=structure_context,
        table_count=len(tables_needing_assessment),
        page_markdown=page_markdown,  # Full markdown for tables
    )

    logger.debug(
        f"Job {job_id}: Assessing accuracy for {len(tables_needing_assessment)} tables on page {page_num}"
    )

    result = await run_agent(
        agent=agent,
        prompt=user_prompt,  # Text-only prompt
        job_id=job_id,
        agent_name="tables_accuracy",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: DataAccuracyOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    logger.debug(
        f"Job {job_id}: Page {page_num} accuracy assessment complete - "
        f"{len(output.assessments)} tables assessed, "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return output, usage


__all__ = [
    "DataAccuracy",
    "DataAccuracyLevel",
    "DataAccuracyOutput",
    "assess_accuracy",
    "get_agent",
    "reset_agent",
]
