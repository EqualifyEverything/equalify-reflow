"""Agent creation utilities with OpenTelemetry instrumentation.

Provides factory functions for creating PydanticAI agents with
standard configuration (YAML prompts, model tiers, cost tracking).
"""

import logging
from pathlib import Path
from typing import Any, TypeVar

import yaml
from opentelemetry import trace
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
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0

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


def get_tracer() -> trace.Tracer:
    """Get the agents tracer."""
    return trace.get_tracer("equalify.agents")


async def run_agent(
    agent: Agent[Any, Any],
    prompt: str | list[Any],
    job_id: str,
    agent_name: str,
    model_tier: ModelTier,
    system_prompt: str | None = None,
    **run_kwargs: Any,
) -> Any:
    """Run agent with OpenTelemetry tracing.

    Creates a span for the agent execution with relevant attributes.

    Args:
        agent: PydanticAI agent instance
        prompt: User prompt (string or list with images)
        job_id: Job ID for correlation
        agent_name: Name of the calling agent (e.g., "typography", "analysis")
        model_tier: Model tier for pricing info
        system_prompt: Optional system prompt text for telemetry logging
        **run_kwargs: Additional kwargs passed to agent.run()

    Returns:
        PydanticAI AgentRunResult
    """
    tracer = get_tracer()

    with tracer.start_as_current_span(
        f"agent.{agent_name}",
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        # Set span attributes
        span.set_attribute("job_id", job_id)
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.model_tier", model_tier.value)
        span.set_attribute("agent.model_id", MODEL_TIER_MAP[model_tier])

        # Log system prompt if provided and telemetry enabled
        if settings.telemetry_log_prompts and system_prompt:
            max_len = 32000
            span.set_attribute(
                "system_prompt.content",
                system_prompt[:max_len] + ("..." if len(system_prompt) > max_len else "")
            )
            span.set_attribute("system_prompt.length", len(system_prompt))

        # Log prompt (full content if enabled, otherwise just length)
        # For multimodal prompts, extract text portions only (skip binary image data)
        if isinstance(prompt, str):
            prompt_text = prompt
        elif isinstance(prompt, list):
            text_parts = []
            image_count = 0
            for item in prompt:
                if isinstance(item, str):
                    text_parts.append(item)
                elif hasattr(item, "data"):  # BinaryContent (image)
                    image_count += 1
                    text_parts.append(f"[IMAGE {image_count}]")
                else:
                    text_parts.append(str(item))
            prompt_text = "\n".join(text_parts)
        else:
            prompt_text = str(prompt)

        span.set_attribute("prompt.length", len(prompt_text))
        if settings.telemetry_log_prompts:
            # Truncate very long prompts to avoid span size limits (64KB typical)
            max_len = 32000
            span.set_attribute(
                "prompt.content",
                prompt_text[:max_len] + ("..." if len(prompt_text) > max_len else "")
            )

        try:
            result = await agent.run(prompt, **run_kwargs)

            # Add usage metrics to span
            usage = result.usage()
            input_tokens = usage.input_tokens or 0
            output_tokens = usage.output_tokens or 0

            span.set_attribute("tokens.input", input_tokens)
            span.set_attribute("tokens.output", output_tokens)
            span.set_attribute("tokens.total", input_tokens + output_tokens)

            # Calculate and record cost
            pricing = get_pricing_for_tier(model_tier)
            cost = calculate_estimated_cost(input_tokens, output_tokens, pricing)
            span.set_attribute("cost.cents", cost)

            # Log output if enabled
            if settings.telemetry_log_prompts:
                output_str = str(result.output)
                max_len = 32000
                span.set_attribute(
                    "output.content",
                    output_str[:max_len] + ("..." if len(output_str) > max_len else "")
                )
                span.set_attribute("output.length", len(output_str))

            span.set_status(trace.Status(trace.StatusCode.OK))
            return result

        except Exception as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
