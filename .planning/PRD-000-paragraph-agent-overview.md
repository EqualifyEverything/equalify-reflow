# PRD-000: ParagraphAgent Implementation Overview

## Executive Summary

This document series defines the implementation of **ParagraphAgent**, a new domain agent that handles text flow issues using the **subagent tools pattern**. This is an additive change - the existing Worker agent continues handling figures, tables, and headings unchanged.

---

## The Problem

Current pipeline handles well:
- ✅ Figures (alt-text generation)
- ✅ Tables (transcription)
- ✅ Headings (level correction)

Current pipeline does NOT handle:
- ❌ Page break artifacts (`---`, split words like `de-\nprecate`)
- ❌ Footnote placement and linking
- ❌ Citation references to bibliography
- ❌ List structure (nesting, numbering)
- ❌ Typography semantics (bold/italic meaning)
- ❌ Cross-page paragraph continuity

---

## The Solution: Subagent Tools Pattern

**Key Insight:** Complex document edits should be LLM-controlled, not deterministic. The subagent tools pattern provides:

1. **Parent Agent** (ParagraphAgent) has judgment and context
2. **Subagent Tools** are specialists that return recommendations with **confidence scores**
3. **Parent Agent reviews** and decides whether to apply via `propose_edit()`
4. **Low confidence** = flag for human review (not skip entirely)

```
ParagraphAgent
    │
    ├── calls remove_page_artifacts() tool
    │       └── Subagent returns: {cleaned_text, confidence: 0.92}
    │
    ├── Agent reviews: confidence >= 0.8 ✓
    │       └── propose_edit(needs_review=False)
    │
    ├── calls correct_footnote() tool
    │       └── Subagent returns: {corrected_md, confidence: 0.65}
    │
    └── Agent reviews: confidence < 0.8
            └── propose_edit(needs_review=True)  ← flagged for human review
```

---

## PRD Index

| PRD | Title | Effort | Dependencies | Status |
|-----|-------|--------|--------------|--------|
| **PRD-001** | Foundation - Models, Types, Base Infrastructure | 1-2 days | Cleanup complete | Pending |
| **PRD-002** | Subagent Implementations | 3-4 days | PRD-001 | Pending |
| **PRD-003** | ParagraphAgent Core | 2-3 days | PRD-001, PRD-002 | Pending |
| **PRD-004** | Paragraph Issue Detection | 1-2 days | PRD-001 | Pending |
| **PRD-005** | Pipeline Integration & Merge Pass | 2-3 days | All above | Pending |

**Total Estimated Effort:** 10-14 days

---

## Architecture Overview

### Current Pipeline (Unchanged)

```
Planning → Worker Agent (figures, tables, headings) → Issue Fixer → Verification
```

### New Pipeline (With ParagraphAgent)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PLANNING                                     │
│  PageChainAgent (enhanced) detects:                                  │
│  - Headings, figures, tables (existing)                              │
│  - Page artifacts, footnotes, citations, lists, typography (NEW)     │
└─────────────────────────────────────────────────────────────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│  STRUCTURE Jobs     │ │   CONTENT Jobs      │ │  PARAGRAPH Jobs     │
│  (heading fixes)    │ │ (figures, tables)   │ │     (NEW)           │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
            │                      │                      │
            ▼                      ▼                      ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│   Worker Agent      │ │   Worker Agent      │ │  ParagraphAgent     │
│   (existing)        │ │   (existing)        │ │     (NEW)           │
│                     │ │                     │ │                     │
│                     │ │                     │ │  Subagent tools:    │
│                     │ │                     │ │  - page_artifacts   │
│                     │ │                     │ │  - footnotes        │
│                     │ │                     │ │  - citations        │
│                     │ │                     │ │  - lists            │
│                     │ │                     │ │  - typography       │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
            │                      │                      │
            └──────────────────────┼──────────────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │  Cross-Page Merge Pass (NEW)│
                    │  - merge_paragraphs subagent│
                    └─────────────────────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │  Issue Fixer (existing)     │
                    └─────────────────────────────┘
                                   │
                                   ▼
                    ┌─────────────────────────────┐
                    │  Verification (existing)    │
                    └─────────────────────────────┘
