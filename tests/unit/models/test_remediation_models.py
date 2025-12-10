"""Unit tests for remediation models (PageFeatures, DocumentManifest)."""

import json
from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from src.shared.models.remediation import DocumentManifest, PageFeatures


class TestPageFeatures:
    """Tests for PageFeatures model."""

    def test_minimal_valid_page_features(self) -> None:
        """Test creating PageFeatures with only required fields."""
        features = PageFeatures(page_num=1)

        assert features.page_num == 1
        assert features.has_images is False
        assert features.image_count == 0
        assert features.has_tables is False
        assert features.table_count == 0
        assert features.has_lists is False
        assert features.has_code_blocks is False
        assert features.has_math is False
        assert features.layout_type == "single_column"
        assert features.has_headers_footers is False
        assert features.complexity_score == 0.5
        assert features.complexity_factors == []

    def test_full_page_features(self) -> None:
        """Test creating PageFeatures with all fields."""
        features = PageFeatures(
            page_num=5,
            has_images=True,
            image_count=3,
            has_tables=True,
            table_count=2,
            has_lists=True,
            has_code_blocks=True,
            has_math=True,
            layout_type="two_column",
            has_headers_footers=True,
            complexity_score=0.85,
            complexity_factors=["dense tables", "code blocks", "math notation"]
        )

        assert features.page_num == 5
        assert features.has_images is True
        assert features.image_count == 3
        assert features.has_tables is True
        assert features.table_count == 2
        assert features.has_lists is True
        assert features.has_code_blocks is True
        assert features.has_math is True
        assert features.layout_type == "two_column"
        assert features.has_headers_footers is True
        assert features.complexity_score == 0.85
        assert len(features.complexity_factors) == 3

    def test_page_num_validation(self) -> None:
        """Test page_num must be >= 1."""
        with pytest.raises(ValidationError) as exc_info:
            PageFeatures(page_num=0)

        assert "page_num" in str(exc_info.value)

    def test_negative_page_num_rejected(self) -> None:
        """Test negative page numbers are rejected."""
        with pytest.raises(ValidationError):
            PageFeatures(page_num=-1)

    def test_image_count_validation(self) -> None:
        """Test image_count must be >= 0."""
        with pytest.raises(ValidationError):
            PageFeatures(page_num=1, image_count=-1)

    def test_complexity_score_bounds(self) -> None:
        """Test complexity_score must be between 0.0 and 1.0."""
        # Valid at boundaries
        features_min = PageFeatures(page_num=1, complexity_score=0.0)
        features_max = PageFeatures(page_num=1, complexity_score=1.0)
        assert features_min.complexity_score == 0.0
        assert features_max.complexity_score == 1.0

        # Invalid below minimum
        with pytest.raises(ValidationError):
            PageFeatures(page_num=1, complexity_score=-0.1)

        # Invalid above maximum
        with pytest.raises(ValidationError):
            PageFeatures(page_num=1, complexity_score=1.1)

    def test_layout_type_validation(self) -> None:
        """Test layout_type accepts only valid values."""
        # Valid values
        for layout in ["single_column", "two_column", "mixed"]:
            features = PageFeatures(page_num=1, layout_type=layout)  # type: ignore[arg-type]
            assert features.layout_type == layout

        # Invalid value
        with pytest.raises(ValidationError):
            PageFeatures(page_num=1, layout_type="three_column")  # type: ignore[arg-type]

    def test_json_serialization(self) -> None:
        """Test PageFeatures serializes to JSON correctly."""
        features = PageFeatures(
            page_num=1,
            has_images=True,
            image_count=2,
            complexity_factors=["images", "complex layout"]
        )

        json_str = features.model_dump_json()
        data = json.loads(json_str)

        assert data["page_num"] == 1
        assert data["has_images"] is True
        assert data["image_count"] == 2
        assert data["complexity_factors"] == ["images", "complex layout"]

    def test_json_deserialization(self) -> None:
        """Test PageFeatures deserializes from JSON correctly."""
        data = {
            "page_num": 3,
            "has_images": True,
            "image_count": 1,
            "has_tables": False,
            "table_count": 0,
            "has_lists": True,
            "has_code_blocks": False,
            "has_math": False,
            "layout_type": "single_column",
            "has_headers_footers": True,
            "complexity_score": 0.6,
            "complexity_factors": ["nested lists"]
        }

        features = PageFeatures(**data)
        assert features.page_num == 3
        assert features.has_images is True
        assert features.has_lists is True


