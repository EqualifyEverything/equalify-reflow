# Equalify Reflow — Agent Guide

> **`CLAUDE.md` is a symlink to this file.** Edit `AGENTS.md` (the real file) and both update.

This file is a **pointer file** — it orients you, tells you where the authoritative information lives, and covers the workflows you'll hit most often. For anything deeper than this, follow the links into `docs/`.

## What this project is

Equalify Reflow is an open-source FastAPI monolith that converts PDF documents into accessible, semantic markdown. It combines IBM Docling extraction with a multi-agent PydanticAI correction pipeline (currently Claude Haiku 4.5 on AWS Bedrock, with Anthropic direct as a swappable backend). Originally built with the University of Illinois Chicago for course materials; maintained openly for any organisation that needs accessible document conversion.

**Domain constraint:** course materials only. Do not add features that process student records or PII beyond the existing Presidio scan.

## Finding things

Docs are grouped under `docs/` by what you're trying to do:

| Looking for | Go to |
|---|---|
| Guided walkthroughs (learning) | [`docs/tutorials/`](docs/tutorials/) |
| Recipes for specific tasks | [`docs/how-to/`](docs/how-to/) |
| Authoritative lookups (tables, configs) | [`docs/reference/`](docs/reference/) |
| Why things are the way they are | [`docs/explanation/`](docs/explanation/) |

Especially useful single pages:

- [`docs/reference/pipeline-phases.md`](docs/reference/pipeline-phases.md) — canonical 5-phase ↔ internal step mapping
- [`docs/reference/model-tiers.md`](docs/reference/model-tiers.md) — `ModelTier` enum and backend maps
- [`docs/explanation/architecture.md`](docs/explanation/architecture.md) — service diagram, data flows, circuit-breaker strategy
- [`docs/how-to/set-up-dev-environment.md`](docs/how-to/set-up-dev-environment.md) — full local setup
- API reference — runtime Swagger at `http://localhost:8080/docs` (locally) or `https://reflow.equalify.uic.edu/docs` (deployed)

## Common commands

```bash
make dev                # Start the full dev stack (auto-detects GPU for docling)
make down               # Stop all services
make test-fast          # Unit tests, ~30s — run before every commit
make test-integration   # Integration tests, ~2min — run before PRs
make test-e2e           # End-to-end, ~5min — run before merges
make logs-api           # Tail api-gateway logs
make shell              # Bash inside the api-gateway container
make redis-cli          # Redis CLI inside the redis container
make health             # Verify infrastructure is up
make help               # All targets
```

Everything runs in Docker. Run Python or pytest through `make` or `docker compose exec api-gateway uv run <cmd>` — never directly on the host.

## Ports (local dev)

`http://localhost:8080/` viewer SPA • `/api/v1/*` API (X-API-Key required externally) • `/docs` public Swagger • Redis `:6379` • Floci `:4566` • Prometheus `:9090` • Grafana `:3001` (admin/admin) • Jaeger `:16686` • Native Docling `:5001` (only with `make dev-gpu`).

## Code layout

```
src/
├── main.py                    # FastAPI app, middleware stack, lifespan
├── config.py                  # Pydantic Settings
├── dependencies.py            # DI factories
├── api/                       # REST endpoints — all /api/v1/*
├── services/
│   ├── pipeline_viewer.py     # Versioned pipeline + all Agent(...) call sites — the core
│   ├── document_processing_service.py
│   ├── storage_service.py     # S3 with circuit breakers
│   ├── job_service.py         # Redis job state (Lua scripts)
│   ├── queue_service.py
│   ├── pii_service.py         # Presidio
│   ├── approval_service.py
│   ├── pdf_classifier.py
│   └── metrics_service.py
├── agents/
│   ├── model_tiers.py         # ModelTier enum + backend maps (authoritative)
│   ├── model_factory.py       # get_model_for_tier() — resolve tier to backend at call time
│   └── prompts/               # Composable prompt fragments
├── workers/                   # Background tasks (PII scan, timeout checks)
├── middleware/                # Auth, logging, rate limit, metrics, CORS
├── shared/                    # Constants and data models
└── utils/                     # Retry, circuit breakers, tokens

clients/viewer/                # React pipeline viewer (Vite + TS + Tailwind)
    src/types/pipeline-viewer.ts   # PIPELINE_STAGES — canonical phase mapping

tests/
├── unit/                      # @pytest.mark.unit
├── integration/               # @pytest.mark.integration (real Redis + Floci)
├── e2e/                       # @pytest.mark.slow (real Bedrock)
└── conftest_fixtures/         # Shared fixtures — reuse, don't reinvent
```

## Common workflows

- [Iterate on a prompt](docs/how-to/iterate-on-a-prompt.md)
- [Add a new agent](docs/how-to/add-a-new-agent.md)
- [Run the test suite](docs/how-to/run-tests.md)
- [Debug a CI failure](docs/how-to/debug-ci-failures.md)
- [Add a new S3 operation](docs/how-to/add-s3-operations.md)
- [Test rate limits locally](docs/how-to/test-rate-limits.md)
- [Enable basic auth on the viewer](docs/how-to/enable-basic-auth.md)

