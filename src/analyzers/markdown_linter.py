"""Markdown linter using PyMarkdown for structure validation (PRD-012).

This module wraps the PyMarkdown API to detect markdown structure issues
that impact accessibility, such as heading level skips and missing alt text.
"""

import logging
from typing import Any

from pymarkdown.api import PyMarkdownApi, PyMarkdownApiException

from src.shared.models.hints_models import (
    HintCategory,
    HintSeverity,
    HintSource,
    IssueHint,
)

logger = logging.getLogger(__name__)


# PyMarkdown rule to HintCategory mapping
# Maps rule IDs to categories for routing to specialist agents
PYMARKDOWN_CATEGORY_MAP: dict[str, HintCategory] = {
    # Structure rules - headings, document organization
    "MD001": HintCategory.STRUCTURE,  # Heading levels should only increment by one
    "MD002": HintCategory.STRUCTURE,  # First heading should be H1
    "MD003": HintCategory.STRUCTURE,  # Heading style
    "MD022": HintCategory.STRUCTURE,  # Headings should be surrounded by blank lines
    "MD024": HintCategory.STRUCTURE,  # Multiple headings with same content
    "MD025": HintCategory.STRUCTURE,  # Multiple top-level headings
    "MD041": HintCategory.STRUCTURE,  # First line should be top-level heading
    "MD043": HintCategory.STRUCTURE,  # Required heading structure
    # Typography rules - emphasis, semantic markup
    "MD036": HintCategory.TYPOGRAPHY,  # Emphasis used instead of heading
    "MD037": HintCategory.TYPOGRAPHY,  # Spaces inside emphasis markers
    "MD038": HintCategory.TYPOGRAPHY,  # Spaces inside code span
    "MD049": HintCategory.TYPOGRAPHY,  # Emphasis style
    "MD050": HintCategory.TYPOGRAPHY,  # Strong style
    # Figure rules - images
    "MD045": HintCategory.FIGURES,  # Images should have alt text
    # Table rules
    "MD055": HintCategory.TABLES,  # Table pipe style
    "MD056": HintCategory.TABLES,  # Table column count
    "MD058": HintCategory.TABLES,  # Tables should be surrounded by blank lines
}

# Rules that indicate ERROR severity (must be addressed for accessibility)
ERROR_RULES = {"MD001", "MD025", "MD045"}

# Default rules most relevant for accessibility correction
DEFAULT_ENABLED_RULES = [
    "MD001",  # Heading levels increment
    "MD025",  # Single H1
    "MD036",  # Emphasis as heading
    "MD045",  # Image alt text
]

# Actionable suggestions for each rule
RULE_SUGGESTIONS: dict[str, str] = {
    "MD001": "Ensure heading levels increase by one (H1 → H2 → H3)",
    "MD002": "Document should start with an H1 heading",
    "MD003": "Use consistent heading style throughout document",
    "MD022": "Add blank lines before and after headings",
    "MD024": "Use unique content for each heading",
    "MD025": "Document should have only one H1 heading",
    "MD036": "Use proper heading markup instead of bold/emphasis for headings",
    "MD037": "Remove spaces inside emphasis markers (**text** not ** text **)",
    "MD038": "Remove spaces inside code spans (`code` not ` code `)",
    "MD041": "Document should start with a top-level heading",
    "MD043": "Follow required heading structure for this document type",
    "MD045": "Add descriptive alt text to image",
    "MD049": "Use consistent emphasis style (* or _)",
    "MD050": "Use consistent strong emphasis style (** or __)",
    "MD055": "Use consistent table pipe style",
    "MD056": "Ensure all table rows have the same number of columns",
    "MD058": "Add blank lines before and after tables",
}


