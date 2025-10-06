"""Unit tests for AccessibilityAgent with multiple AI providers.

Tests provider initialization, configuration, and page processing for both:
- AWS Bedrock (BedrockConverseModel)
- Anthropic Direct API

All AI API calls are mocked for fast, deterministic unit tests.
"""

import pytest
import base64
from unittest.mock import MagicMock, patch, mock_open, AsyncMock

from src.agents.accessibility_agent import (
    AccessibilityAgent,
    PageImprovementResult,
    get_accessibility_agent,
)


pytestmark = pytest.mark.unit


# ============================================================================
# Provider Initialization Tests (8 tests)
# ============================================================================


def test_bedrock_provider_initialization():
    """Test AccessibilityAgent initializes with Bedrock provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"

        with patch("src.agents.accessibility_agent.Agent") as MockAgent:
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel") as MockBedrock:
                with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                    "system_prompt": "System prompt",
                    "user_prompt_template": "User: {page_markdown}"
                }):
                    agent = AccessibilityAgent()

                    # Should create BedrockConverseModel with model_name
                    MockBedrock.assert_called_once_with(
                        model_name="anthropic.claude-3-5-haiku-20241022-v1:0",
                    )

                    # Should pass Bedrock model to Agent
                    MockAgent.assert_called_once()
                    call_args = MockAgent.call_args
                    assert call_args.kwargs["output_type"] == PageImprovementResult
                    assert call_args.kwargs["system_prompt"] == "System prompt"


def test_anthropic_provider_initialization():
    """Test AccessibilityAgent initializes with Anthropic API provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"

        with patch("src.agents.accessibility_agent.Agent") as MockAgent:
            with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                "system_prompt": "System prompt",
                "user_prompt_template": "User: {page_markdown}"
            }):
                agent = AccessibilityAgent()

                # Should create Agent with Anthropic model string
                MockAgent.assert_called_once()
                call_args = MockAgent.call_args
                assert call_args[0][0] == "anthropic:claude-3-5-haiku-20241022"
                assert call_args.kwargs["output_type"] == PageImprovementResult
                assert call_args.kwargs["system_prompt"] == "System prompt"


def test_invalid_provider_raises_error():
    """Test ValueError raised for unsupported AI provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "openai"  # Invalid provider

        with patch.object(AccessibilityAgent, "_load_prompts", return_value={
            "system_prompt": "System",
            "user_prompt_template": "User: {page_markdown}"
        }):
            with pytest.raises(ValueError, match="Unsupported AI provider: openai"):
                agent = AccessibilityAgent()


def test_bedrock_provider_case_insensitive():
    """Test provider name is case-insensitive (BEDROCK, Bedrock, bedrock)."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "BEDROCK"  # Uppercase
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-west-2"

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel") as MockBedrock:
                with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                    "system_prompt": "System",
                    "user_prompt_template": "User: {page_markdown}"
                }):
                    agent = AccessibilityAgent()

                    # Should still create Bedrock model
                    MockBedrock.assert_called_once()


def test_anthropic_provider_case_insensitive():
    """Test provider name is case-insensitive (ANTHROPIC, Anthropic, anthropic)."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "ANTHROPIC"  # Uppercase
        mock_settings.claude_model = "claude-3-5-haiku-20241022"

        with patch("src.agents.accessibility_agent.Agent") as MockAgent:
            with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                "system_prompt": "System",
                "user_prompt_template": "User: {page_markdown}"
            }):
                agent = AccessibilityAgent()

                # Should create Anthropic model
                model_arg = MockAgent.call_args[0][0]
                assert "anthropic:" in model_arg.lower()


def test_bedrock_model_id_configuration():
    """Test Bedrock model ID is configurable from settings."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-opus-20240229-v1:0"

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel") as MockBedrock:
                with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                    "system_prompt": "System",
                    "user_prompt_template": "User: {page_markdown}"
                }):
                    agent = AccessibilityAgent()

                    # Should use configured model ID
                    MockBedrock.assert_called_once_with(
                        model_name="anthropic.claude-3-opus-20240229-v1:0",
                    )


