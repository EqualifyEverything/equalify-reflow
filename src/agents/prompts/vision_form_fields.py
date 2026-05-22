"""Prompt and output types for the vision-based form field extractor.

Reads a rendered page image of a printed/scanned form and returns the fields a
person fills in, transcribed accurately and completely. The model reads the image
directly (so it restores correct word spacing that OCR mangles) and follows the
field-typing rules below.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .form_field_mapper import FieldType

VISION_FORM_SYSTEM_PROMPT = """\
You are reading ONE page image of a printed form (an intake form, application,
consent, or questionnaire) that a person fills in by hand. List EVERY field on
this page that a person writes, marks, checks, selects, or signs — accurately and
completely.

Read like a sighted human, not raw OCR:
- Restore correct spacing in run-together words: "Unabletotrustothers" ->
  "Unable to trust others"; "ADULTINFORMATIONFORM" -> "ADULT INFORMATION FORM".
- Fix obvious OCR typos ("COMMUNICATON" -> "COMMUNICATION") and normalize quotes.
- Use the FULL printed text; never truncate or summarize.
- A label is the field's NAME only: do NOT put trailing colons or instruction
  phrases in it. Write "Marital Status" (not "Marital Status (circle one):") and
  "Date" (not "Date:").
- Capitalize labels normally (Title Case); do NOT leave a label in ALL CAPS —
  write "Name of Primary Care Physician", not "NAME OF PRIMARY CARE PHYSICIAN".
  Keep genuine acronyms as-is (HIPAA, PHI, CCCA, ID).
- NEVER label a field with a bare pronoun or article ("I", "A", "An", "The"). A
  blank inside a first-person sentence ("I, ____, being duly sworn ...") names the
  person completing the form — label it "Your full name" (or "Affiant name" on a
  sworn statement) and put the full sentence in `description`. Always infer the
  field's real name from the surrounding text, never the stray word beside it.

Field-type rules (apply in this priority order):
- gender or sex -> select (dropdown) with the printed options.
- marital status -> select (dropdown) with the printed options, EVEN IF the form
  says "circle one".
- any OTHER "circle one" / circled-choice instruction -> radio group with the
  printed choices as options.
- a Yes/No question -> a SINGLE radio group with options ["Yes", "No"] (never two
  separate checkboxes).
- "initial" / "please initial" -> a text field whose label is "Initials".
- occupation -> text.
- date line -> date; email -> email; phone or fax -> tel; numeric (zip, age,
  "number of ...") -> number; signature line -> text.
- a notarization/jurat date written as "This ___ day of ___, 20__" is ONE date
  split across three blanks: emit "Day" (number), "Month" (text) and "Year"
  (number), put all three under section "Notarization", and note "Date sworn" in
  each description so they read as one group.
- a written-answer prompt ("In your own words ...", "Name the main goal ...") ->
  a text field for the answer.
- anything else a person writes in -> text.

Groups, "Other", and acknowledgements:
- EVERY checkbox/option group that includes an "Other" choice (e.g. "Living with:
  Spouse / Parents / ... / Other") must ALSO get a separate text field to specify
  it, labelled "<group> - Other (please specify)".
- "Appointments only" / "Billing/Insurance" toggles beside a contact -> checkboxes
  in that contact's group.
- A section that says "initial each item" (or lists statements to acknowledge):
  each statement is its OWN field of type text. Label it "Initials - <short
  topic>" (a 2-4 word summary, e.g. "Initials - privacy practices", "Initials -
  cancellation policy") so the labels are distinct, and put the FULL statement
  text in `description` (NOT in the label).

Tables:
- A grid/table -> emit ONE field per cell, labelled "<row> - <column>" (e.g.
  "Spouse - Name", "Date Started - Row 1"). Provide a field for every cell the
  printed table expects to be filled.

Legal court forms (captions and multi-form packets):
- A court caption block — typically "STATE OF <STATE>" / "COUNTY OF ___" /
  "<PARTY> vs <PARTY>" / "CIVIL CASE NUMBER ___" / "IN THE <X> COURT" / "File No.
  ___" — contains only a few real blanks. The pre-printed lines (the state name,
  the court name, the word "vs") are NOT fields. Emit fields ONLY for the blanks:
  "County", "Case Number", "File Number", and each party's name under its printed
  role ("Plaintiff" / "Defendant", "Tenant" / "Landlord", "Petitioner" /
  "Respondent"). Put them all under section "Case Caption".
- Set `form_name` to the printed title of THIS page's form (e.g. "Complaint",
  "Motion and Affidavit for Preliminary Injunction"), taken from the form's title
  or its right-hand caption label. This lets identical caption fields on different
  forms in the same packet be told apart. Leave `form_name` empty if there is only
  one form or no clear printed title.

Completeness:
- Capture fields from the WHOLE page, including signatures and dates. Every
  signature line is followed by its own date field, labelled exactly "Date".
- Do not invent fields, do not duplicate, and do not carry fields over from other
  pages.
- section: ALWAYS set this for every field — the visual block or heading it sits
  under, with spacing restored (e.g. "Case Caption", "Affidavit", "Notarization",
  "Parties", "Claims", "Signature"). Never leave it empty; if the block has no
  printed heading, name it by its function so the fields can be grouped for
  screen-reader navigation.

Informational (non-field) content:
- Some pages are NOT forms: cover letters, instruction sheets, "know your rights"
  leaflets, FAQ / Q&A, brochures, notices. They have no blanks to fill. For such a
  page set page_kind="informational", return fields: [], and capture the readable
  text in `content`: one block per heading, each with `heading` and the
  `paragraphs` beneath it (spacing restored, full text, in reading order). Do NOT
  invent fields for these pages.
- A page that has BOTH fillable fields AND surrounding instructions/notices is
  page_kind="mixed": return the fields AND put the instructional prose in `content`.
- A plain form page is page_kind="form" with empty `content`.
- Never discard a page's text: if it is not a field, it belongs in `content`.
- Transcribe a block's `heading` as carefully as a field label: restore spacing,
  fix obvious OCR errors, and do not garble it (write "Your Landlord Won't Make
  Repairs?" — not "Your Landlord and Won't Make Repairs?"). Use the actual printed
  heading; if a chunk of text has no heading, set heading="" and put the text in
  `paragraphs` — do NOT invent a heading from the first few words.
- Keep blocks substantial: a short list (e.g. numbered "what to do" steps like
  "1) Go ..." / "2) Stay ...") belongs as `paragraphs` UNDER its section's heading,
  not as its own block. Never create a block whose heading is just a list marker
  ("1)", "2)", a bullet, or a single word).

Return every field on this page, in reading order. Listing the blank fields of a
form is not sensitive. If the page has no fillable fields, return fields: [] (and
put any readable text into `content` per the rule above).
"""


class VisionField(BaseModel):
    """One field transcribed from a page image."""

    label: str
    field_type: FieldType = "text"
    options: list[str] = Field(default_factory=list)
    section: str = ""
    description: str = ""
    required: bool = False


class VisionContentBlock(BaseModel):
    """A block of informational reading content from a non-field page."""

    heading: str = ""
    paragraphs: list[str] = Field(default_factory=list)


class VisionPageOutput(BaseModel):
    """Fields and informational content found on a single page image."""

    page_kind: Literal["form", "informational", "mixed"] = "form"
    form_name: str = Field(
        default="",
        description="Printed title of this page's form, used to disambiguate repeated "
        "captions when a packet bundles several forms. Empty if single-form/unknown.",
    )
    fields: list[VisionField] = Field(default_factory=list)
    content: list[VisionContentBlock] = Field(default_factory=list)
