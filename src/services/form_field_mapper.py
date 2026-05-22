"""Reusable Form Field Mapper agent invocation.

Both the pipeline step (``PipelineViewerService._step_form_field_map``) and the
accessible-form builder need to run the same agent over document markdown. This
function is the single place that constructs and runs it, so the prompt, output
type, and model tier never drift between the two call sites.

Returns the validated output plus token counts; raises on failure so callers can
decide how to record or degrade.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_form_field_mapper(markdown: str, *, infer: bool = False) -> tuple[object, int, int]:
    """Run the Form Field Mapper agent on document markdown.

    Args:
        markdown: The assembled accessible markdown of the form document.
        infer: When True, use the field-discovery prompt tuned for OCR'd printed
            forms (aggressively turns labels/questions/sign-lines into fields).
            When False, use the standard descriptive prompt.

    Returns:
        ``(output, input_tokens, output_tokens)`` where ``output`` is a
        ``FormFieldMapOutput``.

    Raises:
        Exception: Whatever the agent run raises (propagated unchanged so callers
        can record the original message).
    """
    from pydantic_ai import Agent

    from ..agents.model_factory import get_model_for_tier
    from ..agents.model_tiers import ModelTier
    from ..agents.prompts.form_field_mapper import (
        FORM_FIELD_INFERENCE_SYSTEM_PROMPT,
        FORM_FIELD_MAPPER_SYSTEM_PROMPT,
        FormFieldMapOutput,
    )

    system_prompt = FORM_FIELD_INFERENCE_SYSTEM_PROMPT if infer else FORM_FIELD_MAPPER_SYSTEM_PROMPT
    model = get_model_for_tier(ModelTier.REASONING)
    agent: Agent[None, FormFieldMapOutput] = Agent(
        model=model,
        output_type=FormFieldMapOutput,
        system_prompt=system_prompt,
    )

    result = await agent.run(markdown)
    usage = result.usage()
    return result.output, (usage.request_tokens or 0), (usage.response_tokens or 0)
