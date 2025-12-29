"""Tests for page-by-page extraction.

Tests cover:
- PageContext building
- PageMetrics validation
- Page stitching with continuation markers
- Parallel extraction flow
- Error handling (ExtractionError)
- Validation logic
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.extraction import (
    CONTINUATION_MARKER_FROM,
    CONTINUATION_MARKER_TO,
    ExtractionError,
    ExtractionMetrics,
    ExtractionResult,
    PageContext,
    PageExtractionResult,
    PageMetrics,
    ValidationIssue,
    validate_page_extraction,
)
from src.agents.extraction.extractor import (
    _build_document_summary,
    _build_page_contexts,
    _build_page_prompt,
    _stitch_pages,
)
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)


# =============================================================================
# Test Fixtures
# =============================================================================


def _create_heading_tree(sections: list[dict] | None = None) -> HeadingTree:
    """Create a HeadingTree for testing."""
    if sections is None:
        sections = [
            {"level": 1, "title": "Document Title", "page": 1},
            {"level": 2, "title": "Introduction", "page": 1},
            {"level": 2, "title": "Methods", "page": 2},
        ]
    return HeadingTree(
        document_title="Test Document",
        title_page=1,
        sections=[HeadingNode(**s) for s in sections],
        total_pages=2,
        layout_type="single_column",
        confidence=0.9,
    )


def _create_manifest(
    total_pages: int = 2,
    heading_tree: HeadingTree | None = None,
    page_features: list[PageFeatures] | None = None,
) -> DocumentManifest:
    """Create a DocumentManifest for testing."""
    if heading_tree is None:
        heading_tree = _create_heading_tree()

    if page_features is None:
        page_features = [PageFeatures(page_num=i + 1, layout_type="single_column") for i in range(total_pages)]

    return DocumentManifest(
        job_id="test-job-123",
        document_title="Test Document",
        document_type="research_paper",
        total_pages=total_pages,
        heading_tree_json=heading_tree.model_dump_json(),
        page_features=page_features,
        required_agents=["structure"],
        skip_agents=[],
        analysis_confidence=0.9,
    )


def _create_page_context(
    page_num: int = 1,
    layout_type: str = "single_column",
    headings: list[HeadingNode] | None = None,
) -> PageContext:
    """Create a PageContext for testing."""
    return PageContext(
        page_num=page_num,
        image_base64="base64encodedimage",
        layout_type=layout_type,
        headings_on_page=headings or [],
        document_summary="Test Document\nType: research_paper\nTotal pages: 2",
        document_title="Test Document",
        document_type="research_paper",
        total_pages=2,
    )


# =============================================================================
# PageMetrics Tests
# =============================================================================


@pytest.mark.unit
class TestPageMetrics:
    """Tests for PageMetrics model."""

    def test_valid_page_metrics(self) -> None:
        """Test creating valid PageMetrics."""
        metrics = PageMetrics(
            page_num=1,
            has_page_marker=True,
            word_count=150,
            expected_headings_found=["Introduction"],
            expected_headings_missing=[],
            has_continuation_from=False,
            has_continuation_to=False,
            issues=[],
            is_valid=True,
        )
        assert metrics.is_valid
        assert metrics.word_count == 150
        assert len(metrics.critical_issues) == 0

    def test_page_metrics_with_issues(self) -> None:
        """Test PageMetrics with validation issues."""
        issues = [
            ValidationIssue(
                issue_type="missing_page_marker",
                severity="critical",
                message="Missing page marker",
                page_num=1,
            ),
            ValidationIssue(
                issue_type="missing_heading",
                severity="warning",
                message="Missing heading: Introduction",
                page_num=1,
            ),
        ]
        metrics = PageMetrics(
            page_num=1,
            has_page_marker=False,
            word_count=50,
            expected_headings_found=[],
            expected_headings_missing=["Introduction"],
            issues=issues,
            is_valid=False,
        )
        assert not metrics.is_valid
        assert len(metrics.critical_issues) == 1
        # ValidationIssue.to_correction_guidance() uppercases issue_type
        assert "MISSING_PAGE_MARKER" in metrics.get_correction_guidance()

    def test_continuation_markers(self) -> None:
        """Test continuation marker tracking."""
        metrics = PageMetrics(
            page_num=2,
            has_page_marker=True,
            word_count=100,
            has_continuation_from=True,
            has_continuation_to=True,
            issues=[],
            is_valid=True,
        )
        assert metrics.has_continuation_from
        assert metrics.has_continuation_to


# =============================================================================
# PageContext Tests
# =============================================================================


@pytest.mark.unit
class TestPageContext:
    """Tests for PageContext dataclass."""

    def test_page_context_creation(self) -> None:
        """Test creating a PageContext."""
        ctx = _create_page_context(page_num=1, layout_type="two_column")
        assert ctx.page_num == 1
        assert ctx.layout_type == "two_column"
        assert ctx.total_pages == 2

    def test_page_context_with_headings(self) -> None:
        """Test PageContext with expected headings."""
        headings = [
            HeadingNode(level=2, title="Introduction", page=1),
            HeadingNode(level=2, title="Background", page=1),
        ]
        ctx = _create_page_context(headings=headings)
        assert len(ctx.headings_on_page) == 2
        assert ctx.headings_on_page[0].title == "Introduction"

    def test_previous_issues_mutable(self) -> None:
        """Test that previous_issues can be updated for retries."""
        ctx = _create_page_context()
        assert ctx.previous_issues == []

        ctx.previous_issues = [
            ValidationIssue(
                issue_type="sparse_content",
                severity="critical",
                message="Page has only 5 words",
                page_num=1,
            )
        ]
        assert len(ctx.previous_issues) == 1


# =============================================================================
# Page Stitching Tests
# =============================================================================


@pytest.mark.unit
class TestPageStitching:
    """Tests for _stitch_pages function."""

    def test_stitch_simple_pages(self) -> None:
        """Test stitching pages without continuation markers."""
        pages = [
            "<!-- Page 1 -->\n\n# Title\n\nFirst page content.",
            "<!-- Page 2 -->\n\n## Section\n\nSecond page content.",
        ]
        result = _stitch_pages(pages)

        assert "<!-- Page 1 -->" in result
        assert "<!-- Page 2 -->" in result
        assert "First page content." in result
        assert "Second page content." in result

    def test_stitch_with_continuation_markers(self) -> None:
        """Test stitching pages with sentence continuation."""
        pages = [
            "<!-- Page 1 -->\n\nThis sentence continues on the next page[CONTINUES_ON_NEXT]",
            "[CONTINUES_FROM_PREVIOUS]and finishes here.\n\n<!-- Page 2 -->\n\nNew paragraph.",
        ]
        result = _stitch_pages(pages)

        # Markers should be removed
        assert CONTINUATION_MARKER_FROM not in result
        assert CONTINUATION_MARKER_TO not in result
        # Sentences should be joined
        assert "continues on the next page and finishes here" in result

    def test_stitch_empty_list(self) -> None:
        """Test stitching empty page list."""
        result = _stitch_pages([])
        assert result == ""

    def test_stitch_single_page(self) -> None:
        """Test stitching single page."""
        pages = ["<!-- Page 1 -->\n\nOnly page content."]
        result = _stitch_pages(pages)
        assert "Only page content." in result

    def test_stitch_removes_excessive_newlines(self) -> None:
        """Test that excessive newlines are cleaned up."""
        pages = [
            "<!-- Page 1 -->\n\n\n\nContent with\n\n\n\nmany newlines.",
        ]
        result = _stitch_pages(pages)
        # Triple newlines should be reduced to double
        assert "\n\n\n" not in result


# =============================================================================
# Page Validation Tests
# =============================================================================


@pytest.mark.unit
class TestValidatePageExtraction:
    """Tests for validate_page_extraction function."""

    def test_valid_page_extraction(self) -> None:
        """Test validation of valid page extraction."""
        markdown = "<!-- Page 1 -->\n\n# Introduction\n\nThis is the introduction paragraph with enough content to pass validation checks."
        ctx = _create_page_context(
            page_num=1,
            headings=[HeadingNode(level=1, title="Introduction", page=1)],
        )

        metrics = validate_page_extraction(markdown, ctx)

        assert metrics.is_valid
        assert metrics.has_page_marker
        assert metrics.word_count > 10

    def test_missing_page_marker(self) -> None:
        """Test validation fails when page marker is missing."""
        markdown = "# Introduction\n\nContent without page marker."
        ctx = _create_page_context(page_num=1)

        metrics = validate_page_extraction(markdown, ctx)

        assert not metrics.is_valid
        assert not metrics.has_page_marker
        assert any(i.issue_type == "missing_page_marker" for i in metrics.issues)

    def test_sparse_content_single_column(self) -> None:
        """Test validation fails for sparse content on single column page."""
        markdown = "<!-- Page 1 -->\n\nShort."
        ctx = _create_page_context(page_num=1, layout_type="single_column")

        metrics = validate_page_extraction(markdown, ctx)

        assert not metrics.is_valid
        assert any(i.issue_type == "sparse_content" for i in metrics.issues)

    def test_sparse_content_two_column_stricter(self) -> None:
        """Test that two-column pages have stricter word count requirements."""
        # 15 words - passes single column but fails two column
        markdown = "<!-- Page 1 -->\n\nThis is some content that has about fifteen words in it here."

        ctx_single = _create_page_context(page_num=1, layout_type="single_column")
        ctx_two_col = _create_page_context(page_num=1, layout_type="two_column")

        metrics_single = validate_page_extraction(markdown, ctx_single)
        metrics_two_col = validate_page_extraction(markdown, ctx_two_col)

        assert metrics_single.is_valid
        assert not metrics_two_col.is_valid

    def test_continuation_markers_detected(self) -> None:
        """Test that continuation markers are detected."""
        markdown = f"<!-- Page 2 -->\n\n{CONTINUATION_MARKER_FROM}continued text here{CONTINUATION_MARKER_TO}"
        ctx = _create_page_context(page_num=2)

        metrics = validate_page_extraction(markdown, ctx)

        assert metrics.has_continuation_from
        assert metrics.has_continuation_to

    def test_missing_heading_warning(self) -> None:
        """Test that missing expected heading creates warning."""
        # Use a heading title that won't accidentally match content words
        markdown = "<!-- Page 1 -->\n\nContent without the expected heading but enough words to pass."
        ctx = _create_page_context(
            page_num=1,
            headings=[HeadingNode(level=2, title="Methodology Section", page=1)],
        )

        metrics = validate_page_extraction(markdown, ctx)

        # Should pass (missing heading is warning, not critical)
        assert metrics.is_valid
        assert "Methodology Section" in metrics.expected_headings_missing


# =============================================================================
# Document Summary Building Tests
# =============================================================================


@pytest.mark.unit
class TestBuildDocumentSummary:
    """Tests for _build_document_summary function."""

    def test_basic_summary(self) -> None:
        """Test building basic document summary."""
        manifest = _create_manifest()
        heading_tree = _create_heading_tree()

        summary = _build_document_summary(manifest, heading_tree)

        assert "Test Document" in summary
        assert "research_paper" in summary
        assert "2" in summary  # total pages

    def test_summary_includes_sections(self) -> None:
        """Test summary includes main section titles."""
        sections = [
            {"level": 1, "title": "Main Title", "page": 1},
            {"level": 2, "title": "Introduction", "page": 1},
            {"level": 2, "title": "Methods", "page": 2},
            {"level": 3, "title": "Subsection", "page": 2},  # H3, should be excluded
        ]
        heading_tree = _create_heading_tree(sections)
        manifest = _create_manifest(heading_tree=heading_tree)

        summary = _build_document_summary(manifest, heading_tree)

        assert "Introduction" in summary
        assert "Methods" in summary


# =============================================================================
# Page Contexts Building Tests
# =============================================================================


@pytest.mark.unit
class TestBuildPageContexts:
    """Tests for _build_page_contexts function."""

    def test_builds_context_per_page(self) -> None:
        """Test that context is built for each page."""
        from src.services.pdf_converter import PageData

        pages = [
            PageData(page_num=1, image_base64="img1"),
            PageData(page_num=2, image_base64="img2"),
        ]
        manifest = _create_manifest(total_pages=2)

        contexts = _build_page_contexts(pages, manifest)

        assert len(contexts) == 2
        assert contexts[0].page_num == 1
        assert contexts[1].page_num == 2

    def test_headings_grouped_by_page(self) -> None:
        """Test that headings are correctly grouped by page."""
        from src.services.pdf_converter import PageData

        sections = [
            {"level": 1, "title": "Title", "page": 1},
            {"level": 2, "title": "Section A", "page": 1},
            {"level": 2, "title": "Section B", "page": 2},
        ]
        heading_tree = _create_heading_tree(sections)
        manifest = _create_manifest(heading_tree=heading_tree)

        pages = [
            PageData(page_num=1, image_base64="img1"),
            PageData(page_num=2, image_base64="img2"),
        ]

        contexts = _build_page_contexts(pages, manifest)

        # Page 1 should have 2 headings
        assert len(contexts[0].headings_on_page) == 2
        # Page 2 should have 1 heading
        assert len(contexts[1].headings_on_page) == 1
        assert contexts[1].headings_on_page[0].title == "Section B"

    def test_layout_type_from_features(self) -> None:
        """Test that layout type is extracted from page features."""
        from src.services.pdf_converter import PageData

        page_features = [
            PageFeatures(page_num=1, layout_type="single_column"),
            PageFeatures(page_num=2, layout_type="two_column"),
        ]
        manifest = _create_manifest(page_features=page_features)

        pages = [
            PageData(page_num=1, image_base64="img1"),
            PageData(page_num=2, image_base64="img2"),
        ]

        contexts = _build_page_contexts(pages, manifest)

        assert contexts[0].layout_type == "single_column"
        assert contexts[1].layout_type == "two_column"


# =============================================================================
# Page Prompt Building Tests
# =============================================================================


@pytest.mark.unit
class TestBuildPagePrompt:
    """Tests for _build_page_prompt function."""

    def test_first_attempt_uses_standard_prompt(self) -> None:
        """Test that first attempt uses standard page_user_prompt."""
        ctx = _create_page_context(page_num=1)
        prompts = {
            "page_user_prompt": "Extract page {page_num}. Layout: {layout_type}. Headings: {headings_for_page}. Summary: {document_summary}. Type: {document_type}. Pages: {total_pages}.",
            "page_correction_prompt": "Correction needed for page {page_num}.",
        }

        result = _build_page_prompt(ctx, prompts, attempt=1)

        assert "Extract page 1" in result
        assert "Correction needed" not in result

    def test_retry_uses_correction_prompt(self) -> None:
        """Test that retry attempts use correction prompt."""
        ctx = _create_page_context(page_num=1)
        ctx.previous_issues = [
            ValidationIssue(
                issue_type="sparse_content",
                severity="critical",
                message="Page has only 5 words",
                page_num=1,
            )
        ]
        prompts = {
            "page_user_prompt": "Extract page {page_num}.",
            "page_correction_prompt": "Fix issues on page {page_num}: {issues}. Layout: {layout_type}. Hint: {reading_order_hint}.",
        }

        result = _build_page_prompt(ctx, prompts, attempt=2)

        assert "Fix issues" in result
        assert "Page has only 5 words" in result

    def test_headings_formatted_in_prompt(self) -> None:
        """Test that headings are formatted in the prompt."""
        headings = [
            HeadingNode(level=2, title="Introduction", page=1, section_number="1"),
            HeadingNode(level=3, title="Background", page=1, section_number="1.1"),
        ]
        ctx = _create_page_context(headings=headings)
        prompts = {
            "page_user_prompt": "Headings: {headings_for_page}",
            "page_correction_prompt": "",
        }

        result = _build_page_prompt(ctx, prompts, attempt=1)

        assert "## 1 Introduction" in result
        assert "### 1.1 Background" in result

    def test_no_headings_message(self) -> None:
        """Test message when no headings expected on page."""
        ctx = _create_page_context(headings=[])
        prompts = {
            "page_user_prompt": "Headings: {headings_for_page}",
            "page_correction_prompt": "",
        }

        result = _build_page_prompt(ctx, prompts, attempt=1)

        assert "No headings expected" in result


# =============================================================================
# ExtractionError Tests
# =============================================================================


@pytest.mark.unit
class TestExtractionError:
    """Tests for ExtractionError exception."""

    def test_basic_error(self) -> None:
        """Test basic ExtractionError creation."""
        error = ExtractionError("Page 1 failed")
        assert str(error) == "Page 1 failed"
        assert error.page_num is None
        assert error.issues == []

    def test_error_with_page_num(self) -> None:
        """Test ExtractionError with page number."""
        error = ExtractionError("Validation failed", page_num=3)
        assert error.page_num == 3

    def test_error_with_issues(self) -> None:
        """Test ExtractionError with validation issues."""
        issues = [
            ValidationIssue(
                issue_type="missing_page_marker",
                severity="critical",
                message="Missing marker",
                page_num=1,
            ),
            ValidationIssue(
                issue_type="sparse_content",
                severity="critical",
                message="Too few words",
                page_num=1,
            ),
        ]
        error = ExtractionError("Page 1 failed", page_num=1, issues=issues)

        error_str = str(error)
        assert "Page 1 failed" in error_str
        assert "Missing marker" in error_str
        assert "Too few words" in error_str


# =============================================================================
# Integration Tests (Mocked)
# =============================================================================


@pytest.mark.unit
class TestExtractWithValidationMocked:
    """Tests for extract_with_validation with mocked LLM calls."""

    @pytest.mark.asyncio
    async def test_raises_on_empty_pages(self) -> None:
        """Test that empty pages list raises ValueError."""
        from src.agents.extraction import extract_with_validation

        manifest = _create_manifest()

        with pytest.raises(ValueError, match="No pages provided"):
            await extract_with_validation([], manifest, "test-job")

    @pytest.mark.asyncio
    async def test_parallel_extraction_success(self) -> None:
        """Test successful parallel extraction of multiple pages."""
        from src.agents.extraction.extractor import extract_with_validation
        from src.services.pdf_converter import PageData

        pages = [
            PageData(page_num=1, image_base64="img1"),
            PageData(page_num=2, image_base64="img2"),
        ]
        manifest = _create_manifest(total_pages=2)

        # Mock the page extraction function
        mock_result = PageExtractionResult(
            page_num=1,
            markdown="<!-- Page 1 -->\n\nTest content with enough words to pass validation.",
            metrics=PageMetrics(
                page_num=1,
                has_page_marker=True,
                word_count=50,
                is_valid=True,
                issues=[],
            ),
            attempt_count=1,
            usage=LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01),
        )

        with patch("src.agents.extraction.extractor._extract_single_page") as mock_extract:
            mock_extract.return_value = mock_result

            with patch("src.agents.extraction.extractor.get_page_agent"):
                result, usage = await extract_with_validation(pages, manifest, "test-job")

        assert result.metrics.is_valid or len(result.metrics.issues) > 0

    @pytest.mark.asyncio
    async def test_extraction_error_on_page_failure(self) -> None:
        """Test that ExtractionError is raised when a page fails validation."""
        from src.agents.extraction.extractor import extract_with_validation
        from src.services.pdf_converter import PageData

        pages = [PageData(page_num=1, image_base64="img1")]
        manifest = _create_manifest(total_pages=1)

        # Create a failed page result
        failed_result = PageExtractionResult(
            page_num=1,
            markdown="<!-- Page 1 -->\n\nShort",
            metrics=PageMetrics(
                page_num=1,
                has_page_marker=True,
                word_count=2,
                is_valid=False,
                issues=[
                    ValidationIssue(
                        issue_type="sparse_content",
                        severity="critical",
                        message="Page has only 2 words",
                        page_num=1,
                    )
                ],
            ),
            attempt_count=2,
            usage=LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01),
        )

        with patch("src.agents.extraction.extractor._extract_single_page") as mock_extract:
            mock_extract.return_value = failed_result

            with patch("src.agents.extraction.extractor.get_page_agent"):
                with pytest.raises(ExtractionError) as exc_info:
                    await extract_with_validation(pages, manifest, "test-job")

        assert exc_info.value.page_num == 1
        assert len(exc_info.value.issues) > 0


# =============================================================================
# Continuation Marker Constants Tests
# =============================================================================


@pytest.mark.unit
class TestContinuationMarkers:
    """Tests for continuation marker constants."""

    def test_marker_values(self) -> None:
        """Test continuation marker constant values."""
        assert CONTINUATION_MARKER_FROM == "[CONTINUES_FROM_PREVIOUS]"
        assert CONTINUATION_MARKER_TO == "[CONTINUES_ON_NEXT]"

    def test_markers_are_unique(self) -> None:
        """Test that markers are different from each other."""
        assert CONTINUATION_MARKER_FROM != CONTINUATION_MARKER_TO

    def test_markers_not_in_normal_text(self) -> None:
        """Test that markers are unlikely to appear in normal text."""
        normal_text = "This is a normal academic paper about continuation methods."
        assert CONTINUATION_MARKER_FROM not in normal_text
        assert CONTINUATION_MARKER_TO not in normal_text
