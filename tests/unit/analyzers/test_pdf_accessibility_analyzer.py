"""Unit tests for PDFAccessibilityAnalyzer (PRD-012).

Tests the VeraPDF response parsing and hint generation logic.
Uses mocked HTTP responses - integration tests cover actual VeraPDF calls.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.analyzers.pdf_accessibility_analyzer import (
    CLAUSE_CATEGORY_MAP,
    KEYWORD_CATEGORY_MAP,
    PDFAccessibilityAnalyzer,
    VERAPDF_CATEGORY_MAP,
)
from src.shared.models.hints_models import (
    HintCategory,
    HintSeverity,
    HintSource,
)


# Sample VeraPDF responses for testing
COMPLIANT_RESPONSE = {
    "report": {
        "jobs": [
            {
                "validationResult": {
                    "isCompliant": True,
                    "details": {"ruleSummaries": []},
                }
            }
        ]
    }
}

NON_COMPLIANT_RESPONSE = {
    "report": {
        "jobs": [
            {
                "validationResult": {
                    "isCompliant": False,
                    "details": {
                        "ruleSummaries": [
                            {
                                "specification": "ISO_14289-1:2014",
                                "clause": "7.2",
                                "testNumber": "1",
                                "description": "Document structure element missing",
                                "failedChecks": 2,
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/Page[1]/StructTreeRoot",
                                    },
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/Page[2]/StructTreeRoot",
                                    },
                                ],
                            },
                            {
                                "specification": "WCAG2AA-1-1-1",
                                "clause": "F65",
                                "testNumber": "",
                                "description": "Image without alternative text",
                                "failedChecks": 1,
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/Page[0]/Image[0]",
                                    },
                                ],
                            },
                        ]
                    },
                }
            }
        ]
    }
}

TABLE_ISSUES_RESPONSE = {
    "report": {
        "jobs": [
            {
                "validationResult": {
                    "isCompliant": False,
                    "details": {
                        "ruleSummaries": [
                            {
                                "specification": "WCAG2AA-1-3-1-H51",
                                "clause": "H51",
                                "testNumber": "1",
                                "description": "Table header cells not properly marked",
                                "failedChecks": 1,
                                "checks": [
                                    {
                                        "status": "failed",
                                        "context": "root/document[0]/Page[3]/Table[0]",
                                    },
                                ],
                            }
                        ]
                    },
                }
            }
        ]
    }
}


class TestAnalyzerInitialization:
    """Tests for PDFAccessibilityAnalyzer initialization."""

    def test_default_url(self) -> None:
        """Should use default URL from settings."""
        analyzer = PDFAccessibilityAnalyzer()
        assert "verapdf" in analyzer.verapdf_url.lower()

    def test_custom_url(self) -> None:
        """Should accept custom URL."""
        custom_url = "http://custom-verapdf:9090"
        analyzer = PDFAccessibilityAnalyzer(verapdf_url=custom_url)
        assert analyzer.verapdf_url == custom_url

    def test_custom_timeout(self) -> None:
        """Should accept custom timeout."""
        analyzer = PDFAccessibilityAnalyzer(timeout=60)
        assert analyzer.timeout == 60


class TestResultParsing:
    """Tests for VeraPDF response parsing."""

    def test_parse_compliant_document(self) -> None:
        """Should return empty hints for compliant document."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(COMPLIANT_RESPONSE)
        assert hints == []

    def test_parse_non_compliant_document(self) -> None:
        """Should extract hints from non-compliant document."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        # Should find 3 hints (2 structure + 1 alt text)
        assert len(hints) == 3

        # All should be from VeraPDF
        for hint in hints:
            assert hint.source == HintSource.VERAPDF
            assert hint.severity == HintSeverity.ERROR

    def test_structure_issues_categorized(self) -> None:
        """Structure issues should be categorized correctly."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        # Find structure hints
        structure_hints = [h for h in hints if h.category == HintCategory.STRUCTURE]
        assert len(structure_hints) >= 1

    def test_figure_issues_categorized(self) -> None:
        """Figure/alt text issues should be categorized correctly."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        # Find figure hints (alt text)
        figure_hints = [h for h in hints if h.category == HintCategory.FIGURES]
        assert len(figure_hints) >= 1

    def test_table_issues_categorized(self) -> None:
        """Table issues should be categorized correctly."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(TABLE_ISSUES_RESPONSE)

        table_hints = [h for h in hints if h.category == HintCategory.TABLES]
        assert len(table_hints) >= 1


class TestPageNumberExtraction:
    """Tests for page number extraction from context strings."""

    def test_extract_page_from_bracket_notation(self) -> None:
        """Should extract page from Page[N] notation."""
        analyzer = PDFAccessibilityAnalyzer()

        # 0-indexed in context -> 1-indexed in output
        assert analyzer._extract_page_number("root/document[0]/Page[0]/...") == 1
        assert analyzer._extract_page_number("root/document[0]/Page[2]/...") == 3
        assert analyzer._extract_page_number("Page[5]/StructTreeRoot") == 6

    def test_no_page_number(self) -> None:
        """Should return None when no page reference found."""
        analyzer = PDFAccessibilityAnalyzer()
        assert analyzer._extract_page_number("root/document[0]/Catalog") is None
        assert analyzer._extract_page_number("") is None


