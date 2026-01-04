"""Deterministic document processor with LLM-assisted decision points.

This replaces the agentic approach with explicit control flow:
- Walk Docling's document tree
- For each element type, apply the appropriate transformation
- Track provenance throughout
- Serialize with embedded provenance

Key insight: We know the task structure. These aren't agent decisions,
they're deterministic traversals with LLM-assisted transformations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PIL import Image

from .analyzer import DocumentAnalysis
from .models import (
    BoundingBox,
    ElementProvenance,
    ElementType,
    ProcessedElement,
    ProcessingResult,
    TransformationType,
)
from .transformers import (
    analyze_table,
    classify_list_type,
    generate_alt_text,
    infer_heading_level,
    transcribe_code_block,
    transcribe_formula,
)

if TYPE_CHECKING:
    from docling_core.types.doc import DoclingDocument

logger = logging.getLogger(__name__)


def _get_element_type(label: str) -> ElementType:
    """Map Docling label to our ElementType."""
    mapping = {
        "section_header": ElementType.SECTION_HEADER,
        "text": ElementType.TEXT,
        "list_item": ElementType.LIST_ITEM,
        "picture": ElementType.PICTURE,
        "table": ElementType.TABLE,
        "footnote": ElementType.FOOTNOTE,
        "caption": ElementType.CAPTION,
        "formula": ElementType.FORMULA,
        "page_header": ElementType.PAGE_HEADER,
        "page_footer": ElementType.PAGE_FOOTER,
    }
    # Handle DocItemLabel enum
    label_str = label.value if hasattr(label, "value") else str(label)
    return mapping.get(label_str, ElementType.UNKNOWN)


def _extract_bbox(element: Any) -> tuple[BoundingBox | None, int]:
    """Extract bounding box and page number from element provenance."""
    if hasattr(element, "prov") and element.prov:
        prov = element.prov[0]
        page_no = prov.page_no if hasattr(prov, "page_no") else 1
        if hasattr(prov, "bbox") and prov.bbox:
            return BoundingBox.from_docling(prov.bbox, page_no), page_no
        return None, page_no
    return None, 1


async def process_document(
    doc: "DoclingDocument",
    page_images: dict[int, Image.Image] | None = None,
    document_type: str = "document",
    validate_headings: bool = True,
    generate_alt_texts: bool = True,
    analyze_tables: bool = True,
    classify_lists: bool = True,
    transcribe_formulas: bool = True,
    transcribe_code: bool = True,
    analysis: DocumentAnalysis | None = None,
) -> ProcessingResult:
    """Process a Docling document with LLM-assisted transformations.

    This is deterministic traversal, not agentic exploration:
    - Every figure gets alt text (if enabled)
    - Every table gets semantic analysis (if enabled)
    - Every heading gets level validation (if enabled)
    - Formulas/code identified by analysis get LLM transcription

    Args:
        doc: Docling Document object (NOT markdown string)
        page_images: Optional dict of page number -> PIL Image
        document_type: Document classification for context
        validate_headings: Whether to LLM-validate heading levels
        generate_alt_texts: Whether to LLM-generate alt texts
        analyze_tables: Whether to LLM-analyze table semantics
        classify_lists: Whether to LLM-classify list types
        transcribe_formulas: Whether to LLM-transcribe formulas
        transcribe_code: Whether to LLM-transcribe code blocks
        analysis: Optional DocumentAnalysis with identified hotspots

    Returns:
        ProcessingResult with all processed elements and provenance
    """
    result = ProcessingResult()

    # Track heading context for hierarchy validation
    recent_headings: list[tuple[str, int]] = []

    # Track list items for batched classification
    current_list_items: list[tuple[Any, int, str]] = []  # (element, level, text)

    # Use document type from analysis if available
    if analysis and analysis.document_type != "unknown":
        document_type = analysis.document_type

    # Build set of pages with hotspots for quick lookup
    pages_with_code = set(analysis.pages_with_code) if analysis else set()
    pages_with_equations = set(analysis.pages_with_equations) if analysis else set()

    logger.info(
        f"Processing document: type={document_type}, "
        f"code_pages={pages_with_code}, equation_pages={pages_with_equations}"
    )

    # Track which pages we've processed hotspots for (to avoid duplicates)
    processed_code_pages: set[int] = set()

    # Deterministic traversal of document tree
    for element, level in doc.iterate_items():
        # Get element type and basic info
        label = element.label if hasattr(element, "label") else "unknown"
        element_type = _get_element_type(label)
        text = element.text if hasattr(element, "text") else ""
        bbox, page_no = _extract_bbox(element)

        # Create provenance
        provenance = ElementProvenance(
            docling_label=str(label.value if hasattr(label, "value") else label),
            docling_bbox=bbox,
            docling_page=page_no,
        )

        # Create base processed element
        processed = ProcessedElement(
            element_type=element_type,
            hierarchy_level=level,
            original_text=text,
            processed_text=text,
            provenance=provenance,
            docling_ref=element,
        )

        result.total_elements += 1

        # =====================================================================
        # Page-level hotspot processing (LLM-driven, not heuristics)
        # When we first encounter a page with code hotspots, send it to the LLM
        # =====================================================================

        if (
            transcribe_code
            and page_no in pages_with_code
            and page_no not in processed_code_pages
            and page_images
        ):
            processed_code_pages.add(page_no)
            page_img = page_images.get(page_no)

            if page_img and analysis:
                page_analysis = analysis.page_analyses.get(page_no)
                if page_analysis and page_analysis.code_blocks:
                    # Get descriptions of code blocks from analysis
                    code_descriptions = [
                        f"{cb.location}: {cb.description}"
                        for cb in page_analysis.code_blocks
                    ]
                    context = f"Analysis identified code: {'; '.join(code_descriptions)}"

                    logger.info(f"Processing code hotspot on page {page_no}: {context}")

                    # Send page to LLM for code transcription
                    code_result = await transcribe_code_block(
                        image=page_img,
                        docling_text="",  # Let LLM find and transcribe the code
                        context=context,
                    )

                    if code_result.confidence > 0.0:
                        # Create a synthetic code element to insert
                        code_provenance = ElementProvenance(
                            docling_label="code_hotspot",
                            docling_page=page_no,
                            transformation=TransformationType.CODE_TRANSCRIBED,
                            transformation_reason=f"Language: {code_result.language}, from analysis hotspot",
                            llm_confidence=code_result.confidence,
                        )

                        code_element = ProcessedElement(
                            element_type=ElementType.CODE,
                            hierarchy_level=level,
                            original_text="",
                            processed_text=code_result.code,
                            code_content=code_result.code,
                            code_language=code_result.language,
                            is_pseudocode=code_result.is_pseudocode,
                            provenance=code_provenance,
                        )

                        result.elements.append(code_element)
                        result.code_blocks_transcribed += 1
                        result.llm_calls += 1
                    else:
                        result.llm_failures += 1
                        result.errors.append(f"Code transcription failed on page {page_no}")
                        result.llm_calls += 1

        # =====================================================================
        # Type-specific transformations
        # =====================================================================

        if element_type == ElementType.SECTION_HEADER and validate_headings:
            # LLM call: Validate heading level
            heading_result = await infer_heading_level(
                heading_text=text,
                docling_level=level,
                preceding_headings=recent_headings,
                document_type=document_type,
            )

            processed.original_heading_level = level
            processed.validated_heading_level = heading_result.inferred_level

            # Track if this was a fallback (error case)
            is_fallback = "due to error" in heading_result.reasoning.lower()
            if is_fallback:
                result.llm_failures += 1
                result.errors.append(f"Heading inference failed for '{text[:30]}...'")

            if heading_result.inferred_level != level:
                provenance.transformation = TransformationType.HEADING_LEVEL_ADJUSTED
                provenance.transformation_reason = heading_result.reasoning
                provenance.llm_confidence = heading_result.confidence

            # Update context for future headings
            recent_headings.append((text, heading_result.inferred_level))
            if len(recent_headings) > 10:
                recent_headings.pop(0)

            result.headings_validated += 1
            result.llm_calls += 1

        elif element_type == ElementType.PICTURE and generate_alt_texts:
            # LLM call: Generate alt text
            if page_images and bbox:
                # Crop image from page
                page_img = page_images.get(page_no)
                if page_img:
                    # Get caption if available
                    caption = ""
                    if hasattr(element, "caption_text"):
                        caption = element.caption_text(doc=doc) or ""

                    # Get surrounding text for context
                    # (simplified - could look at adjacent elements)
                    surrounding = ""

                    alt_result = await generate_alt_text(
                        image=page_img,  # TODO: crop to bbox
                        caption=caption,
                        document_type=document_type,
                        surrounding_text=surrounding,
                    )

                    processed.alt_text = alt_result.alt_text
                    provenance.transformation = TransformationType.ALT_TEXT_GENERATED
                    provenance.transformation_reason = f"Generated for {alt_result.figure_type}"
                    provenance.llm_confidence = alt_result.confidence

                    # Track if this was a fallback (error case)
                    if alt_result.confidence == 0.0 or alt_result.alt_text == "[Image description unavailable]":
                        result.llm_failures += 1
                        result.errors.append(f"Alt text generation failed for picture on page {page_no}")

                    result.figures_processed += 1
                    result.llm_calls += 1

        elif element_type == ElementType.TABLE and analyze_tables:
            # LLM call: Analyze table semantics
            table_image = None
            if hasattr(element, "image") and element.image:
                if hasattr(element.image, "pil_image"):
                    table_image = element.image.pil_image

            # Get Docling's table export if available
            table_markdown = ""
            if hasattr(element, "export_to_markdown"):
                try:
                    table_markdown = element.export_to_markdown()
                except Exception:
                    pass

            caption = ""
            if hasattr(element, "caption_text"):
                caption = element.caption_text(doc=doc) or ""

            table_result = await analyze_table(
                table_image=table_image,
                table_markdown=table_markdown,
                caption=caption,
            )

            processed.table_markdown = table_result.markdown
            processed.has_header_row = table_result.has_header_row
            provenance.transformation = TransformationType.TABLE_SEMANTICS_ADDED
            provenance.transformation_reason = f"Table type: {table_result.table_type}"
            provenance.llm_confidence = table_result.confidence

            # Track if this was a fallback (error case)
            if table_result.confidence == 0.0 or table_result.table_type == "unknown":
                result.llm_failures += 1
                result.errors.append(f"Table analysis failed for table on page {page_no}")

            result.tables_processed += 1
            result.llm_calls += 1

        elif element_type == ElementType.FORMULA and transcribe_formulas:
            # LLM call: Transcribe formula to LaTeX
            # Only transcribe if analysis identified equations on this page, or if no analysis
            should_transcribe = (
                page_no in pages_with_equations or
                not analysis or
                len(pages_with_equations) == 0  # No analysis, transcribe all formulas
            )

            if should_transcribe and page_images:
                page_img = page_images.get(page_no)
                if page_img:
                    formula_result = await transcribe_formula(
                        image=page_img,  # TODO: crop to bbox when available
                        docling_text=text,
                        context="",
                    )

                    processed.formula_latex = formula_result.latex
                    processed.formula_plain_text = formula_result.plain_text
                    provenance.transformation = TransformationType.FORMULA_TRANSCRIBED
                    provenance.transformation_reason = f"Formula type: {formula_result.formula_type}"
                    provenance.llm_confidence = formula_result.confidence

                    # Track if this was a fallback
                    if formula_result.confidence == 0.0:
                        result.llm_failures += 1
                        result.errors.append(f"Formula transcription failed on page {page_no}")

                    result.formulas_transcribed += 1
                    result.llm_calls += 1

        elif element_type == ElementType.LIST_ITEM:
            # Collect list items for batched processing
            current_list_items.append((element, level, text))

        # If we hit a non-list item and have collected list items, process them
        if element_type != ElementType.LIST_ITEM and current_list_items and classify_lists:
            await _process_list_batch(current_list_items, result)
            current_list_items = []

        # Add to result
        result.elements.append(processed)

    # Process any remaining list items
    if current_list_items and classify_lists:
        await _process_list_batch(current_list_items, result)

    logger.info(
        f"Processing complete: {result.total_elements} elements, "
        f"{result.llm_calls} LLM calls, "
        f"{result.figures_processed} figures, "
        f"{result.tables_processed} tables, "
        f"{result.headings_validated} headings, "
        f"{result.formulas_transcribed} formulas, "
        f"{result.code_blocks_transcribed} code blocks"
    )

    return result


async def _process_list_batch(
    items: list[tuple[Any, int, str]],
    result: ProcessingResult,
) -> None:
    """Process a batch of list items together."""
    if not items:
        return

    texts = [text for _, _, text in items]

    list_result = await classify_list_type(texts)
    result.llm_calls += 1

    # Update the processed elements that correspond to these list items
    # (They should be the most recent list_item elements in result.elements)
    list_elements = [
        e for e in result.elements
        if e.element_type == ElementType.LIST_ITEM
    ][-len(items):]

    for elem in list_elements:
        if elem.provenance:
            elem.provenance.transformation = TransformationType.LIST_TYPE_CLASSIFIED
            elem.provenance.transformation_reason = f"Classified as {list_result.list_type}"
            elem.provenance.llm_confidence = list_result.confidence
