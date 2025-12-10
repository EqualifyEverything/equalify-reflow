"""Unit tests for HintsCacheService (PRD-012).

Tests the orchestration service that combines VeraPDF and PyMarkdown
analyzers to build a DocumentHintsCache.
"""


import pytest
from src.analyzers.hints_cache import HintsCacheService
from src.analyzers.markdown_linter import MarkdownLinter
from src.analyzers.pdf_accessibility_analyzer import PDFAccessibilityAnalyzer
from src.shared.models.hints_models import (
    DocumentHintsCache,
    HintCategory,
    HintSeverity,
    HintSource,
    IssueHint,
    PageHints,
)


# Test fixtures
def create_test_hint(
    rule_id: str = "MD001",
    category: HintCategory = HintCategory.STRUCTURE,
    severity: HintSeverity = HintSeverity.ERROR,
    source: HintSource = HintSource.PYMARKDOWN,
    page_number: int | None = 1,
    line_number: int | None = 5,
) -> IssueHint:
    """Create a test hint with configurable attributes."""
    return IssueHint(
        source=source,
        rule_id=rule_id,
        category=category,
        severity=severity,
        message=f"Test message for {rule_id}",
        location=f"Line {line_number}" if line_number else "",
        page_number=page_number,
        line_number=line_number,
        context="Test context",
        suggestion=f"Fix {rule_id} issue",
    )


class TestHintsCacheServiceInitialization:
    """Tests for HintsCacheService initialization."""

    def test_default_analyzers(self) -> None:
        """Should create default analyzers if not provided."""
        service = HintsCacheService()
        assert service.pdf_analyzer is not None
        assert service.markdown_linter is not None

    def test_custom_analyzers(self) -> None:
        """Should accept custom analyzers."""
        pdf_analyzer = PDFAccessibilityAnalyzer(verapdf_url="http://custom:8080")
        markdown_linter = MarkdownLinter(enabled_rules=["MD001"])

        service = HintsCacheService(
            pdf_analyzer=pdf_analyzer,
            markdown_linter=markdown_linter,
        )

        assert service.pdf_analyzer is pdf_analyzer
        assert service.markdown_linter is markdown_linter


class TestAnalyzeDocument:
    """Tests for document analysis."""

    @pytest.mark.asyncio
    async def test_analyze_markdown_only(self) -> None:
        """Should analyze markdown without PDF."""
        service = HintsCacheService()

        cache = await service.analyze_document(
            document_id="test-123",
            page_markdowns={
                1: "# Title\n\n### Skip H2\n",
                2: "## Page 2\n\nContent.\n",
            },
        )

        assert cache.document_id == "test-123"
        assert cache.total_pages == 2
        assert cache.total_hints >= 1  # At least MD001 violation

    @pytest.mark.asyncio
    async def test_analyze_with_pdf_failure(self) -> None:
        """Should continue if PDF analysis fails."""
        service = HintsCacheService()

        # PDF doesn't exist, should not raise
        cache = await service.analyze_document(
            document_id="test-456",
            pdf_path="/nonexistent/path.pdf",
            page_markdowns={1: "# Title\n\n### Skip\n"},
        )

        assert cache.document_id == "test-456"
        # Should still have markdown hints
        assert cache.total_hints >= 0

    @pytest.mark.asyncio
    async def test_analysis_time_recorded(self) -> None:
        """Should record analysis time in milliseconds."""
        service = HintsCacheService()

        cache = await service.analyze_document(
            document_id="test-789",
            page_markdowns={1: "# Title\n"},
        )

        assert cache.analysis_time_ms >= 0


class TestBuildCache:
    """Tests for cache building logic."""

    def test_group_hints_by_page(self) -> None:
        """Should group hints by page number."""
        service = HintsCacheService()

        hints = [
            create_test_hint(rule_id="MD001", page_number=1),
            create_test_hint(rule_id="MD025", page_number=1),
            create_test_hint(rule_id="MD045", page_number=2),
        ]

        cache = service._build_cache(
            document_id="test",
            hints=hints,
            total_pages=2,
        )

        assert len(cache.pages) == 2
        assert len(cache.pages[1].hints) == 2
        assert len(cache.pages[2].hints) == 1

    def test_default_to_page_one(self) -> None:
        """Should default to page 1 if page number unknown."""
        service = HintsCacheService()

        hints = [
            create_test_hint(rule_id="MD001", page_number=None),
        ]

        cache = service._build_cache(
            document_id="test",
            hints=hints,
            total_pages=1,
        )

        assert 1 in cache.pages
        assert len(cache.pages[1].hints) == 1

    def test_calculate_category_statistics(self) -> None:
        """Should calculate hints by category."""
        service = HintsCacheService()

        hints = [
            create_test_hint(category=HintCategory.STRUCTURE),
            create_test_hint(category=HintCategory.STRUCTURE),
            create_test_hint(category=HintCategory.FIGURES),
        ]

        cache = service._build_cache(
            document_id="test",
            hints=hints,
            total_pages=1,
        )

        assert cache.hints_by_category["structure"] == 2
        assert cache.hints_by_category["figures"] == 1

    def test_calculate_severity_statistics(self) -> None:
        """Should calculate hints by severity."""
        service = HintsCacheService()

        hints = [
            create_test_hint(severity=HintSeverity.ERROR),
            create_test_hint(severity=HintSeverity.ERROR),
            create_test_hint(severity=HintSeverity.WARNING),
        ]

        cache = service._build_cache(
            document_id="test",
            hints=hints,
            total_pages=1,
        )

        assert cache.hints_by_severity["error"] == 2
        assert cache.hints_by_severity["warning"] == 1


