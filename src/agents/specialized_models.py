"""Output models for specialized analysis agents.

These Pydantic models define the structured output for each specialized agent:
- FiguresAgent: Image accessibility analysis
- TablesAgent: Table structure validation
- StructureAgent: Heading hierarchy and reading order
- TypographyAgent: Semantic typography detection

Reasoning enhancements:
- Reasoned[T] wrappers for complex determinations (glass-box reasoning)
- QualitySignals for hybrid confidence calculation
- ReasonedOutputMixin for reasoning corpus extraction
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from src.shared.models.quality_signals import QualitySignals
from src.shared.models.reasoned import Reasoned, ReasonedOutputMixin

logger = logging.getLogger(__name__)

# Type aliases for Literal types
ImageType = Literal["decorative", "informative", "complex", "text"]
ImageAction = Literal["add_alt", "improve_alt", "mark_decorative", "add_long_desc", "none"]
TableComplexity = Literal["simple", "merged_cells", "nested", "irregular"]
TableAction = Literal["add_headers", "restructure", "verify_data", "add_caption", "none"]
StructureIssueType = Literal["heading_skip", "heading_mismatch", "reading_order", "missing_landmark"]
Severity = Literal["critical", "major", "minor"]
TypographyIssueType = Literal["emphasis_unmarked", "definition_unmarked", "semantic_color", "visual_heading"]


# =============================================================================
# FiguresAgent Models (#24)
# =============================================================================


class ImageAnalysis(BaseModel, ReasonedOutputMixin):
    """Analysis of a single image on a page with glass-box reasoning.

    Complex determinations (image_type, recommended_action) use Reasoned[T]
    wrapper to capture chain-of-thought reasoning before the value.

    Attributes:
        image_index: Image number on this page (1-indexed)
        image_type: Classification with reasoning (decorative, informative, complex, text)
        visual_description: What the image visually depicts
        current_alt_status: Status of current alt text
        recommended_action: Action with reasoning (add_alt, improve_alt, etc.)
        suggested_alt: Suggested alt text if applicable
        quality_signals: Observable signals for confidence calculation
        model_confidence: Model's self-reported confidence (0.0-1.0)
    """

    image_index: int = Field(..., ge=1, description="Image number on this page (1-indexed)")
    image_type: Reasoned[ImageType] = Field(
        ..., description="Classification with reasoning: decorative, informative, complex, or text"
    )
    visual_description: str = Field(..., min_length=1, description="What the image visually depicts")
    current_alt_status: str = Field(..., description="TODO placeholder, empty, or has description")
    recommended_action: Reasoned[ImageAction] = Field(
        ..., description="Action with reasoning: add_alt, improve_alt, mark_decorative, add_long_desc, none"
    )
    suggested_alt: str | None = Field(default=None, description="Suggested alt text if applicable")
    quality_signals: QualitySignals = Field(
        default_factory=QualitySignals, description="Observable quality signals for confidence calculation"
    )
    model_confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Model's self-reported confidence (used in hybrid calculation)"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> float:
        """Calculate hybrid confidence from quality signals + model assessment."""
        from src.utils.confidence import calculate_confidence

        return calculate_confidence(self.quality_signals, self.model_confidence)


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
# TablesAgent Models
# =============================================================================


class TableAnalysis(BaseModel, ReasonedOutputMixin):
    """Analysis of a single table on a page with glass-box reasoning.

    Complex determinations (complexity, recommended_action) use Reasoned[T] wrapper
    to capture chain-of-thought reasoning before the value.

    Attributes:
        table_index: Table number on this page (1-indexed)
        has_headers: Whether table has identifiable headers
        header_structure: Type of header structure
        complexity: Complexity assessment with reasoning
        data_accuracy: Assessment of markdown accuracy vs visual
        visual_description: What the table shows
        markdown_issues: List of specific issues found
        recommended_action: Action with reasoning connecting complexity to action
        quality_signals: Observable signals for confidence calculation
        model_confidence: Model's self-reported confidence (0.0-1.0)
    """

    table_index: int = Field(..., ge=1, description="Table number on this page (1-indexed)")
    has_headers: bool = Field(default=True, description="Whether table has identifiable headers")
    header_structure: str = Field(default="single_row", description="single_row, multi_row, column_headers, none")
    complexity: Reasoned[TableComplexity] = Field(
        ..., description="Complexity assessment with reasoning: simple, merged_cells, nested, irregular"
    )
    data_accuracy: str = Field(default="accurate", description="accurate, partial, missing_data, structural_loss")
    visual_description: str = Field(..., min_length=1, description="What the table visually shows")
    markdown_issues: list[str] = Field(default_factory=list, description="List of specific issues found in markdown")
    recommended_action: Reasoned[TableAction] = Field(
        ...,
        description=(
            "Action with reasoning: add_headers, restructure, verify_data, add_caption, none. "
            "Reasoning should connect header_structure and complexity to the chosen action."
        ),
    )
    quality_signals: QualitySignals = Field(
        default_factory=QualitySignals, description="Observable quality signals for confidence calculation"
    )
    model_confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Model's self-reported confidence (used in hybrid calculation)"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> float:
        """Calculate hybrid confidence from quality signals + model assessment."""
        from src.utils.confidence import calculate_confidence

        return calculate_confidence(self.quality_signals, self.model_confidence)


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
# StructureAgent Models
# =============================================================================


class StructureIssue(BaseModel, ReasonedOutputMixin):
    """A structural issue found in the document with glass-box reasoning.

    Complex determinations (issue_type, severity) use Reasoned[T] wrapper to
    capture chain-of-thought reasoning before the value.

    Attributes:
        issue_type: Issue classification with reasoning
        location_description: Where the issue occurs
        visual_evidence: What is visually presented
        markup_state: Current state in markup
        severity: Severity assessment with reasoning
        recommended_fix: Suggested fix
        quality_signals: Observable signals for confidence calculation
        model_confidence: Model's self-reported confidence (0.0-1.0)
    """

    issue_type: Reasoned[StructureIssueType] = Field(
        ...,
        description="Issue classification with reasoning",
    )
    location_description: str = Field(..., min_length=1, description="Where the issue occurs in the document")
    visual_evidence: str = Field(..., min_length=1, description="What is visually presented")
    markup_state: str = Field(..., min_length=1, description="Current state in the markup")
    severity: Reasoned[Severity] = Field(..., description="Severity assessment with reasoning: critical, major, minor")
    recommended_fix: str = Field(..., min_length=1, description="Suggested fix for the issue")
    quality_signals: QualitySignals = Field(
        default_factory=QualitySignals, description="Observable quality signals for confidence calculation"
    )
    model_confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Model's self-reported confidence (used in hybrid calculation)"
    )

    @field_validator("model_confidence")
    @classmethod
    def warn_if_default_confidence(cls, v: float) -> float:
        """Warn if confidence appears uncalibrated (exactly 0.8).

        The prompt instructs the model to calibrate confidence based on
        quality signals, not use a default value. This validator logs a
        warning when the suspiciously common default of 0.8 is used.
        """
        if v == 0.8:
            logger.warning(
                "StructureIssue model_confidence is exactly 0.8 - "
                "ensure this is calibrated from quality signals, not a default"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> float:
        """Calculate hybrid confidence from quality signals + model assessment."""
        from src.utils.confidence import calculate_confidence

        return calculate_confidence(self.quality_signals, self.model_confidence)


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
# TypographyAgent Models
# =============================================================================


class TypographyIssue(BaseModel, ReasonedOutputMixin):
    """A typography-based semantic issue with glass-box reasoning.

    Complex determinations (issue_type, severity) use Reasoned[T] wrapper to
    capture chain-of-thought reasoning before the value.

    Attributes:
        issue_type: Issue classification with reasoning
        visual_description: What is visually presented
        markup_state: Current state in markup
        semantic_meaning: What the styling conveys
        severity: Severity assessment with reasoning (critical, major, minor)
        recommended_markup: Suggested markup fix
        quality_signals: Observable signals for confidence calculation
        model_confidence: Model's self-reported confidence (0.0-1.0)
    """

    issue_type: Reasoned[TypographyIssueType] = Field(
        ...,
        description="Issue classification with reasoning citing visual evidence",
    )
    visual_description: str = Field(..., min_length=1, description="What is visually presented")
    markup_state: str = Field(..., min_length=1, description="Current state in the markup")
    semantic_meaning: str = Field(..., min_length=1, description="What the styling conveys semantically")
    severity: Reasoned[Severity] = Field(
        ...,
        description=(
            "Severity assessment with reasoning: critical (blocks access), "
            "major (significant barrier), minor (inconvenience)"
        ),
    )
    recommended_markup: str = Field(..., min_length=1, description="Suggested markup to apply")
    quality_signals: QualitySignals = Field(
        default_factory=QualitySignals, description="Observable quality signals for confidence calculation"
    )
    model_confidence: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Model's self-reported confidence (used in hybrid calculation)"
    )

    @field_validator("model_confidence")
    @classmethod
    def warn_if_default_confidence(cls, v: float) -> float:
        """Warn if confidence appears uncalibrated (exactly 0.8).

        The prompt instructs the model to calibrate confidence based on
        quality signals, not use a default value. This validator logs a
        warning when the suspiciously common default of 0.8 is used.
        """
        if v == 0.8:
            logger.warning(
                "TypographyIssue model_confidence is exactly 0.8 - "
                "ensure this is calibrated from quality signals, not a default"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def confidence(self) -> float:
        """Calculate hybrid confidence from quality signals + model assessment."""
        from src.utils.confidence import calculate_confidence

        return calculate_confidence(self.quality_signals, self.model_confidence)


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
    # Type aliases
    "ImageType",
    "ImageAction",
    "TableComplexity",
    "TableAction",
    "StructureIssueType",
    "Severity",
    "TypographyIssueType",
]
