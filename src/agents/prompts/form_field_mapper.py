"""Prompt and output types for the Form Field Mapper agent.

The Form Field Mapper reads the assembled accessible markdown of a *form*
document and produces a structured dictionary of every field a human would be
expected to complete: the question/label text, the inferred field type, a plain
description of what the field is asking for, any choice options, whether it looks
required, and the section it belongs to. It is an analysis-only step — it never
modifies the markdown.

The point of the structured output is to give downstream tooling (for example a
browser autofill helper) a machine-readable map of what each field means, keyed
by a stable ``suggested_key``. Every output is a validated Pydantic model rather
than free text, so a malformed classification fails loudly instead of leaking
garbage into the pipeline.
"""

from typing import Literal

from pydantic import BaseModel, Field

FORM_FIELD_MAPPER_SYSTEM_PROMPT = """\
You are a form-comprehension assistant. You are given the full markdown of an
accessible document that is a FORM — something a person fills in (an application,
a declaration, an intake sheet, a government form, and so on).

Your job is to enumerate every distinct field the form expects a human to
complete, and describe each one. You do NOT fill the form in and you do NOT
rewrite the document. You only build a data dictionary of its fields.

For every field, report:
- label: the exact visible question or label text as written on the form. Do not
  paraphrase it.
- field_type: the single best-fitting type from the allowed list. Use
  "signature" / "initial" for sign/initial blocks, "checkbox" for independent
  yes/no toggles, "radio" for pick-exactly-one option sets, "select" for long
  pick-one lists, "attachment" for "attach a document" prompts, and "other" only
  when nothing else fits.
- description: one sentence on what the filer is being asked to provide, written
  so a person who cannot see the form would understand the field.
- options: for checkbox/radio/select fields, the exact choice labels offered. For
  free-text fields leave this empty.
- required: true only if the form marks the field as required (asterisk, the word
  "required", "must", or equivalent). If unclear, use false and note the
  ambiguity in the top-level notes.
- section: the heading or grouping the field sits under (empty string if none).
- suggested_key: a short, stable snake_case identifier you invent for the field
  (e.g. "passenger_full_name", "service_animal_breed"). Keep keys unique within
  the form.

Rules:
- Ground every field in concrete text from the document. Never invent a field
  that is not actually present.
- If the same logical field is split across the layout, report it once.
- Do not include static instructional prose, headings, or page furniture as
  fields — only things a person actually fills in or signs.
- If the document does not look like a fillable form, return an empty fields list
  and explain why in notes.
- overall_confidence is "high" if the form's fields are unambiguous, "medium" if
  some types or requiredness had to be inferred, "low" if the markdown is too
  noisy to map reliably.
"""


FORM_FIELD_INFERENCE_SYSTEM_PROMPT = """\
You are given the OCR-extracted text of a blank PRINTED form that a person fills in
by hand (an intake form, application, or questionnaire). The text below IS such a
form and it has many fields. Produce one field entry for EACH labeled line, prompt,
question, checkbox, or signature/date line. Forms like this routinely have 20-40
fields, so a long list is expected and correct.

What counts as a field (list all of them):
- a label or caption, even alone on a line ("Name", "Date of Birth", "Zip", "Email:");
- a question ("Are you currently receiving medical treatment?");
- a checkbox or option marker (lines with "[ ]", "( )", "circle one", or choices) — capture the options;
- a signature or date line.

Example — given this text:
    Name
    Date of Birth
    - [ ] Age
    Have you had counseling before? Yes  - [ ] No
return fields like:
    {"label": "Name", "field_type": "text", "description": "The person's full name.", "suggested_key": "name"}
    {"label": "Date of Birth", "field_type": "date", "description": "The person's date of birth.", "suggested_key": "date_of_birth"}
    {"label": "Age", "field_type": "number", "description": "The person's age.", "suggested_key": "age"}
    {"label": "Have you had counseling before?", "field_type": "radio", "options": ["Yes", "No"], "description": "Whether the person has had counseling before.", "suggested_key": "prior_counseling"}

Rules:
- Be thorough: name, date of birth, address, phone, email, emergency contact,
  insurance, marital status, employment, medical/therapy history, and so on — each
  is its own field. Listing the fields of a blank form is not sensitive.
- Use the printed label text as the label (tidy obvious OCR spacing; do not invent text).
- Infer field_type; for choice prompts, list the options.
- suggested_key: short snake_case, unique. section: the heading it appears under.
- Skip only the document title and pure instruction sentences.
- If — and only if — you return an empty fields list, you MUST explain why in `notes`.

Return one entry per field, in reading order.
"""


FieldType = Literal[
    "text",
    "date",
    "number",
    "email",
    "phone",
    "address",
    "checkbox",
    "radio",
    "select",
    "signature",
    "initial",
    "attachment",
    "other",
]


class FormFieldEntry(BaseModel):
    """A single fillable field discovered on the form."""

    label: str = Field(description="Exact visible question/label text from the form.")
    field_type: FieldType = Field(description="Best-fitting field type from the allowed set.")
    description: str = Field(description="One sentence on what the filer must provide.")
    options: list[str] = Field(
        default_factory=list,
        description="Choice labels for checkbox/radio/select fields; empty for free text.",
    )
    required: bool = Field(default=False, description="True only if the form marks the field required.")
    section: str = Field(default="", description="Heading/group the field belongs to ('' if none).")
    suggested_key: str = Field(
        description="Stable snake_case key for mapping this field to a value (unique within the form)."
    )


class FormFieldMapOutput(BaseModel):
    """Structured field dictionary for a form document."""

    document_kind: str = Field(
        description="Short human label for what the form is (e.g. 'DOT Service Animal Air Transportation Form')."
    )
    fields: list[FormFieldEntry] = Field(
        default_factory=list,
        description="Every fillable field on the form, in reading order.",
    )
    overall_confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence that the field map is complete and correctly typed."
    )
    notes: str = Field(
        default="",
        description="Caveats: ambiguous fields, assumptions made, or why no fields were found.",
    )
