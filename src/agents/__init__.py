"""AI agents for document processing.

This module provides the multi-agent framework including:
- AgentConfig: Configuration dataclass for agent initialization
- Chained analysis pipeline (layout → doctype → headings/features/summary)
- ExtractionAgent: Manifest-guided extraction agent
- Specialized agents for accessibility analysis:
  - FiguresAgent: Image accessibility
  - TablesAgent: Table structure
  - StructureAgent: Heading hierarchy
  - TypographyAgent: Semantic typography
- SummaryAgent: Document summary for downstream context
- AgentRouter: Routes specialized agents based on manifest
"""

from .agent_router import AgentRouter
from .chained_analysis import analyze_document
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
from .summary_agent import DocumentSummaryOutput, SummaryAgent
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
    "SummaryAgent",
    # Specialized agent output models
    "ImageAnalysis",
    "FiguresAnalysisOutput",
    "TableAnalysis",
    "TablesAnalysisOutput",
    "StructureIssue",
    "StructureAnalysisOutput",
    "TypographyIssue",
    "TypographyAnalysisOutput",
    "DocumentSummaryOutput",
]
