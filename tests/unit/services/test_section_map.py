"""Unit tests for section map construction."""

from __future__ import annotations

import pytest

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PipelineViewerResult,
    SectionMap,
    StructureResult,
)


@pytest.fixture
def service():
    return PipelineViewerService()


class TestSectionMapConstruction:
    """Test the deterministic _build_section_map method."""

    def test_basic_two_sections(self, service):
        """Two headings should produce a preamble + 2 sections."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "Preamble text\n\n# Introduction\n\nIntro content\n\n# Methods\n\nMethods content"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "Preamble text\n\n# Introduction\n\nIntro content",
                    "2": "# Methods\n\nMethods content",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[
                OutlineEntry(level=1, text="Introduction", page=1),
                OutlineEntry(level=1, text="Methods", page=2),
            ],
        )

        section_map = service._build_section_map(result, structure)

        assert len(section_map.sections) == 3  # preamble + 2 sections
        assert section_map.sections[0].heading_text == "(Preamble)"
        assert section_map.sections[0].heading_level == 0
        assert section_map.sections[1].heading_text == "Introduction"
        assert section_map.sections[2].heading_text == "Methods"

    def test_no_preamble(self, service):
        """When document starts with a heading, no preamble section."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Title\n\nContent here"},
            page_images={},
            page_markdowns={"v0": {"1": "# Title\n\nContent here"}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[OutlineEntry(level=1, text="Title", page=1)],
        )

        section_map = service._build_section_map(result, structure)

        assert len(section_map.sections) == 1
        assert section_map.sections[0].heading_text == "Title"

    def test_no_headings(self, service):
        """When no headings exist, entire document is one preamble section."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "Just some text without headings."},
            page_images={},
            page_markdowns={"v0": {"1": "Just some text without headings."}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(outline=[])

        section_map = service._build_section_map(result, structure)

        assert len(section_map.sections) == 1
        assert section_map.sections[0].heading_text == "(Preamble)"
        assert section_map.sections[0].heading_level == 0

    def test_multi_page_section(self, service):
        """A section spanning multiple pages should list all pages."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=3,
            versions={"v0": "# Intro\n\nPage 1 content\n\nPage 2 content\n\n# Methods\n\nPage 3 content"},
            page_images={},
            page_markdowns={
                "v0": {
                    "1": "# Intro\n\nPage 1 content",
                    "2": "Page 2 content",
                    "3": "# Methods\n\nPage 3 content",
                },
            },
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[
                OutlineEntry(level=2, text="Intro", page=1),
                OutlineEntry(level=2, text="Methods", page=3),
            ],
        )

        section_map = service._build_section_map(result, structure)

        # Intro section should span pages 1-2
        intro_section = section_map.sections[0]
        assert intro_section.heading_text == "Intro"
        assert 1 in intro_section.pages
        assert 2 in intro_section.pages

        # Methods section should be page 3
        methods_section = section_map.sections[1]
        assert methods_section.heading_text == "Methods"
        assert 3 in methods_section.pages

    def test_section_indices_are_sequential(self, service):
        """Section indices should be 0-based sequential."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "Preamble\n\n# A\n\nContent A\n\n# B\n\nContent B"},
            page_images={},
            page_markdowns={"v0": {"1": "Preamble\n\n# A\n\nContent A\n\n# B\n\nContent B"}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[
                OutlineEntry(level=1, text="A", page=1),
                OutlineEntry(level=1, text="B", page=1),
            ],
        )

        section_map = service._build_section_map(result, structure)

        indices = [s.index for s in section_map.sections]
        assert indices == list(range(len(section_map.sections)))

    def test_section_markdown_content(self, service):
        """Each section should contain its markdown from heading to next heading."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# First\n\nFirst content\n\n# Second\n\nSecond content"},
            page_images={},
            page_markdowns={"v0": {"1": "# First\n\nFirst content\n\n# Second\n\nSecond content"}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[
                OutlineEntry(level=1, text="First", page=1),
                OutlineEntry(level=1, text="Second", page=1),
            ],
        )

        section_map = service._build_section_map(result, structure)

        assert "# First" in section_map.sections[0].markdown
        assert "First content" in section_map.sections[0].markdown
        assert "# Second" not in section_map.sections[0].markdown

        assert "# Second" in section_map.sections[1].markdown
        assert "Second content" in section_map.sections[1].markdown

    def test_outline_position_tracking(self, service):
        """Sections should track their position in the outline."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# A\n\nContent\n\n# B\n\nContent"},
            page_images={},
            page_markdowns={"v0": {"1": "# A\n\nContent\n\n# B\n\nContent"}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[
                OutlineEntry(level=1, text="A", page=1),
                OutlineEntry(level=1, text="B", page=1),
            ],
        )

        section_map = service._build_section_map(result, structure)

        assert section_map.sections[0].outline_position == 0
        assert section_map.sections[1].outline_position == 1

    def test_preamble_outline_position_is_negative(self, service):
        """Preamble sections should have outline_position=-1."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "Preamble\n\n# Heading\n\nContent"},
            page_images={},
            page_markdowns={"v0": {"1": "Preamble\n\n# Heading\n\nContent"}},
            figures=[],
            steps=[],
            stats={},
        )
        structure = StructureResult(
            outline=[OutlineEntry(level=1, text="Heading", page=1)],
        )

        section_map = service._build_section_map(result, structure)

        preamble = section_map.sections[0]
        assert preamble.heading_text == "(Preamble)"
        assert preamble.outline_position == -1
