# Pipeline Architecture

This document details the agentic document processing pipeline, including each phase, the agents involved, and the specialized tools available.

## Pipeline Flow

```
PDF Upload → PII Scan → Docling Extract → Phase 1: Planning → Phase 2: Execution
                                                                      │
                                              ┌───────────────────────┴───────────────────────┐
                                              │                                               │
                                        Phase 2.5: Cross-Page Merge          Phase 2.6: Issue Detection
                                              │                                               │
                                              └───────────────────────┬───────────────────────┘
                                                                      │
                                                            Phase 3: Verification
                                                                      │
                                                              ┌───────┴───────┐
                                                             Pass            Fail
                                                              │               │
                                                              ▼               ▼
                                                          Complete    Phase 4: Recovery → Complete
```

**Diagram description:** The pipeline flows left-to-right starting with PDF Upload, then PII Scan, Docling Extract, Phase 1 (Planning), and Phase 2 (Execution). After execution, two parallel sub-phases run: Phase 2.5 (Cross-Page Merge) and Phase 2.6 (Issue Detection). These converge into Phase 3 (Verification), which branches: if verification passes, processing completes; if it fails, Phase 4 (Recovery) runs before completing.

## Phase 1: Planning

**Purpose:** Analyze the document and create a processing plan with specific jobs.

**Location:** `src/agents/planner.py`

### Stage 1: Quick Scan (No LLM)

Fast regex-based analysis to identify document elements:

| Detection | Method |
|-----------|--------|
| Headings | Markdown `#` patterns |
| Figures | `<!-- image` placeholders |
| Tables | `<!-- table` placeholders, pipe tables |
| Page types | Heuristics (title, TOC, references, content) |

**Output:** `PageSkeleton` objects with element counts per page.

### Stage 2: Page Chain Analysis (LLM)

Sequential analysis with context chaining between pages:

| Analysis | Purpose |
|----------|---------|
| Document structure | Title, type, outline |
| Heading corrections | Level adjustments needed |
| Page summaries | Context for downstream processing |
| Figure/table context | Surrounding text for better alt-text |
| Domain dictionary | Document-specific terminology |

**Output:** `PageChainState` with accumulated document understanding.

### Stage 3: Job Generation (No LLM)

Converts analysis into actionable jobs:

| Job Type | When Created |
|----------|--------------|
| `ALT_TEXT` | Figure detected without description |
| `TABLE_TRANSCRIPTION` | Table placeholder or image table |
| `HEADING_FIX` | Incorrect heading level detected |
| `OCR_FIX` | OCR artifacts detected |
| `PARAGRAPH` | Page needs typography/citation cleanup |

**Output:** `DocumentPlan` with jobs routed to appropriate agents.

## Phase 2: Execution

**Purpose:** Process jobs in parallel using specialized agents and tools.

**Location:** `src/agents/orchestrator.py`, `src/agents/worker.py`, `src/agents/paragraph_agent.py`

### Job Routing

```
Job
 │
 ├─── STRUCTURE/CONTENT jobs ──→ Worker Agent
 │         (alt-text, tables, headings)
 │
 └─── PARAGRAPH jobs ──────────→ Paragraph Agent
              (typography, citations, footnotes)
```

**Diagram description:** Jobs are routed based on type. STRUCTURE and CONTENT jobs (handling alt-text, tables, and headings) go to the Worker Agent. PARAGRAPH jobs (handling typography, citations, and footnotes) go to the Paragraph Agent.

### Worker Agent

**Purpose:** Handle visual content and structural corrections.

**Model:** AWS Bedrock Claude (Efficient tier)

#### Worker Tools

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `view_page_tool` | See full page image + markdown | Page number | Image + text |
| `view_figure_tool` | View cropped figure image | Figure ID | Cropped image |
| `view_table_tool` | View cropped table image | Table ID | Cropped image |
| `read_section_tool` | Read specific markdown lines | Start/end lines | Text content |
| `find_text_tool` | Locate text in markdown | Search string | Line numbers |
| `get_table_markdown_tool` | Get table markdown with line numbers | Table ID | Markdown + lines |
| `propose_edit_tool` | Submit edit for validation | Before/after/reasoning | Approval status |

#### Task Types

| Task | Tool Sequence | Typical Calls |
|------|---------------|---------------|
| Alt-text | view_page → propose_edit | 1-2 calls |
| Table transcription | view_table → get_table_markdown → propose_edit | 2-3 calls |
| Heading fix | read_section → propose_edit | 1-2 calls |

**Optimization:** Pre-emptive context loading reduces tool calls by providing cropped images and found text in the initial prompt.

### Paragraph Agent

**Purpose:** Handle semantic corrections using specialized subagent tools.

**Model:** AWS Bedrock Claude (Efficient tier)

#### Paragraph Tools (Subagents)

Each tool invokes a specialized LLM call with expert prompting:

| Tool | Subagent | Handles |
|------|----------|---------|
| `remove_page_artifacts_tool` | Page Artifact Subagent | Page breaks, orphaned numbers, split words |
| `correct_footnote_tool` | Footnote Subagent | Footnote markers, definitions, linking |
| `fix_citation_links_tool` | Citation Subagent | Citation markers, bibliography linking |
| `fix_list_semantics_tool` | List Subagent | List nesting, numbering, mixed types |
| `fix_typography_tool` | Typography Subagent | Bold, italic, code, semantic meaning |

#### Subagent Pattern

