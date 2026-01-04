"""Analysis Agent for the v3 correction pipeline.

This agent analyzes a page (image + markdown) and outputs a structured list
of issues found. It focuses on DETECTION, not CORRECTION.

Architecture:
    - Receives: page image + markdown
    - Outputs: structured AnalysisResult with list of PageIssue items
    - Reasoning comes first (chain of thought)
    - Describes WHAT is wrong and WHERE, not HOW to fix it

The Analysis Agent is the first step in a two-phase approach:
    1. ANALYZE: This agent detects issues (figures needing alt-text, heading skips, etc.)
    2. EXECUTE: A separate agent applies fixes based on the analysis

This separation ensures:
    - Clear separation of concerns
    - Better reasoning about what needs to be fixed
    - Traceable decisions (why was this flagged?)
"""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


# =============================================================================
# Output Models
# =============================================================================


class PageIssue(BaseModel):
    """A single issue detected on a page.

    Describes WHAT is wrong and WHERE, not HOW to fix it.
    The execution agent will determine the fix.
    """

    issue_id: str = Field(
        ...,
        description="Unique ID like 'image:1', 'table:2', 'heading:3', or 'line:15'",
    )
    issue_type: Literal["figure", "table", "heading", "spelling", "formatting", "other"] = Field(
        ...,
        description="Category of the issue",
    )
    description: str = Field(
        ...,
        description="What is wrong - e.g., 'needs alt-text', 'heading skip H1->H3', 'typo: Doclign'",
    )
    context: str = Field(
        ...,
        description="Relevant context - e.g., 'appears to be a logo in header', 'should be H2'",
    )
    location: str = Field(
        ...,
        description="Where in the markdown - e.g., 'line 15', 'after ## Introduction'",
    )
    severity: Literal["critical", "major", "minor"] = Field(
        default="major",
        description="How important: critical=accessibility barrier, major=should fix, minor=nice to have",
    )


class AnalysisResult(BaseModel):
    """Result from analyzing a page.

    Contains the chain of thought reasoning followed by
    a structured list of detected issues.
    """

    page_num: int = Field(
        ...,
        description="Page number that was analyzed (1-indexed)",
    )
    reasoning: str = Field(
        ...,
        description="Chain of thought explaining the analysis process and findings",
    )
    issues: list[PageIssue] = Field(
        default_factory=list,
        description="List of issues detected on this page",
    )
    markdown_quality: Literal["good", "needs_work", "poor"] = Field(
        ...,
        description="Overall assessment of the markdown quality",
    )


# =============================================================================
# System Prompt
# =============================================================================

