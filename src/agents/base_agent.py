"""Base agent framework for multi-agent document processing (PRD-011, PRD-012).

This module provides the abstract base class and configuration for all
specialist agents in the multi-agent PDF correction system.

Supports model tier selection (PRD-012) for cost/capability tradeoffs:
- REASONING tier: Claude Sonnet 4.5 for analysis, consolidation
- EFFICIENT tier: Claude Haiku 4.5 for transcription, bulk work
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings
from src.services.debug_logging_service import debug_logger
from src.shared.llm_cost import calculate_estimated_cost, get_pricing_for_tier
from src.shared.models.agent_models import AgentInput, LLMUsage

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
    """

    name: str
    prompts_file: Path
    output_type: type[BaseModel]
    correction_types: list[str] = field(default_factory=list)
    max_retries: int = 2
    temperature: float = 0.2
    model_tier: ModelTier = ModelTier.EFFICIENT


class BaseDocumentAgent(ABC, Generic[TOutput]):
    """Abstract base class for document processing agents.

    Provides common functionality shared by all specialist agents:
    - YAML prompt loading with fallback defaults
    - AWS Bedrock model initialization via PydanticAI
    - Token usage and cost tracking
    - Retry logic with output validation

    Type Parameters:
        TOutput: The Pydantic model type for agent output

    Example:
        >>> class StructureAgent(BaseDocumentAgent[StructureOutput]):
        ...     def _default_prompts(self) -> dict:
        ...         return {"system_prompt": "...", "user_prompt_template": "..."}
        ...
        ...     async def process(self, input_data: AgentInput) -> StructureOutput:
        ...         user_message = self.prompts["user_prompt_template"].format(...)
        ...         output, usage = await self._run_agent(user_message, image_bytes)
        ...         return output
    """

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the agent with configuration.

        Args:
            config: Agent configuration including prompts file and output type
        """
        self.config = config
        self.model_tier = config.model_tier
        self.model_id = MODEL_TIER_MAP[self.model_tier]

        logger.info(f"Initializing agent: {config.name}")

        # Load prompts from YAML or fallback to defaults
        self.prompts = self._load_prompts()
        logger.debug(f"Agent {config.name}: Prompts loaded")

        # Lazy initialization - agent is created on first use
        self._agent: Agent[None, TOutput] | None = None
        logger.info(
            f"Agent {config.name} initialized with model tier {self.model_tier.value} "
            f"({self.model_id}) (handles: {config.correction_types}, retries: {config.max_retries})"
        )

    @property
    def name(self) -> str:
        """Get the agent's unique identifier."""
        return self.config.name

    @property
    def correction_types(self) -> list[str]:
        """Get the correction types this agent handles."""
        return self.config.correction_types

    def _load_prompts(self) -> dict[str, Any]:
        """Load prompts from YAML configuration file.

        Attempts to load prompts from the configured YAML file.
        Falls back to _default_prompts() if file not found.

        Returns:
            Dictionary containing at minimum 'system_prompt' and optionally
            'user_prompt_template' keys.
        """
        prompts_file = self.config.prompts_file

        # Check if path is relative to agent prompts dir
        if not prompts_file.is_absolute():
            prompts_file = Path(settings.agent_prompts_dir) / prompts_file

        try:
            with open(prompts_file) as f:
                prompts = yaml.safe_load(f)
                logger.debug(f"Agent {self.name}: Loaded prompts from {prompts_file}")
                return dict(prompts)
        except FileNotFoundError:
            logger.warning(
                f"Agent {self.name}: Prompts file not found at {prompts_file}, "
                "using default prompts"
            )
            return self._default_prompts()

    @abstractmethod
    def _default_prompts(self) -> dict[str, Any]:
        """Provide fallback prompts when YAML file is not found.

        Subclasses must implement this to provide default prompts.

        Returns:
            Dictionary with at minimum 'system_prompt' key, and optionally
            'user_prompt_template' for formatting with input data.
        """
        pass

    def _get_agent(self) -> Agent[None, TOutput]:
        """Get or create the PydanticAI agent (lazy initialization).

        Creates the agent on first call, reuses on subsequent calls.
        This allows agent instantiation without AWS connections for testing.

        Returns:
            Configured PydanticAI Agent instance
        """
        if self._agent is None:
            self._agent = self._create_agent()
        return self._agent

    def _create_agent(self) -> Agent[None, TOutput]:
        """Create PydanticAI agent with AWS Bedrock backend.

        Initializes the Agent with:
        - BedrockConverseModel for Claude access (model based on tier)
        - Structured output type from config
        - System prompt from loaded prompts
        - Retry configuration

        Returns:
            Configured PydanticAI Agent instance
        """
        from pydantic_ai.models.bedrock import BedrockConverseModel

        logger.debug(
            f"Agent {self.name}: Creating BedrockConverseModel "
            f"with model {self.model_id} (tier: {self.model_tier.value})"
        )

        model = BedrockConverseModel(model_name=self.model_id)

        agent: Agent[None, TOutput] = Agent(
            model,
            output_type=self.config.output_type,  # type: ignore[arg-type]
            system_prompt=self.prompts["system_prompt"],
            retries=self.config.max_retries,
        )

        logger.debug(f"Agent {self.name}: PydanticAI Agent created successfully")
        return agent

    def _calculate_estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in cents using tier-based pricing.

        Uses the centralized calculate_estimated_cost function with pricing
        based on the agent's model tier (Sonnet for REASONING, Haiku for EFFICIENT).

        Args:
            input_tokens: Number of input tokens consumed
            output_tokens: Number of output tokens generated

        Returns:
            Estimated cost in cents (float)
        """
        pricing = get_pricing_for_tier(self.model_tier)
        return calculate_estimated_cost(input_tokens, output_tokens, pricing)

    @abstractmethod
    async def process(self, input_data: AgentInput) -> TOutput:
        """Process page input and return structured output.

        Subclasses must implement this method to define their specific
        processing logic.

        Args:
            input_data: Unified input containing page markdown, image, context, and hints

        Returns:
            Typed output matching the agent's output_type configuration
        """
        pass

    async def _run_agent(
        self,
        user_message: str,
        image_bytes: bytes | None = None,
        job_id: str | None = None,
        page_num: int | None = None,
    ) -> tuple[TOutput, LLMUsage]:
        """Execute the PydanticAI agent and return output with usage metrics.

        Sends the user message (and optional image) to the LLM and returns
        both the structured output and token usage information.

        Args:
            user_message: Formatted prompt for the LLM
            image_bytes: Optional PNG image bytes for multimodal input
            job_id: Optional job ID for debug logging correlation
            page_num: Optional page number for debug image naming

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
            f"Agent {self.name}: Running with message length {len(user_message)}, "
            f"image: {image_bytes is not None}"
        )

        # Save debug image if enabled
        if image_bytes and job_id:
            debug_logger.save_debug_image(
                job_id=job_id,
                agent_name=self.name,
                image_bytes=image_bytes,
                page_num=page_num,
            )

        # Debug log: prompt being sent
        image_info = None
        if image_bytes and settings.debug_log_images:
            image_info = {
                "size_bytes": len(image_bytes),
                "format": "png",
            }

        debug_logger.log_prompt(
            job_id=job_id or "unknown",
            agent_name=self.name,
            system_prompt=self.prompts.get("system_prompt"),
            user_message=user_message,
            image_info=image_info,
            model_id=self.model_id,
            model_tier=self.model_tier.value,
            temperature=self.config.temperature,
            max_tokens=settings.claude_max_tokens,
        )

        # Get agent (lazy initialization) and execute
        start_time = time.time()
        agent = self._get_agent()
        result = await agent.run(
            messages,
            model_settings={
                "max_tokens": settings.claude_max_tokens,
                "temperature": self.config.temperature,
            },
        )
        duration_ms = (time.time() - start_time) * 1000

        # Extract usage metrics
        usage = result.usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        total_tokens = input_tokens + output_tokens
        estimated_cost_cents = self._calculate_estimated_cost(input_tokens, output_tokens)

        llm_usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
        )

        # Debug log: response received
        # Get raw response text if available
        raw_response_text = None
        if result.all_messages():
            last_message = result.all_messages()[-1]
            if hasattr(last_message, "content"):
                raw_response_text = str(last_message.content)

        debug_logger.log_response(
            job_id=job_id or "unknown",
            agent_name=self.name,
            response_text=raw_response_text,
            parsed_output=result.output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_cents=estimated_cost_cents,
            duration_ms=duration_ms,
            model_id=self.model_id,
        )

        logger.debug(
            f"Agent {self.name}: Completed "
            f"(tokens: {input_tokens}/{output_tokens}, est. cost: ${estimated_cost_cents/100:.6f})"
        )

        return result.output, llm_usage
