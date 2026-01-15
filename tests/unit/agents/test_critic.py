"""Unit tests for Critic Agent Module.

Tests for the CriticAgent's data structures, tool result types, and helper logic:
- CriticDeps dataclass: initialization, defaults, mutation
- Tool Result Models: ViewSectionResult, FindPatternResult, ReportIssueResult, MarkReadyResult
- IssueSeverity enum and CriticIssue model integration
- Pydantic serialization/deserialization

NOTE: This file tests data structures and pure logic only.
Integration tests for the actual agent with LLM calls are in tests/integration/.
"""

import pytest
from pydantic import ValidationError

from src.agents.critic import (
    CriticDeps,
    FindPatternResult,
    MarkReadyResult,
    ReportIssueResult,
    ViewSectionResult,
)
from src.agents.models import (
    CriticIssue,
    IssueSeverity,
    PageBoundary,
    PageBoundaryMap,
)

# Mark all tests in this module as unit tests
pytestmark = pytest.mark.unit


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_page_boundary_map() -> PageBoundaryMap:
    """Create a sample PageBoundaryMap for testing."""
    return PageBoundaryMap(
        document_id="test-doc-123",
        boundaries=[
            PageBoundary(page_num=1, start_line=1, end_line=25),
            PageBoundary(page_num=2, start_line=26, end_line=50),
            PageBoundary(page_num=3, start_line=51, end_line=75),
        ],
        total_lines=75,
    )


@pytest.fixture
def sample_markdown() -> str:
    """Create sample markdown content for testing."""
    return """# Test Document

## Introduction

This is the introduction section with some content.

![A diagram showing the architecture](image1.png)

## Methods

The methods section describes our approach.

| Column A | Column B |
|----------|----------|
| Data 1   | Data 2   |

## Results

Results are presented below.

![](missing-alt.png)

## Conclusion

Final thoughts here.
"""


@pytest.fixture
def empty_page_boundary_map() -> PageBoundaryMap:
    """Create an empty PageBoundaryMap for testing."""
    return PageBoundaryMap(
        document_id="empty-doc",
        boundaries=[],
        total_lines=0,
    )


# =============================================================================
# CriticDeps Tests
# =============================================================================