```

---

## Subagent Tools

| Tool | Problem Solved | Returns |
|------|----------------|---------|
| `remove_page_artifacts()` | `---`, split words | `PageArtifactResult` |
| `correct_footnote()` | Footnote placement/linking | `FootnoteResult` |
| `fix_citation_links()` | Citation → bibliography | `CitationResult` |
| `fix_list_semantics()` | List nesting/numbering | `ListResult` |
| `fix_typography()` | Semantic bold/italic/code | `TypographyResult` |
| `merge_paragraphs()` | Cross-page splits | `ParagraphMergeResult` |

All results include:
- `confidence: float` (0.0-1.0)
- `reasoning: str`

---

## Confidence Thresholds

```python
CONFIDENCE_AUTO_APPLY = 0.8      # Apply automatically
CONFIDENCE_APPLY_WITH_REVIEW = 0.5  # Apply but flag for review
CONFIDENCE_SKIP = 0.5           # Below this, skip edit
```

This integrates with your human review branch:
- `needs_review=True` on ledger entries
- API returns `entries_needing_review` count
- Human reviewer sees flagged edits

---

## Key Design Decisions

### 1. Additive, Not Replacement

Existing Worker agent is unchanged. ParagraphAgent handles NEW task types. This reduces risk.

### 2. Per-Page Jobs + Merge Pass

- Per-page jobs: artifacts, footnotes, citations, lists, typography
- Merge pass: runs AFTER all per-page jobs, on stable markdown

### 3. LLM-Controlled Everything

No deterministic edits for paragraph tasks. All go through subagent → parent judgment → propose_edit.

### 4. Confidence-Based Review

Low confidence doesn't mean skip - it means flag for human review. This catches uncertain cases without losing work.

---

## Implementation Order

```
1. PRD-001: Foundation (types, models)
           ↓
    ┌──────┴──────┐
    ↓             ↓
2. PRD-002    3. PRD-004
   Subagents     Detection
    ↓             ↓
    └──────┬──────┘
           ↓
4. PRD-003: ParagraphAgent Core
           ↓
5. PRD-005: Pipeline Integration
```

PRD-002 and PRD-004 can be done in parallel after PRD-001.

---

## Cost Impact

### Per-Page (with paragraph issues)

| Component | Model | Cost |
|-----------|-------|------|
| ParagraphAgent | Haiku | ~$0.001 |
| Per subagent call | Haiku | ~$0.001 |
| Typical page (2-3 subagent calls) | | ~$0.003-0.004 |

### Per-Document (20 pages, 10 with issues)

| Current | With ParagraphAgent | Delta |
|---------|---------------------|-------|
| ~$0.05 | ~$0.08-0.09 | +$0.03-0.04 |

**Conclusion:** Negligible cost increase with Haiku.

---

## Files Created/Modified

### New Files

| File | PRD |
|------|-----|
| `src/agents/subagents/__init__.py` | PRD-001 |
| `src/agents/subagents/types.py` | PRD-001 |
| `src/agents/subagents/page_artifacts.py` | PRD-002 |
| `src/agents/subagents/footnotes.py` | PRD-002 |
| `src/agents/subagents/citations.py` | PRD-002 |
| `src/agents/subagents/lists.py` | PRD-002 |
| `src/agents/subagents/typography.py` | PRD-002 |
| `src/agents/subagents/paragraph_merge.py` | PRD-002 |
| `src/agents/paragraph_agent.py` | PRD-003 |

### Modified Files

| File | PRD | Changes |
|------|-----|---------|
| `src/agents/models.py` | PRD-001 | Add types, LedgerEntry.needs_review |
| `src/agents/page_chain.py` | PRD-004 | Extend detection |
| `src/agents/planner.py` | PRD-004, PRD-005 | Generate PARAGRAPH jobs |
| `src/agents/orchestrator.py` | PRD-005 | Route jobs, add merge pass |
| `src/agents/__init__.py` | PRD-005 | Export new agent |
| `src/api/documents.py` | PRD-005 | Surface needs_review |
| `src/api/schemas.py` | PRD-005 | Add needs_review to response |

---

## Success Metrics

After implementation:

1. **Page artifacts removed**: `---` and split words cleaned up
2. **Footnotes linked**: Markers connected to definitions
3. **Citations linked**: References connected to bibliography
4. **Lists fixed**: Proper nesting and numbering
5. **Typography added**: Semantic formatting preserved
6. **Cross-page merges**: Split paragraphs joined
7. **Human review queue**: Low-confidence edits flagged

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Over-editing | Medium | Confidence thresholds, human review |
| Cost increase | Low | Using Haiku, thresholds prevent unnecessary calls |
| Complexity | Medium | Additive design, existing code unchanged |
| Breaking existing | Low | Separate job type, separate agent |

---

## Next Steps

1. Ensure v5-naming-cleanup-plan.md is complete
2. Begin PRD-001 implementation
3. Follow dependency order for remaining PRDs
4. Test with real PDFs containing known issues
