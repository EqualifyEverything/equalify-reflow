"""Output models for specialized analysis agents (PRD-014).

These Pydantic models define the structured output for each specialized agent:
- FiguresAgent: Image accessibility analysis
- TablesAgent: Table structure validation
- StructureAgent: Heading hierarchy and reading order
- TypographyAgent: Semantic typography detection
"""

from pydantic import BaseModel, Field

# =============================================================================
# FiguresAgent Models (#24)
# =============================================================================


class ImageAnalysis(BaseModel):
    """Analysis of a single image on a page.

    Attributes:
        image_index: Image number on this page (1-indexed)
        image_type: Classification of the image
        visual_description: What the image visually depicts
        current_alt_status: Status of current alt text
        recommended_action: What should be done
        suggested_alt: Suggested alt text if applicable
        confidence: Confidence in this analysis (0.0-1.0)
    """

    image_index: int = Field(
        ...,
        ge=1,
        description="Image number on this page (1-indexed)"
    )
    image_type: str = Field(
        ...,
        description="decorative, informative, complex, or text"
    )
    visual_description: str = Field(
        ...,
        min_length=1,
        description="What the image visually depicts"
    )
    current_alt_status: str = Field(
        ...,
        description="TODO placeholder, empty, or has description"
    )
    recommended_action: str = Field(
        ...,
        description="What should be done: add_alt, improve_alt, mark_decorative, add_long_desc, none"
    )
    suggested_alt: str | None = Field(
        default=None,
        description="Suggested alt text if applicable"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in this analysis"
    )


class FiguresAnalysisOutput(BaseModel):
    """Output from figures analysis for a single page.

    Attributes:
        page_num: Page number analyzed
        images_found: Number of images detected on page
        analyses: List of image analyses
        notes: Additional notes about the analysis
    """

    page_num: int = Field(..., ge=1)
    images_found: int = Field(default=0, ge=0)
    analyses: list[ImageAnalysis] = Field(default_factory=list)
    notes: str = Field(default="")


# =============================================================================
# TablesAgent Models (#24)
# =============================================================================


class TableAnalysis(BaseModel):
    """Analysis of a single table on a page.

    Attributes:
        table_index: Table number on this page (1-indexed)
        has_headers: Whether table has identifiable headers
        header_structure: Type of header structure
        complexity: Table complexity classification
        data_accuracy: Assessment of markdown accuracy vs visual
        visual_description: What the table shows
        markdown_issues: List of specific issues found
        recommended_action: What should be done
        confidence: Confidence in this analysis (0.0-1.0)
    """

    table_index: int = Field(
        ...,
        ge=1,
        description="Table number on this page (1-indexed)"
    )
    has_headers: bool = Field(
        default=True,
        description="Whether table has identifiable headers"
    )
    header_structure: str = Field(
        default="single_row",
        description="single_row, multi_row, column_headers, none"
    )
    complexity: str = Field(
        default="simple",
        description="simple, merged_cells, nested, irregular"
    )
    data_accuracy: str = Field(
        default="accurate",
        description="accurate, partial, missing_data, structural_loss"
    )
    visual_description: str = Field(
        ...,
        min_length=1,
        description="What the table visually shows"
    )
    markdown_issues: list[str] = Field(
        default_factory=list,
        description="List of specific issues found in markdown"
    )
    recommended_action: str = Field(
        default="none",
        description="add_headers, restructure, verify_data, add_caption, none"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in this analysis"
    )


class TablesAnalysisOutput(BaseModel):
    """Output from tables analysis for a single page.

    Attributes:
        page_num: Page number analyzed
        tables_found: Number of tables detected on page
        analyses: List of table analyses
        notes: Additional notes about the analysis
    """

    page_num: int = Field(..., ge=1)
    tables_found: int = Field(default=0, ge=0)
    analyses: list[TableAnalysis] = Field(default_factory=list)
    notes: str = Field(default="")


# =============================================================================
# StructureAgent Models (#23)
# =============================================================================


class StructureIssue(BaseModel):
    """A structural issue found in the document.

    Attributes:
        issue_type: Type of structural issue
        location_description: Where the issue occurs
        visual_evidence: What is visually presented
        markup_state: Current state in markup
        severity: Issue severity
        recommended_fix: Suggested fix
        confidence: Confidence in this analysis (0.0-1.0)
    """

    issue_type: str = Field(
        ...,
        description="heading_skip, heading_mismatch, reading_order, missing_landmark"
    )
    location_description: str = Field(
        ...,
        min_length=1,
        description="Where the issue occurs in the document"
    )
    visual_evidence: str = Field(
        ...,
        min_length=1,
        description="What is visually presented"
    )
    markup_state: str = Field(
        ...,
        min_length=1,
        description="Current state in the markup"
    )
    severity: str = Field(
        default="major",
        description="critical, major, minor"
    )
    recommended_fix: str = Field(
        ...,
        min_length=1,
        description="Suggested fix for the issue"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in this analysis"
    )


class StructureAnalysisOutput(BaseModel):
    """Output from structure analysis.

    Attributes:
        issues: List of structural issues found
        heading_hierarchy_valid: Whether heading hierarchy is valid
        reading_order_valid: Whether reading order is correct
        notes: Additional notes about the analysis
    """

    issues: list[StructureIssue] = Field(default_factory=list)
    heading_hierarchy_valid: bool = Field(default=True)
    reading_order_valid: bool = Field(default=True)
    notes: str = Field(default="")


# =============================================================================
# TypographyAgent Models (#23)
# =============================================================================


class TypographyIssue(BaseModel):
    """A typography-based semantic issue.

    Attributes:
        issue_type: Type of typography issue
        visual_description: What is visually presented
        markup_state: Current state in markup
        semantic_meaning: What the styling conveys
        recommended_markup: Suggested markup fix
        confidence: Confidence in this analysis (0.0-1.0)
    """

    issue_type: str = Field(
        ...,
        description="emphasis_unmarked, definition_unmarked, semantic_color, visual_heading"
    )
    visual_description: str = Field(
        ...,
        min_length=1,
        description="What is visually presented"
    )
    markup_state: str = Field(
        ...,
        min_length=1,
        description="Current state in the markup"
    )
    semantic_meaning: str = Field(
        ...,
        min_length=1,
        description="What the styling conveys semantically"
    )
    recommended_markup: str = Field(
        ...,
        min_length=1,
        description="Suggested markup to apply"
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence in this analysis"
    )


class TypographyAnalysisOutput(BaseModel):
    """Output from typography analysis for a single page.

    Attributes:
        page_num: Page number analyzed
        issues: List of typography issues found
        notes: Additional notes about the analysis
    """

    page_num: int = Field(..., ge=1)
    issues: list[TypographyIssue] = Field(default_factory=list)
    notes: str = Field(default="")


__all__ = [
    # Figures models
    "ImageAnalysis",
    "FiguresAnalysisOutput",
    # Tables models
    "TableAnalysis",
    "TablesAnalysisOutput",
    # Structure models
    "StructureIssue",
    "StructureAnalysisOutput",
    # Typography models
    "TypographyIssue",
    "TypographyAnalysisOutput",
]