class TestDocumentManifest:
    """Tests for DocumentManifest model."""

    def test_minimal_valid_manifest(self) -> None:
        """Test creating DocumentManifest with only required fields."""
        manifest = DocumentManifest(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            total_pages=10,
            heading_tree_json='{"sections": []}'
        )

        assert manifest.job_id == "550e8400-e29b-41d4-a716-446655440000"
        assert manifest.total_pages == 10
        assert manifest.heading_tree_json == '{"sections": []}'
        assert manifest.document_title == "Untitled"
        assert manifest.document_type == "unknown"
        assert manifest.page_features == []
        assert manifest.required_agents == []
        assert manifest.skip_agents == []
        assert manifest.analysis_confidence == 0.8
        assert manifest.analysis_notes == ""
        assert manifest.analysis_model == "claude-sonnet-4-5"

    def test_full_manifest(self) -> None:
        """Test creating DocumentManifest with all fields."""
        page_features = [
            PageFeatures(page_num=1, has_images=True, image_count=2),
            PageFeatures(page_num=2, has_tables=True, table_count=1),
        ]

        manifest = DocumentManifest(
            job_id="job-123",
            document_title="CS 101 Syllabus",
            document_type="syllabus",
            total_pages=10,
            heading_tree_json='{"document_title": "CS 101", "sections": []}',
            page_features=page_features,
            required_agents=["figures", "tables"],
            skip_agents=["typography"],
            analysis_confidence=0.92,
            analysis_notes="Clear structure detected",
            analysis_model="claude-sonnet-4-5"
        )

        assert manifest.document_title == "CS 101 Syllabus"
        assert manifest.document_type == "syllabus"
        assert len(manifest.page_features) == 2
        assert manifest.required_agents == ["figures", "tables"]
        assert manifest.skip_agents == ["typography"]
        assert manifest.analysis_confidence == 0.92

    def test_total_pages_validation(self) -> None:
        """Test total_pages must be >= 1."""
        with pytest.raises(ValidationError):
            DocumentManifest(
                job_id="job-123",
                total_pages=0,
                heading_tree_json='{}'
            )

    def test_analysis_confidence_bounds(self) -> None:
        """Test analysis_confidence must be between 0.0 and 1.0."""
        # Valid at boundaries
        manifest_min = DocumentManifest(
            job_id="job-123",
            total_pages=1,
            heading_tree_json='{}',
            analysis_confidence=0.0
        )
        manifest_max = DocumentManifest(
            job_id="job-123",
            total_pages=1,
            heading_tree_json='{}',
            analysis_confidence=1.0
        )
        assert manifest_min.analysis_confidence == 0.0
        assert manifest_max.analysis_confidence == 1.0

        # Invalid values
        with pytest.raises(ValidationError):
            DocumentManifest(
                job_id="job-123",
                total_pages=1,
                heading_tree_json='{}',
                analysis_confidence=1.5
            )

    def test_created_at_default(self) -> None:
        """Test created_at is set to current UTC time by default."""
        before = datetime.now(UTC)
        manifest = DocumentManifest(
            job_id="job-123",
            total_pages=1,
            heading_tree_json='{}'
        )
        after = datetime.now(UTC)

        assert before <= manifest.created_at <= after

    def test_json_serialization(self) -> None:
        """Test DocumentManifest serializes to JSON correctly."""
        manifest = DocumentManifest(
            job_id="job-123",
            document_title="Test Doc",
            total_pages=5,
            heading_tree_json='{"sections": []}',
            required_agents=["figures"],
            page_features=[PageFeatures(page_num=1)]
        )

        json_str = manifest.model_dump_json()
        data = json.loads(json_str)

        assert data["job_id"] == "job-123"
        assert data["document_title"] == "Test Doc"
        assert data["total_pages"] == 5
        assert data["required_agents"] == ["figures"]
        assert len(data["page_features"]) == 1

    def test_json_round_trip(self) -> None:
        """Test DocumentManifest survives JSON round-trip."""
        original = DocumentManifest(
            job_id="job-123",
            document_title="Round Trip Test",
            total_pages=3,
            heading_tree_json='{"test": true}',
            page_features=[
                PageFeatures(page_num=1, has_images=True),
                PageFeatures(page_num=2, has_tables=True),
            ],
            required_agents=["figures", "tables"],
            analysis_confidence=0.88
        )

        json_str = original.model_dump_json()
        restored = DocumentManifest.model_validate_json(json_str)

        assert restored.job_id == original.job_id
        assert restored.document_title == original.document_title
        assert restored.total_pages == original.total_pages
        assert len(restored.page_features) == len(original.page_features)
        assert restored.required_agents == original.required_agents
        assert restored.analysis_confidence == original.analysis_confidence

    def test_nested_page_features(self) -> None:
        """Test page_features list contains valid PageFeatures objects."""
        manifest = DocumentManifest(
            job_id="job-123",
            total_pages=2,
            heading_tree_json='{}',
            page_features=[
                PageFeatures(page_num=1),
                PageFeatures(page_num=2, has_images=True)
            ]
        )

        assert len(manifest.page_features) == 2
        assert manifest.page_features[0].page_num == 1
        assert manifest.page_features[1].has_images is True

    def test_schema_example(self) -> None:
        """Test the JSON schema example is valid."""
        schema = DocumentManifest.model_json_schema()
        example = schema.get("examples", [{}])[0] if "examples" in schema else {}

        # The example should be in json_schema_extra
        config_extra = DocumentManifest.model_config.get("json_schema_extra", {})
        if "example" in config_extra:
            example = config_extra["example"]
            # Validate it creates a valid model
            manifest = DocumentManifest(**example)
            assert manifest.job_id == example["job_id"]
