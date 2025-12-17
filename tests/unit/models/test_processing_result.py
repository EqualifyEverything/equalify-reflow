"""Tests for ProcessingResult, ProcessingTrace, and phase summary models."""

import pytest
from datetime import datetime, UTC

from src.shared.models.processing_result import (
    AnalysisSummary,
    ExtractionSummary,
    ProcessingResult,
    ProcessingTrace,
    StructureSummary,
)
from src.shared.models.agent_trace import AgentTrace
from src.shared.models.review_checklist import ReviewChecklist


class TestAnalysisSummary:
    """Tests for AnalysisSummary model."""

    def test_create_analysis_summary(self):
        """Test creating an analysis summary."""
        summary = AnalysisSummary(
            document_type="research_paper",
            total_pages=9,
            key_entities=["yt", "Enzo"],
            required_agents=["figures", "tables"],
            confidence=0.95,
            time_seconds=12.5,
            cost_cents=5.8,
        )

        assert summary.document_type == "research_paper"
        assert summary.total_pages == 9
        assert len(summary.key_entities) == 2
        assert summary.confidence == 0.95


class TestExtractionSummary:
    """Tests for ExtractionSummary model."""

    def test_create_extraction_summary(self):
        """Test creating an extraction summary."""
        summary = ExtractionSummary(
            confidence=0.92,
            pages_extracted=9,
            correction_iterations=1,
            time_seconds=45.2,
            cost_cents=8.4,
        )

        assert summary.confidence == 0.92
        assert summary.pages_extracted == 9
        assert summary.correction_iterations == 1


class TestStructureSummary:
    """Tests for StructureSummary model."""

    def test_create_structure_summary(self):
        """Test creating a structure summary."""
        summary = StructureSummary(
            iterations=2,
            lint_issues_found=5,
            lint_issues_fixed=5,
            ocr_suggestions_processed=3,
            corrections_applied=2,
            final_lint_clean=True,
            time_seconds=18.3,
            cost_cents=4.2,
        )

        assert summary.iterations == 2
        assert summary.lint_issues_found == 5
        assert summary.final_lint_clean is True


class TestProcessingTrace:
    """Tests for ProcessingTrace model."""

    def _create_analysis_summary(self) -> AnalysisSummary:
        return AnalysisSummary(
            document_type="research_paper",
            total_pages=9,
            confidence=0.95,
            time_seconds=12.5,
            cost_cents=5.8,
        )

    def _create_extraction_summary(self) -> ExtractionSummary:
        return ExtractionSummary(
            confidence=0.92,
            pages_extracted=9,
            time_seconds=45.2,
            cost_cents=8.4,
        )

    def _create_structure_summary(self) -> StructureSummary:
        return StructureSummary(
            iterations=2,
            final_lint_clean=True,
            time_seconds=18.3,
            cost_cents=4.2,
        )

    def test_create_processing_trace(self):
        """Test creating a processing trace."""
        trace = ProcessingTrace(
            analysis=self._create_analysis_summary(),
            extraction=self._create_extraction_summary(),
            structure=self._create_structure_summary(),
            agents=[],
            total_observations=8,
            auto_corrections_applied=5,
            review_items_generated=3,
            total_cost_cents=42.5,
            total_time_seconds=120.0,
            total_tokens=45000,
        )

        assert trace.total_observations == 8
        assert trace.auto_corrections_applied == 5
        assert trace.total_cost_cents == 42.5

    def test_with_agent_traces(self):
        """Test processing trace with agent traces."""
        agent_trace = AgentTrace(
            agent_name="figures",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Processed 3 images",
            confidence=0.88,
            cost_cents=12.5,
            time_seconds=25.0,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )

        trace = ProcessingTrace(
            analysis=self._create_analysis_summary(),
            extraction=self._create_extraction_summary(),
            structure=self._create_structure_summary(),
            agents=[agent_trace],
        )

        assert len(trace.agents) == 1
        assert trace.agents[0].agent_name == "figures"


class TestProcessingResult:
    """Tests for ProcessingResult model."""

    def _create_trace(self) -> ProcessingTrace:
        return ProcessingTrace(
            analysis=AnalysisSummary(
                document_type="research_paper",
                total_pages=9,
                confidence=0.95,
                time_seconds=12.5,
                cost_cents=5.8,
            ),
            extraction=ExtractionSummary(
                confidence=0.92,
                pages_extracted=9,
                time_seconds=45.2,
                cost_cents=8.4,
            ),
            structure=StructureSummary(
                iterations=2,
                final_lint_clean=True,
                time_seconds=18.3,
                cost_cents=4.2,
            ),
        )

    def _create_checklist(self) -> ReviewChecklist:
        return ReviewChecklist(
            items=[],
            summary="No items need review",
        )

    def test_create_completed_result(self):
        """Test creating a completed processing result."""
        result = ProcessingResult(
            job_id="job-123",
            status="completed",
            markdown="# Document\n\nContent...",
            confidence=0.95,
            processing_trace=self._create_trace(),
            review_checklist=self._create_checklist(),
            processing_time_seconds=120.0,
        )

        assert result.job_id == "job-123"
        assert result.status == "completed"
        assert result.confidence == 0.95
        assert result.created_at is not None

    def test_create_needs_review_result(self):
        """Test creating a needs_review processing result."""
        result = ProcessingResult(
            job_id="job-123",
            status="needs_review",
            markdown="# Document\n\nContent...",
            confidence=0.75,
            processing_trace=self._create_trace(),
            review_checklist=self._create_checklist(),
            processing_time_seconds=120.0,
        )

        assert result.status == "needs_review"

    def test_create_failed_result(self):
        """Test creating a failed processing result."""
        result = ProcessingResult(
            job_id="job-123",
            status="failed",
            markdown="",
            confidence=0.0,
            processing_trace=self._create_trace(),
            review_checklist=self._create_checklist(),
            processing_time_seconds=30.0,
        )

        assert result.status == "failed"

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        result = ProcessingResult(
            job_id="job-123",
            status="completed",
            markdown="# Test",
            confidence=0.9,
            processing_trace=self._create_trace(),
            review_checklist=self._create_checklist(),
            processing_time_seconds=100.0,
        )

        json_str = result.model_dump_json()
        restored = ProcessingResult.model_validate_json(json_str)

        assert restored.job_id == result.job_id
        assert restored.status == result.status
        assert restored.confidence == result.confidence