def test_anthropic_model_configuration():
    """Test Anthropic model name is configurable from settings."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-opus-20240229"

        with patch("src.agents.accessibility_agent.Agent") as MockAgent:
            with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                "system_prompt": "System",
                "user_prompt_template": "User: {page_markdown}"
            }):
                agent = AccessibilityAgent()

                # Should use configured model name
                model_arg = MockAgent.call_args[0][0]
                assert model_arg == "anthropic:claude-3-opus-20240229"


def test_provider_logged_on_initialization():
    """Test provider choice is logged during initialization."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                with patch.object(AccessibilityAgent, "_load_prompts", return_value={
                    "system_prompt": "System",
                    "user_prompt_template": "User: {page_markdown}"
                }):
                    with patch("src.agents.accessibility_agent.logger") as mock_logger:
                        agent = AccessibilityAgent()

                        # Should log provider initialization
                        mock_logger.info.assert_any_call(
                            "Accessibility agent initialized with provider: bedrock"
                        )


# ============================================================================
# process_page with Different Providers (6 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_page_bedrock_success():
    """Test successful page processing with Bedrock provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                agent = AccessibilityAgent()

                # Mock agent.run to return result
                mock_result = MagicMock()
                mock_result.output = PageImprovementResult(
                    improved_markdown="# Improved via Bedrock\n\nContent",
                    confidence_score=0.95,
                    processing_notes="Processed by Bedrock"
                )

                agent.agent = AsyncMock()
                agent.agent.run = AsyncMock(return_value=mock_result)

                # Test page data
                page_markdown = "# Original Page\n\nContent"
                page_image_base64 = base64.b64encode(b"fake_image_data").decode("utf-8")

                result = await agent.process_page(
                    page_num=1,
                    page_markdown=page_markdown,
                    page_image_base64=page_image_base64,
                    retry_attempt=1
                )

                # Should return improvement result
                assert result.improved_markdown == "# Improved via Bedrock\n\nContent"
                assert result.confidence_score == 0.95
                assert result.processing_notes == "Processed by Bedrock"

                # Should have called agent.run with correct settings
                agent.agent.run.assert_called_once()
                call_kwargs = agent.agent.run.call_args.kwargs
                assert call_kwargs["model_settings"]["max_tokens"] == 4096
                assert call_kwargs["model_settings"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_process_page_anthropic_success():
    """Test successful page processing with Anthropic API provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"
        mock_settings.claude_max_tokens = 8192
        mock_settings.claude_temperature = 0.3

        with patch("src.agents.accessibility_agent.Agent"):
            agent = AccessibilityAgent()

            # Mock agent.run to return result
            mock_result = MagicMock()
            mock_result.output = PageImprovementResult(
                improved_markdown="# Improved via Anthropic\n\nContent",
                confidence_score=0.88,
                processing_notes="Processed by Anthropic API"
            )

            agent.agent = AsyncMock()
            agent.agent.run = AsyncMock(return_value=mock_result)

            # Test page data
            page_markdown = "# Original Page\n\nContent"
            page_image_base64 = base64.b64encode(b"fake_image_data").decode("utf-8")

            result = await agent.process_page(
                page_num=2,
                page_markdown=page_markdown,
                page_image_base64=page_image_base64,
                retry_attempt=1
            )

            # Should return improvement result
            assert result.improved_markdown == "# Improved via Anthropic\n\nContent"
            assert result.confidence_score == 0.88
            assert result.processing_notes == "Processed by Anthropic API"

            # Should have called agent.run with correct settings
            agent.agent.run.assert_called_once()
            call_kwargs = agent.agent.run.call_args.kwargs
            assert call_kwargs["model_settings"]["max_tokens"] == 8192
            assert call_kwargs["model_settings"]["temperature"] == 0.3


