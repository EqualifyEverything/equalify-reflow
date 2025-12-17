"""AI agents for document processing.

This module provides the multi-agent framework including:
- AgentConfig: Configuration dataclass for agent initialization
- Chained analysis pipeline (layout → doctype → headings/features/summary)
- ExtractionAgent: Manifest-guided extraction agent
- Specialized agents for accessibility analysis:
  - FiguresAgent: Image accessibility (Phase 3b refactored)
  - TablesAgent: Table structure
  - StructureAgent: Heading hierarchy
  - TypographyAgent: Semantic typography
- SummaryAgent: Document summary for downstream context
- AgentRouter: Routes specialized agents based on manifest

Reference: PRD-023 - FiguresAgent now outputs AgentResult directly
"""

from .agent_router import AgentRouter
from .chained_analysis import analyze_document
from .core import AgentConfig
from .extraction_agent import ExtractionAgent
from .figures import FiguresAgent  # Refactored in PRD-023
from .specialized_models import (
    StructureAnalysisOutput,
    StructureIssue,
    TableAnalysis,
    TablesAnalysisOutput,
)
from .structure_agent import StructureAgent
from .summary_agent import DocumentSummaryOutput, SummaryAgent
from .tables_agent import TablesAgent

# PRD-025: Enhanced typography agent with AgentResult output
from .typography import (
    FormattingIssue,
    OCRDecision,
    TypographyAgent,
    TypographyAnalysisOutput,
)

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
    "TableAnalysis",
    "TablesAnalysisOutput",
    "StructureIssue",
    "StructureAnalysisOutput",
    "DocumentSummaryOutput",
    # PRD-025: Enhanced typography models
    "FormattingIssue",
    "OCRDecision",
    "TypographyAnalysisOutput",
]
