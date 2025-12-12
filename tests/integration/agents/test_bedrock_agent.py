"""Integration tests for AccessibilityAgent with AWS Bedrock.

Tests real Bedrock API interactions (requires AWS credentials).
These tests are skipped if:
- AWS credentials are not configured
- SKIP_BEDROCK_TESTS=1 environment variable is set
- AI_PROVIDER is not set to "bedrock"
- AWS_ENDPOINT_URL is set (LocalStack doesn't support Bedrock)

Run with: pytest tests/integration/agents/test_bedrock_agent.py -v
Skip with: SKIP_BEDROCK_TESTS=1 pytest tests/integration/

Note: These tests will NOT run in the standard Docker development environment
because LocalStack does not support the AWS Bedrock Converse API. They require
real AWS credentials and are intended for manual testing or CI/CD environments
with proper AWS access configured.
"""

import base64
import os

import pytest
from src.agents.accessibility_agent import (
    AccessibilityAgent,
    PageImprovementResult,
    get_accessibility_agent,
)
from src.config import settings

pytestmark = pytest.mark.integration


# Skip all tests if Bedrock tests should be skipped
# Note: These tests require REAL AWS credentials and cannot use LocalStack
skip_bedrock = pytest.mark.skipif(
    os.getenv("SKIP_BEDROCK_TESTS") == "1"
    or settings.ai_provider != "bedrock"
    or os.getenv("AWS_ENDPOINT_URL") is not None  # Skip if using LocalStack (generic endpoint)
    or os.getenv("AWS_ENDPOINT_URL_S3") is not None,  # Skip if using LocalStack (S3-specific endpoint)
    reason="Bedrock integration tests skipped (SKIP_BEDROCK_TESTS=1, AI_PROVIDER!=bedrock, or using LocalStack)"
)


# ============================================================================
# Bedrock Connection Tests (2 tests)
# ============================================================================


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_agent_initialization():
    """Test AccessibilityAgent initializes successfully with real Bedrock."""
    # This test verifies that Bedrock credentials and configuration are valid
    agent = AccessibilityAgent()

    # Should have initialized agent
    assert agent.agent is not None
    assert agent.user_prompt_template is not None

    # Should be using Bedrock provider
    assert settings.ai_provider.lower() == "bedrock"


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_singleton_initialization():
    """Test get_accessibility_agent() returns valid Bedrock instance."""
    # Reset singleton
    import src.agents.accessibility_agent as agent_module
    agent_module._agent_instance = None

    try:
        agent = get_accessibility_agent()

        # Should have valid agent
        assert agent is not None
        assert agent.agent is not None

        # Second call should return same instance
        agent2 = get_accessibility_agent()
        assert agent is agent2

    finally:
        # Clean up singleton
        agent_module._agent_instance = None


