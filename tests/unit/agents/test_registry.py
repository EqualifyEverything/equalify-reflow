"""Tests for agent registry (PRD-011)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field
from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.registry import (
    clear_registry,
    get_agent,
    get_agents_for_correction_type,
    get_all_agents,
    get_registered_agent_names,
    is_agent_registered,
    register_agent,
    register_instance,
    unregister_agent,
)

from shared.models.agent_models import AgentInput


# Test output model
class MockOutput(BaseModel):
    """Mock output model for testing."""
    result: str = Field(default="test")


# Mock agent implementation
class MockAgent(BaseDocumentAgent[MockOutput]):
    """Mock agent for testing registry operations."""

    def _default_prompts(self) -> dict:
        return {"system_prompt": "Mock agent prompt"}

    async def process(self, input_data: AgentInput) -> MockOutput:
        return MockOutput(result="processed")


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear registry before and after each test."""
    clear_registry()
    yield
    clear_registry()


def create_mock_agent(
    name: str = "test_agent",
    correction_types: list[str] | None = None
) -> MockAgent:
    """Helper to create mock agents with mocked Bedrock and YAML."""
    if correction_types is None:
        correction_types = ["other"]

    with patch("pydantic_ai.models.bedrock.BedrockConverseModel") as mock_bedrock, \
         patch("src.agents.base_agent.Agent") as mock_agent_class, \
         patch("src.agents.base_agent.yaml.safe_load", return_value={"system_prompt": "test"}), \
         patch("builtins.open", create=True):
        mock_bedrock.return_value = MagicMock()
        mock_agent_class.return_value = MagicMock()

        config = AgentConfig(
            name=name,
            prompts_file=Path("test.yaml"),
            output_type=MockOutput,
            correction_types=correction_types
        )
        return MockAgent(config)


@pytest.mark.unit
class TestRegisterInstance:
    """Test register_instance function."""

    def test_register_single_agent(self):
        """Test registering a single agent."""
        agent = create_mock_agent("single_agent", ["heading_level"])
        register_instance(agent)

        assert is_agent_registered("single_agent")
        assert get_agent("single_agent") is agent

    def test_register_multiple_agents(self):
        """Test registering multiple different agents."""
        agent1 = create_mock_agent("agent_one", ["heading_level"])
        agent2 = create_mock_agent("agent_two", ["table_format"])
        agent3 = create_mock_agent("agent_three", ["alt_text"])

        register_instance(agent1)
        register_instance(agent2)
        register_instance(agent3)

        assert len(get_all_agents()) == 3
        assert is_agent_registered("agent_one")
        assert is_agent_registered("agent_two")
        assert is_agent_registered("agent_three")

    def test_register_duplicate_name_raises_error(self):
        """Test that registering duplicate name raises ValueError."""
        agent1 = create_mock_agent("duplicate_name")
        agent2 = create_mock_agent("duplicate_name")

        register_instance(agent1)

        with pytest.raises(ValueError) as exc_info:
            register_instance(agent2)

        assert "already registered" in str(exc_info.value)
        assert "duplicate_name" in str(exc_info.value)


@pytest.mark.unit
class TestGetAgent:
    """Test get_agent function."""

    def test_get_registered_agent(self):
        """Test retrieving a registered agent."""
        agent = create_mock_agent("retrievable")
        register_instance(agent)

        retrieved = get_agent("retrievable")
        assert retrieved is agent

    def test_get_unregistered_agent_returns_none(self):
        """Test that getting unregistered agent returns None."""
        result = get_agent("nonexistent")
        assert result is None

    def test_get_agent_case_sensitive(self):
        """Test that agent names are case-sensitive."""
        agent = create_mock_agent("CaseSensitive")
        register_instance(agent)

        assert get_agent("CaseSensitive") is agent
        assert get_agent("casesensitive") is None
        assert get_agent("CASESENSITIVE") is None


@pytest.mark.unit
class TestGetAllAgents:
    """Test get_all_agents function."""

    def test_get_all_agents_empty(self):
        """Test get_all_agents with empty registry."""
        agents = get_all_agents()
        assert agents == []

    def test_get_all_agents_returns_list(self):
        """Test that get_all_agents returns a list."""
        agent1 = create_mock_agent("list_agent_1")
        agent2 = create_mock_agent("list_agent_2")

        register_instance(agent1)
        register_instance(agent2)

        agents = get_all_agents()
        assert isinstance(agents, list)
        assert len(agents) == 2
        assert agent1 in agents
        assert agent2 in agents


@pytest.mark.unit
class TestGetAgentsForCorrectionType:
    """Test get_agents_for_correction_type function."""

    def test_get_agents_for_single_type(self):
        """Test getting agents for a specific correction type."""
        agent1 = create_mock_agent("heading_agent", ["heading_level"])
        agent2 = create_mock_agent("table_agent", ["table_format"])
        agent3 = create_mock_agent("mixed_agent", ["heading_level", "list_structure"])

        register_instance(agent1)
        register_instance(agent2)
        register_instance(agent3)

        heading_agents = get_agents_for_correction_type("heading_level")
        assert len(heading_agents) == 2
        assert agent1 in heading_agents
        assert agent3 in heading_agents
        assert agent2 not in heading_agents

    def test_get_agents_for_nonexistent_type(self):
        """Test getting agents for a type no agent handles."""
        agent = create_mock_agent("limited_agent", ["heading_level"])
        register_instance(agent)

        agents = get_agents_for_correction_type("nonexistent_type")
        assert agents == []

    def test_get_agents_for_type_empty_registry(self):
        """Test getting agents for type with empty registry."""
        agents = get_agents_for_correction_type("any_type")
        assert agents == []


