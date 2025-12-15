"""Tests for AgentCore shared infrastructure (PRD-018).

This module tests the core shared infrastructure for all agents:
- LLM usage aggregation
- Path validation security
- BatchResult helper methods
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from src.agents.base_agent import BatchResult
from src.agents.core import AgentConfig, AgentCore
from src.shared.models.processing import LLMUsage


# Test output model for agent configuration
class _TestOutput:
    """Minimal test output type."""
    pass


@pytest.mark.unit
class TestAggregateUsage:
    """Test AgentCore.aggregate_usage() method."""

    def test_aggregate_usage_combines_multiple_usages(self):
        """Test that multiple LLMUsage objects are correctly aggregated."""
        usages = [
            LLMUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                estimated_cost_cents=0.25
            ),
            LLMUsage(
                input_tokens=200,
                output_tokens=75,
                total_tokens=275,
                estimated_cost_cents=0.35
            ),
            LLMUsage(
                input_tokens=150,
                output_tokens=100,
                total_tokens=250,
                estimated_cost_cents=0.40
            ),
        ]

        result = AgentCore.aggregate_usage(usages)

        assert result.input_tokens == 450  # 100 + 200 + 150
        assert result.output_tokens == 225  # 50 + 75 + 100
        assert result.total_tokens == 675  # 150 + 275 + 250
        assert result.estimated_cost_cents == pytest.approx(1.00)  # 0.25 + 0.35 + 0.40

    def test_aggregate_usage_empty_list(self):
        """Test that empty list returns zero values."""
        result = AgentCore.aggregate_usage([])

        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.total_tokens == 0
        assert result.estimated_cost_cents == 0.0

    def test_aggregate_usage_single_item(self):
        """Test that single item is returned with same values."""
        usage = LLMUsage(
            input_tokens=500,
            output_tokens=250,
            total_tokens=750,
            estimated_cost_cents=1.50
        )

        result = AgentCore.aggregate_usage([usage])

        assert result.input_tokens == 500
        assert result.output_tokens == 250
        assert result.total_tokens == 750
        assert result.estimated_cost_cents == pytest.approx(1.50)


@pytest.mark.unit
class TestPathValidation:
    """Test path validation security in prompt loading."""

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_path_validation_prevents_parent_traversal(self, mock_open, mock_yaml):
        """Test that ../ paths are rejected to prevent directory traversal."""
        mock_yaml.return_value = {"system_prompt": "Test"}

        config = AgentConfig(
            name="security_test",
            prompts_file=Path("../../../etc/passwd"),  # Attempt traversal
            output_type=_TestOutput,
            correction_types=[]
        )

        with pytest.raises(ValueError) as exc_info:
            AgentCore(config)

        assert "Invalid prompts file path" in str(exc_info.value)

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("src.agents.core.settings")
    def test_path_validation_prevents_similar_directory_names(
        self, mock_settings, mock_open, mock_yaml
    ):
        """Test that similar directory names don't bypass validation.

        Ensures that a directory named 'agents_evil' doesn't pass validation
        when the allowed directory is 'agents'.
        """
        mock_yaml.return_value = {"system_prompt": "Test"}

        # Set up the allowed base directory
        mock_settings.agent_prompts_dir = "/app/config/agents"

        # Try to use a similar but different directory
        config = AgentConfig(
            name="similarity_test",
            prompts_file=Path("../agents_evil/malicious.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )

        with pytest.raises(ValueError) as exc_info:
            AgentCore(config)

        assert "Invalid prompts file path" in str(exc_info.value)

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    @patch("src.agents.core.settings")
    def test_path_validation_allows_valid_subdirectory(
        self, mock_settings, mock_open, mock_yaml, tmp_path
    ):
        """Test that valid subdirectory paths are allowed."""
        mock_yaml.return_value = {"system_prompt": "Valid prompt"}

        # Create valid directory structure
        agents_dir = tmp_path / "config" / "agents"
        agents_dir.mkdir(parents=True)
        prompts_file = agents_dir / "valid_prompts.yaml"
        prompts_file.write_text("system_prompt: 'Valid prompt'")

        mock_settings.agent_prompts_dir = str(agents_dir)

        config = AgentConfig(
            name="valid_test",
            prompts_file=Path("valid_prompts.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )

        # Should not raise - valid path within allowed directory
        core = AgentCore(config)
        assert core.prompts["system_prompt"] == "Valid prompt"


@pytest.mark.unit
class TestBatchResult:
    """Test BatchResult helper methods."""

    def test_batch_result_has_errors_true_when_errors_exist(self):
        """Test has_errors returns True when errors are present."""
        result = BatchResult(
            outputs=["output1", "output2"],
            usage=LLMUsage(
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                estimated_cost_cents=0.25
            ),
            errors=[(1, "Failed to process page 1"), (3, "Failed to process page 3")]
        )

        assert result.has_errors is True

    def test_batch_result_has_errors_false_when_no_errors(self):
        """Test has_errors returns False when no errors occurred."""
        result = BatchResult(
            outputs=["output1", "output2", "output3"],
            usage=LLMUsage(
                input_tokens=150,
                output_tokens=75,
                total_tokens=225,
                estimated_cost_cents=0.35
            ),
            errors=[]
        )

        assert result.has_errors is False

    def test_batch_result_success_count(self):
        """Test success_count returns the number of successful outputs."""
        result = BatchResult(
            outputs=["output1", "output2", "output3", "output4"],
            usage=LLMUsage(
                input_tokens=200,
                output_tokens=100,
                total_tokens=300,
                estimated_cost_cents=0.50
            ),
            errors=[(5, "Error on page 5")]
        )

        assert result.success_count == 4

    def test_batch_result_error_count(self):
        """Test error_count returns the number of errors."""
        result = BatchResult(
            outputs=["output1"],
            usage=LLMUsage(
                input_tokens=50,
                output_tokens=25,
                total_tokens=75,
                estimated_cost_cents=0.15
            ),
            errors=[
                (2, "Error on page 2"),
                (3, "Error on page 3"),
                (4, "Error on page 4")
            ]
        )

        assert result.error_count == 3

    def test_batch_result_default_values(self):
        """Test BatchResult with default empty values."""
        result = BatchResult()

        assert result.outputs == []
        assert result.usage is None
        assert result.errors == []
        assert result.has_errors is False
        assert result.success_count == 0
        assert result.error_count == 0

    def test_batch_result_all_failures(self):
        """Test BatchResult when all items failed."""
        result = BatchResult(
            outputs=[],
            usage=None,
            errors=[
                (1, "Error 1"),
                (2, "Error 2"),
                (3, "Error 3")
            ]
        )

        assert result.has_errors is True
        assert result.success_count == 0
        assert result.error_count == 3
