"""Integration tests for PageExtractionAgent with AWS Bedrock.

These tests make real API calls to AWS Bedrock and require:
- Valid AWS credentials configured
- Access to Claude Haiku model via Bedrock

Run with: pytest -m "integration and bedrock" tests/integration/agents/
"""

import base64
from pathlib import Path

import pytest
from src.agents.page_extraction_agent import (
    PageExtractionAgent,
    PageMarkdown,
    get_page_extraction_agent,
)
from src.services.pdf_converter import PageData
from src.shared.llm_cost import DEFAULT_PRICING
from src.shared.models.context_models import DocumentContext, DocumentType, HeadingInfo

# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def create_simple_test_image() -> str:
    """Create a simple test image as base64.

    Returns a small valid PNG that can be sent to the model.
    In real tests, you'd use actual PDF page images.
    """
    # 1x1 red PNG pixel (valid but minimal)
    return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def load_test_image(name: str) -> str | None:
    """Load a test image from fixtures directory.

    Args:
        name: Image filename (e.g., "page_1.png")

    Returns:
        Base64-encoded image or None if not found
    """
    image_path = FIXTURES_DIR / name
    if not image_path.exists():
        return None
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


@pytest.mark.integration
@pytest.mark.bedrock
class TestPageExtractionAgentBedrock:
    """Integration tests with real Bedrock API calls."""

    @pytest.fixture
    def agent(self) -> PageExtractionAgent:
        """Get the page extraction agent singleton."""
        return get_page_extraction_agent()

    @pytest.mark.asyncio
    async def test_single_page_extraction(self, agent):
        """Test extraction of a single page without context."""
        # Create minimal test page
        page = PageData(
            page_num=1,
            markdown="",  # Not used in direct extraction
            image_base64=create_simple_test_image(),
        )

        result, usage = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # Verify we got a result
        assert isinstance(result, PageMarkdown)
        assert result.confidence >= 0.0
        assert result.confidence <= 1.0

        # Verify usage was tracked
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0
        assert usage.total_tokens == usage.input_tokens + usage.output_tokens
        assert usage.estimated_cost_cents >= 0

    @pytest.mark.asyncio
    async def test_extraction_with_document_context(self, agent):
        """Test extraction uses document context for heading decisions."""
        page = PageData(
            page_num=2,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        # Provide context indicating we're on page 2 with existing H1
        context = DocumentContext(
            document_id="test-123",
            document_type=DocumentType.ACADEMIC,
            total_pages=5,
            title="Research Paper Title",
            headings=[
                HeadingInfo(
                    level=1,
                    text="Research Paper Title",
                    page_number=1,
                    line_number=1,
                )
            ],
            has_single_h1=True,
        )

        result, usage = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=context,
            total_pages=5,
        )

        # Should complete without error
        assert isinstance(result, PageMarkdown)
        assert usage.input_tokens > 0

    @pytest.mark.asyncio
    async def test_extraction_with_three_page_context(self, agent):
        """Test extraction with previous and next page context."""
        # Create three pages
        prev_page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )
        current_page = PageData(
            page_num=2,
            markdown="",
            image_base64=create_simple_test_image(),
        )
        next_page = PageData(
            page_num=3,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        result, usage = await agent.process(
            current_page=current_page,
            previous_page=prev_page,
            next_page=next_page,
            document_context=None,
            total_pages=3,
        )

        # Should complete without error
        assert isinstance(result, PageMarkdown)

        # With 3 images, input tokens should be higher
        # (This is a rough check - actual tokens depend on image resolution)
        assert usage.input_tokens > 100

    @pytest.mark.asyncio
    async def test_extraction_returns_heading_structure(self, agent):
        """Test extraction identifies heading structure."""
        page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        result, _ = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # heading_structure should be a list (possibly empty for minimal image)
        assert isinstance(result.heading_structure, list)

    @pytest.mark.asyncio
    async def test_extraction_returns_observations(self, agent):
        """Test extraction provides observations for debugging."""
        page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        result, _ = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # observations should be a list
        assert isinstance(result.observations, list)

    @pytest.mark.asyncio
    async def test_extraction_continuation_flags(self, agent):
        """Test extraction sets continuation flags appropriately."""
        page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        result, _ = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # Continuation flags should be booleans
        assert isinstance(result.continues_from_previous, bool)
        assert isinstance(result.continues_to_next, bool)


@pytest.mark.integration
@pytest.mark.bedrock
@pytest.mark.slow
class TestPageExtractionWithRealPDF:
    """Integration tests with real PDF page images.

    These tests require fixture images in tests/integration/agents/fixtures/
    Skip if fixtures not available.
    """

    @pytest.fixture
    def agent(self) -> PageExtractionAgent:
        """Get the page extraction agent singleton."""
        return get_page_extraction_agent()

    @pytest.mark.asyncio
    async def test_extraction_real_page_image(self, agent):
        """Test extraction with a real PDF page image."""
        image_base64 = load_test_image("sample_page.png")
        if not image_base64:
            pytest.skip("Test fixture sample_page.png not available")

        page = PageData(
            page_num=1,
            markdown="",
            image_base64=image_base64,
        )

        result, usage = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # Should produce non-trivial markdown
        assert len(result.markdown) > 10
        assert result.confidence > 0.5

        # Log for manual verification
        print(f"\n=== Extracted Markdown ===\n{result.markdown[:500]}")
        print(f"\n=== Headings Found ===\n{result.heading_structure}")
        print(f"\n=== Observations ===\n{result.observations}")
        print(f"\n=== Est. Cost: ${usage.estimated_cost_cents/100:.6f} ===")


@pytest.mark.integration
@pytest.mark.bedrock
class TestCostTracking:
    """Tests focused on cost tracking accuracy."""

    @pytest.fixture
    def agent(self) -> PageExtractionAgent:
        """Get the page extraction agent singleton."""
        return get_page_extraction_agent()

    @pytest.mark.asyncio
    async def test_cost_calculation(self, agent):
        """Test cost is calculated correctly from token usage."""
        page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        _, usage = await agent.process(
            current_page=page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # Verify cost calculation matches expected formula
        # Haiku 4.5 pricing from DEFAULT_PRICING: $1.00/1M input, $5.00/1M output
        expected_cost = (
            usage.input_tokens * DEFAULT_PRICING.input_cost_per_token_cents
            + usage.output_tokens * DEFAULT_PRICING.output_cost_per_token_cents
        )

        # Allow small floating point variance
        assert abs(usage.estimated_cost_cents - expected_cost) < 0.001

    @pytest.mark.asyncio
    async def test_three_page_context_cost_increase(self, agent):
        """Test that 3-page context increases input tokens appropriately."""
        # Single page
        single_page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        _, single_usage = await agent.process(
            current_page=single_page,
            previous_page=None,
            next_page=None,
            document_context=None,
            total_pages=1,
        )

        # Three pages
        prev_page = PageData(
            page_num=1,
            markdown="",
            image_base64=create_simple_test_image(),
        )
        current_page = PageData(
            page_num=2,
            markdown="",
            image_base64=create_simple_test_image(),
        )
        next_page = PageData(
            page_num=3,
            markdown="",
            image_base64=create_simple_test_image(),
        )

        _, triple_usage = await agent.process(
            current_page=current_page,
            previous_page=prev_page,
            next_page=next_page,
            document_context=None,
            total_pages=3,
        )

        # Triple context should use more input tokens (images are ~1000+ tokens each)
        # But output should be similar (only converting current page)
        assert triple_usage.input_tokens > single_usage.input_tokens

        print(f"\nSingle page: {single_usage.input_tokens} input tokens")
        print(f"Triple context: {triple_usage.input_tokens} input tokens")
        print(f"Increase: {triple_usage.input_tokens / single_usage.input_tokens:.1f}x")
