"""Pydantic response models for the Pipeline Viewer dev tool."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Phase 1 — Structure Analysis models
# ---------------------------------------------------------------------------


class LayoutType(str, Enum):
    """Page layout classification based on text flow."""

    SINGLE_COLUMN = "single_column"
    DOUBLE_COLUMN = "double_column"
    PRESENTATION = "presentation"


class PageAttributes(BaseModel):
    """Compositional page characteristics detected during structure analysis.

    Each attribute maps to a procedure fragment that provides targeted
    correction guidance for Phase 2 agents.
    """

    layout: LayoutType
    is_academic: bool = False
    has_images: bool = False
    has_tables: bool = False
    has_equations: bool = False
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
# Phase 2 — Page Content Correction models
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
# Revision / Feedback Decomposition models
# ---------------------------------------------------------------------------


class RevisionTaskCategory(str, Enum):
    """Category of a revision task."""

    CONTENT = "content"
    FORMATTING = "formatting"
    ACCESSIBILITY = "accessibility"
    STRUCTURE = "structure"


class RevisionTask(BaseModel):
    """A single discrete revision task decomposed from freeform feedback."""

    id: int
    """Sequential task identifier."""

    description: str
    """What needs to be changed."""

    affected_pages: list[int]
    """Page numbers this task applies to."""

    needs_image: bool = False
    """Whether the revision agent needs the page image for this task."""

    category: RevisionTaskCategory = RevisionTaskCategory.CONTENT
    """Task category for grouping and prioritization."""


class TaskDecompositionResult(BaseModel):
    """Result of decomposing freeform feedback into discrete revision tasks."""

    tasks: list[RevisionTask]
    """Ordered list of revision tasks."""

    reasoning: str
    """Explanation of how the feedback was decomposed."""


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


# Resolve forward reference
PageCorrectionResult.model_rebuild()