@pytest.mark.unit
class TestGetRegisteredAgentNames:
    """Test get_registered_agent_names function."""

    def test_get_names_empty(self):
        """Test getting names from empty registry."""
        names = get_registered_agent_names()
        assert names == []

    def test_get_names_returns_list(self):
        """Test that function returns list of names."""
        agent1 = create_mock_agent("name_test_1")
        agent2 = create_mock_agent("name_test_2")

        register_instance(agent1)
        register_instance(agent2)

        names = get_registered_agent_names()
        assert isinstance(names, list)
        assert "name_test_1" in names
        assert "name_test_2" in names


@pytest.mark.unit
class TestIsAgentRegistered:
    """Test is_agent_registered function."""

    def test_is_registered_true(self):
        """Test checking registered agent."""
        agent = create_mock_agent("check_agent")
        register_instance(agent)

        assert is_agent_registered("check_agent") is True

    def test_is_registered_false(self):
        """Test checking unregistered agent."""
        assert is_agent_registered("not_registered") is False


@pytest.mark.unit
class TestUnregisterAgent:
    """Test unregister_agent function."""

    def test_unregister_existing_agent(self):
        """Test unregistering an existing agent."""
        agent = create_mock_agent("to_remove")
        register_instance(agent)

        assert is_agent_registered("to_remove")

        result = unregister_agent("to_remove")

        assert result is True
        assert not is_agent_registered("to_remove")
        assert get_agent("to_remove") is None

    def test_unregister_nonexistent_agent(self):
        """Test unregistering non-existent agent returns False."""
        result = unregister_agent("never_existed")
        assert result is False


@pytest.mark.unit
class TestClearRegistry:
    """Test clear_registry function."""

    def test_clear_registry_removes_all(self):
        """Test that clear_registry removes all agents."""
        agent1 = create_mock_agent("clear_test_1")
        agent2 = create_mock_agent("clear_test_2")

        register_instance(agent1)
        register_instance(agent2)

        assert len(get_all_agents()) == 2

        clear_registry()

        assert len(get_all_agents()) == 0
        assert not is_agent_registered("clear_test_1")
        assert not is_agent_registered("clear_test_2")

    def test_clear_empty_registry(self):
        """Test clearing an already empty registry."""
        # Should not raise an error
        clear_registry()
        assert len(get_all_agents()) == 0


@pytest.mark.unit
class TestRegisterAgentDecorator:
    """Test register_agent decorator."""

    def test_decorator_returns_class(self):
        """Test that decorator returns the class unchanged."""

        @register_agent
        class DecoratedAgent(BaseDocumentAgent[MockOutput]):
            def _default_prompts(self) -> dict:
                return {"system_prompt": "test"}

            async def process(self, input_data: AgentInput) -> MockOutput:
                return MockOutput()

        # Class should be returned unchanged
        assert DecoratedAgent is not None
        assert issubclass(DecoratedAgent, BaseDocumentAgent)

    def test_decorator_does_not_auto_register(self):
        """Test that decorator alone does not register the agent."""

        @register_agent
        class NotAutoRegistered(BaseDocumentAgent[MockOutput]):
            def _default_prompts(self) -> dict:
                return {"system_prompt": "test"}

            async def process(self, input_data: AgentInput) -> MockOutput:
                return MockOutput()

        # Just decorating should not register
        assert not is_agent_registered("NotAutoRegistered")
        assert len(get_all_agents()) == 0


@pytest.mark.unit
class TestRegistryIntegration:
    """Integration tests for registry operations."""

    def test_full_lifecycle(self):
        """Test complete agent lifecycle: register, query, unregister."""
        # Create agents with different capabilities
        structure_agent = create_mock_agent("structure", ["heading_level", "list_structure"])
        figures_agent = create_mock_agent("figures", ["alt_text", "figure_caption"])
        tables_agent = create_mock_agent("tables", ["table_format", "table_header"])

        # Register all
        register_instance(structure_agent)
        register_instance(figures_agent)
        register_instance(tables_agent)

        # Verify all registered
        assert len(get_all_agents()) == 3
        names = get_registered_agent_names()
        assert set(names) == {"structure", "figures", "tables"}

        # Query by type
        heading_handlers = get_agents_for_correction_type("heading_level")
        assert len(heading_handlers) == 1
        assert heading_handlers[0].name == "structure"

        alt_text_handlers = get_agents_for_correction_type("alt_text")
        assert len(alt_text_handlers) == 1
        assert alt_text_handlers[0].name == "figures"

        # Unregister one
        unregister_agent("figures")
        assert len(get_all_agents()) == 2
        assert not is_agent_registered("figures")
        assert len(get_agents_for_correction_type("alt_text")) == 0

        # Clear all
        clear_registry()
        assert len(get_all_agents()) == 0
