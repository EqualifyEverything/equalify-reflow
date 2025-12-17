"""Document context models for the remediation pipeline.

These models provide semantic context to downstream agents, enabling them
to make better decisions and generate AutoCorrections directly.

DocumentSummary captures:
- Topic understanding (what the document is about)
- Key entities (names, projects, technical terms for OCR detection)
- Domain vocabulary (helps catch OCR errors)
- Expected elements (abstract, references, figures)
- Audience level (academic, student, general)

ObservationContext provides full context for processing a single observation.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .observation import Observation
    from .remediation import HeadingTree


class DocumentSummary(BaseModel):
    """Context passed to all downstream agents.

    Generated during Phase 1 (Analysis) by the summary agent. This provides
    semantic context that helps all downstream agents make better decisions.

    Key uses:
    - OCR error detection: key_entities helps catch "Exxon" → "Enzo" errors
    - Structure validation: expected_elements guides heading validation
    - Alt text generation: topic_summary provides context for descriptions

    Attributes:
        title: Document title
        document_type: Classification (research_paper, syllabus, exam, etc.)
        topic_summary: 1-2 sentences about content
        structure_summary: Layout description (e.g., "9 pages, two-column")
        key_entities: Names, projects, technical terms for OCR detection
        domain_terms: Domain-specific vocabulary
        expected_elements: Expected structural elements
        audience_level: Target audience (academic, student, general)

    Example:
        >>> summary = DocumentSummary(
        ...     title="yt: An Open Source Framework",
        ...     document_type="research_paper",
        ...     topic_summary="Research paper about yt, an open-source analysis framework",
        ...     structure_summary="9 pages, two-column, abstract + 6 sections",
        ...     key_entities=["yt", "Enzo", "Matthew Turk", "DVCS"],
        ...     domain_terms=["parallelization", "MPI", "OpenMP"],
        ...     expected_elements=["abstract", "introduction", "references"],
        ...     audience_level="academic"
        ... )
    """

    # Basic info
    title: str = Field(
        ...,
        description="Document title"
    )
    document_type: str = Field(
        ...,
        description="Document classification: research_paper, syllabus, exam, etc."
    )

    # Semantic context
    topic_summary: str = Field(
        ...,
        description="1-2 sentences about document content"
    )
    structure_summary: str = Field(
        default="",
        description="Layout description (e.g., '9 pages, two-column, abstract + 6 sections')"
    )

    # Key terms for OCR detection and context
    key_entities: list[str] = Field(
        default_factory=list,
        description="Names, projects, technical terms: ['yt', 'Enzo', 'Turk']"
    )
    domain_terms: list[str] = Field(
        default_factory=list,
        description="Domain vocabulary: ['parallelization', 'MPI', 'OpenMP']"
    )

    # Expectations
    expected_elements: list[str] = Field(
        default_factory=list,
        description="Expected structural elements: ['abstract', 'references', 'figures']"
    )
    audience_level: str = Field(
        default="general",
        description="Target audience: academic, student, general"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "yt: An Open Source Framework for Analysis",
                "document_type": "research_paper",
                "topic_summary": (
                    "Research paper describing yt, an open-source Python "
                    "framework for analyzing astrophysical simulations."
                ),
                "structure_summary": (
                    "9 pages, two-column layout, abstract + 6 sections + references"
                ),
                "key_entities": ["yt", "Enzo", "Matthew Turk", "DVCS", "NumPy"],
                "domain_terms": ["parallelization", "MPI", "OpenMP", "simulation", "AMR"],
                "expected_elements": [
                    "abstract", "introduction", "methodology", "results", "references"
                ],
                "audience_level": "academic"
            }
        }
    )


class ObservationContext(BaseModel):
    """Full context for processing a single observation.

    Provides all the context needed for an agent to make a decision about
    an observation, including document summary, visual context, and
    surrounding text.

    This model is used when processing individual observations, enabling
    agents to make context-aware decisions.

    Attributes:
        observation: The observation being processed
        document_summary: Semantic context from analysis phase
        heading_tree: Document structure for hierarchy validation
        page_image_base64: The specific page as base64 PNG (for visual comparison)
        markdown_excerpt: ~1000 chars centered on the issue
        before_context: ~500 chars before the issue
        after_context: ~500 chars after the issue
        page_num: Page number where observation occurs
        line_range: Optional line range in markdown

    Example:
        >>> context = ObservationContext(
        ...     observation=obs,
        ...     document_summary=summary,
        ...     heading_tree=tree,
        ...     markdown_excerpt="## Introduction\\n\\nThis paper presents...",
        ...     before_context="# Title\\n\\n",
        ...     after_context="\\n\\n## Methods\\n\\n",
        ...     page_num=1
        ... )
    """

    # Note: Using string type hints to avoid circular imports
    # These will be resolved at runtime by Pydantic
    observation: "Observation" = Field(
        ...,
        description="The observation being processed"
    )
    document_summary: DocumentSummary = Field(
        ...,
        description="Semantic context from analysis phase"
    )
    heading_tree: "HeadingTree" = Field(
        ...,
        description="Document structure for hierarchy validation"
    )

    # Visual context
    page_image_base64: str | None = Field(
        default=None,
        description="The specific page as base64 PNG"
    )

    # Textual context (trimmed, not full doc)
    markdown_excerpt: str = Field(
        ...,
        description="~1000 chars centered on the issue"
    )
    before_context: str = Field(
        default="",
        description="~500 chars before the issue"
    )
    after_context: str = Field(
        default="",
        description="~500 chars after the issue"
    )

    # Location helpers
    page_num: int = Field(
        ...,
        ge=1,
        description="Page number where observation occurs"
    )
    line_range: tuple[int, int] | None = Field(
        default=None,
        description="Line range in markdown (start, end)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "observation": {"id": "obs-123", "job_id": "job-456"},
                "document_summary": {
                    "title": "Example Document",
                    "document_type": "research_paper",
                    "topic_summary": "A research paper about...",
                },
                "heading_tree": {"document_title": "Example", "sections": []},
                "page_image_base64": None,
                "markdown_excerpt": "## Introduction\n\nThis paper presents...",
                "before_context": "# Title\n\n",
                "after_context": "\n\n## Methods\n\n",
                "page_num": 1,
                "line_range": [10, 25]
            }
        }
    )


# Update forward references for runtime
# This allows Pydantic to resolve the string type hints
def _update_forward_refs() -> None:
    """Update forward references after all models are defined."""
    ObservationContext.model_rebuild()


__all__ = ["DocumentSummary", "ObservationContext"]
