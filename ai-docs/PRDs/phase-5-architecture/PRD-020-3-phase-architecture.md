# PRD-020: 4-Phase Processing Architecture

## Status: 🟡 IN PROGRESS

**Completed:**
- ✅ PRD-021: Data Models (2024-12-17)
- ✅ Consolidation layer removed
- ✅ Proposal model removed, replaced with AutoCorrection

**Remaining:**
- PRD-022: Structure Verification Loop
- PRD-023: Figures Agent Refactor
- PRD-024: Tables Agent Merge
- PRD-025: Typography Agent Enhancement
- PRD-026: Assembly Service
- PRD-027: Review Checklist API

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: 5 - Architecture
**Estimated Effort**: 3-4 weeks total (8 PRDs)
**Dependencies**: None (foundational)
**Reference**: [Architecture Discussion](../../../notes.md)

## Problem Statement

The current 5-phase pipeline suffers from **context loss through abstraction**:

```
Document → Observations (loses doc context) → Proposals (guesses at fixes) → Broken output
```

Evidence from testing (notes.md):
- **Extraction**: A- grade (excellent)
- **Observations**: C+ grade (good detection, poor filtering)
- **Consolidation**: D- grade (proposals harm accessibility, 40% would damage structure)
- **Proposals**: 0 usable proposals generated

By the time we reach consolidation, agents see "heading level mismatch at page 3" but have no idea what the document is about, what surrounding structure looks like, or whether changes would break something else.

## Solution

Simplify to **4 clean phases** where specialized agents make decisions **with full context**:

1. **Phase 1: Analyze** - Document understanding, manifest creation, agent routing
2. **Phase 2: Extract** - Raw markdown extraction with placeholders
3. **Phase 3: Refine** - Structure verification loop + specialized agents (parallel)
4. **Phase 4: Assemble** - Apply corrections, build final result (pure Python)

**Key Changes**:
- Eliminate consolidation entirely (11 agents → 5 agents)
- Each agent outputs corrections directly (not observations → proposals)
- Validation-driven loops using linter + spell checker
- Glass box transparency: all reasoning captured in traces

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: ANALYZE                                            │
│                                                             │
│ Analysis (Haiku) → DocumentManifest + DocumentSummary       │
│ Route to required agents based on document type             │
│                                                             │
│ Output: manifest, routing_decisions                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: EXTRACT                                            │
│                                                             │
│ Extraction (Haiku) → Raw markdown with placeholders         │
│ OCR Pre-check (Python) → Potential errors flagged           │
│                                                             │
│ Output: raw_markdown, ocr_suggestions                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: REFINE                                             │
│                                                             │
│ 3a. STRUCTURE VERIFICATION LOOP                             │
│     Loop until clean (max 3 iterations):                    │
│       • Structure Agent (LLM): Verify reading order         │
│       • Markdown Lint (Python): Detect formatting issues    │
│       • Spell Check (Python): Flag OCR errors for LLM       │
│       • mdformat (Python): Auto-fix formatting only         │
│       • LLM Fix: Semantic issues (spelling in context)      │
│                                                             │
│ 3b. SPECIALIZED AGENTS (parallel)                           │
│     • FIGURES (chained): classify → generate → validate     │
│     • TABLES (merged + loop): enhance → validate → loop     │
│     • TYPOGRAPHY (enhanced): analyze with doc-type rules    │
│                                                             │
│ Each outputs: AgentTrace with observations,                 │
│               auto_corrections, review_items                │
│                                                             │
│ Output: refined_markdown, structure_trace, agent_traces[]   │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 4: ASSEMBLE (Pure Python)                             │
│                                                             │
│ 1. Apply auto_corrections from all agents                   │
│ 2. Replace placeholders with enhanced content               │
│ 3. Final markdown validation                                │
│ 4. Compile ProcessingTrace from all AgentTraces             │
│ 5. Build ReviewChecklist from review_items                  │
│ 6. Compute final confidence score                           │
│                                                             │
│ Output: ProcessingResult (exposed via API)                  │
└─────────────────────────────────────────────────────────────┘
```

## Phase 1: DocumentSummary Generation

**Gap Identified**: The architecture diagram shows `DocumentSummary` as an output of Phase 1, but no PRD specifies how it's generated. This section fills that gap.

### Purpose

`DocumentSummary` provides semantic context to all downstream agents, enabling them to make better decisions without a consolidation layer. It contains:
- Topic understanding (what the document is about)
- Key entities (names, projects, technical terms for OCR detection)
- Domain vocabulary (helps catch OCR errors like "Exxon" → "Enzo")
- Expected elements (abstract, references, figures)
- Audience level (academic, student, general)

### Implementation: Summary Agent

Add a new `summary_agent.py` to the chained analysis pipeline that runs **after doctype classification** and **in parallel with headings/features**.

```
Chained Analysis Flow (Updated):
  Layout (5s) → DocType (3s) → [Headings | Features | Summary] parallel (5s) → Assemble
