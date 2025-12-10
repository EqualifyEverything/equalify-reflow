"""Centralized LLM cost configuration and calculation.

This module provides a single source of truth for LLM pricing and cost calculations,
making it easy to update pricing when AWS Bedrock rates change.

Supported Models (as of January 2025):

Claude Haiku 4.5 via AWS Bedrock:
- Input: $1.00 per 1M tokens
- Output: $5.00 per 1M tokens

Claude Sonnet 4.5 via AWS Bedrock:
- Input: $3.00 per 1M tokens
- Output: $15.00 per 1M tokens

Prices are stored in cents per token for precision and to avoid floating point errors
when working with small per-token costs.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.model_tiers import ModelTier


@dataclass
class LLMPricing:
    """LLM pricing configuration in cents per token.

    Stores pricing at the per-token level in cents to maintain precision
    and avoid floating point errors in cost calculations.

    Attributes:
        input_cost_per_token_cents: Cost per input token in cents
        output_cost_per_token_cents: Cost per output token in cents
        model_name: Human-readable model identifier for reference

    Example:
        >>> # Claude Haiku 4.5 pricing
        >>> pricing = LLMPricing(
        ...     input_cost_per_token_cents=0.0001,   # $1.00/1M tokens
        ...     output_cost_per_token_cents=0.0005,  # $5.00/1M tokens
        ...     model_name="Claude Haiku 4.5 (Bedrock)"
        ... )
    """

    input_cost_per_token_cents: float
    output_cost_per_token_cents: float
    model_name: str = "Unknown Model"


# Default pricing for Claude Haiku 4.5 via AWS Bedrock (EFFICIENT tier)
# Updated: January 2025
# Source: https://aws.amazon.com/bedrock/pricing/
DEFAULT_PRICING = LLMPricing(
    input_cost_per_token_cents=0.0001,  # $1.00 per 1M tokens = $0.000001/token = 0.0001 cents/token
    output_cost_per_token_cents=0.0005,  # $5.00 per 1M tokens = $0.000005/token = 0.0005 cents/token
    model_name="Claude Haiku 4.5 (Bedrock)",
)

# Pricing for Claude Sonnet 4.5 via AWS Bedrock (REASONING tier)
# Updated: January 2025
# Source: https://aws.amazon.com/bedrock/pricing/
SONNET_PRICING = LLMPricing(
    input_cost_per_token_cents=0.0003,  # $3.00 per 1M tokens = $0.000003/token = 0.0003 cents/token
    output_cost_per_token_cents=0.0015,  # $15.00 per 1M tokens = $0.000015/token = 0.0015 cents/token
    model_name="Claude Sonnet 4.5 (Bedrock)",
)

# Alias for clarity
HAIKU_PRICING = DEFAULT_PRICING


def get_pricing_for_tier(tier: "ModelTier") -> LLMPricing:
    """Get pricing configuration for a model tier.

    Args:
        tier: The model tier (REASONING or EFFICIENT)

    Returns:
        LLMPricing configuration for the tier

    Example:
        >>> from src.agents.model_tiers import ModelTier
        >>> pricing = get_pricing_for_tier(ModelTier.REASONING)
        >>> print(pricing.model_name)
        'Claude Sonnet 4.5 (Bedrock)'
    """
    # Import here to avoid circular imports
    from src.agents.model_tiers import ModelTier

    if tier == ModelTier.REASONING:
        return SONNET_PRICING
    return HAIKU_PRICING


def calculate_estimated_cost(
    input_tokens: int,
    output_tokens: int,
    pricing: LLMPricing = DEFAULT_PRICING,
) -> float:
    """Calculate estimated LLM cost in cents based on token usage.

    Uses the provided pricing configuration (or defaults to Claude Haiku 4.5 pricing)
    to calculate the total cost in cents.

    Args:
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens generated
        pricing: LLM pricing configuration (defaults to Claude Haiku 4.5)

    Returns:
        Estimated cost in cents (float)

    Example:
        >>> # Calculate cost for 1000 input tokens and 100 output tokens
        >>> cost = calculate_estimated_cost(1000, 100)
        >>> print(f"Cost: ${cost/100:.6f}")
        Cost: $0.001500

        >>> # Calculate with custom pricing
        >>> custom_pricing = LLMPricing(
        ...     input_cost_per_token_cents=0.0002,
        ...     output_cost_per_token_cents=0.001,
        ...     model_name="Custom Model"
        ... )
        >>> cost = calculate_estimated_cost(1000, 100, custom_pricing)
        >>> print(f"Cost: ${cost/100:.6f}")
        Cost: $0.003000

        >>> # Calculate with Sonnet pricing
        >>> cost = calculate_estimated_cost(1000, 100, SONNET_PRICING)
        >>> print(f"Cost: ${cost/100:.6f}")
        Cost: $0.004500
    """
    input_cost = input_tokens * pricing.input_cost_per_token_cents
    output_cost = output_tokens * pricing.output_cost_per_token_cents
    return input_cost + output_cost


def format_cost_dollars(cost_cents: float) -> str:
    """Format cost in cents as dollar string with appropriate precision.

    Args:
        cost_cents: Cost in cents

    Returns:
        Formatted cost string (e.g., "$0.001500" or "$1.23")

    Example:
        >>> format_cost_dollars(0.15)
        '$0.0015'
        >>> format_cost_dollars(150.0)
        '$1.50'
    """
    dollars = cost_cents / 100
    if dollars < 0.01:
        # Use more precision for very small costs
        return f"${dollars:.6f}"
    else:
        return f"${dollars:.2f}"


__all__ = [
    "LLMPricing",
    "DEFAULT_PRICING",
    "SONNET_PRICING",
    "HAIKU_PRICING",
    "get_pricing_for_tier",
    "calculate_estimated_cost",
    "format_cost_dollars",
]
