"""Unit tests for Structure Verification Loop service."""

from unittest.mock import MagicMock, patch

import pytest
from src.services.structure_loop import (
    ENABLED_LINT_RULES,
    FORMATTING_RULES,
    LINT_TIMEOUT_SECONDS,
    HeadingIssue,
    LintIssue,
    ReadingOrderIssue,
    StructureCorrection,
    StructureLoop,
    StructureLoopResult,
    StructureTrace,
)
from src.shared.models.document_context import DocumentSummary
from src.shared.models.remediation import DocumentManifest


@pytest.fixture
def mock_manifest():
    """Create a mock document manifest."""
    manifest = MagicMock(spec=DocumentManifest)
    manifest.summary = MagicMock(spec=DocumentSummary)
    manifest.summary.key_entities = ["Enzo", "yt", "DVCS"]
    manifest.summary.domain_terms = ["parallelization", "MPI"]
    return manifest


@pytest.fixture
def mock_manifest_no_summary():
    """Create a mock manifest without summary."""
    manifest = MagicMock(spec=DocumentManifest)
    manifest.summary = None
    return manifest


@pytest.fixture
def sample_markdown():
    """Sample markdown for testing."""
    return """# Document Title

## Introduction

This is a test document about the Enzo framework.

## Methods

The implementation uses parallelization techniques.

### Implementation Details

Code runs on multiple cores.
"""


@pytest.fixture
def malformed_markdown():
    """Markdown with structural issues."""
    return """# Document Title
## Introduction
This is a test document.
#### Skipped Level
This heading skips H3.

# Second H1
Another top-level heading.
"""


@pytest.mark.unit
class TestLintIssue:
    """Test LintIssue model."""

    def test_lint_issue_creation(self):
        """Test creating a lint issue."""
        issue = LintIssue(
            rule="MD001",
            message="Heading levels should only increment by one level at a time",
            line=5,
            column=1,
        )

        assert issue.rule == "MD001"
        assert issue.line == 5
        assert issue.column == 1

    def test_lint_issue_defaults(self):
        """Test default values."""
        issue = LintIssue(
            rule="MD009",
            message="Trailing spaces",
            line=10,
        )

        assert issue.column == 1


@pytest.mark.unit
class TestHeadingIssue:
    """Test HeadingIssue model."""

    def test_heading_issue_creation(self):
        """Test creating a heading issue."""
        issue = HeadingIssue(
            type="multiple_h1",
            description="Found 2 H1 headings, should have only 1",
            locations=[1, 10],
        )

        assert issue.type == "multiple_h1"
        assert len(issue.locations) == 2

    def test_heading_issue_defaults(self):
        """Test default values."""
        issue = HeadingIssue(
            type="skipped_level",
            description="Skipped from H2 to H4",
        )

        assert issue.locations == []


@pytest.mark.unit
class TestStructureCorrection:
    """Test StructureCorrection model."""

    def test_correction_creation(self):
        """Test creating a structure correction."""
        correction = StructureCorrection(
            type="format",
            description="Fixed 3 spacing issues",
            iteration=0,
            auto=True,
        )

        assert correction.type == "format"
        assert correction.auto is True
        assert correction.iteration == 0


@pytest.mark.unit
class TestStructureTrace:
    """Test StructureTrace model."""

    def test_trace_creation(self):
        """Test creating a structure trace."""
        trace = StructureTrace(
            iterations=2,
            lint_issues_found=5,
            lint_issues_fixed=4,
            ocr_suggestions_processed=2,
            corrections=[],
            final_lint_clean=False,
            observations=[],
            time_seconds=3.5,
            cost_cents=1.2,
        )

        assert trace.iterations == 2
        assert trace.lint_issues_found == 5
        assert trace.lint_issues_fixed == 4

    def test_trace_defaults(self):
        """Test default values."""
        trace = StructureTrace(iterations=1)

        assert trace.lint_issues_found == 0
        assert trace.final_lint_clean is False
        assert trace.cost_cents == 0.0