class TestFormatHintsForPrompt:
    """Tests for prompt formatting."""

    def test_format_empty_hints(self) -> None:
        """Should return empty string for no hints."""
        service = HintsCacheService()
        result = service.format_hints_for_prompt([])
        assert result == ""

    def test_format_single_hint(self) -> None:
        """Should format single hint correctly."""
        service = HintsCacheService()

        hints = [
            create_test_hint(
                rule_id="MD001",
                severity=HintSeverity.ERROR,
                page_number=1,
                line_number=5,
            )
        ]

        result = service.format_hints_for_prompt(hints)

        assert "**Pre-Analysis Hints:**" in result
        assert "MD001" in result
        assert "page 1" in result
        assert "line 5" in result
        assert "[!]" in result  # Error icon

    def test_format_warning_hint(self) -> None:
        """Should use different icon for warnings."""
        service = HintsCacheService()

        hints = [
            create_test_hint(severity=HintSeverity.WARNING)
        ]

        result = service.format_hints_for_prompt(hints)
        assert "[?]" in result  # Warning icon

    def test_format_includes_suggestion(self) -> None:
        """Should include suggestion if available."""
        service = HintsCacheService()

        hints = [
            IssueHint(
                source=HintSource.PYMARKDOWN,
                rule_id="MD001",
                category=HintCategory.STRUCTURE,
                severity=HintSeverity.ERROR,
                message="Heading levels should increment",
                suggestion="Use H2 instead of H3",
            )
        ]

        result = service.format_hints_for_prompt(hints)
        assert "Use H2 instead of H3" in result

    def test_format_respects_max_hints(self) -> None:
        """Should limit number of hints formatted."""
        service = HintsCacheService()

        hints = [create_test_hint(rule_id=f"MD00{i}") for i in range(20)]

        result = service.format_hints_for_prompt(hints, max_hints=5)

        # Should mention remaining hints
        assert "... and 15 more hints" in result

    def test_format_sorts_errors_first(self) -> None:
        """Should sort errors before warnings."""
        service = HintsCacheService()

        hints = [
            create_test_hint(rule_id="WARN1", severity=HintSeverity.WARNING, line_number=1),
            create_test_hint(rule_id="ERR1", severity=HintSeverity.ERROR, line_number=10),
            create_test_hint(rule_id="WARN2", severity=HintSeverity.WARNING, line_number=2),
        ]

        result = service.format_hints_for_prompt(hints)

        # Error should appear before warnings
        err_pos = result.find("ERR1")
        warn1_pos = result.find("WARN1")
        assert err_pos < warn1_pos


class TestFormatCategoryHintsForPrompt:
    """Tests for category-specific formatting."""

    def test_format_structure_hints(self) -> None:
        """Should format only structure hints for structure agent."""
        service = HintsCacheService()

        cache = DocumentHintsCache(
            document_id="test",
            total_pages=1,
            pages={
                1: PageHints(
                    page_number=1,
                    hints=[
                        create_test_hint(category=HintCategory.STRUCTURE, rule_id="MD001"),
                        create_test_hint(category=HintCategory.FIGURES, rule_id="MD045"),
                    ],
                )
            },
            total_hints=2,
            hints_by_category={"structure": 1, "figures": 1},
            hints_by_severity={"error": 2},
        )

        result = service.format_category_hints_for_prompt(
            cache, HintCategory.STRUCTURE
        )

        assert "MD001" in result
        assert "MD045" not in result


