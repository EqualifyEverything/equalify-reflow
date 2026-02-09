"""Tests for PipelineDossier models and shared prompt utilities."""

from __future__ import annotations

import json

import pytest
from src.agents.dossier import (
    ExecutionSnapshot,
    PipelineDossier,
    PlanningSnapshot,
    SubagentFinding,
    VerificationSnapshot,
)
from src.agents.models import DocumentType, OutlineEntry
from src.agents.shared_prompts import (
    CONFIDENCE_THRESHOLD_GUIDE,
    derive_section_map,
    format_dictionary_fragment,
    format_document_identity,
    format_execution_findings,
    format_page_summary,
    format_section_context,
    format_verification_context,
)

# =============================================================================
# PipelineDossier Model Tests
# =============================================================================


class TestPipelineDossier:
    """Test PipelineDossier construction and defaults."""

    def test_minimal_construction(self):
        """Dossier can be created with just required fields."""
        dossier = PipelineDossier(
            document_id="doc-123",
            filename="test.pdf",
        )
        assert dossier.document_id == "doc-123"
        assert dossier.filename == "test.pdf"
        assert dossier.current_phase == "init"
        assert dossier.planning is None
        assert dossier.execution is None
        assert dossier.verification is None

    def test_optional_fields_default_none(self):
        """Phase snapshots default to None."""
        dossier = PipelineDossier(document_id="d", filename="f.pdf")
        assert dossier.planning is None
        assert dossier.execution is None
        assert dossier.verification is None

    def test_full_construction(self):
        """Dossier with all snapshots populated."""
        planning = PlanningSnapshot(
            document_title="Test Doc",
            document_type=DocumentType.PAPER,
            total_pages=10,
            page_summaries={1: "Intro page", 2: "Methods"},
            section_map={1: "Section Introduction", 2: "Section Methods"},
        )
        execution = ExecutionSnapshot(
            pages_processed=[1, 2],
            edits_applied_count=5,
            tasks_completed=3,
            tasks_failed=1,
        )
        verification = VerificationSnapshot(
            passed=False,
            pages_passed=[1],
            pages_failed=[2],
            critical_issues=["Missing alt text on page 2"],
        )
        dossier = PipelineDossier(
            document_id="doc-456",
            filename="paper.pdf",
            current_phase="verification",
            planning=planning,
            execution=execution,
            verification=verification,
        )
        assert dossier.planning.document_title == "Test Doc"
        assert dossier.execution.edits_applied_count == 5
        assert dossier.verification.passed is False

    def test_json_serializable(self):
        """Dossier can be serialized to JSON."""
        dossier = PipelineDossier(
            document_id="doc",
            filename="f.pdf",
            planning=PlanningSnapshot(
                document_title="T",
                total_pages=1,
            ),
        )
        data = dossier.model_dump_json()
        assert isinstance(data, str)
        parsed = json.loads(data)
        assert parsed["document_id"] == "doc"

    def test_memory_under_100kb_for_50_page_doc(self):
        """Dossier for a 50-page doc should be < 100KB JSON."""
        page_summaries = {i: f"Summary for page {i} with some details" for i in range(1, 51)}
        section_map = {i: f"Section {i // 5 + 1}.{i % 5}: Topic {i}" for i in range(1, 51)}
        findings = {
            i: [
                SubagentFinding(
                    page=i,
                    task_type="page_artifact",
                    description=f"Found artifact on line {i * 10}",
                    confidence=0.85,
                    applied=True,
                ),
                SubagentFinding(
                    page=i,
                    task_type="typography",
                    description=f"Fixed bold on line {i * 5}",
                    confidence=0.9,
                    applied=True,
                ),
            ]
            for i in range(1, 51)
        }

        dossier = PipelineDossier(
            document_id="doc-50",
            filename="big_doc.pdf",
            planning=PlanningSnapshot(
                document_title="A Long Document Title About Something",
                document_type=DocumentType.REPORT,
                total_pages=50,
                dictionary=[f"term_{i}" for i in range(100)],
                page_summaries=page_summaries,
                section_map=section_map,
                heading_fixes_count=12,
                figures_planned={i: 2 for i in range(1, 51)},
                tables_planned={i: 1 for i in range(1, 51)},
            ),
            execution=ExecutionSnapshot(
                pages_processed=list(range(1, 51)),
                subagent_findings=findings,
                edits_applied_count=150,
                tasks_completed=200,
                tasks_failed=5,
            ),
            verification=VerificationSnapshot(
                passed=True,
                pages_passed=list(range(1, 49)),
                pages_failed=[49, 50],
                critical_issues=["Issue A", "Issue B"],
                warnings=["Warning 1"],
            ),
        )

        json_bytes = len(dossier.model_dump_json().encode("utf-8"))
        assert json_bytes < 100_000, f"Dossier JSON is {json_bytes} bytes, exceeds 100KB"