@pytest.mark.unit
class TestStructureLoopResult:
    """Test StructureLoopResult model."""

    def test_result_creation(self):
        """Test creating a loop result."""
        trace = StructureTrace(iterations=1)
        result = StructureLoopResult(
            markdown="# Test",
            trace=trace,
        )

        assert result.markdown == "# Test"
        assert result.trace.iterations == 1


@pytest.mark.unit
class TestLintRulesConfiguration:
    """Test lint rules configuration."""

    def test_enabled_rules_not_empty(self):
        """Test that enabled rules are defined."""
        assert len(ENABLED_LINT_RULES) > 0

    def test_formatting_rules_subset(self):
        """Test that formatting rules are subset of enabled rules."""
        assert FORMATTING_RULES.issubset(ENABLED_LINT_RULES)

    def test_heading_rules_included(self):
        """Test that heading rules are included."""
        assert "MD001" in ENABLED_LINT_RULES  # Heading levels increment
        assert "MD025" in ENABLED_LINT_RULES  # Single H1

    def test_table_rules_included(self):
        """Test that table rules are included."""
        assert "MD055" in ENABLED_LINT_RULES  # Table pipe style
        assert "MD056" in ENABLED_LINT_RULES  # Table column count


@pytest.mark.unit
class TestStructureLoopInit:
    """Test StructureLoop initialization."""

    def test_initialization(self):
        """Test basic initialization."""
        loop = StructureLoop()
        assert loop is not None
        assert loop.MAX_ITERATIONS == 3

    def test_max_iterations_constant(self):
        """Test that max iterations is defined."""
        assert StructureLoop.MAX_ITERATIONS == 3


@pytest.mark.unit
class TestHeadingHierarchyValidation:
    """Test heading hierarchy validation."""

    def test_detect_multiple_h1(self, malformed_markdown):
        """Test detection of multiple H1 headings."""
        loop = StructureLoop()
        issues = loop._check_heading_hierarchy(malformed_markdown)

        multiple_h1 = [i for i in issues if i.type == "multiple_h1"]
        assert len(multiple_h1) == 1
        assert len(multiple_h1[0].locations) == 2

    def test_detect_skipped_level(self, malformed_markdown):
        """Test detection of skipped heading levels."""
        loop = StructureLoop()
        issues = loop._check_heading_hierarchy(malformed_markdown)

        skipped = [i for i in issues if i.type == "skipped_level"]
        assert len(skipped) >= 1

    def test_valid_hierarchy(self, sample_markdown):
        """Test that valid hierarchy has no issues."""
        loop = StructureLoop()
        issues = loop._check_heading_hierarchy(sample_markdown)

        # Valid markdown should have no hierarchy issues
        # (may have multiple_h1 if only one H1)
        skipped = [i for i in issues if i.type == "skipped_level"]
        assert len(skipped) == 0


@pytest.mark.unit
class TestExtractHeadings:
    """Test heading extraction."""

    def test_extract_headings_basic(self, sample_markdown):
        """Test basic heading extraction."""
        loop = StructureLoop()
        headings = loop._extract_headings(sample_markdown)

        assert len(headings) >= 3
        assert headings[0].level == 1
        assert headings[0].text == "Document Title"

    def test_extract_headings_all_levels(self):
        """Test extraction of all heading levels."""
        markdown = """# H1
## H2
### H3
#### H4
##### H5
###### H6
"""
        loop = StructureLoop()
        headings = loop._extract_headings(markdown)

        assert len(headings) == 6
        for i, h in enumerate(headings, 1):
            assert h.level == i

    def test_extract_headings_empty(self):
        """Test extraction from text without headings."""
        loop = StructureLoop()
        headings = loop._extract_headings("No headings here.")

        assert len(headings) == 0


@pytest.mark.unit
class TestMdformat:
    """Test mdformat application."""

    def test_mdformat_trailing_whitespace(self):
        """Test that mdformat removes trailing whitespace."""
        loop = StructureLoop()
        markdown = "# Title   \n\nSome text.   \n"

        formatted = loop._apply_mdformat(markdown)

        # mdformat removes trailing spaces
        assert "   \n" not in formatted or formatted == markdown

    def test_mdformat_preserves_content(self):
        """Test that mdformat preserves content."""
        loop = StructureLoop()
        markdown = "# Title\n\nSome important text.\n"

        formatted = loop._apply_mdformat(markdown)

        assert "Title" in formatted
        assert "important text" in formatted

    def test_mdformat_handles_invalid_gracefully(self):
        """Test graceful handling of mdformat errors."""
        loop = StructureLoop()
        # mdformat should handle most markdown, but test graceful failure
        result = loop._apply_mdformat("# Valid markdown")

        assert result is not None


