# Equalify PDF Converter - System Architecture

This document provides a visual overview of the complete PDF processing pipeline.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EQUALIFY PDF CONVERTER                               │
│                    Full Processing Pipeline Overview                         │
└─────────────────────────────────────────────────────────────────────────────┘

                                    INPUT
                                      │
                                      ▼
                              ┌───────────────┐
                              │   PDF File    │
                              │   (upload)    │
                              └───────────────┘
                                      │
                                      ▼
══════════════════════════════════════════════════════════════════════════════
                              PRE-PROCESSING
══════════════════════════════════════════════════════════════════════════════
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   │
           ┌────────────────┐                          │
           │  Upload to S3  │                          │
           │  (temp bucket) │                          │
           └────────────────┘                          │
                    │                                   │
                    ▼                                   │
           ┌────────────────┐      ┌────────────┐     │
           │   PII SCAN     │─────▶│ PII Found? │     │
           │  (Presidio)    │      └────────────┘     │
           └────────────────┘             │            │
                                    ┌─────┴─────┐     │
                                    ▼           ▼     │
                              ┌─────────┐ ┌─────────┐ │
                              │   NO    │ │   YES   │ │
                              └─────────┘ └─────────┘ │
                                    │           │     │
                                    │           ▼     │
                                    │    ┌───────────────┐
                                    │    │ AWAIT HUMAN   │
                                    │    │   APPROVAL    │◀──── Staff reviews
                                    │    └───────────────┘      PII findings
                                    │           │
                                    │     ┌─────┴─────┐
                                    │     ▼           ▼
                                    │ ┌────────┐ ┌────────┐
                                    │ │APPROVED│ │REJECTED│──▶ Job Failed
                                    │ └────────┘ └────────┘
                                    │     │
                                    ▼     ▼
══════════════════════════════════════════════════════════════════════════════
                         PHASE 1: ANALYZE (Haiku)
══════════════════════════════════════════════════════════════════════════════
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
     ┌────────────────┐     ┌────────────────┐     ┌────────────────┐
     │  Layout Agent  │     │ DocType Agent  │     │ Summary Agent  │
     │  (page zones)  │     │ (classify doc) │     │ (key entities) │
     └────────────────┘     └────────────────┘     └────────────────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      ▼
                           ┌─────────────────────┐
                           │  DocumentManifest   │
                           │  + DocumentSummary  │
                           │  + Agent Routing    │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                         PHASE 2: EXTRACT (Haiku)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  Extraction Agent   │
                           │  (page-by-page)     │
                           │                     │
                           │  • Raw markdown     │
                           │  • {{FIGURE_1}}     │
                           │  • {{TABLE_1}}      │
                           └─────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   Raw Markdown +    │
                           │   Placeholders      │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                          PHASE 3: REFINE (Haiku)
══════════════════════════════════════════════════════════════════════════════
                                      │
              ┌───────────────────────┴───────────────────────┐
              ▼                                               ▼
┌──────────────────────────────┐               ┌──────────────────────────────┐
│  3a. STRUCTURE LOOP          │               │  3b. SPECIALIZED AGENTS      │
│  (iterates until clean)      │               │  (run in parallel)           │
│                              │               │                              │
│  ┌────────────────────────┐  │               │  ┌────────────────────────┐  │
│  │ Structure Agent (LLM)  │  │               │  │   FIGURES Agent        │  │
│  │ → verify reading order │  │               │  │   • classify figure    │  │
│  └───────────┬────────────┘  │               │  │   • generate alt-text  │  │
│              ▼               │               │  │   • validate           │  │
│  ┌────────────────────────┐  │               │  └────────────────────────┘  │
│  │ Markdown Lint (Python) │  │               │                              │
│  │ → formatting issues    │  │               │  ┌────────────────────────┐  │
│  └───────────┬────────────┘  │               │  │   TABLES Agent         │  │
│              ▼               │               │  │   • enhance structure  │  │
│  ┌────────────────────────┐  │               │  │   • validate accuracy  │  │
│  │ Spell Check (Python)   │  │               │  └────────────────────────┘  │
│  │ → OCR error detection  │  │               │                              │
│  └───────────┬────────────┘  │               │  ┌────────────────────────┐  │
│              ▼               │               │  │   TYPOGRAPHY Agent     │  │
│  ┌────────────────────────┐  │               │  │   • bold/italic        │  │
│  │ mdformat (Python)      │  │               │  │   • emphasis detection │  │
│  │ → auto-fix formatting  │  │               │  └────────────────────────┘  │
│  └───────────┬────────────┘  │               │                              │
│              ▼               │               └──────────────────────────────┘
│  ┌────────────────────────┐  │                              │
│  │ LLM Fix (if needed)    │  │                              │
│  │ → semantic corrections │  │                              │
│  └────────────────────────┘  │                              │
│              │               │                              │
│         (loop max 3x)        │                              │
└──────────────────────────────┘                              │
              │                                               │
              └───────────────────────┬───────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   Agent Traces +    │
                           │   Observations +    │
                           │   Auto-Corrections  │
                           │   Review Items      │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                     PHASE 4: ASSEMBLE (Pure Python)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  Assembly Service   │
                           │                     │
                           │  1. Apply auto-     │
                           │     corrections     │
                           │  2. Replace         │
                           │     placeholders    │
                           │  3. Final lint      │
                           │  4. Build trace     │
                           │  5. Build checklist │
                           │  6. Compute         │
                           │     confidence      │
                           └─────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  ProcessingResult   │
                           │  ├─ markdown        │
                           │  ├─ confidence      │
                           │  ├─ processing_trace│
                           │  └─ review_checklist│
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                           HUMAN REVIEW (API)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │  GET /api/documents/{id}/result │
                    │  → Fetch checklist + markdown   │
                    └─────────────────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   Staff Reviews     │
                           │   Checklist Items   │
                           │                     │
                           │   • Alt-text OK?    │
                           │   • OCR correct?    │
                           │   • Tables right?   │
                           │   • Bold/italic?    │
                           └─────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │  PUT /api/documents/{id}/reviews│
                    │  → Save all decisions (batch)   │
                    └─────────────────────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │  POST /api/documents/{id}/apply │
                    │  → Apply corrections, finalize  │
                    └─────────────────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                                   OUTPUT
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   Final Markdown    │
                           │   (S3 results)      │
                           │                     │
                           │   • Accessible      │
                           │   • Semantic        │
                           │   • Human-verified  │
                           └─────────────────────┘
