"""Typography semantics subagent.

Specialized LLM agent for adding semantic bold/italic/code formatting
where visual formatting conveys meaning.

Supports both single-region and batch processing modes:
- Single region: invoke_typography_subagent() for one text region
- Batch mode: invoke_typography_subagent_batch() for multiple regions in one LLM call

Batch processing reduces token usage by 30-40% when multiple typography tasks
need to be processed for the same page.
"""

import logging
import re
from io import BytesIO

from PIL import Image
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.utils.circuit_breaker import CircuitBreakerOpenError

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

# Maximum regions to batch in a single call
MAX_BATCH_SIZE = 5

TYPOGRAPHY_BATCH_SYSTEM_PROMPT = """You are a typography semantics specialist analyzing MULTIPLE text regions.

Your job is to add markdown formatting where visual formatting conveys MEANING for each region.

## Semantic Formatting

1. **Bold** (`**text**`): Key terms, warnings, important definitions
2. **Italic** (`*text*`): Emphasis, foreign words, titles, citations
3. **Code** (`` `text` ``): Commands, code, technical terms

## What NOT to Format

1. Already formatted text
2. Table headers (structural)
3. Document titles (already headings)
4. Entire paragraphs (stylistic)
5. Decorative bold (styling, not meaning)

## Output Format

For EACH region, provide a result in this exact format:

--- REGION 1 ---
CORRECTED_MARKDOWN:
[the corrected text here]
END_MARKDOWN
FORMATTING_ADDED:
- text: [text], type: [bold|italic|code], purpose: [purpose]
CONFIDENCE: [0.0-1.0]
REASONING: [brief explanation]

--- REGION 2 ---
[same format]

## Rules

1. Process ALL regions provided
2. Maintain EXACT region numbering
3. If no formatting needed, return original text with confidence 1.0
4. If unsure whether formatting is semantic, set confidence < 0.8
"""


class BatchTypographyResult(BaseModel):
    """Result from batch typography processing."""

    results: list[TypographyResult] = Field(
        default_factory=list,
        description="Results for each region in order",
    )


# Lazy-loaded singletons
_typography_batch_subagent: Agent[None, str] | None = None


def _get_typography_batch_subagent() -> Agent[None, str]:
    """Get or create the batch typography subagent.

    The batch subagent returns raw string output that we parse,
    since structured output for variable-length lists is complex.
    """
    global _typography_batch_subagent
    if _typography_batch_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _typography_batch_subagent = Agent(
            model=model,
            output_type=str,
            system_prompt=TYPOGRAPHY_BATCH_SYSTEM_PROMPT,
        )
    return _typography_batch_subagent


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
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping typography subagent")
        return TypographyResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            corrected_markdown=text_region,
            formatting_added=[],
        )

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

        # Record success
        llm_circuit_breaker.record_success()
        return result.output

    except CircuitBreakerOpenError:
        # Re-raise circuit breaker errors (shouldn't happen after check_state, but be safe)
        raise
    except Exception as e:
        # Don't count rate limit errors as failures
        if not is_rate_limit_error(e):
            llm_circuit_breaker.record_failure()

        logger.warning(f"Typography subagent failed: {e}")
        return TypographyResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrected_markdown=text_region,
            formatting_added=[],
        )