class TestCriticDeps:
    """Tests for CriticDeps dataclass."""

    def test_initialization_required_fields(
        self, sample_page_boundary_map: PageBoundaryMap, sample_markdown: str
    ):
        """Test CriticDeps initialization with required fields only."""
        deps = CriticDeps(
            document_id="doc-123",
            merged_markdown=sample_markdown,
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        assert deps.document_id == "doc-123"
        assert deps.merged_markdown == sample_markdown
        assert deps.page_boundary_map == sample_page_boundary_map
        assert deps.page_images == {}
        assert deps.round_number == 1

    def test_default_values_issues_list(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that issues defaults to an empty list."""
        deps = CriticDeps(
            document_id="doc-456",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=2,
        )

        assert deps.issues == []
        assert isinstance(deps.issues, list)

    def test_default_values_marked_ready(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that marked_ready defaults to False."""
        deps = CriticDeps(
            document_id="doc-789",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=3,
        )

        assert deps.marked_ready is False

    def test_default_values_event_bus(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that event_bus defaults to None."""
        deps = CriticDeps(
            document_id="doc-abc",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        assert deps.event_bus is None

    def test_modifying_issues_list(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that issues list can be modified after creation."""
        deps = CriticDeps(
            document_id="doc-modify",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        # Create an issue
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Missing alt text",
            line_start=10,
            line_end=10,
        )

        # Add to issues
        deps.issues.append(issue)
        assert len(deps.issues) == 1
        assert deps.issues[0].severity == IssueSeverity.CRITICAL

        # Add more issues
        issue2 = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Extra whitespace",
            line_start=20,
            line_end=22,
        )
        deps.issues.append(issue2)
        assert len(deps.issues) == 2

    def test_setting_marked_ready_flag(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that marked_ready can be toggled."""
        deps = CriticDeps(
            document_id="doc-ready",
            merged_markdown="# Clean document",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=2,
        )

        assert deps.marked_ready is False

        deps.marked_ready = True
        assert deps.marked_ready is True

        # Can be toggled back
        deps.marked_ready = False
        assert deps.marked_ready is False

    def test_mutable_default_factory_isolation(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that issues list default factory creates separate instances."""
        deps1 = CriticDeps(
            document_id="doc-1",
            merged_markdown="# Test 1",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )
        deps2 = CriticDeps(
            document_id="doc-2",
            merged_markdown="# Test 2",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        # Add issue to deps1
        deps1.issues.append(
            CriticIssue(
                severity=IssueSeverity.MAJOR,
                category="content",
                description="Test issue",
                line_start=1,
                line_end=1,
            )
        )

        # deps2 should be unaffected
        assert len(deps1.issues) == 1
        assert len(deps2.issues) == 0

    def test_with_page_images(self, sample_page_boundary_map: PageBoundaryMap):
        """Test CriticDeps with page_images dictionary."""
        # Mock PIL Image-like object (just needs to be dict-storable)
        mock_image = object()

        deps = CriticDeps(
            document_id="doc-images",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={1: mock_image, 2: mock_image},
            round_number=1,
        )

        assert len(deps.page_images) == 2
        assert 1 in deps.page_images
        assert 2 in deps.page_images

    def test_multiple_round_numbers(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test CriticDeps with different round numbers."""
        for round_num in [1, 2, 3, 5, 10]:
            deps = CriticDeps(
                document_id=f"doc-round-{round_num}",
                merged_markdown="# Test",
                page_boundary_map=sample_page_boundary_map,
                page_images={},
                round_number=round_num,
            )
            assert deps.round_number == round_num


# =============================================================================
# ViewSectionResult Tests
# =============================================================================


class TestViewSectionResult:
    """Tests for ViewSectionResult Pydantic model."""

    def test_basic_creation(self):
        """Test creating ViewSectionResult with all fields."""
        result = ViewSectionResult(
            content="# Heading\n\nParagraph content here.",
            start_line=1,
            end_line=3,
            total_lines=100,
        )

        assert result.content == "# Heading\n\nParagraph content here."
        assert result.start_line == 1
        assert result.end_line == 3
        assert result.total_lines == 100

    def test_empty_content(self):
        """Test ViewSectionResult with empty content."""
        result = ViewSectionResult(
            content="",
            start_line=1,
            end_line=1,
            total_lines=50,
        )

        assert result.content == ""

    def test_large_line_numbers(self):
        """Test ViewSectionResult with large line numbers."""
        result = ViewSectionResult(
            content="Some content at end of document",
            start_line=9900,
            end_line=10000,
            total_lines=10000,
        )

        assert result.start_line == 9900
        assert result.end_line == 10000
        assert result.total_lines == 10000

    def test_serialization_to_dict(self):
        """Test ViewSectionResult can be serialized to dict."""
        result = ViewSectionResult(
            content="Test content",
            start_line=10,
            end_line=20,
            total_lines=200,
        )

        data = result.model_dump()
        assert data == {
            "content": "Test content",
            "start_line": 10,
            "end_line": 20,
            "total_lines": 200,
        }

    def test_serialization_to_json(self):
        """Test ViewSectionResult can be serialized to JSON."""
        result = ViewSectionResult(
            content="Test content",
            start_line=10,
            end_line=20,
            total_lines=200,
        )

        json_str = result.model_dump_json()
        assert '"content":"Test content"' in json_str
        assert '"start_line":10' in json_str

    def test_deserialization_from_dict(self):
        """Test ViewSectionResult can be deserialized from dict."""
        data = {
            "content": "Deserialized content",
            "start_line": 5,
            "end_line": 15,
            "total_lines": 50,
        }

        result = ViewSectionResult.model_validate(data)
        assert result.content == "Deserialized content"
        assert result.start_line == 5
        assert result.end_line == 15
        assert result.total_lines == 50

    def test_multiline_content(self):
        """Test ViewSectionResult with multiline content."""
        content = """1: # Main Heading
2:
3: This is a paragraph with multiple lines.
4: It continues here.
5:
6: ## Subheading
7:
8: More content."""

        result = ViewSectionResult(
            content=content,
            start_line=1,
            end_line=8,
            total_lines=100,
        )

        assert "# Main Heading" in result.content
        assert "## Subheading" in result.content


# =============================================================================
# FindPatternResult Tests
# =============================================================================


class TestFindPatternResult:
    """Tests for FindPatternResult Pydantic model."""

    def test_pattern_found(self):
        """Test FindPatternResult when pattern is found."""
        result = FindPatternResult(
            found=True,
            matches=[
                {"line": 10, "text": "![](missing-alt.png)"},
                {"line": 25, "text": "![](another-missing.png)"},
            ],
            total_matches=2,
        )

        assert result.found is True
        assert len(result.matches) == 2
        assert result.total_matches == 2
        assert result.matches[0]["line"] == 10

    def test_pattern_not_found(self):
        """Test FindPatternResult when pattern is not found."""
        result = FindPatternResult(
            found=False,
            matches=[],
            total_matches=0,
        )

        assert result.found is False
        assert result.matches == []
        assert result.total_matches == 0

    def test_default_values_matches(self):
        """Test FindPatternResult default value for matches."""
        result = FindPatternResult(
            found=False,
        )

        assert result.matches == []
        assert result.total_matches == 0

    def test_many_matches(self):
        """Test FindPatternResult with many matches."""
        matches = [{"line": i, "text": f"Match {i}"} for i in range(50)]

        result = FindPatternResult(
            found=True,
            matches=matches,
            total_matches=50,
        )

        assert len(result.matches) == 50
        assert result.total_matches == 50

    def test_total_matches_exceeds_returned(self):
        """Test FindPatternResult where total exceeds returned (truncated)."""
        # Simulates when matches are truncated to first 50
        result = FindPatternResult(
            found=True,
            matches=[{"line": 1, "text": "First match"}],
            total_matches=100,  # More exist than returned
        )

        assert len(result.matches) == 1
        assert result.total_matches == 100

    def test_serialization_to_dict(self):
        """Test FindPatternResult can be serialized to dict."""
        result = FindPatternResult(
            found=True,
            matches=[{"line": 5, "text": "Test line"}],
            total_matches=1,
        )

        data = result.model_dump()
        assert data["found"] is True
        assert data["matches"] == [{"line": 5, "text": "Test line"}]
        assert data["total_matches"] == 1

    def test_deserialization_from_dict(self):
        """Test FindPatternResult can be deserialized from dict."""
        data = {
            "found": True,
            "matches": [{"line": 42, "text": "Pattern match"}],
            "total_matches": 1,
        }

        result = FindPatternResult.model_validate(data)
        assert result.found is True
        assert result.matches[0]["line"] == 42

    def test_match_with_truncated_text(self):
        """Test FindPatternResult match with long text truncated."""
        long_text = "x" * 200  # Tool truncates to 200 chars
        result = FindPatternResult(
            found=True,
            matches=[{"line": 1, "text": long_text}],
            total_matches=1,
        )

        assert len(result.matches[0]["text"]) == 200


# =============================================================================
# ReportIssueResult Tests
# =============================================================================


class TestReportIssueResult:
    """Tests for ReportIssueResult Pydantic model."""

    def test_successful_report(self):
        """Test ReportIssueResult for successful issue report."""
        result = ReportIssueResult(
            success=True,
            issue_id="abc12345",
            message="Issue recorded: critical accessibility at lines 10-12",
        )

        assert result.success is True
        assert result.issue_id == "abc12345"
        assert "critical accessibility" in result.message

    def test_failed_report_invalid_severity(self):
        """Test ReportIssueResult for failed report due to invalid severity."""
        result = ReportIssueResult(
            success=False,
            message="Invalid severity 'extreme'. Use: critical, major, minor, cosmetic",
        )

        assert result.success is False
        assert "Invalid severity" in result.message
        assert result.issue_id == ""

    def test_failed_report_invalid_category(self):
        """Test ReportIssueResult for failed report due to invalid category."""
        result = ReportIssueResult(
            success=False,
            message="Invalid category 'unknown'. Use: structure, accessibility, content, formatting",
        )

        assert result.success is False
        assert "Invalid category" in result.message

    def test_default_values(self):
        """Test ReportIssueResult default values."""
        result = ReportIssueResult(success=True)

        assert result.success is True
        assert result.issue_id == ""
        assert result.message == ""

    def test_serialization_round_trip(self):
        """Test ReportIssueResult serialization round trip."""
        original = ReportIssueResult(
            success=True,
            issue_id="xyz789",
            message="Issue recorded successfully",
        )

        data = original.model_dump()
        restored = ReportIssueResult.model_validate(data)

        assert restored.success == original.success
        assert restored.issue_id == original.issue_id
        assert restored.message == original.message

    def test_json_serialization(self):
        """Test ReportIssueResult JSON serialization."""
        result = ReportIssueResult(
            success=False,
            issue_id="",
            message="Validation failed",
        )

        json_str = result.model_dump_json()
        assert '"success":false' in json_str
        assert '"message":"Validation failed"' in json_str


# =============================================================================
# MarkReadyResult Tests
# =============================================================================


class TestMarkReadyResult:
    """Tests for MarkReadyResult Pydantic model."""

    def test_successful_mark_ready(self):
        """Test MarkReadyResult for successful marking."""
        result = MarkReadyResult(
            success=True,
            message="Document marked ready for output. 3 minor/cosmetic issues noted.",
        )

        assert result.success is True
        assert "marked ready" in result.message

    def test_failed_critical_issues(self):
        """Test MarkReadyResult when blocked by critical issues."""
        result = MarkReadyResult(
            success=False,
            message="Cannot mark ready: 2 critical issue(s) found. Fix them first.",
        )

        assert result.success is False
        assert "critical issue" in result.message

    def test_failed_many_major_issues(self):
        """Test MarkReadyResult when blocked by too many major issues."""
        result = MarkReadyResult(
            success=False,
            message="Cannot mark ready: 5 major issues found. Consider fixing them first.",
        )

        assert result.success is False
        assert "major issues" in result.message

    def test_default_message(self):
        """Test MarkReadyResult default message value."""
        result = MarkReadyResult(success=True)

        assert result.success is True
        assert result.message == ""

    def test_serialization_to_dict(self):
        """Test MarkReadyResult serialization to dict."""
        result = MarkReadyResult(
            success=True,
            message="Ready for output",
        )

        data = result.model_dump()
        assert data == {"success": True, "message": "Ready for output"}

    def test_deserialization_from_dict(self):
        """Test MarkReadyResult deserialization from dict."""
        data = {
            "success": False,
            "message": "Cannot proceed due to issues",
        }

        result = MarkReadyResult.model_validate(data)
        assert result.success is False
        assert result.message == "Cannot proceed due to issues"


# =============================================================================
# IssueSeverity Enum Tests
# =============================================================================


class TestIssueSeverity:
    """Tests for IssueSeverity enum usage with CriticIssue."""

    def test_severity_values(self):
        """Test all IssueSeverity enum values."""
        assert IssueSeverity.CRITICAL.value == "critical"
        assert IssueSeverity.MAJOR.value == "major"
        assert IssueSeverity.MINOR.value == "minor"
        assert IssueSeverity.COSMETIC.value == "cosmetic"

    def test_severity_from_string(self):
        """Test creating IssueSeverity from string."""
        assert IssueSeverity("critical") == IssueSeverity.CRITICAL
        assert IssueSeverity("major") == IssueSeverity.MAJOR
        assert IssueSeverity("minor") == IssueSeverity.MINOR
        assert IssueSeverity("cosmetic") == IssueSeverity.COSMETIC

    def test_invalid_severity_string(self):
        """Test that invalid severity string raises ValueError."""
        with pytest.raises(ValueError):
            IssueSeverity("extreme")

        with pytest.raises(ValueError):
            IssueSeverity("warning")

    def test_severity_comparison(self):
        """Test that severity enum members can be compared."""
        assert IssueSeverity.CRITICAL == IssueSeverity.CRITICAL
        assert IssueSeverity.CRITICAL != IssueSeverity.MINOR

    def test_severity_in_list_operations(self):
        """Test severity enum in list filtering."""
        severities = [
            IssueSeverity.CRITICAL,
            IssueSeverity.MAJOR,
            IssueSeverity.MINOR,
            IssueSeverity.CRITICAL,
            IssueSeverity.COSMETIC,
        ]

        critical_count = sum(1 for s in severities if s == IssueSeverity.CRITICAL)
        assert critical_count == 2


# =============================================================================
# CriticIssue Model Tests
# =============================================================================


class TestCriticIssue:
    """Tests for CriticIssue Pydantic model."""

    def test_basic_creation(self):
        """Test creating CriticIssue with required fields."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Image missing alt text",
            line_start=15,
            line_end=15,
        )

        assert issue.severity == IssueSeverity.CRITICAL
        assert issue.category == "accessibility"
        assert issue.description == "Image missing alt text"
        assert issue.line_start == 15
        assert issue.line_end == 15

    def test_auto_generated_issue_id(self):
        """Test that issue_id is auto-generated."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="structure",
            description="Heading hierarchy issue",
            line_start=1,
            line_end=5,
        )

        assert issue.issue_id is not None
        assert len(issue.issue_id) == 8  # UUID[:8]

    def test_unique_issue_ids(self):
        """Test that different issues get unique IDs."""
        issue1 = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Issue 1",
            line_start=1,
            line_end=1,
        )
        issue2 = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="formatting",
            description="Issue 2",
            line_start=2,
            line_end=2,
        )

        assert issue1.issue_id != issue2.issue_id

    def test_default_values(self):
        """Test CriticIssue default values."""
        issue = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="content",
            description="Test issue",
            line_start=10,
            line_end=12,
        )

        assert issue.suggested_fix == ""
        assert issue.search_text == ""
        assert issue.confidence == 0.7  # Default
        assert issue.reasoning == ""
        assert issue.source_pages == []

    def test_with_all_optional_fields(self):
        """Test CriticIssue with all fields populated."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="accessibility",
            description="Table missing headers",
            suggested_fix="Add header row with | --- | separators",
            line_start=45,
            line_end=55,
            search_text="| Data 1 | Data 2 |",
            confidence=0.85,
            reasoning="Screen readers need table headers for navigation",
            source_pages=[2, 3],
        )

        assert issue.suggested_fix == "Add header row with | --- | separators"
        assert issue.search_text == "| Data 1 | Data 2 |"
        assert issue.confidence == 0.85
        assert issue.reasoning == "Screen readers need table headers for navigation"
        assert issue.source_pages == [2, 3]

    def test_issue_with_severity_string(self):
        """Test creating issue with severity as string (via Pydantic coercion)."""
        issue = CriticIssue(
            severity="critical",  # type: ignore - testing string coercion
            category="content",
            description="Unfilled placeholder",
            line_start=20,
            line_end=20,
        )

        assert issue.severity == IssueSeverity.CRITICAL

    def test_issue_categories(self):
        """Test CriticIssue with different categories."""
        categories = ["structure", "accessibility", "content", "formatting"]

        for category in categories:
            issue = CriticIssue(
                severity=IssueSeverity.MINOR,
                category=category,
                description=f"Issue in {category}",
                line_start=1,
                line_end=1,
            )
            assert issue.category == category

    def test_line_range_single_line(self):
        """Test CriticIssue with single line range."""
        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="Single line issue",
            line_start=42,
            line_end=42,
        )

        assert issue.line_start == issue.line_end

    def test_line_range_multi_line(self):
        """Test CriticIssue with multi-line range."""
        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="structure",
            description="Multi-line issue",
            line_start=10,
            line_end=25,
        )

        assert issue.line_end > issue.line_start

    def test_serialization_to_dict(self):
        """Test CriticIssue serialization to dict."""
        issue = CriticIssue(
            severity=IssueSeverity.COSMETIC,
            category="formatting",
            description="Extra whitespace",
            line_start=100,
            line_end=105,
        )

        data = issue.model_dump()
        assert data["severity"] == "cosmetic"
        assert data["category"] == "formatting"
        assert data["description"] == "Extra whitespace"
        assert data["line_start"] == 100

    def test_deserialization_from_dict(self):
        """Test CriticIssue deserialization from dict."""
        data = {
            "severity": "major",
            "category": "content",
            "description": "Placeholder not filled",
            "line_start": 50,
            "line_end": 52,
            "suggested_fix": "Fill in the placeholder",
            "source_pages": [3],
        }

        issue = CriticIssue.model_validate(data)
        assert issue.severity == IssueSeverity.MAJOR
        assert issue.description == "Placeholder not filled"
        assert issue.source_pages == [3]

    def test_confidence_bounds(self):
        """Test CriticIssue confidence within bounds."""
        # Min bound
        issue_min = CriticIssue(
            severity=IssueSeverity.MINOR,
            category="content",
            description="Low confidence",
            line_start=1,
            line_end=1,
            confidence=0.0,
        )
        assert issue_min.confidence == 0.0

        # Max bound
        issue_max = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="accessibility",
            description="High confidence",
            line_start=1,
            line_end=1,
            confidence=1.0,
        )
        assert issue_max.confidence == 1.0

    def test_confidence_validation_out_of_bounds(self):
        """Test CriticIssue rejects confidence outside valid range."""
        with pytest.raises(ValidationError):
            CriticIssue(
                severity=IssueSeverity.MINOR,
                category="content",
                description="Invalid confidence",
                line_start=1,
                line_end=1,
                confidence=1.5,  # Out of bounds
            )

        with pytest.raises(ValidationError):
            CriticIssue(
                severity=IssueSeverity.MINOR,
                category="content",
                description="Invalid confidence",
                line_start=1,
                line_end=1,
                confidence=-0.1,  # Out of bounds
            )


