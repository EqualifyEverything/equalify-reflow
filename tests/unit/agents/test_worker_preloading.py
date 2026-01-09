"""Tests for pre-emptive context loading optimization in worker.py.

This tests the optimization that pre-crops element images and pre-finds
placeholder text to reduce tool calls during job execution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from src.agents.models import DocumentType, Job, JobContext, JobType, Task, TaskType
from src.agents.worker import (
    _build_preloaded_prompt,
    _precrop_elements_for_job,
    _prefind_placeholders_for_job,
)


@pytest.fixture
def sample_job() -> Job:
    """Create a sample job with various task types."""
    return Job(
        job_id="test-job-123",
        job_type=JobType.CONTENT,
        priority=2,
        page=1,
        tasks=[
            Task(
                task_id="task-1",
                task_type=TaskType.ALT_TEXT,
                target="fig:1",
                context="A bar chart showing sales data",
                priority=1,
            ),
            Task(
                task_id="task-2",
                task_type=TaskType.TABLE_TRANSCRIPTION,
                target="table:1",
                context="Product pricing table",
                priority=1,
            ),
            Task(
                task_id="task-3",
                task_type=TaskType.HEADING_FIX,
                target="Introduction",
                context="FIND: ## Introduction\nREPLACE WITH: ### Introduction",
                priority=1,
            ),
        ],
        context=JobContext(
            document_title="Test Document",
            document_type=DocumentType.PAPER,
            section_context="Introduction section",
            dictionary=["test", "document"],
            relevant_outline=[],
        ),
        page_markdown="""# Test Document

<!-- image 1 -->

Some text about the image.

<!-- table 1 -->

## Introduction

This is the introduction.
""",
    )


@pytest.fixture
def sample_page_image():
    """Create a mock PIL Image."""
    mock_image = MagicMock()
    mock_image.width = 800
    mock_image.height = 1000
    return mock_image


@pytest.fixture
def sample_element_bboxes():
    """Create sample bounding boxes for elements."""
    return {
        (1, "figure", 1): (100.0, 100.0, 300.0, 200.0),
        (1, "table", 1): (100.0, 300.0, 700.0, 500.0),
    }


class TestPrecropElementsForJob:
    """Tests for _precrop_elements_for_job function."""

    def test_precrop_figure(self, sample_job: Job, sample_page_image, sample_element_bboxes):
        """Test that figures are pre-cropped when bbox is available."""
        with patch("src.utils.image_utils.crop_element") as mock_crop:
            mock_crop.return_value = MagicMock()

            result = _precrop_elements_for_job(
                job=sample_job,
                page_image=sample_page_image,
                element_bboxes=sample_element_bboxes,
                page_width=800.0,
            )

            assert "fig:1" in result
            mock_crop.assert_any_call(
                sample_page_image,
                (100.0, 100.0, 300.0, 200.0),
                800.0,
            )

    def test_precrop_table(self, sample_job: Job, sample_page_image, sample_element_bboxes):
        """Test that tables are pre-cropped when bbox is available."""
        with patch("src.utils.image_utils.crop_element") as mock_crop:
            mock_crop.return_value = MagicMock()

            result = _precrop_elements_for_job(
                job=sample_job,
                page_image=sample_page_image,
                element_bboxes=sample_element_bboxes,
                page_width=800.0,
            )

            assert "table:1" in result
            mock_crop.assert_any_call(
                sample_page_image,
                (100.0, 300.0, 700.0, 500.0),
                800.0,
            )

    def test_missing_bbox_skipped(self, sample_job: Job, sample_page_image):
        """Test that elements without bboxes are skipped (fallback to tools)."""
        # No bboxes for page 1
        empty_bboxes: dict = {}

        with patch("src.utils.image_utils.crop_element") as mock_crop:
            result = _precrop_elements_for_job(
                job=sample_job,
                page_image=sample_page_image,
                element_bboxes=empty_bboxes,
                page_width=800.0,
            )

            assert len(result) == 0
            mock_crop.assert_not_called()

    def test_invalid_target_format_handled(self, sample_page_image, sample_element_bboxes):
        """Test that invalid target formats don't crash."""
        job = Job(
            job_id="test-job",
            job_type=JobType.CONTENT,
            priority=2,
            page=1,
            tasks=[
                Task(
                    task_id="task-1",
                    task_type=TaskType.ALT_TEXT,
                    target="invalid-format",  # No colon separator
                    context="Test",
                    priority=1,
                ),
            ],
            context=JobContext(
                document_title="Test",
                document_type=DocumentType.OTHER,
                section_context="",
                dictionary=[],
                relevant_outline=[],
            ),
            page_markdown="",
        )

        with patch("src.utils.image_utils.crop_element"):
            result = _precrop_elements_for_job(
                job=job,
                page_image=sample_page_image,
                element_bboxes=sample_element_bboxes,
                page_width=800.0,
            )

            # Should not crash, just return empty
            assert "invalid-format" not in result