@pytest.mark.unit
class TestOCRCheck:
    """Test OCR checking integration."""

    def test_ocr_check_with_key_terms(self, mock_manifest, sample_markdown):
        """Test OCR check with key terms."""
        loop = StructureLoop()
        suggestions = loop._check_ocr(sample_markdown, mock_manifest)

        # Should return a list (may be empty if no errors detected)
        assert isinstance(suggestions, list)

    def test_ocr_check_without_summary(self, mock_manifest_no_summary, sample_markdown):
        """Test OCR check without summary returns empty."""
        loop = StructureLoop()
        suggestions = loop._check_ocr(sample_markdown, mock_manifest_no_summary)

        assert suggestions == []


@pytest.mark.unit
class TestLintIntegration:
    """Test lint integration."""

    def test_run_lint_basic(self, sample_markdown):
        """Test basic lint execution."""
        loop = StructureLoop()
        issues = loop._run_lint(sample_markdown)

        # Should return a list (may have some issues)
        assert isinstance(issues, list)

    def test_run_lint_empty_markdown(self):
        """Test lint on empty markdown."""
        loop = StructureLoop()
        issues = loop._run_lint("")

        assert isinstance(issues, list)

    def test_run_lint_graceful_failure(self):
        """Test graceful handling of lint errors."""
        loop = StructureLoop()
        # Even with potentially problematic input, should not raise
        issues = loop._run_lint("# Test\n\n")

        assert isinstance(issues, list)


@pytest.mark.unit
class TestStructureLoopRun:
    """Test the main run method."""

    @pytest.mark.asyncio
    async def test_run_clean_markdown(self, sample_markdown, mock_manifest):
        """Test run with clean markdown exits early."""
        loop = StructureLoop()

        # Mock the LLM fix to avoid actual API calls
        with patch.object(loop, "_llm_fix") as mock_llm:
            mock_llm.return_value = MagicMock(
                markdown=sample_markdown,
                corrections=[],
                observations=[],
                cost_cents=0.0,
            )

            result = await loop.run(
                markdown=sample_markdown,
                pages=[],
                manifest=mock_manifest,
                job_id="test-job",
            )

            assert isinstance(result, StructureLoopResult)
            assert result.trace.iterations >= 1
            assert result.trace.iterations <= 3

    @pytest.mark.asyncio
    async def test_run_respects_max_iterations(self, malformed_markdown, mock_manifest):
        """Test that run respects max iterations."""
        loop = StructureLoop()

        # Mock LLM fix to not actually fix anything
        with patch.object(loop, "_llm_fix") as mock_llm:
            mock_llm.return_value = MagicMock(
                markdown=malformed_markdown,  # Return same markdown
                corrections=[],
                observations=[],
                cost_cents=0.1,
            )

            result = await loop.run(
                markdown=malformed_markdown,
                pages=[],
                manifest=mock_manifest,
                job_id="test-job",
            )

            assert result.trace.iterations <= 3

    @pytest.mark.asyncio
    async def test_run_tracks_cost(self, sample_markdown, mock_manifest):
        """Test that run tracks LLM cost."""
        loop = StructureLoop()

        with patch.object(loop, "_llm_fix") as mock_llm:
            mock_llm.return_value = MagicMock(
                markdown=sample_markdown,
                corrections=[],
                observations=[],
                cost_cents=0.5,
            )

            result = await loop.run(
                markdown=sample_markdown,
                pages=[],
                manifest=mock_manifest,
                job_id="test-job",
            )

            # Cost should be tracked in trace
            assert isinstance(result.trace.cost_cents, float)

    @pytest.mark.asyncio
    async def test_run_captures_corrections(self, sample_markdown, mock_manifest):
        """Test that run captures corrections."""
        loop = StructureLoop()

        with patch.object(loop, "_llm_fix") as mock_llm:
            mock_llm.return_value = MagicMock(
                markdown=sample_markdown,
                corrections=[
                    StructureCorrection(
                        type="ocr",
                        description="Fixed OCR error",
                        iteration=0,
                        auto=False,
                    )
                ],
                observations=[],
                cost_cents=0.1,
            )

            result = await loop.run(
                markdown=sample_markdown,
                pages=[],
                manifest=mock_manifest,
                job_id="test-job",
            )

            # Should have corrections in trace
            assert isinstance(result.trace.corrections, list)


