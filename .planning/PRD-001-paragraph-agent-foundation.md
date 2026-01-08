# PRD-001: ParagraphAgent Foundation

## Overview

| Field | Value |
|-------|-------|
| **PRD Number** | 001 |
| **Title** | ParagraphAgent Foundation - Models, Types, and Base Infrastructure |
| **Effort** | 1-2 days |
| **Priority** | High |
| **Dependencies** | v5-naming-cleanup-plan.md (should be complete) |
| **Blocks** | PRD-002, PRD-003, PRD-004, PRD-005 |

---

## Problem Statement

The current pipeline handles figures, tables, and headings well, but lacks LLM-controlled handling for:
- Page break artifacts (`---`, split words like `de-\nprecate`)
- Footnote placement and linking
- Citation references to bibliography
- List structure (nesting, numbering)
- Typography semantics (bold/italic meaning)
- Cross-page paragraph continuity

We need to extend the data models and create base infrastructure for a new **ParagraphAgent** that uses the **subagent tools pattern** - where specialized subagents return recommendations with confidence scores, and the parent agent decides whether to apply edits.

---

## Success Criteria

1. New `JobType.PARAGRAPH` routes paragraph tasks separately from existing Worker
2. New `TaskType` values cover all paragraph-related operations
3. Issue detection models capture paragraph problems during planning
4. `LedgerEntry` supports `needs_review` flag for low-confidence edits
5. Base subagent result types defined with confidence scoring
6. All existing tests continue to pass

---

## Technical Requirements

### 1. Update `src/agents/models.py`

#### 1.1 New JobType

```python
class JobType(str, Enum):
    """Job categories mapped to domain agents."""
    
    STRUCTURE = "structure"   # Heading fixes → Worker
    CONTENT = "content"       # Figures, tables → Worker  
    PARAGRAPH = "paragraph"   # Text flow → ParagraphAgent (NEW)
```

#### 1.2 New TaskTypes

```python
class TaskType(str, Enum):
    """Task types that agents perform."""
    
    # Existing (unchanged)
    ALT_TEXT = "alt_text"
    TABLE_TRANSCRIPTION = "table_transcription"
    HEADING_FIX = "heading_fix"
    OCR_FIX = "ocr_fix"
    FORMAT_FIX = "format_fix"
    SPELLING_FIX = "spelling_fix"
    # REMOVED: PAGELESS_OPTIMIZATION - replaced by ParagraphAgent tasks
    
    # NEW: Paragraph tasks (replaces PAGELESS_OPTIMIZATION)
    PAGE_ARTIFACT_REMOVAL = "page_artifact_removal"
    FOOTNOTE_CORRECTION = "footnote_correction"
    CITATION_LINKING = "citation_linking"
    LIST_FIX = "list_fix"
    TYPOGRAPHY_FIX = "typography_fix"
    PARAGRAPH_MERGE = "paragraph_merge"
```

**Note:** `PAGELESS_OPTIMIZATION` is removed because:
1. It was dead code (never called in pipeline)
2. Its functionality is now covered by `PAGE_ARTIFACT_REMOVAL` + `PARAGRAPH_MERGE`
3. The new approach is better: per-page, vision-based, confidence-scored

#### 1.3 Issue Detection Models (for PageChainAgent)

```python
class PageArtifactIssue(BaseModel):
    """A page break artifact detected during planning."""
    
    text: str = Field(description="The artifact text (---, split word)")
    artifact_type: str = Field(description="page_break, split_word, column_break")
    line_number: int = Field(description="Line number in page markdown")


class FootnoteIssue(BaseModel):
    """A footnote problem detected during planning."""
    
    marker: str = Field(description="The footnote marker ([^1], ¹, etc.)")
    issue_type: str = Field(description="missing_definition, misplaced, orphaned")
    line_number: int = Field(default=0)


class CitationIssue(BaseModel):
    """A citation linking problem detected during planning."""
    
    marker: str = Field(description="The citation marker ([1], (Author, 2023))")
    issue_type: str = Field(description="unlinked, missing_reference")
    line_number: int = Field(default=0)


class ListIssue(BaseModel):
    """A list structure problem detected during planning."""
    
    location: str = Field(description="Line range or identifier")
    issue_type: str = Field(description="nesting, numbering, mixed_types")
    description: str = Field(default="")


class TypographyIssue(BaseModel):
    """Semantic typography needing markup."""
    
    text: str = Field(description="The text that should have formatting")
    formatting_type: str = Field(description="bold, italic, monospace")
    semantic_purpose: str = Field(description="emphasis, definition, code, warning")
    line_number: int = Field(default=0)
```

