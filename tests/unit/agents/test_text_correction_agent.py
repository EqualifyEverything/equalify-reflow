"""Tests for TextCorrectionAgent (Refactored)."""

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.text_correction_agent import TextCorrectionAgent, get_text_correction_agent
from src.shared.models.agent_models import AgentInput
from src.shared.models.processing import LLMUsage, PageCorrectionResult, TextCorrection


@pytest.mark.unit
class TestTextCorrectionAgent:
    """Test TextCorrectionAgent implementation."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_initialization(self, mock_agent_class, mock_bedrock):
        """Test that agent initializes correctly with config."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        agent = TextCorrectionAgent(
            config=MagicMock(
                name="test",
                prompts_file=Path("dummy.yaml"),
                output_type=PageCorrectionResult,
                correction_types=[],
                max_retries=2,
                temperature=0.2
            )
        )

        assert agent is not None
        assert isinstance(agent, TextCorrectionAgent)

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_default_prompts(self, mock_agent_class, mock_bedrock):
        """Test default prompts are provided."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        # Initialize with a config that will trigger default prompts (mock file not found behavior if needed,
        # but BaseDocumentAgent handles that. Here we just check the method directly or via init)

        # We can just instantiate and check the method directly since it's what we implemented
        config = MagicMock(prompts_file=Path("dummy.yaml"), output_type=PageCorrectionResult)
        agent = TextCorrectionAgent(config)
        prompts = agent._default_prompts()

        assert "system_prompt" in prompts
        assert "user_prompt_template" in prompts
        assert "text correction expert" in prompts["system_prompt"]

    @patch("src.agents.text_correction_agent.TextCorrectionAgent._load_prompts")
    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    @pytest.mark.asyncio
    async def test_process_method(self, mock_agent_class, mock_bedrock, mock_load_prompts):
        """Test the process method."""
        mock_bedrock.return_value = MagicMock()
        
        # Mock prompts
        mock_load_prompts.return_value = {
            "system_prompt": "Test system prompt",
            "user_prompt_template": "Test template: {page_markdown}"
        }

        # Mock agent run result
        mock_result = MagicMock()
        mock_result.output = PageCorrectionResult(
            visual_observations=["Test observation"],
            corrections=[
                TextCorrection(
                    correction_type="heading_level",
                    original_text="Test",
                    corrected_text="# Test",
                    confidence=0.9,
                    explanation="Fixing heading"
                )
            ],
            overall_confidence=0.9,
            processing_notes="Test notes",
            page_number=1,
            usage=LLMUsage(input_tokens=100, output_tokens=50, cost_cents=0.1)
        )
        mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent_class.return_value = mock_agent

        # Reset singleton for test isolation
        import src.agents.text_correction_agent
        src.agents.text_correction_agent._agent_instance = None
        agent = get_text_correction_agent()

        # Prepare input
        input_data = AgentInput(
            page_number=1,
            page_markdown="Test content",
            page_image_base64=base64.b64encode(b"fake_image").decode("utf-8")
        )

        # Run process
        result = await agent.process(input_data)

        assert isinstance(result, PageCorrectionResult)
        assert len(result.corrections) == 1
        assert result.corrections[0].correction_type == "heading_level"
        assert result.page_number == 1
