"""Unit tests for AccessibilityAgent.

Tests PydanticAI agent initialization, prompt loading, and page processing.
All Claude API calls are mocked for fast, deterministic unit tests.
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open, AsyncMock
from pathlib import Path
import base64

from src.agents.accessibility_agent import (
    AccessibilityAgent,
    PageImprovementResult,
    get_accessibility_agent,
)


pytestmark = pytest.mark.unit


# ============================================================================
# Initialization Tests (5 tests)
# ============================================================================


def test_accessibility_agent_initialization():
    """Test AccessibilityAgent initializes with Claude model."""
    with patch("src.agents.accessibility_agent.Agent") as MockAgent:
        with patch.object(AccessibilityAgent, "_load_prompts", return_value={
            "system_prompt": "System prompt",
            "user_prompt_template": "User prompt {page_markdown}"
        }):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-sonnet-20241022"

                agent = AccessibilityAgent()

                # Should create PydanticAI agent with correct model
                MockAgent.assert_called_once()
                call_args = MockAgent.call_args
                # For Anthropic, model is a string
                assert isinstance(call_args[0][0], str)
                assert "anthropic:" in call_args[0][0]
                assert call_args.kwargs["output_type"] == PageImprovementResult
                assert call_args.kwargs["system_prompt"] == "System prompt"


def test_accessibility_agent_loads_prompts_from_yaml():
    """Test agent loads prompts from YAML file."""
    yaml_content = b"""
system_prompt: "Test system prompt"
user_prompt_template: "Test user prompt: {page_markdown}"
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch("src.agents.accessibility_agent.Agent"):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-sonnet-20241022"

                agent = AccessibilityAgent()

                # Should load prompts from YAML
                assert agent.user_prompt_template == "Test user prompt: {page_markdown}"


def test_accessibility_agent_uses_fallback_prompts_if_yaml_missing():
    """Test agent uses default prompts if YAML file not found."""
    with patch("builtins.open", side_effect=FileNotFoundError()):
        with patch("src.agents.accessibility_agent.Agent"):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-sonnet-20241022"

                agent = AccessibilityAgent()

                # Should have fallback prompts
                assert "accessibility specialist" in agent.user_prompt_template.lower() or \
                       "improve" in agent.user_prompt_template.lower()


def test_accessibility_agent_system_prompt_configured():
    """Test system prompt is passed to PydanticAI agent."""
    test_system_prompt = "Custom system prompt for testing"

    with patch("src.agents.accessibility_agent.Agent") as MockAgent:
        with patch.object(AccessibilityAgent, "_load_prompts", return_value={
            "system_prompt": test_system_prompt,
            "user_prompt_template": "User: {page_markdown}"
        }):
            agent = AccessibilityAgent()

            # Should pass system prompt to Agent
            assert MockAgent.call_args.kwargs["system_prompt"] == test_system_prompt


def test_accessibility_agent_model_settings_from_config():
    """Test agent uses model from config."""
    with patch("src.agents.accessibility_agent.Agent") as MockAgent:
        with patch.object(AccessibilityAgent, "_load_prompts", return_value={
            "system_prompt": "System",
            "user_prompt_template": "User: {page_markdown}"
        }):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-haiku-20241022"

                agent = AccessibilityAgent()

                # Should use model from settings
                model_arg = MockAgent.call_args[0][0]
                assert "claude-3-5-haiku-20241022" in model_arg


