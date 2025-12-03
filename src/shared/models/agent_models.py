"""Unified agent input/output models for multi-agent system (PRD-011).

This module provides standardized Pydantic models for agent communication,
ensuring consistent data structures across all specialist agents.
"""

from typing import Any, Literal

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


class AgentCorrection(BaseModel):
    """Individual correction identified by a specialist agent.

    Represents a specific change that should be made to the markdown
    to improve accessibility or correct layout issues.

    Attributes:
        correction_type: Category of correction matching agent specialization
        original_text: Text as it currently appears in the markdown
        corrected_text: Text as it should appear after correction
        location_context: Surrounding text to help locate the correction point
        line_number: Approximate line number in markdown (if determinable)
        confidence: Agent's confidence in this correction (0.0-1.0)
        explanation: Human-readable explanation of why this correction is needed
        agent_source: Name of the agent that identified this correction

    Example:
        >>> correction = AgentCorrection(
        ...     correction_type="heading_level",
        ...     original_text="Course Schedule",
        ...     corrected_text="## Course Schedule",
        ...     location_context="...Introduction\\n\\nCourse Schedule\\n\\nWeek 1...",
        ...     line_number=15,
        ...     confidence=0.95,
        ...     explanation="Visual layout shows this as a level 2 heading",
        ...     agent_source="structure_agent"
        ... )
    """

    correction_type: Literal[
        "heading_level",
        "list_structure",
        "table_format",
        "paragraph_break",
        "spelling",
        "alt_text",
        "figure_caption",
        "table_header",
        "emphasis",
        "reading_order",
        "other"
    ] = Field(
        ...,
        description="Category of correction matching agent specialization"
    )
    original_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Text as it currently appears in the markdown"
    )
    corrected_text: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Text as it should appear after correction"
    )
    location_context: str = Field(
        default="",
        max_length=10000,
        description="Surrounding text to help locate the correction point"
    )
    line_number: int | None = Field(
        default=None,
        ge=1,
        description="Approximate line number in markdown"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in this correction"
    )
    explanation: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Human-readable explanation of why this correction is needed"
    )
    agent_source: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the agent that identified this correction"
    )
    hint_addressed: str | None = Field(
        default=None,
        max_length=200,
        description="Hint ID if this correction addresses a specific pre-analysis hint"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "correction_type": "heading_level",
                "original_text": "Course Schedule",
                "corrected_text": "## Course Schedule",
                "location_context": "...Introduction\n\nCourse Schedule\n\nWeek 1...",
                "line_number": 15,
                "confidence": 0.95,
                "explanation": "Visual layout shows this as a level 2 heading with larger font",
                "agent_source": "structure_agent",
                "hint_addressed": "MD001:line15"
            }
        }
    )


class AgentOutput(BaseModel):
    """Unified output model for all document agents.

    Provides a consistent interface for agent results, including
    corrections identified and processing metadata.

    Attributes:
        agent_name: Name of the agent that produced this output
        page_number: Page number that was processed (1-indexed)
        corrections: List of corrections identified by the agent
        overall_confidence: Overall confidence in the corrections (0.0-1.0)
        processing_notes: Summary of what was analyzed and any issues encountered
        usage: Token usage and cost for this agent's LLM call(s)

    Example:
        >>> output = AgentOutput(
        ...     agent_name="structure_agent",
        ...     page_number=1,
        ...     corrections=[...],
        ...     overall_confidence=0.92,
        ...     processing_notes="Analyzed 3 headings, 2 lists. Found 1 correction.",
        ...     usage=LLMUsage(input_tokens=1500, output_tokens=200, cost_cents=0.0625)
        ... )
    """

    agent_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name of the agent that produced this output"
    )
    page_number: int = Field(
        ...,
        ge=1,
        description="Page number that was processed (1-indexed)"
    )
    corrections: list[AgentCorrection] = Field(
        default_factory=list,
        description="List of corrections identified by the agent"
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the corrections"
    )
    processing_notes: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Summary of what was analyzed and any issues encountered"
    )
    usage: LLMUsage | None = Field(
        default=None,
        description="Token usage and cost for this agent's LLM call(s)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_name": "structure_agent",
                "page_number": 1,
                "corrections": [
                    {
                        "correction_type": "heading_level",
                        "original_text": "Introduction",
                        "corrected_text": "# Introduction",
                        "location_context": "...\n\nIntroduction\n\nThis course...",
                        "line_number": 5,
                        "confidence": 0.95,
                        "explanation": "Visual layout shows level 1 heading",
                        "agent_source": "structure_agent"
                    }
                ],
                "overall_confidence": 0.92,
                "processing_notes": "Analyzed 3 headings, 2 lists. Found 1 correction.",
                "usage": {
                    "input_tokens": 1500,
                    "output_tokens": 200,
                    "cost_cents": 0.0625
                }
            }
        }
    )


# Re-export LLMUsage for convenience
__all__ = ["AgentInput", "AgentCorrection", "AgentOutput", "LLMUsage"]
