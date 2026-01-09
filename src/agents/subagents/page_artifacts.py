"""Page artifact removal subagent.

Specialized LLM agent for cleaning up extraction artifacts that incorrectly
split text, including page break markers, split words from page/column breaks,
and orphaned page numbers.
"""

import logging
from io import BytesIO

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.utils.circuit_breaker import CircuitBreakerOpenError

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import PageArtifactResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_page_artifact_subagent: Agent[None, PageArtifactResult] | None = None

PAGE_ARTIFACT_SYSTEM_PROMPT = """You are a page artifact removal specialist.

Your job is to clean up extraction artifacts that incorrectly split text.

## Common Artifacts

1. **Page break markers**: `---`, `~~~`, `***`, `___`
   - These are usually extraction errors, not intentional content
   - Remove them entirely

2. **Split words from PAGE breaks**: Words broken at page boundaries
   - Example: `infor-\\nmation` → `information`
   - Example: `de-\\n---\\nprecate` → `deprecate`
   - BUT preserve intentional hyphens: `self-aware`, `well-known`

3. **Split words from COLUMN breaks**: Words broken in double-column layouts
   - Academic papers often have two columns
   - Extraction may produce: `de-\\nprecate` (no --- marker)
   - Look at the page image to verify this is a column break, not intentional
   - Example: `meth-\\nodology` from column break → `methodology`

4. **Column breaks**: Markers or whitespace from multi-column layouts
   - Remove markers that interrupt text flow

5. **Orphaned page numbers**: Page numbers appearing mid-paragraph
   - Example: `The results show 42 that...` (42 is page number)
   - Remove if clearly a page number

## Rules

1. Remove markers that interrupt natural text flow
2. Rejoin hyphenated words ONLY if the hyphen is at a line break
3. Preserve intentional compound words with hyphens
4. Preserve paragraph breaks (double newlines)
5. If unsure whether a break is intentional, set confidence < 0.8

## Output

Return:
- `cleaned_text`: The text with artifacts removed
- `artifacts_removed`: List of what you removed
- `words_rejoined`: List of words you rejoined
- `confidence`: 0.0-1.0 (lower if ambiguous)
- `reasoning`: Why you made these changes
"""


def _get_page_artifact_subagent() -> Agent[None, PageArtifactResult]:
    """Get or create the page artifact removal subagent."""
    global _page_artifact_subagent
    if _page_artifact_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _page_artifact_subagent = Agent(
            model=model,
            output_type=PageArtifactResult,
            system_prompt=PAGE_ARTIFACT_SYSTEM_PROMPT,
        )
    return _page_artifact_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for agent consumption."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


async def invoke_page_artifact_subagent(
    text_region: str,
    page_image: Image.Image,
) -> PageArtifactResult:
    """Invoke the page artifact removal subagent.

    Args:
        text_region: The text region to clean up
        page_image: The page image for visual reference

    Returns:
        PageArtifactResult with cleaned text and details of changes
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping page artifact subagent")
        return PageArtifactResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            cleaned_text=text_region,
            artifacts_removed=[],
            words_rejoined=[],
        )

    try:
        agent = _get_page_artifact_subagent()
        image_content = _image_to_binary(page_image)

        prompt = f"""Analyze this text region and remove any page artifacts:

```
{text_region}
```

The page image is provided for visual reference to verify column layouts and page breaks.
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

        logger.warning(f"Page artifact subagent failed: {e}")
        return PageArtifactResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            cleaned_text=text_region,
            artifacts_removed=[],
            words_rejoined=[],
        )


__all__ = [
    "invoke_page_artifact_subagent",
    "PAGE_ARTIFACT_SYSTEM_PROMPT",
]
