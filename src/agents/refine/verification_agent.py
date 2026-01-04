"""Verification agent for the v3 correction pipeline.

This agent provides "fresh eyes" verification by comparing the final markdown
output against the source page image. It's designed to catch issues that the
execution phase might have missed or introduced.

Architecture:
- Separate context from analysis (fresh eyes principle)
- Compares visual presentation to markdown output
- Returns pass/fail with actionable issues
- Can flag issues for another round of execution

Unlike the main refine agent, this is a simple single-pass agent without tools.
It receives markdown + image and returns a verification result.
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

if TYPE_CHECKING:
    from PIL import Image

    from src.api.experiments_v3 import ExecutionManifest

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

# Use efficient tier since verification is a focused comparison task
MODEL_TIER = ModelTier.EFFICIENT


# =============================================================================
# Output Models
# =============================================================================


class VerificationIssue(BaseModel):
    """An issue found during verification."""

    issue_type: Literal["missing_content", "wrong_content", "formatting", "accessibility"] = Field(
        description="Type of verification issue"
    )
    description: str = Field(
        description="What's wrong - e.g., 'Figure 2 alt-text doesn't match image content'"
    )
    location: str = Field(
        description="Where in the markdown"
    )
    suggested_action: str = Field(
        description="What should be done - e.g., 'Re-describe figure 2', 'Check heading hierarchy'"
    )


class VerificationUsage(BaseModel):
    """Usage info from verification agent."""

    input_tokens: int = 0
    output_tokens: int = 0


class VerificationResult(BaseModel):
    """Result from verifying a page."""

    page_num: int = Field(description="Page number that was verified")
    passed: bool = Field(description="Whether the page passes verification")
    issues: list[VerificationIssue] = Field(default_factory=list)
    reasoning: str = Field(description="Explanation of the verification")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    usage: VerificationUsage = Field(default_factory=VerificationUsage, description="Token usage")


# =============================================================================
# System Prompt
# =============================================================================

VERIFICATION_SYSTEM_PROMPT = '''You are a document quality reviewer. Your job is to verify that the corrected markdown accurately represents the page content.

## CRITICAL: DO NOT REVERT INTENTIONAL CHANGES
Execution made changes to fix issues. Unless the change is WRONG:
- Wrong = contradicts what's visible in the image
- Wrong = was applied incorrectly
- DO NOT flag changes just because they "look different"
- Trust that execution changes were intentional fixes

## What to Check

### Content Accuracy
- Does the markdown capture all visible text?
- Are figures described accurately? (Does alt-text match what's in the image?)
- Are tables transcribed correctly?

### Accessibility
- Do all informative images have meaningful alt-text?
- Are decorative images marked with empty alt (![](image))?
- Is heading hierarchy correct (no skips)?

### Section Numbering to Heading Level (Deterministic Rule)
- Single number (e.g., "3") = H2 (## 3 Title)
- Two-part number (e.g., "3.1") = H3 (### 3.1 Title)
- Three-part number (e.g., "3.1.1") = H4 (#### 3.1.1 Title)
- Apply this rule consistently - do not deviate based on visual appearance

### Formatting
- Is the markdown well-formed?
- Are URLs correct?
- Is the reading order logical?

## Verification Process
1. Review the page image to understand what SHOULD be there
2. Review the markdown to see what IS there
3. If CHANGES MADE BY EXECUTION is provided, respect those changes unless clearly wrong
4. Compare and identify discrepancies
5. Report any issues found

## Output
- passed: true if no significant issues
- issues: list of problems found (if any)
- reasoning: your analysis

## Important
- Be thorough but reasonable - minor formatting differences are OK
- Focus on accessibility-critical issues (alt-text, headings)
- Content accuracy is important (wrong transcriptions are a problem)
- DO NOT suggest reverting intentional execution changes unless they are WRONG
'''


# =============================================================================
# Helpers
# =============================================================================


def _pil_to_bytes(image: "Image.Image") -> bytes:
    """Convert PIL Image to bytes."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# =============================================================================
# Agent Singleton
# =============================================================================

_verification_agent: Agent[None, VerificationResult] | None = None


def _get_verification_agent() -> Agent[None, VerificationResult]:
    """Get or create the verification agent singleton."""
    global _verification_agent

    if _verification_agent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        _verification_agent = Agent(
            model,
            output_type=VerificationResult,
            system_prompt=VERIFICATION_SYSTEM_PROMPT,
        )
        logger.info("Verification agent initialized")

    return _verification_agent


def reset_verification_agent() -> None:
    """Reset the verification agent singleton for testing."""
    global _verification_agent
    _verification_agent = None
    logger.debug("Verification agent reset")


# =============================================================================
# Main Function
# =============================================================================


