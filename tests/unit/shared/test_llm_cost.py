"""Tests for centralized LLM cost configuration module."""

import pytest
from src.shared.llm_cost import (
    DEFAULT_PRICING,
    LLMPricing,
    calculate_estimated_cost,
    format_cost_dollars,
)


@pytest.mark.unit
class TestLLMPricing:
    """Test LLMPricing dataclass."""

    def test_llm_pricing_creation(self):
        """Test creating LLMPricing with all fields."""
        pricing = LLMPricing(
            input_cost_per_token_cents=0.0001,
            output_cost_per_token_cents=0.0005,
            model_name="Test Model",
        )

        assert pricing.input_cost_per_token_cents == 0.0001
        assert pricing.output_cost_per_token_cents == 0.0005
        assert pricing.model_name == "Test Model"

    def test_llm_pricing_with_default_model_name(self):
        """Test LLMPricing defaults to 'Unknown Model' if not provided."""
        pricing = LLMPricing(
            input_cost_per_token_cents=0.0001,
            output_cost_per_token_cents=0.0005,
        )

        assert pricing.model_name == "Unknown Model"


@pytest.mark.unit
class TestDefaultPricing:
    """Test DEFAULT_PRICING constant."""

    def test_default_pricing_values(self):
        """Test DEFAULT_PRICING has correct Claude Haiku 4.5 pricing."""
        # Claude Haiku 4.5 pricing: $1.00/1M input, $5.00/1M output
        assert DEFAULT_PRICING.input_cost_per_token_cents == 0.0001
        assert DEFAULT_PRICING.output_cost_per_token_cents == 0.0005
        assert "Claude Haiku 4.5" in DEFAULT_PRICING.model_name
        assert "Bedrock" in DEFAULT_PRICING.model_name


@pytest.mark.unit
class TestCalculateEstimatedCost:
    """Test calculate_estimated_cost function."""

    def test_calculate_cost_basic(self):
        """Test basic cost calculation with default pricing."""
        cost = calculate_estimated_cost(input_tokens=1000, output_tokens=100)

        # Expected: 1000 * 0.0001 + 100 * 0.0005 = 0.1 + 0.05 = 0.15 cents
        assert cost == pytest.approx(0.15)

    def test_calculate_cost_zero_tokens(self):
        """Test cost calculation with zero tokens."""
        cost = calculate_estimated_cost(input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_calculate_cost_input_only(self):
        """Test cost calculation with only input tokens."""
        cost = calculate_estimated_cost(input_tokens=1000, output_tokens=0)

        # Expected: 1000 * 0.0001 = 0.1 cents
        assert cost == pytest.approx(0.1)

    def test_calculate_cost_output_only(self):
        """Test cost calculation with only output tokens."""
        cost = calculate_estimated_cost(input_tokens=0, output_tokens=100)

        # Expected: 100 * 0.0005 = 0.05 cents
        assert cost == pytest.approx(0.05)

    def test_calculate_cost_large_tokens(self):
        """Test cost calculation with 1 million tokens each."""
        cost = calculate_estimated_cost(input_tokens=1_000_000, output_tokens=1_000_000)

        # Expected: 1M * 0.0001 + 1M * 0.0005 = 100 + 500 = 600 cents = $6.00
        assert cost == pytest.approx(600.0)

    def test_calculate_cost_custom_pricing(self):
        """Test cost calculation with custom pricing."""
        custom_pricing = LLMPricing(
            input_cost_per_token_cents=0.0002,
            output_cost_per_token_cents=0.001,
            model_name="Custom Model",
        )

        cost = calculate_estimated_cost(
            input_tokens=1000, output_tokens=100, pricing=custom_pricing
        )

        # Expected: 1000 * 0.0002 + 100 * 0.001 = 0.2 + 0.1 = 0.3 cents
        assert cost == pytest.approx(0.3)

    def test_calculate_cost_matches_example_in_docstring(self):
        """Test that cost calculation matches the docstring example."""
        # From docstring: 1000 input, 100 output should be $0.001500
        cost = calculate_estimated_cost(1000, 100)
        dollars = cost / 100

        assert dollars == pytest.approx(0.0015)


@pytest.mark.unit
class TestFormatCostDollars:
    """Test format_cost_dollars function."""

    def test_format_very_small_cost(self):
        """Test formatting very small cost (< $0.01) with high precision."""
        # 0.15 cents = $0.0015
        formatted = format_cost_dollars(0.15)
        assert formatted == "$0.001500"

    def test_format_small_cost(self):
        """Test formatting small cost (< $0.01) with high precision."""
        # 0.5 cents = $0.005
        formatted = format_cost_dollars(0.5)
        assert formatted == "$0.005000"

    def test_format_moderate_cost(self):
        """Test formatting moderate cost (>= $0.01) with 2 decimal places."""
        # 150 cents = $1.50
        formatted = format_cost_dollars(150.0)
        assert formatted == "$1.50"

    def test_format_large_cost(self):
        """Test formatting large cost with 2 decimal places."""
        # 12345 cents = $123.45
        formatted = format_cost_dollars(12345.0)
        assert formatted == "$123.45"

    def test_format_zero_cost(self):
        """Test formatting zero cost."""
        formatted = format_cost_dollars(0.0)
        assert formatted == "$0.000000"

    def test_format_one_cent_boundary(self):
        """Test formatting at the $0.01 boundary."""
        # Just below $0.01 (0.99 cents)
        formatted_below = format_cost_dollars(0.99)
        assert formatted_below == "$0.009900"

        # Exactly $0.01 (1.0 cents)
        formatted_exact = format_cost_dollars(1.0)
        assert formatted_exact == "$0.01"

        # Just above $0.01 (1.01 cents)
        formatted_above = format_cost_dollars(1.01)
        assert formatted_above == "$0.01"


@pytest.mark.unit
class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_calculate_and_format_workflow(self):
        """Test typical workflow: calculate cost then format it."""
        # Calculate cost for 10,000 input tokens and 1,000 output tokens
        cost_cents = calculate_estimated_cost(10_000, 1_000)

        # Expected: 10000 * 0.0001 + 1000 * 0.0005 = 1.0 + 0.5 = 1.5 cents
        assert cost_cents == pytest.approx(1.5)

        # Format the cost (1.5 cents = $0.015, which is >= $0.01, so formatted with 2 decimals)
        formatted = format_cost_dollars(cost_cents)
        assert formatted == "$0.01"

    def test_cost_calculation_consistency_with_base_agent(self):
        """Test that cost calculation is consistent with what agents expect."""
        # This mirrors the test in test_base_agent.py
        input_tokens = 1000
        output_tokens = 100

        cost = calculate_estimated_cost(input_tokens, output_tokens)

        expected_input_cost = input_tokens * DEFAULT_PRICING.input_cost_per_token_cents
        expected_output_cost = output_tokens * DEFAULT_PRICING.output_cost_per_token_cents
        expected_total = expected_input_cost + expected_output_cost

        assert cost == pytest.approx(expected_total)
