"""Tests for agent input/output models (PRD-011)."""

import pytest
from pydantic import ValidationError
from src.shared.models.agent_models import AgentCorrection, AgentInput, AgentOutput, LLMUsage
from src.shared.models.processing import LLMUsage as ProcessingLLMUsage


@pytest.mark.unit
class TestLLMUsage:
    """Test LLMUsage model validation and serialization."""

    def test_valid_llm_usage(self):
        """Test creation with valid data."""
        usage = LLMUsage(
            input_tokens=1500,
            output_tokens=200,
            total_tokens=1700,
            estimated_cost_cents=0.25
        )
        assert usage.input_tokens == 1500
        assert usage.output_tokens == 200
        assert usage.total_tokens == 1700
        assert usage.estimated_cost_cents == 0.25

    def test_zero_tokens_valid(self):
        """Test that zero tokens is valid."""
        usage = LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0
        )
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_negative_tokens_rejected(self):
        """Test rejection of negative token counts."""
        with pytest.raises(ValidationError):
            LLMUsage(
                input_tokens=-1,
                output_tokens=100,
                total_tokens=99,
                estimated_cost_cents=0.01
            )

    def test_negative_cost_rejected(self):
        """Test rejection of negative cost."""
        with pytest.raises(ValidationError):
            LLMUsage(
                input_tokens=100,
                output_tokens=100,
                total_tokens=200,
                estimated_cost_cents=-0.01
            )

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        usage = LLMUsage(
            input_tokens=1500,
            output_tokens=200,
            total_tokens=1700,
            estimated_cost_cents=0.25
        )
        json_data = usage.model_dump_json()
        restored = LLMUsage.model_validate_json(json_data)
        assert restored.input_tokens == usage.input_tokens
        assert restored.output_tokens == usage.output_tokens
        assert restored.total_tokens == usage.total_tokens
        assert restored.estimated_cost_cents == usage.estimated_cost_cents

    def test_llm_usage_compatibility_with_processing(self):
        """Test that LLMUsage from agent_models is same as processing module."""
        # Both should be importable and work the same way
        agent_usage = LLMUsage(input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01)
        processing_usage = ProcessingLLMUsage(
            input_tokens=100, output_tokens=50, total_tokens=150, estimated_cost_cents=0.01
        )

        assert agent_usage.input_tokens == processing_usage.input_tokens
        assert agent_usage.output_tokens == processing_usage.output_tokens
        assert agent_usage.total_tokens == processing_usage.total_tokens
        assert agent_usage.estimated_cost_cents == processing_usage.estimated_cost_cents


@pytest.mark.unit
class TestAgentInput:
    """Test AgentInput model validation."""

    def test_valid_agent_input(self):
        """Test creation with valid data."""
        agent_input = AgentInput(
            page_number=1,
            page_markdown="# Introduction\n\nThis is a test.",
            page_image_base64="iVBORw0KGgoAAAANSUhEUg=="
        )
        assert agent_input.page_number == 1
        assert agent_input.page_markdown == "# Introduction\n\nThis is a test."
        assert agent_input.document_context is None
        assert agent_input.hints is None

    def test_agent_input_with_context_and_hints(self):
        """Test creation with optional context and hints."""
        agent_input = AgentInput(
            page_number=2,
            page_markdown="## Chapter 1",
            page_image_base64="iVBORw0KGgoAAAANSUhEUg==",
            document_context={
                "title": "Course Syllabus",
                "toc": ["Introduction", "Schedule", "Grading"]
            },
            hints=[
                {"type": "MD001", "message": "Heading levels should increment by one", "line": 5}
            ]
        )
        assert agent_input.document_context is not None
        assert agent_input.document_context["title"] == "Course Syllabus"
        assert len(agent_input.hints) == 1

    def test_page_number_must_be_positive(self):
        """Test rejection of non-positive page numbers."""
        with pytest.raises(ValidationError) as exc_info:
            AgentInput(
                page_number=0,
                page_markdown="test",
                page_image_base64="abc"
            )
        assert "page_number" in str(exc_info.value)

        with pytest.raises(ValidationError):
            AgentInput(
                page_number=-1,
                page_markdown="test",
                page_image_base64="abc"
            )

    def test_empty_page_markdown_allowed(self):
        """Test that empty markdown is allowed (for image-only pages)."""
        agent_input = AgentInput(
            page_number=1,
            page_markdown="",
            page_image_base64="iVBORw0KGgoAAAANSUhEUg=="
        )
        assert agent_input.page_markdown == ""

    def test_image_base64_required(self):
        """Test that image_base64 is required and cannot be empty."""
        with pytest.raises(ValidationError):
            AgentInput(
                page_number=1,
                page_markdown="test",
                page_image_base64=""
            )

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        agent_input = AgentInput(
            page_number=3,
            page_markdown="Test content",
            page_image_base64="base64data",
            document_context={"key": "value"},
            hints=[{"hint": "test"}]
        )
        json_data = agent_input.model_dump_json()
        restored = AgentInput.model_validate_json(json_data)

        assert restored.page_number == agent_input.page_number
        assert restored.page_markdown == agent_input.page_markdown
        assert restored.document_context == agent_input.document_context
        assert restored.hints == agent_input.hints


