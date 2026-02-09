"""Citation linking subagent.

Specialized LLM agent for linking citation markers to their bibliography
entries and ensuring consistent citation formatting.

Uses compressed images (800px width) since this task only needs to verify
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
from .types import CitationResult

logger = logging.getLogger(__name__)

# Lazy-loaded singleton
_citation_subagent: Agent[None, CitationResult] | None = None

CITATION_SYSTEM_PROMPT = """You are a citation linking specialist.

Your job is to ensure citations are properly linked to their references.

## Citation Formats

1. **Numbered**: `[1]`, `[2]`, `[1-3]`, `[1,2,5]`
2. **Author-date**: `(Smith, 2023)`, `(Smith & Jones, 2023)`
3. **Superscript numbers**: `¹`, `²` (if used for citations, not footnotes)

## Your Task

1. Identify all citation markers in the text
2. Find the bibliography/references section
3. Match citations to their reference entries
4. Ensure consistent formatting

## Rules

1. Don't modify citation content, just ensure linking
2. If bibliography is not found, set confidence < 0.6
3. If citation has no matching reference, note it but don't remove
4. Preserve the citation style used in the document
5. Look at the full document to find References section

## Output

Return:
- `corrected_markdown`: Page markdown (may be unchanged if just noting links)
- `citations_linked`: List of {marker, linked_to, status}
  - status: "linked", "no_reference", "ambiguous"
- `bibliography_found`: Whether you found a references section
- `confidence`: 0.0-1.0
- `reasoning`: What you found and any issues
"""


def _get_citation_subagent() -> Agent[None, CitationResult]:
    """Get or create the citation linking subagent."""
    global _citation_subagent
    if _citation_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _citation_subagent = Agent(
            model=model,
            output_type=CitationResult,
            system_prompt=CITATION_SYSTEM_PROMPT,
        )
    return _citation_subagent


def _image_to_binary(image: Image.Image) -> BinaryContent:
    """Convert PIL Image to compressed BinaryContent for agent consumption.

    Uses text verification compression (800px width) since citation
    linking only needs to verify layout, not pixel-level formatting.
    """
    compressed_bytes = image_to_compressed_bytes(image, for_visual_analysis=False)
    return BinaryContent(data=compressed_bytes, media_type="image/png")


async def invoke_citation_subagent(
    page_markdown: str,
    page_image: Image.Image,
    full_document: str | None = None,
) -> CitationResult:
    """Invoke the citation linking subagent.

    Args:
        page_markdown: The page markdown to analyze
        page_image: The page image for visual reference
        full_document: Optional full document to find bibliography

    Returns:
        CitationResult with linking information and any corrections
    """
    # Import here to avoid circular dependency
    from . import is_rate_limit_error, llm_circuit_breaker

    # Check circuit breaker before making LLM call
    try:
        llm_circuit_breaker.check_state()
    except CircuitBreakerOpenError:
        logger.warning("LLM circuit breaker open, skipping citation subagent")
        return CitationResult(
            confidence=0.0,
            reasoning="LLM circuit breaker open - service unavailable",
            corrected_markdown=page_markdown,
            citations_linked=[],
            bibliography_found=False,
        )

    try:
        agent = _get_citation_subagent()
        image_content = _image_to_binary(page_image)

        # Build prompt with optional full document context
        doc_context = ""
        if full_document:
            doc_context = f"""

## Full Document (for finding bibliography)

```markdown
{full_document}
```
"""

        prompt = f"""Analyze this page markdown and link citations to references:

## Page Markdown

```markdown
{page_markdown}
```
{doc_context}
The page image is provided for visual reference.
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

        logger.warning(f"Citation subagent failed: {e}")
        return CitationResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            corrected_markdown=page_markdown,
            citations_linked=[],
            bibliography_found=False,
        )


__all__ = [
    "invoke_citation_subagent",
    "CITATION_SYSTEM_PROMPT",
]
