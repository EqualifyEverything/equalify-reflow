"""Unit tests for MarkdownLinter.

Tests the PyMarkdown integration for detecting markdown structure issues
that impact accessibility.
"""


from src.analyzers.markdown_linter import (
    DEFAULT_ENABLED_RULES,
    PYMARKDOWN_CATEGORY_MAP,
    MarkdownLinter,
)
from src.shared.models.hints_models import (
    HintCategory,
    HintSeverity,
    HintSource,
)


class TestMarkdownLinterInitialization:
    """Tests for MarkdownLinter initialization."""

    def test_default_rules(self) -> None:
        """Default rules should be the accessibility-focused set."""
        linter = MarkdownLinter()
        assert linter.enabled_rules == DEFAULT_ENABLED_RULES
        assert "MD001" in linter.enabled_rules
        assert "MD025" in linter.enabled_rules
        assert "MD036" in linter.enabled_rules
        assert "MD045" in linter.enabled_rules

    def test_custom_rules(self) -> None:
        """Should accept custom rule list."""
        custom_rules = ["MD001", "MD002"]
        linter = MarkdownLinter(enabled_rules=custom_rules)
        assert linter.enabled_rules == custom_rules


class TestMD001HeadingLevelSkip:
    """Tests for MD001 - Heading levels should increment by one."""

    def test_detects_heading_level_skip(self) -> None:
        """Should detect when heading levels skip (H1 -> H3)."""
        markdown = "# Title\n\n### Skipped H2\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        assert len(hints) >= 1
        md001_hints = [h for h in hints if h.rule_id == "MD001"]
        assert len(md001_hints) >= 1

        hint = md001_hints[0]
        assert hint.source == HintSource.PYMARKDOWN
        assert hint.category == HintCategory.STRUCTURE
        assert hint.severity == HintSeverity.ERROR
        assert hint.page_number == 1
        assert "increment" in hint.message.lower() or "level" in hint.message.lower()

    def test_valid_heading_hierarchy(self) -> None:
        """Should not flag valid heading hierarchy."""
        markdown = "# Title\n\n## Section\n\n### Subsection\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        md001_hints = [h for h in hints if h.rule_id == "MD001"]
        assert len(md001_hints) == 0


