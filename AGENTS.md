# Agents and Prompts in Equalify Reflow

Equalify Reflow's accuracy is driven by a small number of AI agents embedded in a versioned pipeline. This file is the canonical reference for those agents: what they do, where they live, how to iterate on prompts, and how to evaluate changes. If you are working on anything in `src/agents/` or touching agent call sites in `src/services/pipeline_viewer.py`, read this file first.

## 1. The pipeline at a glance

The pipeline runs in `src/services/pipeline_viewer.py`. Each phase reads the previous version's markdown and produces a new version, so any single phase can be re-run without reprocessing from scratch. Phases marked "AI" instantiate a PydanticAI `Agent` backed by a Claude model on AWS Bedrock.

| Phase | Name | AI? | Purpose |
|---|---|---|---|
| 1 | Docling extraction | No | PDF → markdown + page images via IBM Docling |
| 1a | Docling OCR re-extraction | No | Conditional: re-run Docling with Tesseract OCR when the classifier flags a scanned document |
| 2 | Structure analysis | Yes | Identify headings, footnotes, page types, and document structure |
| 3 | Heading reconciliation | Yes | Reconcile heading candidates against the document outline |
| 4 | Heading levels | Yes | Normalise heading hierarchy (H1 → H2 → H3) |
| 5 | Page content corrections | Yes | Per-page corrections with layout/content/quality prompt fragments; vision on demand |
| 6 | Code block languages | Yes | Identify programming languages in fenced code blocks |
| 7 | Cross-page boundary fixes | Yes | Rejoin split content across page breaks and relocate footnotes |
| 8 | Final cleanup | No | Normalise whitespace, lint markdown |

Within the page-content phase there are **subagents** that handle specific content types:

- **Image describer** — alt text and figure descriptions (`src/agents/image_description.py`)
- **Table reconstructor** — table structure, headers, alt descriptions (`src/agents/table_reconstruction.py`)
- **List reconstructor** — ordered/unordered list reconstruction (`src/agents/list_reconstruction.py`)

The boundary phase invokes two further agents:

- **Boundary fix** — cross-page content rejoin (`src/agents/boundary_fix.py`)
- **Footnote relocation** — lifts footnotes to the correct anchor (`src/agents/footnote_relocation.py`)

That is eight distinct `Agent(...)` call sites in `pipeline_viewer.py`, each backed by a system prompt in `src/agents/`.

## 2. Where the code lives

- **Prompt modules:** `src/agents/` — one file per agent, with the system prompt exported as a module-level constant (e.g. `STRUCTURE_SYSTEM_PROMPT`, `BOUNDARY_FIX_SYSTEM_PROMPT`). Pydantic output models live next to the prompts they produce.
- **Prompt fragments for page correction:** `src/agents/prompts/procedures/page_correction/` — composable `.md` fragments selected at runtime based on page attributes (layout, content type, quality). See `_compose_page_prompt` in `pipeline_viewer.py`.
- **Pipeline orchestration:** `src/services/pipeline_viewer.py` — each phase is an `async def _step_*` method. Agent instantiations live inside those methods.
- **Shared model config:** `src/agents/model_tiers.py` — defines the `ModelTier` enum and `MODEL_TIER_MAP` that every agent resolves through. Today every call site uses `BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])`; the provider-abstraction work in §6 is what will replace the direct Bedrock coupling.

## 3. Model tier selection

The project defines two tiers in `src/agents/model_tiers.py`:

- **`ModelTier.EFFICIENT`** — Claude Haiku 4.5. Default for every agent call site today. Fast, cheap, and validated against the integration fixtures for structure, correction, and tagging work.
- **`ModelTier.REASONING`** — Claude Sonnet 4.5. Reserved for heavier analysis or auto-correction work. Not wired into pipeline call sites today; available for new agents that measurably benefit from it.

Both tiers resolve to AWS Bedrock inference profile IDs (the `us.` prefix is required for Claude 4.5 models). Pricing comments in `model_tiers.py` are the canonical cost reference.

The rationale for defaulting to Efficient is that the pipeline is versioned — each phase's output is persisted — so a wrong answer on a specific page or section can be re-run on Reasoning without reprocessing the whole document. Start on Efficient; promote to Reasoning only when fixtures prove it's needed.

## 4. Iterating on a prompt