ANALYSIS_SYSTEM_PROMPT = """You are a document accessibility analyst. Your job is to:
1. Review the page image and markdown
2. Identify issues that need correction
3. Output a structured list of issues

## Your Role: DETECTION, Not Correction

You identify problems. You do NOT suggest solutions.
Another agent (the execution agent) will apply fixes based on your analysis.

## What to Look For

### Figures (image placeholders) - CRITICAL for accessibility
- `<!-- image -->` or `<!-- image:N -->` placeholders that need alt-text
- Note what the image appears to be in the source (logo, chart, photo, diagram)
- Determine if decorative (no alt needed) or informative (needs alt)
- Issue type: "figure"
- Example issue_id: "image:1", "image:2"

### Tables (table placeholders)
- `<!-- table -->` or `<!-- table:N -->` placeholders that need transcription
- Note the table structure visible in the source image (rows, columns, headers)
- Issue type: "table"
- Example issue_id: "table:1", "table:2"

### Headings - CRITICAL for accessibility
- **Page 1 MUST have H1**: If page 1 starts with H2 or lower, that's a CRITICAL issue
- Heading level skips (H1 -> H3 without H2) - this breaks screen reader navigation
- You can only go DOWN one level at a time (H1->H2->H3 is OK, H1->H3 is NOT)
- You CAN go UP any number of levels (H3->H1 is OK)
- Incorrect hierarchy based on document structure
- Issue type: "heading"
- Example issue_id: "heading:1", "heading:2"

## Section Numbering to Heading Level (Deterministic Rule)
- Single number (e.g., "3") = H2 (## 3 Title)
- Two-part number (e.g., "3.1") = H3 (### 3.1 Title)
- Three-part number (e.g., "3.1.1") = H4 (#### 3.1.1 Title)
- Apply this rule consistently - do not deviate based on visual appearance

### Spelling/OCR Errors
- Obvious typos, especially of technical terms
- OCR artifacts (characters that look similar but are wrong)
- Common patterns: "rn" -> "m", "l" -> "1", "O" -> "0"
- Issue type: "spelling"
- Example issue_id: "line:15" (use line number where typo appears)

### Formatting Issues
- Broken URLs (spaces in middle of URL)
- Malformed markdown (unclosed brackets, broken tables)
- Issue type: "formatting"
- Example issue_id: "line:20"

## Output Format

For each issue:
- issue_id: Unique identifier (e.g., "image:1", "table:2", "heading:1", "line:15")
- issue_type: Category (figure, table, heading, spelling, formatting, other)
- description: WHAT is wrong (not how to fix)
- context: What you observe in the image that helps understand the issue
- location: WHERE in the markdown (line number or nearby heading)
- severity: How important (critical, major, minor)

## Severity Guidelines

- CRITICAL: Accessibility barriers (missing alt-text, heading skips, missing table)
- MAJOR: Should be fixed (OCR errors in important text, formatting issues)
- MINOR: Nice to have (minor typos in non-critical text)

## Chain of Thought

ALWAYS start your analysis with reasoning:
1. What do I see in the image?
2. What does the markdown contain?
3. Are there discrepancies?
4. What accessibility issues exist?

Then provide the structured issues list.

## DO NOT
- Suggest specific fixes or corrections
- Generate alt-text (that's the execution agent's job)
- Rewrite any markdown
- Make assumptions about content you cannot see
"""


# =============================================================================
# Agent Setup
# =============================================================================

_analysis_agent: Agent[None, AnalysisResult] | None = None


def _get_analysis_agent() -> Agent[None, AnalysisResult]:
    """Get or create the analysis agent singleton.

    Uses lazy initialization to avoid loading the model
    until it's actually needed.
    """
    global _analysis_agent

    if _analysis_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _analysis_agent = Agent(
            model=model,
            output_type=AnalysisResult,
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
        )
        logger.info("Analysis agent initialized with Haiku model")

    return _analysis_agent


def _pil_to_bytes(image: "Image.Image") -> bytes:
    """Convert PIL Image to PNG bytes."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _extract_placeholders(markdown: str) -> tuple[list[int], list[int]]:
    """Extract image and table placeholder IDs from markdown.

    Returns:
        Tuple of (image_ids, table_ids) found in the markdown
    """
    image_ids = [int(m.group(1)) for m in re.finditer(r"<!-- image:(\d+) -->", markdown)]
    table_ids = [int(m.group(1)) for m in re.finditer(r"<!-- table:(\d+) -->", markdown)]
    return sorted(image_ids), sorted(table_ids)


def _compute_quality(issues: list[PageIssue]) -> Literal["good", "needs_work", "poor"]:
    """Compute markdown quality based on issue count and severity.

    Deterministic quality assessment to replace LLM's arbitrary judgment.

    Rules:
    - good: 0 issues
    - needs_work: 1-3 issues or any major issues
    - poor: any critical issues or 4+ total issues
    """
    if not issues:
        return "good"

    critical_count = sum(1 for i in issues if i.severity == "critical")
    total_count = len(issues)

    if critical_count > 0 or total_count >= 4:
        return "poor"

    return "needs_work"


# =============================================================================
# Main Analysis Function
# =============================================================================


class AnalysisUsage(BaseModel):
    """Usage info from analysis agent."""

    input_tokens: int = 0
    output_tokens: int = 0


async def analyze_page(
    page_num: int,
    markdown: str,
    page_image: "Image.Image",
    context_prompt: str = "",
) -> tuple[AnalysisResult, AnalysisUsage]:
    """Analyze a page and return structured issues found.

    This is the main entry point for the Analysis Agent. It examines
    both the page image and the markdown to identify issues that need
    correction by the execution agent.

    Args:
        page_num: Page number being analyzed (1-indexed)
        markdown: Current markdown for this page
        page_image: PIL Image of the page
        context_prompt: Optional pre-computed document context to include in prompt

    Returns:
        Tuple of (AnalysisResult with reasoning and list of PageIssue items, AnalysisUsage)
    """
    agent = _get_analysis_agent()

    # Convert image to bytes for the API
    image_bytes = _pil_to_bytes(page_image)

    # Extract placeholder inventory
    image_ids, table_ids = _extract_placeholders(markdown)

    # Build the analysis prompt
    h1_reminder = ""
    if page_num == 1:
        h1_reminder = "\n**CRITICAL: Page 1 MUST start with an H1 heading (document title). Flag if first heading is H2 or lower.**\n"

    # Include document context if provided
    context_section = ""
    if context_prompt:
        context_section = f"\n{context_prompt}\n"

    prompt = f"""Analyze page {page_num}.
{h1_reminder}{context_section}
PLACEHOLDERS IN MARKDOWN (only reference these):
- Images: {image_ids if image_ids else 'NONE'}
- Tables: {table_ids if table_ids else 'NONE'}