@pytest.mark.unit
class TestAgentCorrection:
    """Test AgentCorrection model validation."""

    def test_valid_agent_correction(self):
        """Test creation with valid data."""
        correction = AgentCorrection(
            correction_type="heading_level",
            original_text="Course Schedule",
            corrected_text="## Course Schedule",
            location_context="...Introduction\n\nCourse Schedule\n\nWeek 1...",
            line_number=15,
            confidence=0.95,
            explanation="Visual layout shows this as a level 2 heading",
            agent_source="structure_agent"
        )
        assert correction.correction_type == "heading_level"
        assert correction.confidence == 0.95
        assert correction.agent_source == "structure_agent"
        assert correction.hint_addressed is None

    def test_correction_with_hint_addressed(self):
        """Test correction that addresses a specific hint."""
        correction = AgentCorrection(
            correction_type="heading_level",
            original_text="Test",
            corrected_text="## Test",
            confidence=0.90,
            explanation="Fixing heading level",
            agent_source="structure_agent",
            hint_addressed="MD001:line15"
        )
        assert correction.hint_addressed == "MD001:line15"

    def test_all_correction_types_valid(self):
        """Test all valid correction types are accepted."""
        valid_types = [
            "heading_level",
            "list_structure",
            "table_format",
            "paragraph_break",
            "spelling",
            "alt_text",
            "figure_caption",
            "table_header",
            "emphasis",
            "reading_order",
            "other"
        ]
        for correction_type in valid_types:
            correction = AgentCorrection(
                correction_type=correction_type,
                original_text="test",
                corrected_text="corrected",
                confidence=0.8,
                explanation="test explanation",
                agent_source="test_agent"
            )
            assert correction.correction_type == correction_type

    def test_invalid_correction_type_rejected(self):
        """Test rejection of invalid correction types."""
        with pytest.raises(ValidationError) as exc_info:
            AgentCorrection(
                correction_type="invalid_type",
                original_text="test",
                corrected_text="corrected",
                confidence=0.8,
                explanation="test",
                agent_source="test_agent"
            )
        assert "correction_type" in str(exc_info.value)

    def test_confidence_bounds(self):
        """Test confidence score validation."""
        # Valid bounds
        correction_low = AgentCorrection(
            correction_type="other",
            original_text="a",
            corrected_text="b",
            confidence=0.0,
            explanation="test",
            agent_source="test"
        )
        assert correction_low.confidence == 0.0

        correction_high = AgentCorrection(
            correction_type="other",
            original_text="a",
            corrected_text="b",
            confidence=1.0,
            explanation="test",
            agent_source="test"
        )
        assert correction_high.confidence == 1.0

        # Out of bounds
        with pytest.raises(ValidationError):
            AgentCorrection(
                correction_type="other",
                original_text="a",
                corrected_text="b",
                confidence=1.1,
                explanation="test",
                agent_source="test"
            )

        with pytest.raises(ValidationError):
            AgentCorrection(
                correction_type="other",
                original_text="a",
                corrected_text="b",
                confidence=-0.1,
                explanation="test",
                agent_source="test"
            )

    def test_text_fields_not_empty(self):
        """Test that text fields cannot be empty."""
        with pytest.raises(ValidationError):
            AgentCorrection(
                correction_type="other",
                original_text="",
                corrected_text="b",
                confidence=0.8,
                explanation="test",
                agent_source="test"
            )

        with pytest.raises(ValidationError):
            AgentCorrection(
                correction_type="other",
                original_text="a",
                corrected_text="",
                confidence=0.8,
                explanation="test",
                agent_source="test"
            )

        with pytest.raises(ValidationError):
            AgentCorrection(
                correction_type="other",
                original_text="a",
                corrected_text="b",
                confidence=0.8,
                explanation="",
                agent_source="test"
            )

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        correction = AgentCorrection(
            correction_type="list_structure",
            original_text="- Item 1",
            corrected_text="1. Item 1",
            location_context="some context",
            line_number=10,
            confidence=0.88,
            explanation="Should be numbered list",
            agent_source="structure_agent",
            hint_addressed="MD004:line10"
        )
        json_data = correction.model_dump_json()
        restored = AgentCorrection.model_validate_json(json_data)

        assert restored.correction_type == correction.correction_type
        assert restored.original_text == correction.original_text
        assert restored.line_number == correction.line_number
        assert restored.hint_addressed == correction.hint_addressed


