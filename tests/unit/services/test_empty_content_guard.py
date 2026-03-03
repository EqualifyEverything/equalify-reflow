"""Unit tests for the empty-content guard in PipelineViewerService.

When Docling extraction produces zero extractable text (scanned/image-only PDFs),
the pipeline should skip all LLM phases and return early to avoid wasting tokens.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    PipelineViewerResult,
    StepResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """Create a PipelineViewerService instance."""
    return PipelineViewerService()


def _make_result_with_chars(total_chars: int) -> PipelineViewerResult:
    """Build a PipelineViewerResult that looks like _step_docling just ran."""
    result = PipelineViewerResult(
        filename="scanned.pdf",
        total_pages=3,
        versions={"v0": ""},
        page_images={"1": "AAAA", "2": "BBBB", "3": "CCCC"},
        page_markdowns={"v0": {"1": "", "2": "", "3": ""}},
        figures=[],
        steps=[
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=500,
                changes=[],
                metadata={},
            ),
        ],
        stats={
            "total_chars": total_chars,
            "figure_count": 0,
            "is_likely_scanned": total_chars == 0,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _patch_classify():
    """Return patches that stub out classify_pdf and enrich_classification."""
    from unittest.mock import MagicMock

    mock_classification = MagicMock()
    mock_classification.has_errors = False
    mock_classification.warning_messages = []
    mock_classification.error_messages = []
    mock_classification.document_type.value = "course_material"
    mock_classification.findings = []
    mock_classification.elapsed_ms = 10
    mock_classification.metadata.model_dump.return_value = {}

    return (
        patch(
            "src.services.pdf_classifier.classify_pdf",
            return_value=mock_classification,
        ),
        patch("src.services.pdf_classifier.enrich_classification"),
    )


def _patch_docling(total_chars: int):
    """Patch _step_docling to populate the result with the given char count."""

    async def fake_step_docling(self, result, *args, **kwargs):
        result.total_pages = 3
        result.versions["v0"] = "" if total_chars == 0 else "x" * total_chars
        result.page_images = {"1": "AAAA", "2": "BBBB", "3": "CCCC"}
        result.page_markdowns = {"v0": {"1": "", "2": "", "3": ""}}
        result.stats["total_chars"] = total_chars
        result.stats["figure_count"] = 0
        result.stats["is_likely_scanned"] = total_chars == 0
        result.steps.append(
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=500,
                changes=[],
                metadata={},
            )
        )

    return patch.object(PipelineViewerService, "_step_docling", fake_step_docling)


# ---------------------------------------------------------------------------
# Tests — Zero-chars triggers skip
# ---------------------------------------------------------------------------


class TestEmptyContentGuardTriggered:
    """When total_chars == 0, all enabled LLM phases must be skipped."""

    @pytest.mark.asyncio
    async def test_all_phases_skipped_when_zero_chars(self, service):
        """All structure, page-content, and boundary steps are skipped."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
            )

        step_names = [s.name for s in result.steps]
        assert step_names == [
            "docling",
            "structure",
            "heading_reconciliation",
            "heading_levels",
            "page_content",
            "code_blocks",
            "boundaries",
            "cleanup",
        ]

        # All steps after docling should be skipped
        for step in result.steps[1:]:
            assert step.skipped is True
            assert step.elapsed_ms == 0
            assert step.metadata["reason"] == "empty_content"

    @pytest.mark.asyncio
    async def test_only_structure_skipped_when_only_structure_enabled(self, service):
        """Only structure-related steps are skipped when only structure is enabled."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=False,
                enable_boundaries=False,
            )

        step_names = [s.name for s in result.steps]
        assert step_names == [
            "docling",
            "structure",
            "heading_reconciliation",
            "heading_levels",
        ]
        for step in result.steps[1:]:
            assert step.skipped is True

    @pytest.mark.asyncio
    async def test_no_llm_steps_when_no_phases_enabled(self, service):
        """With no phases enabled, only the docling step remains."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=False,
                enable_page_content=False,
                enable_boundaries=False,
            )

        step_names = [s.name for s in result.steps]
        assert step_names == ["docling"]

    @pytest.mark.asyncio
    async def test_warning_appended(self, service):
        """A warning about empty content must appear in the result."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
            )

        assert any("zero extractable text" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Tests — Low chars does NOT trigger skip
# ---------------------------------------------------------------------------


class TestEmptyContentGuardNotTriggered:
    """When total_chars > 0, the guard must not fire — pipeline continues."""

    @pytest.mark.asyncio
    async def test_low_chars_does_not_skip(self, service):
        """A document with 100 chars should proceed to structure analysis."""
        classify_patch, enrich_patch = _patch_classify()

        # Mock _step_structure to verify it was actually called
        mock_structure = AsyncMock(return_value=AsyncMock(outline=[], page_attributes={}, footnotes=[]))

        with (
            classify_patch,
            enrich_patch,
            _patch_docling(100),
            patch.object(PipelineViewerService, "_step_structure", mock_structure),
            patch.object(PipelineViewerService, "_step_heading_reconciliation", AsyncMock(return_value=AsyncMock(outline=[]))),
            patch.object(PipelineViewerService, "_step_heading_levels", AsyncMock()),
        ):
            result = await service.process(
                b"fake-pdf",
                "test.pdf",
                enable_structure=True,
                enable_page_content=False,
                enable_boundaries=False,
            )

        # _step_structure must have been called — the guard did not fire
        mock_structure.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_char_does_not_skip(self, service):
        """Even 1 char of text should not trigger the guard."""
        classify_patch, enrich_patch = _patch_classify()

        mock_structure = AsyncMock(return_value=AsyncMock(outline=[], page_attributes={}, footnotes=[]))

        with (
            classify_patch,
            enrich_patch,
            _patch_docling(1),
            patch.object(PipelineViewerService, "_step_structure", mock_structure),
            patch.object(PipelineViewerService, "_step_heading_reconciliation", AsyncMock(return_value=AsyncMock(outline=[]))),
            patch.object(PipelineViewerService, "_step_heading_levels", AsyncMock()),
        ):
            result = await service.process(
                b"fake-pdf",
                "test.pdf",
                enable_structure=True,
                enable_page_content=False,
                enable_boundaries=False,
            )

        mock_structure.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — Skipped step metadata
# ---------------------------------------------------------------------------


class TestSkippedStepMetadata:
    """Skipped StepResult entries should have the correct shape."""

    @pytest.mark.asyncio
    async def test_skipped_steps_have_correct_fields(self, service):
        """Each skipped step must have version_before/after, zero elapsed, and metadata."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
            )

        for step in result.steps[1:]:
            assert step.skipped is True
            assert step.elapsed_ms == 0
            assert step.version_before == "v0"
            assert step.version_after == "v0"
            assert step.changes == []
            assert step.metadata["reason"] == "empty_content"
            assert step.metadata["total_chars"] == 0
            assert step.error is None
            assert step.input_tokens == 0
            assert step.output_tokens == 0
            assert step.cost_cents == 0.0

    @pytest.mark.asyncio
    async def test_skipped_steps_display_names(self, service):
        """Each skipped step should carry the correct display_name."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
            )

        expected_display = {
            "structure": "Structure Analysis",
            "heading_reconciliation": "Heading Reconciliation",
            "heading_levels": "Heading Levels",
            "page_content": "Page Content Corrections",
            "code_blocks": "Code Block Languages",
            "boundaries": "Cross-Page Fixes",
            "cleanup": "Final Cleanup",
        }

        for step in result.steps[1:]:
            assert step.display_name == expected_display[step.name], (
                f"Step '{step.name}' has wrong display_name: {step.display_name}"
            )


# ---------------------------------------------------------------------------
# Tests — Docling step and classification preserved
# ---------------------------------------------------------------------------


class TestResultPreservation:
    """The guard must not discard the docling step or classification data."""

    @pytest.mark.asyncio
    async def test_docling_step_preserved(self, service):
        """The first step should always be the docling extraction."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
                enable_page_content=True,
                enable_boundaries=True,
            )

        docling_step = result.steps[0]
        assert docling_step.name == "docling"
        assert docling_step.display_name == "Docling Extraction"
        assert docling_step.skipped is False
        assert docling_step.elapsed_ms > 0

    @pytest.mark.asyncio
    async def test_classification_stats_preserved(self, service):
        """Classification stats should be present even when guard fires."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
                enable_structure=True,
            )

        assert "classification" in result.stats
        assert result.stats["classification"]["document_type"] == "course_material"

    @pytest.mark.asyncio
    async def test_page_images_preserved(self, service):
        """Page images should still be available (useful for viewer display)."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
            )

        assert len(result.page_images) == 3

    @pytest.mark.asyncio
    async def test_total_pages_preserved(self, service):
        """Total page count should be set even for empty documents."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
            )

        assert result.total_pages == 3

    @pytest.mark.asyncio
    async def test_stats_total_chars_zero(self, service):
        """Stats should reflect the zero char count."""
        classify_patch, enrich_patch = _patch_classify()
        with classify_patch, enrich_patch, _patch_docling(0):
            result = await service.process(
                b"fake-pdf",
                "scanned.pdf",
            )

        assert result.stats["total_chars"] == 0
