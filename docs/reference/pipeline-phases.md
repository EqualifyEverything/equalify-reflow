# Pipeline phases reference

The pipeline has **5 versioned conversion phases** plus a **PII Review** gate that the viewer renders as a stage indicator. Each conversion phase is implemented by one or more `_step_*` methods in `src/services/pipeline_viewer.py`. PII Review runs inline in the streaming endpoint before any `_step_*` executes — it is a permission gate, not a versioned transformation, and does not bump the markdown version. This document is the authoritative public ↔ internal mapping for everything the viewer surfaces.

**Source of truth:** `clients/viewer/src/types/pipeline-viewer.ts` (`PIPELINE_STAGES`). If this table disagrees with that constant, the constant wins — update the docs.

## Public stages → internal steps

| Stage | Internal step names | AI? | What it does |
|---|---|---|---|
| **PII Review** (gate) | `pii_scan` | No | Runs Presidio against a text-only docling pass before full extraction. If findings exist, the streaming pipeline blocks on `session.pii_decision_event` until the user approves or denies via `POST /api/v1/pipeline/sessions/{sid}/pii-decision`. Denial aborts the pipeline. Can be opted out with `skip_pii_scan=true`. |
| 1. **Extraction** | `docling`, `docling_ocr` (conditional) | No | PDF → markdown + page images via IBM Docling. `docling_ocr` only fires when the classifier flags a scanned document. |
| 2. **Analysis** | `classification`, `structure` | Yes | `classification` tags the document as digital / scanned / malformed. `structure` identifies headings, footnotes, code blocks, per-page layout attributes, and per-page content flags (`has_tables`, `has_lists`, `has_forms`, …) that gate Translation work. |
| 3. **Headings** | `heading_reconciliation`, `heading_levels` | Yes | `heading_reconciliation` reconciles per-page heading candidates against the global outline. `heading_levels` normalises the hierarchy (H1 → H2 → H3, no skips). |
| 4. **Translation** | `page_content`, `code_blocks`, `form_fields` | Yes | `page_content` does per-page accessibility corrections (invokes image / table / list subagents). `code_blocks` tags fenced blocks with detected programming language. `form_fields` runs on every form page (structure `has_forms` or a deterministic underline/checkbox scan) and converts detected fields into accessible HTML (`<fieldset>`/`<label>`/`<input>`). |
| 5. **Assembly** | `boundaries`, `cleanup` | Mixed | `boundaries` rejoins cross-page split content and relocates footnotes (AI). `cleanup` normalises whitespace and lints the markdown (deterministic). |

The viewer also shows a dynamic **Review** stage that catches any orphan steps (`revision_*`, `feedback_*`, custom steps) not listed above.

## Internal step → `_step_*` method map

Ten methods in `src/services/pipeline_viewer.py`, plus `pii_scan` which runs inline in `src/api/pipeline_viewer.py` (no `_step_*` method — it's pre-extraction gate logic, not a PipelineViewerService method):

| Step name | Method | Phase | Deterministic / AI |
|---|---|---|---|
| `pii_scan` | inline in `_pipeline_steps` (pipeline_viewer.py) | PII Review | Deterministic (Presidio) |
| `docling` | `_step_docling` | Extraction | Deterministic |
| `docling_ocr` | `_step_docling_ocr` | Extraction (conditional) | Deterministic (Tesseract) |
| `classification` | emitted inline in `_step_docling` | Analysis | Deterministic |
| `structure` | `_step_structure` | Analysis | AI |
| `heading_reconciliation` | `_step_heading_reconciliation` | Headings | AI |
| `heading_levels` | `_step_heading_levels` | Headings | AI |
| `page_content` | `_step_page_content` | Translation | AI + subagents |
| `code_blocks` | `_step_code_blocks` | Translation | AI |
| `form_fields` | `_step_form_fields` | Translation | AI (vision, per form page) + deterministic HTML injection |
| `boundaries` | `_step_boundaries` | Assembly | AI + subagent |
| `cleanup` | `_step_cleanup` | Assembly | Deterministic |

`classification` is not its own `_step_*` method — it's a `StepResult` emitted from within `_step_docling` when the classifier runs. Up to 11 named step results can appear in one run.

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

## Keeping this table in sync

When you add, rename, or reclassify a step:

1. Update `PIPELINE_STAGES` in `clients/viewer/src/types/pipeline-viewer.ts`
2. Update the `nameMap` in `clients/viewer/src/components/pipeline-viewer/StageTabs.tsx` (used to infer which public phase a processing event belongs to)
3. Update this table
4. Update `AGENTS.md` (short summary of the 5 phases)

There is an open issue to align the SSE stream's emitted phase names with the 5 public names so the WordPress plugin's hardcoded stage list stops drifting — see the repo issues.
