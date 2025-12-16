"""Document analysis models for the remediation pipeline.

These models capture the output of Phase 1 (Analysis) which guides:
- The Extraction agent (Haiku) for markdown generation
- Agent routing decisions (which specialized agents to run)
- Per-page context for specialized analysis
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HeadingNode(BaseModel):
    """A single heading in the document structure.

    Represents a heading detected in the document, including its level,
    title, page location, and optional section numbering.

    Attributes:
        level: Heading level (1-6) following HTML heading conventions
        title: Heading text as it appears in the document
        page: 1-indexed page number where heading appears
        section_number: Optional section number (e.g., '1', '2.1', 'A')

    Example:
        >>> node = HeadingNode(
        ...     level=2,
        ...     title="Introduction",
        ...     page=3,
        ...     section_number="1.1"
        ... )
    """

    level: int = Field(..., ge=1, le=6, description="Heading level (1-6)")
    title: str = Field(..., description="Heading text as it appears")
    page: int = Field(..., ge=1, description="Page number where heading appears")
    section_number: str | None = Field(
        default=None, description="Section number if present (e.g., '1', '2.1', 'A')"
    )


class HeadingTree(BaseModel):
    """Document heading structure from analysis.

    Represents the complete hierarchical structure of headings in a document,
    including metadata about layout and confidence in the analysis.

    This model is used by:
    - Analysis agent (Sonnet 4.5) for deep document analysis
    - Full document agent for two-phase extraction
    - Extraction agent (Haiku) for guided markdown generation

    Attributes:
        document_title: Main document title (H1 level)
        title_page: Page number where the title appears
        sections: All headings in document order
        total_pages: Total number of pages (optional for backward compatibility)
        layout_type: Detected layout type
        confidence: Confidence in structure analysis (0.0-1.0)
        observations: Optional notes about document structure

    Example:
        >>> tree = HeadingTree(
        ...     document_title="CS 101 Syllabus",
        ...     title_page=1,
        ...     sections=[
        ...         HeadingNode(level=2, title="Course Overview", page=1),
        ...         HeadingNode(level=2, title="Schedule", page=3)
        ...     ],
        ...     total_pages=10,
        ...     layout_type="single_column",
        ...     confidence=0.95
        ... )
    """

    document_title: str = Field(..., description="Main document title (H1)")
    title_page: int = Field(default=1, description="Page where title appears")
    sections: list[HeadingNode] = Field(
        default_factory=list, description="All headings in document order"
    )
    total_pages: int | None = Field(
        default=None,
        description="Total pages in document (optional for backward compatibility)"
    )
    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        default="single_column",
        description="Detected layout: single_column, two_column, or mixed",
    )
    confidence: float = Field(
        default=0.9, ge=0.0, le=1.0, description="Confidence in structure analysis"
    )
    observations: str = Field(
        default="", description="Notes about document structure"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_title": "CS 101 Course Syllabus",
                "title_page": 1,
                "sections": [
                    {
                        "level": 2,
                        "title": "Course Overview",
                        "page": 1,
                        "section_number": "1"
                    },
                    {
                        "level": 2,
                        "title": "Schedule",
                        "page": 3,
                        "section_number": "2"
                    }
                ],
                "total_pages": 10,
                "layout_type": "single_column",
                "confidence": 0.95,
                "observations": "Well-structured document with clear hierarchy"
            }
        }
    )


class PageFeatures(BaseModel):
    """Features detected on a single page by the Analysis agent.

    Used by the Analysis phase (Sonnet) to capture per-page characteristics
    that guide extraction and specialized agent routing.

    Attributes:
        page_num: 1-indexed page number
        has_images: Whether page contains INFORMATIVE images
        image_count: Number of informative images on page
        has_tables: Whether page contains tables
        table_count: Number of tables on page
        has_lists: Whether page contains lists
        has_code_blocks: Whether page contains code blocks
        has_math: Whether page contains mathematical notation
        layout_type: Column layout of THIS PAGE ONLY
        has_headers_footers: Whether page has headers/footers
        complexity_score: Overall complexity assessment (0.0-1.0)
        complexity_factors: List of factors contributing to complexity

    Example:
        >>> features = PageFeatures(
        ...     page_num=1,
        ...     has_images=True,
        ...     image_count=2,
        ...     has_tables=False,
        ...     complexity_score=0.7,
        ...     complexity_factors=["multiple images", "two column layout"]
        ... )
    """

    page_num: int = Field(
        ...,
        ge=1,
        description="1-indexed page number (first page is 1, not 0)"
    )

    # Content detection - Images
    has_images: bool = Field(
        default=False,
        description=(
            "True if page contains INFORMATIVE images requiring alt text. "
            "Includes: charts, diagrams, photos, screenshots with content. "
            "Excludes: decorative borders, backgrounds, logos, spacers."
        ),
    )
    image_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of INFORMATIVE images only (matching has_images criteria). "
            "If has_images=True, image_count must be >= 1. "
            "If has_images=False, image_count must be 0."
        ),
    )

    # Content detection - Tables
    has_tables: bool = Field(
        default=False,
        description=(
            "True if page contains data tables requiring structure analysis. "
            "Includes: data tables, comparison tables, schedules. "
            "Excludes: layout tables used purely for positioning."
        ),
    )
    table_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of data tables only (matching has_tables criteria). "
            "If has_tables=True, table_count must be >= 1. "
            "If has_tables=False, table_count must be 0."
        ),
    )

    # Content detection - Other elements
    has_lists: bool = Field(
        default=False,
        description="True if page contains bulleted or numbered lists."
    )
    has_code_blocks: bool = Field(
        default=False,
        description="True if page contains code snippets or preformatted text."
    )
    has_math: bool = Field(
        default=False,
        description="True if page contains mathematical equations or formulas."
    )

    # Layout
    layout_type: Literal["single_column", "two_column", "mixed"] = Field(
        default="single_column",
        description=(
            "Layout for THIS PAGE ONLY (not the whole document). "
            "'single_column': Standard linear reading order, text flows top to bottom. "
            "'two_column': Side-by-side columns (common in academic papers). "
            "'mixed': ONLY if multiple layouts appear on the SAME page. "
            "Note: If page 1 is single-column and page 2 is two-column, "
            "each gets their own layout_type (not 'mixed')."
        ),
    )
    has_headers_footers: bool = Field(
        default=False,
        description="True if page has running headers or footers to exclude from main content."
    )

    # Complexity assessment
    complexity_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Page complexity score for routing decisions. "
            "0.0 = Very simple (plain text, clear structure, no special elements). "
            "0.5 = Moderate (some tables/lists, standard formatting). "
            "1.0 = Very complex (nested tables, multi-column, dense figures, merged cells). "
            "Consider: table nesting depth, list hierarchy, image density, column count."
        ),
    )
    complexity_factors: list[str] = Field(
        default_factory=list,
        description=(
            "List specific factors contributing to complexity_score. "
            "Examples: 'dense tables', 'nested lists', 'multi-column layout', "
            "'complex images with labels', 'mathematical equations', 'merged table cells'. "
            "Empty list if complexity_score <= 0.3."
        ),
    )

    # Reasoning transparency (from Reasoned[T] fields)
    layout_type_reasoning: str | None = Field(
        default=None,
        description=(
            "LLM reasoning for layout_type determination. "
            "Cites visual evidence: gutter presence, text block width, column structure."
        ),
    )
    complexity_score_reasoning: str | None = Field(
        default=None,
        description=(
            "LLM reasoning for complexity_score determination. "
            "Cites factors: table density, nesting depth, image count."
        ),
    )

    @model_validator(mode="after")
    def validate_image_consistency(self) -> "PageFeatures":
        """Ensure has_images and image_count are consistent."""
        if self.has_images and self.image_count == 0:
            # Auto-fix: if has_images is True, count must be at least 1
            self.image_count = 1
        elif not self.has_images and self.image_count > 0:
            # Auto-fix: if has_images is False, count must be 0
            self.image_count = 0
        return self

    @model_validator(mode="after")
    def validate_table_consistency(self) -> "PageFeatures":
        """Ensure has_tables and table_count are consistent."""
        if self.has_tables and self.table_count == 0:
            # Auto-fix: if has_tables is True, count must be at least 1
            self.table_count = 1
        elif not self.has_tables and self.table_count > 0:
            # Auto-fix: if has_tables is False, count must be 0
            self.table_count = 0
        return self

    @model_validator(mode="after")
    def validate_complexity_factors(self) -> "PageFeatures":
        """Ensure complexity_factors matches complexity_score."""
        import logging
        if self.complexity_score <= 0.3 and self.complexity_factors:
            # Low complexity shouldn't have factors
            logging.getLogger(__name__).debug(
                f"Clearing complexity_factors for low complexity page {self.page_num}"
            )
            self.complexity_factors = []
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page_num": 1,
                "has_images": True,
                "image_count": 2,
                "has_tables": False,
                "table_count": 0,
                "has_lists": True,
                "has_code_blocks": False,
                "has_math": False,
                "layout_type": "single_column",
                "has_headers_footers": True,
                "complexity_score": 0.6,
                "complexity_factors": ["multiple images", "nested lists"]
            }
        }
    )


class DocumentManifest(BaseModel):
    """Complete document analysis from the Analysis phase.

    This manifest is produced by the Analysis agent (Sonnet) and guides:
    - The Extraction agent (Haiku) for markdown generation
    - Agent routing decisions (which specialized agents to run)
    - Per-page context for specialized analysis

    Attributes:
        job_id: Associated job ID
        document_title: Detected document title
        document_type: Document classification (syllabus, lecture, exam, etc.)
        total_pages: Total number of pages
        heading_tree_json: Serialized HeadingTree from analysis
        page_features: Per-page feature analysis
        required_agents: Specialized agents that should run
        skip_agents: Agents that can be skipped for this document
        analysis_confidence: Overall confidence in analysis (0.0-1.0)
        analysis_notes: Notes about analysis decisions
        created_at: When analysis was completed
        analysis_model: Model used for analysis (e.g., claude-sonnet-4-5)

    Example:
        >>> manifest = DocumentManifest(
        ...     job_id="550e8400-e29b-41d4-a716-446655440000",
        ...     document_title="CS 101 Syllabus",
        ...     document_type="syllabus",
        ...     total_pages=10,
        ...     heading_tree_json='{"sections": []}',
        ...     page_features=[PageFeatures(page_num=1)],
        ...     required_agents=["figures", "tables"],
        ...     analysis_confidence=0.9
        ... )
    """

    job_id: str = Field(..., description="Associated job ID")

    # Document metadata
    document_title: str = Field(default="Untitled")
    document_type: str = Field(
        default="unknown",
        description="Document classification: syllabus, lecture, exam, etc."
    )
    total_pages: int = Field(..., ge=1, description="Total number of pages")

    # Structure (from existing HeadingTree)
    heading_tree_json: str = Field(
        ...,
        description="Serialized HeadingTree JSON string"
    )

    # Per-page analysis
    page_features: list[PageFeatures] = Field(default_factory=list)

    # Agent routing
    required_agents: list[str] = Field(
        default_factory=list,
        description="Specialized agents to run: figures, tables, structure, typography"
    )
    skip_agents: list[str] = Field(
        default_factory=list,
        description="Agents that can be skipped for this document"
    )

    # Confidence
    analysis_confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Overall confidence in analysis"
    )
    analysis_notes: str = Field(
        default="",
        description="Notes about analysis decisions or observations"
    )

    # Reasoning transparency (from Reasoned[T] and verification fields)
    document_type_reasoning: str | None = Field(
        default=None,
        description=(
            "LLM reasoning for document_type classification. "
            "Cites visual evidence: title patterns, structure indicators."
        ),
    )
    heading_order_verification: str | None = Field(
        default=None,
        description=(
            "LLM verification that heading tree respects reading order. "
            "For two-column: confirms down-left-then-right reading. "
            "Validates subsections follow parents (5.1 after 5)."
        ),
    )
    agent_routing_reasoning: str | None = Field(
        default=None,
        description=(
            "LLM reasoning for agent routing decisions. "
            "Explains which agents were included/excluded and why."
        ),
    )

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    analysis_model: str = Field(default="claude-sonnet-4-5")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "document_title": "CS 101 Course Syllabus",
                "document_type": "syllabus",
                "total_pages": 10,
                "heading_tree_json": '{"document_title": "CS 101", "sections": []}',
                "page_features": [],
                "required_agents": ["figures", "tables"],
                "skip_agents": ["typography"],
                "analysis_confidence": 0.92,
                "analysis_notes": "Clear structure, some complex tables on pages 5-6",
                "created_at": "2024-12-10T10:30:00Z",
                "analysis_model": "claude-sonnet-4-5"
            }
        }
    )