# =============================================================================
# derive_section_map Tests
# =============================================================================


class TestDeriveSectionMap:
    """Test section map derivation from outline tree."""

    def test_empty_outline(self):
        """Empty outline produces empty section map."""
        assert derive_section_map([]) == {}

    def test_flat_outline(self):
        """Single-level outline maps pages to sections."""
        outline = [
            OutlineEntry(heading="Introduction", level=1, page_start=1, page_end=3),
            OutlineEntry(heading="Methods", level=1, page_start=4, page_end=6),
        ]
        section_map = derive_section_map(outline)
        assert section_map[1] == "Section Introduction"
        assert section_map[3] == "Section Introduction"
        assert section_map[4] == "Section Methods"
        assert section_map[6] == "Section Methods"

    def test_nested_outline_deepest_wins(self):
        """Nested sections: deepest heading wins for a page."""
        outline = [
            OutlineEntry(
                heading="Chapter 1",
                level=1,
                page_start=1,
                page_end=5,
                children=[
                    OutlineEntry(heading="1.1 Background", level=2, page_start=1, page_end=2),
                    OutlineEntry(heading="1.2 Related Work", level=2, page_start=3, page_end=5),
                ],
            ),
        ]
        section_map = derive_section_map(outline)
        # Pages 1-2 should have the deepest heading "1.1 Background"
        assert section_map[1] == "Section 1.1 Background"
        assert section_map[2] == "Section 1.1 Background"
        # Pages 3-5 should have "1.2 Related Work"
        assert section_map[3] == "Section 1.2 Related Work"

    def test_deeply_nested_three_levels(self):
        """Three-level nesting: deepest wins."""
        outline = [
            OutlineEntry(
                heading="Part I",
                level=1,
                page_start=1,
                page_end=10,
                children=[
                    OutlineEntry(
                        heading="Chapter 1",
                        level=2,
                        page_start=1,
                        page_end=5,
                        children=[
                            OutlineEntry(heading="1.1 Intro", level=3, page_start=1, page_end=2),
                        ],
                    ),
                ],
            ),
        ]
        section_map = derive_section_map(outline)
        # Page 1 should have deepest "1.1 Intro"
        assert section_map[1] == "Section 1.1 Intro"
        # Page 3 should have "Chapter 1" (no deeper section)
        assert section_map[3] == "Section Chapter 1"
        # Page 7 should have "Part I" (no deeper section)
        assert section_map[7] == "Section Part I"


# =============================================================================
# Format Function Tests
# =============================================================================


