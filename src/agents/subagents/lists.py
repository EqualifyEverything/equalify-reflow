"""List semantics subagent.

Specialized LLM agent for fixing list structure including nesting,
numbering, and bullet consistency.
"""

import logging
from io import BytesIO

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.utils.circuit_breaker import CircuitBreakerOpenError

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import ListResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_list_subagent: Agent[None, ListResult] | None = None

LIST_SEMANTICS_SYSTEM_PROMPT = """You are a list structure specialist.

Your job is to ensure lists are properly formatted with correct structure.

## Markdown List Rules

1. **Unordered lists**: Use `-`, `*`, or `+` consistently
2. **Ordered lists**: Use `1.`, `2.`, `3.` (sequential)
3. **Nesting**: 2 spaces per indentation level
4. **Mixed lists**: Don't mix ordered/unordered at the same level

## Common Issues

1. **Inconsistent nesting**: Wrong number of spaces
   - Bad: `   - item` (3 spaces)
   - Good: `  - item` (2 spaces) or `    - item` (4 spaces)

2. **Broken numbering**: `1.`, `2.`, `5.` should be `1.`, `2.`, `3.`

3. **Mixed bullets at same level**: `-` and `*` mixed
   - Standardize to one style (prefer `-`)

4. **Visual vs markdown mismatch**: Image shows nesting that markdown doesn't reflect

## Rules

1. Compare markdown structure to visual layout in image
2. Use 2-space indentation for each nesting level
3. Preserve the author's choice of ordered vs unordered
4. Don't change list content, only structure
5. If visual hierarchy is unclear, set confidence < 0.8

## Output

Return:
- `corrected_markdown`: The list with structure fixed
- `issues_fixed`: List of what you fixed
- `confidence`: 0.0-1.0
- `reasoning`: What was wrong and how you fixed it
"""


def _get_list_subagent() -> Agent[None, ListResult]:
    """Get or create the list semantics subagent."""
    global _list_subagent
    if _list_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _list_subagent = Agent(
            model=model,
            output_type=ListResult,
            system_prompt=LIST_SEMANTICS_SYSTEM_PROMPT,
        )
    return _list_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for agent consumption."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


async def invoke_list_subagent(
    list_markdown: str,
    page_image: Image.Image,
) -> ListResult:
    """Invoke the list semantics subagent.

    Args:
        list_markdown: The list markdown to correct
        page_image: The page image for visual reference

    Returns:
        ListResult with corrected markdown and details of fixes
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping list subagent")
        return ListResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            corrected_markdown=list_markdown,
            issues_fixed=[],
        )

    try:
        agent = _get_list_subagent()
        image_content = _image_to_binary(page_image)

        prompt = f"""Analyze this list markdown and fix any structural issues:

```markdown
{list_markdown}
```

The page image is provided to verify the visual hierarchy matches the markdown structure.
"""

        result = await agent.run([prompt, image_content])

        # Record success
        llm_circuit_breaker.record_success()
        return result.output

    except CircuitBreakerOpenError:
        # Re-raise circuit breaker errors
        raise
    except Exception as e:
        # Don't count rate limit errors as failures
        if not is_rate_limit_error(e):
            llm_circuit_breaker.record_failure()

        logger.warning(f"List subagent failed: {e}")
        return ListResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrected_markdown=list_markdown,
            issues_fixed=[],
        )


__all__ = [
    "invoke_list_subagent",
    "LIST_SEMANTICS_SYSTEM_PROMPT",
]
