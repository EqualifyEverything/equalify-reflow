"""Tests for ReasoningCorpusService (PRD-020).

Tests the reasoning corpus service that logs reasoning from Reasoned[T] fields.
"""

from typing import Literal
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field
from src.services.reasoning_corpus_service import (
    ReasoningCorpusService,
    get_reasoning_corpus_service,
)
from src.shared.models.reasoned import Reasoned, ReasonedOutputMixin

pytestmark = pytest.mark.unit


class SampleAnalysis(BaseModel, ReasonedOutputMixin):
    """Sample model with Reasoned fields for testing."""

    image_type: Reasoned[Literal["decorative", "informative"]] = Field(...)
    severity: Reasoned[Literal["critical", "major", "minor"]] = Field(...)
    plain_field: str = Field(default="not reasoned")


class TestReasoningCorpusServiceBasics:
    """Test basic ReasoningCorpusService functionality."""

    def test_initialization(self) -> None:
        """Service should initialize with zero entry count."""
        service = ReasoningCorpusService()
        assert service.get_entry_count() == 0

    @pytest.mark.asyncio
    async def test_log_reasoning_increments_count(self) -> None:
        """log_reasoning should increment entry count."""
        service = ReasoningCorpusService()

        await service.log_reasoning(
            job_id="job-123",
            agent_name="figures",
            model_class="ImageAnalysis",
            field="image_type",
            reasoning="Chart with data points visible.",
            value="informative",
        )

        assert service.get_entry_count() == 1

    @pytest.mark.asyncio
    async def test_log_multiple_entries(self) -> None:
        """Multiple log entries should be tracked."""
        service = ReasoningCorpusService()

        for i in range(5):
            await service.log_reasoning(
                job_id=f"job-{i}",
                agent_name="figures",
                model_class="ImageAnalysis",
                field="image_type",
                reasoning=f"Reasoning {i}",
                value="informative",
            )

        assert service.get_entry_count() == 5


class TestLogCorpusBatch:
    """Test log_corpus_batch() method."""

    @pytest.mark.asyncio
    async def test_log_corpus_batch_from_extract(self) -> None:
        """log_corpus_batch should process output from extract_reasoning_corpus()."""
        service = ReasoningCorpusService()

        # Create a sample analysis with reasoned fields
        analysis = SampleAnalysis(
            image_type=Reasoned(
                reasoning="Chart with axis labels and data points.",
                value="informative",
            ),
            severity=Reasoned(
                reasoning="Missing alt text blocks screen readers.",
                value="major",
            ),
        )

        # Extract and log corpus
        corpus = analysis.extract_reasoning_corpus()
        await service.log_corpus_batch(
            job_id="job-456",
            agent_name="figures",
            corpus=corpus,
        )

        # Should have logged 2 entries (image_type and severity)
        assert service.get_entry_count() == 2

    @pytest.mark.asyncio
    async def test_log_corpus_batch_empty(self) -> None:
        """log_corpus_batch should handle empty corpus gracefully."""
        service = ReasoningCorpusService()

        await service.log_corpus_batch(
            job_id="job-789",
            agent_name="figures",
            corpus=[],
        )

        assert service.get_entry_count() == 0