class TestPrefindPlaceholdersForJob:
    """Tests for _prefind_placeholders_for_job function."""

    def test_find_image_placeholder_comment(self, sample_job: Job):
        """Test finding <!-- image N --> placeholders."""
        result = _prefind_placeholders_for_job(
            job=sample_job,
            markdown=sample_job.page_markdown,
        )

        assert result.get("fig:1") == "<!-- image 1 -->"

    def test_find_table_placeholder_comment(self, sample_job: Job):
        """Test finding <!-- table N --> placeholders."""
        result = _prefind_placeholders_for_job(
            job=sample_job,
            markdown=sample_job.page_markdown,
        )

        assert result.get("table:1") == "<!-- table 1 -->"

    def test_find_heading_fix_placeholder(self, sample_job: Job):
        """Test finding heading text from FIND: in context."""
        result = _prefind_placeholders_for_job(
            job=sample_job,
            markdown=sample_job.page_markdown,
        )

        assert result.get("Introduction") == "## Introduction"

    def test_missing_placeholder_returns_none(self):
        """Test that missing placeholders return None (triggers fallback)."""
        job = Job(
            job_id="test-job",
            job_type=JobType.CONTENT,
            priority=2,
            page=1,
            tasks=[
                Task(
                    task_id="task-1",
                    task_type=TaskType.ALT_TEXT,
                    target="fig:5",  # Doesn't exist
                    context="Test",
                    priority=1,
                ),
            ],
            context=JobContext(
                document_title="Test",
                document_type=DocumentType.OTHER,
                section_context="",
                dictionary=[],
                relevant_outline=[],
            ),
            page_markdown="No placeholders here",
        )

        result = _prefind_placeholders_for_job(job=job, markdown=job.page_markdown)

        assert result.get("fig:5") is None

    def test_find_image_markdown_pattern(self):
        """Test finding ![](figure_N.png) patterns."""
        job = Job(
            job_id="test-job",
            job_type=JobType.CONTENT,
            priority=2,
            page=1,
            tasks=[
                Task(
                    task_id="task-1",
                    task_type=TaskType.ALT_TEXT,
                    target="fig:2",
                    context="Test",
                    priority=1,
                ),
            ],
            context=JobContext(
                document_title="Test",
                document_type=DocumentType.OTHER,
                section_context="",
                dictionary=[],
                relevant_outline=[],
            ),
            page_markdown="Some text ![](figure_2.png) more text",
        )

        result = _prefind_placeholders_for_job(job=job, markdown=job.page_markdown)

        assert result.get("fig:2") == "![](figure_2.png)"


class TestBuildPreloadedPrompt:
    """Tests for _build_preloaded_prompt function."""

    def test_prompt_includes_preloaded_placeholder(self, sample_job: Job):
        """Test that pre-found placeholders are included in prompt."""
        prefound = {"fig:1": "<!-- image 1 -->", "table:1": "<!-- table 1 -->"}

        prompt = _build_preloaded_prompt(
            job=sample_job,
            context=sample_job.context,
            outline_summary="# Test Document",
            prefound_placeholders=prefound,
            precropped_targets={"fig:1", "table:1"},
        )

        assert "<!-- image 1 -->" in prompt
        assert "<!-- table 1 -->" in prompt

    def test_prompt_indicates_missing_placeholder(self, sample_job: Job):
        """Test that missing placeholders trigger fallback instructions."""
        prefound: dict = {"fig:1": None}  # Not found

        prompt = _build_preloaded_prompt(
            job=sample_job,
            context=sample_job.context,
            outline_summary="# Test Document",
            prefound_placeholders=prefound,
            precropped_targets=set(),
        )

        assert "find_text()" in prompt
        assert "Not found" in prompt

    def test_prompt_includes_optimized_workflow_when_all_preloaded(self, sample_job: Job):
        """Test that optimized workflow is shown when all context is pre-loaded."""
        prefound = {
            "fig:1": "<!-- image 1 -->",
            "table:1": "<!-- table 1 -->",
            "Introduction": "## Introduction",
        }

        prompt = _build_preloaded_prompt(
            job=sample_job,
            context=sample_job.context,
            outline_summary="# Test Document",
            prefound_placeholders=prefound,
            precropped_targets={"fig:1", "table:1"},
        )

        assert "Optimized Workflow" in prompt or "Context Pre-loaded" in prompt

    def test_prompt_includes_standard_workflow_when_partial_preloading(self, sample_job: Job):
        """Test that standard workflow is shown when pre-loading is incomplete."""
        # Missing some placeholders
        prefound: dict = {"fig:1": None, "table:1": None, "Introduction": None}

        prompt = _build_preloaded_prompt(
            job=sample_job,
            context=sample_job.context,
            outline_summary="# Test Document",
            prefound_placeholders=prefound,
            precropped_targets=set(),
        )

        assert "Standard Workflow" in prompt or "find_text()" in prompt