```

## Summary

| Attribute | Value |
|-----------|-------|
| **Input** | PDF (course materials only, no student PII) |
| **Output** | Accessible semantic markdown |
| **Cost** | ~$0.15-0.50 per document (Haiku-based) |
| **Time** | 3-5 minutes processing + human review |
| **Agents** | 5 total (down from 11) |

## Agents

| Agent | Phase | Purpose |
|-------|-------|---------|
| Structure Loop | 3a | Verify reading order, fix formatting |
| Figures | 3b | Classify figures, generate alt-text |
| Tables | 3b | Enhance table structure, validate |
| Typography | 3b | Detect bold/italic emphasis |
| Extraction | 2 | Page-by-page markdown extraction |

## API Endpoints (PRD-028)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/documents/{id}/result` | Fetch processing result + checklist |
| `PUT` | `/api/documents/{id}/reviews` | Save review decisions (batch) |
| `POST` | `/api/documents/{id}/apply` | Apply corrections, finalize |

## Related Documentation

- [PRD-020: 4-Phase Architecture](PRDs/phase-5-architecture/PRD-020-3-phase-architecture.md)
- [PRD-028: Review API v2](PRDs/phase-5-architecture/PRD-028-review-api-v2.md)
- [CLAUDE.md](../CLAUDE.md) - Quick reference for developers

---

# Experimental Pipeline Architecture

The experimental endpoint (`POST /api/experiments/process`) implements a simplified architecture
with a single unified refine agent replacing the specialized agents.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    EXPERIMENTAL PIPELINE (NEW)                              │
│                  POST /api/experiments/process                              │
└─────────────────────────────────────────────────────────────────────────────┘

                                    INPUT
                                      │
                                      ▼
                              ┌───────────────┐
                              │   PDF File    │
                              │   (upload)    │
                              └───────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                      PHASE 1: INGEST (Docling - FREE)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │   PDFConverter      │
                           │                     │
                           │  • Page images      │
                           │  • Element bboxes   │
                           │  • Docling markdown │◀── NEW: Native text extraction
                           │  • is_scanned flag  │◀── NEW: Scanned detection
                           └─────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │ PDFConversionResult │
                           │ ├─ pages[]          │
                           │ ├─ extracted_images │
                           │ ├─ docling_markdown │
                           │ └─ is_scanned       │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                      PHASE 2: ANALYZE (Haiku - 1 LLM call)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  _analyze_document  │
                           │                     │
                           │  Input: first page  │
                           │  Output:            │
                           │  • document_type    │
                           │  • expected_headings│
                           │  • expected_figures │
                           │  • expected_tables  │
                           │  • key_terms        │
                           │  • hotspots[]       │
                           └─────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │    Requirements     │
                           │    (drives refine)  │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                 PHASE 3: EXTRACT (Docling-first, LLM fallback)
