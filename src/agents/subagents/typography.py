"""Typography semantics subagent.

Specialized LLM agent for adding semantic bold/italic/code formatting
where visual formatting conveys meaning.
"""

import logging
from io import BytesIO

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import TypographyResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_typography_subagent: Agent[None, TypographyResult] | None = None

TYPOGRAPHY_SYSTEM_PROMPT = """You are a typography semantics specialist.

Your job is to add markdown formatting where visual formatting conveys MEANING.

## Semantic Formatting

1. **Bold** (`**text**`): Key terms, warnings, important definitions
   - Example: "The **Critical Path Method** is a technique..."
   - Example: "**Warning:** Do not proceed without..."

2. **Italic** (`*text*`): Emphasis, foreign words, titles, citations
   - Example: "This is *very* important"
   - Example: "The term *zeitgeist* means..."
   - Example: "As described in *Nature*..."

3. **Code** (`` `text` ``): Commands, code, technical terms
   - Example: "Run `npm install` to begin"
   - Example: "The `onClick` handler..."

## What NOT to Format

1. **Already formatted text**: Don't double-format
2. **Table headers**: Structural, not semantic
3. **Document titles**: Already captured as headings
4. **Entire paragraphs**: Stylistic, not semantic
5. **Decorative bold**: Bold that's just styling, not meaning

## Rules

1. Look at the page image to see visual formatting
2. ONLY add formatting if it conveys semantic meaning
3. If the text is already formatted in markdown, leave it
4. If unsure whether formatting is semantic, set confidence < 0.8
5. Preserve the exact text content

## Output

Return:
- `corrected_markdown`: Text with formatting added
- `formatting_added`: List of {text, type, purpose}
  - type: "bold", "italic", "code"
  - purpose: "emphasis", "definition", "foreign_word", "command", etc.
- `confidence`: 0.0-1.0
- `reasoning`: Why you added each format
"""


def _get_typography_subagent() -> Agent[None, TypographyResult]:
    """Get or create the typography semantics subagent."""
    global _typography_subagent
    if _typography_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _typography_subagent = Agent(
            model=model,
            output_type=TypographyResult,
            system_prompt=TYPOGRAPHY_SYSTEM_PROMPT,
        )
    return _typography_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to BinaryContent for agent consumption."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")


async def invoke_typography_subagent(
    text_region: str,
    page_image: Image.Image,
) -> TypographyResult:
    """Invoke the typography semantics subagent.

    Args:
        text_region: The text region to analyze for formatting
        page_image: The page image for visual reference

    Returns:
        TypographyResult with formatted markdown and details of additions
    """
    try:
        agent = _get_typography_subagent()
        image_content = _image_to_binary(page_image)

        prompt = f"""Analyze this text and add semantic markdown formatting:

```
{text_region}
```

The page image shows the visual formatting. Only add markdown where formatting conveys meaning.
"""

        result = await agent.run([prompt, image_content])
        return result.output

    except Exception as e:
        logger.warning(f"Typography subagent failed: {e}")
        return TypographyResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrected_markdown=text_region,
            formatting_added=[],
        )


__all__ = [
    "invoke_typography_subagent",
    "TYPOGRAPHY_SYSTEM_PROMPT",
]