class TestRuleIdBuilding:
    """Tests for rule ID construction."""

    def test_pdfua_rule_id(self) -> None:
        """Should build PDF/UA-1 rule IDs correctly."""
        analyzer = PDFAccessibilityAnalyzer()
        rule_id = analyzer._build_rule_id("ISO_14289-1:2014", "7.2", "1")
        assert "PDF/UA-1" in rule_id
        assert "7.2" in rule_id

    def test_wcag_rule_id(self) -> None:
        """Should handle WCAG spec identifiers."""
        analyzer = PDFAccessibilityAnalyzer()
        rule_id = analyzer._build_rule_id("WCAG2AA-1-1-1", "F65", "")
        assert rule_id != ""

    def test_empty_test_number(self) -> None:
        """Should handle empty test number."""
        analyzer = PDFAccessibilityAnalyzer()
        rule_id = analyzer._build_rule_id("ISO_14289-1:2014", "7.2", "")
        assert "-" not in rule_id.split(":")[-1] or rule_id.endswith("7.2")


class TestCategoryDetermination:
    """Tests for category determination logic."""

    def test_specification_based_category(self) -> None:
        """Should use specification for category when mapped."""
        analyzer = PDFAccessibilityAnalyzer()
        category = analyzer._determine_category(
            "WCAG2AAA-1-1-1", "F65", "Alternative text missing"
        )
        assert category == HintCategory.FIGURES

    def test_clause_based_category(self) -> None:
        """Should fall back to clause for category."""
        analyzer = PDFAccessibilityAnalyzer()
        category = analyzer._determine_category(
            "UNKNOWN_SPEC", "7.4", "Table structure issue"
        )
        assert category == HintCategory.TABLES

    def test_keyword_based_category(self) -> None:
        """Should use keywords in description as fallback."""
        analyzer = PDFAccessibilityAnalyzer()
        category = analyzer._determine_category(
            "UNKNOWN_SPEC", "99.99", "Heading structure is invalid"
        )
        assert category == HintCategory.STRUCTURE

    def test_default_to_general(self) -> None:
        """Should default to GENERAL when no match found."""
        analyzer = PDFAccessibilityAnalyzer()
        category = analyzer._determine_category(
            "UNKNOWN", "0.0", "Unknown issue type"
        )
        assert category == HintCategory.GENERAL


class TestSuggestions:
    """Tests for suggestion generation."""

    def test_alt_text_suggestion(self) -> None:
        """Should provide alt text suggestion for image issues."""
        analyzer = PDFAccessibilityAnalyzer()
        suggestion = analyzer._get_suggestion(
            HintCategory.FIGURES, "Alternative text is missing"
        )
        assert "alternative" in suggestion.lower() or "alt" in suggestion.lower()

    def test_heading_suggestion(self) -> None:
        """Should provide heading suggestion for structure issues."""
        analyzer = PDFAccessibilityAnalyzer()
        suggestion = analyzer._get_suggestion(
            HintCategory.STRUCTURE, "Heading structure is invalid"
        )
        assert "heading" in suggestion.lower()

    def test_table_header_suggestion(self) -> None:
        """Should provide table suggestion for table issues."""
        analyzer = PDFAccessibilityAnalyzer()
        suggestion = analyzer._get_suggestion(
            HintCategory.TABLES, "Table header cells not marked"
        )
        assert "header" in suggestion.lower() or "table" in suggestion.lower()


class TestCategoryMaps:
    """Tests for category mapping dictionaries."""

    def test_verapdf_map_has_key_specs(self) -> None:
        """Should have mappings for key specifications."""
        # WCAG mappings
        assert "WCAG2AAA-1-1-1" in VERAPDF_CATEGORY_MAP
        assert "WCAG2AAA-1-3-1" in VERAPDF_CATEGORY_MAP

    def test_clause_map_has_pdfua_sections(self) -> None:
        """Should have mappings for PDF/UA clause sections."""
        assert "7.1" in CLAUSE_CATEGORY_MAP  # General structure
        assert "7.2" in CLAUSE_CATEGORY_MAP  # Document structure
        assert "7.4" in CLAUSE_CATEGORY_MAP  # Tables
        assert "7.18" in CLAUSE_CATEGORY_MAP  # Figures

    def test_keyword_map_has_accessibility_terms(self) -> None:
        """Should have mappings for accessibility keywords."""
        assert "heading" in KEYWORD_CATEGORY_MAP
        assert "alt text" in KEYWORD_CATEGORY_MAP
        assert "table" in KEYWORD_CATEGORY_MAP
        assert "figure" in KEYWORD_CATEGORY_MAP


class TestAsyncOperations:
    """Tests for async operations with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_analyze_pdf_file_not_found(self) -> None:
        """Should raise FileNotFoundError for missing PDF."""
        analyzer = PDFAccessibilityAnalyzer()
        with pytest.raises(FileNotFoundError):
            await analyzer.analyze_pdf("/nonexistent/path/to/file.pdf")

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Should work as async context manager."""
        async with PDFAccessibilityAnalyzer() as analyzer:
            assert analyzer is not None
        # Client should be closed after exit

    @pytest.mark.asyncio
    async def test_close_method(self) -> None:
        """Should close HTTP client cleanly."""
        analyzer = PDFAccessibilityAnalyzer()
        # Force client creation
        await analyzer._get_client()
        assert analyzer._client is not None

        # Close should work without error
        await analyzer.close()
        assert analyzer._client is None


class TestHintMetadata:
    """Tests for hint metadata accuracy."""

    def test_hints_have_page_numbers(self) -> None:
        """Hints should have page numbers extracted from context."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        # At least one hint should have a page number
        hints_with_pages = [h for h in hints if h.page_number is not None]
        assert len(hints_with_pages) >= 1

    def test_hints_have_context(self) -> None:
        """Hints should include context from VeraPDF."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        for hint in hints:
            # Context or location should be populated
            assert hint.context != "" or hint.location != ""

    def test_hints_have_suggestions(self) -> None:
        """Hints should include actionable suggestions."""
        analyzer = PDFAccessibilityAnalyzer()
        hints = analyzer._parse_verapdf_results(NON_COMPLIANT_RESPONSE)

        for hint in hints:
            assert hint.suggestion != ""
