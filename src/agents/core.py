"""Core shared infrastructure for all agents.

This module provides AgentCore - shared functionality for prompt loading,
model initialization, and token usage tracking across all agents.

Eliminates duplication of init, prompt loading, and cost calculation code
that was previously repeated in every agent.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)


class AgentCore:
    """Shared infrastructure for all agents.

    Provides:
    - YAML prompt loading (fails if file missing - no fallback)
    - Model tier and ID management
    - Token usage extraction and cost calculation

    Example:
        >>> core = AgentCore(Path("analysis.yaml"), ModelTier.REASONING)
        >>> prompts = core.prompts  # Loaded from YAML
        >>> usage = core.create_llm_usage(result)  # From agent result
    """

    def __init__(self, prompts_file: Path, model_tier: ModelTier) -> None:
        """Initialize core infrastructure.

        Args:
            prompts_file: Path to YAML prompts file (relative to agent_prompts_dir or absolute)
            model_tier: Model tier for cost/capability tradeoff

        Raises:
            FileNotFoundError: If prompts file does not exist (no fallback)
        """
        self.model_tier = model_tier
        self.model_id = MODEL_TIER_MAP[model_tier]
        self.prompts = self._load_prompts(prompts_file)

        logger.debug(
            f"AgentCore initialized with tier {model_tier.value} ({self.model_id})"
        )

    def _load_prompts(self, prompts_file: Path) -> dict[str, Any]:
        """Load prompts from YAML file.

        Args:
            prompts_file: Path to YAML file (relative or absolute)

        Returns:
            Dictionary containing prompts (at minimum 'system_prompt')

        Raises:
            FileNotFoundError: If prompts file does not exist (fail fast, no fallback)
        """
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        # No try/except - let FileNotFoundError propagate (fail fast)
        with open(prompts_file) as f:
            prompts: dict[str, Any] = yaml.safe_load(f)
            logger.debug(f"Loaded prompts from {prompts_file}")
            return prompts

    def create_llm_usage(self, result: Any) -> LLMUsage:
        """Extract token usage and calculate cost from agent result.

        Standardizes on PydanticAI's request_tokens/response_tokens naming,
        then maps to our LLMUsage model's input_tokens/output_tokens fields.

        Args:
            result: PydanticAI agent run result (has .usage() method)

        Returns:
            LLMUsage with token counts and estimated cost
        """
        usage = result.usage()

        # PydanticAI uses request_tokens/response_tokens
        input_tokens = usage.request_tokens or 0
        output_tokens = usage.response_tokens or 0
        total_tokens = input_tokens + output_tokens

        pricing = get_pricing_for_tier(self.model_tier)
        cost = calculate_estimated_cost(input_tokens, output_tokens, pricing)

        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=cost,
        )

    def log_usage(self, agent_name: str, usage: LLMUsage) -> None:
        """Log token usage for an agent run.

        Args:
            agent_name: Name of the agent for logging
            usage: LLMUsage with token counts and cost
        """
        logger.debug(
            f"Agent {agent_name}: Completed "
            f"(tokens: {usage.input_tokens}/{usage.output_tokens}, "
            f"est. cost: ${usage.estimated_cost_cents/100:.6f})"
        )