```
Paragraph Agent receives page
    │
    ▼
Agent calls fix_typography_tool()
    │
    ▼
Typography Subagent runs (separate LLM call)
    │
    ▼
Returns: {corrected_text, confidence: 0.85, reasoning: "..."}
    │
    ▼
Agent reviews confidence
    │
    ├── ≥ 0.8: propose_edit(needs_review=false)
    ├── 0.5-0.8: propose_edit(needs_review=true)
    └── < 0.5: skip, log for manual review
```

**Diagram description:** The Paragraph Agent receives a page, then calls a specialized tool (e.g., fix_typography_tool). This invokes a separate Typography Subagent LLM call, which returns corrected text with a confidence score and reasoning. The agent then reviews the confidence: scores 0.8 or higher are auto-applied, scores between 0.5-0.8 are applied but flagged for review, and scores below 0.5 are skipped and logged for manual review.

### Subagent Specifications

| Subagent | Location | Handles |
|----------|----------|---------|
| Page Artifact | `subagents/page_artifacts.py` | Page breaks, split words, orphaned numbers |
| Footnote | `subagents/footnotes.py` | Footnote markers, definitions, linking |
| Citation | `subagents/citations.py` | Citation markers, bibliography linking |
| List | `subagents/lists.py` | List nesting, numbering, mixed types |
| Typography | `subagents/typography.py` | Bold, italic, code semantic meaning |

## Phase 2.5: Cross-Page Merge

**Purpose:** Fix paragraphs split across page boundaries.

**Location:** `src/agents/subagents/paragraph_merge.py`

A paragraph is merged if: page ends mid-sentence, next page starts lowercase, or hyphenated word at break.

## Phase 2.6: Issue Detection & Fixing

**Purpose:** Catch issues that workers missed.

Routing: deterministic fixes (headings, placeholders) run synchronously; LLM-based fixes (alt-text, tables) run asynchronously.

## Phase 3: Verification

**Purpose:** Validate processing completeness against the original plan.

**Location:** `src/agents/orchestrator.py` → `verify_document()`

### Verification Checks

| Check | Validates |
|-------|-----------|
| V1: Placeholders | No unfilled `<!-- image >` or `<!-- table >` |
| V2: Alt-text | All figures have non-empty alt-text |
| V3.1: Headings | Hierarchy matches document outline |
| V3.2: Figures | All planned figures processed |
| V3.3: Tables | All planned tables transcribed |
| V3.3.1: Table accuracy | Vision-based content verification |
| V3.4: Spelling | Against document dictionary |

### Verification Output

```python
{
    "pages": [...],           # Per-page results
    "critical_issues": [...], # Blocking failures
    "warnings": [...],        # Non-critical issues
    "passed": true/false
}
```

## Phase 4: Recovery (Conditional)

**Purpose:** Attempt to fix issues found during verification.

**Location:** `src/agents/orchestrator.py` → `run_recovery_phase()`

### Trigger Conditions

- Verification failed
- Pass rate ≥ 50% (worth attempting recovery)

### Recovery Process

```
For each failed page:
    attempts = 0
    while attempts < MAX_RECOVERY_ATTEMPTS:
        Run recovery agent with specific issues
        Re-verify page
        if passed: break
        attempts += 1

    Track: recovered | accepted_with_caveats | unrecoverable
```

### Recovery Output

```python
{
    "pages_recovered": 3,
    "pages_accepted_with_caveats": 1,
    "pages_unrecoverable": 0,
    "recovery_edits": [...]
}
```

## Edit Validation Pipeline

All proposed edits pass through validation before application:

```
propose_edit(before, after, reasoning)
    │
    ▼
validate_edit()
    ├── Check 'before' exists verbatim
    ├── Check spelling against dictionary
    ├── Check structure validity
    └── Return {approved, feedback}
    │
    ▼
If approved:
    ├── Apply edit to markdown
    ├── Create LedgerEntry
    └── Emit EditCommittedEvent

If rejected:
    ├── Return feedback to agent
    └── Agent can retry with corrections
```

**Diagram description:** When an agent calls propose_edit with before text, after text, and reasoning, the edit goes through validate_edit which checks that the "before" text exists verbatim in the document, validates spelling against the document dictionary, and checks structural validity. If approved, the edit is applied to the markdown, a LedgerEntry is created for the audit trail, and an EditCommittedEvent is emitted. If rejected, feedback is returned to the agent, which can retry with corrections.

## Event Stream

The pipeline emits Server-Sent Events (SSE) throughout processing:

| Event | Phase | Data |
|-------|-------|------|
| `docling:started` | Extract | - |
| `docling:complete` | Extract | Page count |
| `planning:started` | Planning | - |
| `planning:structure` | Planning | Document outline |
| `planning:page_summarized` | Planning | Page summary |
| `planning:complete` | Planning | Job count |
| `job:started` | Execution | Job type, page |
| `agent:thinking` | Execution | Tool calls, reasoning |
| `edit:committed` | Execution | Before/after/confidence |
| `job:completed` | Execution | Job result |
| `verification:started` | Verification | - |
| `verification:page` | Verification | Page status |
| `verification:complete` | Verification | Pass/fail |
| `recovery:started` | Recovery | Issue count |
| `recovery:complete` | Recovery | Recovery stats |
| `processing:complete` | Final | Result URLs |
| `processing:error` | Error | Error details |

## File Locations

| Component | Path |
|-----------|------|
| Orchestrator | `src/agents/orchestrator.py` |
| Planner | `src/agents/planner.py` |
| Worker Agent | `src/agents/worker.py` |
| Paragraph Agent | `src/agents/paragraph_agent.py` |
| Subagents | `src/agents/subagents/` |
| Page Chain | `src/agents/page_chain.py` |
| Processing Service | `src/services/document_processing.py` |
