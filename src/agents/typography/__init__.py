"""Typography agent package for semantic typography analysis.

This package provides the enhanced TypographyAgent that:
1. Uses document-type-specific rules via dynamic prompts
2. Verifies bold/italic by comparing page images to markdown
3. Confirms OCR suggestions from Python pre-check
4. Routes to auto_corrections or review_items based on confidence

Example:
    >>> from src.agents.typography import analyze
    >>> result = await analyze(
    ...     pages=page_images,
    ...     manifest=document_manifest,
    ...     markdown=extracted_markdown,
    ...     job_id="job-123",
    ...     ocr_suggestions=ocr_results
    ... )
    >>> print(f"{len(result.auto_corrections)} auto-corrections")

The agent outputs AgentResult for integration with the 4-phase architecture.
"""

from src.agents.typography.typography_agent import (
    FormattingIssue,
    OCRDecision,
    TypographyAgent,
    TypographyAnalysisOutput,
    analyze,
)

__all__ = [
    "FormattingIssue",
    "OCRDecision",
    "TypographyAgent",
    "TypographyAnalysisOutput",
    "analyze",
]