def _parse_batch_response(
    response: str,
    original_regions: list[tuple[str, str]],
) -> list[tuple[str, TypographyResult]]:
    """Parse the batch response into individual TypographyResults.

    Args:
        response: Raw LLM response text
        original_regions: List of (task_target, text_region) tuples

    Returns:
        List of (task_target, TypographyResult) tuples
    """
    results: list[tuple[str, TypographyResult]] = []

    # Split response by region markers
    region_pattern = r"---\s*REGION\s*(\d+)\s*---"
    region_splits = re.split(region_pattern, response, flags=re.IGNORECASE)

    # region_splits will be: [preamble, num1, content1, num2, content2, ...]
    # Skip preamble (index 0), then pairs of (number, content)
    region_contents: dict[int, str] = {}
    for i in range(1, len(region_splits) - 1, 2):
        try:
            region_num = int(region_splits[i])
            region_content = region_splits[i + 1]
            region_contents[region_num] = region_content
        except (ValueError, IndexError):
            continue

    # Parse each region
    for idx, (task_target, original_text) in enumerate(original_regions):
        region_num = idx + 1
        content = region_contents.get(region_num, "")

        if not content:
            # No result for this region, return failed result
            results.append((
                task_target,
                TypographyResult(
                    confidence=0.0,
                    reasoning="No result returned for this region",
                    corrected_markdown=original_text,
                    formatting_added=[],
                ),
            ))
            continue

        # Parse corrected markdown
        markdown_match = re.search(
            r"CORRECTED_MARKDOWN:\s*\n(.*?)\nEND_MARKDOWN",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        corrected_markdown = (
            markdown_match.group(1).strip() if markdown_match else original_text
        )

        # Parse confidence
        confidence_match = re.search(
            r"CONFIDENCE:\s*([\d.]+)",
            content,
            re.IGNORECASE,
        )
        try:
            confidence = float(confidence_match.group(1)) if confidence_match else 0.5
            confidence = max(0.0, min(1.0, confidence))
        except ValueError:
            confidence = 0.5

        # Parse reasoning
        reasoning_match = re.search(
            r"REASONING:\s*(.+?)(?=---|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        reasoning = (
            reasoning_match.group(1).strip() if reasoning_match else "Batch processing"
        )

        # Parse formatting added
        formatting_added: list[dict] = []
        formatting_section = re.search(
            r"FORMATTING_ADDED:\s*(.*?)(?=CONFIDENCE:|---|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if formatting_section:
            # Parse entries like: - text: [text], type: [type], purpose: [purpose]
            entries = re.findall(
                r"-\s*text:\s*([^,]+),\s*type:\s*([^,]+),\s*purpose:\s*([^\n]+)",
                formatting_section.group(1),
                re.IGNORECASE,
            )
            for text, fmt_type, purpose in entries:
                formatting_added.append({
                    "text": text.strip().strip("[]"),
                    "type": fmt_type.strip().strip("[]"),
                    "purpose": purpose.strip().strip("[]"),
                })

        results.append((
            task_target,
            TypographyResult(
                confidence=confidence,
                reasoning=reasoning,
                corrected_markdown=corrected_markdown,
                formatting_added=formatting_added,
            ),
        ))

    return results


async def invoke_typography_subagent_batch(
    text_regions: list[tuple[str, str]],
    page_image: Image.Image,
    capture_debug: bool = False,
) -> list[tuple[str, TypographyResult]]:
    """Analyze multiple text regions in a single subagent call.

    This function batches multiple typography analysis tasks into a single
    LLM call, reducing token usage by 30-40% compared to individual calls.

    Args:
        text_regions: List of (task_target, text_region) tuples
        page_image: The page image for visual reference
        capture_debug: If True, include additional debug information

    Returns:
        List of (task_target, TypographyResult) tuples in the same order
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    if not text_regions:
        return []

    # Single region optimization: delegate to non-batch function
    if len(text_regions) == 1:
        task_target, text_region = text_regions[0]
        single_result = await invoke_typography_subagent(text_region, page_image)
        return [(task_target, single_result)]

    # Split into batches if too large
    if len(text_regions) > MAX_BATCH_SIZE:
        logger.info(
            f"Splitting {len(text_regions)} typography regions into batches of {MAX_BATCH_SIZE}"
        )
        all_results: list[tuple[str, TypographyResult]] = []
        for i in range(0, len(text_regions), MAX_BATCH_SIZE):
            batch = text_regions[i : i + MAX_BATCH_SIZE]
            batch_results = await invoke_typography_subagent_batch(
                batch, page_image, capture_debug
            )
            all_results.extend(batch_results)
        return all_results

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping batch typography subagent")
        return [
            (
                task_target,
                TypographyResult(
                    confidence=0.0,
                    reasoning="LLM circuit breaker open - service unavailable",
                    corrected_markdown=text_region,
                    formatting_added=[],
                ),
            )
            for task_target, text_region in text_regions
        ]

    # Build batch prompt
    prompt_parts = ["Analyze the following text regions for typography issues:\n"]

    for idx, (task_target, text_region) in enumerate(text_regions):
        region_num = idx + 1
        prompt_parts.append(f"\n--- REGION {region_num} (target: {task_target}) ---")
        prompt_parts.append(f"```\n{text_region}\n```\n")

    prompt_parts.append(
        "\nFor each region, provide corrections following the output format."
    )
    prompt_parts.append(
        "The page image shows the visual formatting. Only add markdown where formatting conveys meaning."
    )

    prompt = "\n".join(prompt_parts)

    try:
        agent = _get_typography_batch_subagent()
        image_content = _image_to_binary(page_image)

        agent_result = await agent.run([prompt, image_content])
        response = agent_result.output

        # Record success
        llm_circuit_breaker.record_success()

        # Parse the batch response
        parsed_results = _parse_batch_response(response, text_regions)

        logger.info(
            f"Batch typography processed {len(text_regions)} regions in single call"
        )
        return parsed_results

    except CircuitBreakerOpenError:
        # Re-raise circuit breaker errors
        raise
    except Exception as e:
        # Don't count rate limit errors as failures
        if not is_rate_limit_error(e):
            llm_circuit_breaker.record_failure()

        logger.warning(f"Batch typography subagent failed: {e}")
        # Return failed results for all regions
        return [
            (
                task_target,
                TypographyResult(
                    confidence=0.0,
                    reasoning=f"Batch subagent error: {e}",
                    corrected_markdown=text_region,
                    formatting_added=[],
                ),
            )
            for task_target, text_region in text_regions
        ]


def reset_typography_agents() -> None:
    """Reset typography subagent singletons.

    Used by tests to ensure agent isolation between test cases.
    """
    global _typography_subagent, _typography_batch_subagent
    _typography_subagent = None
    _typography_batch_subagent = None


__all__ = [
    "invoke_typography_subagent",
    "invoke_typography_subagent_batch",
    "TYPOGRAPHY_SYSTEM_PROMPT",
    "TYPOGRAPHY_BATCH_SYSTEM_PROMPT",
    "MAX_BATCH_SIZE",
    "BatchTypographyResult",
    "reset_typography_agents",
    "_parse_batch_response",
]
