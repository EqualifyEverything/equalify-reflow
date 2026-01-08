"""Subagent tools for domain agents.

Each subagent is a specialized LLM that returns structured recommendations
with confidence scores. Parent agents review recommendations and decide
whether to apply edits via propose_edit().
"""

from pydantic import BaseModel, Field

# =============================================================================
# Confidence Thresholds
# =============================================================================

CONFIDENCE_AUTO_APPLY = 0.8  # Apply automatically
CONFIDENCE_APPLY_WITH_REVIEW = 0.5  # Apply but flag for review
CONFIDENCE_SKIP = 0.5  # Below this, skip the edit


# =============================================================================
# Base Result Type
# =============================================================================


class SubagentResult(BaseModel):
    """Base class for all subagent results.

    All subagent tools return a result that includes:
    - confidence: How confident the subagent is in its recommendation (0.0-1.0)
    - reasoning: Explanation of what was found and why the recommendation was made

    Parent agents use confidence to decide:
    - >= CONFIDENCE_AUTO_APPLY: Apply via propose_edit(needs_review=False)
    - >= CONFIDENCE_APPLY_WITH_REVIEW: Apply via propose_edit(needs_review=True)
    - < CONFIDENCE_SKIP: Skip the edit, log for manual review
    """

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in recommendation (0.0-1.0)",
    )
    reasoning: str = Field(
        description="Explanation of the recommendation",
    )


__all__ = [
    # Constants
    "CONFIDENCE_AUTO_APPLY",
    "CONFIDENCE_APPLY_WITH_REVIEW",
    "CONFIDENCE_SKIP",
    # Base type
    "SubagentResult",
]