MARKDOWN:
```
{markdown}
```

Find issues: missing alt-text, heading problems, OCR errors. Only reference placeholders that exist above."""

    try:
        result = await agent.run(
            [
                prompt,
                BinaryContent(data=image_bytes, media_type="image/png"),
            ]
        )

        output = result.output

        # Extract usage info
        usage_info = result.usage()
        usage = AnalysisUsage(
            input_tokens=usage_info.input_tokens or 0,
            output_tokens=usage_info.output_tokens or 0,
        )

        # Override LLM quality assessment with deterministic logic based on issue count/severity
        computed_quality = _compute_quality(output.issues)
        if output.markdown_quality != computed_quality:
            logger.debug(
                f"Page {page_num}: overriding LLM quality {output.markdown_quality} -> {computed_quality}"
            )
            # Create new result with corrected quality
            output = AnalysisResult(
                page_num=output.page_num,
                reasoning=output.reasoning,
                issues=output.issues,
                markdown_quality=computed_quality,
            )

        logger.info(
            f"Page {page_num} analysis: {len(output.issues)} issues found, "
            f"quality={output.markdown_quality}, tokens={usage.input_tokens}/{usage.output_tokens}"
        )

        # Log issue breakdown by type
        issue_types = {}
        for issue in output.issues:
            issue_types[issue.issue_type] = issue_types.get(issue.issue_type, 0) + 1
        if issue_types:
            logger.debug(f"Page {page_num} issue breakdown: {issue_types}")

        return output, usage

    except Exception as e:
        logger.error(f"Analysis failed for page {page_num}: {e}")
        return AnalysisResult(
            page_num=page_num,
            reasoning=f"Analysis failed with error: {str(e)}",
            issues=[],
            markdown_quality="poor",
        ), AnalysisUsage()


async def analyze_document_pages(
    page_markdowns: dict[int, str],
    page_images: dict[int, "Image.Image"],
) -> dict[int, AnalysisResult]:
    """Analyze all pages of a document.

    Processes each page sequentially (could be parallelized in future).

    Args:
        page_markdowns: Dict mapping page number to markdown content
        page_images: Dict mapping page number to PIL Image

    Returns:
        Dict mapping page number to AnalysisResult
    """
    results: dict[int, AnalysisResult] = {}

    for page_num in sorted(page_markdowns.keys()):
        if page_num not in page_images:
            logger.warning(f"No image for page {page_num}, skipping analysis")
            continue

        markdown = page_markdowns[page_num]
        image = page_images[page_num]

        result = await analyze_page(
            page_num=page_num,
            markdown=markdown,
            page_image=image,
        )

        results[page_num] = result

    # Summary logging
    total_issues = sum(len(r.issues) for r in results.values())
    critical_issues = sum(
        1 for r in results.values() for i in r.issues if i.severity == "critical"
    )
    logger.info(
        f"Document analysis complete: {len(results)} pages, "
        f"{total_issues} issues ({critical_issues} critical)"
    )

    return results


# =============================================================================
# Reset for Testing
# =============================================================================


def reset_analysis_agent() -> None:
    """Reset the analysis agent singleton for testing."""
    global _analysis_agent
    _analysis_agent = None
    logger.debug("Analysis agent reset")
