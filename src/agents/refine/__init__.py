"""Unified refine agent for PDF-to-markdown refinement.

This package implements a single agent with tool use that replaces
the previous specialized agents (Figures, Tables, Typography).

V3 Correction Pipeline Agents:
- AnalysisAgent: Detects issues (WHAT is wrong, WHERE)
- ExecutionAgent: Applies fixes based on analysis
"""

from src.agents.refine.analysis_agent import (
    AnalysisResult,
    AnalysisUsage,
    PageIssue,
    analyze_document_pages,
    analyze_page,
    reset_analysis_agent,
)
from src.agents.refine.execution_agent import (
    ExecutionDeps,
    ExecutionResult,
    ExecutionUsage,
    execute_corrections,
    execute_single_issue,
    reset_execution_agent,
)
from src.agents.refine.models import (
    Correction,
    EditPatch,
    EditType,
    Hotspot,
    ProcessingTrace,
    RefineResult,
    Requirements,
)
from src.agents.refine.refine_agent import refine_document, refine_page
from src.agents.refine.tools import RefineToolDeps
from src.agents.refine.verification_agent import (
    VerificationIssue,
    VerificationResult,
    VerificationUsage,
    reset_verification_agent,
    summarize_verification,
    verify_document,
    verify_page,
)

__all__ = [
    # V3 Analysis Agent
    "AnalysisResult",
    "AnalysisUsage",
    "PageIssue",
    "analyze_document_pages",
    "analyze_page",
    "reset_analysis_agent",
    # V3 Execution Agent
    "ExecutionDeps",
    "ExecutionResult",
    "ExecutionUsage",
    "execute_corrections",
    "execute_single_issue",
    "reset_execution_agent",
    # Models
    "Correction",
    "EditPatch",
    "EditType",
    "Hotspot",
    "ProcessingTrace",
    "RefineResult",
    "Requirements",
    "RefineToolDeps",
    # Original Refine Agent
    "refine_document",
    "refine_page",
    # Verification Agent
    "VerificationIssue",
    "VerificationResult",
    "VerificationUsage",
    "reset_verification_agent",
    "summarize_verification",
    "verify_document",
    "verify_page",
]
