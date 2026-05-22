"""Accessible form generation and PDF write-back.

This module turns a PDF form into something a screen-reader or voice-tool user
can complete independently, then writes their answers back into the original PDF
so the final document is byte-for-byte the official form.

Two halves:

1. Read + render (accessibility): :func:`extract_acroform_fields` reads the
   PDF's real AcroForm fields (the authoritative skeleton), :func:`build_form`
   merges in the agent's plain-language descriptions, and
   :func:`render_accessible_form_html` emits a WCAG-conformant HTML form. The
   render path is pure standard library (``html.escape`` only) so it is trivially
   testable and has no third-party dependency.

2. Write-back (visual fidelity): :func:`fill_pdf_form` fills the original PDF's
   fields with submitted values and flips ``NeedAppearances`` so any viewer
   renders them. The output is the original document, visually unchanged, now
   populated.

The PDF read/write functions use ``pypdf`` (pure-Python AcroForm support). It is
imported lazily so that the render path keeps working even if ``pypdf`` is not
installed; the PDF functions raise a clear install hint instead. Add it with
``uv add pypdf``.

Accessibility decisions follow WCAG 2.1 AA: every control has a programmatic
label (1.3.1, 4.1.2), required state is conveyed in text and via
``aria-required`` (3.3.2), help text is wired with ``aria-describedby``, radio
sets use ``fieldset``/``legend`` (1.3.1), the layout reflows (1.4.10) and respects
the user's colour scheme and font size, and focus is always visible (2.4.7).
"""

from __future__ import annotations

import html
import logging
import re
from io import BytesIO
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# AcroForm field flag bits (PDF spec Table 226/227/228), 1-indexed in the spec.
_FF_REQUIRED = 1 << 1       # bit 2  — field must have a value
_FF_MULTILINE = 1 << 12     # bit 13 — text field is multi-line
_FF_PUSHBUTTON = 1 << 16    # bit 17 — button is a push button (no value)
_FF_RADIO = 1 << 15         # bit 16 — button is a radio set
_FF_COMBO = 1 << 17         # bit 18 — choice field is a combo box

ControlType = Literal[
    "text", "textarea", "date", "email", "tel", "number",
    "checkbox", "radio", "select", "signature",
]


class FormOption(BaseModel):
    """A single choice for a radio set or select."""

    value: str = Field(description="Export value written back to the PDF.")
    label: str = Field(description="Human-readable option text.")


class AccessibleFormField(BaseModel):
    """One control in the accessible form, keyed to its PDF field for write-back."""

    pdf_field: str = Field(description="AcroForm fully-qualified field name (write-back key).")
    control: ControlType = Field(description="Which HTML control to render.")
    label: str = Field(description="Accessible label (from /TU, the agent, or the field name).")
    description: str = Field(default="", description="Plain-language help text.")
    required: bool = Field(default=False)
    options: list[FormOption] = Field(default_factory=list, description="Choices for radio/select.")
    value: str = Field(default="", description="Current/default value.")
    on_value: str = Field(default="/Yes", description="Export 'on' state for a checkbox.")
    max_length: int | None = Field(default=None)
    section: str = Field(default="", description="Section/group heading this field sits under.")
    page: int | None = Field(default=None, description="0-based page index for overlay positioning (vision-extracted scans).")
    box: list[float] | None = Field(default=None, description="Normalized [x0, y0, x1, y1] answer-area box on the page (0..1).")


class FormContentBlock(BaseModel):
    """A block of informational reading content (not a fillable field).

    Captured from informational pages (leaflets, instruction sheets, FAQs, rights
    notices, cover letters) so a screen-reader user gets the whole packet — not
    just the blanks. Rendered as a static reading region, never as an input.
    """

    heading: str = Field(default="", description="Heading for this block, if any.")
    paragraphs: list[str] = Field(
        default_factory=list, description="Readable paragraphs, in reading order."
    )
    page: int | None = Field(
        default=None, description="0-based source page index, used to order content against fields."
    )


