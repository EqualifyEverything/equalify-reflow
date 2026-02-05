"""Consolidated text structure subagent.

Handles all text structure corrections in ONE LLM call:
- Page artifacts (---, split words, orphaned page numbers)
- Footnotes (markers, definitions, linking)
- Citations ([1], (Smith, 2023))
- Lists (indentation, numbering, bullets)

This consolidation reduces LLM calls from 4 per page to 1 for text structure tasks,
significantly reducing token usage and latency.

Uses compressed images (800px width) since these tasks only need to verify
text layout, not detect pixel-level visual formatting.
"""

import logging

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.utils.circuit_breaker import CircuitBreakerOpenError
from src.utils.image_utils import image_to_compressed_bytes

from ..model_tiers import MODEL_TIER_MAP, ModelTier
from .types import TextStructureResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_text_structure_subagent: Agent[None, TextStructureResult] | None = None

TEXT_STRUCTURE_SYSTEM_PROMPT = """You are a text structure specialist for PDF extraction cleanup.

Analyze the page and identify ALL corrections needed for text structure issues.
Return a list of corrections, each with exact before/after text.

## 1. Page Artifacts

Remove extraction artifacts that interrupt text flow:

- **Page break markers**: `---`, `~~~`, `***`, `___` mid-paragraph
- **Split words from page breaks**: `infor-\\nmation` → `information`
- **Split words from column breaks**: `meth-\\nodology` → `methodology`
  (use page image to verify column layout)
- **Orphaned page numbers**: Page numbers appearing mid-paragraph

Rules:
- Remove markers that interrupt natural text flow
- Rejoin hyphenated words ONLY if hyphen is at line break
- Preserve intentional compound words: `self-aware`, `well-known`
- Preserve paragraph breaks (double newlines)

## 2. Footnotes

Ensure footnotes are properly formatted:

- **Detect markers**: superscript `¹²³`, plain numbers after punctuation
- **Detect definitions**: text at page bottom with `[^N]:` format
- **Convert to markdown**: `[^N]` for references, `[^N]: definition` for definitions

Formats to convert:
- Superscript `¹` → `[^1]`
- Parenthetical `(1)` → `[^1]` (if clearly footnote)
- Asterisk `*` → `[^1]` (if used as footnote)

Note: Final cross-page renumbering happens at document assembly level.

## 3. Citations

Identify citations (don't modify, just link if possible):

- **Numbered**: `[1]`, `[2]`, `[1-3]`, `[1,2,5]`
- **Author-date**: `(Smith, 2023)`, `(Smith & Jones, 2023)`

If you see a References/Bibliography section, note it. Don't modify citation
format unless clearly broken.

## 4. Lists

Fix list structure issues:

- **Indentation**: 2 spaces per nesting level
- **Numbering**: Fix broken sequences `1.`, `2.`, `5.` → `1.`, `2.`, `3.`
- **Bullets**: Standardize to `-` (prefer dash)
- **Mixed types**: Don't mix ordered/unordered at same level

Compare markdown structure to visual layout in image.

## Output Format

Return a TextStructureResult with:

1. **corrections**: List of TextStructureCorrection objects. Each has:
   - `task_type`: "page_artifact" | "footnote" | "citation" | "list"
   - `before`: EXACT text to replace (must be findable in source)
   - `after`: Corrected text
   - `confidence`: 0.0-1.0 for this specific correction
   - `reasoning`: Brief explanation

2. **Summary counts** (auto-calculated from corrections):
   - `artifacts_removed`
   - `footnotes_fixed`
   - `citations_linked`
   - `lists_fixed`

3. **Overall confidence and reasoning**

## Important Rules

1. Only include items that NEED correction. If nothing to fix, return empty list.
2. The `before` field MUST contain exact text that exists in the source.
3. For multi-line changes, include the full context in `before`.
4. Set confidence < 0.8 if unsure about author intent.
5. When in doubt, preserve original text.
"""


def _get_text_structure_subagent() -> Agent[None, TextStructureResult]:
    """Get or create the text structure subagent."""
    global _text_structure_subagent
    if _text_structure_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _text_structure_subagent = Agent(
            model=model,
            output_type=TextStructureResult,
            system_prompt=TEXT_STRUCTURE_SYSTEM_PROMPT,
        )
    return _text_structure_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to compressed BinaryContent for agent consumption.

    Uses text verification compression (800px width) since text structure
    analysis only needs to verify layout, not pixel-level formatting.
    """
    compressed_bytes = image_to_compressed_bytes(image, for_visual_analysis=False)
    return BinaryContent(data=compressed_bytes, media_type="image/png")


async def invoke_text_structure_subagent(
    page_markdown: str,
    page_image: Image.Image,
) -> TextStructureResult:
    """Invoke the consolidated text structure subagent.

    Analyzes the page for all text structure issues in a single LLM call:
    - Page artifacts (---, split words)
    - Footnotes
    - Citations
    - Lists

    Args:
        page_markdown: The page markdown to analyze
        page_image: The page image for visual reference

    Returns:
        TextStructureResult with list of corrections and summary counts
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping text structure subagent")
        return TextStructureResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            corrections=[],
            artifacts_removed=0,
            footnotes_fixed=0,
            citations_linked=0,
            lists_fixed=0,
        )

    try:
        agent = _get_text_structure_subagent()
        image_content = _image_to_binary(page_image)

        prompt = f"""Analyze this page markdown for text structure issues:

```markdown
{page_markdown}
```

The page image is provided to verify layout, column breaks, and visual structure.
Return corrections for any page artifacts, footnotes, citations, or list issues found.
If no corrections needed, return an empty corrections list with high confidence.
"""

        result = await agent.run([prompt, image_content])

        # Record success
        llm_circuit_breaker.record_success()

        output = result.output

        # Auto-calculate summary counts from corrections
        output.artifacts_removed = sum(
            1 for c in output.corrections if c.task_type == "page_artifact"
        )
        output.footnotes_fixed = sum(
            1 for c in output.corrections if c.task_type == "footnote"
        )
        output.citations_linked = sum(
            1 for c in output.corrections if c.task_type == "citation"
        )
        output.lists_fixed = sum(
            1 for c in output.corrections if c.task_type == "list"
        )

        return output

    except CircuitBreakerOpenError:
        # Re-raise circuit breaker errors
        raise
    except Exception as e:
        # Don't count rate limit errors as failures
        if not is_rate_limit_error(e):
            llm_circuit_breaker.record_failure()

        logger.warning(f"Text structure subagent failed: {e}")
        return TextStructureResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrections=[],
            artifacts_removed=0,
            footnotes_fixed=0,
            citations_linked=0,
            lists_fixed=0,
        )


def reset_text_structure_agent() -> None:
    """Reset the text structure subagent singleton.

    Used by tests to ensure agent isolation between test cases.
    """
    global _text_structure_subagent
    _text_structure_subagent = None


__all__ = [
    "invoke_text_structure_subagent",
    "TEXT_STRUCTURE_SYSTEM_PROMPT",
    "reset_text_structure_agent",
]