class TestFormatFunctions:
    """Test prompt fragment formatting functions."""

    @pytest.fixture()
    def populated_dossier(self) -> PipelineDossier:
        """Create a dossier with all snapshots populated."""
        return PipelineDossier(
            document_id="doc-test",
            filename="test.pdf",
            planning=PlanningSnapshot(
                document_title="COVID-19 X-Ray Analysis",
                document_type=DocumentType.PAPER,
                total_pages=12,
                dictionary=["COVID-19", "radiograph", "pneumonia", "SARS-CoV-2"],
                page_summaries={
                    1: "Title page with abstract.",
                    2: "Introduction to COVID-19 detection.",
                },
                section_map={
                    1: "Section Abstract",
                    2: "Section 1. Introduction",
                },
                figures_planned={1: 0, 2: 1},
                tables_planned={1: 0, 2: 0},
            ),
            execution=ExecutionSnapshot(
                pages_processed=[1, 2],
                subagent_findings={
                    1: [
                        SubagentFinding(
                            page=1,
                            task_type="page_artifact",
                            description="Removed page break artifact",
                            confidence=0.9,
                            applied=True,
                        ),
                    ],
                },
                edits_applied_count=3,
                tasks_completed=5,
                tasks_failed=0,
            ),
            verification=VerificationSnapshot(
                passed=False,
                pages_passed=[1],
                pages_failed=[2],
                critical_issues=["Missing alt text for figure on page 2"],
                warnings=["Minor heading inconsistency"],
            ),
        )

    @pytest.fixture()
    def empty_dossier(self) -> PipelineDossier:
        """Create a dossier with no snapshots."""
        return PipelineDossier(document_id="empty", filename="empty.pdf")

    def test_format_document_identity_populated(self, populated_dossier):
        result = format_document_identity(populated_dossier)
        assert "COVID-19 X-Ray Analysis" in result
        assert "paper" in result
        assert "12 pages" in result

    def test_format_document_identity_empty(self, empty_dossier):
        assert format_document_identity(empty_dossier) == ""

    def test_format_section_context_found(self, populated_dossier):
        result = format_section_context(populated_dossier, 2)
        assert "Section 1. Introduction" in result

    def test_format_section_context_not_found(self, populated_dossier):
        result = format_section_context(populated_dossier, 99)
        assert result == ""

    def test_format_section_context_no_planning(self, empty_dossier):
        assert format_section_context(empty_dossier, 1) == ""

    def test_format_page_summary_found(self, populated_dossier):
        result = format_page_summary(populated_dossier, 1)
        assert "Title page with abstract" in result

    def test_format_page_summary_not_found(self, populated_dossier):
        assert format_page_summary(populated_dossier, 99) == ""

    def test_format_page_summary_no_planning(self, empty_dossier):
        assert format_page_summary(empty_dossier, 1) == ""

    def test_format_dictionary_fragment(self, populated_dossier):
        result = format_dictionary_fragment(populated_dossier)
        assert "COVID-19" in result
        assert "radiograph" in result

    def test_format_dictionary_fragment_empty(self, empty_dossier):
        assert format_dictionary_fragment(empty_dossier) == ""

    def test_format_dictionary_fragment_max_terms(self, populated_dossier):
        result = format_dictionary_fragment(populated_dossier, max_terms=2)
        assert "COVID-19" in result
        assert "radiograph" in result
        # Only 2 terms should be included
        assert "pneumonia" not in result

    def test_format_verification_context(self, populated_dossier):
        result = format_verification_context(populated_dossier)
        assert "FAILED" in result
        assert "2" in result  # Page 2 failed
        assert "Missing alt text" in result

    def test_format_verification_context_empty(self, empty_dossier):
        assert format_verification_context(empty_dossier) == ""

    def test_format_execution_findings_found(self, populated_dossier):
        result = format_execution_findings(populated_dossier, 1)
        assert "page_artifact" in result
        assert "Removed page break artifact" in result
        assert "applied" in result

    def test_format_execution_findings_not_found(self, populated_dossier):
        assert format_execution_findings(populated_dossier, 99) == ""

    def test_format_execution_findings_no_execution(self, empty_dossier):
        assert format_execution_findings(empty_dossier, 1) == ""


class TestConfidenceThresholdGuide:
    """Test shared constant."""

    def test_confidence_guide_content(self):
        assert ">= 0.8" in CONFIDENCE_THRESHOLD_GUIDE
        assert "0.5-0.8" in CONFIDENCE_THRESHOLD_GUIDE
        assert "< 0.5" in CONFIDENCE_THRESHOLD_GUIDE