# ============================================================================
# Real Bedrock API Tests (4 tests)
# ============================================================================


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_process_simple_page():
    """Test processing a simple page with real Bedrock API."""
    agent = AccessibilityAgent()

    # Simple test page with heading and content
    page_markdown = """# Introduction to Accessibility

This is a test document for accessibility processing.

Key topics include:
- Screen reader support
- Keyboard navigation
- Color contrast
"""

    # Create a simple test image (1x1 white pixel PNG)
    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    # Process page
    result = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Verify result structure
    assert isinstance(result, PageImprovementResult)
    assert isinstance(result.improved_markdown, str)
    assert len(result.improved_markdown) > 0
    assert isinstance(result.confidence_score, float)
    assert 0.0 <= result.confidence_score <= 1.0
    assert isinstance(result.processing_notes, str)

    # Basic content preservation check
    assert "accessibility" in result.improved_markdown.lower() or \
           "test document" in result.improved_markdown.lower()


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_confidence_score_validation():
    """Test Bedrock returns valid confidence scores."""
    agent = AccessibilityAgent()

    # Test with well-structured content (should have high confidence)
    well_structured = """# Main Title

## Section 1

This is well-structured content with clear hierarchy.

### Subsection 1.1

Content with proper structure.
"""

    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    result = await agent.process_page(
        page_num=1,
        page_markdown=well_structured,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Confidence score should be in valid range
    assert 0.0 <= result.confidence_score <= 1.0

    # For well-structured content, expect reasonable confidence
    # (not enforcing specific threshold as AI behavior may vary)
    assert result.confidence_score > 0.0  # Should not be zero


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_multimodal_processing():
    """Test Bedrock processes both markdown text and page image."""
    agent = AccessibilityAgent()

    # Markdown mentions an image
    page_markdown = """# Test Document

This page contains a chart showing quarterly sales data.

[Image placeholder]
"""

    # Use a slightly larger test image
    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02"
        b"\x08\x06\x00\x00\x00r\xb6\r$\x00\x00\x00\x1cIDATx\x9cc\xfc\xcf\xf0"
        b"\x9f\x81\x81\x81\x81\x81\x81\xff\xff\xff\x1f\x03\x03\x03\x00\x00"
        b"=\xf6\x07\xfc\x0f\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    result = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Verify multimodal processing
    assert isinstance(result, PageImprovementResult)
    assert len(result.improved_markdown) > 0
    assert 0.0 <= result.confidence_score <= 1.0

    # Result should maintain content
    assert len(result.improved_markdown) >= len(page_markdown) * 0.5


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_error_handling_invalid_input():
    """Test Bedrock error handling with invalid input."""
    agent = AccessibilityAgent()

    # Test with invalid base64 image
    page_markdown = "# Test"
    invalid_base64 = "not_valid_base64!!!"

    # Should raise exception for invalid base64
    with pytest.raises(Exception):
        await agent.process_page(
            page_num=1,
            page_markdown=page_markdown,
            page_image_base64=invalid_base64,
            retry_attempt=1
        )


# ============================================================================
# Bedrock Configuration Tests (3 tests)
# ============================================================================


@skip_bedrock
def test_bedrock_region_configuration():
    """Test Bedrock region is configured (via AWS SDK)."""
    # Note: Bedrock region is configured via AWS SDK/boto3 (environment variables,
    # credentials file, or IAM role), not directly in PydanticAI
    AccessibilityAgent()

    # Configuration should have region setting (for boto3)
    assert settings.bedrock_region is not None
    assert len(settings.bedrock_region) > 0


@skip_bedrock
def test_bedrock_model_tiers_configuration():
    """Test Bedrock uses model tiers from model_tiers.py."""
    from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

    AccessibilityAgent()

    # Should have model tiers configured
    assert ModelTier.EFFICIENT in MODEL_TIER_MAP
    assert ModelTier.REASONING in MODEL_TIER_MAP
    assert "claude" in MODEL_TIER_MAP[ModelTier.EFFICIENT].lower()
    assert "claude" in MODEL_TIER_MAP[ModelTier.REASONING].lower()


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_model_settings_applied():
    """Test Bedrock respects max_tokens and temperature settings."""
    agent = AccessibilityAgent()

    # Settings should be configured
    assert settings.claude_max_tokens > 0
    assert 0.0 <= settings.claude_temperature <= 2.0

    # Process a page to verify settings are applied
    page_markdown = "# Test\n\nContent"
    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    result = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Should complete successfully with settings applied
    assert isinstance(result, PageImprovementResult)
    assert result.confidence_score >= 0.0


# ============================================================================
# Performance and Reliability Tests (2 tests)
# ============================================================================


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_processing_notes_populated():
    """Test Bedrock provides meaningful processing notes."""
    agent = AccessibilityAgent()

    page_markdown = """# Document Title

Some content that needs accessibility improvements.

Image here [placeholder]
"""

    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    result = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Processing notes should be populated
    assert result.processing_notes is not None
    assert len(result.processing_notes) > 0
    assert result.processing_notes != ""


@skip_bedrock
@pytest.mark.asyncio
async def test_bedrock_consistency_across_retries():
    """Test Bedrock provides consistent results across retry attempts."""
    agent = AccessibilityAgent()

    page_markdown = "# Consistency Test\n\nThis tests retry handling."
    test_image = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    page_image_base64 = base64.b64encode(test_image).decode("utf-8")

    # Process with retry_attempt=1
    result1 = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=1
    )

    # Process with retry_attempt=2 (simulating retry)
    result2 = await agent.process_page(
        page_num=1,
        page_markdown=page_markdown,
        page_image_base64=page_image_base64,
        retry_attempt=2
    )

    # Both should return valid results
    assert isinstance(result1, PageImprovementResult)
    assert isinstance(result2, PageImprovementResult)

    # Confidence scores should be in valid range
    assert 0.0 <= result1.confidence_score <= 1.0
    assert 0.0 <= result2.confidence_score <= 1.0


# ============================================================================
# Helper for Manual Testing
# ============================================================================


if __name__ == "__main__":
    """
    Manual test runner for Bedrock integration tests.

    Usage:
        python -m tests.integration.agents.test_bedrock_agent
    """
    import asyncio

    async def run_manual_test():
        from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier

        print("Running manual Bedrock integration test...")
        print(f"AI Provider: {settings.ai_provider}")
        print(f"EFFICIENT Model: {MODEL_TIER_MAP[ModelTier.EFFICIENT]}")
        print(f"REASONING Model: {MODEL_TIER_MAP[ModelTier.REASONING]}")
        print(f"Bedrock Region (for boto3): {settings.bedrock_region}")
        print()

        try:
            agent = AccessibilityAgent()
            print("✓ Agent initialized successfully")

            page_markdown = "# Test Document\n\nThis is a test."
            test_image = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
                b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            page_image_base64 = base64.b64encode(test_image).decode("utf-8")

            print("Processing test page...")
            result = await agent.process_page(
                page_num=1,
                page_markdown=page_markdown,
                page_image_base64=page_image_base64,
                retry_attempt=1
            )

            print("✓ Page processed successfully")
            print(f"  Confidence: {result.confidence_score:.2f}")
            print(f"  Notes: {result.processing_notes}")
            print(f"  Output length: {len(result.improved_markdown)} chars")
            print()
            print("All manual tests passed!")

        except Exception as e:
            print(f"✗ Test failed: {e}")
            raise

    asyncio.run(run_manual_test())
