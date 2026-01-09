"""Tests for PageBoundaryMap model."""

import pytest
from src.agents.models import PageBoundary, PageBoundaryMap


@pytest.mark.unit
class TestPageBoundary:
    """Tests for PageBoundary model."""

    def test_creation(self):
        """Test creating a PageBoundary."""
        pb = PageBoundary(page_num=1, start_line=1, end_line=10)
        assert pb.page_num == 1
        assert pb.start_line == 1
        assert pb.end_line == 10

    def test_optional_char_offsets(self):
        """Test PageBoundary with character offsets."""
        pb = PageBoundary(
            page_num=2,
            start_line=11,
            end_line=20,
            start_char=500,
            end_char=1000,
        )
        assert pb.start_char == 500
        assert pb.end_char == 1000


@pytest.mark.unit
class TestPageBoundaryMap:
    """Tests for PageBoundaryMap model."""

    @pytest.fixture
    def sample_map(self):
        """Create a sample PageBoundaryMap for testing."""
        return PageBoundaryMap(
            document_id="test-doc",
            boundaries=[
                PageBoundary(page_num=1, start_line=1, end_line=15),
                PageBoundary(page_num=2, start_line=16, end_line=30),
                PageBoundary(page_num=3, start_line=31, end_line=45),
            ],
            total_lines=45,
        )

    def test_get_page_for_line_first_page(self, sample_map):
        """Test getting page for line in first page."""
        assert sample_map.get_page_for_line(1) == 1
        assert sample_map.get_page_for_line(10) == 1
        assert sample_map.get_page_for_line(15) == 1

    def test_get_page_for_line_middle_page(self, sample_map):
        """Test getting page for line in middle page."""
        assert sample_map.get_page_for_line(16) == 2
        assert sample_map.get_page_for_line(20) == 2
        assert sample_map.get_page_for_line(30) == 2

    def test_get_page_for_line_last_page(self, sample_map):
        """Test getting page for line in last page."""
        assert sample_map.get_page_for_line(31) == 3
        assert sample_map.get_page_for_line(40) == 3
        assert sample_map.get_page_for_line(45) == 3

    def test_get_page_for_line_out_of_bounds(self, sample_map):
        """Test getting page for line outside document."""
        assert sample_map.get_page_for_line(0) is None
        assert sample_map.get_page_for_line(46) is None
        assert sample_map.get_page_for_line(-1) is None
        assert sample_map.get_page_for_line(100) is None

    def test_get_pages_for_range_single_page(self, sample_map):
        """Test getting pages for range within single page."""
        assert sample_map.get_pages_for_range(5, 10) == [1]
        assert sample_map.get_pages_for_range(20, 25) == [2]

    def test_get_pages_for_range_multiple_pages(self, sample_map):
        """Test getting pages for range spanning multiple pages."""
        assert sample_map.get_pages_for_range(10, 20) == [1, 2]
        assert sample_map.get_pages_for_range(1, 45) == [1, 2, 3]

    def test_get_pages_for_range_at_boundaries(self, sample_map):
        """Test getting pages for range at page boundaries."""
        assert sample_map.get_pages_for_range(15, 16) == [1, 2]
        assert sample_map.get_pages_for_range(30, 31) == [2, 3]

    def test_empty_boundaries(self):
        """Test PageBoundaryMap with no boundaries."""
        empty_map = PageBoundaryMap(
            document_id="empty",
            boundaries=[],
            total_lines=0,
        )
        assert empty_map.get_page_for_line(1) is None
        assert empty_map.get_pages_for_range(1, 10) == []

    def test_single_page_document(self):
        """Test PageBoundaryMap with single page."""
        single_map = PageBoundaryMap(
            document_id="single",
            boundaries=[
                PageBoundary(page_num=1, start_line=1, end_line=50),
            ],
            total_lines=50,
        )
        assert single_map.get_page_for_line(1) == 1
        assert single_map.get_page_for_line(25) == 1
        assert single_map.get_page_for_line(50) == 1
        assert single_map.get_pages_for_range(1, 50) == [1]

    def test_pages_for_range_overlapping_boundaries(self, sample_map):
        """Test that get_pages_for_range correctly handles overlapping ranges."""
        # Range that starts in middle of page 1 and ends in middle of page 2
        result = sample_map.get_pages_for_range(12, 18)
        assert result == [1, 2]
        assert set(result) == {1, 2}

    def test_pages_for_range_partial_overlap(self, sample_map):
        """Test range that partially overlaps with page boundaries."""
        # Start before page 2, end within page 2
        result = sample_map.get_pages_for_range(14, 17)
        assert result == [1, 2]

    def test_with_character_offsets(self):
        """Test PageBoundaryMap with character offset tracking."""
        boundary_map = PageBoundaryMap(
            document_id="with-chars",
            boundaries=[
                PageBoundary(
                    page_num=1,
                    start_line=1,
                    end_line=10,
                    start_char=0,
                    end_char=500,
                ),
                PageBoundary(
                    page_num=2,
                    start_line=11,
                    end_line=20,
                    start_char=501,
                    end_char=1000,
                ),
            ],
            total_lines=20,
        )
        assert boundary_map.get_page_for_line(5) == 1
        assert boundary_map.get_page_for_line(15) == 2
        # Character offsets are preserved
        assert boundary_map.boundaries[0].start_char == 0
        assert boundary_map.boundaries[0].end_char == 500
        assert boundary_map.boundaries[1].start_char == 501
        assert boundary_map.boundaries[1].end_char == 1000

    def test_get_page_for_line_returns_first_match(self):
        """Test that get_page_for_line returns the first matching page."""
        # Create boundaries with a line number that could match multiple pages
        # (shouldn't happen in practice, but test robustness)
        boundary_map = PageBoundaryMap(
            document_id="test",
            boundaries=[
                PageBoundary(page_num=1, start_line=1, end_line=10),
                PageBoundary(page_num=2, start_line=11, end_line=20),
            ],
            total_lines=20,
        )
        # Line 10 should only match page 1
        assert boundary_map.get_page_for_line(10) == 1
        # Line 11 should only match page 2
        assert boundary_map.get_page_for_line(11) == 2

    def test_document_id_preserved(self):
        """Test that document_id is preserved."""
        doc_id = "test-document-123"
        boundary_map = PageBoundaryMap(
            document_id=doc_id,
            boundaries=[],
            total_lines=0,
        )
        assert boundary_map.document_id == doc_id

    def test_total_lines_field(self):
        """Test that total_lines field is set correctly."""
        boundary_map = PageBoundaryMap(
            document_id="test",
            boundaries=[
                PageBoundary(page_num=1, start_line=1, end_line=100),
            ],
            total_lines=100,
        )
        assert boundary_map.total_lines == 100
