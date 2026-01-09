"""Paragraph merge subagent.

Specialized LLM agent for detecting and merging paragraphs that are
split across page boundaries.
"""

import logging
from io import BytesIO

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.utils.circuit_breaker import CircuitBreakerOpenError

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import ParagraphMergeResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_paragraph_merge_subagent: Agent[None, ParagraphMergeResult] | None = None

PARAGRAPH_MERGE_SYSTEM_PROMPT = """You are a paragraph continuity specialist.

Your job is to detect when a paragraph is split across pages and determine how to merge.

## Split Paragraph Signs

1. **Incomplete sentence**: Page ends without punctuation (., !, ?, :, ;)
2. **Split word**: Word broken with hyphen: `infor-` on page 1, `mation` on page 2
3. **Lowercase start**: Page 2 starts with lowercase (continuing sentence)
4. **Context continuity**: The meaning continues across the break

## Join Methods

1. **space**: Add a space between the texts
   - Page 1: "The results show"
   - Page 2: "significant improvement"
   - Result: "The results show significant improvement"

2. **hyphen_removal**: Remove hyphen and join directly
   - Page 1: "infor-"
   - Page 2: "mation is key"
   - Result: "information is key"

3. **direct**: Join without any separator
   - Page 1: "anti"
   - Page 2: "thesis of the argument"
   - Result: "antithesis of the argument"

## Rules

1. Look at BOTH page images to understand the visual flow
2. If page 1 ends with complete sentence, don't merge (should_merge=False)
3. If page 2 starts with heading or new section, don't merge
4. Return exact character counts to remove from each page
5. If unsure, set confidence < 0.7

## Output

Return:
- `should_merge`: True if pages should be merged
- `merged_text`: The combined text if merging
- `join_method`: "space", "hyphen_removal", or "direct"
- `page1_remove_chars`: Characters to remove from end of page 1
- `page2_remove_chars`: Characters to remove from start of page 2
- `confidence`: 0.0-1.0
- `reasoning`: Why you made this decision

## Example

Page 1 ends: "The experimental results demon-"
Page 2 starts: "strate that the hypothesis..."

Output:
- should_merge: true
- merged_text: "The experimental results demonstrate that the hypothesis..."
- join_method: "hyphen_removal"
- page1_remove_chars: 7 ("demon-\\n")
- page2_remove_chars: 6 ("strate")
- confidence: 0.95
- reasoning: "Word 'demonstrate' is split with hyphen across pages"
"""


def _get_paragraph_merge_subagent() -> Agent[None, ParagraphMergeResult]:
    """Get or create the paragraph merge subagent."""
    global _paragraph_merge_subagent
    if _paragraph_merge_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _paragraph_merge_subagent = Agent(
            model=model,
            output_type=ParagraphMergeResult,
            system_prompt=PARAGRAPH_MERGE_SYSTEM_PROMPT,
        )
    return _paragraph_merge_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for agent consumption."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


async def invoke_paragraph_merge_subagent(
    page1_end: str,
    page2_start: str,
    page1_image: Image.Image,
    page2_image: Image.Image,
) -> ParagraphMergeResult:
    """Invoke the paragraph merge subagent.

    Args:
        page1_end: The ending text of page 1
        page2_start: The starting text of page 2
        page1_image: Image of page 1 for visual reference
        page2_image: Image of page 2 for visual reference

    Returns:
        ParagraphMergeResult with merge decision and details
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping paragraph merge subagent")
        return ParagraphMergeResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            should_merge=False,
            merged_text="",
            join_method="space",
            page1_remove_chars=0,
            page2_remove_chars=0,
        )

    try:
        agent = _get_paragraph_merge_subagent()
        page1_image_content = _image_to_binary(page1_image)
        page2_image_content = _image_to_binary(page2_image)

        prompt = f"""Analyze whether these page boundaries should be merged:

## Page 1 Ending

```
{page1_end}
```

## Page 2 Starting

```
{page2_start}
```

Two page images are provided: first is page 1 (ending), second is page 2 (starting).
"""

        result = await agent.run([
            prompt,
            page1_image_content,
            page2_image_content,
        ])

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

        logger.warning(f"Paragraph merge subagent failed: {e}")
        return ParagraphMergeResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            should_merge=False,
            merged_text="",
            join_method="space",
            page1_remove_chars=0,
            page2_remove_chars=0,
        )


__all__ = [
    "invoke_paragraph_merge_subagent",
    "PARAGRAPH_MERGE_SYSTEM_PROMPT",
]
