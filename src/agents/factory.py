"""Agent creation utilities.

Provides factory functions for creating PydanticAI agents with
standard configuration (YAML prompts, model tiers, cost tracking).
"""

import logging
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

TOutput = TypeVar("TOutput", bound=BaseModel)


def load_prompts(filename: str) -> dict[str, Any]:
    """Load prompts from YAML with security validation.

    Args:
        filename: YAML filename in config/agents/ directory

    Returns:
        Dictionary containing prompts

    Raises:
        FileNotFoundError: If prompts file does not exist
        ValueError: If path traversal attempted or invalid extension
    """
    base_dir = Path(settings.agent_prompts_dir).resolve()
    prompts_file = (base_dir / filename).resolve()

    # Security: Prevent path traversal
    if base_dir not in prompts_file.parents and prompts_file != base_dir:
        raise ValueError(f"Invalid prompts file path: must be within {base_dir}")

    # Security: Validate extension
    if prompts_file.suffix.lower() not in (".yaml", ".yml"):
        raise ValueError(f"Invalid file type: {prompts_file.suffix}")

    with open(prompts_file) as f:
        return yaml.safe_load(f)  # type: ignore[no-any-return]


def create_agent(
    prompts_file: str,
    output_type: type[TOutput],
    model_tier: ModelTier = ModelTier.EFFICIENT,
    use_deps: bool = False,
    max_retries: int = 2,
) -> Agent[Any, TOutput]:
    """Create a PydanticAI agent with standard configuration.

    Args:
        prompts_file: YAML filename for prompts
        output_type: Pydantic model for structured output
        model_tier: REASONING (Sonnet) or EFFICIENT (Haiku)
        use_deps: Enable AgentDependencies for dynamic instructions
        max_retries: Retry count for validation failures

    Returns:
        Configured PydanticAI Agent instance
    """
    prompts = load_prompts(prompts_file)
    model = BedrockConverseModel(MODEL_TIER_MAP[model_tier])

    if use_deps:
        from src.agents.dependencies import AgentDependencies
        return Agent(
            model,
            deps_type=AgentDependencies,
            output_type=output_type,
            system_prompt=prompts["system_prompt"],
            retries=max_retries,
        )

    return Agent(
        model,
        output_type=output_type,
        system_prompt=prompts["system_prompt"],
        retries=max_retries,
    )


def extract_usage(result: Any, model_tier: ModelTier) -> LLMUsage:
    """Extract token usage and calculate cost from agent result.

    Args:
        result: PydanticAI agent run result
        model_tier: Model tier for pricing lookup

    Returns:
        LLMUsage with token counts and cost
    """
    usage = result.usage()
    input_tokens = usage.request_tokens or 0
    output_tokens = usage.response_tokens or 0

    pricing = get_pricing_for_tier(model_tier)
    cost = calculate_estimated_cost(input_tokens, output_tokens, pricing)

    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_cents=cost,
    )


def aggregate_usage(usages: list[LLMUsage]) -> LLMUsage:
    """Aggregate multiple LLMUsage objects into one."""
    if not usages:
        return LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0,
        )
    return LLMUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        total_tokens=sum(u.total_tokens for u in usages),
        estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
    )