# ============================================================================
# process_page Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_page_success():
    """Test successful page processing with multimodal input."""
    with patch("src.agents.accessibility_agent.Agent"):
        agent = AccessibilityAgent()

        # Mock agent.run to return result
        mock_result = MagicMock()
        mock_result.output = PageImprovementResult(
            improved_markdown="# Improved Page\n\nContent",
            confidence_score=0.92,
            processing_notes="Added alt text"
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
        assert result.improved_markdown == "# Improved Page\n\nContent"
        assert result.confidence_score == 0.92
        assert result.processing_notes == "Added alt text"

        # Should have called agent.run
        agent.agent.run.assert_called_once()


@pytest.mark.asyncio
async def test_process_page_multimodal_input_formatting():
    """Test multimodal input (text + image) is formatted correctly."""
    with patch("src.agents.accessibility_agent.Agent"):
        agent = AccessibilityAgent()
        agent.user_prompt_template = "Process: {page_markdown}"

        mock_result = MagicMock()
        mock_result.output = PageImprovementResult(
            improved_markdown="Improved", confidence_score=0.9, processing_notes="OK"
        )

        agent.agent = AsyncMock()
        agent.agent.run = AsyncMock(return_value=mock_result)

        page_markdown = "Test content"
        page_image_base64 = base64.b64encode(b"image_bytes").decode("utf-8")

        await agent.process_page(
            page_num=1,
            page_markdown=page_markdown,
            page_image_base64=page_image_base64
        )

        # Verify agent.run called with correct multimodal input
        call_args = agent.agent.run.call_args
        messages = call_args[0][0]

        # Should have text message and binary content
        assert len(messages) == 2
        assert "Process: Test content" in messages[0]

        # Second message should be BinaryContent
        from pydantic_ai.messages import BinaryContent
        assert isinstance(messages[1], BinaryContent)
        assert messages[1].media_type == "image/png"


@pytest.mark.asyncio
async def test_process_page_model_settings_passed():
    """Test model settings (max_tokens, temperature) are passed correctly."""
    with patch("src.agents.accessibility_agent.Agent"):
        agent = AccessibilityAgent()

        mock_result = MagicMock()
        mock_result.output = PageImprovementResult(
            improved_markdown="Improved", confidence_score=0.9, processing_notes="OK"
        )

        agent.agent = AsyncMock()
        agent.agent.run = AsyncMock(return_value=mock_result)

        with patch("src.agents.accessibility_agent.settings") as mock_settings:
            mock_settings.claude_max_tokens = 4096
            mock_settings.claude_temperature = 0.3

            page_image_base64 = base64.b64encode(b"img").decode("utf-8")

            await agent.process_page(
                page_num=1,
                page_markdown="Test",
                page_image_base64=page_image_base64
            )

            # Verify model_settings passed
            call_kwargs = agent.agent.run.call_args.kwargs
            assert "model_settings" in call_kwargs
            assert call_kwargs["model_settings"]["max_tokens"] == 4096
            assert call_kwargs["model_settings"]["temperature"] == 0.3


@pytest.mark.asyncio
async def test_process_page_error_handling():
    """Test exception handling when Claude API fails."""
    with patch("src.agents.accessibility_agent.Agent"):
        agent = AccessibilityAgent()

        # Mock agent.run to raise exception
        agent.agent = AsyncMock()
        agent.agent.run = AsyncMock(side_effect=Exception("Claude API rate limit"))

        page_image_base64 = base64.b64encode(b"img").decode("utf-8")

        # Should raise exception
        with pytest.raises(Exception, match="Claude API rate limit"):
            await agent.process_page(
                page_num=1,
                page_markdown="Test",
                page_image_base64=page_image_base64
            )


# ============================================================================
# Prompt Loading Tests (2 tests)
# ============================================================================


def test_load_prompts_reads_yaml_file():
    """Test _load_prompts reads from correct YAML file path."""
    yaml_content = b"""
system_prompt: "YAML system prompt"
user_prompt_template: "YAML user: {page_markdown}"
"""
    with patch("builtins.open", mock_open(read_data=yaml_content)) as mock_file:
        with patch("src.agents.accessibility_agent.Agent"):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-sonnet-20241022"

                agent = AccessibilityAgent()

                # Should have opened prompts file
                mock_file.assert_called()
                # Check file path contains accessibility_prompts.yaml
                call_args = str(mock_file.call_args)
                assert "accessibility_prompts.yaml" in call_args


def test_default_prompts_structure():
    """Test _default_prompts returns correctly structured dict."""
    with patch("builtins.open", side_effect=FileNotFoundError()):
        with patch("src.agents.accessibility_agent.Agent"):
            with patch("src.agents.accessibility_agent.settings") as mock_settings:
                mock_settings.ai_provider = "anthropic"
                mock_settings.claude_model = "claude-3-5-sonnet-20241022"

                agent = AccessibilityAgent()

                # Get default prompts
                prompts = agent._default_prompts()

                # Should have required keys
                assert "system_prompt" in prompts
                assert "user_prompt_template" in prompts

                # Prompts should contain relevant keywords
                assert isinstance(prompts["system_prompt"], str)
                assert isinstance(prompts["user_prompt_template"], str)
                assert "{page_markdown}" in prompts["user_prompt_template"]


# ============================================================================
# Singleton Pattern Test (1 test)
# ============================================================================


def test_get_accessibility_agent_singleton():
    """Test get_accessibility_agent returns same instance."""
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
