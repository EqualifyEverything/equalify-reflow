"""Subagent result types for ParagraphAgent tools.

Each result type extends SubagentResult with task-specific fields.
All results include confidence and reasoning from the base class.
"""

from pydantic import BaseModel, Field

# =============================================================================
# Base Result Type
# =============================================================================


class SubagentResult(BaseModel):
    """Base class for all subagent results.

    All subagent tools return a result that includes:
    - confidence: How confident the subagent is in its recommendation (0.0-1.0)
    - reasoning: Explanation of what was found and why the recommendation was made

    Parent agents use confidence to decide:
    - >= CONFIDENCE_AUTO_APPLY: Apply via propose_edit(needs_review=False)
    - >= CONFIDENCE_APPLY_WITH_REVIEW: Apply via propose_edit(needs_review=True)
    - < CONFIDENCE_SKIP: Skip the edit, log for manual review
    """

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in recommendation (0.0-1.0)",
    )
    reasoning: str = Field(
        description="Explanation of the recommendation",
    )


# =============================================================================
# Specialized Result Types
# =============================================================================


class PageArtifactResult(SubagentResult):
    """Result from page artifact removal subagent.

    Handles:
    - Page break markers (---, ===, etc.)
    - Split words across lines (de-\\nprecate → deprecate)
    - Column break artifacts
    """

    cleaned_text: str = Field(description="Text with artifacts removed")
    artifacts_removed: list[str] = Field(
        default_factory=list,
        description="List of artifacts found and removed",
    )
    words_rejoined: list[str] = Field(
        default_factory=list,
        description="Words that were split and rejoined",
    )


class FootnoteResult(SubagentResult):
    """Result from footnote correction subagent.

    Handles:
    - Misplaced footnote definitions
    - Missing footnote links
    - Orphaned footnote markers
    """

    corrected_markdown: str = Field(description="Markdown with footnotes fixed")
    footnotes_fixed: list[dict] = Field(
        default_factory=list,
        description="List of {marker, action, definition}",
    )


class CitationResult(SubagentResult):
    """Result from citation linking subagent.

    Handles:
    - Unlinked citation markers ([1], [2], etc.)
    - Missing bibliography references
    - Author-year citation linking
    """

    corrected_markdown: str = Field(description="Markdown with citations linked")
    citations_linked: list[dict] = Field(
        default_factory=list,
        description="List of {marker, linked_to}",
    )
    bibliography_found: bool = Field(default=False)


class ListResult(SubagentResult):
    """Result from list semantics subagent.

    Handles:
    - Incorrect list nesting
    - Numbering errors (1, 2, 4, 5...)
    - Mixed list type issues
    """

    corrected_markdown: str = Field(description="Markdown with list structure fixed")
    issues_fixed: list[str] = Field(
        default_factory=list,
        description="List of fixes applied",
    )


class TypographyResult(SubagentResult):
    """Result from typography semantics subagent.

    Handles:
    - Bold text that should be marked **bold**
    - Italic text that should be marked *italic*
    - Code/monospace that should be marked `code`
    """

    corrected_markdown: str = Field(description="Markdown with formatting added")
    formatting_added: list[dict] = Field(
        default_factory=list,
        description="List of {text, type, purpose}",
    )


class ParagraphMergeResult(SubagentResult):
    """Result from cross-page paragraph merge subagent.

    Handles paragraphs split across page boundaries:
    - Page 1 ends: "The algorithm uses a greedy ap-"
    - Page 2 starts: "proach to optimize..."
    - Merged: "The algorithm uses a greedy approach to optimize..."
    """

    should_merge: bool = Field(description="Whether pages should be merged")
    merged_text: str = Field(default="", description="The joined text if merging")
    join_method: str = Field(
        default="space",
        description="How to join: space, hyphen_removal, direct",
    )
    page1_remove_chars: int = Field(
        default=0,
        description="Characters to remove from end of page 1",
    )
    page2_remove_chars: int = Field(
        default=0,
        description="Characters to remove from start of page 2",
    )


# =============================================================================
# Consolidated Text Structure Types
# =============================================================================


class TextStructureCorrection(BaseModel):
    """A single correction from the TextStructure subagent.

    Represents one text fix with its type and confidence.
    """

    task_type: str = Field(
        description="Type of correction: page_artifact, footnote, citation, or list"
    )
    before: str = Field(description="Text to find and replace")
    after: str = Field(description="Corrected text")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this correction"
    )
    reasoning: str = Field(description="Why this correction was made")


class TextStructureResult(SubagentResult):
    """Result from consolidated text structure subagent.

    Handles all text corrections in one LLM call:
    - Page artifacts (---, split words, orphaned page numbers)
    - Footnotes (markers, definitions, linking)
    - Citations ([1], (Smith, 2023))
    - Lists (indentation, numbering, bullets)
    """

    corrections: list[TextStructureCorrection] = Field(
        default_factory=list,
        description="List of corrections to apply",
    )
    # Summary counts for logging/metrics
    artifacts_removed: int = Field(default=0, description="Count of page artifacts removed")
    footnotes_fixed: int = Field(default=0, description="Count of footnotes corrected")
    citations_linked: int = Field(default=0, description="Count of citations linked")
    lists_fixed: int = Field(default=0, description="Count of list issues fixed")


__all__ = [
    "SubagentResult",
    # Legacy result types
    "PageArtifactResult",
    "FootnoteResult",
    "CitationResult",
    "ListResult",
    "TypographyResult",
    "ParagraphMergeResult",
    # Consolidated text structure types
    "TextStructureCorrection",
    "TextStructureResult",
]
