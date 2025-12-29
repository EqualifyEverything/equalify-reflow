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
