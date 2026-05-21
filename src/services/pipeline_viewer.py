"""Pipeline Viewer service — versioned step-by-step PDF processing.

Starts with Docling extraction (v0) and can grow one step at a time.
Each step reads the previous version's markdown and produces a new version.

Usage:
    service = PipelineViewerService()
    result = await service.process(file_content, filename, images_scale=2.0)
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import html
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import settings
from .pipeline_viewer_models import (
    CodeBlockInfo,
    DocumentChange,
    FigureData,
    FootnoteInfo,
    FormFieldInfo,
    FormFieldsPageOutput,
    FormFieldType,
    HeadingReconciliationOutput,
    ImageDescriptionResult,
    LayoutType,
    ListReconstructionResult,
    OutlineEntry,
    PageAttributes,
    PageCorrectionResult,
    PipelineViewerResult,
    SectionEntry,
    SectionMap,
    StepResult,
    StructurePageOutput,
    StructureResult,
)

logger = logging.getLogger(__name__)

PROCEDURES_DIR = Path(__file__).parent.parent / "agents" / "prompts" / "procedures"
PAGE_AGENT_SEMAPHORE_LIMIT = 3

# Fuzzy match thresholds — named for clarity and consistent tuning
HEADING_MATCH_THRESHOLD = 0.8  # Heading level correction (strict)
SECTION_MAP_MATCH_THRESHOLD = 0.6  # Section map building (lenient)
CODE_BLOCK_MATCH_THRESHOLD = 0.6  # Code block first-line matching
FUZZY_LINE_MATCH_THRESHOLD = 0.6  # General fuzzy line finding (default)


def _load_fragment(name: str) -> str:
    """Load a procedure fragment by relative name.

    Args:
        name: Fragment path relative to page_correction/, without .md extension.
              e.g. "base", "layout/double_column", "content/academic".
    """
    path = PROCEDURES_DIR / "page_correction" / f"{name}.md"
    if not path.exists():
        logger.warning(f"No fragment at {path}")
        return ""
    return path.read_text()


def _compose_page_prompt(attrs: PageAttributes) -> str:
    """Compose a correction prompt from page attribute fragments.

    Always includes the base fragment. Layout fragment is selected by
    attrs.layout. Boolean flags toggle content and quality fragments.

    For poster layouts, the ``base_create`` fragment is used instead of
    ``base`` to switch the agent into "creation mode" — composing
    markdown from the page image rather than correcting existing text.
    """
    fragments = []

    # Select base prompt by mode
    if attrs.layout == LayoutType.POSTER:
        base = _load_fragment("base_create")
    else:
        base = _load_fragment("base")
    if base:
        fragments.append(base)

    layout = _load_fragment(f"layout/{attrs.layout.value}")
    if layout:
        fragments.append(layout)

    if attrs.is_academic:
        acad = _load_fragment("content/academic")
        if acad:
            fragments.append(acad)

    if attrs.has_images:
        img = _load_fragment("content/has_images")
        if img:
            fragments.append(img)

    if attrs.has_tables:
        tbl = _load_fragment("content/has_tables")
        if tbl:
            fragments.append(tbl)

    if attrs.has_lists:
        lst = _load_fragment("content/has_lists")
        if lst:
            fragments.append(lst)

    if attrs.has_forms:
        frm = _load_fragment("content/has_forms")
        if frm:
            fragments.append(frm)

    if attrs.has_equations:
        eq = _load_fragment("content/has_equations")
        if eq:
            fragments.append(eq)

    if attrs.is_scanned:
        scan = _load_fragment("quality/scanned")
        if scan:
            fragments.append(scan)

    return "\n\n".join(fragments)


_MARKDOWN_TABLE_PATTERN = re.compile(
    r"((?:^\|.+\|[ \t]*\n)(?:^\|[-:| ]+\|[ \t]*\n)(?:^\|.+\|[ \t]*\n?)+)",
    re.MULTILINE,
)


def _extract_tables_from_markdown(
    page_markdown: str, page_number: int
) -> list:
    """Extract markdown tables from page text and assign ref_ids.

    Returns a list of TableData objects, one per table found.
    """
    from .pipeline_viewer_models import TableData

    tables = []
    for i, match in enumerate(_MARKDOWN_TABLE_PATTERN.finditer(page_markdown)):
        tables.append(
            TableData(
                ref_id=f"table-{i + 1}",
                page_number=page_number,
                markdown_content=match.group(0).rstrip("\n"),
            )
        )
    return tables


def _fuzzy_find_line(lines: list[str], target: str, threshold: float = FUZZY_LINE_MATCH_THRESHOLD) -> int:
    """Find the line index best matching *target* via fuzzy match.

    Returns the index of the best match, or -1 if nothing exceeds *threshold*.
    """
    target_lower = target.strip().lower()
    if not target_lower:
        return -1
    best_idx = -1
    best_ratio = 0.0
    for i, line in enumerate(lines):
        line_lower = line.strip().lower()
        if not line_lower:
            continue
        ratio = difflib.SequenceMatcher(None, target_lower, line_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i
    return best_idx if best_ratio >= threshold else -1


def _apply_code_block_fence(
    page_md: str,
    cb: CodeBlockInfo,
    page_num: int,
) -> tuple[str, DocumentChange | None]:
    """Locate a code block in *page_md* and wrap it in fences.

    Strategy:
      1. Check if the code is already inside a fenced block (```) — if so,
         just add the language tag if missing.
      2. Otherwise, fuzzy-find ``cb.first_line`` and ``cb.last_line`` among
         the markdown lines and wrap the region with ```language ... ```.

    Returns (new_markdown, change) or (original, None) if nothing matched.
    """
    lines = page_md.split("\n")

    # --- Case 1: already fenced (bare ``` or ```lang) --------------------------
    fence_pattern = re.compile(r"^```(\w*)\s*$")
    skip_to = -1
    for i, line in enumerate(lines):
        if i <= skip_to:
            continue
        m = fence_pattern.match(line)
        if not m:
            continue
        existing_lang = m.group(1)
        # Find matching close fence
        for j in range(i + 1, len(lines)):
            if lines[j].strip() == "```":
                # Check if the content inside matches cb.first_line
                first_line_ratio = difflib.SequenceMatcher(
                    None,
                    cb.first_line.strip().lower(),
                    lines[i + 1].strip().lower() if i + 1 < len(lines) else "",
                ).ratio()
                if first_line_ratio >= CODE_BLOCK_MATCH_THRESHOLD:
                    if existing_lang:
                        # Already fenced with a language tag — nothing to do
                        return page_md, None
                    # Tag the opening fence
                    old_fence = lines[i]
                    lines[i] = f"```{cb.language}"
                    return "\n".join(lines), DocumentChange(
                        page=page_num,
                        old_text=old_fence,
                        new_text=lines[i],
                        reasoning=(
                            f"Tagged existing code fence as {cb.language} "
                            f"(first line match {first_line_ratio:.0%})"
                        ),
                        stage="code_block",
                    )
                skip_to = j  # skip past this fenced block
                break  # close fence found but didn't match, keep looking

    # --- Case 2: unfenced code ------------------------------------------------
    start_idx = _fuzzy_find_line(lines, cb.first_line)
    if start_idx < 0:
        return page_md, None

    # Guard: skip if the matched line sits inside an existing fenced block
    fence_depth = 0
    for k in range(start_idx):
        if re.match(r"^```", lines[k]):
            fence_depth = 1 - fence_depth  # toggle open/close
    if fence_depth:
        return page_md, None

    end_idx = _fuzzy_find_line(lines[start_idx:], cb.last_line)
    if end_idx < 0:
        # last_line not found — try using just the start line as a single-line block
        end_idx = 0
    end_idx += start_idx  # convert to absolute index

    # Ensure end >= start
    if end_idx < start_idx:
        end_idx = start_idx

    # Extract the code region
    code_lines = lines[start_idx : end_idx + 1]
    old_text = "\n".join(code_lines)

    # Build fenced replacement
    new_text = f"```{cb.language}\n{old_text}\n```"

    # Splice into lines
    new_lines = lines[:start_idx] + [new_text] + lines[end_idx + 1 :]

    return "\n".join(new_lines), DocumentChange(
        page=page_num,
        old_text=old_text[:200] + ("..." if len(old_text) > 200 else ""),
        new_text=new_text[:200] + ("..." if len(new_text) > 200 else ""),
        reasoning=(
            f"Wrapped unfenced code in ```{cb.language} fence "
            f"(structure analysis: {cb.reasoning})"
        ),
        stage="code_block",
    )


# Markers Docling leaves behind when it flattens a form: runs of underscores
# (fill-in blanks), checkbox glyphs, and bracket checkboxes. Used to detect
# form pages deterministically, independent of the structure agent.
_FORM_UNDERLINE_RE = re.compile(r"_{4,}")
_FORM_CHECKBOX_RE = re.compile(r"[☐☑☒□◻❐❏]|\[\s?\]")


def _page_has_form_signals(page_md: str) -> bool:
    """Heuristically decide whether a page contains a fillable form.

    Keys on the typical artifacts Docling produces for forms — long underline
    runs (``____``) and checkbox glyphs (``☐``, ``[ ]``) — so form pages are
    caught even when the structure agent's ``has_forms`` flag misses them.
    """
    return bool(
        _FORM_UNDERLINE_RE.search(page_md) or _FORM_CHECKBOX_RE.search(page_md)
    )


def _render_form_field_html(field: FormFieldInfo, field_id: str) -> str:
    """Render a detected form field as an accessible, static HTML control.

    The output is a *representation* of the form for accessibility, not a
    working form: there is no surrounding ``<form>`` and inputs are marked
    ``readonly`` / ``disabled`` so they advertise their role and label to a
    screen reader without implying the document is fillable.

    Every control is paired with a programmatic label:
      - text / textarea / date / select use ``<label for=...>``,
      - checkbox / single fields put the label after the control,
      - radio_group / checkbox_group / multi-option select wrap options in a
        ``<fieldset>`` with the prompt as the ``<legend>``.
    """
    label = html.escape(field.label or "Field")
    required_attr = ' aria-required="true"' if field.required else ""
    req_marker = ' <abbr title="required">*</abbr>' if field.required else ""

    if field.field_type in (FormFieldType.TEXT, FormFieldType.DATE):
        input_type = "date" if field.field_type == FormFieldType.DATE else "text"
        return (
            f'<label for="{field_id}">{label}{req_marker}</label>\n'
            f'<input type="{input_type}" id="{field_id}" '
            f'name="{field_id}"{required_attr} readonly>'
        )

    if field.field_type == FormFieldType.TEXTAREA:
        return (
            f'<label for="{field_id}">{label}{req_marker}</label>\n'
            f'<textarea id="{field_id}" name="{field_id}"{required_attr} '
            f"readonly></textarea>"
        )

    if field.field_type == FormFieldType.SIGNATURE:
        return (
            f'<label for="{field_id}">{label}{req_marker}</label>\n'
            f'<input type="text" id="{field_id}" name="{field_id}" '
            f'aria-label="{label} (signature)"{required_attr} readonly>'
        )

    if field.field_type == FormFieldType.CHECKBOX:
        checked = " checked" if (field.options and field.options[0].checked) else ""
        return (
            f'<input type="checkbox" id="{field_id}" name="{field_id}"'
            f"{checked}{required_attr} disabled> "
            f'<label for="{field_id}">{label}{req_marker}</label>'
        )

    if field.field_type == FormFieldType.SELECT:
        opts = "\n".join(
            f'  <option{" selected" if o.checked else ""}>'
            f"{html.escape(o.label)}</option>"
            for o in field.options
        )
        return (
            f'<label for="{field_id}">{label}{req_marker}</label>\n'
            f'<select id="{field_id}" name="{field_id}"{required_attr} disabled>\n'
            f"{opts}\n"
            f"</select>"
        )

    # radio_group / checkbox_group — fieldset + legend, one control per option
    input_type = (
        "checkbox" if field.field_type == FormFieldType.CHECKBOX_GROUP else "radio"
    )
    parts = ["<fieldset>", f"  <legend>{label}{req_marker}</legend>"]
    for i, opt in enumerate(field.options):
        opt_id = f"{field_id}-{i}"
        checked = " checked" if opt.checked else ""
        # All radios in a group share a name; checkboxes get distinct names.
        name = field_id if input_type == "radio" else opt_id
        parts.append(
            f'  <input type="{input_type}" id="{opt_id}" name="{name}"'
            f"{checked} disabled> "
            f'<label for="{opt_id}">{html.escape(opt.label)}</label>'
        )
    parts.append("</fieldset>")
    return "\n".join(parts)


def _apply_form_field(
    page_md: str,
    field: FormFieldInfo,
    field_id: str,
) -> tuple[str, DocumentChange | None]:
    """Replace a detected field's ``anchor_text`` with accessible HTML.

    Fuzzy-finds the anchor line in *page_md* and swaps it for the rendered
    control. Returns ``(new_markdown, change)`` or ``(original, None)`` if the
    anchor could not be located.
    """
    lines = page_md.split("\n")
    idx = _fuzzy_find_line(lines, field.anchor_text)
    if idx < 0:
        return page_md, None

    new_html = _render_form_field_html(field, field_id)
    old_text = lines[idx]
    new_lines = lines[:idx] + [new_html] + lines[idx + 1 :]

    return "\n".join(new_lines), DocumentChange(
        page=field.page,
        old_text=old_text[:200] + ("..." if len(old_text) > 200 else ""),
        new_text=new_html[:200] + ("..." if len(new_html) > 200 else ""),
        reasoning=(
            f"Replaced {field.field_type.value} field "
            f"'{field.label}' with accessible HTML ({field.reasoning})"
        ),
        stage="form_field",
    )


def _replace_image_placeholders(
    markdown: str,
    figures: list[FigureData],
) -> str:
    """Replace ``<!-- image -->`` placeholders with markdown image syntax.

    Docling emits one ``<!-- image -->`` placeholder per picture in document
    order.  The Nth placeholder corresponds to the Nth figure.  We walk through
    both lists sequentially: for each figure we replace the *first* remaining
    ``<!-- image -->`` with ``![caption](figures/ref_id.png)``.

    Uses a relative ``figures/`` path so the markdown is portable (works in ZIP
    bundles or relative to the S3 result prefix).

    If there are more placeholders than figures, the extras are left as-is.
    If there are more figures than placeholders (shouldn't happen), extras are
    ignored.
    """
    placeholder = "<!-- image -->"

    for fig in figures:
        idx = markdown.find(placeholder)
        if idx == -1:
            break
        alt = fig.caption.replace("]", "\\]") if fig.caption else ""
        image_ref = f"![{alt}](figures/{fig.ref_id}.png)"
        markdown = markdown[:idx] + image_ref + markdown[idx + len(placeholder) :]

    return markdown


def _detect_page_columns(doc: Any, page_no: int) -> str:
    """Infer column layout from Docling item bounding boxes.

    Analyses the horizontal positions of text items on a page to determine
    whether they form one column or two.  Uses median item width (robust
    against full-width titles/abstracts) and checks for items in both
    halves of the page.

    Returns:
        "double_column", "single_column", or "unknown".
    """
    page = doc.pages.get(page_no)
    if not page or not page.size or page.size.width <= 0:
        return "unknown"

    page_width = page.size.width

    item_widths: list[float] = []
    item_centers: list[float] = []

    for item, _level in doc.iterate_items(page_no=page_no):
        if not hasattr(item, "prov") or not item.prov:
            continue
        for p in item.prov:
            if p.page_no != page_no:
                continue
            bbox = p.bbox
            width = abs(bbox.r - bbox.l)
            # Skip very narrow items (footnote markers, page numbers, etc.)
            if width < page_width * 0.1:
                continue
            item_widths.append(width / page_width)
            item_centers.append(((bbox.l + bbox.r) / 2) / page_width)

    if len(item_widths) < 4:
        return "unknown"

    sorted_widths = sorted(item_widths)
    median_width = sorted_widths[len(sorted_widths) // 2]

    if median_width < 0.55:
        # Most items are narrow — verify items exist in both halves
        left_count = sum(1 for c in item_centers if c < 0.45)
        right_count = sum(1 for c in item_centers if c > 0.55)
        if left_count >= 2 and right_count >= 2:
            return "double_column"

    return "single_column"


def _is_running_header(line: str, doc_title: str, page_num: int) -> bool:
    """Detect if *line* is a running page header (repeated title at top of page).

    Never matches markdown headings (lines starting with #).
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False

    if doc_title:
        # Fuzzy-match against doc title (strip trailing digits/spaces)
        cleaned = stripped.lower().rstrip("0123456789 ")
        ratio = difflib.SequenceMatcher(None, cleaned, doc_title.lower()).ratio()
        if ratio >= 0.7:
            return True

    # Pattern: "{title text} {page_num}" at end of line
    m = re.match(r"^(.+?)\s+(\d+)\s*$", stripped)
    if m and int(m.group(2)) == page_num and len(m.group(1)) < 80:
        return True

    return False


def _is_running_footer(line: str, page_num: int) -> bool:
    """Detect if *line* is a running page footer (page number, etc.)."""
    stripped = line.strip()
    if not stripped:
        return False

    # Bare digit close to page number
    if stripped.isdigit() and abs(int(stripped) - page_num) <= 1:
        return True

    # "Page N" or "N of M"
    if re.match(r"^[Pp]age\s+\d+", stripped):
        return True
    if re.match(r"^\d+\s+of\s+\d+$", stripped):
        return True

    return False


class PipelineViewerService:
    """Versioned pipeline that stores full markdown at every step.

    v0: Docling extraction (raw PDF -> markdown)
    Phase 1: Structure analysis (metadata only — outline, page types, footnotes)
    v1: Page content corrections (parallel, procedure-loaded by doc type)
    v2: Cross-page fixes (boundaries + footnotes on assembled document)
    v3: Cleanup (deterministic)
    """

    async def process(
        self,
        file_content: bytes,
        filename: str,
        *,
        images_scale: float = 2.0,
        do_table_structure: bool = True,
        enable_structure: bool = False,
        enable_page_content: bool = False,
        enable_boundaries: bool = False,
        ocr_languages: list[str] | None = None,
        on_phase: Callable[[str, str, int, int], None] | None = None,
    ) -> PipelineViewerResult:
        """Run the pipeline on a PDF.

        Args:
            file_content: Raw PDF bytes.
            filename: Original filename.
            images_scale: Scale factor for page image generation.
            do_table_structure: Whether to run Docling table structure recognition.
            enable_structure: Run Phase 1 structure analysis.
            enable_page_content: Run Phase 2 per-page corrections.
            enable_boundaries: Run Phase 3 cross-page fixes.
            ocr_languages: Tesseract OCR language codes for scanned documents.
            on_phase: Optional callback invoked before each pipeline step.
                Signature: (phase_name, display_name, step_number, total_steps).

        Returns:
            PipelineViewerResult with versioned markdowns, images, figures, and stats.
        """
        from .pdf_classifier import classify_pdf, enrich_classification

        result = PipelineViewerResult(filename=filename, total_pages=0)

        # ── Compute total steps for progress reporting ──────────
        phase_total = 1  # docling is always step 1
        if enable_structure:
            phase_total += 3  # structure, heading_reconciliation, heading_levels
        if enable_page_content and enable_structure:
            phase_total += 2  # page_content, code_blocks
        if enable_boundaries and enable_structure:
            phase_total += 2  # boundaries, cleanup
        phase_step = 0

        def _emit_phase(name: str, display_name: str) -> None:
            nonlocal phase_step
            phase_step += 1
            if on_phase:
                on_phase(name, display_name, phase_step, phase_total)

        # Pre-flight PDF classification
        classification = classify_pdf(file_content)

        if classification.has_errors:
            error_msg = "; ".join(classification.error_messages)
            result.warnings = classification.warning_messages
            result.steps.append(
                StepResult(
                    name="classification",
                    display_name="PDF Classification",
                    version_after="v0",
                    elapsed_ms=classification.elapsed_ms,
                    error=error_msg,
                    metadata={
                        "document_type": classification.document_type.value,
                        "findings": [f.model_dump() for f in classification.findings],
                        "pdf_metadata": classification.metadata.model_dump(),
                    },
                )
            )
            return result

        result.warnings = classification.warning_messages

        _emit_phase("docling", "Docling Extraction")
        await self._step_docling(result, file_content, filename, images_scale, do_table_structure)

        # Enrich classification with post-extraction signals
        enrich_classification(
            classification,
            total_pages=result.total_pages,
            total_chars=result.stats.get("total_chars", 0),
            figure_count=result.stats.get("figure_count", 0),
            layout_hints=result.stats.get("layout_hints", {}),
        )
        result.warnings = classification.warning_messages
        result.stats["classification"] = {
            "document_type": classification.document_type.value,
            "findings_count": len(classification.findings),
            "elapsed_ms": classification.elapsed_ms,
        }
        # Document-level form signal: count of interactive AcroForm widgets.
        # Docling often drops form-field content from the extracted markdown
        # (especially on later pages), so the form step uses this to decide
        # whether to scan every page with the vision agent.
        result.stats["form_field_count"] = classification.metadata.form_field_count

        # ── Tesseract OCR re-run for scanned documents ──────────
        # If the classifier flagged a scanned document (< 50 chars/page),
        # re-run Docling with OCR enabled.  Documents with a few chars from
        # image captions still need OCR for their actual text content.
        if result.stats.get("is_likely_scanned", False):
            from .pdf_classifier import FINDING_SCAN_PRODUCER, FINDING_SCANNED

            scanned_codes = {FINDING_SCANNED, FINDING_SCAN_PRODUCER}
            finding_codes = {f.code for f in classification.findings}
            if finding_codes & scanned_codes:
                # OCR wasn't known upfront — adjust the total and emit phase
                phase_total += 1
                _emit_phase("docling_ocr", "OCR Re-extraction")
                effective_langs = ocr_languages or settings.ocr_default_languages
                try:
                    await self._step_docling_ocr(
                        result,
                        file_content,
                        filename,
                        images_scale,
                        do_table_structure,
                        languages=effective_langs,
                    )
                    # Update scanned finding to reflect that OCR was applied
                    for finding in classification.findings:
                        if finding.code in scanned_codes:
                            finding.message = (
                                "Document appears to be scanned. OCR has been "
                                "applied, which may increase processing time and introduce "
                                "character recognition errors."
                            )
                    # Re-enrich classification with post-OCR stats
                    enrich_classification(
                        classification,
                        total_pages=result.total_pages,
                        total_chars=result.stats.get("total_chars", 0),
                        figure_count=result.stats.get("figure_count", 0),
                        layout_hints=result.stats.get("layout_hints", {}),
                    )
                    result.warnings = classification.warning_messages
                    result.stats["classification"] = {
                        "document_type": classification.document_type.value,
                        "findings_count": len(classification.findings),
                        "elapsed_ms": classification.elapsed_ms,
                    }
                except Exception as e:
                    logger.error(f"OCR re-extraction failed: {e}")
                    # Continue with the non-OCR extraction already in v0
        # ── End Tesseract OCR re-run ────────────────────────────

        # ── Empty-content guard ─────────────────────────────────
        # Scanned / image-only PDFs that produce zero extractable text
        # cannot benefit from LLM phases.  Skip them to save tokens.
        if result.stats.get("total_chars", 0) == 0:
            skipped_meta = {"reason": "empty_content", "total_chars": 0}

            if enable_structure:
                for name, display in (
                    ("structure", "Structure Analysis"),
                    ("heading_reconciliation", "Heading Reconciliation"),
                    ("heading_levels", "Heading Levels"),
                ):
                    result.steps.append(
                        StepResult(
                            name=name,
                            display_name=display,
                            version_before="v0",
                            version_after="v0",
                            elapsed_ms=0,
                            changes=[],
                            metadata=skipped_meta,
                            skipped=True,
                        )
                    )

            if enable_page_content:
                for name, display in (
                    ("page_content", "Page Content Corrections"),
                    ("code_blocks", "Code Block Languages"),
                ):
                    result.steps.append(
                        StepResult(
                            name=name,
                            display_name=display,
                            version_before="v0",
                            version_after="v0",
                            elapsed_ms=0,
                            changes=[],
                            metadata=skipped_meta,
                            skipped=True,
                        )
                    )

            if enable_boundaries:
                for name, display in (
                    ("boundaries", "Cross-Page Fixes"),
                    ("cleanup", "Final Cleanup"),
                ):
                    result.steps.append(
                        StepResult(
                            name=name,
                            display_name=display,
                            version_before="v0",
                            version_after="v0",
                            elapsed_ms=0,
                            changes=[],
                            metadata=skipped_meta,
                            skipped=True,
                        )
                    )

            if not result.warnings:
                result.warnings = []
            result.warnings.append(
                "Document produced zero extractable text. "
                "LLM processing phases were skipped."
            )
            return result
        # ── End empty-content guard ─────────────────────────────

        structure: StructureResult | None = None

        if enable_structure:
            _emit_phase("structure", "Structure Analysis")
            structure = await self._step_structure(result)
            _emit_phase("heading_reconciliation", "Heading Reconciliation")
            structure = await self._step_heading_reconciliation(result, structure)
            _emit_phase("heading_levels", "Heading Levels")
            await self._step_heading_levels(result, structure)

        section_map: SectionMap | None = None
        if enable_page_content and structure is not None:
            section_map = self._build_section_map(result, structure)
            page_hints = self._generate_page_hints(result, structure, section_map)
            _emit_phase("page_content", "Page Content Corrections")
            await self._step_page_content(result, structure, page_hints=page_hints)
            _emit_phase("code_blocks", "Code Block Languages")
            await self._step_code_blocks(result, structure)
            _emit_phase("form_fields", "Form Fields")
            await self._step_form_fields(result, structure)

        if enable_boundaries and structure is not None:
            if section_map is None and structure is not None:
                section_map = self._build_section_map(result, structure)
            _emit_phase("boundaries", "Cross-Page Fixes")
            await self._step_boundaries(result, structure, section_map=section_map)
            _emit_phase("cleanup", "Final Cleanup")
            await self._step_cleanup(result)

        return result

    async def _step_docling(
        self,
        result: PipelineViewerResult,
        file_content: bytes,
        filename: str,
        images_scale: float,
        do_table_structure: bool,
        on_cold_start: Callable[[], None] | None = None,
    ) -> None:
        """Run docling-serve extraction + pypdfium2 page rendering, producing v0.

        Docling-serve conversion and page image rendering run in parallel via
        ``asyncio.gather()`` — the HTTP call and CPU rendering overlap.
        """
        from .docling_response_parser import (
            detect_columns_from_json,
            extract_figures_from_json,
            get_page_info,
            split_markdown_by_page,
        )
        from .docling_serve_client import get_docling_client
        from .page_image_renderer import crop_figure_from_page_image, render_page_images

        step_start = time.time()
        client = get_docling_client()

        # Run docling-serve conversion and page image rendering in parallel
        # Use placeholder mode — no embedded base64, keeps JSON response small
        docling_task = client.convert(
            file_content,
            filename,
            do_ocr=False,
            do_table_structure=do_table_structure,
            include_images=True,
            images_scale=images_scale,
            image_export_mode="placeholder",
            md_page_break_placeholder="<!-- PAGE_BREAK -->",
            on_cold_start=on_cold_start,
        )
        images_task = render_page_images(file_content, scale=images_scale)
        response, page_images = await asyncio.gather(docling_task, images_task)

        # Page images from pypdfium2
        result.page_images = page_images

        # Page count from JSON
        page_info = get_page_info(response.json_content)
        result.total_pages = page_info["page_count"]

        from src.utils.text_cleanup import sanitize_extracted_text

        # Full document markdown -> v0
        try:
            full_md = sanitize_extracted_text(response.md_content)
        except Exception as e:
            logger.warning(f"Failed to sanitize markdown: {e}")
            full_md = response.md_content

        result.versions["v0"] = full_md

        # Per-page markdown
        total_chars = 0
        page_mds = split_markdown_by_page(full_md)
        for page_md in page_mds.values():
            total_chars += len(page_md)

        # Sanitize per-page markdown
        for page_key, page_md in page_mds.items():
            try:
                page_mds[page_key] = sanitize_extracted_text(page_md)
            except Exception:
                pass

        result.page_markdowns["v0"] = page_mds

        # Extract figures from JSON (bbox metadata) and crop from page renders
        figure_dicts = extract_figures_from_json(response.json_content)
        for fig_dict in figure_dicts:
            page_key = str(fig_dict["page_number"])
            page_b64 = page_images.get(page_key)
            if not page_b64:
                continue
            try:
                cropped_b64 = crop_figure_from_page_image(
                    page_b64,
                    fig_dict["bbox"],
                    fig_dict["page_width"],
                    fig_dict["page_height"],
                )
            except Exception as e:
                logger.warning("Failed to crop figure %s: %s", fig_dict["ref_id"], e)
                continue
            result.figures.append(
                FigureData(
                    ref_id=fig_dict["ref_id"],
                    caption=fig_dict["caption"],
                    page_number=fig_dict["page_number"],
                    image_base64=cropped_b64,
                )
            )

        # Replace <!-- image --> placeholders with inline image refs in v0
        if result.figures:
            for page_key, page_md in page_mds.items():
                page_figures = [f for f in result.figures if str(f.page_number) == page_key]
                if page_figures:
                    page_mds[page_key] = _replace_image_placeholders(page_md, page_figures)
            result.page_markdowns["v0"] = page_mds

            result.versions["v0"] = _replace_image_placeholders(
                result.versions["v0"], result.figures
            )

        # Detect column layout per page from JSON provenance
        layout_hints: dict[str, str] = {}
        for page_key in page_mds:
            try:
                page_no = int(page_key)
                layout_hints[page_key] = detect_columns_from_json(response.json_content, page_no)
            except (ValueError, TypeError):
                layout_hints[page_key] = "unknown"

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=[],
                metadata={
                    "do_table_structure": do_table_structure,
                    "images_scale": images_scale,
                    "engine": "docling-serve",
                },
            )
        )

        # Compute stats
        chars_per_page = total_chars / result.total_pages if result.total_pages > 0 else 0
        is_likely_scanned = chars_per_page < 50 and result.total_pages > 0

        result.stats = {
            "total_chars": total_chars,
            "chars_per_page": round(chars_per_page, 1),
            "is_likely_scanned": is_likely_scanned,
            "figure_count": len(result.figures),
            "images_scale": images_scale,
            "do_table_structure": do_table_structure,
            "total_elapsed_ms": elapsed_ms,
            "layout_hints": layout_hints,
        }

    # ------------------------------------------------------------------
    # Phase 0b — Tesseract OCR re-extraction (scanned documents only)
    # ------------------------------------------------------------------

    async def _step_docling_ocr(
        self,
        result: PipelineViewerResult,
        file_content: bytes,
        filename: str,
        images_scale: float,
        do_table_structure: bool,
        *,
        languages: list[str],
    ) -> None:
        """Re-run docling-serve with OCR for scanned documents.

        Called when the initial ``_step_docling()`` produced zero extractable
        text and the classifier flagged the document as scanned.  Overwrites
        v0 content with OCR results.  Page images are NOT re-rendered
        (already available from the initial extraction).

        Args:
            result: Pipeline result to update (v0 overwritten).
            file_content: Raw PDF bytes.
            filename: Original filename.
            images_scale: Scale factor for page images.
            do_table_structure: Whether to run table structure recognition.
            languages: Tesseract language codes (e.g. ``["eng", "deu"]``).
        """
        from src.utils.ocr_languages import tesseract_to_easyocr

        from .docling_response_parser import (
            detect_columns_from_json,
            extract_figures_from_json,
            get_page_info,
            split_markdown_by_page,
        )
        from .docling_serve_client import get_docling_client

        step_start = time.time()

        # Map Tesseract codes to EasyOCR codes for docling-serve
        easyocr_langs = tesseract_to_easyocr(languages)
        logger.info(
            "Running OCR re-extraction for %s: languages=%s (EasyOCR: %s)",
            filename,
            languages,
            easyocr_langs,
        )

        from .page_image_renderer import crop_figure_from_page_image

        client = get_docling_client()
        # OCR is significantly slower than text extraction — use 5min timeout
        response = await client.convert(
            file_content,
            filename,
            do_ocr=True,
            force_ocr=True,
            ocr_engine="easyocr",
            ocr_lang=easyocr_langs,
            do_table_structure=do_table_structure,
            include_images=True,
            images_scale=images_scale,
            image_export_mode="placeholder",
            md_page_break_placeholder="<!-- PAGE_BREAK -->",
            timeout=300.0,
        )

        # Page count from JSON
        page_info = get_page_info(response.json_content)
        result.total_pages = page_info["page_count"]

        # Overwrite v0 with OCR content
        result.versions["v0"] = response.md_content

        # Per-page markdown (overwrite)
        total_chars = 0
        page_mds = split_markdown_by_page(response.md_content)
        for page_md in page_mds.values():
            total_chars += len(page_md)

        result.page_markdowns["v0"] = page_mds
        # Page images already set by _step_docling — no re-render needed

        # Re-extract figures (crop from page renders)
        result.figures.clear()
        figure_dicts = extract_figures_from_json(response.json_content)
        for fig_dict in figure_dicts:
            page_key = str(fig_dict["page_number"])
            page_b64 = result.page_images.get(page_key)
            if not page_b64:
                continue
            try:
                cropped_b64 = crop_figure_from_page_image(
                    page_b64,
                    fig_dict["bbox"],
                    fig_dict["page_width"],
                    fig_dict["page_height"],
                )
            except Exception as e:
                logger.warning("Failed to crop figure %s: %s", fig_dict["ref_id"], e)
                continue
            result.figures.append(
                FigureData(
                    ref_id=fig_dict["ref_id"],
                    caption=fig_dict["caption"],
                    page_number=fig_dict["page_number"],
                    image_base64=cropped_b64,
                )
            )

        # Replace <!-- image --> placeholders with inline image refs
        if result.figures:
            for page_key, page_md in page_mds.items():
                page_figures = [f for f in result.figures if str(f.page_number) == page_key]
                if page_figures:
                    page_mds[page_key] = _replace_image_placeholders(page_md, page_figures)
            result.page_markdowns["v0"] = page_mds
            result.versions["v0"] = _replace_image_placeholders(
                result.versions["v0"], result.figures
            )

        # Detect column layout per page from JSON provenance
        layout_hints: dict[str, str] = {}
        for page_key in page_mds:
            try:
                page_no = int(page_key)
                layout_hints[page_key] = detect_columns_from_json(response.json_content, page_no)
            except (ValueError, TypeError):
                layout_hints[page_key] = "unknown"

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="docling_ocr",
                display_name="OCR Re-extraction",
                version_before="v0",
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=[],
                metadata={
                    "ocr_engine": "easyocr",
                    "languages": languages,
                    "easyocr_languages": easyocr_langs,
                    "total_chars_after_ocr": total_chars,
                    "force_ocr": True,
                },
            )
        )

        # Update stats with OCR results
        chars_per_page = total_chars / result.total_pages if result.total_pages > 0 else 0
        result.stats.update({
            "total_chars": total_chars,
            "chars_per_page": round(chars_per_page, 1),
            "is_likely_scanned": True,
            "ocr_applied": True,
            "ocr_languages": languages,
            "ocr_elapsed_ms": elapsed_ms,
            "layout_hints": layout_hints,
        })

    # ------------------------------------------------------------------
    # Phase 1 — Structure Analysis (sequential, analysis-only)
    # ------------------------------------------------------------------

    async def _step_structure(
        self,
        result: PipelineViewerResult,
    ) -> StructureResult:
        """Analyze document structure page by page.

        Processes pages sequentially, accumulating an outline. Each call
        receives the page image + markdown + outline-so-far and returns
        structural findings (headings, footnotes, page type).

        Does NOT modify the markdown — produces metadata consumed by
        later phases.

        Returns:
            StructureResult with outline, page types, and footnotes.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.structure_analysis import (
            STRUCTURE_SYSTEM_PROMPT,
            build_structure_user_message,
        )

        step_start = time.time()

        model = get_model_for_tier(ModelTier.EFFICIENT)
        agent: Agent[None, StructurePageOutput] = Agent(
            model=model,
            output_type=StructurePageOutput,
            system_prompt=STRUCTURE_SYSTEM_PROMPT,
        )

        structure = StructureResult()
        page_mds = result.page_markdowns["v0"]
        total_input_tokens = 0
        total_output_tokens = 0

        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            page_md = page_mds.get(page_key, "")
            page_image_b64 = result.page_images.get(page_key)

            # Build text portion of user message
            outline_dicts = [e.model_dump() for e in structure.outline]
            layout_hints = result.stats.get("layout_hints", {})
            layout_hint = layout_hints.get(page_key)
            text_msg = build_structure_user_message(
                page_markdown=page_md,
                outline_so_far=outline_dicts,
                page_number=page_num,
                total_pages=result.total_pages,
                layout_hint=layout_hint,
            )

            # Build message list: image + text
            messages: list[Any] = []
            if page_image_b64:
                image_bytes = base64.b64decode(page_image_b64)
                messages.append(BinaryContent(data=image_bytes, media_type="image/png"))
            messages.append(text_msg)

            # Run agent for this page
            try:
                agent_result = await agent.run(messages)
                page_output = agent_result.output
                usage = agent_result.usage()
                total_input_tokens += usage.request_tokens or 0
                total_output_tokens += usage.response_tokens or 0
            except Exception as e:
                logger.error(f"Structure analysis failed on page {page_num}: {e}")
                continue

            # Accumulate results
            structure.page_attributes[page_num] = page_output.page_attributes

            for heading in page_output.headings:
                structure.outline.append(
                    OutlineEntry(
                        level=heading.recommended_level,
                        text=heading.text,
                        page=page_num,
                        reasoning=heading.reasoning,
                    )
                )

            for fn in page_output.footnotes:
                structure.footnotes.append(
                    FootnoteInfo(
                        number=fn.number,
                        body_text=fn.body_text,
                        source_page=page_num,
                    )
                )

            for cb in page_output.code_blocks:
                structure.code_blocks.append(
                    CodeBlockInfo(
                        language=cb.language,
                        first_line=cb.first_line,
                        last_line=cb.last_line,
                        page=page_num,
                        reasoning=cb.reasoning,
                    )
                )

            attrs = page_output.page_attributes
            logger.info(
                f"Structure page {page_num}/{result.total_pages}: "
                f"layout={attrs.layout.value}, "
                f"headings={len(page_output.headings)}, "
                f"footnotes={len(page_output.footnotes)}, "
                f"code_blocks={len(page_output.code_blocks)}"
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        # Phase 1 is analysis-only: no version change, just metadata
        result.steps.append(
            StepResult(
                name="structure",
                display_name="Structure Analysis",
                version_before="v0",
                version_after="v0",  # no markdown change
                elapsed_ms=elapsed_ms,
                changes=[],
                metadata={
                    "page_attributes": {
                        str(k): v.model_dump() for k, v in structure.page_attributes.items()
                    },
                    "outline": [e.model_dump() for e in structure.outline],
                    "footnotes": [f.model_dump() for f in structure.footnotes],
                    "code_blocks": [cb.model_dump() for cb in structure.code_blocks],
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

        return structure

    # ------------------------------------------------------------------
    # Phase 1b — Heading Reconciliation (single LLM call, all headings)
    # ------------------------------------------------------------------

    async def _step_heading_reconciliation(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> StructureResult:
        """Reconcile heading levels across the full document.

        The per-page structure analysis assigns heading levels based on
        limited context (one page + accumulated outline). This step reviews
        ALL heading candidates at once and assigns globally consistent levels.

        Returns:
            Updated StructureResult with reconciled outline.
        """
        from pydantic_ai import Agent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.heading_reconciliation import (
            HEADING_RECONCILIATION_SYSTEM_PROMPT,
            build_heading_reconciliation_message,
        )

        step_start = time.time()

        if not structure.outline:
            result.steps.append(
                StepResult(
                    name="heading_reconciliation",
                    display_name="Heading Reconciliation",
                    version_before="v0",
                    version_after="v0",
                    elapsed_ms=0,
                    changes=[],
                    metadata={"headings_reviewed": 0, "headings_changed": 0},
                    skipped=True,
                )
            )
            return structure

        # Build heading candidates with reasoning from per-page analysis
        heading_candidates = []
        for entry in structure.outline:
            heading_candidates.append({
                "text": entry.text,
                "page": entry.page,
                "recommended_level": entry.level,
                "reasoning": entry.reasoning or f"Assigned h{entry.level} on page {entry.page}",
            })

        model = get_model_for_tier(ModelTier.EFFICIENT)
        agent: Agent[None, HeadingReconciliationOutput] = Agent(
            model=model,
            output_type=HeadingReconciliationOutput,
            system_prompt=HEADING_RECONCILIATION_SYSTEM_PROMPT,
        )

        user_msg = build_heading_reconciliation_message(
            heading_candidates=heading_candidates,
            total_pages=result.total_pages,
        )

        total_input_tokens = 0
        total_output_tokens = 0
        changes: list[DocumentChange] = []

        try:
            agent_result = await agent.run(user_msg)
            reconciled = agent_result.output
            usage = agent_result.usage()
            total_input_tokens += usage.request_tokens or 0
            total_output_tokens += usage.response_tokens or 0

            # Compare old and new outlines, record changes
            old_by_key = {
                (e.text, e.page): e.level for e in structure.outline
            }
            for entry in reconciled.outline:
                key = (entry.text, entry.page)
                old_level = old_by_key.get(key)
                if old_level is not None and old_level != entry.level:
                    changes.append(
                        DocumentChange(
                            page=entry.page,
                            old_text=f"h{old_level}: {entry.text}",
                            new_text=f"h{entry.level}: {entry.text}",
                            reasoning=(
                                f"Reconciliation changed from h{old_level} to h{entry.level}"
                            ),
                            stage="heading_reconciliation",
                        )
                    )

            # Update the structure's outline with reconciled levels
            structure.outline = reconciled.outline

        except Exception as e:
            logger.error(f"Heading reconciliation failed: {e}")
            # Keep original outline on failure

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        result.steps.append(
            StepResult(
                name="heading_reconciliation",
                display_name="Heading Reconciliation",
                version_before="v0",
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "headings_reviewed": len(heading_candidates),
                    "headings_changed": len(changes),
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

        return structure

    # ------------------------------------------------------------------
    # Phase 1c — Deterministic heading level fix
    # ------------------------------------------------------------------

    async def _step_heading_levels(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> None:
        """Fix heading levels in v0 markdown using outline metadata.

        For each page, scans markdown lines starting with ``#`` and fuzzy-
        matches against the outline entries for that page.  When a match is
        found the ``#`` prefix is replaced with the correct level.

        Mutates ``v0`` page markdowns in-place so that the page-content step
        reads already-corrected headings.  Produces no new version.
        """
        step_start = time.time()
        changes: list[DocumentChange] = []
        page_mds = result.page_markdowns["v0"]
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$")

        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            page_md = page_mds.get(page_key, "")
            page_entries = [e for e in structure.outline if e.page == page_num]
            if not page_entries:
                continue

            lines = page_md.split("\n")
            changed = False
            for i, line in enumerate(lines):
                m = heading_re.match(line)
                if not m:
                    continue
                current_hashes = m.group(1)
                heading_text = m.group(2).strip()

                # Find best-matching outline entry via fuzzy match
                best_entry = None
                best_ratio = 0.0
                for entry in page_entries:
                    ratio = difflib.SequenceMatcher(
                        None, heading_text.lower(), entry.text.lower()
                    ).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_entry = entry

                if best_entry is None or best_ratio < HEADING_MATCH_THRESHOLD:
                    continue

                correct_hashes = "#" * best_entry.level
                if current_hashes == correct_hashes:
                    continue

                old_line = line
                new_line = f"{correct_hashes} {heading_text}"
                lines[i] = new_line
                changed = True
                changes.append(
                    DocumentChange(
                        page=page_num,
                        old_text=old_line,
                        new_text=new_line,
                        reasoning=(
                            f"Outline says \"{best_entry.text}\" is level {best_entry.level} "
                            f"(was {'#' * len(current_hashes)} = level {len(current_hashes)}, "
                            f"match {best_ratio:.0%})"
                        ),
                        stage="heading_level",
                    )
                )

            if changed:
                page_mds[page_key] = "\n".join(lines)

        # Rebuild full v0 from corrected pages
        if changes:
            result.versions["v0"] = "\n\n".join(
                page_mds.get(str(p), "") for p in range(1, result.total_pages + 1)
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="heading_levels",
                display_name="Heading Levels",
                version_before="v0",
                version_after="v0",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "fixes_applied": len(changes),
                },
            )
        )

    # ------------------------------------------------------------------
    # Section Map Construction (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _build_section_map(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> SectionMap:
        """Build a map of sections from the reconciled outline.

        Deterministic step — no LLM call. Concatenates all v0 page markdowns,
        finds heading positions, and splits the document at heading boundaries.

        Returns:
            SectionMap with ordered sections.
        """
        page_mds = result.page_markdowns["v0"]
        heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

        # Build full document from pages with page boundary markers
        # We need to track which page each line belongs to
        page_line_offsets: list[tuple[int, int]] = []  # (start_line, page_num)
        all_lines: list[str] = []
        for page_num in range(1, result.total_pages + 1):
            page_md = page_mds.get(str(page_num), "")
            page_line_offsets.append((len(all_lines), page_num))
            all_lines.extend(page_md.split("\n"))
            # Add blank line between pages (matching the \n\n join)
            if page_num < result.total_pages:
                all_lines.append("")

        full_text = "\n".join(all_lines)

        def _line_to_page(line_idx: int) -> int:
            """Map a line index to its page number."""
            result_page = 1
            for start_line, page_num in page_line_offsets:
                if start_line <= line_idx:
                    result_page = page_num
                else:
                    break
            return result_page

        # Find all headings in the full text with their positions
        heading_positions: list[tuple[int, int, str, int]] = []  # (char_pos, line_idx, text, level)
        running_char_pos = 0
        for line_idx, line in enumerate(all_lines):
            m = heading_re.match(line)
            if m:
                level = len(m.group(1))
                text = m.group(2).strip()
                heading_positions.append((running_char_pos, line_idx, text, level))
            running_char_pos += len(line) + 1  # +1 for newline

        # Match headings to outline entries via fuzzy match
        matched_outline: list[tuple[int, int, str, int, int]] = []  # (char_pos, line_idx, text, level, outline_idx)
        used_outline_indices: set[int] = set()

        for char_pos, line_idx, text, level in heading_positions:
            best_idx = -1
            best_ratio = 0.0
            for oi, entry in enumerate(structure.outline):
                if oi in used_outline_indices:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, text.lower(), entry.text.lower()
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = oi

            if best_idx >= 0 and best_ratio >= SECTION_MAP_MATCH_THRESHOLD:
                used_outline_indices.add(best_idx)
                matched_outline.append((char_pos, line_idx, text, level, best_idx))

        # Build sections by splitting at heading boundaries
        sections: list[SectionEntry] = []
        section_idx = 0

        # Preamble: content before first heading
        if matched_outline:
            first_heading_line = matched_outline[0][1]
            if first_heading_line > 0:
                preamble_lines = all_lines[:first_heading_line]
                preamble_md = "\n".join(preamble_lines).strip()
                if preamble_md:
                    preamble_pages = sorted({
                        _line_to_page(li) for li in range(first_heading_line)
                        if all_lines[li].strip()
                    })
                    sections.append(SectionEntry(
                        index=section_idx,
                        heading_text="(Preamble)",
                        heading_level=0,
                        pages=preamble_pages or [1],
                        markdown=preamble_md,
                        outline_position=-1,
                    ))
                    section_idx += 1

            # Each heading starts a section
            for i, (_, line_idx, text, level, outline_idx) in enumerate(matched_outline):
                # Section ends at the next heading or end of document
                if i + 1 < len(matched_outline):
                    end_line = matched_outline[i + 1][1]
                else:
                    end_line = len(all_lines)

                section_lines = all_lines[line_idx:end_line]
                section_md = "\n".join(section_lines).strip()

                # Determine pages this section spans
                section_pages = sorted({
                    _line_to_page(li)
                    for li in range(line_idx, end_line)
                    if li < len(all_lines) and all_lines[li].strip()
                })
                if not section_pages:
                    section_pages = [_line_to_page(line_idx)]

                sections.append(SectionEntry(
                    index=section_idx,
                    heading_text=text,
                    heading_level=level,
                    pages=section_pages,
                    markdown=section_md,
                    outline_position=outline_idx,
                ))
                section_idx += 1
        else:
            # No headings found — entire document is one section
            full_md = full_text.strip()
            if full_md:
                sections.append(SectionEntry(
                    index=0,
                    heading_text="(Preamble)",
                    heading_level=0,
                    pages=list(range(1, result.total_pages + 1)),
                    markdown=full_md,
                    outline_position=-1,
                ))

        return SectionMap(sections=sections)

    # ------------------------------------------------------------------
    # Hint generation (programmatic edge-case detection)
    # ------------------------------------------------------------------

    def _generate_page_hints(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        section_map: SectionMap,
    ) -> dict[int, list[str]]:
        """Generate per-page context hints from programmatic edge-case detection.

        Returns:
            Mapping of page_num -> list of plain-text hint strings.
        """
        page_mds = result.page_markdowns.get("v0", {})
        hints: dict[int, list[str]] = {}

        # Determine document title (h1 from outline)
        doc_title = ""
        for entry in structure.outline:
            if entry.level == 1:
                doc_title = entry.text
                break

        # Build per-page section lookup
        page_to_sections: dict[int, list[SectionEntry]] = {}
        for section in section_map.sections:
            for p in section.pages:
                page_to_sections.setdefault(p, []).append(section)

        for page_num in range(1, result.total_pages + 1):
            page_md = page_mds.get(str(page_num), "")
            page_hints: list[str] = []
            lines = page_md.split("\n")
            non_blank = [ln for ln in lines if ln.strip()]

            if not non_blank:
                continue

            # --- Running header (page > 1 only) ---
            if page_num > 1:
                first_line = non_blank[0]
                if _is_running_header(first_line, doc_title, page_num):
                    page_hints.append(
                        f"First line '{first_line.strip()}' appears to be a "
                        f"running page header, not document content."
                    )

            # --- Running footer ---
            last_line = non_blank[-1]
            if _is_running_footer(last_line, page_num):
                page_hints.append(
                    f"Last line '{last_line.strip()}' appears to be a "
                    f"page number footer."
                )

            # --- Section context ---
            sections_on_page = page_to_sections.get(page_num, [])
            for section in sections_on_page:
                if section.heading_level == 0:
                    continue  # skip preamble
                if len(section.pages) == 1:
                    position = "only page"
                elif page_num == section.pages[0]:
                    position = "start of section"
                elif page_num == section.pages[-1]:
                    position = "end of section"
                else:
                    position = "middle of section"
                page_hints.append(
                    f"This page is in section '{section.heading_text}' "
                    f"(h{section.heading_level}, pages {section.pages[0]}-"
                    f"{section.pages[-1]}). You are on page {page_num} "
                    f"({position})."
                )

            # --- Mid-sentence start ---
            first_content = ""
            for ln in non_blank:
                if not ln.strip().startswith("#"):
                    first_content = ln.strip()
                    break
            if first_content and first_content[0].islower():
                page_hints.append(
                    "This page starts mid-sentence (continues from previous "
                    "page). Do not fix the incomplete start."
                )

            # --- Mid-sentence end ---
            last_content = non_blank[-1].strip()
            if last_content and last_content[-1] not in ".!?:":
                # Don't flag headings or list items as mid-sentence
                if not last_content.startswith("#") and not last_content.startswith("- "):
                    page_hints.append(
                        "This page ends mid-sentence. Content continues on "
                        "next page."
                    )

            # --- Expected footnotes ---
            page_footnotes = [
                fn for fn in structure.footnotes if fn.source_page == page_num
            ]
            if page_footnotes:
                markers = ", ".join(f"[{fn.number}]" for fn in page_footnotes)
                page_hints.append(
                    f"Footnotes {markers} are expected on this page."
                )

            if page_hints:
                hints[page_num] = page_hints

        return hints

    def _generate_boundary_hints(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        section_map: SectionMap,
    ) -> list[dict]:
        """Build boundary snippets with section-aware hints.

        Returns a list of boundary snippet dicts, each with:
            page_before, page_after, tail_text, head_text, hints: list[str]
        """
        source_version = "v1" if "v1" in result.page_markdowns else "v0"
        page_mds = result.page_markdowns.get(source_version, {})

        # Determine document title (h1 from outline)
        doc_title = ""
        for entry in structure.outline:
            if entry.level == 1:
                doc_title = entry.text
                break

        # Build per-page section lookup
        page_to_sections: dict[int, list[SectionEntry]] = {}
        for section in section_map.sections:
            for p in section.pages:
                page_to_sections.setdefault(p, []).append(section)

        snippets: list[dict] = []

        for page_num in range(1, result.total_pages):
            page_before = page_num
            page_after = page_num + 1

            md_before = page_mds.get(str(page_before), "")
            md_after = page_mds.get(str(page_after), "")

            lines_before = md_before.split("\n")
            lines_after = md_after.split("\n")

            tail_text = "\n".join(lines_before[-5:])
            head_text = "\n".join(lines_after[:5])

            boundary_hints: list[str] = []

            # --- Section context ---
            sections_before = page_to_sections.get(page_before, [])
            sections_after = page_to_sections.get(page_after, [])

            # Check if same section spans both pages
            shared = [s for s in sections_before if s in sections_after]
            if shared:
                sec = shared[0]
                boundary_hints.append(
                    f"SAME SECTION ('{sec.heading_text}'): content flows "
                    f"continuously. Join split words/sentences."
                )
            else:
                # Section transition
                if sections_before and sections_after:
                    sec_a = sections_before[-1]
                    sec_b = sections_after[0]
                    if sec_a.heading_level > 0 and sec_b.heading_level > 0:
                        boundary_hints.append(
                            f"SECTION TRANSITION: '{sec_a.heading_text}' ends "
                            f"→ '{sec_b.heading_text}' begins. This is a "
                            f"natural break."
                        )

            # --- Running header at start of page_after ---
            non_blank_after = [ln for ln in lines_after if ln.strip()]
            if non_blank_after:
                first_line = non_blank_after[0]
                if _is_running_header(first_line, doc_title, page_after):
                    boundary_hints.append(
                        f"Running header: '{first_line.strip()}' at start of "
                        f"page {page_after}. Remove this line."
                    )

            # --- Running footer at end of page_before ---
            non_blank_before = [ln for ln in lines_before if ln.strip()]
            if non_blank_before:
                last_line = non_blank_before[-1]
                if _is_running_footer(last_line, page_before):
                    boundary_hints.append(
                        f"Page number footer: '{last_line.strip()}' at end of "
                        f"page {page_before}. Remove this line."
                    )

            # --- Split table detection ---
            # Check if page_before ends with table rows and page_after
            # starts with table rows (markdown pipe tables or HTML tables)
            tail_has_table = any(
                ln.strip().startswith("|") and ln.strip().endswith("|")
                for ln in lines_before[-3:]
                if ln.strip()
            )
            head_has_table = any(
                ln.strip().startswith("|") and ln.strip().endswith("|")
                for ln in lines_after[:3]
                if ln.strip()
            )
            # Also check for HTML table continuation
            tail_has_html_table = any(
                "</tbody>" in ln or "</table>" in ln or "<tr>" in ln
                for ln in lines_before[-5:]
            )
            head_has_html_table = any(
                "<table" in ln or "<tbody>" in ln or "<tr>" in ln
                for ln in lines_after[:5]
            )

            if (tail_has_table and head_has_table) or (
                tail_has_html_table and head_has_html_table
            ):
                # Expand context to capture full table tails/heads
                tail_text = "\n".join(lines_before[-15:])
                head_text = "\n".join(lines_after[:15])
                boundary_hints.append(
                    "SPLIT TABLE: A table appears to span this page "
                    "boundary. Merge the two halves into one table — "
                    "remove any duplicate header row from the second half."
                )

            snippets.append({
                "page_before": page_before,
                "page_after": page_after,
                "tail_text": tail_text,
                "head_text": head_text,
                "hints": boundary_hints,
            })

        return snippets

    # ------------------------------------------------------------------
    # Image description subagent
    # ------------------------------------------------------------------

    async def _run_image_describer(
        self,
        figure: FigureData,
        current_markdown: str,
        page_images: dict[int, str | None],
        describer_tokens: dict[str, int],
        update_tokens: Any,
    ) -> str:
        """Spawn a vision subagent to generate alt text for a single figure.

        Returns a formatted instruction string for the section agent.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.image_description import (
            IMAGE_DESCRIBER_SYSTEM_PROMPT,
            build_describer_user_message,
        )

        # Extract surrounding context (~200 chars around the figure reference)
        ref_pattern = f"figures/{figure.ref_id}"
        idx = current_markdown.find(ref_pattern)
        if idx >= 0:
            start = max(0, idx - 200)
            end = min(len(current_markdown), idx + len(ref_pattern) + 200)
            surrounding_text = current_markdown[start:end]
        else:
            surrounding_text = ""

        # Build multimodal user message: prefer cropped figure only,
        # fall back to full page image when the crop is unavailable.
        user_parts: list[Any] = []
        fallback_mode = False

        if figure.image_base64:
            # Ideal: send only the isolated cropped figure
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(figure.image_base64),
                    media_type="image/png",
                )
            )
        else:
            # Fallback: Docling didn't extract a crop — send the full page
            fallback_mode = True
            page_img = page_images.get(figure.page_number)
            if page_img:
                user_parts.append(
                    BinaryContent(
                        data=base64.b64decode(page_img),
                        media_type="image/png",
                    )
                )

        user_message = build_describer_user_message(
            caption=figure.caption,
            surrounding_text=surrounding_text,
            ref_id=figure.ref_id,
            fallback_mode=fallback_mode,
        )
        user_parts.append(user_message)

        model = get_model_for_tier(ModelTier.EFFICIENT)
        describer_agent: Agent[None, ImageDescriptionResult] = Agent(
            model=model,
            output_type=ImageDescriptionResult,
            system_prompt=IMAGE_DESCRIBER_SYSTEM_PROMPT,
        )

        try:
            desc_result = await describer_agent.run(user_parts)
            usage = desc_result.usage()
            update_tokens(
                usage.request_tokens or 0,
                usage.response_tokens or 0,
            )

            result = desc_result.output
            if result.is_decorative:
                return (
                    f"Figure '{figure.ref_id}' is decorative. "
                    f"No alt text needed — leave the alt attribute empty."
                )
            return (
                f"Use this alt text for '{figure.ref_id}': {result.alt_text}\n"
                f"Use str_replace to update the markdown image syntax to include this alt text."
            )
        except Exception as e:
            logger.warning(f"Image describer failed for {figure.ref_id}: {e}")
            return f"ERROR: Image describer failed for '{figure.ref_id}': {e}"

    # ------------------------------------------------------------------
    # Table reconstruction subagent
    # ------------------------------------------------------------------

    async def _run_table_reconstructor(
        self,
        table: Any,
        current_markdown: str,
        page_image_b64: str | None,
        update_tokens: Any,
    ) -> str:
        """Run the table reconstruction vision subagent.

        Mirrors _run_image_describer pattern: creates a one-shot PydanticAI
        agent with structured output, passes the page image and table context,
        returns an instruction string for the page agent.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.table_reconstruction import (
            TABLE_RECONSTRUCTOR_SYSTEM_PROMPT,
            build_table_user_message,
        )
        from .pipeline_viewer_models import TableReconstructionResult

        # Extract surrounding context
        idx = current_markdown.find(table.markdown_content[:60])
        if idx >= 0:
            start = max(0, idx - 200)
            end = min(
                len(current_markdown),
                idx + len(table.markdown_content) + 200,
            )
            surrounding_text = current_markdown[start:end]
        else:
            surrounding_text = ""

        # Build user message parts
        user_parts: list[Any] = []

        # Always use full page image (no table crop available)
        if page_image_b64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_image_b64),
                    media_type="image/png",
                )
            )

        user_parts.append(
            build_table_user_message(
                table_markdown=table.markdown_content,
                surrounding_text=surrounding_text,
                ref_id=table.ref_id,
            )
        )

        # Create and run subagent
        model = get_model_for_tier(ModelTier.EFFICIENT)
        reconstructor_agent: Agent[None, TableReconstructionResult] = Agent(
            model=model,
            output_type=TableReconstructionResult,
            system_prompt=TABLE_RECONSTRUCTOR_SYSTEM_PROMPT,
        )

        try:
            agent_result = await reconstructor_agent.run(user_parts)
            usage = agent_result.usage()
            update_tokens(
                usage.request_tokens or 0,
                usage.response_tokens or 0,
            )
        except Exception as e:
            logger.warning(f"Table reconstructor failed for {table.ref_id}: {e}")
            return (
                f"Table reconstruction failed for '{table.ref_id}': {e}. "
                f"Use str_replace to fix the table manually if needed."
            )

        result = agent_result.output

        if result.table_format == "html":
            return (
                f"Reconstructed table '{table.ref_id}' as HTML (complex table with "
                f"{'merged cells, ' if result.has_merged_cells else ''}"
                f"{'row headers, ' if result.has_row_headers else ''}"
                f"caption: \"{result.caption}\").\n"
                f"Use str_replace to replace the entire markdown table with this HTML:\n\n"
                f"{result.reconstructed_content}"
            )
        return (
            f"Reconstructed table '{table.ref_id}' as corrected markdown"
            f"{f' (caption: {result.caption!r})' if result.caption else ''}.\n"
            f"Use str_replace to replace the markdown table with this corrected version:\n\n"
            f"{result.reconstructed_content}"
        )

    # ------------------------------------------------------------------
    # List reconstruction subagent
    # ------------------------------------------------------------------

    async def _run_list_reconstructor(
        self,
        list_text: str,
        surrounding_text: str,
        page_image_b64: str | None,
        page_number: int,
        update_tokens: Any,
    ) -> str:
        """Run the list reconstruction vision subagent.

        Mirrors _run_image_describer pattern: creates a one-shot PydanticAI
        agent with structured output, passes the page image and list context,
        returns an instruction string for the page agent.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.list_reconstruction import (
            LIST_RECONSTRUCTOR_SYSTEM_PROMPT,
            build_list_user_message,
        )

        # Build user message parts
        user_parts: list[Any] = []

        # Pass page image for visual comparison
        if page_image_b64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_image_b64),
                    media_type="image/png",
                )
            )

        user_parts.append(
            build_list_user_message(
                list_text=list_text,
                surrounding_text=surrounding_text,
                page_number=page_number,
            )
        )

        # Create and run subagent
        model = get_model_for_tier(ModelTier.EFFICIENT)
        reconstructor_agent: Agent[None, ListReconstructionResult] = Agent(
            model=model,
            output_type=ListReconstructionResult,
            system_prompt=LIST_RECONSTRUCTOR_SYSTEM_PROMPT,
        )

        try:
            agent_result = await reconstructor_agent.run(user_parts)
            usage = agent_result.usage()
            update_tokens(
                usage.request_tokens or 0,
                usage.response_tokens or 0,
            )
        except Exception as e:
            logger.warning(f"List reconstructor failed on page {page_number}: {e}")
            return (
                f"List reconstruction failed: {e}. "
                f"Use str_replace to fix the list manually if needed."
            )

        result = agent_result.output

        format_note = (
            "HTML definition list"
            if result.list_type == "definition"
            else f"{result.list_type} markdown list"
        )

        return (
            f"Reconstructed as {format_note}.\n"
            f"Use str_replace to replace the original list with this "
            f"corrected version:\n\n{result.reconstructed_content}"
        )

    # ------------------------------------------------------------------
    # Phase 2c — Form fields → accessible HTML (guaranteed on form pages)
    # ------------------------------------------------------------------

    async def _step_form_fields(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> None:
        """Convert detected form fields to accessible HTML on every form page.

        Runs unconditionally as a step (not an agent-optional tool, which the
        page agent was skipping). A page is processed when the structure agent
        flagged it ``has_forms`` OR a deterministic scan finds form signals
        (underline runs, checkbox glyphs) — so multi-page forms are caught even
        where ``has_forms`` missed a page.

        For each form page, a vision agent reads the page image + markdown and
        returns the fields it sees; a deterministic renderer then replaces each
        field's ``anchor_text`` with accessible HTML (labelled controls,
        ``<fieldset>``/``<legend>`` for option groups). Edits whichever per-page
        version is newest (v1 if present, else v0) in-place.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent

        from ..agents.model_factory import get_model_for_tier
        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.form_fields import (
            FORM_FIELDS_SYSTEM_PROMPT,
            build_form_fields_user_message,
        )
        from ..shared.llm_cost import calculate_estimated_cost

        step_start = time.time()
        source_version = "v1" if "v1" in result.page_markdowns else "v0"
        page_mds = result.page_markdowns.get(source_version, {})

        model = get_model_for_tier(ModelTier.EFFICIENT)
        agent: Agent[None, FormFieldsPageOutput] = Agent(
            model=model,
            output_type=FormFieldsPageOutput,
            system_prompt=FORM_FIELDS_SYSTEM_PROMPT,
        )

        changes: list[DocumentChange] = []
        fields_found = 0
        pages_processed = 0
        total_input_tokens = 0
        total_output_tokens = 0

        # Document-level form signal. Docling frequently drops form-field text
        # (underscores, checkboxes) from later pages, so per-page text signals
        # alone miss multi-page forms. When the document is a form — it has
        # AcroForm widgets, OR any page was flagged ``has_forms``, OR any page
        # shows form signals — scan EVERY page with the vision agent (it returns
        # no fields for non-form pages, so the cost is bounded and safe).
        has_acroform = result.stats.get("form_field_count", 0) > 0
        any_flagged = any(
            a.has_forms for a in structure.page_attributes.values()
        )
        any_signal = any(_page_has_form_signals(md) for md in page_mds.values())
        is_form_document = has_acroform or any_flagged or any_signal

        for page_num in range(1, result.total_pages + 1):
            page_key = str(page_num)
            page_md = page_mds.get(page_key, "")
            page_image_b64 = result.page_images.get(page_key)
            if not page_md or not page_image_b64:
                continue

            attrs = structure.page_attributes.get(page_num)
            is_form_page = (
                is_form_document
                or (attrs and attrs.has_forms)
                or _page_has_form_signals(page_md)
            )
            if not is_form_page:
                continue
            pages_processed += 1

            text_msg = build_form_fields_user_message(
                page_markdown=page_md,
                page_number=page_num,
                total_pages=result.total_pages,
            )
            messages: list[Any] = [
                BinaryContent(
                    data=base64.b64decode(page_image_b64), media_type="image/png"
                ),
                text_msg,
            ]

            try:
                agent_result = await agent.run(messages)
                page_output = agent_result.output
                usage = agent_result.usage()
                total_input_tokens += usage.request_tokens or 0
                total_output_tokens += usage.response_tokens or 0
            except Exception as e:
                logger.error(f"Form field detection failed on page {page_num}: {e}")
                continue

            if not page_output.form_fields:
                continue

            fields_found += len(page_output.form_fields)
            current_md = page_mds[page_key]
            for i, field in enumerate(page_output.form_fields):
                field.page = page_num
                field_id = f"ff-p{page_num}-{i}"
                new_md, change = _apply_form_field(current_md, field, field_id)
                if change:
                    current_md = new_md
                    changes.append(change)
                else:
                    logger.info(
                        f"Form field anchor not found on page {page_num}: "
                        f"{field.anchor_text!r}"
                    )
            page_mds[page_key] = current_md

            logger.info(
                f"Form fields page {page_num}/{result.total_pages}: "
                f"detected={len(page_output.form_fields)}"
            )

        # Rebuild the full version from corrected pages
        if changes:
            result.versions[source_version] = "\n\n".join(
                page_mds.get(str(p), "") for p in range(1, result.total_pages + 1)
            )

        elapsed_ms = int((time.time() - step_start) * 1000)
        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        result.steps.append(
            StepResult(
                name="form_fields",
                display_name="Form Fields",
                version_before=source_version,
                version_after=source_version,
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "pages_processed": pages_processed,
                    "fields_detected": fields_found,
                    "fields_injected": len(changes),
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

    # ------------------------------------------------------------------
    # Phase 2 — Page Content Corrections (parallel, semaphore-limited)
    # ------------------------------------------------------------------

    async def _step_page_content(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        *,
        page_hints: dict[int, list[str]] | None = None,
    ) -> None:
        """Correct each page's markdown against its page image.

        Runs page agents in parallel (up to PAGE_AGENT_SEMAPHORE_LIMIT
        concurrent). Each agent receives the page image, page markdown,
        document outline, and a procedure file based on the page's
        document type.

        Uses str_replace tool calls for surgical edits. Failed edits
        report the current markdown state back to the agent for retry.

        Produces v1: corrected per-page markdowns.
        """


        step_start = time.time()
        all_changes: list[DocumentChange] = []
        all_issues: list[str] = []
        page_mds = dict(result.page_markdowns["v0"])  # copy to mutate
        semaphore = asyncio.Semaphore(PAGE_AGENT_SEMAPHORE_LIMIT)

        # Build per-page figure lookup
        page_figures_map: dict[int, list[FigureData]] = {}
        for fig in result.figures:
            page_figures_map.setdefault(fig.page_number, []).append(fig)

        # Build per-page table lookup
        page_tables_map: dict[int, list] = {}
        for page_num_key in page_mds:
            p = int(page_num_key)
            tables = _extract_tables_from_markdown(page_mds[page_num_key], p)
            if tables:
                page_tables_map[p] = tables

        # Pre-compute outline text once (identical for every page)
        outline_text = "\n".join(
            f"  {'#' * e.level} {e.text} (p{e.page})"
            for e in structure.outline
        )

        async def _process_page(page_num: int) -> PageCorrectionResult:
            pf = page_figures_map.get(page_num) or None
            pt = page_tables_map.get(page_num) or None
            async with semaphore:
                return await self._run_page_agent(
                    page_num=page_num,
                    page_markdown=page_mds[str(page_num)],
                    page_image_b64=result.page_images.get(str(page_num)),
                    structure=structure,
                    context_hints=page_hints.get(page_num) if page_hints else None,
                    page_figures=pf,
                    outline_text=outline_text,
                    page_tables=pt,
                )

        # Fan out only pages that have markdown content (scanned PDFs may
        # have blank pages that were not extracted by OCR).
        pages_to_process = [
            p for p in range(1, result.total_pages + 1) if str(p) in page_mds
        ]
        tasks = [_process_page(p) for p in pages_to_process]
        page_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        corrected_mds: dict[str, str] = {}
        total_input_tokens = 0
        total_output_tokens = 0
        total_describer_input = 0
        total_describer_output = 0
        total_table_reconstructor_input = 0
        total_table_reconstructor_output = 0
        total_list_reconstructor_input = 0
        total_list_reconstructor_output = 0
        for i, pr in enumerate(page_results):
            page_num = pages_to_process[i]
            page_key = str(page_num)

            if isinstance(pr, Exception):
                logger.error(f"Page {page_num} agent failed: {pr}")
                corrected_mds[page_key] = page_mds.get(page_key, "")  # keep original
                all_issues.append(f"Page {page_num}: agent error — {pr}")
                continue

            corrected_mds[page_key] = pr.corrected_markdown
            all_changes.extend(pr.changes)
            all_issues.extend(pr.issues)
            total_input_tokens += pr.input_tokens
            total_output_tokens += pr.output_tokens
            total_describer_input += pr.describer_input_tokens
            total_describer_output += pr.describer_output_tokens
            total_table_reconstructor_input += pr.table_reconstructor_input_tokens
            total_table_reconstructor_output += pr.table_reconstructor_output_tokens
            total_list_reconstructor_input += pr.list_reconstructor_input_tokens
            total_list_reconstructor_output += pr.list_reconstructor_output_tokens

        # Write v1
        result.page_markdowns["v1"] = corrected_mds
        result.versions["v1"] = "\n\n".join(
            corrected_mds.get(str(p), "") for p in range(1, result.total_pages + 1)
        )

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        combined_input = (
            total_input_tokens
            + total_describer_input
            + total_table_reconstructor_input
            + total_list_reconstructor_input
        )
        combined_output = (
            total_output_tokens
            + total_describer_output
            + total_table_reconstructor_output
            + total_list_reconstructor_output
        )
        cost_cents = calculate_estimated_cost(combined_input, combined_output)

        result.steps.append(
            StepResult(
                name="page_content",
                display_name="Page Content Corrections",
                version_before="v0",
                version_after="v1",
                elapsed_ms=elapsed_ms,
                changes=all_changes,
                metadata={
                    "pages_with_changes": len(
                        {c.page for c in all_changes}
                    ),
                    "total_changes": len(all_changes),
                    "issues": all_issues,
                    "describer_input_tokens": total_describer_input,
                    "describer_output_tokens": total_describer_output,
                    "table_reconstructor_input_tokens": total_table_reconstructor_input,
                    "table_reconstructor_output_tokens": total_table_reconstructor_output,
                    "list_reconstructor_input_tokens": total_list_reconstructor_input,
                    "list_reconstructor_output_tokens": total_list_reconstructor_output,
                },
                input_tokens=combined_input,
                output_tokens=combined_output,
                cost_cents=cost_cents,
            )
        )

    async def _run_page_agent(
        self,
        page_num: int,
        page_markdown: str,
        page_image_b64: str | None,
        structure: StructureResult,
        context_hints: list[str] | None = None,
        outline_text: str = "",
        page_figures: list[FigureData] | None = None,
        page_tables: list | None = None,
    ) -> PageCorrectionResult:
        """Run a single page correction agent.

        Sets up a PydanticAI agent with str_replace and no_changes tools,
        loads the appropriate procedure based on document type, and runs
        the agent. Failed str_replace calls are reported back to the agent
        for retry.

        When ``page_figures`` is provided and the page has images, a
        ``describe_image`` tool is registered for WCAG-compliant alt text.

        When ``page_tables`` is provided and the page has tables, a
        ``reconstruct_table`` tool is registered for table reconstruction.
        """
        from pydantic_ai import Agent
        from pydantic_ai.messages import BinaryContent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier

        # Compose procedure from page attributes
        page_attrs = structure.page_attributes.get(page_num)
        procedure = _compose_page_prompt(page_attrs) if page_attrs else ""

        # Mutable state for str_replace tool
        current_markdown = page_markdown
        changes: list[DocumentChange] = []
        issues: list[str] = []

        # Build system prompt from composed procedure
        base_prompt = procedure if procedure else ""

        # Determine mode from page attributes
        is_create_mode = bool(
            page_attrs and page_attrs.layout == LayoutType.POSTER
        )

        # ------------------------------------------------------------------
        # describe_image tool: closure state
        # ------------------------------------------------------------------
        max_describe_calls = 10
        figure_lookup: dict[str, FigureData] = {}
        describer_input_tokens = 0
        describer_output_tokens = 0
        describe_call_count = 0

        enable_describe = bool(
            page_attrs and page_attrs.has_images and page_figures
        )
        if enable_describe:
            figure_lookup = {f.ref_id: f for f in (page_figures or [])}
            available_refs = sorted(figure_lookup.keys())
            base_prompt += (
                f"\n## Available figures for describe_image\n"
                f"The following figure ref_ids are available: {', '.join(available_refs)}\n"
                f"Call `describe_image` for each one, then use `str_replace` to "
                f"update the alt text.\n"
            )

        # ------------------------------------------------------------------
        # reconstruct_table tool: closure state
        # ------------------------------------------------------------------
        max_table_reconstruct_calls = 10
        table_lookup: dict[str, Any] = {}
        table_reconstructor_input_tokens = 0
        table_reconstructor_output_tokens = 0
        table_reconstruct_call_count = 0

        enable_table_reconstruct = bool(
            page_attrs and page_attrs.has_tables and page_tables
        )
        if enable_table_reconstruct:
            table_lookup = {t.ref_id: t for t in (page_tables or [])}
            available_table_refs = sorted(table_lookup.keys())
            base_prompt += (
                f"\n## Available tables for reconstruct_table\n"
                f"The following table ref_ids are available: {', '.join(available_table_refs)}\n"
                f"Call `reconstruct_table` for tables with structural issues "
                f"(wrong columns, misaligned cells, merged cells), then use "
                f"`str_replace` to apply the corrected table.\n"
            )

        # ------------------------------------------------------------------
        # reconstruct_list tool: closure state
        # ------------------------------------------------------------------
        max_list_reconstruct_calls = 5
        list_reconstructor_input_tokens = 0
        list_reconstructor_output_tokens = 0
        list_reconstruct_call_count = 0

        enable_list_reconstruct = bool(
            page_attrs and page_attrs.has_lists
        )

        # Create agent with tools
        model = get_model_for_tier(ModelTier.EFFICIENT)
        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,  # output comes through tool calls
            instructions=base_prompt,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the page markdown.

            Args:
                old_text: Exact text to find. Must appear once in the page
                    region. Include surrounding context for uniqueness.
                new_text: Corrected text. Must differ from old_text.
                reasoning: Why this change is needed. Reference what the
                    image shows vs what the markdown has.
                category: Type of correction: ocr_error, formatting,
                    heading_level, list_structure, character_encoding, other.
            """
            nonlocal current_markdown

            count = current_markdown.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in page markdown.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Current page markdown:\n---\n{current_markdown}\n---\n\n"
                    f"Review the current markdown and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_markdown = current_markdown.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=page_num,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage=category,
                )
            )
            return f"OK — replaced on page {page_num}."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that the page markdown matches the image.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations about uncertain areas.
            """
            if notes:
                issues.append(f"Page {page_num} ({confidence}): {notes}")
            return "Acknowledged — no changes needed."

        # ------------------------------------------------------------------
        # Conditionally register describe_image tool
        # ------------------------------------------------------------------
        if enable_describe:
            @agent.tool_plain
            async def describe_image(ref_id: str) -> str:
                """Generate WCAG-compliant alt text for a figure.

                Args:
                    ref_id: The figure reference ID (e.g. "figure-1.png").
                        Must match one of the available figure ref_ids.
                """
                nonlocal describer_input_tokens, describer_output_tokens
                nonlocal describe_call_count

                if describe_call_count >= max_describe_calls:
                    return (
                        f"ERROR: Maximum describe_image calls ({max_describe_calls}) "
                        f"reached. Skip remaining figures."
                    )

                describe_call_count += 1

                if ref_id not in figure_lookup:
                    available = sorted(figure_lookup.keys())
                    return (
                        f"ERROR: ref_id '{ref_id}' not found. "
                        f"Available ref_ids: {', '.join(available)}"
                    )

                figure = figure_lookup[ref_id]
                return await self._run_image_describer(
                    figure=figure,
                    current_markdown=current_markdown,
                    page_images={page_num: page_image_b64},
                    describer_tokens={
                        "input": describer_input_tokens,
                        "output": describer_output_tokens,
                    },
                    update_tokens=lambda inp, out: _update_page_describer_tokens(inp, out),
                )

            def _update_page_describer_tokens(inp: int, out: int) -> None:
                nonlocal describer_input_tokens, describer_output_tokens
                describer_input_tokens += inp
                describer_output_tokens += out

        # ------------------------------------------------------------------
        # Conditionally register reconstruct_table tool
        # ------------------------------------------------------------------
        if enable_table_reconstruct:
            @agent.tool_plain
            async def reconstruct_table(ref_id: str) -> str:
                """Reconstruct a table by comparing against the page image.

                Args:
                    ref_id: The table reference ID (e.g. "table-1").
                        Must match one of the available table ref_ids.
                """
                nonlocal table_reconstructor_input_tokens
                nonlocal table_reconstructor_output_tokens
                nonlocal table_reconstruct_call_count

                if table_reconstruct_call_count >= max_table_reconstruct_calls:
                    return (
                        f"ERROR: Maximum reconstruct_table calls "
                        f"({max_table_reconstruct_calls}) reached. "
                        f"Fix remaining tables with str_replace."
                    )

                table_reconstruct_call_count += 1

                if ref_id not in table_lookup:
                    available = sorted(table_lookup.keys())
                    return (
                        f"ERROR: ref_id '{ref_id}' not found. "
                        f"Available ref_ids: {', '.join(available)}"
                    )

                table = table_lookup[ref_id]
                return await self._run_table_reconstructor(
                    table=table,
                    current_markdown=current_markdown,
                    page_image_b64=page_image_b64,
                    update_tokens=lambda inp, out: _update_table_tokens(inp, out),
                )

            def _update_table_tokens(inp: int, out: int) -> None:
                nonlocal table_reconstructor_input_tokens
                nonlocal table_reconstructor_output_tokens
                table_reconstructor_input_tokens += inp
                table_reconstructor_output_tokens += out

        # ------------------------------------------------------------------
        # Conditionally register reconstruct_list tool
        # ------------------------------------------------------------------
        if enable_list_reconstruct:
            @agent.tool_plain
            async def reconstruct_list(list_text: str, reasoning: str) -> str:
                """Reconstruct a list by comparing against the page image.

                Args:
                    list_text: The markdown list text to reconstruct.
                        Include enough surrounding context to uniquely
                        identify the list in the page markdown.
                    reasoning: Why reconstruction is needed (e.g. "items
                        appear merged" or "nesting doesn't match image").
                """
                nonlocal list_reconstructor_input_tokens
                nonlocal list_reconstructor_output_tokens
                nonlocal list_reconstruct_call_count

                if list_reconstruct_call_count >= max_list_reconstruct_calls:
                    return (
                        f"ERROR: Maximum reconstruct_list calls "
                        f"({max_list_reconstruct_calls}) reached. "
                        f"Fix remaining lists with str_replace."
                    )

                list_reconstruct_call_count += 1

                # Verify the list text exists in the markdown
                if list_text not in current_markdown:
                    return (
                        f"ERROR: list_text not found in page markdown. "
                        f"Make sure you pass the exact text from the "
                        f"current markdown. Current markdown:\n"
                        f"---\n{current_markdown}\n---"
                    )

                # Extract surrounding context
                idx = current_markdown.find(list_text)
                start = max(0, idx - 200)
                end = min(len(current_markdown), idx + len(list_text) + 200)
                surrounding = current_markdown[start:end]

                return await self._run_list_reconstructor(
                    list_text=list_text,
                    surrounding_text=surrounding,
                    page_image_b64=page_image_b64,
                    page_number=page_num,
                    update_tokens=lambda inp, out: _update_list_tokens(inp, out),
                )

            def _update_list_tokens(inp: int, out: int) -> None:
                nonlocal list_reconstructor_input_tokens
                nonlocal list_reconstructor_output_tokens
                list_reconstructor_input_tokens += inp
                list_reconstructor_output_tokens += out

        # ------------------------------------------------------------------
        # Conditionally register rewrite_page tool (create mode only)
        # ------------------------------------------------------------------
        if is_create_mode:
            @agent.tool_plain
            def rewrite_page(new_markdown: str, reasoning: str) -> str:
                """Replace the ENTIRE page markdown with composed content.

                Use this when the page markdown is empty or stub-only and you
                need to compose content from the page image. After rewrite_page,
                you can use str_replace for small refinements.

                Args:
                    new_markdown: The complete new markdown for this page.
                        Must include all visible text from the page image.
                    reasoning: Why the rewrite is needed and what content
                        was composed from the image.
                """
                nonlocal current_markdown

                if not new_markdown.strip():
                    return (
                        "ERROR: new_markdown is empty. Read the page image "
                        "and compose markdown that captures all visible text."
                    )

                old_content = current_markdown
                current_markdown = new_markdown
                changes.append(
                    DocumentChange(
                        page=page_num,
                        old_text=old_content[:200] + ("..." if len(old_content) > 200 else ""),
                        new_text=new_markdown[:200] + ("..." if len(new_markdown) > 200 else ""),
                        reasoning=reasoning,
                        stage="create_mode_rewrite",
                    )
                )
                return f"OK — page {page_num} markdown replaced ({len(new_markdown)} chars)."

        # Build user message
        if not outline_text:
            outline_text = "\n".join(
                f"  {'#' * e.level} {e.text} (p{e.page})"
                for e in structure.outline
            )
        user_parts: list[Any] = []
        if page_image_b64:
            user_parts.append(
                BinaryContent(
                    data=base64.b64decode(page_image_b64),
                    media_type="image/png",
                )
            )
        # Build hints section if present
        hints_block = ""
        if context_hints:
            hints_lines = "\n".join(f"- {h}" for h in context_hints)
            hints_block = f"### Context hints\n{hints_lines}\n\n"

        if is_create_mode:
            instruction_tail = (
                "Read the page image and compose markdown that captures all "
                "visible text content. Use rewrite_page to set the page "
                "markdown, then describe_image for any figure references."
            )
        else:
            instruction_tail = (
                "Compare the image against the markdown. Make corrections "
                "with str_replace, or call no_changes if the page is correct."
            )

        user_parts.append(
            f"## Page {page_num}\n\n"
            f"### Document outline\n{outline_text}\n\n"
            f"{hints_block}"
            f"### Page markdown\n```\n{page_markdown}\n```\n\n"
            f"{instruction_tail}"
        )

        # Run agent
        agent_result = await agent.run(user_parts)
        usage = agent_result.usage()

        return PageCorrectionResult(
            page=page_num,
            corrected_markdown=current_markdown,
            changes=changes,
            issues=issues,
            input_tokens=usage.request_tokens or 0,
            output_tokens=usage.response_tokens or 0,
            describer_input_tokens=describer_input_tokens,
            describer_output_tokens=describer_output_tokens,
            table_reconstructor_input_tokens=table_reconstructor_input_tokens,
            table_reconstructor_output_tokens=table_reconstructor_output_tokens,
            list_reconstructor_input_tokens=list_reconstructor_input_tokens,
            list_reconstructor_output_tokens=list_reconstructor_output_tokens,
        )

    # ------------------------------------------------------------------
    # Phase 2b — Deterministic code block language tagging
    # ------------------------------------------------------------------

    async def _step_code_blocks(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
    ) -> None:
        """Wrap and tag code blocks using structure analysis metadata.

        Docling often renders code as plain unfenced text. This step uses the
        ``first_line`` / ``last_line`` reported by the structure agent to
        locate the code region in the markdown and wrap it in proper fences
        with the detected language.

        Also handles already-fenced blocks that lack a language tag.

        Edits whichever per-page version is newest (v1 if exists, else v0)
        in-place.
        """
        step_start = time.time()
        changes: list[DocumentChange] = []

        source_version = "v1" if "v1" in result.page_markdowns else "v0"

        if not structure.code_blocks:
            result.steps.append(
                StepResult(
                    name="code_blocks",
                    display_name="Code Block Languages",
                    version_before=source_version,
                    version_after=source_version,
                    elapsed_ms=0,
                    changes=[],
                    metadata={"blocks_tagged": 0},
                )
            )
            return

        page_mds = result.page_markdowns[source_version]

        for cb in structure.code_blocks:
            page_key = str(cb.page)
            page_md = page_mds.get(page_key, "")
            if not page_md:
                continue

            new_md, change = _apply_code_block_fence(
                page_md, cb, page_num=cb.page
            )
            if change:
                page_mds[page_key] = new_md
                changes.append(change)

        # Rebuild full version from corrected pages
        if changes:
            result.versions[source_version] = "\n\n".join(
                page_mds.get(str(p), "") for p in range(1, result.total_pages + 1)
            )

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="code_blocks",
                display_name="Code Block Languages",
                version_before=source_version,
                version_after=source_version,
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "blocks_tagged": len(changes),
                },
            )
        )

    # ------------------------------------------------------------------
    # Phase 3 — Cross-page fixes (on assembled document)
    # ------------------------------------------------------------------

    async def _step_boundaries(
        self,
        result: PipelineViewerResult,
        structure: StructureResult,
        *,
        section_map: SectionMap | None = None,
    ) -> None:
        """Fix cross-page issues on the assembled document.

        1. Deterministic hyphen-join for residual page-join artifacts
        2. Boundary agent with section-aware hints (running headers/footers,
           same-section joins, section transitions)
        3. Footnote relocation (move footnote bodies to endnotes)

        Produces v2.
        """
        source_version = "v1" if "v1" in result.versions else "v0"

        step_start = time.time()
        changes: list[DocumentChange] = []
        issues: list[str] = []
        total_input_tokens = 0
        total_output_tokens = 0

        current_document = result.versions.get(source_version, "")

        # --- Deterministic: hyphen-join for residual page-join artifacts ---
        hyphen_re = re.compile(r"([a-z])-\n\n([a-z])")
        before = current_document
        current_document = hyphen_re.sub(r"\1\2", current_document)
        if current_document != before:
            # Count the number of joins made
            join_count = len(hyphen_re.findall(before))
            changes.append(
                DocumentChange(
                    page=0,
                    old_text="(hyphenated line breaks)",
                    new_text="(joined words)",
                    reasoning=f"Joined {join_count} hyphenated word(s) split across page boundaries",
                    stage="boundary_fix",
                )
            )

        # --- Agent: Boundary fix with section-aware hints ---
        if result.total_pages > 1:
            if section_map is not None:
                boundary_snippets = self._generate_boundary_hints(
                    result, structure, section_map
                )
            else:
                # Fallback: basic boundary snippets without hints
                page_mds = result.page_markdowns.get(source_version, {})
                boundary_snippets = []
                for p in range(1, result.total_pages):
                    md_before = page_mds.get(str(p), "")
                    md_after = page_mds.get(str(p + 1), "")
                    boundary_snippets.append({
                        "page_before": p,
                        "page_after": p + 1,
                        "tail_text": "\n".join(md_before.split("\n")[-5:]),
                        "head_text": "\n".join(md_after.split("\n")[:5]),
                        "hints": [],
                    })

            footnote_numbers = [fn.number for fn in structure.footnotes]
            current_document, bd_changes, bd_issues, bd_in, bd_out = (
                await self._run_boundary_agent(
                    current_document,
                    boundary_snippets,
                    footnote_numbers,
                )
            )
            changes.extend(bd_changes)
            issues.extend(bd_issues)
            total_input_tokens += bd_in
            total_output_tokens += bd_out

        # --- Agent: Footnote relocation (kept for orphan footnotes) ---
        if structure.footnotes:
            current_document, fn_changes, fn_issues, fn_in, fn_out = (
                await self._run_footnote_agent(
                    current_document,
                    structure.footnotes,
                )
            )
            changes.extend(fn_changes)
            issues.extend(fn_issues)
            total_input_tokens += fn_in
            total_output_tokens += fn_out

        result.versions["v2"] = current_document

        elapsed_ms = int((time.time() - step_start) * 1000)

        from ..shared.llm_cost import calculate_estimated_cost

        cost_cents = calculate_estimated_cost(total_input_tokens, total_output_tokens)

        result.steps.append(
            StepResult(
                name="boundaries",
                display_name="Cross-Page Fixes",
                version_before=source_version,
                version_after="v2",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={
                    "footnotes_to_relocate": len(structure.footnotes),
                    "footnotes_relocated": len(
                        [c for c in changes if c.stage == "footnote"]
                    ),
                    "boundary_fixes": len(
                        [c for c in changes if c.stage == "boundary_fix"]
                    ),
                    "issues": issues,
                },
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cost_cents=cost_cents,
            )
        )

    async def _run_boundary_agent(
        self,
        document: str,
        boundary_snippets: list[dict],
        footnote_numbers: list[str],
    ) -> tuple[str, list[DocumentChange], list[str], int, int]:
        """Run the boundary fix agent on the assembled document.

        Returns:
            Tuple of (updated document, changes, issues, input_tokens, output_tokens).
        """
        from pydantic_ai import Agent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.boundary_fix import (
            BOUNDARY_FIX_SYSTEM_PROMPT,
            build_boundary_user_message,
        )

        model = get_model_for_tier(ModelTier.EFFICIENT)

        current_document = document
        changes: list[DocumentChange] = []
        issues: list[str] = []

        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,
            system_prompt=BOUNDARY_FIX_SYSTEM_PROMPT,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the document.

            Args:
                old_text: Exact text to find. Must appear once.
                new_text: Corrected text.
                reasoning: Why this change is needed.
                category: Should be "boundary_fix".
            """
            nonlocal current_document

            count = current_document.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in document.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Review the document and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_document = current_document.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=0,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage="boundary_fix",
                )
            )
            return "OK — replacement applied."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that no boundary fixes are needed.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations.
            """
            if notes:
                issues.append(f"Boundary agent ({confidence}): {notes}")
            return "Acknowledged — no boundary fixes needed."

        user_msg = build_boundary_user_message(
            assembled_document=document,
            boundary_snippets=boundary_snippets,
            footnote_numbers=footnote_numbers,
        )

        input_tokens = 0
        output_tokens = 0
        try:
            agent_result = await agent.run(user_msg)
            usage = agent_result.usage()
            input_tokens = usage.request_tokens or 0
            output_tokens = usage.response_tokens or 0
        except Exception as e:
            logger.error(f"Boundary fix agent failed: {e}")
            issues.append(f"Boundary agent error: {e}")

        return current_document, changes, issues, input_tokens, output_tokens

    async def _run_footnote_agent(
        self,
        document: str,
        footnotes: list[FootnoteInfo],
    ) -> tuple[str, list[DocumentChange], list[str], int, int]:
        """Run the footnote relocation agent on the document.

        Returns:
            Tuple of (updated document, changes, issues, input_tokens, output_tokens).
        """
        from pydantic_ai import Agent
        from ..agents.model_factory import get_model_for_tier

        from ..agents.model_tiers import ModelTier
        from ..agents.prompts.footnote_relocation import (
            FOOTNOTE_RELOCATION_SYSTEM_PROMPT,
            build_footnote_user_message,
        )

        model = get_model_for_tier(ModelTier.EFFICIENT)

        current_document = document
        changes: list[DocumentChange] = []
        issues: list[str] = []

        agent: Agent[None, None] = Agent(
            model=model,
            output_type=None,
            system_prompt=FOOTNOTE_RELOCATION_SYSTEM_PROMPT,
        )

        @agent.tool_plain
        def str_replace(old_text: str, new_text: str, reasoning: str, category: str) -> str:
            """Replace exact text in the document.

            Args:
                old_text: Exact text to find. Must appear once.
                new_text: Corrected text.
                reasoning: Why this change is needed.
                category: Should be "footnote".
            """
            nonlocal current_document

            count = current_document.count(old_text)
            if count == 0:
                return (
                    f"ERROR: old_text not found in document.\n\n"
                    f"You searched for:\n  {old_text!r}\n\n"
                    f"Review the document and try again."
                )
            if count > 1:
                return (
                    f"ERROR: old_text found {count} times. Include more "
                    f"surrounding context to make it unique."
                )

            current_document = current_document.replace(old_text, new_text, 1)
            changes.append(
                DocumentChange(
                    page=0,
                    old_text=old_text,
                    new_text=new_text,
                    reasoning=reasoning,
                    stage="footnote",
                )
            )
            return "OK — replacement applied."

        @agent.tool_plain
        def no_changes(confidence: str, notes: str = "") -> str:
            """Affirm that no footnote relocation is needed.

            Args:
                confidence: How confident: "high", "medium", or "low".
                notes: Optional observations.
            """
            if notes:
                issues.append(f"Footnote agent ({confidence}): {notes}")
            return "Acknowledged — no footnotes to relocate."

        footnote_dicts = [fn.model_dump() for fn in footnotes]
        user_msg = build_footnote_user_message(
            document=document,
            footnotes=footnote_dicts,
        )

        input_tokens = 0
        output_tokens = 0
        try:
            agent_result = await agent.run(user_msg)
            usage = agent_result.usage()
            input_tokens = usage.request_tokens or 0
            output_tokens = usage.response_tokens or 0
        except Exception as e:
            logger.error(f"Footnote relocation agent failed: {e}")
            issues.append(f"Footnote agent error: {e}")

        return current_document, changes, issues, input_tokens, output_tokens

    async def _step_cleanup(self, result: PipelineViewerResult) -> None:
        """Deterministic cleanup on the assembled document.

        Applies the full text_cleanup pipeline (PUA stripping, quote
        normalisation, NFKC unicode, letter-spacing collapse, whitespace
        cleanup, URL formatting) then records what changed in the ledger.

        Produces v3.
        """
        from src.utils.text_cleanup import cleanup_markdown as _cleanup_markdown

        step_start = time.time()
        source = result.versions.get("v2", "")
        changes: list[DocumentChange] = []

        cleaned = _cleanup_markdown(source, log_warnings=True)

        if cleaned != source:
            changes.append(
                DocumentChange(
                    page=0,
                    old_text="(multiple locations)",
                    new_text="(cleaned)",
                    reasoning=(
                        "Applied deterministic text cleanup: "
                        "PUA character removal, quote normalisation, "
                        "unicode NFKC, letter-spacing collapse, "
                        "whitespace cleanup, URL formatting"
                    ),
                    stage="deterministic",
                )
            )

        result.versions["v3"] = cleaned

        elapsed_ms = int((time.time() - step_start) * 1000)

        result.steps.append(
            StepResult(
                name="cleanup",
                display_name="Final Cleanup",
                version_before="v2",
                version_after="v3",
                elapsed_ms=elapsed_ms,
                changes=changes,
                metadata={},
            )
        )