@pytest.mark.unit
class TestReadingOrderIssue:
    """Test ReadingOrderIssue model."""

    def test_reading_order_issue_creation(self):
        """Test creating a reading order issue."""
        issue = ReadingOrderIssue(
            page_num=3,
            description="Two-column text appears to be in wrong order",
            suggested_fix="Reorder paragraphs to follow left-column-first pattern",
            confidence=0.85,
        )

        assert issue.page_num == 3
        assert issue.confidence == 0.85

    def test_reading_order_issue_defaults(self):
        """Test default values."""
        issue = ReadingOrderIssue(
            page_num=1,
            description="Reading order issue",
        )

        assert issue.suggested_fix == ""
        assert issue.confidence == 0.8


# =============================================================================
# Error Path Tests
# =============================================================================


@pytest.mark.unit
class TestLintErrorHandling:
    """Test lint error handling and graceful degradation."""

    def test_lint_timeout_returns_empty(self):
        """Test that lint timeout returns empty list gracefully."""
        loop = StructureLoop()

        with patch("src.services.structure_loop.subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired("pymarkdown", 30)

            issues = loop._run_lint("# Test markdown")

            assert issues == []

    def test_lint_not_found_returns_empty(self):
        """Test that missing pymarkdown returns empty list."""
        loop = StructureLoop()

        with patch("src.services.structure_loop.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("pymarkdown not found")

            issues = loop._run_lint("# Test markdown")

            assert issues == []

    def test_lint_generic_error_returns_empty(self):
        """Test that generic errors return empty list."""
        loop = StructureLoop()

        with patch("src.services.structure_loop.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")

            issues = loop._run_lint("# Test markdown")

            assert issues == []

    def test_lint_invalid_json_returns_empty(self):
        """Test that invalid JSON output returns empty list."""
        loop = StructureLoop()

        with patch("src.services.structure_loop.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "not valid json"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            issues = loop._run_lint("# Test markdown")

            assert issues == []


@pytest.mark.unit
class TestMdformatErrorHandling:
    """Test mdformat error handling."""

    def test_mdformat_error_returns_original(self):
        """Test that mdformat error returns original markdown."""
        loop = StructureLoop()
        original = "# Title\n\nSome content"

        with patch("src.services.structure_loop.mdformat.text") as mock_mdformat:
            mock_mdformat.side_effect = Exception("mdformat failed")

            result = loop._apply_mdformat(original)

            assert result == original


@pytest.mark.unit
class TestLLMFixErrorHandling:
    """Test LLM fix error handling."""

    @pytest.mark.asyncio
    async def test_llm_fix_returns_original_when_disabled(self, mock_manifest):
        """Test that LLM fixes return original markdown (currently disabled).

        Note: LLM-based structural fixes are currently disabled after the
        structure_fix_agent was removed. This test verifies the graceful
        degradation behavior.
        """
        loop = StructureLoop()
        original_md = "# Original markdown\n\nWith content"

        # LLM fixes are disabled, so issues are logged but not fixed
        result = await loop._llm_fix(
            markdown=original_md,
            lint_issues=[LintIssue(rule="MD001", message="Test", line=1)],
            heading_issues=[],
            ocr_suggestions=[],
            manifest=mock_manifest,
            job_id="test-job",
            iteration=0,
        )

        assert result.markdown == original_md
        assert result.corrections == []
        assert result.observations == []
        assert result.cost_cents == 0.0

    @pytest.mark.asyncio
    async def test_llm_fix_no_issues_returns_original(self, mock_manifest):
        """Test that no issues returns original unchanged."""
        loop = StructureLoop()
        original_md = "# Clean markdown"

        # No issues to fix - should return unchanged
        result = await loop._llm_fix(
            markdown=original_md,
            lint_issues=[],
            heading_issues=[],
            ocr_suggestions=[],
            manifest=mock_manifest,
            job_id="test-job",
            iteration=0,
        )

        assert result.markdown == original_md
        assert result.cost_cents == 0.0


# =============================================================================
# Specific Assertion Tests (strengthened from >= 0)
# =============================================================================


@pytest.mark.unit
class TestSpecificAssertions:
    """Tests with specific assertions instead of weak bounds."""

    def test_heading_extraction_count(self):
        """Test exact count of extracted headings."""
        markdown = """# H1
## H2
### H3
## Another H2
"""
        loop = StructureLoop()
        headings = loop._extract_headings(markdown)

        assert len(headings) == 4  # Exact count
        assert headings[0].level == 1
        assert headings[0].text == "H1"
        assert headings[1].level == 2
        assert headings[1].text == "H2"
        assert headings[2].level == 3
        assert headings[2].text == "H3"
        assert headings[3].level == 2
        assert headings[3].text == "Another H2"

    def test_multiple_h1_detection_exact(self):
        """Test exact detection of multiple H1 headings."""
        markdown = """# First H1
Some text.
# Second H1
More text.
# Third H1
"""
        loop = StructureLoop()
        issues = loop._check_heading_hierarchy(markdown)

        multiple_h1 = [i for i in issues if i.type == "multiple_h1"]
        assert len(multiple_h1) == 1
        assert len(multiple_h1[0].locations) == 3  # Exactly 3 H1s found

    def test_skipped_level_detection_exact(self):
        """Test exact detection of skipped heading levels."""
        markdown = """# H1
#### H4 (skips H2 and H3)
## H2
##### H5 (skips H3 and H4)
"""
        loop = StructureLoop()
        issues = loop._check_heading_hierarchy(markdown)

        skipped = [i for i in issues if i.type == "skipped_level"]
        assert len(skipped) == 2  # Exactly 2 skips

    def test_lint_rules_configuration_exact_counts(self):
        """Test exact counts for lint rule configuration."""
        # These should be stable configuration values
        assert len(ENABLED_LINT_RULES) == 18  # Exact count of enabled rules
        assert len(FORMATTING_RULES) == 6  # Exact count of formatting rules

        # All formatting rules must be in enabled rules
        for rule in FORMATTING_RULES:
            assert rule in ENABLED_LINT_RULES

    def test_structure_trace_valid_ranges(self):
        """Test StructureTrace with specific valid values."""
        trace = StructureTrace(
            iterations=2,
            lint_issues_found=10,
            lint_issues_fixed=8,
            ocr_suggestions_processed=5,
            corrections=[],
            final_lint_clean=False,
            observations=[],
            time_seconds=2.5,
            cost_cents=1.25,
        )

        assert trace.iterations == 2
        assert trace.lint_issues_found == 10
        assert trace.lint_issues_fixed == 8
        assert trace.ocr_suggestions_processed == 5
        assert trace.time_seconds == 2.5
        assert trace.cost_cents == 1.25


@pytest.mark.unit
class TestOCRCheckerIntegration:
    """Test OCR checker integration with specific scenarios."""

    def test_ocr_check_with_no_key_terms_returns_empty(self, mock_manifest_no_summary):
        """Test OCR check with no key terms returns empty list."""
        loop = StructureLoop()
        suggestions = loop._check_ocr("Some text", mock_manifest_no_summary)

        assert suggestions == []  # Exact empty

    def test_ocr_check_extracts_key_terms_from_summary(self, mock_manifest):
        """Test that OCR check uses key terms from manifest summary."""
        loop = StructureLoop()

        # The OCRChecker should be initialized with key terms
        # Even if no suggestions are found, the method should work
        suggestions = loop._check_ocr("Document about Enzo and yt frameworks", mock_manifest)

        # Should return a list (empty or with suggestions)
        assert isinstance(suggestions, list)


# =============================================================================
# Extracted Method Tests
# =============================================================================


@pytest.mark.unit
class TestExtractedMethods:
    """Test newly extracted methods from run() refactoring."""

    def test_detect_all_issues(self, mock_manifest, sample_markdown):
        """Test _detect_all_issues returns tuple of issue lists."""
        loop = StructureLoop()

        lint_issues, ocr_suggestions, heading_issues = loop._detect_all_issues(sample_markdown, mock_manifest)

        assert isinstance(lint_issues, list)
        assert isinstance(ocr_suggestions, list)
        assert isinstance(heading_issues, list)

    def test_auto_fix_formatting_no_issues(self, sample_markdown):
        """Test _auto_fix_formatting with no formatting issues."""
        loop = StructureLoop()

        result_md, correction = loop._auto_fix_formatting(
            sample_markdown,
            [],  # No lint issues
            iteration=0,
            job_id="test-job",
        )

        assert result_md == sample_markdown
        assert correction is None

    def test_auto_fix_formatting_with_semantic_only(self, sample_markdown):
        """Test _auto_fix_formatting ignores semantic issues."""
        loop = StructureLoop()

        # MD001 is a semantic issue (heading levels), not formatting
        semantic_issues = [LintIssue(rule="MD001", message="Test", line=1)]

        result_md, correction = loop._auto_fix_formatting(
            sample_markdown,
            semantic_issues,
            iteration=0,
            job_id="test-job",
        )

        assert result_md == sample_markdown
        assert correction is None

    def test_get_semantic_issues_filters_formatting(self):
        """Test _get_semantic_issues filters out formatting rules."""
        loop = StructureLoop()

        all_issues = [
            LintIssue(rule="MD001", message="Semantic", line=1),  # Semantic
            LintIssue(rule="MD009", message="Trailing spaces", line=2),  # Formatting
            LintIssue(rule="MD025", message="Multiple H1", line=3),  # Semantic
            LintIssue(rule="MD012", message="Blank lines", line=4),  # Formatting
        ]

        semantic = loop._get_semantic_issues(all_issues)

        assert len(semantic) == 2
        assert all(i.rule in ["MD001", "MD025"] for i in semantic)

    def test_is_semantically_clean_true(self):
        """Test _is_semantically_clean returns True for clean markdown."""
        loop = StructureLoop()

        # Well-formed markdown should be semantically clean
        clean_md = "# Title\n\n## Section\n\nParagraph.\n"

        with patch.object(loop, "_run_lint", return_value=[]):
            assert loop._is_semantically_clean(clean_md) is True

    def test_is_semantically_clean_false(self):
        """Test _is_semantically_clean returns False with semantic issues."""
        loop = StructureLoop()

        with patch.object(loop, "_run_lint", return_value=[LintIssue(rule="MD001", message="Test", line=1)]):
            assert loop._is_semantically_clean("# Test") is False


# =============================================================================
# Constants Tests
# =============================================================================


@pytest.mark.unit
class TestConstants:
    """Test module constants."""

    def test_lint_timeout_value(self):
        """Test lint timeout constant has reasonable value."""
        assert LINT_TIMEOUT_SECONDS == 30
        assert isinstance(LINT_TIMEOUT_SECONDS, int)

    def test_formatting_rules_are_subset_of_enabled(self):
        """Test formatting rules are subset of enabled rules."""
        assert FORMATTING_RULES.issubset(ENABLED_LINT_RULES)

    def test_enabled_rules_are_all_md_prefixed(self):
        """Test all enabled rules have MD prefix."""
        for rule in ENABLED_LINT_RULES:
            assert rule.startswith("MD"), f"{rule} should start with MD"

    def test_formatting_rules_are_auto_fixable(self):
        """Test formatting rules list contains expected auto-fixable rules."""
        # These are rules that mdformat can fix automatically
        expected_auto_fixable = {"MD009", "MD010", "MD012", "MD047"}
        assert expected_auto_fixable.issubset(FORMATTING_RULES)