## Conventions

- **Python tooling:** `uv` only. Never `pip` or host `python`. `uv run script.py` / `uvx tool-name`.
- **Commits:** semantic prefixes (`feat:`, `fix:`, `chore:`, `docs:`, `test:`) — history uses these consistently.
- **Prose:** British `licence` when referring to the AGPL LICENCE file. No emojis in source or docs.
- **Async everywhere:** every FastAPI endpoint and service method that touches I/O is `async`. Don't block the event loop.
- **Structured outputs:** every agent uses `output_type=<PydanticModel>` — never parse free text.
- **No hidden state:** agents don't share memory across phases. State lives in versioned pipeline outputs.
- **Security:** never log API keys, PII, or full user content. Redaction happens in middleware.

## Never do

- Do not use `localhost:6379` or `localhost:4566` in source code — services reach each other via Docker network hostnames (`redis:6379`, `floci:4566`).
- Do not run `uv run uvicorn`, `python`, `pytest`, or `uv sync` on the host.
- Do not reference private infrastructure repos, internal hostnames, AWS account IDs, or operator absolute paths in any tracked file — this is the public repo.
- Do not rename AWS resources — they stay `equalify-pdf-*` on purpose.
- Do not add features that process student records or PII beyond the existing Presidio scan.

## Releasing

Release tags (`vX.Y.Z`) are immutable archaeological records of what shipped — they're used by downstream integrators and deploy pipelines, and stale tags cause very real confusion months later. Before pushing any release tag, a single release PR must land that:

1. **Bumps the version string** in `pyproject.toml` *and* `src/main.py` (the FastAPI `version=` kwarg). These must agree — the FastAPI value is what `/docs` and the OpenAPI schema surface, so drift is user-visible.
2. **Updates `docs/reference/pipeline-phases.md`** if any pipeline step was added, renamed, or reordered since the last release. The viewer's `PIPELINE_STAGES` constant is the source of truth; the reference doc must match.
3. **Updates this file (`AGENTS.md`)** if a convention, port, command, or workflow changed. The pointer tables at the top are load-bearing — a stale row sends the next contributor to a dead file.

Only after that PR merges do you `git tag -a vX.Y.Z` the merge commit and push the tag. Never tag a commit that still has a stale version string or missing phase docs. If you discover the drift after tagging, stop, fix it in a follow-up PR, and retag — don't push through with a known-broken artifact.

## Improving these docs as you go

When you work through any of the above workflows, **leave the docs better than you found them**. This is a standing expectation, not a nice-to-have — docs drift fastest when nobody updates them during the work that proves them wrong.

Concretely:

- **Hit a step that didn't work as described?** Fix the relevant how-to before you fix the code. A broken tutorial step means the next person wastes time on the same thing.
- **Found out *why* a non-obvious design choice was made?** Add a paragraph to the matching `docs/explanation/` page. Don't let that knowledge stay in your head.
- **Discovered a stale file path, renamed symbol, or missing step?** Update `docs/reference/` immediately — reference pages are the trust surface for everything else.
- **Got surprised by a workflow quirk?** Add it to the relevant `docs/how-to/` page as a tip or gotcha. If multiple workflows hit it, promote it to a conventions entry in this file.
- **Added a new agent or pipeline step?** Update [`docs/reference/pipeline-phases.md`](docs/reference/pipeline-phases.md) and the `PIPELINE_STAGES` constant in the viewer in the same commit. These two must stay in lockstep.
- **Learned something that isn't captured anywhere?** Err on the side of writing it down. A rough paragraph in the right doc is always better than a polished paragraph that never gets written.

Prefer small, in-the-moment doc edits over batched "I'll clean up later" passes. The cost of a one-line fix now is much less than a confused contributor a month from now.

## Debugging quick-reference

| Problem | First thing to try |
|---|---|
| Stack seems broken | `make health`, then `make logs-api` |
| Redis state looks wrong | `make redis-cli`, inspect `eq-pdf:*` keys |
| S3 upload failing | Check circuit breaker state in Grafana; search api-gateway logs for `circuit` |
| Test fails only in CI | [Debug a CI failure](docs/how-to/debug-ci-failures.md) |
| Container won't start | `docker compose ps`, then `docker compose logs <service>` |
| Stale container from pre-rename squatting on ports | `docker compose -p equalify-pdf-converter down --remove-orphans` |
| Agent returning garbage | `GET /api/v1/documents/{job_id}/ledger` shows raw agent output |
| Prompt regression hunt | Enable Logfire (`LOGFIRE_ENABLED=true`) for full agent traces |

## Context7 library IDs

For MCP context7 lookups:

| Library | ID |
|---|---|
| PydanticAI | `/pydantic/pydantic-ai` |
| FastAPI | `/tiangolo/fastapi` |
| Floci | `/floci-io/floci` |
| Boto3 | `/boto/boto3` |
| Microsoft Presidio | `/microsoft/presidio` |
| Docling | `/docling-project/docling` |
| Redis | `/redis/redis-py` |
