"""Core shared infrastructure for all agents.

This module provides AgentCore - shared functionality for prompt loading,
model initialization, and token usage tracking across all agents.

Eliminates duplication of init, prompt loading, and cost calculation code
that was previously repeated in every agent.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.processing import LLMUsage

logger = logging.getLogger(__name__)

TOutput = TypeVar("TOutput", bound=BaseModel)


@dataclass
class AgentConfig:
    """Configuration for specialist agents.

    Attributes:
        name: Unique agent identifier (e.g., "structure_agent")
        prompts_file: Path to YAML file containing system/user prompts
        output_type: Pydantic model class for structured output
        correction_types: List of correction types this agent handles
        max_retries: Maximum retries for output validation failures
        temperature: LLM temperature setting (0.0-2.0)
        model_tier: Model tier for cost/capability tradeoff (default: EFFICIENT)
        max_tokens: Maximum tokens for LLM response (default: 16000)
    """

    name: str
    prompts_file: Path
    output_type: type[BaseModel]
    correction_types: list[str] = field(default_factory=list)
    max_retries: int = 2
    temperature: float = 0.2
    model_tier: ModelTier = ModelTier.EFFICIENT
    max_tokens: int = 16000


class AgentCore:
    """Shared infrastructure for all agents.

    Provides:
    - YAML prompt loading (fails if file missing - no fallback)
    - Model tier and ID management
    - Token usage extraction and cost calculation
    - PydanticAI agent creation and management

    Example:
        >>> config = AgentConfig(
        ...     name="test_agent",
        ...     prompts_file=Path("test.yaml"),
        ...     output_type=MyOutput,
        ...     model_tier=ModelTier.REASONING
        ... )
        >>> core = AgentCore(config)
        >>> prompts = core.prompts  # Loaded from YAML
        >>> agent = core.get_agent()  # Get or create PydanticAI agent
        >>> usage = core.create_llm_usage(result)  # From agent result
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize core infrastructure.

        Args:
            config: Agent configuration including prompts file and model settings

        Raises:
            FileNotFoundError: If prompts file does not exist (no fallback)
        """
        self.config = config
        self.model_tier = config.model_tier
        self.model_id = MODEL_TIER_MAP[config.model_tier]
        self.prompts = self._load_prompts(config.prompts_file)
        # Use Any for agent generic type since output_type varies by configuration
        # and is provided at runtime via get_agent(output_type)
        self._agent: Agent[None, Any] | None = None

        logger.debug(
            f"AgentCore initialized with tier {config.model_tier.value} ({self.model_id})"
        )

    def _load_prompts(self, prompts_file: Path) -> dict[str, Any]:
        """Load prompts from YAML file with security validation.

        Args:
            prompts_file: Path to YAML file (relative or absolute)

        Returns:
            Dictionary containing prompts (at minimum 'system_prompt')

        Raises:
            FileNotFoundError: If prompts file does not exist (fail fast, no fallback)
            ValueError: If prompts file path is outside agent_prompts_dir (path traversal),
                       if file extension is not .yaml/.yml, or if YAML syntax is invalid
        """
        if not prompts_file.is_absolute():
            base_dir = Path(settings.agent_prompts_dir).resolve()
            prompts_file = (base_dir / prompts_file).resolve()

            # SECURITY: Prevent path traversal attacks
            # Use path comparison instead of string prefix to avoid edge cases
            # (e.g., "agents_evil" would pass string check for "agents" directory)
            if base_dir not in prompts_file.parents and prompts_file != base_dir:
                logger.error(
                    f"Path traversal attempt blocked: {prompts_file}",
                    extra={
                        "security_event": "path_traversal_blocked",
                        "attempted_path": str(prompts_file),
                        "base_dir": str(base_dir),
                    },
                )
                raise ValueError(
                    f"Invalid prompts file path: must be within {base_dir}"
                )

        # SECURITY: Validate file extension
        if prompts_file.suffix.lower() not in (".yaml", ".yml"):
            logger.error(
                f"Invalid file extension blocked: {prompts_file.suffix}",
                extra={
                    "security_event": "invalid_extension_blocked",
                    "file_path": str(prompts_file),
                    "extension": prompts_file.suffix,
                },
            )
            raise ValueError(
                f"Invalid file type: {prompts_file.suffix} (must be .yaml or .yml)"
            )

        # No try/except for FileNotFoundError - let it propagate (fail fast)
        # But catch YAML parse errors to provide better context
        try:
            with open(prompts_file) as f:
                prompts: dict[str, Any] = yaml.safe_load(f)
                logger.debug(f"Loaded prompts from {prompts_file}")
                return prompts
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {prompts_file}: {e}") from e

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

    def get_agent(self, output_type: type[TOutput] | None = None) -> Agent[None, TOutput]:
        """Get or create the PydanticAI agent (lazy initialization).

        Creates the agent on first call, reuses on subsequent calls.
        This allows agent instantiation without AWS connections for testing.

        Args:
            output_type: Optional output type override (uses config.output_type if not provided)

        Returns:
            Configured PydanticAI Agent instance
        """
        if self._agent is None:
            from pydantic_ai.models.bedrock import BedrockConverseModel

            logger.debug(
                f"AgentCore: Creating BedrockConverseModel "
                f"with model {self.model_id} (tier: {self.model_tier.value})"
            )

            model = BedrockConverseModel(model_name=self.model_id)

            self._agent = Agent(
                model,
                output_type=output_type or self.config.output_type,
                system_prompt=self.prompts["system_prompt"],
                retries=self.config.max_retries,
            )

            logger.debug("AgentCore: PydanticAI Agent created successfully")

        return self._agent

    @staticmethod
    def aggregate_usage(usages: list[LLMUsage]) -> LLMUsage:
        """Aggregate multiple LLMUsage objects into one.

        Useful for combining usage from multiple agent calls (e.g., batch processing).

        Args:
            usages: List of LLMUsage objects to aggregate

        Returns:
            Single LLMUsage with summed token counts and costs
        """
        return LLMUsage(
            input_tokens=sum(u.input_tokens for u in usages),
            output_tokens=sum(u.output_tokens for u in usages),
            total_tokens=sum(u.total_tokens for u in usages),
            estimated_cost_cents=sum(u.estimated_cost_cents for u in usages),
        )

    def log_usage(self, agent_name: str, usage: LLMUsage) -> None:
        """Log token usage using structured metrics.

        Uses structured logging to enable metrics aggregation without
        exposing raw cost data in text logs. Sensitive usage data is
        included in the 'extra' dict for log processors that need it.

        Args:
            agent_name: Name of the agent for logging
            usage: LLMUsage with token counts and cost
        """
        # Use structured logging - sensitive metrics in extra dict only
        logger.debug(
            "Agent completed",
            extra={
                "agent": agent_name,
                "metrics": {
                    "tokens_in": usage.input_tokens,
                    "tokens_out": usage.output_tokens,
                    "cost_cents": usage.estimated_cost_cents,
                },
            },
        )


__all__ = ["AgentConfig", "AgentCore"]
