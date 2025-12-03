"""Agent registry for dynamic agent discovery and registration (PRD-011).

This module provides a centralized registry for managing specialist agents,
enabling dynamic discovery, lookup by name or correction type, and
controlled access to agent instances.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base_agent import BaseDocumentAgent

logger = logging.getLogger(__name__)

# Global registry storage
_agent_registry: dict[str, type["BaseDocumentAgent[Any]"]] = {}
_agent_instances: dict[str, "BaseDocumentAgent[Any]"] = {}


def register_agent(cls: type["BaseDocumentAgent[Any]"]) -> type["BaseDocumentAgent[Any]"]:
    """Class decorator to mark an agent class for registration.

    Note: This decorator only marks the class. Actual registration happens
    when an instance is created and register_instance() is called.

    Args:
        cls: The agent class to mark for registration

    Returns:
        The unmodified class (for decorator chaining)

    Example:
        >>> @register_agent
        ... class StructureAgent(BaseDocumentAgent[StructureOutput]):
        ...     pass
    """
    logger.debug(f"Agent class marked for registration: {cls.__name__}")
    return cls


def register_instance(agent: "BaseDocumentAgent[Any]") -> None:
    """Register an agent instance in the global registry.

    Stores both the agent's class and instance for later retrieval.
    Logs registration including the correction types the agent handles.

    Args:
        agent: The agent instance to register

    Raises:
        ValueError: If an agent with the same name is already registered

    Example:
        >>> agent = StructureAgent(config)
        >>> register_instance(agent)
        >>> get_agent("structure_agent")  # Returns the agent
    """
    if agent.name in _agent_instances:
        raise ValueError(
            f"Agent '{agent.name}' is already registered. "
            "Use clear_registry() first if re-registration is intended."
        )

    _agent_registry[agent.name] = type(agent)
    _agent_instances[agent.name] = agent
    logger.info(
        f"Registered agent: {agent.name} "
        f"(handles: {agent.correction_types})"
    )


def unregister_agent(name: str) -> bool:
    """Remove an agent from the registry by name.

    Args:
        name: The agent name to unregister

    Returns:
        True if agent was found and removed, False if not found
    """
    if name in _agent_instances:
        del _agent_instances[name]
        del _agent_registry[name]
        logger.info(f"Unregistered agent: {name}")
        return True
    return False


def get_agent(name: str) -> "BaseDocumentAgent[Any] | None":
    """Get an agent instance by its unique name.

    Args:
        name: The agent's unique identifier

    Returns:
        The agent instance if found, None otherwise

    Example:
        >>> agent = get_agent("structure_agent")
        >>> if agent:
        ...     output = await agent.process(input_data)
    """
    return _agent_instances.get(name)


def get_all_agents() -> list["BaseDocumentAgent[Any]"]:
    """Get all registered agent instances.

    Returns:
        List of all registered agent instances (may be empty)

    Example:
        >>> for agent in get_all_agents():
        ...     print(f"{agent.name}: {agent.correction_types}")
    """
    return list(_agent_instances.values())


def get_agents_for_correction_type(correction_type: str) -> list["BaseDocumentAgent[Any]"]:
    """Get all agents that handle a specific correction type.

    Useful for routing corrections to the appropriate specialist agent(s).

    Args:
        correction_type: The type of correction (e.g., "heading_level", "alt_text")

    Returns:
        List of agents that handle this correction type (may be empty)

    Example:
        >>> agents = get_agents_for_correction_type("heading_level")
        >>> # Returns [StructureAgent] if it handles heading_level corrections
    """
    return [
        agent for agent in _agent_instances.values()
        if correction_type in agent.correction_types
    ]


def get_registered_agent_names() -> list[str]:
    """Get names of all registered agents.

    Returns:
        List of agent names in registration order
    """
    return list(_agent_instances.keys())


def is_agent_registered(name: str) -> bool:
    """Check if an agent is registered by name.

    Args:
        name: The agent name to check

    Returns:
        True if agent is registered, False otherwise
    """
    return name in _agent_instances


def clear_registry() -> None:
    """Clear all registered agents from the registry.

    Primarily used for testing to ensure clean state between tests.
    Logs a warning when called to help debug unexpected clears.

    Example:
        >>> clear_registry()
        >>> assert len(get_all_agents()) == 0
    """
    count = len(_agent_instances)
    _agent_registry.clear()
    _agent_instances.clear()
    logger.warning(f"Agent registry cleared ({count} agents removed)")
