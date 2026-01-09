"""Unified agent input/output models for multi-agent system.

This module provides standardized Pydantic models for agent communication,
ensuring consistent data structures across all specialist agents.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .processing import LLMUsage


class AgentInput(BaseModel):
    """Unified input model for all document agents.

    Provides a consistent interface for passing page data and context
    to any specialist agent in the multi-agent system.

    Attributes:
        page_number: 1-indexed page number in the document
        page_markdown: Extracted markdown text from Docling
        page_image_base64: Base64-encoded PNG image of the page
        document_context: Optional document-level context (TOC, heading hierarchy)
        hints: Optional pre-analysis hints (from VeraPDF, PyMarkdown)

    Example:
        >>> agent_input = AgentInput(
        ...     page_number=1,
        ...     page_markdown="# Introduction\\n\\nThis course covers...",
        ...     page_image_base64="iVBORw0KGgo...",
        ...     document_context={"title": "Course Syllabus", "toc": [...]},
        ...     hints={"heading_issues": ["MD001: Heading levels should increment by one"]}
        ... )
    """

    page_number: int = Field(
        ...,
        ge=1,
        description="1-indexed page number in the document"
    )
    page_markdown: str = Field(
        ...,
        min_length=0,
        description="Extracted markdown text from Docling (can be empty for image-only pages)"
    )
    page_image_base64: str = Field(
        ...,
        min_length=1,
        description="Base64-encoded PNG image of the page"
    )
    document_context: dict[str, Any] | None = Field(
        default=None,
        description="Optional document-level context (metadata, settings, etc.)"
    )
    hints: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional pre-analysis hints from deterministic tools (VeraPDF, PyMarkdown)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page_number": 1,
                "page_markdown": "# Introduction\n\nThis course covers Python programming.",
                "page_image_base64": "iVBORw0KGgoAAAANSUhEUg...",
                "document_context": {
                    "title": "Course Syllabus",
                    "toc": ["Introduction", "Schedule", "Grading"]
                },
                "hints": {
                    "heading_issues": ["MD001: Heading levels should increment by one"]
                }
            }
        }
    )


# Re-export LLMUsage for convenience
__all__ = ["AgentInput", "LLMUsage"]
