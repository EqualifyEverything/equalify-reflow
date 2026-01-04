"""Build structured transformations from ProcessedElements.

Converts internal ProcessedElement/ElementProvenance data into the
Transformation schema model for API exposure. This enables:
- Human review of AI decisions
- Structured before/after comparison
- Per-transformation approval/rejection workflow
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.api.schemas import Transformation

from .models import ElementType, ProcessedElement, TransformationType

if TYPE_CHECKING:
    pass


def _generate_element_id(elem: ProcessedElement, idx: int) -> str:
    """Generate a unique, human-readable element ID.

    Format: {type}-p{page}-{idx} or {type}-{idx}
    Examples: heading-p1-3, figure-p5-1, table-p8-2
    """
    type_prefix = elem.element_type.value.replace("_", "-")
    page = elem.provenance.docling_page if elem.provenance else 1
    return f"{type_prefix}-p{page}-{idx}"


def _build_original(elem: ProcessedElement) -> dict:
    """Build the 'original' dict showing pre-transformation state."""
    if elem.element_type == ElementType.SECTION_HEADER:
        return {
            "level": elem.original_heading_level or elem.hierarchy_level,
            "text": elem.original_text[:200] if elem.original_text else "",
        }

    elif elem.element_type == ElementType.PICTURE:
        return {
            "alt_text": None,  # No alt text before transformation
            "text": elem.original_text[:200] if elem.original_text else "",
        }

    elif elem.element_type == ElementType.TABLE:
        return {
            "has_header_row": None,  # Unknown before analysis
            "markdown": elem.original_text[:500] if elem.original_text else "",
        }

    elif elem.element_type == ElementType.LIST_ITEM:
        return {
            "list_type": "unknown",
            "text": elem.original_text[:200] if elem.original_text else "",
        }

    elif elem.element_type == ElementType.FORMULA:
        return {
            "latex": None,
            "plain_text": None,
            "text": elem.original_text[:200] if elem.original_text else "",
        }

    elif elem.element_type == ElementType.CODE:
        return {
            "language": None,
            "code": None,
            "text": elem.original_text[:500] if elem.original_text else "",
        }

    else:
        return {
            "text": elem.original_text[:200] if elem.original_text else "",
        }


def _build_transformed(elem: ProcessedElement) -> dict:
    """Build the 'transformed' dict showing post-transformation state."""
    if elem.element_type == ElementType.SECTION_HEADER:
        return {
            "level": elem.validated_heading_level or elem.hierarchy_level,
            "text": elem.processed_text[:200] if elem.processed_text else "",
        }

    elif elem.element_type == ElementType.PICTURE:
        return {
            "alt_text": elem.alt_text,
            "text": elem.processed_text[:200] if elem.processed_text else "",
        }

    elif elem.element_type == ElementType.TABLE:
        return {
            "has_header_row": elem.has_header_row,
            "markdown": elem.table_markdown[:500] if elem.table_markdown else "",
        }

    elif elem.element_type == ElementType.LIST_ITEM:
        # List type is in the transformation reason
        list_type = "unknown"
        if elem.provenance and elem.provenance.transformation_reason:
            reason = elem.provenance.transformation_reason
            if "ordered" in reason.lower():
                list_type = "ordered"
            elif "unordered" in reason.lower():
                list_type = "unordered"
            elif "definition" in reason.lower():
                list_type = "definition"
        return {
            "list_type": list_type,
            "text": elem.processed_text[:200] if elem.processed_text else "",
        }

    elif elem.element_type == ElementType.FORMULA:
        return {
            "latex": elem.formula_latex,
            "plain_text": elem.formula_plain_text,
            "text": elem.processed_text[:200] if elem.processed_text else "",
        }

    elif elem.element_type == ElementType.CODE:
        return {
            "language": elem.code_language,
            "code": elem.code_content[:500] if elem.code_content else "",
            "is_pseudocode": elem.is_pseudocode,
        }

    else:
        return {
            "text": elem.processed_text[:200] if elem.processed_text else "",
        }


def build_transformations(elements: list[ProcessedElement]) -> list[Transformation]:
    """Convert ProcessedElements to structured Transformation objects.

    Only includes elements that have undergone LLM transformation.
    Provides before/after state for human review.

    Args:
        elements: List of processed document elements

    Returns:
        List of Transformation objects for API response
    """
    transformations = []

    # Track element count by type for ID generation
    type_counts: dict[ElementType, int] = {}

    for elem in elements:
        # Skip elements without transformations
        if not elem.provenance:
            continue
        if elem.provenance.transformation == TransformationType.NONE:
            continue

        # Generate unique ID
        elem_type = elem.element_type
        type_counts[elem_type] = type_counts.get(elem_type, 0) + 1
        idx = type_counts[elem_type]
        element_id = _generate_element_id(elem, idx)

        # Build transformation object
        transformation = Transformation(
            element_id=element_id,
            element_type=elem_type.value,
            transformation_type=elem.provenance.transformation.value,
            page=elem.provenance.docling_page,
            original=_build_original(elem),
            transformed=_build_transformed(elem),
            reason=elem.provenance.transformation_reason or "No reason provided",
            confidence=elem.provenance.llm_confidence or 0.0,
            status="pending",
        )

        transformations.append(transformation)

    return transformations
