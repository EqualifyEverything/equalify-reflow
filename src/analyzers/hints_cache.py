"""Hints cache service for deterministic pre-analysis.

This module orchestrates the VeraPDF and PyMarkdown analyzers to build
a complete DocumentHintsCache for use by specialist agents.
"""

import logging
import time
from pathlib import Path

from src.shared.models.hints_models import (
    DocumentHintsCache,
    HintCategory,
    HintSeverity,
    IssueHint,
    PageHints,
)

from .markdown_linter import MarkdownLinter
from .pdf_accessibility_analyzer import PDFAccessibilityAnalyzer

logger = logging.getLogger(__name__)


class HintsCacheService:
    """Service to analyze documents and cache hints for agents.

    Orchestrates deterministic analyzers (VeraPDF, PyMarkdown) to build
    a DocumentHintsCache that provides targeted hints to specialist agents.

    Attributes:
        pdf_analyzer: VeraPDF analyzer for PDF accessibility
        markdown_linter: PyMarkdown linter for markdown structure

    Example:
        >>> service = HintsCacheService()
        >>> cache = await service.analyze_document(
        ...     document_id="job-123",
        ...     pdf_path="/path/to/doc.pdf",
        ...     page_markdowns={1: "# Title\\n\\nContent..."}
        ... )
        >>> print(f"Found {cache.total_hints} hints")
        >>> structure_hints = cache.get_hints_for_category(HintCategory.STRUCTURE)
    """

    def __init__(
        self,
        pdf_analyzer: PDFAccessibilityAnalyzer | None = None,
        markdown_linter: MarkdownLinter | None = None,
    ) -> None:
        """Initialize with analyzers.

        Args:
            pdf_analyzer: VeraPDF analyzer (created if not provided)
            markdown_linter: PyMarkdown linter (created if not provided)
        """
        self.pdf_analyzer = pdf_analyzer or PDFAccessibilityAnalyzer()
        self.markdown_linter = markdown_linter or MarkdownLinter()
        logger.debug("HintsCacheService initialized")

    async def close(self) -> None:
        """Close resources (HTTP clients, etc.)."""
        await self.pdf_analyzer.close()

    async def __aenter__(self) -> "HintsCacheService":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        await self.close()

    async def analyze_document(
        self,
        document_id: str,
        pdf_path: Path | str | None = None,
        page_markdowns: dict[int, str] | None = None,
    ) -> DocumentHintsCache:
        """Analyze document and build hints cache.

        Runs both VeraPDF (if PDF path provided) and PyMarkdown (if markdown
        provided) analyzers, combining results into a single cache.

        Args:
            document_id: Job ID or document identifier
            pdf_path: Path to source PDF (for VeraPDF analysis)
            page_markdowns: Dict of page_number -> markdown content

        Returns:
            DocumentHintsCache with all detected hints
        """
        start_time = time.monotonic()
        logger.info(f"Starting pre-analysis for document: {document_id}")

        all_hints: list[IssueHint] = []

        # Run VeraPDF if PDF path provided
        if pdf_path:
            pdf_hints = await self._analyze_pdf(pdf_path)
            all_hints.extend(pdf_hints)

        # Run PyMarkdown on each page
        if page_markdowns:
            md_hints = self._analyze_markdowns(page_markdowns)
            all_hints.extend(md_hints)

        # Build cache from all hints
        total_pages = len(page_markdowns) if page_markdowns else 1
        cache = self._build_cache(
            document_id=document_id,
            hints=all_hints,
            total_pages=total_pages,
        )

        # Record analysis time
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        cache.analysis_time_ms = elapsed_ms

        logger.info(
            f"Pre-analysis complete: {cache.total_hints} hints in {elapsed_ms}ms "
            f"(errors: {cache.error_count}, warnings: {cache.warning_count})"
        )

        return cache

    async def _analyze_pdf(self, pdf_path: Path | str) -> list[IssueHint]:
        """Run VeraPDF analysis on PDF.

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of hints from VeraPDF, empty list if analysis fails
        """
        try:
            hints = await self.pdf_analyzer.analyze_pdf(pdf_path)
            logger.info(f"VeraPDF found {len(hints)} issues")
            return hints
        except FileNotFoundError as e:
            logger.warning(f"PDF not found for VeraPDF analysis: {e}")
            return []
        except Exception as e:
            logger.warning(f"VeraPDF analysis failed (continuing): {e}")
            return []

    def _analyze_markdowns(
        self,
        page_markdowns: dict[int, str],
    ) -> list[IssueHint]:
        """Run PyMarkdown analysis on all page markdowns.

        Args:
            page_markdowns: Dict of page_number -> markdown content

        Returns:
            List of hints from PyMarkdown
        """
        all_hints: list[IssueHint] = []

        for page_num, markdown in page_markdowns.items():
            hints = self.markdown_linter.analyze_markdown(
                markdown,
                page_number=page_num,
            )
            all_hints.extend(hints)

        logger.info(f"PyMarkdown found {len(all_hints)} issues")
        return all_hints

    def _build_cache(
        self,
        document_id: str,
        hints: list[IssueHint],
        total_pages: int,
    ) -> DocumentHintsCache:
        """Build DocumentHintsCache from list of hints.

        Args:
            document_id: Document identifier
            hints: All hints to include
            total_pages: Total number of pages

        Returns:
            Fully populated DocumentHintsCache
        """
        # Group hints by page
        pages: dict[int, PageHints] = {}

        for hint in hints:
            page_num = hint.page_number or 1  # Default to page 1 if unknown

            if page_num not in pages:
                pages[page_num] = PageHints(page_number=page_num)

            pages[page_num].hints.append(hint)

        # Calculate statistics
        by_category: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        for hint in hints:
            category_key = hint.category.value
            severity_key = hint.severity.value

            by_category[category_key] = by_category.get(category_key, 0) + 1
            by_severity[severity_key] = by_severity.get(severity_key, 0) + 1

        return DocumentHintsCache(
            document_id=document_id,
            total_pages=total_pages,
            pages=pages,
            total_hints=len(hints),
            hints_by_category=by_category,
            hints_by_severity=by_severity,
        )

    def format_hints_for_prompt(
        self,
        hints: list[IssueHint],
        max_hints: int = 10,
    ) -> str:
        """Format hints as text for inclusion in agent prompts.

        Formats hints in a concise, actionable format suitable for
        injection into LLM prompts.

        Args:
            hints: List of hints to format
            max_hints: Maximum hints to include (default: 10)

        Returns:
            Formatted string for prompt injection, empty if no hints
        """
        if not hints:
            return ""

        # Sort by severity (errors first) then by line number
        sorted_hints = sorted(
            hints,
            key=lambda h: (
                h.severity != HintSeverity.ERROR,  # Errors first
                h.line_number or 0,
            ),
        )[:max_hints]

        lines = ["**Pre-Analysis Hints:**"]

        for hint in sorted_hints:
            severity_icon = "!" if hint.severity == HintSeverity.ERROR else "?"
            location_parts = []

            if hint.page_number:
                location_parts.append(f"page {hint.page_number}")
            if hint.line_number:
                location_parts.append(f"line {hint.line_number}")

            location = f" ({', '.join(location_parts)})" if location_parts else ""

            lines.append(
                f"- [{severity_icon}] {hint.rule_id}{location}: {hint.message}"
            )

            if hint.suggestion:
                lines.append(f"  → {hint.suggestion}")

        if len(hints) > max_hints:
            remaining = len(hints) - max_hints
            lines.append(f"  ... and {remaining} more hints")

        return "\n".join(lines)

    def format_category_hints_for_prompt(
        self,
        cache: DocumentHintsCache,
        category: HintCategory,
        max_hints: int = 10,
    ) -> str:
        """Format hints for a specific category for agent prompts.

        Convenience method for agents to get only their relevant hints.

        Args:
            cache: Document hints cache
            category: Category to filter by
            max_hints: Maximum hints to include

        Returns:
            Formatted string for prompt injection
        """
        hints = cache.get_hints_for_category(category)
        return self.format_hints_for_prompt(hints, max_hints)
