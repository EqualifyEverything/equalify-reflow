"""AI agents for document processing.

This module provides the multi-agent framework (PRD-011) including:
- BaseDocumentAgent: Abstract base class for all specialist agents
- AgentConfig: Configuration dataclass for agent initialization
- Agent registry functions for dynamic agent discovery
- Existing TextCorrectionAgent for backward compatibility
"""

from .base_agent import AgentConfig, BaseDocumentAgent
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
from .text_correction_agent import TextCorrectionAgent, get_text_correction_agent

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
    # Existing agents (backward compatibility)
    "TextCorrectionAgent",
    "get_text_correction_agent",
]
