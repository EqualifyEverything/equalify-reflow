"""AI agents for document processing.

This module provides the multi-agent framework including:
- BaseDocumentAgent: Abstract base class for all specialist agents
- AgentConfig: Configuration dataclass for agent initialization
- Agent registry functions for dynamic agent discovery
- AnalysisAgent: Document analysis agent
- ExtractionAgent: Manifest-guided extraction agent
- Specialized agents for accessibility analysis:
  - FiguresAgent: Image accessibility
  - TablesAgent: Table structure
  - StructureAgent: Heading hierarchy
  - TypographyAgent: Semantic typography
- AgentRouter: Routes specialized agents based on manifest
- ConsolidationAgent: Observations to proposals
"""

from .agent_router import AgentRouter
from .analysis_agent import AnalysisAgent
from .base_agent import AgentConfig, BaseDocumentAgent
from .consolidation_agent import ConsolidationAgent, ConsolidationOutput, ProposalDraft
from .extraction_agent import ExtractionAgent
from .figures_agent import FiguresAgent
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
from .specialized_models import (
    FiguresAnalysisOutput,
    ImageAnalysis,
    StructureAnalysisOutput,
    StructureIssue,
    TableAnalysis,
    TablesAnalysisOutput,
    TypographyAnalysisOutput,
    TypographyIssue,
)
from .structure_agent import StructureAgent
from .tables_agent import TablesAgent
from .typography_agent import TypographyAgent

__all__ = [
    # Base agent framework
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
    # Analysis and extraction agents
    "AnalysisAgent",
    "ExtractionAgent",
    # Specialized agents
    "AgentRouter",
    "FiguresAgent",
    "TablesAgent",
    "StructureAgent",
    "TypographyAgent",
    # Specialized agent output models
    "ImageAnalysis",
    "FiguresAnalysisOutput",
    "TableAnalysis",
    "TablesAnalysisOutput",
    "StructureIssue",
    "StructureAnalysisOutput",
    "TypographyIssue",
    "TypographyAnalysisOutput",
    # Consolidation agent
    "ConsolidationAgent",
    "ConsolidationOutput",
    "ProposalDraft",
]