class TestLogReasoningStructure:
    """Test structure of logged reasoning entries."""

    @pytest.mark.asyncio
    async def test_log_entry_structure(self) -> None:
        """Logged entries should have correct structure."""
        service = ReasoningCorpusService()

        with patch("src.services.reasoning_corpus_service.logger") as mock_logger:
            await service.log_reasoning(
                job_id="job-123",
                agent_name="figures",
                model_class="ImageAnalysis",
                field="image_type",
                reasoning="Chart with data points visible. Contains quantitative information.",
                value="informative",
            )

            # Verify logger was called
            mock_logger.info.assert_called_once()

            # Get the logged extra data
            call_args = mock_logger.info.call_args
            extra = call_args.kwargs.get("extra", {})
            entry = extra.get("reasoning_data", {})

            # Verify entry structure
            assert entry["job_id"] == "job-123"
            assert entry["agent"] == "figures"
            assert entry["model_class"] == "ImageAnalysis"
            assert entry["field"] == "image_type"
            assert entry["reasoning"] == "Chart with data points visible. Contains quantitative information."
            assert entry["value"] == "informative"
            assert "timestamp" in entry
            assert "reasoning_length" in entry
            assert "sentence_count" in entry

    @pytest.mark.asyncio
    async def test_sentence_count_calculation(self) -> None:
        """Sentence count should be calculated correctly."""
        service = ReasoningCorpusService()

        with patch("src.services.reasoning_corpus_service.logger") as mock_logger:
            # Test with 2 sentences (period + space pattern)
            await service.log_reasoning(
                job_id="job-123",
                agent_name="figures",
                model_class="ImageAnalysis",
                field="image_type",
                reasoning="First sentence. Second sentence.",
                value="informative",
            )

            call_args = mock_logger.info.call_args
            entry = call_args.kwargs["extra"]["reasoning_data"]

            # "First sentence. Second sentence." has 1 ". " separator + 1 = 2 sentences
            assert entry["sentence_count"] == 2

    @pytest.mark.asyncio
    async def test_reasoning_length_calculation(self) -> None:
        """Reasoning length should be calculated correctly."""
        service = ReasoningCorpusService()

        reasoning_text = "This is a 50 character reasoning string here!!!"

        with patch("src.services.reasoning_corpus_service.logger") as mock_logger:
            await service.log_reasoning(
                job_id="job-123",
                agent_name="figures",
                model_class="ImageAnalysis",
                field="image_type",
                reasoning=reasoning_text,
                value="informative",
            )

            call_args = mock_logger.info.call_args
            entry = call_args.kwargs["extra"]["reasoning_data"]

            assert entry["reasoning_length"] == len(reasoning_text)


class TestGetReasoningCorpusService:
    """Test get_reasoning_corpus_service() singleton function."""

    def test_returns_singleton(self) -> None:
        """get_reasoning_corpus_service should return the same instance."""
        # Reset the singleton for testing
        import src.services.reasoning_corpus_service as module
        module._corpus_service = None

        service1 = get_reasoning_corpus_service()
        service2 = get_reasoning_corpus_service()

        assert service1 is service2

    def test_singleton_preserves_state(self) -> None:
        """Singleton should preserve state across calls."""
        # Reset the singleton
        import src.services.reasoning_corpus_service as module
        module._corpus_service = None

        service1 = get_reasoning_corpus_service()
        # Manually increment for testing
        service1.entry_count = 5

        service2 = get_reasoning_corpus_service()
        assert service2.entry_count == 5


class TestReasonedOutputMixinIntegration:
    """Test integration with ReasonedOutputMixin."""

    def test_extract_reasoning_corpus_basic(self) -> None:
        """extract_reasoning_corpus should return list of dicts."""
        analysis = SampleAnalysis(
            image_type=Reasoned(
                reasoning="Clear informative chart.",
                value="informative",
            ),
            severity=Reasoned(
                reasoning="Important for accessibility.",
                value="major",
            ),
        )

        corpus = analysis.extract_reasoning_corpus()

        assert len(corpus) == 2
        assert all("field" in entry for entry in corpus)
        assert all("reasoning" in entry for entry in corpus)
        assert all("value" in entry for entry in corpus)
        assert all("model_class" in entry for entry in corpus)

    def test_extract_includes_model_class(self) -> None:
        """Extracted corpus should include model class name."""
        analysis = SampleAnalysis(
            image_type=Reasoned(
                reasoning="Test reasoning.",
                value="decorative",
            ),
            severity=Reasoned(
                reasoning="Test severity reasoning.",
                value="minor",
            ),
        )

        corpus = analysis.extract_reasoning_corpus()

        for entry in corpus:
            assert entry["model_class"] == "SampleAnalysis"

    def test_plain_fields_not_extracted(self) -> None:
        """Non-Reasoned fields should not be in corpus."""
        analysis = SampleAnalysis(
            image_type=Reasoned(
                reasoning="This is a test reasoning for image type classification.",
                value="informative",
            ),
            severity=Reasoned(
                reasoning="This is a test reasoning for severity assessment.",
                value="major",
            ),
            plain_field="This should not be extracted",
        )

        corpus = analysis.extract_reasoning_corpus()

        field_names = [entry["field"] for entry in corpus]
        assert "plain_field" not in field_names


class TestSecurityConsiderations:
    """Test security-related documentation and behavior."""

    def test_service_has_security_documentation(self) -> None:
        """ReasoningCorpusService should have security documentation in docstring."""
        docstring = ReasoningCorpusService.__doc__

        assert "SECURITY" in docstring.upper()
        assert "PII" in docstring.upper()
        assert "Access" in docstring
