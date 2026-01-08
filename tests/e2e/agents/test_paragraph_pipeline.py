"""E2E tests for paragraph pipeline integration (PRD-005).

Tests the complete flow:
1. PARAGRAPH jobs are created from detected paragraph issues
2. Jobs are routed to ParagraphAgent (not Worker)
3. Cross-page merge pass runs after per-page jobs
4. needs_review flags are properly surfaced in ledger
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.agents.events import EventBus
from src.agents.models import (
    CitationIssue,
    DocumentPlan,
    DocumentStructure,
    DocumentType,
    FootnoteIssue,
    Job,
    JobContext,
    JobType,
    Ledger,
    ListIssue,
    OutlineEntry,
    PageArtifactIssue,
    PagePlan,
    PageSkeleton,
    PageType,
    Task,
    TaskType,
    TypographyIssue,
)
from src.agents.planner import stage4_generate_jobs
from src.agents.subagents.types import ParagraphMergeResult
from src.agents.worker import execute_jobs_parallel


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_page_images() -> dict[int, Image.Image]:
    """Create sample page images."""
    return {
        1: Image.new("RGB", (800, 1000), color="white"),
        2: Image.new("RGB", (800, 1000), color="white"),
    }


@pytest.fixture
def sample_page_markdowns() -> dict[int, str]:
    """Create sample page markdowns with paragraph issues."""
    return {
        1: """# Test Document

This is the first paragraph of the document.

---

This text follows a page break marker.

The experiment demonstrates signif-
""",
        2: """icant improvements in accuracy[^1].

## Results

1. Item one
2) Item two
3. Item three

[^1]: Statistical significance p < 0.05

## References

