"""Unit tests for AssemblyService.

Tests the Phase 4 (Assemble) service that combines all agent outputs
into the final ProcessingResult.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from src.services.assembly_service import AssemblyService
from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.review_checklist import ReviewItem, ReviewOption

if TYPE_CHECKING:
    from src.services.structure_loop import StructureTrace
    from src.shared.models.remediation import DocumentManifest


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def assembly_service() -> AssemblyService:
    """Create AssemblyService instance."""
    return AssemblyService()


@pytest.fixture
def mock_manifest() -> DocumentManifest:
    """Create a mock DocumentManifest."""
    manifest = MagicMock()
    manifest.document_type = "research_paper"
    manifest.total_pages = 5
    manifest.required_agents = ["figures", "typography"]
    manifest.analysis_confidence = 0.92
    manifest.summary = MagicMock()
    manifest.summary.key_entities = ["Entity1", "Entity2"]
    return manifest


@pytest.fixture
def mock_structure_trace() -> StructureTrace:
    """Create a mock StructureTrace."""
    trace = MagicMock()
    trace.iterations = 2
    trace.lint_issues_found = 5
    trace.lint_issues_fixed = 4
    trace.ocr_suggestions_processed = 2
    trace.corrections = [MagicMock(), MagicMock()]
    trace.final_lint_clean = True
    trace.time_seconds = 3.5
    trace.cost_cents = 1.2
    return trace


@pytest.fixture
def sample_observation() -> Observation:
    """Create a sample observation."""
    return Observation(
        id="obs-123",
        job_id="job-456",
        agent="typography",
        visual_description="Text shows 'Exxon' in document",
        markup_description="Likely OCR error for 'Enzo'",
        location=ObservationLocation(
            location_type="region",
            value="paragraph 3",
            page_num=2,
        ),
        confidence=0.95,
        severity="minor",
        category="ocr",
        status="open",
    )


@pytest.fixture
def sample_auto_correction(sample_observation: Observation) -> AutoCorrection:
    """Create a sample auto correction."""
    return AutoCorrection(
        id="ac-789",
        observation_id=sample_observation.id,
        search="Exxon",
        replace="Enzo",
        justification="OCR error - document discusses Enzo project",
        confidence=0.98,
        agent="typography",
        page_num=2,
    )


@pytest.fixture
def sample_review_item(sample_observation: Observation) -> ReviewItem:
    """Create a sample review item."""
    return ReviewItem(
        id="ri-101",
        observation_id=sample_observation.id,
        agent="typography",
        question="Is 'Exxon' a typo for 'Enzo'?",
        options=[
            ReviewOption(
                label="Yes, replace with 'Enzo'",
                action="replace",
                replacement_text="Enzo",
                is_recommended=True,
            ),
            ReviewOption(
                label="No, keep 'Exxon'",
                action="keep",
            ),
        ],
        search_text="Exxon",
        context="Both Exxon and yt are developed...",
        page_num=2,
        agent_recommendation="Likely OCR error",
        agent_confidence=0.75,
    )


@pytest.fixture
def sample_agent_result(
    sample_observation: Observation,
    sample_auto_correction: AutoCorrection,
) -> AgentResult:
    """Create a sample agent result with corrections."""
    return AgentResult(
        agent_name="typography",
        observations=[sample_observation],
        auto_corrections=[sample_auto_correction],
        review_items=[],
        reasoning_summary="Found 1 OCR error and auto-corrected it",
        confidence=0.95,
        cost_cents=2.5,
        time_seconds=5.0,
        iterations=1,
    )


@pytest.fixture
def sample_agent_result_with_review(
    sample_review_item: ReviewItem,
) -> AgentResult:
    """Create a sample agent result with review items."""
    obs = Observation(
        id="obs-review",
        job_id="job-456",
        agent="figures",
        visual_description="Image shows a flowchart",
        markup_description="Alt text is generic",
        location=ObservationLocation(
            location_type="element",
            value="img[src='fig1.png']",
            page_num=3,
        ),
        confidence=0.7,
        severity="major",
        category="alt_text",
        status="open",
    )

    review_item = ReviewItem(
        id="ri-fig",
        observation_id=obs.id,
        agent="figures",
        question="What should the alt text be?",
        options=[
            ReviewOption(
                label="Use suggested alt text",
                action="replace",
                replacement_text="Flowchart showing 5 steps of registration process",
                is_recommended=True,
            ),
            ReviewOption(
                label="Keep current alt text",
                action="keep",
            ),
        ],
        search_text="![](fig1.png)",
        context="The registration process...",
        page_num=3,
        agent_recommendation="Alt text should describe flowchart",
        agent_confidence=0.7,
    )

    return AgentResult(
        agent_name="figures",
        observations=[obs],
        auto_corrections=[],
        review_items=[review_item],
        reasoning_summary="Found 1 image needing alt text review",
        confidence=0.7,
        cost_cents=5.0,
        time_seconds=10.0,
        iterations=1,
    )


# =============================================================================
# Test: _apply_corrections
# =============================================================================


class TestApplyCorrections:
    """Tests for _apply_corrections method."""

    def test_applies_single_correction(
        self,
        assembly_service: AssemblyService,
        sample_agent_result: AgentResult,
    ) -> None:
        """Test applying a single correction."""
        markdown = "Both Exxon and yt are developed using Python."

        result_md, applied = assembly_service._apply_corrections(
            markdown, [sample_agent_result]
        )

        assert "Enzo" in result_md
        assert "Exxon" not in result_md
        assert len(applied) == 1
        assert applied[0].applied is True
        assert applied[0].applied_at is not None

    def test_closes_observation_on_correction(
        self,
        assembly_service: AssemblyService,
        sample_agent_result: AgentResult,
    ) -> None:
        """Test that observation is closed when correction is applied."""
        markdown = "Both Exxon and yt are developed using Python."

        # Verify observation starts open
        obs = sample_agent_result.observations[0]
        assert obs.status == "open"

        assembly_service._apply_corrections(markdown, [sample_agent_result])

        # Observation should now be closed
        assert obs.status == "closed"
        assert obs.resolution == "fixed"

    def test_first_occurrence_only(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test that only first occurrence is replaced."""
        obs = Observation(
            id="obs-1",
            job_id="job-1",
            agent="typography",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(
                location_type="region",
                value="test",
                page_num=1,
            ),
        )
        correction = AutoCorrection(
            observation_id=obs.id,
            search="test",
            replace="TEST",
            justification="Uppercase first occurrence",
            confidence=0.99,
            agent="typography",
        )
        result = AgentResult(
            agent_name="typography",
            observations=[obs],
            auto_corrections=[correction],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )

        markdown = "test test test"
        result_md, applied = assembly_service._apply_corrections(
            markdown, [result]
        )

        # Only first "test" should be replaced
        assert result_md == "TEST test test"
        assert len(applied) == 1

    def test_correction_not_found(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test handling when search string is not found."""
        obs = Observation(
            id="obs-1",
            job_id="job-1",
            agent="typography",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(
                location_type="region",
                value="test",
                page_num=1,
            ),
        )
        correction = AutoCorrection(
            observation_id=obs.id,
            search="nonexistent",
            replace="replacement",
            justification="Test",
            confidence=0.99,
            agent="typography",
        )
        result = AgentResult(
            agent_name="typography",
            observations=[obs],
            auto_corrections=[correction],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )

        markdown = "This text does not contain the search string."
        result_md, applied = assembly_service._apply_corrections(
            markdown, [result]
        )

        # Markdown unchanged, no corrections applied
        assert result_md == markdown
        assert len(applied) == 0
        assert correction.applied is False

    def test_multiple_agent_corrections(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test corrections from multiple agents."""
        obs1 = Observation(
            id="obs-1",
            job_id="job-1",
            agent="typography",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(
                location_type="region",
                value="test",
                page_num=1,
            ),
        )
        obs2 = Observation(
            id="obs-2",
            job_id="job-1",
            agent="figures",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(
                location_type="region",
                value="test",
                page_num=2,
            ),
        )

        result1 = AgentResult(
            agent_name="typography",
            observations=[obs1],
            auto_corrections=[
                AutoCorrection(
                    observation_id=obs1.id,
                    search="typo1",
                    replace="fixed1",
                    justification="Fix typo",
                    confidence=0.99,
                    agent="typography",
                )
            ],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )
        result2 = AgentResult(
            agent_name="figures",
            observations=[obs2],
            auto_corrections=[
                AutoCorrection(
                    observation_id=obs2.id,
                    search="typo2",
                    replace="fixed2",
                    justification="Fix typo",
                    confidence=0.99,
                    agent="figures",
                )
            ],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )

        markdown = "This has typo1 and typo2 in it."
        result_md, applied = assembly_service._apply_corrections(
            markdown, [result1, result2]
        )

        assert "fixed1" in result_md
        assert "fixed2" in result_md
        assert "typo1" not in result_md
        assert "typo2" not in result_md
        assert len(applied) == 2


# =============================================================================
# Test: _validate_final
# =============================================================================


class TestValidateFinal:
    """Tests for _validate_final method."""

    def test_clean_markdown(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test validation passes for clean markdown."""
        markdown = """# Document Title

This is clean markdown with no issues.

## Section 1

Content here.
"""
        issues = assembly_service._validate_final(markdown)
        assert len(issues) == 0

    def test_detects_unfilled_image_placeholders(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test detection of unfilled image placeholders."""
        markdown = """# Document

![TODO: Add alt text](image1.png)

Some content.

![TODO: Describe chart](chart.png)
"""
        issues = assembly_service._validate_final(markdown)
        assert len(issues) == 1
        assert "2 unfilled image placeholders" in issues[0]

    def test_detects_mismatched_table_markers(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test detection of mismatched table markers."""
        markdown = """# Document

<!-- TABLE:p1:t1 -->
| Header |
|--------|
| Data |
<!-- /TABLE:p1:t1 -->

<!-- TABLE:p2:t1 -->
| Another |
|---------|
| Missing close marker |
"""
        issues = assembly_service._validate_final(markdown)
        assert len(issues) == 1
        assert "Mismatched table markers" in issues[0]

    def test_detects_generic_placeholders(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test detection of unfilled generic placeholders."""
        markdown = """# Document

Content with {{placeholder1}} and {{placeholder2}} unfilled.
"""
        issues = assembly_service._validate_final(markdown)
        assert len(issues) == 1
        assert "2 unfilled placeholders" in issues[0]


# =============================================================================
# Test: _compute_confidence
# =============================================================================


class TestComputeConfidence:
    """Tests for _compute_confidence method."""

    def test_high_confidence_clean_document(
        self,
        assembly_service: AssemblyService,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test high confidence for clean document."""
        mock_structure_trace.final_lint_clean = True
        mock_structure_trace.iterations = 1

        agent_results = [
            AgentResult(
                agent_name="typography",
                observations=[],
                auto_corrections=[],
                review_items=[],
                reasoning_summary="No issues found",
                confidence=0.95,
            )
        ]

        confidence = assembly_service._compute_confidence(
            extraction_confidence=0.95,
            structure_trace=mock_structure_trace,
            agent_results=agent_results,
            review_items=[],
        )

        # High confidence expected (close to 1.0)
        assert confidence >= 0.9

    def test_lower_confidence_with_review_items(
        self,
        assembly_service: AssemblyService,
        mock_structure_trace: StructureTrace,
        sample_review_item: ReviewItem,
    ) -> None:
        """Test confidence is reduced with review items."""
        mock_structure_trace.final_lint_clean = True

        agent_results = [
            AgentResult(
                agent_name="typography",
                observations=[],
                auto_corrections=[],
                review_items=[],
                reasoning_summary="Test",
                confidence=0.9,
            )
        ]

        # With no review items
        conf_no_review = assembly_service._compute_confidence(
            extraction_confidence=0.9,
            structure_trace=mock_structure_trace,
            agent_results=agent_results,
            review_items=[],
        )

        # With multiple review items (each item reduces by 5%, 4 items = 20% reduction)
        # This ensures the difference is significant enough to survive rounding
        many_review_items = [sample_review_item] * 4
        conf_with_review = assembly_service._compute_confidence(
            extraction_confidence=0.9,
            structure_trace=mock_structure_trace,
            agent_results=agent_results,
            review_items=many_review_items,
        )

        # Confidence should be lower with review items
        assert conf_with_review < conf_no_review

    def test_lower_confidence_with_lint_issues(
        self,
        assembly_service: AssemblyService,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test confidence is reduced when lint is not clean."""
        agent_results: list[AgentResult] = []

        # Clean lint
        mock_structure_trace.final_lint_clean = True
        mock_structure_trace.iterations = 1
        conf_clean = assembly_service._compute_confidence(
            extraction_confidence=0.9,
            structure_trace=mock_structure_trace,
            agent_results=agent_results,
            review_items=[],
        )

        # Not clean lint
        mock_structure_trace.final_lint_clean = False
        conf_dirty = assembly_service._compute_confidence(
            extraction_confidence=0.9,
            structure_trace=mock_structure_trace,
            agent_results=agent_results,
            review_items=[],
        )

        assert conf_dirty < conf_clean

    def test_confidence_bounded_zero_to_one(
        self,
        assembly_service: AssemblyService,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test confidence is always between 0 and 1."""
        # Create many review items to test lower bound
        review_items = [
            ReviewItem(
                id=f"ri-{i}",
                observation_id=f"obs-{i}",
                agent="test",
                question="Test?",
                options=[
                    ReviewOption(label="Yes", action="replace"),
                    ReviewOption(label="No", action="keep"),
                ],
                search_text="test",
                context="test",
                page_num=1,
                agent_recommendation="test",
                agent_confidence=0.5,
            )
            for i in range(20)  # Many review items
        ]

        confidence = assembly_service._compute_confidence(
            extraction_confidence=0.5,
            structure_trace=mock_structure_trace,
            agent_results=[],
            review_items=review_items,
        )

        assert 0.0 <= confidence <= 1.0


# =============================================================================
# Test: _build_trace
# =============================================================================


class TestBuildTrace:
    """Tests for _build_trace method."""

    def test_builds_complete_trace(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
        sample_agent_result: AgentResult,
    ) -> None:
        """Test building a complete processing trace."""
        trace = assembly_service._build_trace(
            manifest=mock_manifest,
            extraction_confidence=0.92,
            extraction_time=45.0,
            extraction_cost=8.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[sample_agent_result],
            applied_corrections=[],
        )

        # Check analysis summary
        assert trace.analysis.document_type == "research_paper"
        assert trace.analysis.total_pages == 5
        assert trace.analysis.confidence == 0.92

        # Check extraction summary
        assert trace.extraction.confidence == 0.92
        assert trace.extraction.time_seconds == 45.0
        assert trace.extraction.cost_cents == 8.0

        # Check structure summary
        assert trace.structure.iterations == 2
        assert trace.structure.lint_issues_found == 5
        assert trace.structure.final_lint_clean is True

        # Check agent traces
        assert len(trace.agents) == 1
        assert trace.agents[0].agent_name == "typography"

        # Check aggregates
        assert trace.total_observations == 1

    def test_aggregates_costs(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test that costs are properly aggregated."""
        agent1 = AgentResult(
            agent_name="typography",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
            cost_cents=5.0,
            time_seconds=10.0,
        )
        agent2 = AgentResult(
            agent_name="figures",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
            cost_cents=10.0,
            time_seconds=20.0,
        )

        trace = assembly_service._build_trace(
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=30.0,
            extraction_cost=15.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[agent1, agent2],
            applied_corrections=[],
        )

        # Total cost: extraction (15) + structure (1.2) + agent1 (5) + agent2 (10)
        expected_cost = 15.0 + 1.2 + 5.0 + 10.0
        assert trace.total_cost_cents == pytest.approx(expected_cost)

        # Total time: extraction (30) + structure (3.5) + agent1 (10) + agent2 (20)
        expected_time = 30.0 + 3.5 + 10.0 + 20.0
        assert trace.total_time_seconds == pytest.approx(expected_time)


# =============================================================================
# Test: assemble (full integration)
# =============================================================================


class TestAssemble:
    """Integration tests for full assemble method."""

    def test_assemble_clean_document(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test assembling a clean document with no review items."""
        agent_result = AgentResult(
            agent_name="typography",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="No issues found",
            confidence=0.95,
        )

        result = assembly_service.assemble(
            job_id="job-123",
            markdown="# Clean Document\n\nNo issues here.",
            manifest=mock_manifest,
            extraction_confidence=0.95,
            extraction_time=30.0,
            extraction_cost=10.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[agent_result],
        )

        assert result.job_id == "job-123"
        assert result.status == "completed"
        assert result.confidence >= 0.9
        assert result.review_checklist.total_items == 0

    def test_assemble_with_corrections(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
        sample_agent_result: AgentResult,
    ) -> None:
        """Test assembling document with auto-corrections."""
        markdown = "Both Exxon and yt are developed using Python."

        result = assembly_service.assemble(
            job_id="job-456",
            markdown=markdown,
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=25.0,
            extraction_cost=8.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[sample_agent_result],
        )

        # Correction should be applied
        assert "Enzo" in result.markdown
        assert "Exxon" not in result.markdown

        # Trace should show correction applied
        assert result.processing_trace.auto_corrections_applied == 1

    def test_assemble_with_review_items(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
        sample_agent_result_with_review: AgentResult,
    ) -> None:
        """Test assembling document that needs review."""
        result = assembly_service.assemble(
            job_id="job-789",
            markdown="# Document\n\n![](fig1.png)",
            manifest=mock_manifest,
            extraction_confidence=0.85,
            extraction_time=20.0,
            extraction_cost=5.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[sample_agent_result_with_review],
        )

        assert result.status == "needs_review"
        assert result.review_checklist.total_items == 1
        assert result.processing_trace.review_items_generated == 1

    def test_assemble_with_validation_issues(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test assembling document with validation issues."""
        markdown = """# Document

![TODO: Add alt text](image.png)

Content here.
"""
        agent_result = AgentResult(
            agent_name="figures",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Placeholder not filled",
            confidence=0.8,
        )

        result = assembly_service.assemble(
            job_id="job-validation",
            markdown=markdown,
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=15.0,
            extraction_cost=3.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[agent_result],
        )

        # Should need review due to validation issues
        assert result.status == "needs_review"

    def test_assemble_multiple_agents(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test assembling with multiple agent results."""
        obs1 = Observation(
            id="obs-1",
            job_id="job-multi",
            agent="typography",
            visual_description="Test",
            markup_description="Test",
            location=ObservationLocation(
                location_type="region",
                value="Test",
                page_num=1,
            ),
        )
        obs2 = Observation(
            id="obs-2",
            job_id="job-multi",
            agent="figures",
            visual_description="Test",
            markup_description="Test",
            location=ObservationLocation(
                location_type="region",
                value="Test",
                page_num=2,
            ),
        )

        typography_result = AgentResult(
            agent_name="typography",
            observations=[obs1],
            auto_corrections=[
                AutoCorrection(
                    observation_id=obs1.id,
                    search="tpyo",
                    replace="typo",
                    justification="Fix typo",
                    confidence=0.99,
                    agent="typography",
                )
            ],
            review_items=[],
            reasoning_summary="Fixed 1 typo",
            confidence=0.95,
            cost_cents=2.0,
            time_seconds=5.0,
        )

        figures_result = AgentResult(
            agent_name="figures",
            observations=[obs2],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="All images OK",
            confidence=0.9,
            cost_cents=5.0,
            time_seconds=10.0,
        )

        result = assembly_service.assemble(
            job_id="job-multi",
            markdown="This has a tpyo in it.",
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=20.0,
            extraction_cost=5.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[typography_result, figures_result],
        )

        # Typo should be fixed
        assert "typo" in result.markdown
        assert "tpyo" not in result.markdown

        # Both agents in trace
        assert len(result.processing_trace.agents) == 2

        # Total observations from both agents
        assert result.processing_trace.total_observations == 2


# =============================================================================
# Test: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_agent_results(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test with no agent results."""
        result = assembly_service.assemble(
            job_id="job-empty",
            markdown="# Simple Document",
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=10.0,
            extraction_cost=2.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[],
        )

        assert result.status == "completed"
        assert result.review_checklist.total_items == 0
        assert len(result.processing_trace.agents) == 0

    def test_unknown_agent_name_skipped(
        self,
        assembly_service: AssemblyService,
        mock_manifest: DocumentManifest,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test that unknown agent names are skipped in trace."""
        result_unknown = AgentResult(
            agent_name="unknown_agent",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )
        result_known = AgentResult(
            agent_name="typography",
            observations=[],
            auto_corrections=[],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )

        result = assembly_service.assemble(
            job_id="job-unknown",
            markdown="# Document",
            manifest=mock_manifest,
            extraction_confidence=0.9,
            extraction_time=10.0,
            extraction_cost=2.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[result_unknown, result_known],
        )

        # Only known agent should be in trace
        assert len(result.processing_trace.agents) == 1
        assert result.processing_trace.agents[0].agent_name == "typography"

    def test_manifest_without_summary(
        self,
        assembly_service: AssemblyService,
        mock_structure_trace: StructureTrace,
    ) -> None:
        """Test with manifest that has no summary."""
        manifest = MagicMock()
        manifest.document_type = "unknown"
        manifest.total_pages = 1
        manifest.required_agents = []
        manifest.analysis_confidence = 0.5
        manifest.summary = None  # No summary

        result = assembly_service.assemble(
            job_id="job-no-summary",
            markdown="# Document",
            manifest=manifest,
            extraction_confidence=0.8,
            extraction_time=5.0,
            extraction_cost=1.0,
            extraction_iterations=1,
            structure_trace=mock_structure_trace,
            agent_results=[],
        )

        # Should still work, key_entities will be empty
        assert result.processing_trace.analysis.key_entities == []

    def test_observation_already_closed(
        self,
        assembly_service: AssemblyService,
    ) -> None:
        """Test that already-closed observations aren't closed again."""
        obs = Observation(
            id="obs-closed",
            job_id="job-1",
            agent="typography",
            visual_description="test",
            markup_description="test",
            location=ObservationLocation(
                location_type="region",
                value="test",
                page_num=1,
            ),
            status="closed",
            resolution="fixed",
        )
        correction = AutoCorrection(
            observation_id=obs.id,
            search="old",
            replace="new",
            justification="Test",
            confidence=0.99,
            agent="typography",
        )
        result = AgentResult(
            agent_name="typography",
            observations=[obs],
            auto_corrections=[correction],
            review_items=[],
            reasoning_summary="Test",
            confidence=0.9,
        )

        markdown = "This is old text."
        result_md, applied = assembly_service._apply_corrections(
            markdown, [result]
        )

        # Correction should still be applied
        assert "new" in result_md
        assert len(applied) == 1

        # Observation should remain closed (not raise error)
        assert obs.status == "closed"
        assert obs.resolution == "fixed"
