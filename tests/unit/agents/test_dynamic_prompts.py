"""Tests for dynamic prompt decorators, section_context fix, and subagent findings."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.agents.dossier import (
    ExecutionSnapshot,
    PipelineDossier,
    PlanningSnapshot,
    SubagentFinding,
    VerificationSnapshot,
)
from src.agents.models import (
    DocumentType,
    OutlineEntry,
)
from src.agents.shared_prompts import (
    derive_section_map,
    format_document_identity,
    format_execution_findings,
    format_section_context,
    format_verification_context,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def populated_dossier() -> PipelineDossier:
    """Dossier with all snapshots populated."""
    return PipelineDossier(
        document_id="doc-test",
        filename="test.pdf",
        planning=PlanningSnapshot(
            document_title="COVID-19 X-Ray Analysis",
            document_type=DocumentType.PAPER,
            total_pages=12,
            outline=[
                OutlineEntry(
                    heading="Introduction",
                    level=1,
                    page_start=1,
                    page_end=3,
                    children=[
                        OutlineEntry(heading="1.1 Background", level=2, page_start=1, page_end=2),
                    ],
                ),
                OutlineEntry(heading="Methods", level=1, page_start=4, page_end=8),
            ],
            dictionary=["COVID-19", "radiograph", "pneumonia"],
            page_summaries={
                1: "Title page with abstract.",
                2: "Introduction to COVID-19 detection.",
                4: "Methods section describing approach.",
            },
            section_map={
                1: "Section 1.1 Background",
                2: "Section 1.1 Background",
                3: "Section Introduction",
                4: "Section Methods",
            },
            figures_planned={1: 0, 2: 1, 4: 2},
            tables_planned={4: 1},
        ),
        execution=ExecutionSnapshot(
            pages_processed=[1, 2, 4],
            subagent_findings={
                1: [
                    SubagentFinding(
                        page=1,
                        task_type="page_artifact",
                        description="Removed page break",
                        confidence=0.9,
                        applied=True,
                    ),
                ],
            },
            edits_applied_count=5,
            tasks_completed=8,
            tasks_failed=1,
        ),
        verification=VerificationSnapshot(
            passed=False,
            pages_passed=[1, 2],
            pages_failed=[4],
            critical_issues=["Missing alt text on page 4 figure 1"],
            warnings=["Minor heading inconsistency"],
        ),
    )


# =============================================================================
# Worker Decorator Tests
# =============================================================================


class TestWorkerDynamicPrompt:
    """Test that worker agent instructions decorator works correctly."""

    def test_returns_empty_when_dossier_none(self):
        """Worker instructions return empty string when dossier is None."""
        from src.agents.worker import WorkerDeps

        _deps = WorkerDeps(
            job=MagicMock(page=1),
            page_image=MagicMock(),
            element_bboxes={},
            page_width=612.0,
            current_markdown="# Test",
            pending_edits=[],
            validated_edits=[],
            dictionary=[],
            dossier=None,
        )
        # Directly test format functions (decorator calls these)
        assert format_document_identity(PipelineDossier(document_id="d", filename="f.pdf")) == ""

    def test_returns_context_when_dossier_populated(self, populated_dossier):
        """Worker instructions return context when dossier is populated."""
        result = format_document_identity(populated_dossier)
        assert "COVID-19 X-Ray Analysis" in result
        assert "paper" in result

        result = format_section_context(populated_dossier, 1)
        assert "1.1 Background" in result


# =============================================================================
# ParagraphAgent Decorator Tests
# =============================================================================


class TestParagraphAgentDynamicPrompt:
    """Test paragraph agent instructions decorator."""

    def test_returns_empty_when_dossier_none(self):
        from src.agents.shared_prompts import format_page_summary

        empty = PipelineDossier(document_id="d", filename="f.pdf")
        assert format_page_summary(empty, 1) == ""

    def test_returns_page_summary(self, populated_dossier):
        from src.agents.shared_prompts import format_page_summary

        result = format_page_summary(populated_dossier, 1)
        assert "Title page with abstract" in result

    def test_section_context_for_page(self, populated_dossier):
        result = format_section_context(populated_dossier, 4)
        assert "Methods" in result


# =============================================================================
# Recovery Decorator Tests
# =============================================================================


class TestRecoveryDynamicPrompt:
    """Test recovery agent instructions decorator — the critical fix."""

    def test_returns_empty_when_dossier_none(self):
        empty = PipelineDossier(document_id="d", filename="f.pdf")
        assert format_verification_context(empty) == ""
        assert format_execution_findings(empty, 1) == ""

    def test_verification_context_populated(self, populated_dossier):
        """Recovery now gets verification context it never had before."""
        result = format_verification_context(populated_dossier)
        assert "FAILED" in result
        assert "4" in result  # page 4 failed
        assert "Missing alt text" in result

    def test_execution_findings_populated(self, populated_dossier):
        """Recovery now gets what subagents already tried."""
        result = format_execution_findings(populated_dossier, 1)
        assert "page_artifact" in result
        assert "Removed page break" in result
        assert "applied" in result

    def test_execution_findings_empty_for_unprocessed_page(self, populated_dossier):
        """No findings for pages that weren't processed."""
        result = format_execution_findings(populated_dossier, 99)
        assert result == ""