@pytest.mark.asyncio
async def test_process_page_bedrock_multimodal_input():
    """Test multimodal input formatting for Bedrock provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                agent = AccessibilityAgent()
                agent.user_prompt_template = "Process: {page_markdown}"

                mock_result = MagicMock()
                mock_result.output = PageImprovementResult(
                    improved_markdown="Improved", confidence_score=0.9, processing_notes="OK"
                )

                agent.agent = AsyncMock()
                agent.agent.run = AsyncMock(return_value=mock_result)

                page_markdown = "Test Bedrock content"
                page_image_base64 = base64.b64encode(b"bedrock_image").decode("utf-8")

                await agent.process_page(
                    page_num=1,
                    page_markdown=page_markdown,
                    page_image_base64=page_image_base64
                )

                # Verify multimodal input (text + image)
                call_args = agent.agent.run.call_args
                messages = call_args[0][0]

                # Should have text and image
                assert len(messages) == 2
                assert "Process: Test Bedrock content" in messages[0]

                # Second message should be BinaryContent
                from pydantic_ai.messages import BinaryContent
                assert isinstance(messages[1], BinaryContent)
                assert messages[1].media_type == "image/png"


@pytest.mark.asyncio
async def test_process_page_anthropic_multimodal_input():
    """Test multimodal input formatting for Anthropic API provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        with patch("src.agents.accessibility_agent.Agent"):
            agent = AccessibilityAgent()
            agent.user_prompt_template = "Process: {page_markdown}"

            mock_result = MagicMock()
            mock_result.output = PageImprovementResult(
                improved_markdown="Improved", confidence_score=0.9, processing_notes="OK"
            )

            agent.agent = AsyncMock()
            agent.agent.run = AsyncMock(return_value=mock_result)

            page_markdown = "Test Anthropic content"
            page_image_base64 = base64.b64encode(b"anthropic_image").decode("utf-8")

            await agent.process_page(
                page_num=1,
                page_markdown=page_markdown,
                page_image_base64=page_image_base64
            )

            # Verify multimodal input (text + image)
            call_args = agent.agent.run.call_args
            messages = call_args[0][0]

            # Should have text and image
            assert len(messages) == 2
            assert "Process: Test Anthropic content" in messages[0]

            # Second message should be BinaryContent
            from pydantic_ai.messages import BinaryContent
            assert isinstance(messages[1], BinaryContent)
            assert messages[1].media_type == "image/png"


@pytest.mark.asyncio
async def test_process_page_bedrock_error_handling():
    """Test error handling when Bedrock API fails."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                agent = AccessibilityAgent()

                # Mock agent.run to raise Bedrock exception
                agent.agent = AsyncMock()
                agent.agent.run = AsyncMock(
                    side_effect=Exception("Bedrock throttling error")
                )

                page_image_base64 = base64.b64encode(b"img").decode("utf-8")

                # Should raise exception
                with pytest.raises(Exception, match="Bedrock throttling error"):
                    await agent.process_page(
                        page_num=1,
                        page_markdown="Test",
                        page_image_base64=page_image_base64
                    )


@pytest.mark.asyncio
async def test_process_page_anthropic_error_handling():
    """Test error handling when Anthropic API fails."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        with patch("src.agents.accessibility_agent.Agent"):
            agent = AccessibilityAgent()

            # Mock agent.run to raise Anthropic exception
            agent.agent = AsyncMock()
            agent.agent.run = AsyncMock(
                side_effect=Exception("Anthropic rate limit exceeded")
            )

            page_image_base64 = base64.b64encode(b"img").decode("utf-8")

            # Should raise exception
            with pytest.raises(Exception, match="Anthropic rate limit exceeded"):
                await agent.process_page(
                    page_num=1,
                    page_markdown="Test",
                    page_image_base64=page_image_base64
                )


# ============================================================================
# Singleton Pattern with Different Providers (2 tests)
# ============================================================================


def test_get_accessibility_agent_singleton_bedrock():
    """Test singleton pattern with Bedrock provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                # Reset singleton
                import src.agents.accessibility_agent as agent_module
                agent_module._agent_instance = None

                # First call creates instance
                agent1 = get_accessibility_agent()

                # Second call returns same instance
                agent2 = get_accessibility_agent()

                # Should be same object
                assert agent1 is agent2

                # Clean up
                agent_module._agent_instance = None


def test_get_accessibility_agent_singleton_anthropic():
    """Test singleton pattern with Anthropic provider."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"

        with patch("src.agents.accessibility_agent.Agent"):
            # Reset singleton
            import src.agents.accessibility_agent as agent_module
            agent_module._agent_instance = None

            # First call creates instance
            agent1 = get_accessibility_agent()

            # Second call returns same instance
            agent2 = get_accessibility_agent()

            # Should be same object
            assert agent1 is agent2

            # Clean up
            agent_module._agent_instance = None


