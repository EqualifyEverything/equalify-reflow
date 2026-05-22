"""Pydantic response models for the Pipeline Viewer dev tool."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Phase 1 — Structure Analysis models
# ---------------------------------------------------------------------------


class LayoutType(str, Enum):
    """Page layout classification based on text flow."""

    SINGLE_COLUMN = "single_column"
    DOUBLE_COLUMN = "double_column"
    PRESENTATION = "presentation"
    POSTER = "poster"


class PageAttributes(BaseModel):
    """Compositional page characteristics detected during structure analysis.

    Each attribute maps to a procedure fragment that provides targeted
    correction guidance for Phase 2 agents.
    """

    layout: LayoutType
    is_academic: bool = False
    has_images: bool = False
    has_tables: bool = False
    has_lists: bool = False
    has_equations: bool = False
    has_forms: bool = False
    is_scanned: bool = False


class HeadingRecommendation(BaseModel):
    """A heading identified on a page with its recommended level."""

    text: str
    """Heading text as read from the page image (ground truth)."""

    recommended_level: int
    """Correct heading level (1-6) based on document outline context."""

    reasoning: str
    """Why this level was chosen, referencing the outline."""


class FootnoteInfo(BaseModel):
    """A footnote found on a page."""

    number: str
    """Footnote marker (e.g. "1", "2", "*")."""

    body_text: str
    """The footnote content text."""

    source_page: int
    """Page where this footnote appears."""


class OutlineEntry(BaseModel):
    """A single entry in the accumulated document outline."""

    level: int
    """Heading level (1-6)."""

    text: str
    """Heading text."""

    page: int
    """Page number where this heading appears."""

    reasoning: str = ""
    """Why this level was chosen (from per-page analysis)."""


class CodeBlockInfo(BaseModel):
    """A code block found on a page."""

    language: str
    """Detected programming language (e.g. "python", "java", "sql")."""

    first_line: str
    """First line of the code block as visible in the page image."""

    last_line: str
    """Last line of the code block as visible in the page image."""

    page: int
    """Page where this code block appears."""

    reasoning: str
    """How the language was identified (syntax, surrounding context, etc.)."""


class StructurePageOutput(BaseModel):
    """What the Phase 1 agent returns for a single page.

    The agent analyzes the page image + markdown and reports structural
    findings. It does NOT modify the markdown — that happens in Phase 2.
    """

    page_attributes: PageAttributes
    """Compositional page characteristics detected from the page image."""

    headings: list[HeadingRecommendation] = Field(default_factory=list)
    """Headings found on this page with recommended levels."""

    footnotes: list[FootnoteInfo] = Field(default_factory=list)
    """Footnotes found on this page (marker + body text)."""

    code_blocks: list[CodeBlockInfo] = Field(default_factory=list)
    """Code blocks found on this page with detected language."""


class StructureResult(BaseModel):
    """Complete Phase 1 output, accumulated across all pages.

    Built by the orchestrator from individual StructurePageOutput results.
    Consumed by Phase 2 (per-page agents) and Phase 3 (cross-page).
    """

    page_attributes: dict[int, PageAttributes] = Field(default_factory=dict)
    """Page number -> compositional attributes detected for that page."""

    outline: list[OutlineEntry] = Field(default_factory=list)
    """Full document outline in page order."""

    footnotes: list[FootnoteInfo] = Field(default_factory=list)
    """All footnotes found across all pages."""

    code_blocks: list[CodeBlockInfo] = Field(default_factory=list)
    """All code blocks found across all pages."""


# ---------------------------------------------------------------------------
# Phase 1c — Heading Reconciliation models
# ---------------------------------------------------------------------------


class HeadingReconciliationOutput(BaseModel):
    """Output from heading reconciliation — corrected outline."""

    outline: list[OutlineEntry]
    """Globally reconciled outline with corrected heading levels."""

    reasoning: str
    """Global rationale for hierarchy decisions."""


# ---------------------------------------------------------------------------
# Phase 1d — Section Map models
# ---------------------------------------------------------------------------


class SectionEntry(BaseModel):
    """A single section in the document."""

    index: int
    """0-based section index (0 = preamble)."""

    heading_text: str
    """Heading text ('(Preamble)' for index 0)."""

    heading_level: int
    """1-6 (0 for preamble)."""

    pages: list[int]
    """Page numbers this section spans."""

    markdown: str
    """Section markdown (heading through next heading)."""

    outline_position: int
    """Index in the full outline (-1 for preamble)."""


class SectionMap(BaseModel):
    """Maps the document outline to concrete page ranges and markdown slices."""

    sections: list[SectionEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 2 — Content Correction models
# ---------------------------------------------------------------------------


class ImageDescriptionResult(BaseModel):
    """Result from the image description subagent."""

    image_category: Literal["decorative", "informative", "complex_informative"]
    """Classification of the image's accessibility role."""

    alt_text: str
    """WCAG-compliant alt text (empty string for decorative images)."""

    is_decorative: bool = False
    """True if the image is purely decorative (borders, spacers, logos)."""

    confidence: str = "high"
    """Confidence level: high, medium, or low."""

    reasoning: str = ""
    """Why this alt text was chosen."""


