"""Tests for DocumentSummary and ObservationContext models."""


from src.shared.models.document_context import DocumentSummary, ObservationContext
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import HeadingTree


class TestDocumentSummary:
    """Tests for the DocumentSummary model."""

    def test_create_document_summary(self):
        """Test creating a document summary."""
        summary = DocumentSummary(
            title="yt: An Open Source Framework",
            document_type="research_paper",
            topic_summary="Research paper about yt, an analysis framework.",
            structure_summary="9 pages, two-column layout",
            key_entities=["yt", "Enzo", "Matthew Turk"],
            domain_terms=["parallelization", "MPI", "OpenMP"],
            expected_elements=["abstract", "introduction", "references"],
            audience_level="academic",
        )

        assert summary.title == "yt: An Open Source Framework"
        assert summary.document_type == "research_paper"
        assert len(summary.key_entities) == 3
        assert "yt" in summary.key_entities
        assert summary.audience_level == "academic"

    def test_default_values(self):
        """Test default values for optional fields."""
        summary = DocumentSummary(
            title="Test Document",
            document_type="other",
            topic_summary="A test document",
        )

        assert summary.structure_summary == ""
        assert summary.key_entities == []
        assert summary.domain_terms == []
        assert summary.expected_elements == []
        assert summary.audience_level == "general"

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        summary = DocumentSummary(
            title="Test",
            document_type="syllabus",
            topic_summary="Course syllabus",
            key_entities=["CS101", "Professor Smith"],
        )

        json_str = summary.model_dump_json()
        restored = DocumentSummary.model_validate_json(json_str)

        assert restored.title == summary.title
        assert restored.key_entities == summary.key_entities


class TestObservationContext:
    """Tests for the ObservationContext model."""

    def _create_observation(self) -> Observation:
        """Helper to create a test observation."""
        return Observation(
            job_id="job-123",
            agent="figures",
            visual_description="Image shows a chart",
            markup_description="Empty alt text",
            location=ObservationLocation(
                location_type="element",
                value="img[src='fig1.png']",
                page_num=1,
            ),
        )

    def _create_summary(self) -> DocumentSummary:
        """Helper to create a test summary."""
        return DocumentSummary(
            title="Test Document",
            document_type="research_paper",
            topic_summary="A research paper",
        )

    def _create_heading_tree(self) -> HeadingTree:
        """Helper to create a test heading tree."""
        return HeadingTree(
            document_title="Test Document",
            sections=[],
        )

    def test_create_observation_context(self):
        """Test creating an observation context."""
        obs = self._create_observation()
        summary = self._create_summary()
        tree = self._create_heading_tree()

        context = ObservationContext(
            observation=obs,
            document_summary=summary,
            heading_tree=tree,
            markdown_excerpt="## Introduction\n\nSome text...",
            before_context="# Title\n\n",
            after_context="\n\n## Methods",
            page_num=1,
        )

        assert context.observation.job_id == "job-123"
        assert context.document_summary.title == "Test Document"
        assert context.page_num == 1

    def test_optional_visual_context(self):
        """Test that visual context is optional."""
        obs = self._create_observation()
        summary = self._create_summary()
        tree = self._create_heading_tree()

        context = ObservationContext(
            observation=obs,
            document_summary=summary,
            heading_tree=tree,
            markdown_excerpt="text",
            page_num=1,
        )

        assert context.page_image_base64 is None
        assert context.line_range is None

    def test_line_range(self):
        """Test line range field."""
        obs = self._create_observation()
        summary = self._create_summary()
        tree = self._create_heading_tree()

        context = ObservationContext(
            observation=obs,
            document_summary=summary,
            heading_tree=tree,
            markdown_excerpt="text",
            page_num=1,
            line_range=(10, 25),
        )

        assert context.line_range == (10, 25)