class AccessibleForm(BaseModel):
    """A complete, render-ready accessible form."""

    title: str
    language: str = "en"
    instructions: str = ""
    source: Literal["acroform", "agent", "merged"] = "merged"
    fields: list[AccessibleFormField] = Field(default_factory=list)
    content: list[FormContentBlock] = Field(
        default_factory=list,
        description="Informational reading content (non-field) from leaflet/instruction pages.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_pypdf() -> Any:
    """Import pypdf lazily, with a friendly error if it is missing."""
    try:
        import pypdf  # type: ignore
    except ImportError as e:  # pragma: no cover - environment-specific
        raise RuntimeError(
            "pypdf is required for PDF AcroForm read/write. Install it with "
            "'uv add pypdf' (or 'pip install pypdf')."
        ) from e
    return pypdf


def _normalize(text: str) -> str:
    """Lowercased, alphanumeric-only key for fuzzy label matching."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _label_tokens(text: str) -> set[str]:
    """Word tokens for overlap-based matching (lowercased, alphanumeric)."""
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t}


def _disambiguate_labels(fields: list[AccessibleFormField]) -> None:
    """Make duplicate labels unique using each field's (distinct) name.

    Real forms routinely give several fields the same /TU label ("Phone") while
    the field names carry the distinction ("Phone (Veterinarian)"). Identical
    labels are an accessibility problem on their own — a screen reader announces
    them the same way — so when a label repeats, promote the field name to the
    label.
    """
    from collections import Counter

    counts = Counter(f.label for f in fields)
    for f in fields:
        if counts[f.label] > 1 and f.pdf_field and f.pdf_field != f.label:
            f.label = f.pdf_field


def _assign_agent_entries(
    fields: list[AccessibleFormField], agent_fields: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Map each form field to its best agent entry by token overlap.

    Matching agent descriptions back to the authoritative fields by exact label
    fails when labels collide or are phrased differently. Instead score every
    (field, agent-entry) pair by Jaccard token overlap over the field's label +
    name vs. the agent entry's label + suggested_key, then assign greedily from
    the highest score so each description lands on the field it actually
    describes (and each agent entry is used at most once).
    """
    pairs: list[tuple[float, int, int]] = []
    for i, fld in enumerate(fields):
        ftok = _label_tokens(fld.label) | _label_tokens(fld.pdf_field)
        for j, ag in enumerate(agent_fields):
            gtok = _label_tokens(ag.get("label")) | _label_tokens(ag.get("suggested_key"))
            inter = ftok & gtok
            if not inter:
                continue
            pairs.append((len(inter) / len(ftok | gtok), i, j))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_field: set[int] = set()
    used_agent: set[int] = set()
    out: dict[int, dict[str, Any]] = {}
    for score, i, j in pairs:
        if score < 0.34:
            break
        if i in used_field or j in used_agent:
            continue
        out[i] = agent_fields[j]
        used_field.add(i)
        used_agent.add(j)
    return out


def _prettify_name(name: str) -> str:
    """Turn an AcroForm field name into a readable fallback label."""
    spaced = re.sub(r"[_\-.]+", " ", name or "")
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", spaced)  # camelCase -> camel Case
    spaced = re.sub(r"\s+", " ", spaced).strip()
    return spaced[:1].upper() + spaced[1:] if spaced else name


def _refine_text_control(label: str) -> ControlType:
    """Pick a more specific text input type from label keywords (conservative)."""
    key = (label or "").lower()
    if any(w in key for w in ("e-mail", "email")):
        return "email"
    if any(w in key for w in ("phone", "telephone", "fax", "mobile", "cell")):
        return "tel"
    if any(w in key for w in ("date", "dob", "birth", "d.o.b")):
        return "date"
    if any(w in key for w in ("zip", "postal", "amount", "quantity", "number of", "age")):
        return "number"
    return "text"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 1. Read AcroForm fields
# ---------------------------------------------------------------------------

def extract_acroform_fields(pdf_bytes: bytes) -> list[AccessibleFormField]:
    """Read the PDF's AcroForm fields as the authoritative form skeleton.

    Returns one :class:`AccessibleFormField` per fillable field, in document
    order where pypdf preserves it. Push buttons are skipped (they hold no
    value). Returns an empty list for flat PDFs with no AcroForm.
    """
    pypdf = _require_pypdf()
    reader = pypdf.PdfReader(BytesIO(pdf_bytes))

    fields = reader.get_fields()
    if not fields:
        return []

    out: list[AccessibleFormField] = []
    for name, f in fields.items():
        ft = f.get("/FT")
        flags = _as_int(f.get("/Ff"))
        required = bool(flags & _FF_REQUIRED)
        label = str(f.get("/TU") or _prettify_name(str(name)))
        value = f.get("/V")
        value = "" if value is None else str(value).lstrip("/")

        if ft == "/Btn":
            if flags & _FF_PUSHBUTTON:
                continue  # action button, nothing to fill
            states = [str(s) for s in (f.get("/_States_") or [])]
            on_states = [s for s in states if s not in ("/Off", "Off")]
            if flags & _FF_RADIO or len(on_states) > 1:
                out.append(AccessibleFormField(
                    pdf_field=str(name),
                    control="radio",
                    label=label,
                    required=required,
                    value=value,
                    options=[FormOption(value=s, label=s.lstrip("/")) for s in on_states],
                ))
            else:
                on_value = on_states[0] if on_states else "/Yes"
                out.append(AccessibleFormField(
                    pdf_field=str(name),
                    control="checkbox",
                    label=label,
                    required=required,
                    on_value=on_value,
                    value=value,
                ))
        elif ft == "/Ch":
            opts: list[FormOption] = []
            for opt in (f.get("/Opt") or []):
                if isinstance(opt, (list, tuple)) and len(opt) >= 2:
                    opts.append(FormOption(value=str(opt[0]), label=str(opt[1])))
                else:
                    opts.append(FormOption(value=str(opt), label=str(opt)))
            out.append(AccessibleFormField(
                pdf_field=str(name),
                control="select",
                label=label,
                required=required,
                options=opts,
                value=value,
            ))
        elif ft == "/Sig":
            out.append(AccessibleFormField(
                pdf_field=str(name),
                control="signature",
                label=label,
                required=required,
            ))
        elif ft == "/Tx":
            control: ControlType = "textarea" if (flags & _FF_MULTILINE) else _refine_text_control(label)
            out.append(AccessibleFormField(
                pdf_field=str(name),
                control=control,
                label=label,
                required=required,
                value=value,
                max_length=_as_int(f.get("/MaxLen")) or None,
            ))
        else:
            # No /FT (a non-terminal/parent node pypdf surfaced) or an unsupported
            # type — not a renderable terminal field, so skip it.
            continue
    return out


# ---------------------------------------------------------------------------
# 2. Merge with the agent field dictionary
# ---------------------------------------------------------------------------

def build_form(
    *,
    title: str,
    acroform_fields: list[AccessibleFormField] | None = None,
    field_map: dict[str, Any] | None = None,
    language: str = "en",
    instructions: str = "",
) -> AccessibleForm:
    """Combine the authoritative AcroForm skeleton with the agent's descriptions.

    ``field_map`` is the Form Field Mapper step's ``metadata`` (with a ``fields``
    list of ``{label, description, suggested_key, field_type, section, ...}``).

    If ``acroform_fields`` is non-empty it is the source of truth (so write-back
    is exact) and the agent entries only enrich labels/descriptions/sections.
    If there is no AcroForm, the form is built from the agent dictionary alone
    (``source='agent'`` — write-back to a flat PDF is not possible).
    """
    agent_fields = list((field_map or {}).get("fields", []) or [])

    if acroform_fields:
        # Distinct labels first (fields that share a /TU label like "Phone"), then
        # attach each agent description to the field it actually describes via
        # token overlap — exact-label matching collides across repeated labels.
        _disambiguate_labels(acroform_fields)
        matches = _assign_agent_entries(acroform_fields, agent_fields)
        for i, fld in enumerate(acroform_fields):
            match = matches.get(i)
            if not match:
                continue
            if match.get("description"):
                fld.description = str(match["description"])
            if match.get("section"):
                fld.section = str(match["section"])
            # Refine a generic text box using the agent's semantic type.
            if fld.control == "text":
                at = str(match.get("field_type") or "")
                if at in ("date", "email", "number"):
                    fld.control = at  # type: ignore[assignment]
                elif at in ("phone", "tel"):
                    fld.control = "tel"
        return AccessibleForm(title=title, language=language, instructions=instructions,
                              source="merged", fields=acroform_fields)

    # No AcroForm: build from the agent dictionary (no write-back possible).
    fields: list[AccessibleFormField] = []
    _agent_to_control = {
        "text": "text", "date": "date", "number": "number", "email": "email",
        "phone": "tel", "address": "textarea", "checkbox": "checkbox",
        "radio": "radio", "select": "select", "signature": "signature",
        "initial": "text", "attachment": "text", "other": "text",
    }
    for af in agent_fields:
        ctype: ControlType = _agent_to_control.get(str(af.get("field_type")), "text")  # type: ignore[assignment]
        opts = [FormOption(value=str(o), label=str(o)) for o in (af.get("options") or [])]
        fields.append(AccessibleFormField(
            pdf_field=str(af.get("suggested_key") or af.get("label") or "field"),
            control=ctype,
            label=str(af.get("label") or af.get("suggested_key") or "Field"),
            description=str(af.get("description") or ""),
            required=bool(af.get("required")),
            options=opts,
            section=str(af.get("section") or ""),
        ))
    _disambiguate_labels(fields)
    return AccessibleForm(title=title, language=language, instructions=instructions,
                          source="agent", fields=fields)


def fields_from_markdown(markdown: str) -> list[AccessibleFormField]:
    """Deterministically discover fields from a form's (OCR'd) markdown.

    Docling renders a printed form as roughly one label per line, with ``## ``
    section headings and ``- [ ]`` checkboxes. We turn each label / checkbox /
    choice line into a field. This needs NO model, so it works on scanned forms
    from OCR alone. It is heuristic and best-effort, for forms that have no
    AcroForm layer; the field names are inferred, not authoritative.
    """
    fields: list[AccessibleFormField] = []
    section = ""
    seen: set[str] = set()

    def _key(label: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:60] or "field"
        key, i = base, 2
        while key in seen:
            key = f"{base}_{i}"
            i += 1
        seen.add(key)
        return key

    def _add(label: str, control: ControlType, options: list[str] | None = None) -> None:
        label = label.strip().rstrip("_").strip().rstrip(":").strip()
        if len(label) < 2 or len(label) > 120:  # skip noise and long instruction lines
            return
        fields.append(AccessibleFormField(
            pdf_field=_key(label),
            control=control,
            label=label,
            section=section,
            options=[FormOption(value=o, label=o) for o in (options or [])],
        ))

    # Long lines are kept only if they're a question or a fill-in prompt; long
    # declarative sentences are instructions/policies, not fields.
    prompt_cues = (
        "describe", "name", "list", "explain", "provide", "briefly",
        "what", "how", "why", "in your own words", "please describe",
    )
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--") or line.startswith("|"):
            continue  # blank, image marker, or a markdown table row
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        m = re.match(r"^[-*]\s*\[\s*[xX]?\s*\]\s*(.+)$", line)
        if m:
            _add(m.group(1), "checkbox")
            continue
        if "circle one" in line.lower():
            label = re.split(r"\(?\s*circle one\s*\)?", line, flags=re.I)[0]
            tail = re.sub(r".*circle one\s*\)?", "", line, flags=re.I)
            options = re.findall(r"[A-Za-z][A-Za-z/]*", tail)
            _add(label, "radio", options)
            continue
        if len(line) > 60:
            low = line.lower().lstrip("-*0123456789. ").strip()
            if line.rstrip().endswith("?") or low.startswith(prompt_cues):
                _add(line, "text")  # a question or fill-in prompt (free text)
            # otherwise a long declarative line (instruction/policy) — skip it
            continue
        _add(line, _refine_text_control(line))

    # NB: labels are intentionally NOT disambiguated here. On a fieldless form the
    # field name is a slug of the label, so promoting it to the label would turn
    # readable text ("Date") into keys ("date_2"). Repeated labels are instead
    # contextualised by their section heading in the rendered form.
    return fields


# ---------------------------------------------------------------------------
# 3. Render accessible HTML (pure standard library)
# ---------------------------------------------------------------------------

_BASE_CSS = """\
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 1rem/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1a1a1a; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #f2f2f2; background: #14161a; } }
main { max-width: 46rem; margin: 0 auto; padding: 1.25rem clamp(1rem, 4vw, 2rem) 4rem; }
h1 { font-size: 1.55rem; line-height: 1.25; }
h2 { font-size: 1.2rem; margin-top: 2rem; border-bottom: 1px solid currentColor; padding-bottom: .25rem; }
.skip-link { position: absolute; left: -9999px; top: 0; background: #0b5fff; color: #fff;
  padding: .6rem 1rem; border-radius: 0 0 .4rem 0; z-index: 10; }
.skip-link:focus { left: 0; }
.intro { padding: .75rem 1rem; border-left: 4px solid #0b5fff; background: rgba(11,95,255,.08); }
.reading { margin: 1.5rem 0; }
.reading p { margin: .55rem 0; }
.field { margin: 1.1rem 0; }
.field > label, fieldset > legend { display: block; font-weight: 600; margin-bottom: .35rem; }
.req { color: #b00020; font-weight: 700; }
@media (prefers-color-scheme: dark) { .req { color: #ff7a8a; } }
.hint { margin: .2rem 0 .4rem; font-size: .9rem; opacity: .85; }
input[type=text], input[type=email], input[type=tel], input[type=number],
input[type=date], textarea, select {
  width: 100%; max-width: 34rem; padding: .55rem .6rem; font-size: 1rem;
  border: 1px solid #767676; border-radius: .4rem; background: Field; color: FieldText; }
textarea { min-height: 5rem; }
fieldset { border: 1px solid #aaa; border-radius: .5rem; margin: 1.1rem 0; padding: .5rem 1rem 1rem; }
.choice { display: flex; align-items: flex-start; gap: .5rem; margin: .4rem 0; }
.choice input { width: 1.15rem; height: 1.15rem; margin-top: .15rem; flex: none; }
.choice label { font-weight: 400; }
:focus-visible { outline: 3px solid #0b5fff; outline-offset: 2px; }
button[type=submit] { margin-top: 2rem; font-size: 1.05rem; font-weight: 600; padding: .7rem 1.4rem;
  border: 0; border-radius: .5rem; background: #0b5fff; color: #fff; cursor: pointer; }
button[type=submit]:hover { background: #0a4fd6; }
#error-summary:not(:empty) { border: 2px solid #b00020; background: rgba(176,0,32,.08);
  padding: 1rem; border-radius: .5rem; margin: 1rem 0; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _slug(value: str, idx: int) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-")
    return f"f{idx}-{base or 'field'}"


def _render_field(fld: AccessibleFormField, idx: int) -> str:
    fid = _slug(fld.pdf_field, idx)
    desc_id = f"{fid}-desc"
    has_desc = bool(fld.description)
    describedby = f' aria-describedby="{desc_id}"' if has_desc else ""
    req_attr = ' aria-required="true" required' if fld.required else ""
    req_mark = ' <span class="req" aria-hidden="true">*</span><span class="sr-only">required</span>' if fld.required else ""
    name = _esc(fld.pdf_field)
    label = _esc(fld.label)
    hint = f'<p class="hint" id="{desc_id}">{_esc(fld.description)}</p>' if has_desc else ""

    if fld.control == "radio":
        choices = []
        for j, opt in enumerate(fld.options):
            oid = f"{fid}-{j}"
            checked = " checked" if fld.value and fld.value.lstrip("/") == opt.value.lstrip("/") else ""
            choices.append(
                f'<div class="choice"><input type="radio" id="{oid}" name="{name}" '
                f'value="{_esc(opt.value)}"{req_attr if j == 0 else ""}{checked}>'
                f'<label for="{oid}">{_esc(opt.label)}</label></div>'
            )
        described = f' aria-describedby="{desc_id}"' if has_desc else ""
        return (
            f'<fieldset class="field"{described}><legend>{label}{req_mark}</legend>'
            f'{hint}{"".join(choices)}</fieldset>'
        )

    if fld.control == "checkbox":
        checked = " checked" if fld.value and fld.value.lstrip("/") not in ("", "Off") else ""
        return (
            f'<div class="field"><div class="choice">'
            f'<input type="checkbox" id="{fid}" name="{name}" value="{_esc(fld.on_value)}"{req_attr}{describedby}{checked}>'
            f'<label for="{fid}">{label}{req_mark}</label></div>{hint}</div>'
        )

    if fld.control == "select":
        opts = ['<option value="">Choose…</option>']
        for opt in fld.options:
            sel = " selected" if fld.value and fld.value == opt.value else ""
            opts.append(f'<option value="{_esc(opt.value)}"{sel}>{_esc(opt.label)}</option>')
        return (
            f'<div class="field"><label for="{fid}">{label}{req_mark}</label>{hint}'
            f'<select id="{fid}" name="{name}"{req_attr}{describedby}>{"".join(opts)}</select></div>'
        )

    if fld.control == "textarea":
        return (
            f'<div class="field"><label for="{fid}">{label}{req_mark}</label>{hint}'
            f'<textarea id="{fid}" name="{name}"{req_attr}{describedby}>{_esc(fld.value)}</textarea></div>'
        )

    if fld.control == "signature":
        note = fld.description or "Type your full legal name as your signature."
        return (
            f'<div class="field"><label for="{fid}">{label}{req_mark}</label>'
            f'<p class="hint" id="{desc_id}">{_esc(note)}</p>'
            f'<input type="text" id="{fid}" name="{name}" autocomplete="name" '
            f'aria-describedby="{desc_id}"{req_attr}></div>'
        )

    # text / date / email / tel / number
    input_type = fld.control if fld.control in ("date", "email", "tel", "number") else "text"
    maxlen = f' maxlength="{fld.max_length}"' if fld.max_length else ""
    return (
        f'<div class="field"><label for="{fid}">{label}{req_mark}</label>{hint}'
        f'<input type="{input_type}" id="{fid}" name="{name}" value="{_esc(fld.value)}"'
        f'{maxlen}{req_attr}{describedby}></div>'
    )


def _render_content_block(block: FormContentBlock) -> str:
    """Render one informational block as a static reading region (no inputs)."""
    parts: list[str] = ['<section class="reading">']
    if block.heading:
        parts.append(f"<h2>{_esc(block.heading)}</h2>")
    for para in block.paragraphs:
        if para and para.strip():
            parts.append(f"<p>{_esc(para)}</p>")
    parts.append("</section>")
    return "".join(parts)


def _page_key(page: int | None) -> int:
    """Sort key for page ordering; fields/blocks with no page sort first together."""
    return page if page is not None else -1


def render_accessible_form_html(form: AccessibleForm, *, action_url: str = "", method: str = "post") -> str:
    """Render the form as a complete, WCAG-conformant HTML document.

    The returned page is self-contained (inline CSS, no external assets), reflows
    on zoom, respects the user's colour scheme, and every control is
    programmatically labelled. Each control's ``name`` is its PDF field, so the
    submitted form data maps straight back into :func:`fill_pdf_form`.
    """
    title = _esc(form.title or "Form")
    intro = (
        f'<p class="intro" id="form-intro">{_esc(form.instructions)}</p>'
        if form.instructions else ""
    )

    # Render fields grouped by section (first-seen order), continuing a shared
    # control index so ids stay unique across sections/pages.
    idx = 0

    def _render_sections(fields_subset: list[AccessibleFormField]) -> list[str]:
        nonlocal idx
        out: list[str] = []
        secs: list[str] = []
        for f in fields_subset:
            sec = f.section or ""
            if sec not in secs:
                secs.append(sec)
        for sec in secs:
            if sec:
                out.append(f"<h2>{_esc(sec)}</h2>")
            for f in [x for x in fields_subset if (x.section or "") == sec]:
                out.append(_render_field(f, idx))
                idx += 1
        return out

    body_parts: list[str] = []
    if form.content:
        # Interleave informational reading regions with field sections in page
        # order (a page's reading content precedes its fields).
        pages = sorted(
            {_page_key(b.page) for b in form.content}
            | {_page_key(f.page) for f in form.fields}
        )
        for pg in pages:
            for block in [b for b in form.content if _page_key(b.page) == pg]:
                body_parts.append(_render_content_block(block))
            body_parts.extend(_render_sections([f for f in form.fields if _page_key(f.page) == pg]))
    else:
        # Legacy path: no informational content — render exactly as before.
        body_parts.extend(_render_sections(list(form.fields)))

    described = ' aria-describedby="form-intro"' if intro else ""
    has_required = any(f.required for f in form.fields)
    required_note = (
        '<p>Fields marked with an asterisk (<span class="req" aria-hidden="true">*</span>) are required.</p>'
        if has_required else ""
    )

    if not form.fields:
        # Pure informational packet (no fillable fields): a reading document with
        # no <form> or submit control.
        return f"""<!DOCTYPE html>
<html lang="{_esc(form.language or 'en')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
<a class="skip-link" href="#content-start">Skip to content</a>
<main>
<h1>{title}</h1>
{intro}
<div id="content-start">
{chr(10).join(body_parts)}
</div>
</main>
</body>
</html>
"""

    return f"""<!DOCTYPE html>
<html lang="{_esc(form.language or 'en')}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
<a class="skip-link" href="#form-start">Skip to the form</a>
<main>
<h1>{title}</h1>
{intro}
{required_note}
<div id="error-summary" role="alert" tabindex="-1"></div>
<form id="form-start" method="{_esc(method)}" action="{_esc(action_url)}"{described}>
{chr(10).join(body_parts)}
<button type="submit">Review and download filled PDF</button>
</form>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 4. Write values back into the original PDF
# ---------------------------------------------------------------------------

def _set_need_appearances(writer: Any) -> None:
    """Flip NeedAppearances so viewers render the filled values (API varies)."""
    try:
        writer.set_need_appearances_writer(True)
        return
    except TypeError:
        try:
            writer.set_need_appearances_writer()
            return
        except Exception:
            pass
    except Exception:
        pass
    # Fallback: set the flag directly on the AcroForm dictionary.
    try:
        from pypdf.generic import BooleanObject, NameObject  # type: ignore
        root = writer._root_object
        if "/AcroForm" in root:
            root["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    except Exception:  # pragma: no cover - best effort
        logger.warning("Could not set NeedAppearances on the filled PDF")


def fill_pdf_form(pdf_bytes: bytes, values: dict[str, Any]) -> bytes:
    """Fill the original PDF's fields and return the populated PDF bytes.

    ``values`` maps PDF field names to submitted values (the ``name`` attributes
    from the rendered HTML). Checkbox values are coerced to the field's on/off
    state automatically, so a truthy value ticks the box. The original document
    is preserved exactly; only field values change.
    """
    pypdf = _require_pypdf()
    reader = pypdf.PdfReader(BytesIO(pdf_bytes))
    writer = pypdf.PdfWriter()
    writer.append(reader)

    # Coerce values per field type so checkboxes/radios get valid state names.
    field_defs = reader.get_fields() or {}
    coerced: dict[str, Any] = {}
    for name, raw in values.items():
        spec = field_defs.get(name)
        ft = spec.get("/FT") if spec else None
        flags = _as_int(spec.get("/Ff")) if spec else 0
        if ft == "/Btn" and not (flags & _FF_RADIO):
            states = [str(s) for s in (spec.get("/_States_") or [])] if spec else []
            on_state = next((s for s in states if s not in ("/Off", "Off")), "/Yes")
            truthy = raw not in (None, "", False, "off", "Off", "false", "False", "0", 0)
            coerced[name] = on_state if truthy else "/Off"
        elif ft == "/Btn":  # radio
            sval = str(raw)
            coerced[name] = sval if sval.startswith("/") else f"/{sval}"
        else:
            coerced[name] = "" if raw is None else str(raw)

    for page in writer.pages:
        try:
            writer.update_page_form_field_values(page, coerced, auto_regenerate=False)
        except TypeError:
            # Older pypdf without auto_regenerate kwarg.
            writer.update_page_form_field_values(page, coerced)
        except Exception as e:  # pragma: no cover - per-page resilience
            logger.warning(f"Form fill on a page failed: {e}")

    _set_need_appearances(writer)

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def render_responses_pdf(form: AccessibleForm, values: dict[str, Any]) -> bytes:
    """Produce a clean 'responses' PDF for forms with no fillable layer.

    Flat/scanned PDFs have no AcroForm fields to write into, so we record the
    user's answers as a readable, paginated document (label: value, grouped by
    section). This is the reliable fallback; a visually-identical overlay onto the
    original scan is a separate, position-dependent step.
    """
    from io import BytesIO as _BytesIO
    from xml.sax.saxutils import escape as _xml

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    buf = _BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=(form.title or "Responses"))
    styles = getSampleStyleSheet()
    story: list[Any] = [Paragraph(_xml(form.title or "Responses"), styles["Title"]), Spacer(1, 12)]
    last_section: str | None = None
    for fld in form.fields:
        if fld.section and fld.section != last_section:
            story.append(Spacer(1, 6))
            story.append(Paragraph(_xml(fld.section), styles["Heading2"]))
            last_section = fld.section
        raw = values.get(fld.pdf_field)
        if fld.control == "checkbox":
            text = "Yes" if raw not in (None, "", False, "Off", "/Off", "false", "0") else "No"
        else:
            text = "" if raw is None else str(raw)
        body = _xml(text) if text else "<i>(blank)</i>"
        story.append(Paragraph(f"<b>{_xml(fld.label)}:</b> {body}", styles["Normal"]))
        if fld.description:
            # Show the statement/help text (e.g. the acknowledgement a person
            # initialed) so the record is complete.
            story.append(Paragraph(f"<font size=8 color='#555555'>{_xml(fld.description)}</font>", styles["Normal"]))
        story.append(Spacer(1, 6))

    # Informational (non-field) content captured from leaflet/instruction pages,
    # appended so the saved record carries the full packet context.
    if form.content:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Additional information", styles["Heading1"]))
        for block in form.content:
            if block.heading:
                story.append(Paragraph(_xml(block.heading), styles["Heading2"]))
            for para in block.paragraphs:
                if para and para.strip():
                    story.append(Paragraph(_xml(para), styles["Normal"]))
                    story.append(Spacer(1, 4))

    doc.build(story)
    return buf.getvalue()


def overlay_answers_on_pdf(
    pdf_bytes: bytes, fields: list[AccessibleFormField], values: dict[str, Any], *, scale: float = 1.5
) -> bytes:
    """Draw answers onto the original page images at each field's box.

    For scanned forms (no AcroForm), renders each page as an image and writes the
    user's answers at the approximate answer-area positions captured by the vision
    extractor, so the output resembles the original form filled in. Positions are
    approximate, so placement is best-effort. Falls back to the unmodified PDF if
    rendering is unavailable.
    """
    import io

    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    by_page: dict[int, list[AccessibleFormField]] = {}
    for fld in fields:
        if fld.page is not None and fld.box and len(fld.box) >= 4:
            by_page.setdefault(fld.page, []).append(fld)

    pdf = pdfium.PdfDocument(pdf_bytes)
    out = io.BytesIO()
    c = None
    for i in range(len(pdf)):
        pil = pdf[i].render(scale=scale).to_pil().convert("RGB")
        w, h = pil.size
        if c is None:
            c = canvas.Canvas(out, pagesize=(w, h))
        else:
            c.setPageSize((w, h))
        c.drawImage(ImageReader(pil), 0, 0, width=w, height=h)
        c.setFont("Helvetica", 11)
        for fld in by_page.get(i, []):
            raw = values.get(fld.pdf_field)
            if raw in (None, "", False):
                continue
            x0, _y0, _x1, y1 = (list(fld.box) + [0, 0, 0, 0])[:4]
            # normalized (top-left origin) -> PDF points (bottom-left origin)
            px = x0 * w + 2
            py = h - (y1 * h) + 2
            text = "X" if fld.control == "checkbox" else str(raw)
            c.setFillColorRGB(0.0, 0.0, 0.55)  # dark blue so answers stand out on the scan
            c.drawString(px, py, text[:120])
        c.showPage()
    if c is None:
        return pdf_bytes
    c.save()
    return out.getvalue()