[1] Author A (2024). Title.
""",
    }


@pytest.fixture
def sample_document_structure() -> DocumentStructure:
    """Create a sample document structure."""
    return DocumentStructure(
        title="Test Document",
        document_type=DocumentType.PAPER,
        outline=[
            OutlineEntry(heading="Test Document", level=1, page_start=1, page_end=2),
            OutlineEntry(heading="Results", level=2, page_start=2, page_end=2),
            OutlineEntry(heading="References", level=2, page_start=2, page_end=2),
        ],
        heading_fixes=[],
        key_terms=["accuracy", "experiment"],
    )


@pytest.fixture
def sample_page_plans_with_issues() -> dict[int, PagePlan]:
    """Create page plans with paragraph issues detected."""
    return {
        1: PagePlan(
            page_num=1,
            summary="Introduction with page break artifact",
            section_context="Introduction section",
            keywords=["experiment"],
            figures=[],
            tables=[],
            ocr_errors=[],
            # Paragraph issues
            page_artifacts=[
                PageArtifactIssue(
                    text="---",
                    artifact_type="page_break",
                    line_number=5,
                ),
            ],
            footnote_issues=[],
            citation_issues=[],
            list_issues=[],
            typography_issues=[],
            has_page_continuation=True,  # Paragraph continues to page 2
        ),
        2: PagePlan(
            page_num=2,
            summary="Results and references",
            section_context="Results section",
            keywords=["accuracy", "statistical"],
            figures=[],
            tables=[],
            ocr_errors=[],
            # Paragraph issues
            page_artifacts=[],
            footnote_issues=[
                FootnoteIssue(
                    marker="[^1]",
                    issue_type="verify_placement",
                    line_number=1,
                ),
            ],
            citation_issues=[
                CitationIssue(
                    marker="[1]",
                    issue_type="unlinked",
                    line_number=15,
                ),
            ],
            list_issues=[
                ListIssue(
                    location="lines:7-9",
                    issue_type="mixed_types",
                    description="Mixed numbering formats (1. and 2))",
                ),
            ],
            typography_issues=[],
            has_page_continuation=False,
        ),
    }


@pytest.fixture
def sample_ledger() -> Ledger:
    """Create an empty ledger."""
    return Ledger(document_id="test-doc-001", entries=[])


# =============================================================================
# Job Generation Tests
# =============================================================================


class TestParagraphJobGeneration:
    """Tests for PARAGRAPH job generation in stage4_generate_jobs."""

    def test_creates_paragraph_jobs_from_page_artifacts(
        self,
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """PARAGRAPH jobs should be created from page artifact issues."""
        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=sample_page_plans_with_issues,
            structure=sample_document_structure,
        )

        # Filter to PARAGRAPH jobs
        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]

        # Should have 2 PARAGRAPH jobs (one per page with issues)
        assert len(paragraph_jobs) == 2

        # Page 1 job should have page artifact task
        page1_job = next(j for j in paragraph_jobs if j.page == 1)
        artifact_tasks = [t for t in page1_job.tasks if t.task_type == TaskType.PAGE_ARTIFACT_REMOVAL]
        assert len(artifact_tasks) == 1
        assert "page_break" in artifact_tasks[0].context

    def test_creates_paragraph_jobs_from_footnote_issues(
        self,
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """PARAGRAPH jobs should include footnote correction tasks."""
        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=sample_page_plans_with_issues,
            structure=sample_document_structure,
        )

        # Page 2 job should have footnote task
        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]
        page2_job = next(j for j in paragraph_jobs if j.page == 2)
        footnote_tasks = [t for t in page2_job.tasks if t.task_type == TaskType.FOOTNOTE_CORRECTION]
        assert len(footnote_tasks) == 1
        assert "[^1]" in footnote_tasks[0].target

    def test_creates_paragraph_jobs_from_citation_issues(
        self,
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """PARAGRAPH jobs should include citation linking tasks."""
        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=sample_page_plans_with_issues,
            structure=sample_document_structure,
        )

        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]
        page2_job = next(j for j in paragraph_jobs if j.page == 2)
        citation_tasks = [t for t in page2_job.tasks if t.task_type == TaskType.CITATION_LINKING]
        assert len(citation_tasks) == 1
        assert "[1]" in citation_tasks[0].target

    def test_creates_paragraph_jobs_from_list_issues(
        self,
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """PARAGRAPH jobs should include list fix tasks."""
        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=sample_page_plans_with_issues,
            structure=sample_document_structure,
        )

        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]
        page2_job = next(j for j in paragraph_jobs if j.page == 2)
        list_tasks = [t for t in page2_job.tasks if t.task_type == TaskType.LIST_FIX]
        assert len(list_tasks) == 1
        assert "mixed_types" in list_tasks[0].context

    def test_paragraph_job_priority_after_content(
        self,
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """PARAGRAPH jobs should have priority > 100 (after CONTENT jobs)."""
        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=sample_page_plans_with_issues,
            structure=sample_document_structure,
        )

        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]
        for job in paragraph_jobs:
            # Priority should be page_num + 100
            assert job.priority == job.page + 100
            assert job.priority > 100  # After all CONTENT jobs

    def test_no_paragraph_job_if_no_issues(
        self,
        sample_page_markdowns: dict[int, str],
        sample_document_structure: DocumentStructure,
    ) -> None:
        """No PARAGRAPH job should be created if page has no issues."""
        empty_page_plans = {
            1: PagePlan(
                page_num=1,
                summary="Clean page",
                section_context="Test",
                keywords=[],
                figures=[],
                tables=[],
                ocr_errors=[],
                page_artifacts=[],
                footnote_issues=[],
                citation_issues=[],
                list_issues=[],
                typography_issues=[],
                has_page_continuation=False,
            ),
        }

        jobs = stage4_generate_jobs(
            page_markdowns=sample_page_markdowns,
            page_plans=empty_page_plans,
            structure=sample_document_structure,
        )

        paragraph_jobs = [j for j in jobs if j.job_type == JobType.PARAGRAPH]
        assert len(paragraph_jobs) == 0


# =============================================================================
# Job Routing Tests
# =============================================================================


class TestParagraphJobRouting:
    """Tests for PARAGRAPH job routing to ParagraphAgent."""

    @pytest.mark.asyncio
    async def test_paragraph_job_routes_to_paragraph_agent(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_ledger: Ledger,
    ) -> None:
        """PARAGRAPH jobs should be routed to ParagraphAgent, not Worker."""
        from src.agents.paragraph_agent import ParagraphAgentOutput

        # Create a PARAGRAPH job
        paragraph_job = Job(
            job_id="test-para-job",
            job_type=JobType.PARAGRAPH,
            priority=101,
            page=1,
            tasks=[
                Task(
                    task_type=TaskType.PAGE_ARTIFACT_REMOVAL,
                    target="artifact:1:5",
                    context="page_break: ---",
                ),
            ],
            context=JobContext(
                document_title="Test",
                document_type=DocumentType.PAPER,
                section_context="Test",
                dictionary=[],
            ),
            page_markdown=sample_page_markdowns[1],
        )

        # Mock the ParagraphAgent execution
        mock_output = ParagraphAgentOutput(
            tasks_applied_auto=["PAGE_ARTIFACT_REMOVAL: Removed ---"],
            tasks_applied_review=[],
            tasks_skipped=[],
            tasks_failed=[],
            summary="1 task applied",
        )

        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)

        with patch(
            "src.agents.paragraph_agent._get_paragraph_agent"
        ) as mock_get_agent:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            results = await execute_jobs_parallel(
                jobs=[paragraph_job],
                page_images=sample_page_images,
                element_bboxes={},
                page_width=800.0,
                ledger=sample_ledger,
                page_markdowns=sample_page_markdowns,
            )

        # ParagraphAgent should have been called (not Worker)
        mock_get_agent.assert_called_once()
        assert len(results) == 1
        assert results[0].job_id == "test-para-job"

    @pytest.mark.asyncio
    async def test_content_job_routes_to_worker(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_ledger: Ledger,
    ) -> None:
        """CONTENT jobs should still route to Worker, not ParagraphAgent."""
        # Create a CONTENT job
        content_job = Job(
            job_id="test-content-job",
            job_type=JobType.CONTENT,
            priority=2,
            page=1,
            tasks=[
                Task(
                    task_type=TaskType.ALT_TEXT,
                    target="fig:1",
                    context="Architecture diagram",
                ),
            ],
            context=JobContext(
                document_title="Test",
                document_type=DocumentType.PAPER,
                section_context="Test",
                dictionary=[],
            ),
            page_markdown=sample_page_markdowns[1],
        )

        # Mock the Worker execution
        with patch("src.agents.worker.execute_job") as mock_execute_job:
            from src.agents.worker import JobResult

            mock_execute_job.return_value = JobResult(
                job_id="test-content-job",
                success=True,
                updated_markdown=sample_page_markdowns[1],
                ledger_entries=[],
                tasks_completed=1,
                tasks_failed=0,
                input_tokens=100,
                output_tokens=50,
                duration_ms=1000,
            )

            results = await execute_jobs_parallel(
                jobs=[content_job],
                page_images=sample_page_images,
                element_bboxes={},
                page_width=800.0,
                ledger=sample_ledger,
                page_markdowns=sample_page_markdowns,
            )

        # Worker's execute_job should have been called
        mock_execute_job.assert_called_once()
        assert results[0].job_id == "test-content-job"


# =============================================================================
# Cross-Page Merge Tests
# =============================================================================


class TestCrossPageMerge:
    """Tests for cross-page paragraph merge pass."""

    @pytest.mark.asyncio
    async def test_merge_called_for_continuation_pages(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
        sample_ledger: Ledger,
    ) -> None:
        """Merge should be called for pages with has_page_continuation=True."""
        from src.agents.orchestrator import merge_cross_page_paragraphs

        # Create DocumentPlan with page continuation
        plan = DocumentPlan(
            filename="test.pdf",
            total_pages=2,
            structure=sample_document_structure,
            pages=sample_page_plans_with_issues,
            jobs=[],
        )

        # Mock the merge subagent
        mock_result = ParagraphMergeResult(
            should_merge=True,
            merged_text="significant improvements in accuracy",
            join_method="hyphen_removal",
            page1_remove_chars=7,  # "signif-\n"
            page2_remove_chars=6,  # "icant "
            confidence=0.92,
            reasoning="Word 'significant' split across pages",
        )

        with patch(
            "src.agents.orchestrator.invoke_paragraph_merge_subagent",
            return_value=mock_result,
        ) as mock_invoke:
            result = await merge_cross_page_paragraphs(
                page_markdowns=dict(sample_page_markdowns),
                page_images=sample_page_images,
                plan=plan,
                ledger=sample_ledger,
            )

        # Merge subagent should have been called for page 1->2
        mock_invoke.assert_called_once()

        # Ledger should have merge entries
        merge_entries = [e for e in sample_ledger.entries if e.action == TaskType.PARAGRAPH_MERGE]
        assert len(merge_entries) >= 1

    @pytest.mark.asyncio
    async def test_merge_not_called_without_continuation(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_document_structure: DocumentStructure,
        sample_ledger: Ledger,
    ) -> None:
        """Merge should not be called if no pages have continuation."""
        from src.agents.orchestrator import merge_cross_page_paragraphs

        # Create page plans without continuation
        page_plans = {
            1: PagePlan(
                page_num=1,
                summary="Test",
                section_context="Test",
                keywords=[],
                has_page_continuation=False,  # No continuation
            ),
            2: PagePlan(
                page_num=2,
                summary="Test",
                section_context="Test",
                keywords=[],
                has_page_continuation=False,
            ),
        }

        plan = DocumentPlan(
            filename="test.pdf",
            total_pages=2,
            structure=sample_document_structure,
            pages=page_plans,
            jobs=[],
        )

        with patch(
            "src.agents.orchestrator.invoke_paragraph_merge_subagent"
        ) as mock_invoke:
            await merge_cross_page_paragraphs(
                page_markdowns=dict(sample_page_markdowns),
                page_images=sample_page_images,
                plan=plan,
                ledger=sample_ledger,
            )

        # Merge subagent should NOT have been called
        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_merge_sets_needs_review(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
        sample_ledger: Ledger,
    ) -> None:
        """Merges with confidence < 0.8 should set needs_review=True."""
        from src.agents.orchestrator import merge_cross_page_paragraphs

        plan = DocumentPlan(
            filename="test.pdf",
            total_pages=2,
            structure=sample_document_structure,
            pages=sample_page_plans_with_issues,
            jobs=[],
        )

        # Mock with medium confidence (needs review)
        mock_result = ParagraphMergeResult(
            should_merge=True,
            merged_text="merged text",
            join_method="space",
            page1_remove_chars=5,
            page2_remove_chars=5,
            confidence=0.65,  # Below 0.8 threshold
            reasoning="Uncertain merge",
        )

        with patch(
            "src.agents.orchestrator.invoke_paragraph_merge_subagent",
            return_value=mock_result,
        ):
            await merge_cross_page_paragraphs(
                page_markdowns=dict(sample_page_markdowns),
                page_images=sample_page_images,
                plan=plan,
                ledger=sample_ledger,
            )

        # Ledger entries should have needs_review=True
        merge_entries = [e for e in sample_ledger.entries if e.action == TaskType.PARAGRAPH_MERGE]
        assert all(e.needs_review is True for e in merge_entries)

    @pytest.mark.asyncio
    async def test_very_low_confidence_merge_skipped(
        self,
        sample_page_images: dict[int, Image.Image],
        sample_page_markdowns: dict[int, str],
        sample_page_plans_with_issues: dict[int, PagePlan],
        sample_document_structure: DocumentStructure,
        sample_ledger: Ledger,
    ) -> None:
        """Merges with confidence < 0.5 should be skipped entirely."""
        from src.agents.orchestrator import merge_cross_page_paragraphs

        plan = DocumentPlan(
            filename="test.pdf",
            total_pages=2,
            structure=sample_document_structure,
            pages=sample_page_plans_with_issues,
            jobs=[],
        )

        # Mock with very low confidence (should skip)
        mock_result = ParagraphMergeResult(
            should_merge=True,  # Says merge, but...
            merged_text="merged text",
            join_method="space",
            page1_remove_chars=5,
            page2_remove_chars=5,
            confidence=0.35,  # Below 0.5 threshold
            reasoning="Very uncertain",
        )

        original_markdowns = dict(sample_page_markdowns)

        with patch(
            "src.agents.orchestrator.invoke_paragraph_merge_subagent",
            return_value=mock_result,
        ):
            result = await merge_cross_page_paragraphs(
                page_markdowns=dict(sample_page_markdowns),
                page_images=sample_page_images,
                plan=plan,
                ledger=sample_ledger,
            )

        # No ledger entries should be created (merge skipped)
        merge_entries = [e for e in sample_ledger.entries if e.action == TaskType.PARAGRAPH_MERGE]
        assert len(merge_entries) == 0


# =============================================================================
# API Response Tests
# =============================================================================


class TestNeedsReviewInAPI:
    """Tests for needs_review flag in API responses."""

    def test_ledger_entry_response_has_needs_review(self) -> None:
        """LedgerEntryResponse should include needs_review field."""
        from src.api.schemas import LedgerEntryResponse

        entry = LedgerEntryResponse(
            entry_id="test-entry",
            page=1,
            action="paragraph_merge",
            target="page_start:2",
            before="old",
            after="new",
            reasoning="test",
            confidence=0.65,
            timestamp="2024-01-01T00:00:00Z",
            needs_review=True,
        )

        assert entry.needs_review is True

    def test_ledger_response_has_entries_needing_review(self) -> None:
        """LedgerResponse should include entries_needing_review count."""
        from src.api.schemas import LedgerEntryResponse, LedgerPageGroup, LedgerResponse

        entries = [
            LedgerEntryResponse(
                entry_id="e1",
                page=1,
                action="alt_text",
                target="fig:1",
                before="",
                after="alt",
                reasoning="test",
                confidence=0.9,
                timestamp="2024-01-01T00:00:00Z",
                needs_review=False,
            ),
            LedgerEntryResponse(
                entry_id="e2",
                page=1,
                action="paragraph_merge",
                target="page_start:2",
                before="old",
                after="new",
                reasoning="test",
                confidence=0.65,
                timestamp="2024-01-01T00:00:00Z",
                needs_review=True,
            ),
        ]

        response = LedgerResponse(
            job_id="test-job",
            document_title="test.pdf",
            total_pages=2,
            pages_with_changes=1,
            total_edits=2,
            entries_needing_review=1,
            pages=[
                LedgerPageGroup(
                    page=1,
                    entries=entries,
                    edit_count=2,
                ),
            ],
            final_markdown_url="https://example.com/result.md",
        )

        assert response.entries_needing_review == 1
