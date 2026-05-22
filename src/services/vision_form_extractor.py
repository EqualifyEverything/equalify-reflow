"""Vision-based field extraction for scanned / flat forms.

Renders each PDF page to an image and asks the model to list the fields with
approximate answer-area positions. This (a) captures fields the OCR-text parser
misses, and (b) yields positions so answers can be drawn back onto the page.
Positions are model-estimated, so they are approximate.

``render_pdf_pages`` is pure (pypdfium2 + Pillow) and unit-testable. The vision
call requires a model backend and is exercised live.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass, field as _dc_field
from typing import Any

from .accessible_form import AccessibleFormField, ControlType, FormContentBlock, FormOption

logger = logging.getLogger(__name__)


@dataclass
class VisionExtraction:
    """What reading a PDF's page images yields: fillable fields plus the
    informational reading content (leaflets, instructions, FAQs) found alongside
    or instead of fields, so no page's text is discarded."""

    fields: list[AccessibleFormField] = _dc_field(default_factory=list)
    content: list[FormContentBlock] = _dc_field(default_factory=list)

# FieldType (agent vocabulary) -> ControlType (what the renderer understands).
_FIELDTYPE_TO_CONTROL: dict[str, ControlType] = {
    "text": "text", "date": "date", "number": "number", "email": "email",
    "phone": "tel", "address": "textarea", "checkbox": "checkbox", "radio": "radio",
    "select": "select", "signature": "signature", "initial": "text",
    "attachment": "text", "other": "text",
}


def _qualify_sections_by_form(
    fields: list[AccessibleFormField], page_form_names: dict[int, str]
) -> None:
    """Prefix each field's section with its form's name for multi-form packets.

    When a packet bundles several distinct forms (e.g. a motion and a complaint),
    the same caption fields recur on each, and identical section names ("Case
    Caption") would make a screen-reader user unable to tell them apart. If two or
    more distinct ``form_name`` values are present, prefix every field's section
    with its page's form name so the rendered fieldset/heading distinguishes them.
    A no-op for single-form packets (fewer than two distinct names).
    """
    distinct = {n for n in page_form_names.values() if n}
    if len(distinct) < 2:
        return
    for f in fields:
        form_name = page_form_names.get(f.page if f.page is not None else -1, "")
        if not form_name:
            continue
        base = (f.section or "").strip()
        f.section = f"{form_name} — {base}" if base else form_name


# A "thin" heading that is really a list step, not a section: a number/letter
# marker ("1)", "2.", "(a)") or a bullet, optionally trailed by a single short
# word ("1) Go", "2) Stay"). Multi-word headings never match.
_MARKER_HEADING_RE = re.compile(
    r"^\s*(?:[(\[]?\s*(?:\d{1,2}|[A-Za-z])\s*[)\].:]|[•·\-*])\s*\w{0,15}\s*$"
)


def _is_marker_heading(heading: str) -> bool:
    """True if ``heading`` is just a list marker (a step), not a real heading."""
    return bool(heading and _MARKER_HEADING_RE.match(heading.strip()))


def _merge_marker_blocks(content: list[FormContentBlock]) -> list[FormContentBlock]:
    """Fold thin list-step blocks into the previous same-page block's paragraphs.

    The model is told to keep numbered "what to do" steps ("1) Go", "2) Stay") as
    paragraphs under their heading, but is inconsistent about it. This guarantees
    the merge deterministically: a step block's heading is preserved as a paragraph
    (so no text is lost) and its paragraphs are appended to the previous block. A
    marker block with no preceding block on the same page is left untouched.
    """
    merged: list[FormContentBlock] = []
    for block in content:
        if merged and merged[-1].page == block.page and _is_marker_heading(block.heading):
            prev = merged[-1]
            if block.heading.strip():
                prev.paragraphs.append(block.heading.strip())
            prev.paragraphs.extend(block.paragraphs)
            continue
        merged.append(block)
    return merged


async def _run_page_with_retry(
    agent: Any, message: list, idx: int, total: int, *,
    attempts: int = 2, base_delay: float = 1.0, retry_empty: bool = True,
) -> Any:
    """Run the vision agent on one page image, retrying transient failures.

    A single transient error (rate limit, timeout, malformed structured output)
    used to drop a whole page's fields/content silently. Retry the call a bounded
    number of times — and once more if a successful call comes back completely
    empty — surfacing every failure in the logs. Returns the model output, or
    ``None`` only if every attempt raised.
    """
    last_output: Any = None
    for attempt in range(1, attempts + 1):
        try:
            result = await agent.run(message)
        except Exception as e:
            if attempt < attempts:
                logger.warning(
                    "vision page %d/%d attempt %d/%d failed (%s); retrying",
                    idx + 1, total, attempt, attempts, e,
                )
                await asyncio.sleep(base_delay * attempt)
                continue
            logger.error(
                "vision page %d/%d dropped after %d attempt(s): %s",
                idx + 1, total, attempts, e,
            )
            return last_output
        output = result.output
        empty = not getattr(output, "fields", None) and not getattr(output, "content", None)
        if empty and retry_empty and attempt < attempts:
            logger.warning(
                "vision page %d/%d returned nothing (kind=%s); retrying once",
                idx + 1, total, getattr(output, "page_kind", "?"),
            )
            last_output = output
            await asyncio.sleep(base_delay * attempt)
            continue
        return output
    return last_output


