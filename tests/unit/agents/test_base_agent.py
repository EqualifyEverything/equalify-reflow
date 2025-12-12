"""Tests for BaseDocumentAgent abstract class (PRD-011)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field
from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.shared.llm_cost import DEFAULT_PRICING
from src.shared.models.agent_models import AgentInput, LLMUsage


# Test output model for concrete agent implementation
# Named with underscore prefix to avoid pytest collection (PytestCollectionWarning)
class _TestOutput(BaseModel):
    """Simple output model for testing."""
    message: str = Field(..., description="Test message")
    confidence: float = Field(..., ge=0.0, le=1.0)


# Concrete implementation for testing
class ConcreteTestAgent(BaseDocumentAgent[_TestOutput]):
    """Concrete implementation of BaseDocumentAgent for testing."""

    def _default_prompts(self) -> dict:
        return {
            "system_prompt": "You are a test agent.",
            "user_prompt_template": "Process this: {content}"
        }

    async def process(self, input_data: AgentInput) -> _TestOutput:
        """Simple implementation that returns mock output."""
        return _TestOutput(message="Test result", confidence=0.9)


@pytest.mark.unit
class TestAgentConfig:
    """Test AgentConfig dataclass."""

    def test_agent_config_required_fields(self):
        """Test creation with required fields only."""
        config = AgentConfig(
            name="test_agent",
            prompts_file=Path("test_prompts.yaml"),
            output_type=_TestOutput,
            correction_types=["heading_level"]
        )
        assert config.name == "test_agent"
        assert config.prompts_file == Path("test_prompts.yaml")
        assert config.output_type == _TestOutput
        assert config.correction_types == ["heading_level"]
        # Check defaults
        assert config.max_retries == 2
        assert config.temperature == 0.2

    def test_agent_config_custom_values(self):
        """Test creation with custom optional values."""
        config = AgentConfig(
            name="custom_agent",
            prompts_file=Path("prompts.yaml"),
            output_type=_TestOutput,
            correction_types=["spelling", "emphasis"],
            max_retries=5,
            temperature=0.5
        )
        assert config.max_retries == 5
        assert config.temperature == 0.5
        assert len(config.correction_types) == 2

    def test_agent_config_empty_correction_types(self):
        """Test creation with empty correction types list."""
        config = AgentConfig(
            name="no_corrections",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        assert config.correction_types == []


@pytest.mark.unit
class TestBaseDocumentAgentProperties:
    """Test BaseDocumentAgent properties."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_name_property(self, mock_agent_class, mock_bedrock):
        """Test name property returns config name."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="my_test_agent",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=["other"]
        )
        agent = ConcreteTestAgent(config)
        assert agent.name == "my_test_agent"

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_correction_types_property(self, mock_agent_class, mock_bedrock):
        """Test correction_types property returns config types."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=["heading_level", "list_structure"]
        )
        agent = ConcreteTestAgent(config)
        assert agent.correction_types == ["heading_level", "list_structure"]