1. **Find the prompt.** Each agent's system prompt lives in `src/agents/*.py` as a module-level constant. Grep for the constant name or the `Agent(` call site in `pipeline_viewer.py`.
2. **Reproduce the failing case.** The pipeline viewer at `http://localhost:8080/viewer` accepts a PDF upload and renders per-phase output with version diffs. Use a small PDF from `tests/` or a public-domain document.
3. **Edit the prompt locally.** Hot reload picks up changes inside the running dev container (started with `make dev`).
4. **Re-run the pipeline.** Because each phase is versioned, you can resubmit a document and inspect the diff for just the phase you changed.
5. **Compare versions.** The pipeline viewer shows the v(n-1) → v(n) diff for every phase. Use this to confirm your prompt change fixed the failing case and to scan for regressions elsewhere in the document.
6. **Run the test suites.**
   - `make test-fast` — unit tests (prompts mocked). Quickest signal.
   - `make test-integration` — exercises services against fixtures.
   - `make test-e2e` — exercises the full pipeline end to end. Slower, but the strongest regression signal.
   If a prompt change breaks tests, the fix is usually a coordinated update to both the prompt and the fixtures — the fixtures are regression detectors, not oracles.
7. **Open a PR.** Include the before/after markdown diff (or a link to a pipeline-viewer session) in the PR body so reviewers can see the behaviour change.

## 5. Adding a new agent

1. Create a new module under `src/agents/` with your system prompt constant and any Pydantic output models.
2. Add a call site in the appropriate `_step_*` method in `src/services/pipeline_viewer.py`.
3. Resolve the model through `MODEL_TIER_MAP[ModelTier.EFFICIENT]` (or `REASONING` if justified) — do **not** hardcode model IDs.
4. Add unit tests that mock the model response.
5. Add integration tests that exercise the real agent against a small fixture.
6. Update the table in §1 of this file so contributors can find your agent.

## 6. Provider abstraction (in progress)

Today every agent call site imports `BedrockConverseModel` directly and instantiates it with a Bedrock inference profile ID:

```python
from pydantic_ai.models.bedrock import BedrockConverseModel
from ..agents.model_tiers import MODEL_TIER_MAP, ModelTier

model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
```

A provider-abstraction effort is planned that will introduce an `AIProvider` protocol so contributors can run the pipeline against Anthropic direct, Bedrock, or other providers without editing agent call sites. Until that effort lands, new agent work should keep using the pattern above; the rewrite will touch all call sites in a single pass.

## 7. Prompt engineering conventions

- **System prompts are terse.** State the role, the input contract, and the output contract. No padding.
- **Structured outputs via Pydantic.** Every agent uses PydanticAI's `output_type=...` with a Pydantic model; contributors should not parse free-text from agent responses.
- **Temperature is model-default** unless a specific phase has a documented reason to override (none currently do).
- **Vision is opt-in.** Page content corrections can attach a rendered page image when text-only correction is insufficient. Other phases use text only.
- **No hidden state.** Each agent call is self-contained. Agents do not share memory across phases — state lives in the versioned pipeline outputs that each phase reads and writes.
- **Prompt fragments compose.** For page correction, the prompt is assembled from a base fragment plus layout/content/quality fragments under `src/agents/prompts/procedures/page_correction/`. Prefer adding or editing a fragment over hardcoding variants.

## 8. Debugging prompt issues

- **Check the traces.** Logfire is wired into PydanticAI in `src/main.py` behind the `LOGFIRE_ENABLED` flag. With it enabled you can see every agent call with full input/output.
- **Check the pipeline viewer.** Per-phase markdown and JSON outputs for every job, with inter-version diffs.
- **Check the integration tests.** `tests/integration/` and `tests/e2e/` hold "correct" expectations to compare your output against.
- **Check the ledger.** `GET /api/v1/documents/{job_id}/ledger` returns the change ledger for a completed job — useful for seeing exactly which corrections an agent applied.

## 9. Related documentation

- [docs/architecture.md](docs/architecture.md) — overall system design, data flow, service layer, and Bedrock setup
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to develop locally, test tiers, and submit changes
- [CLAUDE.md](CLAUDE.md) — Claude Code session conventions (essential commands, never-do-these, default ports, Context7 library IDs) for contributors using the Claude Code CLI