class TableReconstructionResult(BaseModel):
    """Result from the table reconstruction subagent."""

    table_format: Literal["markdown", "html"]
    """Whether the output is markdown or HTML."""

    reconstructed_content: str
    """The corrected table content (markdown or HTML)."""

    caption: str
    """WCAG table caption describing the table's purpose."""

    has_row_headers: bool = False
    """True if the table has row headers (leftmost column)."""

    has_merged_cells: bool = False
    """True if the table has merged cells (colspan/rowspan)."""

    confidence: str = "high"
    """Confidence level: high, medium, or low."""

    reasoning: str = ""
    """Why this reconstruction was chosen."""


class ListReconstructionResult(BaseModel):
    """Result from the list reconstruction subagent."""

    list_type: Literal["unordered", "ordered", "definition"]
    """Classification of the list type."""

    reconstructed_content: str
    """The corrected list content (markdown or HTML for definition lists)."""

    confidence: str = "high"
    """Confidence level: high, medium, or low."""

    reasoning: str = ""
    """Why this reconstruction was chosen."""


class FormFieldType(str, Enum):
    """Type of form control to render for a detected field."""

    TEXT = "text"
    """Single-line free-text input (name, email, short answer)."""

    TEXTAREA = "textarea"
    """Multi-line free-text input (comments, essay box)."""

    CHECKBOX = "checkbox"
    """A single standalone checkbox (e.g. "I attest …")."""

    CHECKBOX_GROUP = "checkbox_group"
    """A set of checkboxes where more than one may be ticked."""

    RADIO_GROUP = "radio_group"
    """A set of mutually-exclusive options (exactly one selected)."""

    SELECT = "select"
    """A dropdown / choice list."""

    DATE = "date"
    """A date entry field."""

    SIGNATURE = "signature"
    """A signature line."""


class FormFieldOption(BaseModel):
    """One selectable option within a radio, checkbox, or select group."""

    label: str
    """Visible option text as read from the page image."""

    checked: bool = False
    """True if the option appears pre-selected / ticked in the image."""


class FormFieldInfo(BaseModel):
    """A form field detected on a page from the page image.

    The agent reports what it sees; the deterministic injector turns this
    into accessible HTML and splices it into the page markdown by replacing
    ``anchor_text``.
    """

    field_type: FormFieldType
    """The kind of control this field represents."""

    label: str
    """Accessible label for the field, read from the IMAGE (e.g. "Full name").
    For grouped fields this is the group prompt/legend (e.g. "Marital status")."""

    anchor_text: str
    """The exact existing markdown text representing this field (e.g.
    "Name: ____________" or "☐ I attest …"). The injector replaces this text
    with the rendered accessible HTML. Copy it verbatim from the markdown."""

    options: list[FormFieldOption] = Field(default_factory=list)
    """Options for radio_group / checkbox_group / select fields. Empty otherwise."""

    required: bool = False
    """True if the field is marked required (asterisk, "required", etc.)."""

    page: int = 0
    """Page where this field appears (1-indexed). Set by the orchestrator."""

    reasoning: str = ""
    """How the field type and label were determined."""


