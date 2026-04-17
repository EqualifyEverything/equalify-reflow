# Pipeline phases reference

The pipeline has **5 public phases** that consumers (the viewer, the WordPress plugin, API clients) see. Internally, each phase is implemented by one or more `_step_*` methods in `src/services/pipeline_viewer.py`. This document is the authoritative public ↔ internal mapping.

**Source of truth:** `clients/viewer/src/types/pipeline-viewer.ts` (`PIPELINE_STAGES`). If this table disagrees with that constant, the constant wins — update the docs.

## Public phases → internal steps

| Public phase | Internal step names | AI? | What it does |
|---|---|---|---|
| 1. **Extraction** | `docling`, `docling_ocr` (conditional) | No | PDF → markdown + page images via IBM Docling. `docling_ocr` only fires when the classifier flags a scanned document. |
| 2. **Analysis** | `classification`, `structure` | Yes | `classification` tags the document as digital / scanned / malformed. `structure` identifies headings, footnotes, code blocks, per-page layout attributes. |
| 3. **Headings** | `heading_reconciliation`, `heading_levels` | Yes | `heading_reconciliation` reconciles per-page heading candidates against the global outline. `heading_levels` normalises the hierarchy (H1 → H2 → H3, no skips). |
| 4. **Translation** | `page_content`, `code_blocks` | Yes | `page_content` does per-page accessibility corrections (invokes image / table / list subagents). `code_blocks` tags fenced blocks with detected programming language. |
| 5. **Assembly** | `boundaries`, `cleanup` | Mixed | `boundaries` rejoins cross-page split content and relocates footnotes (AI). `cleanup` normalises whitespace and lints the markdown (deterministic). |

The viewer also shows a dynamic **Review** stage that catches any orphan steps (`revision_*`, `feedback_*`, custom steps) not listed above.

## Internal step → `_step_*` method map

Nine methods in `src/services/pipeline_viewer.py`:

| Step name | Method | Phase | Deterministic / AI |
|---|---|---|---|
| `docling` | `_step_docling` | Extraction | Deterministic |
| `docling_ocr` | `_step_docling_ocr` | Extraction (conditional) | Deterministic (Tesseract) |
| `classification` | emitted inline in `_step_docling` | Analysis | Deterministic |
| `structure` | `_step_structure` | Analysis | AI |
| `heading_reconciliation` | `_step_heading_reconciliation` | Headings | AI |
| `heading_levels` | `_step_heading_levels` | Headings | AI |
| `page_content` | `_step_page_content` | Translation | AI + subagents |
| `code_blocks` | `_step_code_blocks` | Translation | AI |
| `boundaries` | `_step_boundaries` | Assembly | AI + subagent |
| `cleanup` | `_step_cleanup` | Assembly | Deterministic |

`classification` is not its own `_step_*` method — it's a `StepResult` emitted from within `_step_docling` when the classifier runs. Up to 10 named step results can appear in one run.

## Subagents

Some main-agent tool calls delegate to specialist subagents:

| Parent step | Subagent | Output model |
|---|---|---|
| `page_content` | Image describer | `ImageDescriptionResult` |
| `page_content` | Table reconstructor | `TableReconstructionResult` |
| `page_content` | List reconstructor | `ListReconstructionResult` |
| `boundaries` | Footnote relocator | (inline, no dedicated output model) |

## Versioning

Each phase that changes the markdown writes a new version to S3:

| Version | After phase | Contents |
|---|---|---|
| `v0` | Extraction | Docling's initial markdown |
| `v1` | Translation | AI per-page corrections applied |
| `v2` | Assembly (boundaries) | Cross-page rejoin, footnote relocation |
| `v3` | Assembly (cleanup) | Whitespace + lint pass |

Intermediate phases (Analysis, Headings) produce metadata (outlines, classifications) that feed into later phases; they don't bump the markdown version.

## SSE `user_phase` field

Every SSE event that references a pipeline step carries a `user_phase` field alongside the internal step name. Consumers should drive progress UI off `user_phase` — it is the public 5-phase contract made explicit in the payload. Internal tooling and tracing can keep reading the internal `step_name` / `step.name` for granular behaviour.

**Valid values:** `extraction`, `analysis`, `headings`, `translation`, `assembly`, `review`.

`review` is the catch-all fallback for any step not declared in `PIPELINE_STAGES` (e.g. `revision_*`, `feedback_*`, future custom steps). Synthetic names like pipeline-level error markers also fall back to `review`.

**Where it is emitted:**

| Stream | Endpoint | Event shape |
|---|---|---|
| Pipeline viewer | `POST /api/v1/pipeline/process/stream`, `GET /api/v1/pipeline/sessions/{session_id}/stream` | Field on `processing`, `step`, and `error` event data payloads |
| Document processing | `GET /api/v1/documents/{job_id}/stream` | Field on `PipelinePhaseEvent` (`event_type: pipeline:phase`) |

Example `processing` event payload from the pipeline-viewer stream:

```json
{
  "step_name": "heading_reconciliation",
  "display_name": "Heading Reconciliation",
  "user_phase": "headings"
}
```

**Server-side source of truth:** `src/shared/pipeline_phases.py` (`PIPELINE_STAGES`, `user_phase_for_step`). The SSE emitter in `src/api/pipeline_viewer.py` calls `_attach_user_phase` at event-emit time so every event with a recognised step name is enriched uniformly — call sites do not repeat the lookup.

The client constant `PIPELINE_STAGES` in `clients/viewer/src/types/pipeline-viewer.ts` is the viewer's source of truth. `tests/unit/shared/test_pipeline_phases.py` parses that TypeScript file and asserts it stays structurally aligned with the Python mapping, so a one-sided rename breaks CI.

## Keeping this table in sync

When you add, rename, or reclassify a step:

1. Update `PIPELINE_STAGES` in `clients/viewer/src/types/pipeline-viewer.ts`
2. Update `PIPELINE_STAGES` in `src/shared/pipeline_phases.py` to match (the alignment test will fail otherwise)
3. Update this table
4. Update `AGENTS.md` (short summary of the 5 phases)

The viewer derives the active stage directly from the SSE `user_phase` field (see `clients/viewer/src/components/pipeline-viewer/StageTabs.tsx::isProcessingInStage`), so the old display-name `nameMap` no longer exists — adding a step is a one-line change in each `PIPELINE_STAGES` constant.
