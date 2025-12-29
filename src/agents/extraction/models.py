"""Models for the extraction validation system."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import HeadingNode

# Continuation markers for cross-page sentence handling
CONTINUATION_MARKER_FROM = "[CONTINUES_FROM_PREVIOUS]"
CONTINUATION_MARKER_TO = "[CONTINUES_ON_NEXT]"


class ValidationIssue(BaseModel):
    """A single validation issue found in extraction output."""

    issue_type: str = Field(description="Type of issue: missing_page, heading_order, truncated, etc.")
    severity: str = Field(description="critical, warning, or info")
    message: str = Field(description="Human-readable description of the issue")
    page_num: int | None = Field(default=None, description="Page number if applicable")

    def to_correction_guidance(self) -> str:
        """Format this issue as correction guidance for the model."""
        if self.page_num:
            return f"- {self.issue_type.upper()} (page {self.page_num}): {self.message}"
        return f"- {self.issue_type.upper()}: {self.message}"


class ExtractionMetrics(BaseModel):
    """Metrics and validation results from extraction output.

    These are computed heuristically from the markdown output,
    NOT from AI-provided values. This is more reliable.
    """

    # Computed confidence (not AI-provided)
    confidence: float = Field(ge=0.0, le=1.0, description="Heuristically computed confidence score")

    # Page tracking
    pages_found: list[int] = Field(default_factory=list, description="Page numbers found via <!-- Page N --> markers")
    pages_missing: list[int] = Field(default_factory=list, description="Expected pages not found in output")

    # Quality indicators
    unclear_text_count: int = Field(default=0, description="Number of [?] markers found (uncertain text)")
    heading_count: int = Field(default=0, description="Number of headings found")
    image_placeholder_count: int = Field(default=0, description="Number of image placeholders found")
    table_count: int = Field(default=0, description="Number of markdown tables found")

    # Validation
    reading_order_valid: bool = Field(default=True, description="Whether heading order matches manifest")
    issues: list[ValidationIssue] = Field(default_factory=list, description="List of validation issues found")

    # Overall status
    is_valid: bool = Field(default=False, description="Whether extraction passed all critical validations")

    @property
    def critical_issues(self) -> list[ValidationIssue]:
        """Get only critical issues that require correction."""
        return [i for i in self.issues if i.severity == "critical"]

    @property
    def has_critical_issues(self) -> bool:
        """Check if there are any critical issues."""
        return len(self.critical_issues) > 0

    def get_correction_guidance(self) -> str:
        """Build correction guidance string from critical issues."""
        if not self.critical_issues:
            return ""

        lines = ["The previous extraction had the following issues that need correction:"]
        lines.append("")
        for issue in self.critical_issues:
            lines.append(issue.to_correction_guidance())

        return "\n".join(lines)


class ExtractionResult(BaseModel):
    """Complete result from extraction including markdown and metrics."""

    markdown: str = Field(description="The extracted markdown content")
    metrics: ExtractionMetrics = Field(description="Validation metrics")
    attempt_count: int = Field(default=1, description="Number of extraction attempts")
    correction_applied: bool = Field(default=False, description="Whether correction was applied")


class PageMetrics(BaseModel):
    """Validation metrics for a single page extraction."""

    page_num: int = Field(description="Page number")
    has_page_marker: bool = Field(description="Whether page marker is present")
    word_count: int = Field(description="Number of words extracted")
    expected_headings_found: list[str] = Field(default_factory=list, description="Expected headings that were found")
    expected_headings_missing: list[str] = Field(default_factory=list, description="Expected headings that are missing")
    has_continuation_from: bool = Field(default=False, description="Page starts with continuation marker")
    has_continuation_to: bool = Field(default=False, description="Page ends with continuation marker")
    issues: list[ValidationIssue] = Field(default_factory=list, description="Validation issues for this page")
    is_valid: bool = Field(description="Whether page passed validation")

    @property
    def critical_issues(self) -> list[ValidationIssue]:
        """Get only critical issues that require correction."""
        return [i for i in self.issues if i.severity == "critical"]

    def get_correction_guidance(self) -> str:
        """Build correction guidance string from all issues."""
        if not self.issues:
            return ""

        lines = [f"Issues found on page {self.page_num}:"]
        for issue in self.issues:
            lines.append(issue.to_correction_guidance())

        return "\n".join(lines)


@dataclass
class PageContext:
    """Context for extracting a single page."""

    page_num: int
    image_base64: str
    layout_type: str
    headings_on_page: list[HeadingNode]
    document_summary: str
    document_title: str
    document_type: str
    total_pages: int
    # Mutable field for tracking retry issues
    previous_issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class PageExtractionResult:
    """Result from extracting a single page."""

    page_num: int
    markdown: str
    metrics: PageMetrics
    attempt_count: int
    usage: LLMUsage


__all__ = [
    "ValidationIssue",
    "ExtractionMetrics",
    "ExtractionResult",
    "PageMetrics",
    "PageContext",
    "PageExtractionResult",
    "CONTINUATION_MARKER_FROM",
    "CONTINUATION_MARKER_TO",
]