class FormFieldsPageOutput(BaseModel):
    """What the form-fields agent returns for a single page."""

    form_fields: list[FormFieldInfo] = Field(default_factory=list)
    """Form fields found on this page. Empty list if the page has no form."""


class SectionCorrectionResult(BaseModel):
    """What a section correction agent returns."""

    section_index: int
    heading_text: str
    corrected_markdown: str
    changes: list[DocumentChange] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    describer_input_tokens: int = 0
    describer_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Phase 2 (legacy) — Page Content Correction models
# ---------------------------------------------------------------------------


class PageCorrectionResult(BaseModel):
    """What a Phase 2 page agent returns."""

    page: int
    """Page number that was corrected."""

    corrected_markdown: str
    """The page markdown after all corrections applied."""

    changes: list[DocumentChange] = Field(default_factory=list)
    """Log of every str_replace applied."""

    issues: list[str] = Field(default_factory=list)
    """Uncertainties or failed edits flagged for human review."""

    input_tokens: int = 0
    """LLM input tokens consumed for this page."""

    output_tokens: int = 0
    """LLM output tokens generated for this page."""

    describer_input_tokens: int = 0
    """Input tokens consumed by image describer subagent calls."""

    describer_output_tokens: int = 0
    """Output tokens from image describer subagent calls."""

    table_reconstructor_input_tokens: int = 0
    """Input tokens consumed by table reconstructor subagent calls."""

    table_reconstructor_output_tokens: int = 0
    """Output tokens from table reconstructor subagent calls."""

    list_reconstructor_input_tokens: int = 0
    """Input tokens consumed by list reconstructor subagent calls."""

    list_reconstructor_output_tokens: int = 0
    """Output tokens from list reconstructor subagent calls."""


# ---------------------------------------------------------------------------
# Phase 3 — Cross-page models
# ---------------------------------------------------------------------------


class BoundaryContext(BaseModel):
    """Context for a boundary fix between two adjacent pages."""

    page_before: int
    page_after: int
    tail_text: str
    """Last ~10 lines of the earlier page."""

    head_text: str
    """First ~10 lines of the later page."""


# ---------------------------------------------------------------------------
# Viewer response models (used by API)
# ---------------------------------------------------------------------------


class DocumentChange(BaseModel):
    """A single change made by a pipeline step."""

    page: int
    old_text: str
    new_text: str
    reasoning: str
    stage: str


class FigureData(BaseModel):
    """An extracted figure/picture from the document."""

    ref_id: str
    caption: str
    page_number: int
    image_base64: str


class TableData(BaseModel):
    """A markdown table extracted from a page."""

    ref_id: str
    """Table reference ID (e.g. "table-1")."""

    page_number: int
    """Page where this table appears."""

    markdown_content: str
    """The extracted markdown table text."""


class StepResult(BaseModel):
    """Result of a single pipeline step."""

    name: str
    display_name: str
    version_before: str | None = None
    version_after: str
    elapsed_ms: int
    changes: list[DocumentChange] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    skipped: bool = False
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0


class PipelineViewerResult(BaseModel):
    """Top-level response from the pipeline viewer."""

    filename: str
    total_pages: int
    versions: dict[str, str] = Field(default_factory=dict)
    page_images: dict[str, str] = Field(default_factory=dict)
    page_markdowns: dict[str, dict[str, str]] = Field(default_factory=dict)
    figures: list[FigureData] = Field(default_factory=list)
    steps: list[StepResult] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    """Human-readable warnings from PDF classification."""


# Resolve forward references
PageCorrectionResult.model_rebuild()
SectionCorrectionResult.model_rebuild()
