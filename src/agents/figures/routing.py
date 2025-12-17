"""Pure Python routing logic for figures analysis.

Step 3 of the figures chain: Determine routing and action.
This is pure Python - NO LLM calls, zero cost.

Routing rules:
- decorative → action: mark_decorative, route: auto
- informative + high confidence → action: add_alt, route: auto
- complex → action: add_long_desc, route: manual
- text → action: add_alt, route: manual (verify OCR)
- low confidence → route: manual
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.agents.figures.classification_agent import ImageClassification
from src.agents.figures.generation_agent import AltTextGeneration
from src.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Routing Types
# =============================================================================


ImageAction = Literal["add_alt", "mark_decorative", "add_long_desc", "none"]
Route = Literal["auto", "manual"]
Severity = Literal["critical", "major", "minor"]


@dataclass
class ImageRoutingResult:
    """Result of routing decision for a single image."""

    image_index: int
    image_type: str
    action: ImageAction
    route: Route
    severity: Severity
    confidence: float
    alt_text: str | None
    long_description: str | None
    visual_description: str
    manual_reason: str | None


# =============================================================================
# Routing Logic (Pure Python)
# =============================================================================


def route_image(
    classification: ImageClassification,
    generation: AltTextGeneration | None,
) -> ImageRoutingResult:
    """Route a single image based on classification and generation results.

    This is pure Python logic - no LLM calls, zero cost.

    Args:
        classification: Classification result from Step 1
        generation: Generation result from Step 2 (None for decorative images)

    Returns:
        ImageRoutingResult with action, route, and severity
    """
    image_type = classification.image_type
    confidence = classification.confidence

    # Use generation confidence if available (it's usually more accurate)
    if generation is not None:
        confidence = min(confidence, generation.confidence)

    # Decorative images: mark as decorative, auto-route
    if image_type == "decorative":
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="mark_decorative",
            route="auto",
            severity="minor",
            confidence=confidence,
            alt_text=None,
            long_description=None,
            visual_description=classification.visual_description,
            manual_reason=None,
        )

    # Non-decorative images need alt text
    if generation is None:
        # Should not happen, but handle gracefully
        logger.warning(
            f"Image {classification.image_index}: Non-decorative but no generation result"
        )
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="add_alt",
            route="manual",
            severity="major",
            confidence=0.0,
            alt_text=None,
            long_description=None,
            visual_description=classification.visual_description,
            manual_reason="Missing alt text generation",
        )

    # Complex images: need long description, manual review
    if image_type == "complex":
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="add_long_desc",
            route="manual",
            severity="major",
            confidence=confidence,
            alt_text=generation.alt_text,
            long_description=generation.long_description,
            visual_description=classification.visual_description,
            manual_reason="Complex image requires human verification of description",
        )

    # Text images: OCR needs verification, manual review
    if image_type == "text":
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="add_alt",
            route="manual",
            severity="major",
            confidence=confidence,
            alt_text=generation.alt_text,
            long_description=None,
            visual_description=classification.visual_description,
            manual_reason="Text image OCR requires human verification",
        )

    # Informative images: route based on confidence
    min_confidence = settings.min_confidence_for_auto_approval
    if confidence >= min_confidence:
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="add_alt",
            route="auto",
            severity="major",
            confidence=confidence,
            alt_text=generation.alt_text,
            long_description=None,
            visual_description=classification.visual_description,
            manual_reason=None,
        )
    else:
        return ImageRoutingResult(
            image_index=classification.image_index,
            image_type=image_type,
            action="add_alt",
            route="manual",
            severity="major",
            confidence=confidence,
            alt_text=generation.alt_text,
            long_description=None,
            visual_description=classification.visual_description,
            manual_reason=f"Low confidence ({confidence:.2f} < {min_confidence})",
        )


def route_page_images(
    classifications: list[ImageClassification],
    generations: list[AltTextGeneration],
) -> list[ImageRoutingResult]:
    """Route all images on a page.

    Args:
        classifications: All classification results for the page
        generations: Generation results (only for non-decorative images)

    Returns:
        List of routing results for all images
    """
    # Build lookup for generations by image index
    gen_by_index = {g.image_index: g for g in generations}

    results = []
    for classification in classifications:
        generation = gen_by_index.get(classification.image_index)
        result = route_image(classification, generation)
        results.append(result)

    return results


__all__ = [
    "ImageAction",
    "ImageRoutingResult",
    "Route",
    "Severity",
    "route_image",
    "route_page_images",
]
