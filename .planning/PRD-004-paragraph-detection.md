# PRD-004: Paragraph Issue Detection

## Overview

| Field | Value |
|-------|-------|
| **PRD Number** | 004 |
| **Title** | Paragraph Issue Detection - Extending PageChainAgent |
| **Effort** | 1-2 days |
| **Priority** | High |
| **Dependencies** | PRD-001 (Foundation) |
| **Blocks** | PRD-005 (Integration) |

---

## Problem Statement

For ParagraphAgent to have work to do, the planning phase must detect paragraph issues. Currently, `PageChainAgent` detects:
- Headings
- Figures
- Tables
- OCR errors

We need to extend it to also detect:
- Page artifacts (`---`, split words)
- Footnote issues
- Citation issues
- List structure issues
- Typography issues
- Cross-page paragraph continuity

---

## Success Criteria

1. `PageAnalysisOutput` includes all paragraph issue types
2. `PAGE_ANALYSIS_SYSTEM_PROMPT` instructs LLM to detect paragraph issues
3. Planning creates `PARAGRAPH` jobs with detected issues
4. Detection doesn't significantly increase planning cost
5. Existing detection (headings, figures, tables) unchanged

---

## Technical Requirements

### 1. Extend PageAnalysisOutput

**File:** `src/agents/page_chain.py`

Add to `PageAnalysisOutput`:

```python
class PageAnalysisOutput(BaseModel):
    """LLM output for analyzing a single page."""
    
    # === EXISTING (unchanged) ===
    document_title: str | None = Field(default=None)
    document_type: str | None = Field(default=None)
    headings: list[HeadingAnalysis] = Field(default_factory=list)
    summary: str = Field(description="2-3 sentence summary")
    terms: list[str] = Field(default_factory=list)
    figures: list[FigureAnalysis] = Field(default_factory=list)
    tables: list[TableAnalysis] = Field(default_factory=list)
    
    # === NEW: Paragraph issues ===
    page_artifacts: list[PageArtifactIssue] = Field(
        default_factory=list,
        description="Page break artifacts found (---, split words)"
    )
    footnote_issues: list[FootnoteIssue] = Field(
        default_factory=list,
        description="Footnote problems (missing definitions, misplaced)"
    )
    citation_issues: list[CitationIssue] = Field(
        default_factory=list,
        description="Citation linking problems"
    )
    list_issues: list[ListIssue] = Field(
        default_factory=list,
        description="List structure problems"
    )
    typography_issues: list[TypographyIssue] = Field(
        default_factory=list,
        description="Semantic formatting needing markup"
    )
    has_page_continuation: bool = Field(
        default=False,
        description="True if page ends mid-sentence (for cross-page merge)"
    )
```

### 2. Extend System Prompt

Add to `PAGE_ANALYSIS_SYSTEM_PROMPT`:

```python
PAGE_ANALYSIS_SYSTEM_PROMPT = """You are a document structure analyst processing one page at a time.

Your job is to analyze the current page and:
1. Identify all headings and determine their CORRECT level
2. Summarize what the page covers
3. Extract domain-specific terms for spell-checking
4. Describe any figures and tables
5. **Detect paragraph-level issues** (NEW)

## Heading Level Rules
[existing content unchanged]

## Context from Previous Pages
[existing content unchanged]

## Output Requirements

[existing 1-5 unchanged]

6. **Page Artifacts**: Look for extraction artifacts:
   - `---`, `~~~`, `***` page break markers
   - Words split across lines with hyphens: `infor-` (end of line) `mation` (next line)
   - These are usually extraction errors, not intentional content

7. **Footnotes**: Look for footnote markers and issues:
   - Markers: `[^1]`, `¹`, `(1)`, `*`
   - Issues: marker without definition, misplaced definition, orphaned text

8. **Citations**: Look for citation patterns:
   - Numbered: `[1]`, `[2]`, `[1-3]`
   - Author-date: `(Smith, 2023)`, `(Smith & Jones, 2023)`
   - Note if citations appear unlinked to references

9. **List Structure**: Check lists for issues:
   - Inconsistent indentation (should be 2 spaces per level)
   - Mixed ordered/unordered at same nesting level
   - Broken numbering sequences (1, 2, 5 instead of 1, 2, 3)

10. **Typography**: Identify SEMANTIC formatting visible in the image but missing in markdown:
    - **Bold** for key terms, warnings, definitions (not just styling)
    - *Italic* for emphasis, foreign words, titles
    - `Code` for commands, technical terms
    - Only flag if the formatting conveys MEANING

11. **Page Continuity**: Set `has_page_continuation=True` if:
    - The page ends mid-sentence (no terminal punctuation)
    - A word appears to be split at the page boundary
    - This helps identify paragraphs that need merging across pages
"""
```

### 3. Import Issue Types

At the top of `page_chain.py`:

```python
from .models import (
    # Existing imports
    DocumentType,
    FigureContext,
    HeadingFix,
    OutlineEntry,
    TableContext,
    # NEW imports
    PageArtifactIssue,
    FootnoteIssue,
    CitationIssue,
    ListIssue,
    TypographyIssue,
)
```

