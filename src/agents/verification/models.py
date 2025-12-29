"""Models for verification agent output.

These models define the structured output from the verification agent,
which compares extracted markdown against source page images.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VerificationCorrection(BaseModel):
    """A correction identified by comparing markdown to source image.

    The search text should be unique enough to match exactly once.
    Include surrounding context if needed for uniqueness.

    Attributes:
        search: The exact text to find in the markdown
        replace: The corrected text based on the source image
        issue_type: Category of the issue
        confidence: How confident the agent is (0.0-1.0)
        reasoning: Why this correction is needed

    Example:
        >>> correction = VerificationCorrection(
        ...     search="stimulation platforms",
        ...     replace="simulation platforms",
        ...     issue_type="ocr_error",
        ...     confidence=0.95,
        ...     reasoning="Image clearly shows 'simulation' not 'stimulation'"
        ... )
    """

    search: str = Field(..., description="Exact text to find (include context for uniqueness)")
    replace: str = Field(..., description="Corrected text based on source image")
    issue_type: Literal[
        "ocr_error", "missing_text", "extra_text", "double_word", "truncated", "formatting", "other"
    ] = Field(..., description="Category of the issue")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this correction (0.0-1.0)")
    reasoning: str = Field(..., description="Why this correction is needed")


class VerificationIssue(BaseModel):
    """An issue that couldn't be auto-corrected.

    Used for issues that need human review rather than auto-fix.

    Attributes:
        description: What the issue is
        issue_type: Category of the issue
        location: Where in the text (approximate)
        suggestion: What might fix it
    """

    description: str = Field(..., description="What the issue is")
    issue_type: str = Field(..., description="Category of the issue")
    location: str = Field(..., description="Approximate location in text")
    suggestion: str | None = Field(default=None, description="Suggested fix if known")


class PageVerificationOutput(BaseModel):
    """Output from verifying a single page.

    This is what the LLM returns for each page verification.

    Attributes:
        page_num: The page number being verified
        corrections: List of corrections to apply
        issues: Issues that need human review
        is_accurate: Whether the markdown accurately represents the image
        summary: Brief summary of findings
    """

    page_num: int = Field(..., description="Page number")
    corrections: list[VerificationCorrection] = Field(default_factory=list, description="Corrections to apply")
    issues: list[VerificationIssue] = Field(default_factory=list, description="Issues needing human review")
    is_accurate: bool = Field(..., description="Whether markdown accurately represents the image")
    summary: str = Field(..., description="Brief summary of verification findings")


class PageVerificationResult(BaseModel):
    """Full result from verifying a single page.

    Extends PageVerificationOutput with metadata.

    Attributes:
        page_num: The page number
        corrections: Corrections identified
        corrections_applied: How many were successfully applied
        corrections_failed: How many failed to apply
        issues: Issues needing human review
        is_accurate: Whether markdown was accurate
        summary: Summary of findings
        cost_cents: LLM cost for this page
    """

    page_num: int
    corrections: list[VerificationCorrection] = Field(default_factory=list)
    corrections_applied: int = 0
    corrections_failed: int = 0
    issues: list[VerificationIssue] = Field(default_factory=list)
    is_accurate: bool = True
    summary: str = ""
    cost_cents: float = 0.0


class VerificationResult(BaseModel):
    """Aggregate result from verifying all pages.

    Attributes:
        corrected_markdown: The final corrected markdown
        page_results: Results for each page
        total_corrections_applied: Total corrections applied across all pages
        total_corrections_failed: Total corrections that failed
        total_issues: Total issues needing human review
        all_pages_accurate: Whether all pages were accurate
        cost_cents: Total LLM cost
    """

    corrected_markdown: str
    page_results: list[PageVerificationResult] = Field(default_factory=list)
    total_corrections_applied: int = 0
    total_corrections_failed: int = 0
    total_issues: int = 0
    all_pages_accurate: bool = True
    cost_cents: float = 0.0
