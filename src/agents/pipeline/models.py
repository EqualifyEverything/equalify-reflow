"""Data models for pipeline-based document processing.

These models carry structured evidence through the pipeline,
preserving what Docling detected vs what we transformed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ElementType(str, Enum):
    """Document element types from Docling."""

    SECTION_HEADER = "section_header"
    TEXT = "text"
    LIST_ITEM = "list_item"
    PICTURE = "picture"
    TABLE = "table"
    FOOTNOTE = "footnote"
    CAPTION = "caption"
    FORMULA = "formula"
    CODE = "code"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    UNKNOWN = "unknown"


class TransformationType(str, Enum):
    """Types of LLM transformations applied."""

    HEADING_LEVEL_ADJUSTED = "heading_level_adjusted"
    ALT_TEXT_GENERATED = "alt_text_generated"
    TABLE_SEMANTICS_ADDED = "table_semantics_added"
    LIST_TYPE_CLASSIFIED = "list_type_classified"
    SPELLING_CORRECTED = "spelling_corrected"
    FORMULA_TRANSCRIBED = "formula_transcribed"
    CODE_TRANSCRIBED = "code_transcribed"
    NONE = "none"


@dataclass
class BoundingBox:
    """Bounding box in document coordinates."""

    left: float
    top: float
    right: float
    bottom: float
    page: int

    @classmethod
    def from_docling(cls, bbox: Any, page_no: int) -> "BoundingBox":
        """Create from Docling BoundingBox object."""
        return cls(
            left=bbox.l,
            top=bbox.t,
            right=bbox.r,
            bottom=bbox.b,
            page=page_no,
        )


@dataclass
class ElementProvenance:
    """Tracks what Docling detected vs what we changed."""

    # What Docling gave us
    docling_label: str
    docling_confidence: float | None = None  # If Docling exposes this
    docling_bbox: BoundingBox | None = None
    docling_page: int = 1

    # What we changed
    transformation: TransformationType = TransformationType.NONE
    transformation_reason: str = ""
    llm_confidence: float | None = None


@dataclass
class ProcessedElement:
    """A document element with its processing history.

    This is the intermediate representation that carries:
    1. The raw extraction (what Docling detected)
    2. Confidence signals
    3. Modification tracking
    """

    # Identity
    element_type: ElementType
    hierarchy_level: int  # From Docling's iterate_items

    # Content
    original_text: str  # What Docling extracted
    processed_text: str  # After any transformations

    # For figures
    alt_text: str | None = None

    # For tables
    table_markdown: str | None = None
    has_header_row: bool | None = None

    # For headings
    original_heading_level: int | None = None
    validated_heading_level: int | None = None

    # For formulas/equations
    formula_latex: str | None = None
    formula_plain_text: str | None = None

    # For code blocks
    code_content: str | None = None
    code_language: str | None = None
    is_pseudocode: bool | None = None

    # Provenance
    provenance: ElementProvenance | None = None

    # Reference to original Docling element (for images, etc.)
    docling_ref: Any = None

    # Asset tracking (for figures/tables with S3 URLs)
    asset_id: str | None = None
    asset_s3_url: str | None = None

    def to_markdown_with_provenance(self) -> str:
        """Serialize to markdown with embedded provenance comments."""
        lines = []

        # Add provenance comment
        if self.provenance and self.provenance.transformation != TransformationType.NONE:
            lines.append(
                f"<!-- docling:{self.provenance.docling_label} "
                f"page={self.provenance.docling_page} -->"
            )
            lines.append(
                f"<!-- llm:{self.provenance.transformation.value} "
                f'reason="{self.provenance.transformation_reason}" '
                f"confidence={self.provenance.llm_confidence or 'N/A'} -->"
            )

        # Emit content based on type
        if self.element_type == ElementType.SECTION_HEADER:
            level = self.validated_heading_level or self.hierarchy_level
            prefix = "#" * min(level, 6)
            lines.append(f"{prefix} {self.processed_text}")

        elif self.element_type == ElementType.PICTURE:
            alt = self.alt_text or "TODO: describe image"
            # Use S3 URL if available, otherwise use placeholder
            if self.asset_s3_url:
                url = self.asset_s3_url
            else:
                page = self.provenance.docling_page if self.provenance else 1
                url = f"image-p{page}.png"
            lines.append(f"![{alt}]({url})")

        elif self.element_type == ElementType.TABLE:
            if self.table_markdown:
                lines.append(self.table_markdown)
            else:
                lines.append(self.processed_text)

        elif self.element_type == ElementType.LIST_ITEM:
            lines.append(f"- {self.processed_text}")

        elif self.element_type == ElementType.FORMULA:
            if self.formula_latex:
                # Use display math block for formulas
                lines.append(f"$$\n{self.formula_latex}\n$$")
                if self.formula_plain_text:
                    lines.append(f"<!-- accessible: {self.formula_plain_text} -->")
            else:
                lines.append(self.processed_text)

        elif self.element_type == ElementType.CODE:
            if self.code_content:
                lang = self.code_language or ""
                lines.append(f"```{lang}")
                lines.append(self.code_content)
                lines.append("```")
            else:
                lines.append(self.processed_text)

        else:
            lines.append(self.processed_text)

        return "\n".join(lines)


@dataclass
class ProcessingResult:
    """Result of processing a complete document."""

    elements: list[ProcessedElement] = field(default_factory=list)

    # Aggregate stats
    total_elements: int = 0
    figures_processed: int = 0
    tables_processed: int = 0
    headings_validated: int = 0
    formulas_transcribed: int = 0
    code_blocks_transcribed: int = 0

    # LLM usage
    llm_calls: int = 0
    llm_failures: int = 0  # Track failed LLM calls that used fallbacks
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Errors
    errors: list[str] = field(default_factory=list)

    # Footnote links (populated by footnote_linker)
    # Using Any to avoid circular import with schemas
    footnote_links: list[Any] = field(default_factory=list)

    def to_markdown(self, include_provenance: bool = True) -> str:
        """Serialize all elements to markdown."""
        parts = []
        for element in self.elements:
            if include_provenance:
                parts.append(element.to_markdown_with_provenance())
            else:
                # Simple serialization without comments
                parts.append(element.processed_text)
        return "\n\n".join(parts)

    @property
    def transformation_summary(self) -> dict[str, int]:
        """Count transformations by type."""
        from collections import Counter
        transformations = [
            e.provenance.transformation.value
            for e in self.elements
            if e.provenance and e.provenance.transformation != TransformationType.NONE
        ]
        return dict(Counter(transformations))

    def apply_asset_urls(self, assets: dict[str, Any]) -> None:
        """Apply asset URLs to elements for markdown generation.

        This updates the asset_id and asset_s3_url fields on elements
        that have corresponding assets (figures and tables).

        Args:
            assets: Dict mapping asset_id to ImageAsset objects
        """
        # Build lookup maps for figures and tables by page and index
        figure_counts: dict[int, int] = {}
        table_counts: dict[int, int] = {}

        for elem in self.elements:
            page = elem.provenance.docling_page if elem.provenance else 1

            if elem.element_type == ElementType.PICTURE:
                figure_counts[page] = figure_counts.get(page, 0) + 1
                idx = figure_counts[page]
                asset_id = f"figure-p{page}-{idx}"

                if asset_id in assets:
                    elem.asset_id = asset_id
                    elem.asset_s3_url = assets[asset_id].s3_url

            elif elem.element_type == ElementType.TABLE:
                table_counts[page] = table_counts.get(page, 0) + 1
                idx = table_counts[page]
                asset_id = f"table-p{page}-{idx}"

                if asset_id in assets:
                    elem.asset_id = asset_id
                    elem.asset_s3_url = assets[asset_id].s3_url