#### 1.4 Enhanced LedgerEntry

```python
class LedgerEntry(BaseModel):
    """Immutable record of a change made by an agent."""
    
    # Existing fields
    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    page: int
    action: TaskType
    target: str
    before: str
    after: str
    reasoning: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    validated: bool = Field(default=False)
    timestamp: datetime = Field(default_factory=datetime.now)
    
    # NEW: Review flag for low-confidence edits
    needs_review: bool = Field(
        default=False,
        description="True if edit was applied with low confidence and needs human review"
    )
```

### 2. Create `src/agents/subagents/__init__.py`

Base types for all subagent results:

```python
"""Subagent tools for domain agents.

Each subagent is a specialized LLM that returns structured recommendations
with confidence scores. Parent agents review recommendations and decide
whether to apply edits via propose_edit().
"""

from pydantic import BaseModel, Field


class SubagentResult(BaseModel):
    """Base class for all subagent results."""
    
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in recommendation (0.0-1.0)"
    )
    reasoning: str = Field(
        description="Explanation of the recommendation"
    )


# Confidence thresholds
CONFIDENCE_AUTO_APPLY = 0.8    # Apply automatically
CONFIDENCE_APPLY_WITH_REVIEW = 0.5  # Apply but flag for review
CONFIDENCE_SKIP = 0.5         # Below this, skip the edit
```

### 3. Create Subagent Result Types

Create `src/agents/subagents/types.py`:

