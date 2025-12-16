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

    The model reports what it observes; the confidence calculator
    applies penalties based on these signals.

    Example:
        >>> signals = QualitySignals(
        ...     image_clarity="clear",
        ...     text_legibility="clear",
        ...     structure_complexity="moderate",
        ...     content_ambiguity="unambiguous",
        ... )
        >>> final_confidence = calculate_confidence(signals, model_confidence=0.9)
    """

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


__all__ = ["QualitySignals"]