# =============================================================================
# Section Context Fix Tests
# =============================================================================


class TestSectionContextFix:
    """Test that section_context is now populated from outline."""

    def test_derive_section_map_basic(self):
        """Section map correctly maps pages to headings."""
        outline = [
            OutlineEntry(heading="Intro", level=1, page_start=1, page_end=3),
            OutlineEntry(heading="Methods", level=1, page_start=4, page_end=6),
        ]
        section_map = derive_section_map(outline)
        assert section_map[1] == "Section Intro"
        assert section_map[4] == "Section Methods"

    def test_convert_chain_state_populates_section_context(self):
        """convert_chain_state_to_page_plans now fills section_context."""
        from src.agents.page_chain import PageChainState

        chain_state = PageChainState()
        chain_state.outline = [
            OutlineEntry(heading="Introduction", level=1, page_start=1, page_end=2),
        ]
        chain_state.page_summaries = {1: "Page one summary", 2: "Page two summary"}

        from src.agents.planner import convert_chain_state_to_page_plans

        plans = convert_chain_state_to_page_plans(chain_state)
        assert plans[1].section_context == "Section Introduction"
        assert plans[2].section_context == "Section Introduction"

    def test_section_context_empty_when_no_outline(self):
        """Section context is empty string when outline is empty."""
        from src.agents.page_chain import PageChainState
        from src.agents.planner import convert_chain_state_to_page_plans

        chain_state = PageChainState()
        chain_state.page_summaries = {1: "Page one summary"}

        plans = convert_chain_state_to_page_plans(chain_state)
        assert plans[1].section_context == ""


# =============================================================================
# Subagent Findings Extraction Tests
# =============================================================================


class TestSubagentFindingsExtraction:
    """Test _extract_subagent_findings preserves findings in JobResult."""

    def test_extract_empty_precomputed(self):
        from src.agents.paragraph_agent import _extract_subagent_findings

        findings = _extract_subagent_findings(page=1, precomputed={}, applied_edits=[])
        assert findings == []

    def test_extract_generic_result(self):
        from src.agents.paragraph_agent import _extract_subagent_findings

        precomputed = {"text_structure:1": MagicMock(spec=[])}
        findings = _extract_subagent_findings(page=1, precomputed=precomputed, applied_edits=[])
        assert len(findings) == 1
        assert findings[0]["page"] == 1
        assert findings[0]["task_type"] == "text_structure"

    def test_extract_with_corrections(self):
        from src.agents.paragraph_agent import _extract_subagent_findings

        # Mock a TextStructureResult-like object (real fields: task_type, before, after, confidence, reasoning)
        correction = MagicMock()
        correction.task_type = "footnote"
        correction.reasoning = "Fixed orphaned footnote"
        correction.confidence = 0.85
        correction.before = "fn:1"

        result = MagicMock()
        result.corrections = [correction]

        precomputed = {"text_structure:1": result}
        findings = _extract_subagent_findings(page=1, precomputed=precomputed, applied_edits=[])
        assert len(findings) == 1
        assert findings[0]["task_type"] == "footnote"
        assert findings[0]["description"] == "Fixed orphaned footnote"
        assert findings[0]["confidence"] == 0.85

    def test_extract_with_typography_fixes(self):
        from src.agents.paragraph_agent import _extract_subagent_findings

        fix = MagicMock()
        fix.description = "Applied bold to keyword"
        fix.confidence = 0.92

        result = MagicMock(spec=[])
        result.fixes = [fix]
        # Ensure 'corrections' is not present
        del result.corrections

        precomputed = {"typography:1": result}
        findings = _extract_subagent_findings(page=1, precomputed=precomputed, applied_edits=[])
        assert len(findings) == 1
        assert findings[0]["task_type"] == "typography"

    def test_job_result_includes_subagent_findings(self):
        """Verify JobResult model accepts subagent_findings field."""
        from src.agents.models import JobResult

        result = JobResult(
            job_id="j1",
            success=True,
            updated_markdown="# test",
            subagent_findings=[
                {"page": 1, "task_type": "page_artifact", "description": "test", "confidence": 0.9, "applied": True}
            ],
        )
        assert len(result.subagent_findings) == 1
        assert result.subagent_findings[0]["task_type"] == "page_artifact"

    def test_job_result_default_empty_findings(self):
        """JobResult defaults to empty subagent_findings."""
        from src.agents.models import JobResult

        result = JobResult(
            job_id="j1",
            success=True,
            updated_markdown="# test",
        )
        assert result.subagent_findings == []
