# Accessible Forms

Turn a PDF form into something a blind or low-vision user can complete
independently: a WCAG-conformant HTML form generated from the PDF, with the
submitted answers written back into the original document so the final file is
visually identical to the official form.

This branch adds the accessible-forms feature to Equalify Reflow. It reuses the
existing stack — FastAPI, Docling (`docling-serve`), the PydanticAI agent
pipeline, the S3/Floci storage layer, and the Bedrock/Anthropic model factory —
rather than introducing new infrastructure.

## How it works

A PDF is routed by its structure into one of three tiers:

1. **AcroForm (fillable) PDFs.** The PDF's embedded form fields are the source of
   truth. They are read with `pypdf`, rendered as accessible HTML, and the
   submitted values are written straight back into the original PDF — so the
   output is byte-for-byte the official document, now populated.

2. **Scanned / image PDFs (vision tier).** There are no embedded fields, so each
   page image is read by the reasoning-tier vision model, which returns the
   fillable fields *and* the informational reading content (leaflets, FAQs,
   instructions) on the page. A clean accessible form is generated from that
   content, and answers are captured as a readable "responses" PDF (we do not
   overlay onto the scan).

3. **Flat digital PDFs (OCR fallback).** When vision is unavailable or returns
   nothing, a deterministic OCR-text parser discovers fields from the Docling
   markdown — no model required.

The whole flow degrades gracefully: if enrichment or vision fails, the build
falls back to a still-usable form rather than failing the request.

## Accessibility

The rendered HTML follows WCAG 2.1 AA: every control has a programmatic label
(1.3.1, 4.1.2), required state is conveyed in text and via `aria-required`
(3.3.2), help text is wired with `aria-describedby`, radio sets use
`fieldset`/`legend`, the layout reflows on zoom (1.4.10), it respects the user's
colour scheme and font size, and focus is always visible (2.4.7). The render path
is pure standard library (`html.escape` only), so it has no third-party
dependency and is trivially testable.

## Informational content (mixed packets)

Real packets often bundle a form with non-form pages — a "know your rights"
leaflet, a FAQ, instructions. Those pages used to be dropped. They are now
captured as reading content (`FormContentBlock`: heading + paragraphs, page
stamped) and rendered as static reading regions interleaved with the form in
page order, so a screen-reader user gets the whole packet. The same content is
appended to the responses PDF as an "Additional information" section.

## Vision-tier extraction rules

The vision prompt and extractor encode the rules that make legal/intake forms
read correctly:

- Labels are inferred from context, never a bare pronoun ("I, ____, being duly
  sworn …" becomes "Affiant name", not "I").
- Every field is assigned a `section` so the form is navigable by region.
- Court captions emit only the real blanks (County, Case Number, File Number,
  party names by printed role) under a "Case Caption" section.
- Multi-form packets set a per-page `form_name`; when two or more distinct forms
  are present, field sections are prefixed with the form name
  ("Complaint — Case Caption" vs "Motion and Affidavit — Case Caption") so
  repeated captions are distinguishable.
- A notarization/jurat date ("This __ day of __, 20__") is grouped as
  Day/Month/Year under "Notarization".
- Informational headings are transcribed accurately, and thin numbered "what to
  do" steps are folded into their parent block (deterministically, in
  `_merge_marker_blocks`, regardless of model variance).

### Reliability

Each page is read with a bounded retry (`_run_page_with_retry`): a transient
error or a completely empty return is retried, and any failure is logged at
WARNING/ERROR — so a transient hiccup can no longer silently drop a page's
content.

## Endpoints

API (require the API key):

- `POST /api/v1/forms/accessible/html` — PDF (+ optional Form Field Mapper
  metadata) → accessible HTML form (AcroForm fields are the source of truth).
- `POST /api/v1/forms/accessible/from-pdf` — end-to-end: PDF → accessible form,
  optionally enriched/vision-extracted. `as_json=true` returns `{form, html}`.
- `POST /api/v1/forms/accessible/fill` — original PDF + JSON values → filled PDF.
- `POST /api/v1/forms/accessible/prepare` — store a PDF + its form, return a
  browser `view_url`.
- `POST /api/v1/forms/upload` — store a local PDF and return a fetchable public
  URL (S3 temp bucket; Floci in dev).

Public, browser-facing (no API key, served same-origin):

- `GET /accessible-forms/view/{form_id}` — open the prepared form in a browser.
- `POST /accessible-forms/fill/{form_id}` — submit the form; returns the filled
  PDF (AcroForm) or a responses PDF (scanned/agent source).

## Files

New:

- `src/api/accessible_forms.py` — the API + browser routers.
- `src/services/accessible_form.py` — models, AcroForm read, HTML render,
  PDF write-back, responses PDF.
- `src/services/accessible_form_builder.py` — end-to-end PDF → form routing.
- `src/services/vision_form_extractor.py` — page-image vision extraction,
  retry, section qualification, marker-block merge.
- `src/services/accessible_form_store.py` — in-memory TTL store for prepared
  forms.
- `src/services/form_field_mapper.py` + `src/agents/prompts/form_field_mapper.py`
  — the Form Field Mapper agent (plain-language field descriptions).
- `src/agents/prompts/vision_form_fields.py` — vision prompt + output schema.

Modified:

- `src/main.py` — include the accessible-forms routers.
- `src/api/pipeline_viewer.py`, `src/services/pipeline_viewer.py` — optional
  `form_field_map` pipeline step.
- `pyproject.toml` — add the `pypdf` dependency.

Tests:

- `tests/unit/services/test_accessible_form.py`
- `tests/unit/services/test_accessible_form_builder.py`
- `tests/unit/services/test_form_field_map_step.py`

## Running the tests

```bash
uv run pytest tests/unit/services/test_accessible_form.py \
              tests/unit/services/test_accessible_form_builder.py \
              tests/unit/services/test_form_field_map_step.py -q
```

The accessible-form read/write tests need `pypdf` and `reportlab`; both are
declared in `pyproject.toml`. The render and helper tests are pure standard
library and always run.

## Notes

- `pypdf` is required for AcroForm read/write; it is imported lazily so the
  render path still works without it (the PDF functions raise a clear install
  hint instead).
- The vision tier uses the REASONING model tier (Sonnet) via the existing
  `get_model_for_tier` factory; backend selection (Bedrock vs Anthropic direct)
  is unchanged.
- PII handling is unchanged — this feature relies on Reflow's existing PII gate
  rather than adding its own.