class TestMD025MultipleH1:
    """Tests for MD025 - Multiple top-level headings."""

    def test_detects_multiple_h1(self) -> None:
        """Should detect when document has multiple H1 headings."""
        markdown = "# First Title\n\nSome content.\n\n# Second Title\n"

        linter = MarkdownLinter(enabled_rules=["MD025"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        md025_hints = [h for h in hints if h.rule_id == "MD025"]
        assert len(md025_hints) >= 1

        hint = md025_hints[0]
        assert hint.source == HintSource.PYMARKDOWN
        assert hint.category == HintCategory.STRUCTURE
        assert hint.severity == HintSeverity.ERROR

    def test_single_h1_valid(self) -> None:
        """Should not flag document with single H1."""
        markdown = "# Only Title\n\n## Section\n\n## Another Section\n"

        linter = MarkdownLinter(enabled_rules=["MD025"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        md025_hints = [h for h in hints if h.rule_id == "MD025"]
        assert len(md025_hints) == 0


class TestMD036EmphasisAsHeading:
    """Tests for MD036 - Emphasis used instead of heading."""

    def test_detects_emphasis_as_heading(self) -> None:
        """Should detect bold text used as a pseudo-heading."""
        markdown = "**This looks like a heading**\n\nSome content below.\n"

        linter = MarkdownLinter(enabled_rules=["MD036"])
        hints = linter.analyze_markdown(markdown, page_number=2)

        md036_hints = [h for h in hints if h.rule_id == "MD036"]
        assert len(md036_hints) >= 1

        hint = md036_hints[0]
        assert hint.source == HintSource.PYMARKDOWN
        assert hint.category == HintCategory.TYPOGRAPHY
        assert hint.severity == HintSeverity.WARNING
        assert hint.page_number == 2

    def test_inline_emphasis_valid(self) -> None:
        """Should not flag inline emphasis within paragraphs."""
        markdown = "# Title\n\nThis is **bold text** within a paragraph.\n"

        linter = MarkdownLinter(enabled_rules=["MD036"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        md036_hints = [h for h in hints if h.rule_id == "MD036"]
        assert len(md036_hints) == 0


class TestMD045MissingAltText:
    """Tests for MD045 - Images should have alt text."""

    def test_detects_missing_alt_text(self) -> None:
        """Should detect image without alt text."""
        markdown = "# Document\n\n![](image.png)\n"

        linter = MarkdownLinter(enabled_rules=["MD045"])
        hints = linter.analyze_markdown(markdown, page_number=3)

        md045_hints = [h for h in hints if h.rule_id == "MD045"]
        assert len(md045_hints) >= 1

        hint = md045_hints[0]
        assert hint.source == HintSource.PYMARKDOWN
        assert hint.category == HintCategory.FIGURES
        assert hint.severity == HintSeverity.ERROR
        assert hint.page_number == 3

    def test_image_with_alt_text_valid(self) -> None:
        """Should not flag image with alt text."""
        markdown = "# Document\n\n![A descriptive alt text](image.png)\n"

        linter = MarkdownLinter(enabled_rules=["MD045"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        md045_hints = [h for h in hints if h.rule_id == "MD045"]
        assert len(md045_hints) == 0


class TestEmptyAndEdgeCases:
    """Tests for edge cases and empty inputs."""

    def test_empty_markdown(self) -> None:
        """Should handle empty markdown gracefully."""
        linter = MarkdownLinter()
        hints = linter.analyze_markdown("", page_number=1)
        assert hints == []

    def test_whitespace_only_markdown(self) -> None:
        """Should handle whitespace-only markdown."""
        linter = MarkdownLinter()
        hints = linter.analyze_markdown("   \n\n  \t  \n", page_number=1)
        assert hints == []

    def test_no_page_number(self) -> None:
        """Should handle missing page number."""
        markdown = "# Title\n\n### Skip\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=None)

        assert len(hints) >= 1
        assert hints[0].page_number is None


class TestHintMetadata:
    """Tests for hint metadata accuracy."""

    def test_line_number_included(self) -> None:
        """Should include accurate line numbers."""
        markdown = "# Title\n\nParagraph.\n\n### Skip\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        assert len(hints) >= 1
        # The ### Skip is on line 5
        assert hints[0].line_number is not None
        assert hints[0].line_number > 0

    def test_context_extracted(self) -> None:
        """Should extract context around the issue."""
        markdown = "# Title\n\n### Skip H2\n\nMore content.\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        assert len(hints) >= 1
        # Context should include nearby lines
        assert hints[0].context != ""

    def test_suggestion_provided(self) -> None:
        """Should provide actionable suggestion."""
        markdown = "# Title\n\n### Skip\n"

        linter = MarkdownLinter(enabled_rules=["MD001"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        assert len(hints) >= 1
        assert hints[0].suggestion != ""
        assert "H2" in hints[0].suggestion or "increment" in hints[0].suggestion.lower()


class TestCategoryMapping:
    """Tests for rule-to-category mapping."""

    def test_structure_rules_mapped(self) -> None:
        """Structure rules should map to STRUCTURE category."""
        structure_rules = ["MD001", "MD002", "MD003", "MD022", "MD024", "MD025"]
        for rule in structure_rules:
            if rule in PYMARKDOWN_CATEGORY_MAP:
                assert PYMARKDOWN_CATEGORY_MAP[rule] == HintCategory.STRUCTURE

    def test_typography_rules_mapped(self) -> None:
        """Typography rules should map to TYPOGRAPHY category."""
        typography_rules = ["MD036", "MD037", "MD038", "MD049", "MD050"]
        for rule in typography_rules:
            if rule in PYMARKDOWN_CATEGORY_MAP:
                assert PYMARKDOWN_CATEGORY_MAP[rule] == HintCategory.TYPOGRAPHY

    def test_figures_rules_mapped(self) -> None:
        """Figure rules should map to FIGURES category."""
        assert PYMARKDOWN_CATEGORY_MAP["MD045"] == HintCategory.FIGURES

    def test_tables_rules_mapped(self) -> None:
        """Table rules should map to TABLES category."""
        tables_rules = ["MD055", "MD056", "MD058"]
        for rule in tables_rules:
            if rule in PYMARKDOWN_CATEGORY_MAP:
                assert PYMARKDOWN_CATEGORY_MAP[rule] == HintCategory.TABLES


class TestMultipleIssues:
    """Tests for documents with multiple issues."""

    def test_multiple_violations_detected(self) -> None:
        """Should detect multiple violations in one document."""
        markdown = """# Title

### Skip H2

# Another H1

![](no-alt.png)
"""
        linter = MarkdownLinter(enabled_rules=["MD001", "MD025", "MD045"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        # Should find at least 3 issues
        rule_ids = {h.rule_id for h in hints}
        assert "MD001" in rule_ids or "MD025" in rule_ids  # At least one structure issue
        assert "MD045" in rule_ids  # Missing alt text

    def test_all_hints_have_required_fields(self) -> None:
        """All hints should have required fields populated."""
        markdown = "# Title\n\n### Skip\n\n# Another\n"

        linter = MarkdownLinter(enabled_rules=["MD001", "MD025"])
        hints = linter.analyze_markdown(markdown, page_number=1)

        for hint in hints:
            assert hint.source == HintSource.PYMARKDOWN
            assert hint.rule_id != ""
            assert hint.category is not None
            assert hint.severity is not None
            assert hint.message != ""
