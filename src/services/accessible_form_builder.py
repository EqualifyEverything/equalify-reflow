"""End-to-end builder: PDF -> accessible form.

Ties the pieces together in one call:

1. Read the PDF's authoritative AcroForm fields (the write-back skeleton).
2. Optionally enrich them with the Form Field Mapper agent's plain-language
   descriptions (Docling text extraction -> agent).
3. Merge and return a render-ready :class:`AccessibleForm`.

The Docling extraction and agent calls are injectable so the wiring can be tested
without a live model/Docling stack. If enrichment fails for any reason, the build
degrades gracefully to an AcroForm-only form rather than failing the request — a
usable form without rich descriptions beats no form at all.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .accessible_form import (
    AccessibleForm,
    _assign_agent_entries,
    build_form,
    extract_acroform_fields,
    fields_from_markdown,
)

logger = logging.getLogger(__name__)

ExtractText = Callable[..., Awaitable[str]]  # (pdf_bytes, *, ocr=False) -> markdown
RunMapper = Callable[..., Awaitable[Any]]  # (markdown, *, infer=False) -> FormFieldMapOutput
ExtractVision = Callable[[bytes], Awaitable[Any]]  # (pdf_bytes) -> VisionExtraction | list[AccessibleFormField]


def _title_from_filename(name: str | None) -> str:
    """Derive a readable title from a PDF filename (extension + separators stripped)."""
    if not name:
        return ""
    import os
    import re

    stem = os.path.splitext(os.path.basename(name))[0]
    return re.sub(r"[_\-]+", " ", stem).strip()


async def _default_extract_text(pdf_bytes: bytes, *, ocr: bool = False) -> str:
    if not ocr:
        from .pdf_extractor import extract_pdf_text

        return await extract_pdf_text(pdf_bytes)

    # Fieldless / scanned PDFs have no text layer, so enable OCR so the agent has
    # something to read. (Reflow's docling-serve runs OCR for scanned documents.)
    from .docling_serve_client import get_docling_client

    client = get_docling_client()
    resp = await client.convert(
        pdf_bytes,
        "document.pdf",
        do_ocr=True,
        ocr_lang=["en"],
        do_table_structure=True,
        include_images=False,
    )
    return resp.md_content or ""


async def _default_run_mapper(markdown: str, *, infer: bool = False) -> Any:
    from .form_field_mapper import run_form_field_mapper

    output, _in, _out = await run_form_field_mapper(markdown, infer=infer)
    return output


async def _default_extract_vision(pdf_bytes: bytes) -> list:
    from .vision_form_extractor import extract_fields_with_vision

    return await extract_fields_with_vision(pdf_bytes)


async def _attach_descriptions(
    fields: list, markdown: str, run_mapper: RunMapper | None
) -> None:
    """Best-effort: ask the agent to describe deterministically-discovered fields.

    Used for fieldless forms when ``enrich=True``. The fields themselves come from
    the deterministic parser; the agent only supplies one-sentence descriptions,
    matched back to the fields by token overlap. If the agent returns nothing (it
    can decline on sensitive content), the fields keep their labels and we move on.
    """
    _run = run_mapper or _default_run_mapper
    try:
        output = await _run(markdown, infer=True)
        agent_fields = [f.model_dump() for f in getattr(output, "fields", [])]
    except Exception as e:
        logger.warning("fieldless description enrichment failed (%s)", e)
        return
    if not agent_fields:
        logger.info("accessible-form: agent supplied no descriptions for fieldless form")
        return
    matches = _assign_agent_entries(fields, agent_fields)
    attached = 0
    for i, fld in enumerate(fields):
        match = matches.get(i)
        if match and match.get("description"):
            fld.description = str(match["description"])
            attached += 1
    logger.info("accessible-form: attached %d/%d descriptions (fieldless)", attached, len(fields))


async def build_accessible_form_from_pdf(
    pdf_bytes: bytes,
    *,
    enrich: bool = True,
    title: str | None = None,
    instructions: str = "",
    source_name: str | None = None,
    extract_text: ExtractText | None = None,
    run_mapper: RunMapper | None = None,
    extract_vision: ExtractVision | None = None,
) -> AccessibleForm:
    """Build an :class:`AccessibleForm` from a PDF, end to end.

    Args:
        pdf_bytes: The source PDF.
        enrich: If True, run Docling extraction + the Form Field Mapper agent to
            add plain-language descriptions/sections. If False, use only the
            PDF's AcroForm fields (no model calls).
        title: Optional title override.
        instructions: Optional intro text shown atop the form.
        extract_text: Injectable async ``(pdf_bytes) -> markdown`` (defaults to
            Docling text extraction).
        run_mapper: Injectable async ``(markdown) -> FormFieldMapOutput``
            (defaults to the shared agent runner).

    Returns:
        A render-ready :class:`AccessibleForm`. ``source`` is ``"merged"`` when
        AcroForm fields are present, or ``"agent"`` for a flat PDF enriched only
        from the agent dictionary.
    """
    acro_fields = extract_acroform_fields(pdf_bytes)

    if not acro_fields:
        # Flat / scanned PDF: no embedded fields.
        resolved_title = title or _title_from_filename(source_name) or "Form"

        if enrich:
            # Vision: read the page images for fillable fields AND informational
            # reading content, so non-form pages (leaflets, instructions, FAQs) are
            # surfaced rather than dropped.
            _vision = extract_vision or _default_extract_vision
            try:
                vres = await _vision(pdf_bytes)
            except Exception as e:
                logger.warning("vision extraction unavailable (%s)", e)
                vres = None
            # Accept either a bare list of fields (legacy / injected fakes) or a
            # VisionExtraction carrying both fields and content.
            if isinstance(vres, list):
                vision_fields, vision_content = vres, []
            else:
                vision_fields = list(getattr(vres, "fields", []) or [])
                vision_content = list(getattr(vres, "content", []) or [])
            if vision_fields or vision_content:
                logger.info(
                    "accessible-form: vision found %d field(s), %d content block(s) (fieldless)",
                    len(vision_fields), len(vision_content),
                )
                return AccessibleForm(
                    title=resolved_title, instructions=instructions,
                    source="agent", fields=vision_fields, content=vision_content,
                )
            logger.info("accessible-form: vision returned nothing; falling back to OCR parse")

        # Deterministic OCR-text parse: the enrich=false path, and the fallback when
        # vision yields nothing. No model needed; answers come back as a responses doc.
        _extract = extract_text or _default_extract_text
        try:
            markdown = await _extract(pdf_bytes, ocr=True)
        except Exception as e:
            logger.warning("OCR extraction failed for fieldless PDF (%s)", e)
            markdown = ""
        fields = fields_from_markdown(markdown)
        logger.info(
            "accessible-form: parsed %d fields from %d OCR chars (fieldless)",
            len(fields), len(markdown or ""),
        )
        if enrich and fields and (markdown or "").strip():
            await _attach_descriptions(fields, markdown, run_mapper)
        return AccessibleForm(
            title=resolved_title, instructions=instructions, source="agent", fields=fields,
        )

    field_map: dict[str, Any] | None = None
    if enrich:
        _extract = extract_text or _default_extract_text
        _run = run_mapper or _default_run_mapper
        needs_ocr = not acro_fields  # no embedded fields => scanned/flat; OCR to read it
        try:
            markdown = await _extract(pdf_bytes, ocr=needs_ocr)
            logger.info(
                "accessible-form enrich: extracted %d markdown chars (ocr=%s)",
                len(markdown or ""), needs_ocr,
            )
            output = await _run(markdown, infer=needs_ocr)
            field_map = {
                "document_kind": getattr(output, "document_kind", "") or "",
                "fields": [f.model_dump() for f in getattr(output, "fields", [])],
            }
            n_fields = len(field_map["fields"])
            logger.info("accessible-form enrich: agent inferred %d fields", n_fields)
            if n_fields == 0:
                logger.info(
                    "accessible-form enrich: 0 fields; doc_kind=%r notes=%r; markdown preview: %r",
                    getattr(output, "document_kind", ""),
                    getattr(output, "notes", ""),
                    (markdown or "")[:500],
                )
        except Exception as e:
            logger.warning("Form field enrichment failed (%s); continuing without inferred fields", e)
            field_map = None

    resolved_title = (
        title
        or (field_map or {}).get("document_kind")
        or _title_from_filename(source_name)
        or "Form"
    )
    return build_form(
        title=str(resolved_title),
        acroform_fields=acro_fields,
        field_map=field_map,
        instructions=instructions,
    )