```python
"""Subagent result types for ParagraphAgent tools."""

from pydantic import BaseModel, Field
from . import SubagentResult


class PageArtifactResult(SubagentResult):
    """Result from page artifact removal subagent."""
    
    cleaned_text: str = Field(description="Text with artifacts removed")
    artifacts_removed: list[str] = Field(
        default_factory=list,
        description="List of artifacts found and removed"
    )
    words_rejoined: list[str] = Field(
        default_factory=list,
        description="Words that were split and rejoined"
    )


class FootnoteResult(SubagentResult):
    """Result from footnote correction subagent."""
    
    corrected_markdown: str = Field(description="Markdown with footnotes fixed")
    footnotes_fixed: list[dict] = Field(
        default_factory=list,
        description="List of {marker, action, definition}"
    )


class CitationResult(SubagentResult):
    """Result from citation linking subagent."""
    
    corrected_markdown: str = Field(description="Markdown with citations linked")
    citations_linked: list[dict] = Field(
        default_factory=list,
        description="List of {marker, linked_to}"
    )
    bibliography_found: bool = Field(default=False)


class ListResult(SubagentResult):
    """Result from list semantics subagent."""
    
    corrected_markdown: str = Field(description="Markdown with list structure fixed")
    issues_fixed: list[str] = Field(
        default_factory=list,
        description="List of fixes applied"
    )


class TypographyResult(SubagentResult):
    """Result from typography semantics subagent."""
    
    corrected_markdown: str = Field(description="Markdown with formatting added")
    formatting_added: list[dict] = Field(
        default_factory=list,
        description="List of {text, type, purpose}"
    )


class ParagraphMergeResult(SubagentResult):
    """Result from cross-page paragraph merge subagent."""
    
    should_merge: bool = Field(description="Whether pages should be merged")
    merged_text: str = Field(default="", description="The joined text if merging")
    join_method: str = Field(
        default="space",
        description="How to join: space, hyphen_removal, direct"
    )
    page1_remove_chars: int = Field(
        default=0,
        description="Characters to remove from end of page 1"
    )
    page2_remove_chars: int = Field(
        default=0,
        description="Characters to remove from start of page 2"
    )
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATA MODELS                                  │
├─────────────────────────────────────────────────────────────────────┤
│  models.py                                                           │
│  ├── JobType.PARAGRAPH (NEW)                                        │
│  ├── TaskType.PAGE_ARTIFACT_REMOVAL (NEW)                           │
│  ├── TaskType.FOOTNOTE_CORRECTION (NEW)                             │
│  ├── TaskType.CITATION_LINKING (NEW)                                │
│  ├── TaskType.LIST_FIX (NEW)                                        │
│  ├── TaskType.TYPOGRAPHY_FIX (NEW)                                  │
│  ├── TaskType.PARAGRAPH_MERGE (NEW)                                 │
│  ├── PageArtifactIssue (NEW)                                        │
│  ├── FootnoteIssue (NEW)                                            │
│  ├── CitationIssue (NEW)                                            │
│  ├── ListIssue (NEW)                                                │
│  ├── TypographyIssue (NEW)                                          │
│  └── LedgerEntry.needs_review (NEW field)                           │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      SUBAGENT BASE TYPES                             │
├─────────────────────────────────────────────────────────────────────┤
│  subagents/__init__.py                                               │
│  ├── SubagentResult (base class)                                    │
│  ├── CONFIDENCE_AUTO_APPLY = 0.8                                    │
│  ├── CONFIDENCE_APPLY_WITH_REVIEW = 0.5                             │
│  └── CONFIDENCE_SKIP = 0.5                                          │
│                                                                       │
│  subagents/types.py                                                  │
│  ├── PageArtifactResult                                             │
│  ├── FootnoteResult                                                 │
│  ├── CitationResult                                                 │
│  ├── ListResult                                                     │
│  ├── TypographyResult                                               │
│  └── ParagraphMergeResult                                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Deliverables

| File | Action | Description |
|------|--------|-------------|
| `src/agents/models.py` | Modify | Add JobType.PARAGRAPH, new TaskTypes, issue models, LedgerEntry.needs_review |
| `src/agents/subagents/__init__.py` | Create | SubagentResult base class, confidence constants |
| `src/agents/subagents/types.py` | Create | All subagent result types |
| `tests/unit/agents/test_paragraph_models.py` | Create | Tests for new models |

---

## Acceptance Criteria

- [ ] `JobType.PARAGRAPH` exists and is distinct from STRUCTURE/CONTENT
- [ ] All 6 new TaskTypes are defined
- [ ] All 5 issue detection models are defined
- [ ] `LedgerEntry.needs_review` field exists with default=False
- [ ] `SubagentResult` base class has confidence and reasoning fields
- [ ] All 6 subagent result types inherit from SubagentResult
- [ ] Existing tests pass (no regressions)
- [ ] New model tests pass
- [ ] Type checking passes (`make typecheck` or `mypy`)

---

## Definition of Done

1. All deliverables created
2. All acceptance criteria met
3. Code follows existing patterns in `models.py`
4. Docstrings on all new classes
5. No linting errors
6. Tests pass: `make test-fast`

---

## Implementation Notes

### Confidence Thresholds

The thresholds are guidance for ParagraphAgent:
- `>= 0.8`: Auto-apply via `propose_edit()`
- `0.5 - 0.8`: Apply but set `needs_review=True`
- `< 0.5`: Skip the edit, log for manual review

### Backward Compatibility

- Existing `JobType.STRUCTURE` and `JobType.CONTENT` unchanged
- Existing `TaskType` values unchanged
- `LedgerEntry.needs_review` defaults to False (existing entries unaffected)

### Import Structure

```python
# In other files:
from src.agents.models import JobType, TaskType, PageArtifactIssue, ...
from src.agents.subagents import SubagentResult, CONFIDENCE_AUTO_APPLY
from src.agents.subagents.types import PageArtifactResult, FootnoteResult, ...
```
