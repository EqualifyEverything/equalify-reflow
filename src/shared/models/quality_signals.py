"""Quality signals model for hybrid confidence calculation.

This module provides a model for capturing observable quality signals
that can be used to programmatically adjust confidence scores.

The hybrid approach combines:
- 80% weight on heuristic signals (observable, verifiable)
- 20% weight on model self-assessment (subjective, but useful)

This provides more calibrated confidence than pure model self-assessment,
which tends to be overconfident.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class QualitySignals(BaseModel):
    """Signals detected by model for programmatic confidence calculation.

    These signals capture observable qualities that affect reliability:
    - Image clarity affects visual analysis accuracy
    - Text legibility affects transcription accuracy
    - Structure complexity affects structural analysis accuracy
    - Content ambiguity affects interpretation accuracy
    - Extraction metrics capture processing success indicators

    The model reports what it observes; the confidence calculator
    applies penalties based on these signals.

    Example:
        >>> signals = QualitySignals(
        ...     image_clarity="clear",
        ...     text_legibility="clear",
        ...     structure_complexity="moderate",
        ...     content_ambiguity="unambiguous",
        ...     chars_per_page=450.0,
        ...     vision_extraction_used=False,
        ... )
        >>> final_confidence = calculate_confidence(signals, model_confidence=0.9)
    """

    # === Original quality signals (subjective, from model observation) ===

    image_clarity: Literal["clear", "blurry", "partial", "missing"] = Field(
        default="clear",
        description=(
            "Quality of page images. "
            "'clear'=sharp, readable, good resolution; "
            "'blurry'=degraded quality, hard to read details; "
            "'partial'=cropped, incomplete, or cut off content; "
            "'missing'=no image available for visual verification."
        ),
    )
    text_legibility: Literal["clear", "faded", "mixed", "handwritten"] = Field(
        default="clear",
        description=(
            "Legibility of text content. "
            "'clear'=typed, sharp, high contrast; "
            "'faded'=low contrast, washed out; "
            "'mixed'=varies across page, some clear some not; "
            "'handwritten'=manual writing, variable quality."
        ),
    )
    structure_complexity: Literal["simple", "moderate", "complex"] = Field(
        default="simple",
        description=(
            "Document structure complexity. "
            "'simple'=single column, clear headings, obvious structure; "
            "'moderate'=some tables, lists, or multiple sections; "
            "'complex'=nested tables, multi-column, merged cells, mixed layouts."
        ),
    )
    content_ambiguity: Literal["unambiguous", "some_ambiguity", "highly_ambiguous"] = Field(
        default="unambiguous",
        description=(
            "How clear the content meaning is. "
            "'unambiguous'=obvious structure, clear intent; "
            "'some_ambiguity'=unclear heading levels, uncertain reading order; "
            "'highly_ambiguous'=multiple valid interpretations, unclear boundaries."
        ),
    )

    # === NEW: Extraction metrics (objective, from processing) ===

    chars_per_page: float = Field(
        default=500.0,
        ge=0.0,
        description=(
            "Average characters extracted per page. "
            "Low values (<100) indicate failed extraction or scanned documents."
        ),
    )
    vision_extraction_used: bool = Field(
        default=False,
        description=(
            "Whether LLM vision extraction was used (scanned PDF fallback). "
            "Vision extraction has higher uncertainty than native text extraction."
        ),
    )
    placeholder_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of unfilled placeholders (<!-- image -->, [TODO:], etc). "
            "High counts indicate incomplete processing."
        ),
    )
    total_elements: int = Field(
        default=1,
        ge=1,
        description="Total elements in document (for ratio calculations).",
    )
    spacing_artifact_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Count of letter-spacing artifacts detected (e.g., 'r e q u i r e m e n t s'). "
            "Each indicates an OCR readability issue."
        ),
    )
    tables_found: int = Field(
        default=0,
        ge=0,
        description="Number of tables successfully transcribed.",
    )
    tables_planned: int = Field(
        default=0,
        ge=0,
        description="Number of tables identified in planning phase.",
    )
    images_with_alt: int = Field(
        default=0,
        ge=0,
        description="Number of images with alt-text.",
    )
    total_images: int = Field(
        default=0,
        ge=0,
        description="Total number of images in document.",
    )
    headings_found: int = Field(
        default=0,
        ge=0,
        description="Number of headings in final markdown.",
    )
    headings_planned: int = Field(
        default=0,
        ge=0,
        description="Number of headings identified in planning phase.",
    )

    @property
    def placeholder_ratio(self) -> float:
        """Ratio of placeholders to total elements (0.0-1.0)."""
        if self.total_elements == 0:
            return 0.0
        return min(1.0, self.placeholder_count / self.total_elements)

    @property
    def table_extraction_rate(self) -> float:
        """Ratio of tables found to tables planned (0.0-1.0)."""
        if self.tables_planned == 0:
            return 1.0  # No tables planned = 100% success
        return min(1.0, self.tables_found / self.tables_planned)

    @property
    def alt_text_coverage(self) -> float:
        """Ratio of images with alt-text to total images (0.0-1.0)."""
        if self.total_images == 0:
            return 1.0  # No images = 100% coverage
        return min(1.0, self.images_with_alt / self.total_images)

    @property
    def heading_completeness(self) -> float:
        """Ratio of headings found to headings planned (0.0-1.0)."""
        if self.headings_planned == 0:
            return 1.0  # No headings planned = 100% complete
        return min(1.0, self.headings_found / self.headings_planned)


__all__ = ["QualitySignals"]