══════════════════════════════════════════════════════════════════════════════
                                      │
                              ┌───────┴───────┐
                              ▼               ▼
                       ┌────────────┐  ┌────────────┐
                       │ is_scanned │  │ is_scanned │
                       │  = False   │  │  = True    │
                       │ (digital)  │  │ (scanned)  │
                       └────────────┘  └────────────┘
                              │               │
                              ▼               ▼
                    ┌─────────────────┐ ┌─────────────────┐
                    │ Use Docling     │ │ Use LLM Vision  │
                    │ markdown        │ │ per page        │
                    │                 │ │                 │
                    │ Cost: FREE      │ │ Cost: 9 calls   │
                    │ Time: instant   │ │ Time: ~2-3 min  │
                    └─────────────────┘ └─────────────────┘
                              │               │
                              └───────┬───────┘
                                      ▼
                           ┌─────────────────────┐
                           │  page_markdowns{}   │
                           │  (per-page content) │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
              PHASE 4: REFINE (Single Agent with Tool Use)
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │     FOR EACH PAGE (sequential)  │
                    │                                 │
                    │  ┌───────────────────────────┐  │
                    │  │  1. PREPROCESS (FREE)     │  │
                    │  │     • run_lint()          │  │
                    │  │     • spell_check()       │  │
                    │  │     • Auto-apply fixes    │  │
                    │  └───────────────────────────┘  │
                    │              │                  │
                    │              ▼                  │
                    │  ┌───────────────────────────┐  │
                    │  │  2. REFINE (Haiku Agent)  │  │
                    │  │     with tool use:        │  │
                    │  │                           │  │
                    │  │  ┌─────────────────────┐  │  │
                    │  │  │ describe_figure()   │  │  │
                    │  │  │ → subagent call     │  │  │
                    │  │  │ → cropped + highlight│  │  │
                    │  │  └─────────────────────┘  │  │
                    │  │                           │  │
                    │  │  ┌─────────────────────┐  │  │
                    │  │  │ describe_table()    │  │  │
                    │  │  │ → subagent call     │  │  │
                    │  │  │ → cropped + highlight│  │  │
                    │  │  └─────────────────────┘  │  │
                    │  │                           │  │
                    │  │  ┌─────────────────────┐  │  │
                    │  │  │ check_against_image │  │  │
                    │  │  │ → verify vs source  │  │  │
                    │  │  └─────────────────────┘  │  │
                    │  │                           │  │
                    │  │  ┌─────────────────────┐  │  │
                    │  │  │ apply_edit()        │  │  │
                    │  │  │ → search/replace    │  │  │
                    │  │  │ → full provenance   │  │  │
                    │  │  └─────────────────────┘  │  │
                    │  └───────────────────────────┘  │
                    │              │                  │
                    │              ▼                  │
                    │  ┌───────────────────────────┐  │
                    │  │  3. VALIDATE             │  │
                    │  │     • Must pass lint     │  │
                    │  │     • Max 3 iterations   │  │
                    │  └───────────────────────────┘  │
                    │                                 │
                    │         (loop per page)         │
                    └─────────────────────────────────┘
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │  ProcessingTrace    │
                           │  ├─ phases[]        │
                           │  ├─ total_llm_calls │
                           │  ├─ page_results[]  │
                           │  └─ edit_history[]  │
                           └─────────────────────┘
                                      │
══════════════════════════════════════════════════════════════════════════════
                           PHASE 5: OUTPUT
══════════════════════════════════════════════════════════════════════════════
                                      │
                                      ▼
                           ┌─────────────────────┐
                           │ ExperimentResponse  │
                           │                     │
                           │  • success: bool    │
                           │  • markdown: str    │
                           │  • requirements     │
                           │  • trace            │
                           │  • edit_history[]   │◀── Full provenance
                           │  • edit_summary{}   │◀── Counts by type
                           └─────────────────────┘
```

## Experimental vs Production Comparison

| Aspect | Production Pipeline | Experimental Pipeline |
|--------|--------------------|-----------------------|
| **Extraction** | LLM vision (always) | Docling-first (born-digital = FREE) |
| **Specialized Agents** | 4 (Figures, Tables, Typography, Structure) | 1 unified agent with tools |
| **Edit Tracking** | Limited | Full provenance (reasoning, type, source) |
| **PII Scanning** | Yes (Presidio) | Skipped (for experimentation) |
| **Endpoint** | `POST /api/documents/submit` | `POST /api/experiments/process` |

## Edit Types Tracked

| Type | Description | Source |
|------|-------------|--------|
| `lint_fix` | Auto-applied formatting fixes | mdformat |
| `spell_fix` | OCR artifact corrections | spell_check |
| `figure_alt` | Alt-text for figures | describe_figure subagent |
| `table_transcription` | Markdown table | describe_table subagent |
| `heading_fix` | Heading level corrections | refine_agent |
| `content_fix` | General content corrections | refine_agent |
| `verification_fix` | Fixes from image verification | check_against_image |

## Cost Comparison (9-page document)

| Phase | Production | Experimental (digital) | Experimental (scanned) |
|-------|------------|------------------------|------------------------|
| Analyze | - | 1 call | 1 call |
| Extract | 9 calls | **0 calls** | 9 calls |
| Refine | ~20 calls | ~15 calls | ~15 calls |
| **Total** | ~29 calls | **~16 calls** | ~25 calls |

## Files

| File | Purpose |
|------|---------|
| `src/api/experiments.py` | Endpoint + phase orchestration |
| `src/agents/refine/refine_agent.py` | Unified refine agent |
| `src/agents/refine/tools.py` | Tool implementations |
| `src/agents/refine/subagents.py` | Figure/table/verify subagents |
| `src/agents/refine/models.py` | Requirements, EditPatch, ProcessingTrace |
| `src/utils/image_utils.py` | crop_element, highlight_element |
| `config/agents/refine.yaml` | Agent prompts |
