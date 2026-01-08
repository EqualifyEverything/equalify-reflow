"""Footnote correction subagent.

Specialized LLM agent for ensuring footnotes are properly formatted and linked,
converting various footnote formats to markdown style.
"""

import logging
from io import BytesIO

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import FootnoteResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_footnote_subagent: Agent[None, FootnoteResult] | None = None

FOOTNOTE_SYSTEM_PROMPT = """You are a footnote specialist.

Your job is to ensure footnotes are properly formatted and linked.

## Footnote Formats

1. **Markdown style**: `[^1]` with definition `[^1]: Footnote text`
2. **Superscript**: `¹`, `²`, `³` (convert to markdown)
3. **Parenthetical**: `(1)`, `(2)` (convert if clearly footnotes)
4. **Asterisk**: `*`, `**` (convert if used as footnotes)

## Common Issues

1. **Marker without definition**: `[^1]` exists but no `[^1]: ...`
2. **Definition without marker**: Footnote text at bottom with no reference
3. **Misplaced definition**: Footnote text appears inline instead of bottom
4. **Inconsistent format**: Mix of `[^1]` and `¹` styles

## Rules

1. Standardize to markdown format: `[^N]` and `[^N]: definition`
2. Footnote definitions should be at the bottom of the page/section
3. Preserve the original footnote content exactly
4. If a definition is missing, set confidence < 0.7
5. Look at the page image to find footnote text at bottom

## Output

Return:
- `corrected_markdown`: Full page markdown with footnotes fixed
- `footnotes_fixed`: List of {marker, action, definition}
  - action: "linked", "moved", "converted", "definition_missing"
- `confidence`: 0.0-1.0
- `reasoning`: What you fixed and why
"""


def _get_footnote_subagent() -> Agent[None, FootnoteResult]:
    """Get or create the footnote correction subagent."""
    global _footnote_subagent
    if _footnote_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _footnote_subagent = Agent(
            model=model,
            output_type=FootnoteResult,
            system_prompt=FOOTNOTE_SYSTEM_PROMPT,
        )
    return _footnote_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for agent consumption."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


async def invoke_footnote_subagent(
    page_markdown: str,
    page_image: Image.Image,
) -> FootnoteResult:
    """Invoke the footnote correction subagent.

    Args:
        page_markdown: The page markdown to correct
        page_image: The page image for visual reference

    Returns:
        FootnoteResult with corrected markdown and details of fixes
    """
    try:
        agent = _get_footnote_subagent()
        image_content = _image_to_binary(page_image)

        prompt = f"""Analyze this page markdown and fix any footnote issues:

```markdown
{page_markdown}
```

The page image is provided to help locate footnote definitions at the bottom of the page.
"""

        result = await agent.run([prompt, image_content])
        return result.output

    except Exception as e:
        logger.warning(f"Footnote subagent failed: {e}")
        return FootnoteResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrected_markdown=page_markdown,
            footnotes_fixed=[],
        )


__all__ = [
    "invoke_footnote_subagent",
    "FOOTNOTE_SYSTEM_PROMPT",
]