@pytest.mark.unit
class TestPromptLoading:
    """Test YAML prompt loading functionality."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_load_prompts_from_yaml(self, mock_agent_class, mock_bedrock, tmp_path):
        """Test loading prompts from YAML file."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        # Create temp YAML file
        prompts_file = tmp_path / "test_prompts.yaml"
        prompts_file.write_text(
            "system_prompt: 'Custom system prompt'\n"
            "user_prompt_template: 'Custom user template: {content}'"
        )

        config = AgentConfig(
            name="yaml_test",
            prompts_file=prompts_file,
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        assert agent.prompts["system_prompt"] == "Custom system prompt"
        assert "Custom user template" in agent.prompts["user_prompt_template"]

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_load_prompts_fallback_to_defaults(self, mock_agent_class, mock_bedrock):
        """Test fallback to default prompts when file not found."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="fallback_test",
            prompts_file=Path("nonexistent_file.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        # Should use default prompts from _default_prompts()
        assert agent.prompts["system_prompt"] == "You are a test agent."
        assert "Process this" in agent.prompts["user_prompt_template"]

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    @patch("src.agents.base_agent.settings")
    def test_load_prompts_relative_to_config_dir(
        self, mock_settings, mock_agent_class, mock_bedrock, tmp_path
    ):
        """Test relative paths are resolved against agent_prompts_dir."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        # Create config/agents directory structure
        agents_dir = tmp_path / "config" / "agents"
        agents_dir.mkdir(parents=True)
        prompts_file = agents_dir / "relative_prompts.yaml"
        prompts_file.write_text("system_prompt: 'Relative prompt'")

        mock_settings.agent_prompts_dir = str(agents_dir)
        mock_settings.claude_max_tokens = 4096

        config = AgentConfig(
            name="relative_test",
            prompts_file=Path("relative_prompts.yaml"),  # Relative path
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        assert agent.prompts["system_prompt"] == "Relative prompt"


@pytest.mark.unit
class TestCostCalculation:
    """Test token cost calculation."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_calculate_estimated_cost_basic(self, mock_agent_class, mock_bedrock):
        """Test basic cost calculation with Claude Haiku 4.5 pricing."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="cost_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        # Haiku 4.5 pricing from DEFAULT_PRICING: input $1.00/1M, output $5.00/1M
        # In cents: input 0.0001/token, output 0.0005/token
        cost = agent._calculate_estimated_cost(input_tokens=1000, output_tokens=100)

        expected_input_cost = 1000 * DEFAULT_PRICING.input_cost_per_token_cents  # 0.1 cents
        expected_output_cost = 100 * DEFAULT_PRICING.output_cost_per_token_cents  # 0.05 cents
        expected_total = expected_input_cost + expected_output_cost  # 0.15 cents

        assert cost == pytest.approx(expected_total)

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_calculate_estimated_cost_zero_tokens(self, mock_agent_class, mock_bedrock):
        """Test cost calculation with zero tokens."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="zero_cost_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        cost = agent._calculate_estimated_cost(input_tokens=0, output_tokens=0)
        assert cost == 0.0

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_calculate_estimated_cost_large_tokens(self, mock_agent_class, mock_bedrock):
        """Test cost calculation with large token counts."""
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name="large_cost_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        # 1 million tokens each
        cost = agent._calculate_estimated_cost(input_tokens=1_000_000, output_tokens=1_000_000)

        # Expected: $1.00 (input) + $5.00 (output) = $6.00 = 600 cents
        assert cost == pytest.approx(600.0)


@pytest.mark.unit
class TestAgentCreation:
    """Test PydanticAI agent creation."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    def test_agent_created_with_correct_params(self, mock_agent_class, mock_bedrock):
        """Test that PydanticAI Agent is created with correct parameters on first use."""
        mock_model = MagicMock()
        mock_bedrock.return_value = mock_model
        mock_agent = MagicMock()
        mock_agent_class.return_value = mock_agent

        config = AgentConfig(
            name="creation_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[],
            max_retries=3,
            temperature=0.3
        )
        agent = ConcreteTestAgent(config)

        # Agent is lazy initialized - should not be created yet
        assert agent._agent is None

        # Trigger lazy initialization by calling _get_agent()
        agent._get_agent()

        # Verify Agent was called with correct params
        mock_agent_class.assert_called_once()
        call_kwargs = mock_agent_class.call_args
        assert call_kwargs[1]["output_type"] == _TestOutput
        assert call_kwargs[1]["retries"] == 3
        assert "system_prompt" in call_kwargs[1]


@pytest.mark.unit
class TestRunAgent:
    """Test _run_agent method."""

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    @pytest.mark.asyncio
    async def test_run_agent_text_only(self, mock_agent_class, mock_bedrock):
        """Test running agent with text-only input."""
        mock_bedrock.return_value = MagicMock()

        # Mock the agent's run method
        mock_result = MagicMock()
        mock_result.output = _TestOutput(message="Result", confidence=0.85)
        mock_result.usage.return_value = MagicMock(input_tokens=100, output_tokens=50)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent_class.return_value = mock_agent

        config = AgentConfig(
            name="run_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        output, usage = await agent._run_agent("Test message")

        assert output.message == "Result"
        assert output.confidence == 0.85
        assert isinstance(usage, LLMUsage)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    @patch("pydantic_ai.models.bedrock.BedrockConverseModel")
    @patch("src.agents.base_agent.Agent")
    @pytest.mark.asyncio
    async def test_run_agent_with_image(self, mock_agent_class, mock_bedrock):
        """Test running agent with image input."""
        mock_bedrock.return_value = MagicMock()

        mock_result = MagicMock()
        mock_result.output = _TestOutput(message="Image result", confidence=0.90)
        mock_result.usage.return_value = MagicMock(input_tokens=500, output_tokens=100)

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_result)
        mock_agent_class.return_value = mock_agent

        config = AgentConfig(
            name="image_test",
            prompts_file=Path("test.yaml"),
            output_type=_TestOutput,
            correction_types=[]
        )
        agent = ConcreteTestAgent(config)

        image_bytes = b"fake_image_data"
        output, usage = await agent._run_agent("Analyze this image", image_bytes)

        assert output.message == "Image result"
        # Verify image was passed to agent
        call_args = mock_agent.run.call_args
        messages = call_args[0][0]
        assert len(messages) == 2  # Text message + BinaryContent


@pytest.mark.unit
class TestAbstractMethods:
    """Test that abstract methods must be implemented."""

    def test_cannot_instantiate_base_class(self):
        """Test that BaseDocumentAgent cannot be instantiated directly."""
        with pytest.raises(TypeError) as exc_info:
            config = AgentConfig(
                name="test",
                prompts_file=Path("test.yaml"),
                output_type=_TestOutput,
                correction_types=[]
            )
            BaseDocumentAgent(config)

        assert "abstract" in str(exc_info.value).lower()

    def test_subclass_must_implement_default_prompts(self):
        """Test that subclass must implement _default_prompts."""

        class IncompleteAgent(BaseDocumentAgent[_TestOutput]):
            async def process(self, input_data: AgentInput) -> _TestOutput:
                return _TestOutput(message="test", confidence=0.5)

        with pytest.raises(TypeError) as exc_info:
            config = AgentConfig(
                name="incomplete",
                prompts_file=Path("test.yaml"),
                output_type=_TestOutput,
                correction_types=[]
            )
            IncompleteAgent(config)

        assert "abstract" in str(exc_info.value).lower()

    def test_subclass_must_implement_process(self):
        """Test that subclass must implement process."""

        class IncompleteAgent(BaseDocumentAgent[_TestOutput]):
            def _default_prompts(self) -> dict:
                return {"system_prompt": "test"}

        with pytest.raises(TypeError) as exc_info:
            config = AgentConfig(
                name="incomplete",
                prompts_file=Path("test.yaml"),
                output_type=_TestOutput,
                correction_types=[]
            )
            IncompleteAgent(config)

        assert "abstract" in str(exc_info.value).lower()