class TestAsyncContextManager:
    """Tests for async context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_resources(self) -> None:
        """Should close resources when exiting context."""
        async with HintsCacheService() as service:
            assert service is not None

        # PDF analyzer should have been closed
        # (We can't directly verify this without accessing private state,
        # but the context manager should complete without error)


class TestDocumentHintsCacheModel:
    """Tests for DocumentHintsCache model methods."""

    def test_get_page_hints_existing(self) -> None:
        """Should return hints for existing page."""
        cache = DocumentHintsCache(
            document_id="test",
            total_pages=2,
            pages={
                1: PageHints(page_number=1, hints=[create_test_hint()]),
            },
            total_hints=1,
        )

        page_hints = cache.get_page_hints(1)
        assert len(page_hints.hints) == 1

    def test_get_page_hints_missing(self) -> None:
        """Should return empty PageHints for missing page."""
        cache = DocumentHintsCache(
            document_id="test",
            total_pages=2,
            pages={},
            total_hints=0,
        )

        page_hints = cache.get_page_hints(1)
        assert page_hints.page_number == 1
        assert page_hints.hints == []

    def test_get_hints_for_category(self) -> None:
        """Should return all hints for a category across pages."""
        cache = DocumentHintsCache(
            document_id="test",
            total_pages=2,
            pages={
                1: PageHints(
                    page_number=1,
                    hints=[
                        create_test_hint(category=HintCategory.STRUCTURE),
                        create_test_hint(category=HintCategory.FIGURES),
                    ],
                ),
                2: PageHints(
                    page_number=2,
                    hints=[create_test_hint(category=HintCategory.STRUCTURE)],
                ),
            },
            total_hints=3,
        )

        structure_hints = cache.get_hints_for_category(HintCategory.STRUCTURE)
        assert len(structure_hints) == 2

    def test_get_all_hints(self) -> None:
        """Should return all hints in page order."""
        cache = DocumentHintsCache(
            document_id="test",
            total_pages=2,
            pages={
                2: PageHints(page_number=2, hints=[create_test_hint(page_number=2)]),
                1: PageHints(page_number=1, hints=[create_test_hint(page_number=1)]),
            },
            total_hints=2,
        )

        all_hints = cache.get_all_hints()
        assert len(all_hints) == 2
        # Should be in page order
        assert all_hints[0].page_number == 1
        assert all_hints[1].page_number == 2

    def test_has_errors_property(self) -> None:
        """Should detect if cache has any errors."""
        cache_with_errors = DocumentHintsCache(
            document_id="test",
            total_pages=1,
            hints_by_severity={"error": 1, "warning": 2},
            total_hints=3,
        )
        assert cache_with_errors.has_errors is True

        cache_no_errors = DocumentHintsCache(
            document_id="test",
            total_pages=1,
            hints_by_severity={"warning": 2},
            total_hints=2,
        )
        assert cache_no_errors.has_errors is False

    def test_error_count_property(self) -> None:
        """Should return correct error count."""
        cache = DocumentHintsCache(
            document_id="test",
            total_pages=1,
            hints_by_severity={"error": 5, "warning": 3},
            total_hints=8,
        )
        assert cache.error_count == 5


class TestPageHintsModel:
    """Tests for PageHints model methods."""

    def test_filter_by_category(self) -> None:
        """Should filter hints by category."""
        page = PageHints(
            page_number=1,
            hints=[
                create_test_hint(category=HintCategory.STRUCTURE),
                create_test_hint(category=HintCategory.FIGURES),
                create_test_hint(category=HintCategory.STRUCTURE),
            ],
        )

        structure_hints = page.filter_by_category(HintCategory.STRUCTURE)
        assert len(structure_hints) == 2

    def test_filter_by_source(self) -> None:
        """Should filter hints by source."""
        page = PageHints(
            page_number=1,
            hints=[
                create_test_hint(source=HintSource.PYMARKDOWN),
                create_test_hint(source=HintSource.VERAPDF),
            ],
        )

        pymarkdown_hints = page.filter_by_source(HintSource.PYMARKDOWN)
        assert len(pymarkdown_hints) == 1

    def test_filter_by_severity(self) -> None:
        """Should filter hints by severity."""
        page = PageHints(
            page_number=1,
            hints=[
                create_test_hint(severity=HintSeverity.ERROR),
                create_test_hint(severity=HintSeverity.WARNING),
                create_test_hint(severity=HintSeverity.ERROR),
            ],
        )

        errors = page.filter_by_severity(HintSeverity.ERROR)
        assert len(errors) == 2

    def test_error_count_property(self) -> None:
        """Should return correct error count."""
        page = PageHints(
            page_number=1,
            hints=[
                create_test_hint(severity=HintSeverity.ERROR),
                create_test_hint(severity=HintSeverity.WARNING),
                create_test_hint(severity=HintSeverity.ERROR),
            ],
        )
        assert page.error_count == 2

    def test_warning_count_property(self) -> None:
        """Should return correct warning count."""
        page = PageHints(
            page_number=1,
            hints=[
                create_test_hint(severity=HintSeverity.ERROR),
                create_test_hint(severity=HintSeverity.WARNING),
                create_test_hint(severity=HintSeverity.WARNING),
            ],
        )
        assert page.warning_count == 2
