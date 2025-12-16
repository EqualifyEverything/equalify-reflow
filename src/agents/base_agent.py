"""Base agent framework for multi-agent document processing.

This module provides the abstract base class and configuration for all
specialist agents in the multi-agent PDF correction system.

Supports model tier selection for cost/capability tradeoffs:
- REASONING tier: Claude Sonnet 4.5 for analysis, consolidation
- EFFICIENT tier: Claude Haiku 4.5 for transcription, bulk work

Supports dynamic instructions via AgentDependencies for runtime context injection.
"""

import logging
import warnings
from abc import ABC
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.core import AgentConfig, AgentCore
from src.shared.models.agent_models import LLMUsage

if TYPE_CHECKING:
    from src.agents.dependencies import AgentDependencies
    from src.agents.model_tiers import ModelTier

logger = logging.getLogger(__name__)

TOutput = TypeVar("TOutput", bound=BaseModel)


# Re-export AgentConfig from core for backward compatibility
# (existing code imports from base_agent.py)
__all__ = ["AgentConfig", "BaseDocumentAgent"]


class BaseDocumentAgent(ABC, Generic[TOutput]):
    """Abstract base class for document processing agents.

    Provides common functionality shared by all specialist agents:
    - YAML prompt loading with fallback defaults
    - AWS Bedrock model initialization via PydanticAI
    - Token usage and cost tracking
    - Retry logic with output validation

    Uses composition pattern with AgentCore to eliminate code duplication.

    Type Parameters:
        TOutput: The Pydantic model type for agent output

    Example:
        >>> class StructureAgent(BaseDocumentAgent[StructureOutput]):
        ...     async def analyze(self, pages: list[PageData], ...) -> tuple[list[Observation], LLMUsage]:
        ...         user_message = self.prompts["user_prompt_template"].format(...)
        ...         output, usage = await self._run_agent(user_message, image_bytes)
        ...         return self._convert_to_observations(output), usage
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the agent with configuration.

        Args:
            config: Agent configuration including prompts file and output type
        """
        self.config = config
        self._core = AgentCore(config)

        logger.info(f"Initializing agent: {config.name}")
        logger.debug(f"Agent {config.name}: Prompts loaded")
        logger.info(
            f"Agent {config.name} initialized with model tier {self._core.model_tier.value} "
            f"({self._core.model_id}) (handles: {config.correction_types}, retries: {config.max_retries})"
        )

    @property
    def name(self) -> str:
        """Get the agent's unique identifier."""
        return self.config.name

    @property
    def correction_types(self) -> list[str]:
        """Get the correction types this agent handles."""
        return self.config.correction_types

    @property
    def model_tier(self) -> "ModelTier":
        """Get the model tier from core."""
        return self._core.model_tier

    @property
    def model_id(self) -> str:
        """Get the model ID from core."""
        return self._core.model_id

    @property
    def prompts(self) -> dict[str, Any]:
        """Get the loaded prompts from core."""
        return self._core.prompts

    def _get_agent(self) -> Agent[None, TOutput]:
        """Get or create the PydanticAI agent (lazy initialization).

        Delegates to AgentCore for agent creation and management.

        Returns:
            Configured PydanticAI Agent instance
        """
        return self._core.get_agent(self.config.output_type)  # type: ignore[arg-type]

    def _calculate_estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in cents using tier-based pricing.

        .. deprecated::
            Use `self._core.create_llm_usage(result).estimated_cost_cents` instead.
            This method will be removed in a future version.

        Args:
            input_tokens: Number of input tokens consumed
            output_tokens: Number of output tokens generated

        Returns:
            Estimated cost in cents (float)
        """
        warnings.warn(
            "_calculate_estimated_cost is deprecated. "
            "Use self._core.create_llm_usage(result).estimated_cost_cents instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier

        pricing = get_pricing_for_tier(self.model_tier)
        return calculate_estimated_cost(input_tokens, output_tokens, pricing)

    async def _run_agent(
        self,
        user_message: str,
        image_bytes: bytes | None = None,
    ) -> tuple[TOutput, LLMUsage]:
        """Execute the PydanticAI agent and return output with usage metrics.

        Sends the user message (and optional image) to the LLM and returns
        both the structured output and token usage information.

        Args:
            user_message: Formatted prompt for the LLM
            image_bytes: Optional PNG image bytes for multimodal input

        Returns:
            Tuple of (typed output, LLMUsage with token counts and cost)

        Raises:
            Exception: If agent execution fails after all retries
        """
        # Build message list
        messages: list[str | BinaryContent] = [user_message]
        if image_bytes:
            messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

        logger.debug(
            f"Agent {self.name}: Running with message length {len(user_message)}, image: {image_bytes is not None}"
        )

        # Get agent (lazy initialization) and execute
        agent = self._get_agent()
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": self.config.max_tokens,  # Use config, not settings
                "temperature": self.config.temperature,
            },
        )

        # Use core for usage extraction
        llm_usage = self._core.create_llm_usage(result)

        logger.debug(
            f"Agent {self.name}: Completed "
            f"(tokens: {llm_usage.input_tokens}/{llm_usage.output_tokens}, "
            f"est. cost: ${llm_usage.estimated_cost_cents / 100:.6f})"
        )

        return result.output, llm_usage

    async def _run_with_deps(
        self,
        user_message: str,
        deps: "AgentDependencies",
        image_bytes: bytes | None = None,
    ) -> tuple[TOutput, LLMUsage]:
        """Execute the PydanticAI agent with dependencies for dynamic instructions.

        Similar to _run_agent() but passes AgentDependencies to enable
        dynamic instruction injection via @agent.instructions decorators.

        Requires config.use_deps=True to be set when creating the agent.

        Args:
            user_message: Formatted prompt for the LLM
            deps: Runtime dependencies for dynamic instruction generation
            image_bytes: Optional PNG image bytes for multimodal input

        Returns:
            Tuple of (typed output, LLMUsage with token counts and cost)

        Raises:
            Exception: If agent execution fails after all retries
            ValueError: If called on an agent created without use_deps=True
        """
        if not self.config.use_deps:
            raise ValueError(
                f"Agent {self.name} was not created with use_deps=True. "
                "Cannot use _run_with_deps() without dependency injection enabled."
            )

        # Build message list
        messages: list[str | BinaryContent] = [user_message]
        if image_bytes:
            messages.append(BinaryContent(data=image_bytes, media_type="image/png"))

        logger.debug(
            f"Agent {self.name}: Running with deps (job_id={deps.job_id}, "
            f"attempt={deps.attempt_number}, message_len={len(user_message)}, "
            f"image={image_bytes is not None})"
        )

        # Get agent (lazy initialization) and execute with deps
        agent = self._get_agent()
        result = await agent.run(  # type: ignore[call-overload]
            user_prompt=messages,
            deps=deps,
            model_settings={
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            },
        )

        # Use core for usage extraction
        llm_usage = self._core.create_llm_usage(result)

        logger.debug(
            f"Agent {self.name}: Completed with deps "
            f"(tokens: {llm_usage.input_tokens}/{llm_usage.output_tokens}, "
            f"est. cost: ${llm_usage.estimated_cost_cents / 100:.6f})"
        )

        return result.output, llm_usage
