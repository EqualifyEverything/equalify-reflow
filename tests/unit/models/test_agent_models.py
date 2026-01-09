"""Tests for agent input/output models."""

import pytest
from pydantic import ValidationError
from src.shared.models.agent_models import AgentInput, LLMUsage
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
