# Equalify Reflow — Scripts

Small utility and exploration scripts that live alongside the main application. Each one is self-contained and runs from the repo root.

## Available scripts

### `health-check.sh`

Validates that the local dev stack is running correctly. Checks Docker / Compose installation, container status, Redis connectivity, Floci S3 accessibility, Docker network connectivity, and volume persistence.

```bash
./scripts/health-check.sh
```

Exit code `0` when all critical checks pass, `1` otherwise. Run it after `make dev` if something looks off, or as a first-line smoke test when a container won't start.

### `batch_run.py`

Batch-submits PDFs to a running Equalify Reflow API and collects the results. Supports two modes: a **manifest file** (reproducible benchmark corpora) or a **directory glob** (ad-hoc fixture runs). Full usage in [`docs/how-to/run-the-benchmark.md`](../docs/how-to/run-the-benchmark.md).

```bash
# Reproduce a published benchmark (PDFs placed beside the manifest):
BATCH_API_KEY=... uv run scripts/batch_run.py \
    --manifest docs/reference/benchmarks/v0.1.0-beta.6-pilot/manifest.txt

# Ad-hoc run over a local directory:
BATCH_API_KEY=... uv run scripts/batch_run.py --pdf-dir ~/pdfs/
```

### `test_chained_analysis.py`

Exploration script for a "chained analysis" approach that splits the monolithic structure-analysis prompt into focused sequential steps (layout detection → document type → heading structure → page features → agent routing). Not wired into the production pipeline — used for prompt-engineering experiments.

```bash
uv run scripts/test_chained_analysis.py project-docs/pdfs/07_attention_transformer_paper.pdf --pages 3
```

### `test_chained_integration.py`

Companion integration test for `test_chained_analysis.py` — exercises the same chained-analysis approach end-to-end against a sample document.

## Running scripts

Scripts run via `uv run` from the repo root. Do not run Python directly on the host — use `uv run script.py` for one-offs, or run inside the dev container with `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec api-gateway uv run scripts/<name>`.