### 4. Update PagePlan

**File:** `src/agents/planner.py`

Update `PagePlan` to include paragraph issues:

```python
class PagePlan(BaseModel):
    """Plan for processing a single page."""
    
    page_num: int
    summary: str
    section_context: str
    keywords: list[str] = Field(default_factory=list)
    
    # Existing
    figures: list[FigureContext] = Field(default_factory=list)
    tables: list[TableContext] = Field(default_factory=list)
    ocr_errors: list[str] = Field(default_factory=list)
    formatting_issues: list[str] = Field(default_factory=list)
    
    # NEW: Paragraph issues
    page_artifacts: list[PageArtifactIssue] = Field(default_factory=list)
    footnote_issues: list[FootnoteIssue] = Field(default_factory=list)
    citation_issues: list[CitationIssue] = Field(default_factory=list)
    list_issues: list[ListIssue] = Field(default_factory=list)
    typography_issues: list[TypographyIssue] = Field(default_factory=list)
    has_page_continuation: bool = Field(default=False)
```

### 5. Pass Issues Through Planning Stages

In `stage3_page_summaries()`, propagate issues to `PagePlan`:

```python
# In the loop that creates PagePlan objects:
page_plans[summary.page_num] = PagePlan(
    page_num=summary.page_num,
    summary=summary.summary,
    section_context=summary.section_context,
    keywords=summary.keywords,
    figures=summary.figures,
    tables=summary.tables,
    ocr_errors=summary.ocr_errors,
    formatting_issues=summary.formatting_issues,
    # NEW: Paragraph issues
    page_artifacts=summary.page_artifacts,
    footnote_issues=summary.footnote_issues,
    citation_issues=summary.citation_issues,
    list_issues=summary.list_issues,
    typography_issues=summary.typography_issues,
    has_page_continuation=summary.has_page_continuation,
)
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DETECTION FLOW                                  │
│                                                                       │
│  PDF Page → Docling → markdown + image                               │
│                                                                       │
│              ▼                                                       │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  PageChainAgent (Claude Haiku)                                   │ │
│  │                                                                   │ │
│  │  Analyzes page and detects:                                      │ │
│  │  ├── headings (existing)                                         │ │
│  │  ├── figures (existing)                                          │ │
│  │  ├── tables (existing)                                           │ │
│  │  ├── page_artifacts (NEW) ────────────────┐                     │ │
│  │  ├── footnote_issues (NEW) ───────────────┤                     │ │
│  │  ├── citation_issues (NEW) ───────────────┤ → PageAnalysisOutput│ │
│  │  ├── list_issues (NEW) ───────────────────┤                     │ │
│  │  ├── typography_issues (NEW) ─────────────┤                     │ │
│  │  └── has_page_continuation (NEW) ─────────┘                     │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│              ▼                                                       │
│                                                                       │
│  PagePlan includes all detected issues                               │
│                                                                       │
│              ▼                                                       │
│                                                                       │
│  stage4_generate_jobs() creates:                                     │
│  ├── STRUCTURE jobs (heading fixes)                                  │
│  ├── CONTENT jobs (figures, tables)                                  │
│  └── PARAGRAPH jobs (NEW - from paragraph issues)                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Deliverables

| File | Action | Description |
|------|--------|-------------|
| `src/agents/page_chain.py` | Modify | Extend PageAnalysisOutput and system prompt |
| `src/agents/planner.py` | Modify | Extend PagePlan, propagate paragraph issues |
| `tests/unit/agents/test_paragraph_detection.py` | Create | Tests for detection |

---

## Acceptance Criteria

- [ ] `PageAnalysisOutput` has all 6 new fields
- [ ] System prompt includes paragraph detection instructions
- [ ] `PagePlan` carries paragraph issues
- [ ] Detection doesn't break existing heading/figure/table detection
- [ ] `has_page_continuation` properly detected
- [ ] Tests verify detection outputs

---

## Definition of Done

1. PageChainAgent detects paragraph issues
2. PagePlan includes all issue types
3. Existing tests pass (no regressions)
4. New detection tests pass
5. Manual test: run planning on a PDF with known issues

---

## Implementation Notes

### Detection Cost

Adding paragraph detection to the system prompt will slightly increase:
- Prompt tokens (longer instructions)
- Output tokens (more fields to populate)

Estimate: ~10-15% more tokens per page. With Haiku pricing, this is negligible.

### Graceful Handling

If the LLM doesn't detect paragraph issues, the fields default to empty lists. This is fine - not every page has paragraph issues.

### has_page_continuation Logic

The LLM should set `has_page_continuation=True` when:
- Last sentence ends without `.`, `!`, `?`, `:`, `;`
- Last word appears hyphenated at end: `infor-`
- Context suggests continuation (mid-thought)

This flag is used later to generate `PARAGRAPH_MERGE` jobs in the cross-page merge pass.

### Code Comment Standards

- **DO NOT include PRD numbers in code comments** - Comments like "PRD-001" or "(PRD-003)" should never appear in source code
- Comments should describe *what* and *why*, not *when* or *which PRD*