# =============================================================================
# Integration: CriticDeps with CriticIssue
# =============================================================================


class TestCriticDepsWithIssues:
    """Tests for CriticDeps integration with CriticIssue model."""

    def test_add_multiple_issues_different_severities(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test adding issues of different severities to CriticDeps."""
        deps = CriticDeps(
            document_id="doc-multi",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=2,
        )

        # Add issues of each severity
        deps.issues.append(
            CriticIssue(
                severity=IssueSeverity.CRITICAL,
                category="accessibility",
                description="Missing alt text",
                line_start=10,
                line_end=10,
            )
        )
        deps.issues.append(
            CriticIssue(
                severity=IssueSeverity.MAJOR,
                category="structure",
                description="Heading skip",
                line_start=5,
                line_end=5,
            )
        )
        deps.issues.append(
            CriticIssue(
                severity=IssueSeverity.MINOR,
                category="formatting",
                description="Inconsistent spacing",
                line_start=20,
                line_end=22,
            )
        )
        deps.issues.append(
            CriticIssue(
                severity=IssueSeverity.COSMETIC,
                category="formatting",
                description="Extra blank line",
                line_start=30,
                line_end=30,
            )
        )

        assert len(deps.issues) == 4

        # Count by severity
        critical = sum(1 for i in deps.issues if i.severity == IssueSeverity.CRITICAL)
        major = sum(1 for i in deps.issues if i.severity == IssueSeverity.MAJOR)
        minor = sum(1 for i in deps.issues if i.severity == IssueSeverity.MINOR)
        cosmetic = sum(1 for i in deps.issues if i.severity == IssueSeverity.COSMETIC)

        assert critical == 1
        assert major == 1
        assert minor == 1
        assert cosmetic == 1

    def test_issue_source_pages_from_boundary_map(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test that issues can have source_pages derived from boundary map."""
        deps = CriticDeps(
            document_id="doc-pages",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        # Lines 26-50 are page 2 based on sample_page_boundary_map
        source_pages = deps.page_boundary_map.get_pages_for_range(30, 45)

        issue = CriticIssue(
            severity=IssueSeverity.MAJOR,
            category="content",
            description="Issue spanning page 2",
            line_start=30,
            line_end=45,
            source_pages=source_pages,
        )

        deps.issues.append(issue)

        assert deps.issues[0].source_pages == [2]

    def test_issue_spanning_multiple_pages(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test issue that spans multiple pages."""
        # Lines 20-60 span pages 1, 2, and 3
        source_pages = sample_page_boundary_map.get_pages_for_range(20, 60)

        issue = CriticIssue(
            severity=IssueSeverity.CRITICAL,
            category="structure",
            description="Issue spanning multiple pages",
            line_start=20,
            line_end=60,
            source_pages=source_pages,
        )

        assert issue.source_pages == [1, 2, 3]

    def test_filtering_issues_by_category(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test filtering CriticDeps issues by category."""
        deps = CriticDeps(
            document_id="doc-filter",
            merged_markdown="# Test",
            page_boundary_map=sample_page_boundary_map,
            page_images={},
            round_number=1,
        )

        # Add issues of different categories
        for category in ["accessibility", "structure", "accessibility", "formatting"]:
            deps.issues.append(
                CriticIssue(
                    severity=IssueSeverity.MINOR,
                    category=category,
                    description=f"{category} issue",
                    line_start=1,
                    line_end=1,
                )
            )

        # Filter by accessibility
        accessibility_issues = [i for i in deps.issues if i.category == "accessibility"]
        assert len(accessibility_issues) == 2

        # Filter by structure
        structure_issues = [i for i in deps.issues if i.category == "structure"]
        assert len(structure_issues) == 1


# =============================================================================
# PageBoundaryMap Helper Method Tests (for Critic context)
# =============================================================================


class TestPageBoundaryMapForCritic:
    """Tests for PageBoundaryMap methods used by CriticAgent."""

    def test_get_pages_for_range_within_single_page(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test getting pages for range within a single page."""
        # Page 1: lines 1-25
        pages = sample_page_boundary_map.get_pages_for_range(5, 20)
        assert pages == [1]

    def test_get_pages_for_range_spanning_two_pages(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test getting pages for range spanning two pages."""
        # Page 1: 1-25, Page 2: 26-50
        pages = sample_page_boundary_map.get_pages_for_range(20, 35)
        assert pages == [1, 2]

    def test_get_pages_for_range_spanning_all_pages(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test getting pages for range spanning all pages."""
        pages = sample_page_boundary_map.get_pages_for_range(1, 75)
        assert pages == [1, 2, 3]

    def test_get_page_for_line_boundary(
        self, sample_page_boundary_map: PageBoundaryMap
    ):
        """Test getting page for line at page boundary."""
        # Last line of page 1
        assert sample_page_boundary_map.get_page_for_line(25) == 1
        # First line of page 2
        assert sample_page_boundary_map.get_page_for_line(26) == 2

    def test_empty_boundary_map(self, empty_page_boundary_map: PageBoundaryMap):
        """Test behavior with empty boundary map."""
        assert empty_page_boundary_map.get_page_for_line(1) is None
        assert empty_page_boundary_map.get_pages_for_range(1, 10) == []


# =============================================================================
# Tool Result Type Interaction Tests
# =============================================================================


class TestToolResultInteractions:
    """Tests for interactions between different tool result types."""

    def test_view_then_report_workflow(self):
        """Test simulated workflow: view section, then report issue."""
        # Step 1: View a section
        view_result = ViewSectionResult(
            content="10: ![](missing-alt.png)\n11: \n12: Some text",
            start_line=10,
            end_line=12,
            total_lines=100,
        )

        # Step 2: Issue found in viewed content, report it
        report_result = ReportIssueResult(
            success=True,
            issue_id="abc123",
            message="Issue recorded: critical accessibility at lines 10-10",
        )

        assert view_result.content is not None
        assert report_result.success is True

    def test_find_pattern_then_report_workflow(self):
        """Test simulated workflow: find pattern, then report issues."""
        # Step 1: Find patterns
        find_result = FindPatternResult(
            found=True,
            matches=[
                {"line": 15, "text": "![](no-alt.png)"},
                {"line": 42, "text": "![](another.png)"},
            ],
            total_matches=2,
        )

        # Step 2: Report each found issue
        reports = []
        for match in find_result.matches:
            report = ReportIssueResult(
                success=True,
                issue_id=f"issue-{match['line']}",
                message=f"Issue at line {match['line']}",
            )
            reports.append(report)

        assert find_result.found is True
        assert len(reports) == 2
        assert all(r.success for r in reports)

    def test_mark_ready_after_no_critical_issues(self):
        """Test marking ready after finding no critical issues."""
        # Simulated: no critical issues found
        find_result = FindPatternResult(
            found=False,
            matches=[],
            total_matches=0,
        )

        # Mark ready succeeds
        mark_result = MarkReadyResult(
            success=True,
            message="Document marked ready for output. 0 minor/cosmetic issues noted.",
        )

        assert find_result.found is False
        assert mark_result.success is True

    def test_mark_ready_blocked_by_critical(self):
        """Test marking ready blocked when critical issues exist."""
        # Simulated: critical issue found
        find_result = FindPatternResult(
            found=True,
            matches=[{"line": 25, "text": "![](critical-missing.png)"}],
            total_matches=1,
        )

        # Mark ready fails
        mark_result = MarkReadyResult(
            success=False,
            message="Cannot mark ready: 1 critical issue(s) found. Fix them first.",
        )

        assert find_result.found is True
        assert mark_result.success is False
