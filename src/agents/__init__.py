"""AI agents for document processing.

This module provides the multi-agent framework including:
- AgentConfig: Configuration dataclass for agent initialization
- Chained analysis pipeline (layout → doctype → headings/features)
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
from .chained_analysis import analyze_document
from .consolidation_agent import ConsolidationAgent, ConsolidationOutput, ProposalDraft
from .core import AgentConfig
from .extraction_agent import ExtractionAgent
from .figures_agent import FiguresAgent
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
    # Agent configuration
    "AgentConfig",
    # Chained analysis and extraction
    "analyze_document",
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