@pytest.mark.unit
class TestAgentOutput:
    """Test AgentOutput model validation."""

    def test_valid_agent_output(self):
        """Test creation with valid data."""
        output = AgentOutput(
            agent_name="structure_agent",
            page_number=1,
            corrections=[],
            overall_confidence=0.92,
            processing_notes="Analyzed 3 headings, 2 lists. No corrections needed."
        )
        assert output.agent_name == "structure_agent"
        assert output.page_number == 1
        assert len(output.corrections) == 0
        assert output.overall_confidence == 0.92
        assert output.usage is None

    def test_agent_output_with_corrections_and_usage(self):
        """Test output with corrections and usage tracking."""
        corrections = [
            AgentCorrection(
                correction_type="heading_level",
                original_text="Title",
                corrected_text="# Title",
                confidence=0.95,
                explanation="Should be H1",
                agent_source="structure_agent"
            )
        ]
        usage = LLMUsage(
            input_tokens=1500,
            output_tokens=200,
            total_tokens=1700,
            estimated_cost_cents=0.25
        )
        output = AgentOutput(
            agent_name="structure_agent",
            page_number=1,
            corrections=corrections,
            overall_confidence=0.90,
            processing_notes="Found 1 heading correction.",
            usage=usage
        )
        assert len(output.corrections) == 1
        assert output.corrections[0].correction_type == "heading_level"
        assert output.usage is not None
        assert output.usage.input_tokens == 1500

    def test_page_number_must_be_positive(self):
        """Test rejection of non-positive page numbers."""
        with pytest.raises(ValidationError):
            AgentOutput(
                agent_name="test",
                page_number=0,
                corrections=[],
                overall_confidence=0.8,
                processing_notes="test"
            )

    def test_confidence_bounds(self):
        """Test confidence score validation."""
        # Valid
        output = AgentOutput(
            agent_name="test",
            page_number=1,
            corrections=[],
            overall_confidence=0.0,
            processing_notes="test"
        )
        assert output.overall_confidence == 0.0

        # Invalid
        with pytest.raises(ValidationError):
            AgentOutput(
                agent_name="test",
                page_number=1,
                corrections=[],
                overall_confidence=1.5,
                processing_notes="test"
            )

    def test_processing_notes_required(self):
        """Test that processing_notes cannot be empty."""
        with pytest.raises(ValidationError):
            AgentOutput(
                agent_name="test",
                page_number=1,
                corrections=[],
                overall_confidence=0.8,
                processing_notes=""
            )

    def test_json_serialization_with_nested_models(self):
        """Test serialization with nested corrections and usage."""
        corrections = [
            AgentCorrection(
                correction_type="alt_text",
                original_text="![](image.png)",
                corrected_text="![Chart showing quarterly sales](image.png)",
                confidence=0.85,
                explanation="Added descriptive alt text",
                agent_source="figures_agent"
            )
        ]
        output = AgentOutput(
            agent_name="figures_agent",
            page_number=5,
            corrections=corrections,
            overall_confidence=0.85,
            processing_notes="Processed 1 image.",
            usage=LLMUsage(input_tokens=2000, output_tokens=150, total_tokens=2150, estimated_cost_cents=0.275)
        )

        json_data = output.model_dump_json()
        restored = AgentOutput.model_validate_json(json_data)

        assert restored.agent_name == output.agent_name
        assert len(restored.corrections) == 1
        assert restored.corrections[0].correction_type == "alt_text"
        assert restored.usage.input_tokens == 2000