```

#### Summary Agent Output

```python
class DocumentSummaryOutput(BaseModel):
    """Output from summary agent."""

    title: str = Field(description="Document title")
    topic_summary: str = Field(
        description="1-2 sentences describing what the document is about"
    )
    key_entities: list[str] = Field(
        description="Important names, projects, technical terms (max 10)"
    )
    domain_terms: list[str] = Field(
        description="Domain-specific vocabulary (max 10)"
    )
    expected_elements: list[str] = Field(
        description="Expected elements: abstract, references, figures, etc."
    )
    audience_level: str = Field(
        description="Target audience: academic, student, general"
    )
    confidence: float = Field(ge=0.0, le=1.0)
```

#### Agent Configuration

```yaml
# config/agents/summary.yaml

system_prompt: |
  You are a document summarization specialist. Analyze the document and extract:

  1. TITLE: The main document title
  2. TOPIC SUMMARY: 1-2 sentences describing the content
  3. KEY ENTITIES: Important names, projects, or technical terms (max 10)
     - These are used for OCR error detection
     - Include proper nouns, project names, author names
     - Example: ["yt", "Enzo", "Matthew Turk", "DVCS"]
  4. DOMAIN TERMS: Technical vocabulary specific to this field (max 10)
     - Example: ["parallelization", "MPI", "OpenMP", "version control"]
  5. EXPECTED ELEMENTS: What structural elements should this document have?
     - Example: ["abstract", "introduction", "references", "figures"]
  6. AUDIENCE LEVEL: Who is this written for?
     - "academic" = researchers, assumes domain expertise
     - "student" = learners, may need more explanation
     - "general" = broad audience, accessible language

user_prompt: |
  Analyze this {document_type} document.

  Layout: {layout_summary}
  Total pages: {total_pages}

  Extract the summary information based on the first few pages.
```

#### Integration with chained_analysis.py

```python
# In analyze_document():

# Step 3: Headings + Features + Summary (can run in parallel)
if parallel:
    with tracer.start_as_current_span("step.headings_features_summary_parallel"):
        headings_task = headings_agent.extract(pages, layouts, doc_type, job_id)
        features_task = features_agent.detect(pages, job_id)
        summary_task = summary_agent.summarize(pages, doc_type, layouts, job_id)  # NEW

        (headings_output, headings_usage), \
        (features_list, features_usage), \
        (summary_output, summary_usage) = await asyncio.gather(
            headings_task, features_task, summary_task
        )
        usages.extend([headings_usage, features_usage, summary_usage])

# Step 4: Assemble manifest (include summary)
manifest = assemble_manifest(
    job_id=job_id,
    total_pages=len(pages),
    layouts=layouts,
    doc_type=doc_type,
    headings=headings_output,
    features=features_list,
    summary=summary_output,  # NEW
)
```

#### DocumentManifest Update

Add `summary` field to `DocumentManifest` in `remediation.py`:

```python
class DocumentManifest(BaseModel):
    # ... existing fields ...

    # NEW: Document summary for downstream agents
    summary: DocumentSummary | None = Field(
        default=None,
        description="Generated during analysis for downstream context"
    )
```

### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/agents/summary_agent.py` | CREATE | New agent for DocumentSummary generation |
| `config/agents/summary.yaml` | CREATE | Prompts for summary agent |
| `src/shared/models/remediation.py` | MODIFY | Add DocumentSummary model, add summary field to DocumentManifest |
| `src/agents/chained_analysis.py` | MODIFY | Add summary_agent call in parallel with headings/features |

### Cost Impact

- **Additional LLM call**: ~$0.005 per document (Haiku, first 2-3 pages only)
- **Offset**: Better OCR detection saves manual review time
- **Net**: Minimal cost increase for significant quality improvement

