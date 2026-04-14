"""Model tier definitions for multi-agent document processing.

This module defines the model tiers used to balance cost and capability
across different agent types in the remediation pipeline, plus the
backend-specific model ID maps consumed by ``model_factory``.

Model Tiers:
- REASONING: Claude Sonnet 4.5 - For analysis, auto-correction, complex reasoning
- EFFICIENT: Claude Haiku 4.5 - For transcription, simple tasks, bulk processing

Pricing (per 1M tokens):
- Sonnet 4.5: ~$3.00 input, ~$15.00 output (Anthropic direct and Bedrock are comparable)
- Haiku 4.5: ~$1.00 input, ~$5.00 output
"""

from enum import Enum


class ModelTier(str, Enum):
    """Model tier for cost/capability tradeoff.

    Attributes:
        REASONING: High-capability tier (Sonnet) for analysis and complex reasoning
        EFFICIENT: Cost-effective tier (Haiku) for transcription and bulk work
    """

    REASONING = "reasoning"  # Sonnet - analysis, auto-correction
    EFFICIENT = "efficient"  # Haiku - transcription, simple tasks


# Bedrock inference profile IDs for each tier.
# Claude 4.5 models on Bedrock require inference profiles (us. prefix), not on-demand IDs.
BEDROCK_TIER_MAP: dict[ModelTier, str] = {
    ModelTier.REASONING: "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ModelTier.EFFICIENT: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}


# Direct Anthropic API model IDs for each tier. Pinned to the same dates as the
# Bedrock profiles above to keep output parity between the two backends.
ANTHROPIC_TIER_MAP: dict[ModelTier, str] = {
    ModelTier.REASONING: "claude-sonnet-4-5-20250929",
    ModelTier.EFFICIENT: "claude-haiku-4-5-20251001",
}


# Backwards-compatibility alias. Historically this module exported a single
# ``MODEL_TIER_MAP`` that was implicitly the Bedrock map. The alias preserves
# that import surface for any remaining callers; new code should import
# ``BEDROCK_TIER_MAP`` or ``ANTHROPIC_TIER_MAP`` directly, or preferably use
# ``model_factory.get_model_for_tier`` which handles backend selection.
MODEL_TIER_MAP: dict[ModelTier, str] = BEDROCK_TIER_MAP


def get_model_id(tier: ModelTier) -> str:
    """Get the Bedrock inference profile ID for a given tier.

    Retained for backwards compatibility. New call sites should use
    ``model_factory.get_model_for_tier`` instead, which returns a fully
    constructed PydanticAI ``Model`` for the selected backend.

    Args:
        tier: The model tier to get the ID for

    Returns:
        The AWS Bedrock inference profile ID string

    Example:
        >>> get_model_id(ModelTier.REASONING)
        'us.anthropic.claude-sonnet-4-5-20250929-v1:0'
    """
    return BEDROCK_TIER_MAP[tier]


__all__ = [
    "ModelTier",
    "BEDROCK_TIER_MAP",
    "ANTHROPIC_TIER_MAP",
    "MODEL_TIER_MAP",
    "get_model_id",
]