# ============================================================================
# Shared Configuration Tests (3 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_shared_claude_settings_bedrock():
    """Test shared Claude settings (max_tokens, temperature) work with Bedrock."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "bedrock"
        mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
        mock_settings.bedrock_region = "us-east-1"
        mock_settings.claude_max_tokens = 16000
        mock_settings.claude_temperature = 0.5

        with patch("src.agents.accessibility_agent.Agent"):
            with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                agent = AccessibilityAgent()

                mock_result = MagicMock()
                mock_result.output = PageImprovementResult(
                    improved_markdown="Test", confidence_score=0.9, processing_notes="OK"
                )

                agent.agent = AsyncMock()
                agent.agent.run = AsyncMock(return_value=mock_result)

                page_image_base64 = base64.b64encode(b"img").decode("utf-8")

                await agent.process_page(
                    page_num=1,
                    page_markdown="Test",
                    page_image_base64=page_image_base64
                )

                # Verify shared settings passed to Bedrock
                call_kwargs = agent.agent.run.call_args.kwargs
                assert call_kwargs["model_settings"]["max_tokens"] == 16000
                assert call_kwargs["model_settings"]["temperature"] == 0.5


@pytest.mark.asyncio
async def test_shared_claude_settings_anthropic():
    """Test shared Claude settings (max_tokens, temperature) work with Anthropic API."""
    with patch("src.agents.accessibility_agent.settings") as mock_settings:
        mock_settings.ai_provider = "anthropic"
        mock_settings.claude_model = "claude-3-5-haiku-20241022"
        mock_settings.claude_max_tokens = 12000
        mock_settings.claude_temperature = 0.7

        with patch("src.agents.accessibility_agent.Agent"):
            agent = AccessibilityAgent()

            mock_result = MagicMock()
            mock_result.output = PageImprovementResult(
                improved_markdown="Test", confidence_score=0.9, processing_notes="OK"
            )

            agent.agent = AsyncMock()
            agent.agent.run = AsyncMock(return_value=mock_result)

            page_image_base64 = base64.b64encode(b"img").decode("utf-8")

            await agent.process_page(
                page_num=1,
                page_markdown="Test",
                page_image_base64=page_image_base64
            )

            # Verify shared settings passed to Anthropic
            call_kwargs = agent.agent.run.call_args.kwargs
            assert call_kwargs["model_settings"]["max_tokens"] == 12000
            assert call_kwargs["model_settings"]["temperature"] == 0.7


def test_prompts_shared_across_providers():
    """Test prompt templates are loaded identically for both providers."""
    yaml_content = """
system_prompt: "Shared system prompt for testing"
user_prompt_template: "Shared user prompt: {page_markdown}"
"""

    # Test Bedrock
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("src.agents.accessibility_agent.settings") as mock_settings:
            mock_settings.ai_provider = "bedrock"
            mock_settings.bedrock_model_id = "anthropic.claude-3-5-haiku-20241022-v1:0"
            mock_settings.bedrock_region = "us-east-1"

            with patch("src.agents.accessibility_agent.Agent"):
                with patch("pydantic_ai.models.bedrock.BedrockConverseModel"):
                    bedrock_agent = AccessibilityAgent()

    # Test Anthropic
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("src.agents.accessibility_agent.settings") as mock_settings:
            mock_settings.ai_provider = "anthropic"
            mock_settings.claude_model = "claude-3-5-haiku-20241022"

            with patch("src.agents.accessibility_agent.Agent"):
                anthropic_agent = AccessibilityAgent()

    # Both should have same prompts
    assert bedrock_agent.user_prompt_template == anthropic_agent.user_prompt_template
    assert bedrock_agent.user_prompt_template == "Shared user prompt: {page_markdown}"
