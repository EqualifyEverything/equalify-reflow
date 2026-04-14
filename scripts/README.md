# Equalify Reflow — Scripts

Small utility and exploration scripts that live alongside the main application. Each one is self-contained and runs from the repo root.

## Available scripts

### `health-check.sh`

Validates that the local dev stack is running correctly. Checks Docker / Compose installation, container status, Redis connectivity, LocalStack S3 accessibility, Docker network connectivity, and volume persistence.

```bash
./scripts/health-check.sh
```

Exit code `0` when all critical checks pass, `1` otherwise. Run it after `make dev` if something looks off, or as a first-line smoke test when a container won't start.

### `batch_run.py`

Batch-submits a directory of PDFs to a running Equalify Reflow API and collects the results. Useful for regression-testing pipeline changes against a fixture set, benchmarking, or building a quick dataset of pipeline outputs.

```bash
# Requires a running API (make dev) and a valid API key.
uv run scripts/batch_run.py --help
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

## Infrastructure scripts

Historically this directory also contained `setup-aws.sh`, `deploy-app.sh`, and `deploy-infrastructure.sh`. Those have been removed — Terraform and deployment now live in the separate deploy repo (see [AGENTS.md](../AGENTS.md) for repo layout).
