"""Hint models for deterministic pre-analysis.

This module defines the data models for storing accessibility hints
detected by deterministic tools (VeraPDF, PyMarkdown) before LLM processing.
"""

from enum import Enum

from pydantic import BaseModel, Field


class HintSource(str, Enum):
    """Source of the accessibility hint."""

    VERAPDF = "verapdf"
    PYMARKDOWN = "pymarkdown"
    SPELL_CHECK = "spell_check"


class HintSeverity(str, Enum):
    """Severity level for hints."""

    ERROR = "error"  # Must be addressed
    WARNING = "warning"  # Should be addressed
    INFO = "info"  # Optional improvement


class HintCategory(str, Enum):
    """Category for routing hints to specialist agents.

    Categories map to specialist agents:
    - STRUCTURE -> StructureAgent (headings, lists, reading order)
    - TYPOGRAPHY -> TypographyAgent (emphasis, semantic markup)
    - FIGURES -> FiguresAgent (alt text, image classification)
    - TABLES -> TablesAgent (table structure, headers)
    - GENERAL -> Cross-cutting concerns, may apply to multiple agents
    """

    STRUCTURE = "structure"
    TYPOGRAPHY = "typography"
    FIGURES = "figures"
    TABLES = "tables"
    GENERAL = "general"


class IssueHint(BaseModel):
    """Individual hint from deterministic analysis.

    Represents a single issue detected by a deterministic tool
    (VeraPDF or PyMarkdown) that should be addressed by a specialist agent.

    Attributes:
        source: Tool that detected this issue
        rule_id: Rule identifier (e.g., MD001, WCAG-2.1, PDF/UA-1:7.2)
        category: Category for agent routing
        severity: Issue severity level
        message: Human-readable description
        location: Location in document (e.g., "Page 3", "Line 45")
        page_number: Page number if applicable (1-indexed)
        line_number: Line number in markdown if applicable (1-indexed)
        context: Surrounding text for context (max 500 chars)
        suggestion: Suggested fix if available

    Example:
        >>> hint = IssueHint(
        ...     source=HintSource.PYMARKDOWN,
        ...     rule_id="MD001",
        ...     category=HintCategory.STRUCTURE,
        ...     severity=HintSeverity.ERROR,
        ...     message="Heading levels should increment by one",
        ...     line_number=15,
        ...     suggestion="Use H2 instead of H3 after H1"
        ... )
    """

    source: HintSource = Field(..., description="Tool that detected this issue")
    rule_id: str = Field(..., description="Rule identifier (e.g., MD001, WCAG-2.1)")
    category: HintCategory = Field(..., description="Category for agent routing")
    severity: HintSeverity = Field(..., description="Issue severity")
    message: str = Field(..., description="Human-readable description")
    location: str = Field(default="", description="Location in document")
    page_number: int | None = Field(default=None, ge=1)
    line_number: int | None = Field(default=None, ge=1)
    context: str = Field(default="", max_length=500, description="Surrounding text")
    suggestion: str = Field(default="", description="Suggested fix if available")


class PageHints(BaseModel):
    """Hints for a single page.

    Groups all hints detected on a specific page, with convenience
    methods for filtering by category or source.

    Attributes:
        page_number: Page number (1-indexed)
        hints: List of hints detected on this page
    """

    page_number: int = Field(..., ge=1)
    hints: list[IssueHint] = Field(default_factory=list)

    def filter_by_category(self, category: HintCategory) -> list[IssueHint]:
        """Get hints for a specific category.

        Args:
            category: The category to filter by

        Returns:
            List of hints matching the category
        """
        return [h for h in self.hints if h.category == category]

    def filter_by_source(self, source: HintSource) -> list[IssueHint]:
        """Get hints from a specific source.

        Args:
            source: The source to filter by

        Returns:
            List of hints from the specified source
        """
        return [h for h in self.hints if h.source == source]

    def filter_by_severity(self, severity: HintSeverity) -> list[IssueHint]:
        """Get hints with a specific severity.

        Args:
            severity: The severity to filter by

        Returns:
            List of hints with the specified severity
        """
        return [h for h in self.hints if h.severity == severity]

    @property
    def error_count(self) -> int:
        """Count of ERROR severity hints."""
        return len(self.filter_by_severity(HintSeverity.ERROR))

    @property
    def warning_count(self) -> int:
        """Count of WARNING severity hints."""
        return len(self.filter_by_severity(HintSeverity.WARNING))


class DocumentHintsCache(BaseModel):
    """Cached hints for entire document.

    Aggregates hints from all pages with statistics and convenience
    methods for accessing hints by page or category.

    Attributes:
        document_id: Job ID or document identifier
        total_pages: Total number of pages analyzed
        pages: Dictionary mapping page number to PageHints
        analysis_time_ms: Time taken for pre-analysis in milliseconds
        total_hints: Total number of hints across all pages
        hints_by_category: Count of hints per category
        hints_by_severity: Count of hints per severity level

    Example:
        >>> cache = DocumentHintsCache(
        ...     document_id="job-123",
        ...     total_pages=5,
        ...     pages={1: PageHints(page_number=1, hints=[...])},
        ...     total_hints=10,
        ...     hints_by_category={"structure": 5, "figures": 3, "tables": 2}
        ... )
    """

    document_id: str = Field(..., description="Job ID or document identifier")
    total_pages: int = Field(..., ge=1)
    pages: dict[int, PageHints] = Field(default_factory=dict)
    analysis_time_ms: int = Field(default=0, ge=0)

    # Summary statistics
    total_hints: int = Field(default=0, ge=0)
    hints_by_category: dict[str, int] = Field(default_factory=dict)
    hints_by_severity: dict[str, int] = Field(default_factory=dict)

    def get_page_hints(self, page_number: int) -> PageHints:
        """Get hints for a specific page.

        Args:
            page_number: Page number (1-indexed)

        Returns:
            PageHints for the page, or empty PageHints if not found
        """
        return self.pages.get(page_number, PageHints(page_number=page_number))

    def get_hints_for_category(self, category: HintCategory) -> list[IssueHint]:
        """Get all hints for a specific agent category.

        Useful for routing hints to specialist agents.

        Args:
            category: The category to filter by

        Returns:
            List of all hints matching the category across all pages
        """
        all_hints: list[IssueHint] = []
        for page in self.pages.values():
            all_hints.extend(page.filter_by_category(category))
        return all_hints

    def get_hints_for_source(self, source: HintSource) -> list[IssueHint]:
        """Get all hints from a specific source.

        Args:
            source: The source to filter by

        Returns:
            List of all hints from the specified source
        """
        all_hints: list[IssueHint] = []
        for page in self.pages.values():
            all_hints.extend(page.filter_by_source(source))
        return all_hints

    def get_all_hints(self) -> list[IssueHint]:
        """Get all hints across all pages.

        Returns:
            Flat list of all hints in document order
        """
        all_hints: list[IssueHint] = []
        for page_num in sorted(self.pages.keys()):
            all_hints.extend(self.pages[page_num].hints)
        return all_hints

    @property
    def has_errors(self) -> bool:
        """Check if document has any ERROR severity hints."""
        return self.hints_by_severity.get("error", 0) > 0

    @property
    def error_count(self) -> int:
        """Total count of ERROR severity hints."""
        return self.hints_by_severity.get("error", 0)

    @property
    def warning_count(self) -> int:
        """Total count of WARNING severity hints."""
        return self.hints_by_severity.get("warning", 0)
