"""Models for the extraction validation system."""

from pydantic import BaseModel, Field


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
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Heuristically computed confidence score"
    )

    # Page tracking
    pages_found: list[int] = Field(
        default_factory=list,
        description="Page numbers found via <!-- Page N --> markers"
    )
    pages_missing: list[int] = Field(
        default_factory=list,
        description="Expected pages not found in output"
    )

    # Quality indicators
    unclear_text_count: int = Field(
        default=0,
        description="Number of [?] markers found (uncertain text)"
    )
    heading_count: int = Field(
        default=0,
        description="Number of headings found"
    )
    image_placeholder_count: int = Field(
        default=0,
        description="Number of image placeholders found"
    )
    table_count: int = Field(
        default=0,
        description="Number of markdown tables found"
    )

    # Validation
    reading_order_valid: bool = Field(
        default=True,
        description="Whether heading order matches manifest"
    )
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="List of validation issues found"
    )

    # Overall status
    is_valid: bool = Field(
        default=False,
        description="Whether extraction passed all critical validations"
    )

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


__all__ = [
    "ValidationIssue",
    "ExtractionMetrics",
    "ExtractionResult",
]
