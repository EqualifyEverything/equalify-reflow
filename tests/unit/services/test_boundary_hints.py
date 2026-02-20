"""Unit tests for boundary hint generation (section context, running headers/footers)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    SectionEntry,
    SectionMap,
    StructureResult,
)


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def structure():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            3: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=1, text="Title", page=1),
            OutlineEntry(level=2, text="Methods", page=1),
            OutlineEntry(level=2, text="Results", page=3),
        ],
        footnotes=[],
    )


class TestBoundaryHintGeneration:
    """Test _generate_boundary_hints method."""

    def test_same_section_hint(self, service, structure):
        """Pages in the same section should get a SAME SECTION hint."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=3,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "## Methods\n\nFirst part of methods.",
                    "2": "Continuing methods discussion.",
                    "3": "## Results\n\nResults here.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[
            SectionEntry(
                index=0,
                heading_text="Methods",
                heading_level=2,
                pages=[1, 2],
                markdown="methods content",
                outline_position=1,
            ),
            SectionEntry(
                index=1,
                heading_text="Results",
                heading_level=2,
                pages=[3],
                markdown="results content",
                outline_position=2,
            ),
        ])

        snippets = service._generate_boundary_hints(result, structure, section_map)

        assert len(snippets) == 2  # boundary 1-2 and 2-3

        # Boundary 1-2: same section
        b12 = snippets[0]
        assert b12["page_before"] == 1
        assert b12["page_after"] == 2
        assert any("SAME SECTION" in h for h in b12["hints"])
        assert any("Methods" in h for h in b12["hints"])

    def test_section_transition_hint(self, service, structure):
        """Boundary where one section ends and another begins."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "## Methods\n\nMethods content.",
                    "2": "## Results\n\nResults content.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[
            SectionEntry(
                index=0,
                heading_text="Methods",
                heading_level=2,
                pages=[1],
                markdown="methods",
                outline_position=1,
            ),
            SectionEntry(
                index=1,
                heading_text="Results",
                heading_level=2,
                pages=[2],
                markdown="results",
                outline_position=2,
            ),
        ])

        snippets = service._generate_boundary_hints(result, structure, section_map)

        assert len(snippets) == 1
        hints = snippets[0]["hints"]
        assert any("SECTION TRANSITION" in h for h in hints)
        assert any("Methods" in h and "Results" in h for h in hints)

    def test_running_header_in_boundary(self, service, structure):
        """Running header at start of page_after should be flagged."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nContent of page 1.",
                    "2": "Title 2\n\nContent of page 2.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])

        snippets = service._generate_boundary_hints(result, structure, section_map)

        hints = snippets[0]["hints"]
        assert any("Running header" in h for h in hints)

    def test_running_footer_in_boundary(self, service, structure):
        """Running footer at end of page_before should be flagged."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nContent.\n\n1",
                    "2": "More content.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])

        snippets = service._generate_boundary_hints(result, structure, section_map)

        hints = snippets[0]["hints"]
        assert any("Page number footer" in h for h in hints)

    def test_single_page_no_boundaries(self, service, structure):
        """A single-page document should produce no boundary snippets."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "combined"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Title\n\nContent.",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        section_map = SectionMap(sections=[])

        snippets = service._generate_boundary_hints(result, structure, section_map)

        assert len(snippets) == 0
