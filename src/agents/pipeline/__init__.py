"""Pipeline-based document processing.

This module implements a deterministic traversal approach with LLM-assisted
decision points, replacing the agentic refinement approach.

Key insight: We know the task structure upfront:
- Every figure needs alt text
- Every table needs semantic labeling
- Headings need hierarchy validation
- Formulas get LaTeX transcription
- Code blocks get proper formatting

These are deterministic traversals with LLM-assisted transformations,
not agent decisions.
"""

from .analyzer import DocumentAnalysis, PageAnalysis, analyze_document, analyze_page
from .footnote_linker import FootnoteLinker, link_footnotes
from .models import ElementProvenance, ProcessedElement, ProcessingResult
from .processor import process_document
from .transformation_builder import build_transformations

__all__ = [
    # Processing
    "process_document",
    "ProcessedElement",
    "ProcessingResult",
    "ElementProvenance",
    # Analysis
    "analyze_document",
    "analyze_page",
    "DocumentAnalysis",
    "PageAnalysis",
    # Transformation building
    "build_transformations",
    # Footnote linking
    "link_footnotes",
    "FootnoteLinker",
]