## Key Principles

### 1. Glass Box AI
All reasoning transparent and traceable:
- Every agent outputs `AgentTrace` with observations, corrections, reasoning
- `ProcessingTrace` captures full pipeline execution
- `ReviewChecklist` shows humans what decisions were made and why

### 2. Validation-Driven Loops
Deterministic detection, targeted LLM fixes:
- Markdown linter catches formatting issues (zero cost)
- Spell checker flags OCR errors (zero cost)
- LLM only called for semantic decisions
- Loop until clean or max iterations

### 3. Context Preservation
Agents see full document when deciding:
- `DocumentSummary` passed to all agents (topic, key entities, audience)
- Page images available for visual comparison
- Full markdown context for each decision

### 4. Human Focus on Semantics
Structure auto-fixed, humans verify meaning:
- Phase 3 (Refine) fixes structural issues automatically
- Phase 3 specialized agents generate `ReviewItem` for semantic decisions
- Humans focus on figures, tables, OCR - not heading hierarchy

## Success Criteria

- [ ] Pipeline completes with 4 clean phases
- [ ] Consolidation phase completely removed
- [ ] All agents output `AgentTrace` with glass box transparency
- [ ] Validation loops pass markdown linter
- [ ] `ProcessingResult` exposed via API
- [ ] `ReviewChecklist` usable for human review UI
- [ ] Cost reduced by ~40% (no Sonnet consolidation)
- [ ] Quality improved (no harmful proposals)

## Related PRDs

| PRD | Component | Phase | Effort |
|-----|-----------|-------|--------|
| PRD-021 | Data Models + Summary Agent | Foundation | 2 days |
| PRD-022 | Structure Verification Loop | Phase 3: Refine | 3 days |
| PRD-023 | Figures Agent Refactor | Phase 3: Refine | 2 days |
| PRD-024 | Tables Agent Merge | Phase 3: Refine | 2 days |
| PRD-025 | Typography Agent Enhancement | Phase 3: Refine | 2 days |
| PRD-026 | Assembly Service | Phase 4: Assemble | 2 days |
| PRD-027 | Review Checklist API | Phase 4: Assemble | 2 days |

**Note**: PRD-021 includes the `summary_agent.py` implementation described in "Phase 1: DocumentSummary Generation" section above.

## Migration Path

### Week 1: Foundation
1. PRD-021: Add new data models (hard cut - remove Proposal model)
2. PRD-022: Implement Structure Loop (Phase 3a)

### Week 2: Agent Refactors (Phase 3b)
3. PRD-023: Refactor Figures Agent
4. PRD-024: Merge Tables Agent
5. PRD-025: Enhance Typography Agent

### Week 3: Integration
6. PRD-026: Implement Assembly Service (Phase 4)
7. PRD-027: Add Review API endpoints
8. Integration testing and cleanup

### Week 4: Cleanup
9. Remove consolidation phase entirely
10. Remove old observation/proposal endpoints (no deprecation)
11. Documentation and monitoring

## Technical Notes

### New Dependencies
```
symspellpy>=6.7.0       # Spell checking
pymarkdownlnt>=0.9.0    # Markdown linting
mdformat>=0.7.0         # Markdown formatting
```

### Files to Remove
```
src/agents/consolidation/       # Entire directory
src/agents/chained_consolidation.py
src/agents/structure/alignment_agent.py  # Merged into structure loop
src/agents/structure/reading_order_agent.py
src/agents/tables/structure_agent.py  # Merged
src/agents/tables/accuracy_agent.py
```

### Agent Count
| Before | After | Phase | Change |
|--------|-------|-------|--------|
| 2 figures agents | 2 figures agents | Phase 3: Refine | Keep |
| 2 tables agents | 1 tables agent | Phase 3: Refine | Merge |
| 2 structure agents | 1 structure loop | Phase 3: Refine | Merge |
| 1 typography agent | 1 typography agent | Phase 3: Refine | Enhance |
| 4 consolidation agents | 0 | N/A | Remove |
| **11 total** | **5 total** | | **-55%** |

## Definition of Done

- [ ] All 8 PRDs implemented
- [ ] Integration tests pass
- [ ] Cost metrics show reduction
- [ ] Quality metrics show improvement
- [ ] API documentation updated
- [ ] Consolidation code removed
- [ ] Old endpoints removed (hard cut, no deprecation)
