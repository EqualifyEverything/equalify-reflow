"""Unit tests for the revision prompt module.

Tests that prompt constants exist and contain expected keywords, and that
builder functions produce correct markdown sections.
"""

from __future__ import annotations

import pytest

from src.agents.prompts.revision import (
    DECOMPOSITION_SYSTEM_PROMPT,
    REVISION_SYSTEM_PROMPT,
    build_decomposition_user_message,
    build_page_attributes_summary,
    build_revision_user_message,
)
from src.services.pipeline_viewer_models import (
    LayoutType,
    PageAttributes,
    RevisionTask,
    RevisionTaskCategory,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestDecompositionSystemPrompt:
    def test_contains_task_fields(self):
        assert "tasks" in DECOMPOSITION_SYSTEM_PROMPT
        assert "reasoning" in DECOMPOSITION_SYSTEM_PROMPT

    def test_contains_category_guidance(self):
        assert "content" in DECOMPOSITION_SYSTEM_PROMPT
        assert "formatting" in DECOMPOSITION_SYSTEM_PROMPT
        assert "accessibility" in DECOMPOSITION_SYSTEM_PROMPT

    def test_mentions_needs_image(self):
        assert "needs_image" in DECOMPOSITION_SYSTEM_PROMPT


class TestRevisionSystemPrompt:
    def test_contains_tool_names(self):
        assert "str_replace" in REVISION_SYSTEM_PROMPT
        assert "no_changes" in REVISION_SYSTEM_PROMPT

    def test_contains_guidelines(self):
        assert "old_text" in REVISION_SYSTEM_PROMPT
        assert "category" in REVISION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# build_decomposition_user_message
# ---------------------------------------------------------------------------


class TestBuildDecompositionUserMessage:
    def test_includes_feedback(self):
        msg = build_decomposition_user_message(
            feedback="Fix the heading",
            outline_text="",
            total_pages=3,
            page_attributes_summary="",
        )
        assert "Fix the heading" in msg

    def test_includes_outline(self):
        msg = build_decomposition_user_message(
            feedback="Fix it",
            outline_text="  # Introduction (p1)",
            total_pages=3,
            page_attributes_summary="",
        )
        assert "# Introduction (p1)" in msg
        assert "Document Outline" in msg

    def test_includes_total_pages(self):
        msg = build_decomposition_user_message(
            feedback="Fix it",
            outline_text="",
            total_pages=5,
            page_attributes_summary="",
        )
        assert "5" in msg

    def test_includes_page_attributes(self):
        msg = build_decomposition_user_message(
            feedback="Fix it",
            outline_text="",
            total_pages=3,
            page_attributes_summary="- Page 1: single_column, images",
        )
        assert "Page Characteristics" in msg
        assert "single_column" in msg

    def test_includes_feedback_history(self):
        msg = build_decomposition_user_message(
            feedback="Fix it again",
            outline_text="",
            total_pages=3,
            page_attributes_summary="",
            feedback_history=["First round feedback"],
        )
        assert "Prior Feedback Rounds" in msg
        assert "Round 1" in msg
        assert "First round feedback" in msg

    def test_omits_optional_sections_when_empty(self):
        msg = build_decomposition_user_message(
            feedback="Fix it",
            outline_text="",
            total_pages=1,
            page_attributes_summary="",
        )
        assert "Document Outline" not in msg
        assert "Page Characteristics" not in msg
        assert "Prior Feedback" not in msg


# ---------------------------------------------------------------------------
# build_page_attributes_summary
# ---------------------------------------------------------------------------


class TestBuildPageAttributesSummary:
    def test_basic_formatting(self):
        attrs = {
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            2: PageAttributes(layout=LayoutType.DOUBLE_COLUMN, has_images=True),
        }
        summary = build_page_attributes_summary(attrs)
        assert "Page 1: single_column" in summary
        assert "Page 2: double_column, images" in summary

    def test_all_flags(self):
        attrs = {
            1: PageAttributes(
                layout=LayoutType.PRESENTATION,
                is_academic=True,
                has_images=True,
                has_tables=True,
                has_equations=True,
                is_scanned=True,
            ),
        }
        summary = build_page_attributes_summary(attrs)
        assert "academic" in summary
        assert "images" in summary
        assert "tables" in summary
        assert "equations" in summary
        assert "scanned" in summary

    def test_empty_dict(self):
        assert build_page_attributes_summary({}) == ""


# ---------------------------------------------------------------------------
# build_revision_user_message
# ---------------------------------------------------------------------------


class TestBuildRevisionUserMessage:
    def test_includes_tasks(self):
        tasks = [
            RevisionTask(
                id=1,
                description="Fix the typo on line 5",
                affected_pages=[1],
                category=RevisionTaskCategory.CONTENT,
            ),
        ]
        msg = build_revision_user_message(
            tasks=tasks,
            page_num=1,
            current_markdown="Hello wrold",
            outline_text="",
        )
        assert "Task 1" in msg
        assert "Fix the typo on line 5" in msg

    def test_includes_markdown(self):
        tasks = [
            RevisionTask(id=1, description="Fix", affected_pages=[1]),
        ]
        msg = build_revision_user_message(
            tasks=tasks,
            page_num=1,
            current_markdown="# Title\n\nBody text",
            outline_text="",
        )
        assert "# Title" in msg
        assert "Body text" in msg

    def test_includes_page_attrs(self):
        tasks = [
            RevisionTask(id=1, description="Fix", affected_pages=[1]),
        ]
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_tables=True)
        msg = build_revision_user_message(
            tasks=tasks,
            page_num=1,
            current_markdown="text",
            outline_text="",
            page_attrs=attrs,
        )
        assert "Page Attributes" in msg
        assert "tables" in msg