class MarkdownLinter:
    """Linter using PyMarkdown for markdown structure validation.

    Analyzes markdown content for structure issues that impact accessibility,
    such as heading level skips (MD001), multiple H1s (MD025), emphasis
    used as headings (MD036), and missing image alt text (MD045).

    Attributes:
        enabled_rules: List of rule IDs to check

    Example:
        >>> linter = MarkdownLinter()
        >>> hints = linter.analyze_markdown("# Title\\n\\n### Skip H2\\n", page_number=1)
        >>> len(hints)  # Should detect MD001 violation
        1
        >>> hints[0].rule_id
        'MD001'
    """

    def __init__(self, enabled_rules: list[str] | None = None) -> None:
        """Initialize with specific rules enabled.

        Args:
            enabled_rules: List of rule IDs to enable (default: DEFAULT_ENABLED_RULES)
        """
        self.enabled_rules = enabled_rules or DEFAULT_ENABLED_RULES
        logger.debug(f"MarkdownLinter initialized with rules: {self.enabled_rules}")

    def _create_configured_api(self) -> PyMarkdownApi:
        """Create and configure a PyMarkdownApi instance.

        Configures the API to only check the enabled rules by using
        selective enable mode.

        Returns:
            Configured PyMarkdownApi instance
        """
        api = PyMarkdownApi()

        # Suppress non-error logging from PyMarkdown
        api = api.log_error_and_above()

        # Enable selective mode - this disables all rules by default
        api = api.set_boolean_property("plugins.selectively_enable_rules", True)

        # Enable only our specified rules
        for rule_id in self.enabled_rules:
            property_name = f"plugins.{rule_id.lower()}.enabled"
            api = api.set_boolean_property(property_name, True)

        return api

    def analyze_markdown(
        self,
        markdown: str,
        page_number: int | None = None,
    ) -> list[IssueHint]:
        """Analyze markdown string for structure issues.

        Args:
            markdown: Markdown content to analyze
            page_number: Optional page number for context (1-indexed)

        Returns:
            List of IssueHint objects for detected violations
        """
        if not markdown or not markdown.strip():
            logger.debug(f"Empty markdown content (page {page_number}), skipping")
            return []

        logger.debug(
            f"Analyzing markdown (page {page_number}, {len(markdown)} chars)"
        )

        try:
            api = self._create_configured_api()
            result = api.scan_string(markdown)

            # Parse results into hints
            hints = self._parse_scan_results(result, markdown, page_number)

            logger.debug(
                f"PyMarkdown analysis complete: {len(hints)} issues "
                f"(page {page_number})"
            )

            return hints

        except PyMarkdownApiException as e:
            logger.error(f"PyMarkdown analysis failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in markdown analysis: {e}")
            return []

    def _parse_scan_results(
        self,
        result: Any,
        markdown: str,
        page_number: int | None,
    ) -> list[IssueHint]:
        """Parse PyMarkdown scan results into IssueHint objects.

        Args:
            result: PyMarkdownScanPathResult from scan_string
            markdown: Original markdown content for context extraction
            page_number: Page number for hints

        Returns:
            List of IssueHint objects
        """
        hints: list[IssueHint] = []

        for failure in result.scan_failures:
            rule_id = failure.rule_id.upper()

            # Map to category (default to GENERAL if unknown)
            category = PYMARKDOWN_CATEGORY_MAP.get(rule_id, HintCategory.GENERAL)

            # Determine severity based on rule importance
            severity = (
                HintSeverity.ERROR
                if rule_id in ERROR_RULES
                else HintSeverity.WARNING
            )

            # Extract context around the failure line
            context = self._extract_context(markdown, failure.line_number)

            # Get actionable suggestion
            suggestion = RULE_SUGGESTIONS.get(rule_id, "")

            # Build descriptive message
            message = failure.rule_description
            if failure.extra_error_information:
                message = f"{message}: {failure.extra_error_information}"

            hint = IssueHint(
                source=HintSource.PYMARKDOWN,
                rule_id=rule_id,
                category=category,
                severity=severity,
                message=message,
                location=f"Line {failure.line_number}, Column {failure.column_number}",
                page_number=page_number,
                line_number=failure.line_number,
                context=context,
                suggestion=suggestion,
            )
            hints.append(hint)

        return hints

    def _extract_context(self, markdown: str, line_number: int) -> str:
        """Extract context around a specific line.

        Gets 1 line before and after the target line for context.

        Args:
            markdown: Full markdown content
            line_number: Target line number (1-indexed)

        Returns:
            Context string (max 500 chars)
        """
        lines = markdown.split("\n")

        if line_number <= 0 or line_number > len(lines):
            return ""

        # Get 1 line before and after (0-indexed)
        start = max(0, line_number - 2)
        end = min(len(lines), line_number + 1)

        context_lines = lines[start:end]
        return "\n".join(context_lines)[:500]
