"""Model tier definitions for multi-agent document processing.

This module defines the model tiers used to balance cost and capability
across different agent types in the remediation pipeline.

Model Tiers:
- REASONING: Claude Sonnet 4.5 - For analysis, consolidation, complex reasoning
- EFFICIENT: Claude Haiku 4.5 - For transcription, simple tasks, bulk processing

Pricing (per 1M tokens via AWS Bedrock):
- Sonnet 4.5: $3.00 input, $15.00 output
- Haiku 4.5: $1.00 input, $5.00 output
"""

from enum import Enum


class ModelTier(str, Enum):
    """Model tier for cost/capability tradeoff.

    Attributes:
        REASONING: High-capability tier (Sonnet) for analysis and complex reasoning
        EFFICIENT: Cost-effective tier (Haiku) for transcription and bulk work
    """

    REASONING = "reasoning"  # Sonnet - analysis, consolidation
    EFFICIENT = "efficient"  # Haiku - transcription, simple tasks


# Bedrock inference profile IDs for each tier
# Note: Claude 4.5 models require inference profiles (us. prefix), not on-demand model IDs
MODEL_TIER_MAP: dict[ModelTier, str] = {
    ModelTier.REASONING: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ModelTier.EFFICIENT: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


def get_model_id(tier: ModelTier) -> str:
    """Get the Bedrock model ID for a given tier.

    Args:
        tier: The model tier to get the ID for

    Returns:
        The AWS Bedrock model ID string

    Example:
        >>> get_model_id(ModelTier.REASONING)
        'us.anthropic.claude-sonnet-4-5-20250929-v1:0'
    """
    return MODEL_TIER_MAP[tier]


__all__ = [
    "ModelTier",
    "MODEL_TIER_MAP",
    "get_model_id",
]
