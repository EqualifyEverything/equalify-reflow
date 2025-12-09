"""AI agents for document processing.

This module provides the multi-agent framework (PRD-011) including:
- BaseDocumentAgent: Abstract base class for all specialist agents
- AgentConfig: Configuration dataclass for agent initialization
- Agent registry functions for dynamic agent discovery
- FullDocumentAgent: Two-phase document extraction agent
"""

from .base_agent import AgentConfig, BaseDocumentAgent
from .full_document_agent import FullDocumentAgent
from .registry import (
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

__all__ = [
    # Base agent framework (PRD-011)
    "BaseDocumentAgent",
    "AgentConfig",
    # Registry functions
    "register_agent",
    "register_instance",
    "unregister_agent",
    "get_agent",
    "get_all_agents",
    "get_agents_for_correction_type",
    "get_registered_agent_names",
    "is_agent_registered",
    "clear_registry",
    # Full document extraction agent
    "FullDocumentAgent",
]