async def verify_page(
    page_num: int,
    markdown: str,
    page_image: "Image.Image",
    execution_manifest: "ExecutionManifest | None" = None,
    context_prompt: str = "",
) -> VerificationResult:
    """Verify that markdown accurately represents the page content.

    This function provides "fresh eyes" verification by comparing the final
    markdown output against the source page image. It's designed to catch
    issues that the execution phase might have missed or introduced.

    Args:
        page_num: Page number being verified (1-indexed)
        markdown: The corrected markdown to verify
        page_image: PIL Image of the source page
        execution_manifest: Optional manifest of changes made by execution
                           (helps verification avoid flagging intentional fixes)
        context_prompt: Optional pre-computed document context to include in prompt

    Returns:
        VerificationResult with pass/fail status and any issues found
    """
    # Import here to avoid circular dependency
    from src.api.experiments_v3 import format_manifest_for_prompt

    agent = _get_verification_agent()

    # Include document context if provided
    context_section = ""
    if context_prompt:
        context_section = f"\n{context_prompt}\n"

    # Include execution manifest if provided
    manifest_section = ""
    if execution_manifest:
        manifest_text = format_manifest_for_prompt(execution_manifest)
        if manifest_text:
            manifest_section = f"\n{manifest_text}\n"

    # Build user prompt with the markdown to verify
    user_prompt = f"""Please verify that this markdown accurately represents page {page_num}.
{context_section}{manifest_section}
## Markdown to Verify

```markdown
{markdown}
```

Compare this markdown against the page image and report:
1. Whether the markdown passes verification (no significant issues)
2. Any issues found (content accuracy, accessibility, formatting)
3. Your reasoning for the assessment

Remember to set page_num to {page_num} in your response."""

    # Convert page image to bytes
    image_bytes = _pil_to_bytes(page_image)

    try:
        result = await agent.run(
            [
                "Here is the page image to verify against:",
                BinaryContent(data=image_bytes, media_type="image/png"),
                user_prompt,
            ]
        )

        output = result.output

        # Extract usage info
        usage_info = result.usage()
        usage = VerificationUsage(
            input_tokens=usage_info.input_tokens or 0,
            output_tokens=usage_info.output_tokens or 0,
        )

        # Ensure page_num is set correctly (agent might not set it)
        # Also include usage in the output
        output = VerificationResult(
            page_num=page_num,
            passed=output.passed,
            issues=output.issues,
            reasoning=output.reasoning,
            confidence=output.confidence,
            usage=usage,
        )

        # Force consistency: issues=[] implies passed=True
        # This prevents the agent from returning passed=False with no issues
        if len(output.issues) == 0 and not output.passed:
            logger.warning(
                f"Page {page_num}: Forcing pass (no issues found but marked as failed)"
            )
            output = VerificationResult(
                page_num=page_num,
                passed=True,
                issues=[],
                reasoning=output.reasoning,
                confidence=output.confidence,
                usage=usage,
            )

        logger.info(
            f"Verification complete for page {page_num}: "
            f"passed={output.passed}, issues={len(output.issues)}, "
            f"confidence={output.confidence}, tokens={usage.input_tokens}/{usage.output_tokens}"
        )

        return output

    except Exception as e:
        logger.error(f"Verification failed for page {page_num}: {e}")
        return VerificationResult(
            page_num=page_num,
            passed=False,
            issues=[
                VerificationIssue(
                    issue_type="formatting",
                    description=f"Verification failed with error: {str(e)}",
                    location="entire page",
                    suggested_action="Retry verification or review manually",
                )
            ],
            reasoning=f"Verification could not be completed due to error: {str(e)}",
            confidence=0.0,
            usage=VerificationUsage(),
        )


async def verify_document(
    page_markdowns: dict[int, str],
    page_images: dict[int, "Image.Image"],
) -> dict[int, VerificationResult]:
    """Verify all pages in a document.

    Runs verification sequentially for each page and returns results.

    Args:
        page_markdowns: Markdown for each page (1-indexed)
        page_images: PIL images for each page (1-indexed)

    Returns:
        Dictionary mapping page number to VerificationResult
    """
    results: dict[int, VerificationResult] = {}

    for page_num in sorted(page_markdowns.keys()):
        markdown = page_markdowns[page_num]
        page_image = page_images.get(page_num)

        if page_image is None:
            logger.warning(f"No image for page {page_num}, skipping verification")
            results[page_num] = VerificationResult(
                page_num=page_num,
                passed=False,
                issues=[
                    VerificationIssue(
                        issue_type="formatting",
                        description="No page image available for verification",
                        location="entire page",
                        suggested_action="Provide page image for verification",
                    )
                ],
                reasoning="Cannot verify without page image",
                confidence=0.0,
            )
            continue

        results[page_num] = await verify_page(page_num, markdown, page_image)

    # Log summary
    total = len(results)
    passed = sum(1 for r in results.values() if r.passed)
    logger.info(f"Document verification complete: {passed}/{total} pages passed")

    return results


def summarize_verification(results: dict[int, VerificationResult]) -> dict:
    """Summarize verification results for reporting.

    Args:
        results: Verification results by page number

    Returns:
        Summary dictionary with stats and issues
    """
    total_pages = len(results)
    passed_pages = sum(1 for r in results.values() if r.passed)
    failed_pages = total_pages - passed_pages

    all_issues: list[dict] = []
    for page_num, result in sorted(results.items()):
        for issue in result.issues:
            all_issues.append({
                "page": page_num,
                "type": issue.issue_type,
                "description": issue.description,
                "location": issue.location,
                "action": issue.suggested_action,
            })

    # Group issues by type
    issues_by_type: dict[str, int] = {}
    for issue in all_issues:
        issue_type = issue["type"]
        issues_by_type[issue_type] = issues_by_type.get(issue_type, 0) + 1

    avg_confidence = (
        sum(r.confidence for r in results.values()) / total_pages
        if total_pages > 0
        else 0.0
    )

    return {
        "total_pages": total_pages,
        "passed_pages": passed_pages,
        "failed_pages": failed_pages,
        "pass_rate": passed_pages / total_pages if total_pages > 0 else 0.0,
        "average_confidence": avg_confidence,
        "total_issues": len(all_issues),
        "issues_by_type": issues_by_type,
        "all_issues": all_issues,
    }