def render_pdf_pages(pdf_bytes: bytes, *, scale: float = 1.5, max_pages: int = 15) -> list[bytes]:
    """Render up to ``max_pages`` PDF pages to PNG bytes (one per page)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(pdf_bytes)
    pages: list[bytes] = []
    for i in range(min(len(pdf), max_pages)):
        pil = pdf[i].render(scale=scale).to_pil().convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        pages.append(buf.getvalue())
    return pages


async def extract_fields_with_vision(
    pdf_bytes: bytes, *, scale: float = 1.5, max_pages: int = 15
) -> VisionExtraction:
    """Read a PDF's page images for fillable fields and informational content.

    Returns a :class:`VisionExtraction` whose ``fields`` are the fillable controls
    (each stamped with its 0-based ``page``) and whose ``content`` holds the
    readable text of informational pages (leaflets, instructions, FAQs), also
    page-stamped so the renderer can interleave both in reading order. Returns an
    empty result if the model is unavailable (caller falls back to the OCR parser).
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import BinaryContent

    from ..agents.model_factory import get_model_for_tier
    from ..agents.model_tiers import ModelTier
    from ..agents.prompts.vision_form_fields import VISION_FORM_SYSTEM_PROMPT, VisionPageOutput

    pages = render_pdf_pages(pdf_bytes, scale=scale, max_pages=max_pages)
    model = get_model_for_tier(ModelTier.REASONING)
    agent: Agent[None, VisionPageOutput] = Agent(
        model=model,
        output_type=VisionPageOutput,
        system_prompt=VISION_FORM_SYSTEM_PROMPT,
        # Dense pages (demographics, symptom checklists) can have 40+ fields; the
        # default token budget truncates that structured output and the page is
        # then dropped. Give it generous headroom.
        model_settings={"max_tokens": 8192},
    )

    fields: list[AccessibleFormField] = []
    content: list[FormContentBlock] = []
    page_form_names: dict[int, str] = {}
    seen: set[str] = set()
    for idx, png in enumerate(pages):
        message = [
            BinaryContent(data=png, media_type="image/png"),
            f"This is page {idx + 1} of {len(pages)}. List every fillable field; if the "
            f"page is informational (a leaflet, instructions, or FAQ), capture its readable "
            f"text as content blocks instead.",
        ]
        output = await _run_page_with_retry(agent, message, idx, len(pages))
        if output is None:
            continue  # every attempt failed; already logged at ERROR
        page_fields = output.fields
        page_content = getattr(output, "content", []) or []
        page_kind = getattr(output, "page_kind", "form")
        page_form_names[idx] = (getattr(output, "form_name", "") or "").strip()
        logger.info(
            "vision page %d/%d: kind=%s, %d field(s), %d content block(s)",
            idx + 1, len(pages), page_kind, len(page_fields), len(page_content),
        )
        if not page_fields and not page_content:
            logger.warning(
                "vision page %d/%d produced no fields and no content (kind=%s)",
                idx + 1, len(pages), page_kind,
            )
        for vf in page_fields:
            label = (vf.label or "").strip()
            if len(label) < 2:
                continue
            base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:60] or "field"
            key, n = base, 2
            while key in seen:
                key = f"{base}_{n}"
                n += 1
            seen.add(key)
            fields.append(AccessibleFormField(
                pdf_field=key,
                control=_FIELDTYPE_TO_CONTROL.get(str(vf.field_type), "text"),
                label=label,
                description=(getattr(vf, "description", "") or "").strip(),
                options=[FormOption(value=o, label=o) for o in (vf.options or [])],
                required=bool(getattr(vf, "required", False)),
                section=(getattr(vf, "section", "") or "").strip(),
                page=idx,
            ))
        for cb in page_content:
            heading = (getattr(cb, "heading", "") or "").strip()
            paragraphs = [p.strip() for p in (getattr(cb, "paragraphs", []) or []) if (p or "").strip()]
            if not heading and not paragraphs:
                continue
            content.append(FormContentBlock(heading=heading, paragraphs=paragraphs, page=idx))

    # Fold any thin list-step blocks ("1) Go", "2) Stay") the model split out back
    # into their parent block, regardless of model variance.
    content = _merge_marker_blocks(content)

    # Multi-form packets: distinguish repeated captions by prefixing sections with
    # the form name (no-op when the packet holds a single form).
    _qualify_sections_by_form(fields, page_form_names)
    logger.info(
        "vision extraction: %d field(s), %d content block(s) across %d page(s)",
        len(fields), len(content), len(pages),
    )
    return VisionExtraction(fields=fields, content=content)
