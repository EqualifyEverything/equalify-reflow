# Equalify Reflow — Agent Guide

> **`CLAUDE.md` is a symlink to this file.** Edit `AGENTS.md` (the real file) and both update. Keep it under ~220 lines so it stays cheap to load into an agent's context.

Equalify Reflow is an open-source FastAPI monolith that converts PDF documents into accessible, semantic markdown. It combines IBM Docling extraction with a multi-agent PydanticAI correction pipeline — currently AWS Bedrock + Claude Haiku 4.5; a provider abstraction is in progress. Originally built with the University of Illinois Chicago for course-material accessibility; now maintained for any organisation that needs accessible document conversion.

**Domain constraint:** course materials only. Do not add features that process student records or PII beyond the existing Presidio scan.

## Essential commands

| Command | Purpose |
|---|---|
| `make dev` | Start the full dev stack (auto-detects GPU for docling) |
| `make dev-docker` | Start with CPU-only docling |
| `make down` | Stop all services |
| `make test-fast` | Unit tests, ~30s — run before every commit |
| `make test-integration` | Integration tests, ~2min — run before PRs |
| `make test-e2e` | End-to-end tests, ~5min — run before merges |
| `make logs-api` | Tail api-gateway logs |
| `make shell` | Bash inside the api-gateway container |
| `make redis-cli` | Redis CLI inside the redis container |
| `make health` | Verify infrastructure is up |
| `make coverage` | Tests with coverage report |
| `make help` | All available targets |

Everything runs in Docker. Run Python or pytest through `make` or `docker compose exec api-gateway uv run <cmd>` — never directly on the host.

## Ports and URLs

| Service | URL | Notes |
|---|---|---|
| API Gateway | http://localhost:8080 | FastAPI app |
| Swagger UI | http://localhost:8080/docs | User: `dase`, pass: `a11y` |
| Reflow Viewer | http://localhost:8080/viewer | Pipeline viewer SPA |
| Redis | localhost:6379 | In-app code uses `redis:6379` |
| LocalStack | localhost:4566 | S3 + CloudWatch emulation |
| Prometheus | http://localhost:9090 | Metrics scraping |
| Grafana | http://localhost:3001 | Dashboards, `admin/admin` |
| Jaeger | http://localhost:16686 | Distributed tracing |
| Native Docling | localhost:5001 | Only when using `make dev-gpu` |

## Never do

- Do not use `localhost:6379` or `localhost:4566` in source code — services reach each other via Docker network hostnames (`redis:6379`, `localstack:4566`)
- Do not run `uv run uvicorn`, `python`, `pytest`, or `uv sync` on the host — everything runs in containers
- Do not reference private infrastructure repos, internal hostnames, AWS account IDs, or operator absolute paths in any tracked file — this is the public repo
- Do not rename AWS resources — they stay `equalify-pdf-*` on purpose
- Do not add features that process student records or PII beyond the existing Presidio scan

## Architecture at a glance

FastAPI monolith. A PDF enters at `POST /api/v1/documents/submit`, gets PII-scanned with Microsoft Presidio, then runs through `PipelineViewerService` — a versioned pipeline that alternates deterministic extraction (Docling, pypdfium2) with AI correction (PydanticAI agents on AWS Bedrock). Each phase writes a new version of the document; prior versions are preserved in S3 so any phase can be re-run without reprocessing from scratch. Job state and rate-limiting live in Redis; progress streams to clients over SSE.

See [docs/architecture.md](docs/architecture.md) for the full service diagram, data flows, circuit-breaker strategy, and Bedrock setup.

### Code layout

```
src/
├── main.py                    # FastAPI app, middleware stack, lifespan
├── config.py                  # Settings (env vars, validators)
├── dependencies.py            # DI factories
├── api/                       # REST endpoints — all /api/v1/*
├── services/                  # Business logic
│   ├── pipeline_viewer.py     # Versioned pipeline + all agent call sites
│   ├── document_processing_service.py   # Pipeline orchestration
│   ├── storage_service.py     # S3 upload/download with circuit breakers
│   ├── s3_url_service.py      # URL generation (LocalStack vs AWS)
│   ├── s3_cleanup_service.py  # File deletion, best-effort
│   ├── job_service.py         # Redis job state (Lua scripts)
│   ├── queue_service.py       # Redis queues
│   ├── pii_service.py         # Presidio PII detection
│   ├── approval_service.py    # Token-based PII approval
│   ├── pdf_classifier.py      # Scanned/digital/malformed classification
│   └── metrics_service.py     # Prometheus metrics
├── agents/                    # PydanticAI prompt modules + output models
│   ├── model_tiers.py         # ModelTier enum + Bedrock inference profile IDs
│   ├── structure_analysis.py, heading_reconciliation.py
│   ├── boundary_fix.py, footnote_relocation.py
│   ├── table_reconstruction.py, list_reconstruction.py, image_description.py
│   └── prompts/procedures/    # Composable prompt fragments for page correction
├── workers/                   # Background tasks (PII scan, timeout checks)
├── middleware/                # Auth, logging, rate limit, metrics, CORS
├── shared/                    # Constants and data models
└── utils/                     # Retry logic, circuit breakers, tokens

tests/
├── unit/                      # @pytest.mark.unit — no network, mocked I/O
├── integration/               # @pytest.mark.integration — real Redis + LocalStack
├── e2e/                       # @pytest.mark.slow — full stack + real fixtures
└── conftest_fixtures/         # Shared fixtures (clients, data, redis)
```

### Key files to know

| File | Why it matters |
|---|---|
| `src/main.py` | FastAPI app construction, middleware order, lifespan — startup behaviour |
| `src/services/pipeline_viewer.py` | Every pipeline phase and every `Agent(...)` call site — the core |
| `src/agents/model_tiers.py` | `ModelTier` enum → Bedrock inference profile IDs (single source of truth) |
| `src/agents/prompts/procedures/page_correction/` | Composable prompt fragments for per-page correction |
| `src/config.py` | All env-var-driven settings + validators |
| `src/dependencies.py` | DI factories (storage, job, queue) |
| `Makefile` | Every dev command worth knowing |
| `docker-compose.yml` + `docker-compose.dev.yml` | Stack definition; hot reload via `./src` bind mount |
| `tests/conftest_fixtures/` | Shared pytest fixtures — reuse, don't reinvent |
| `pyproject.toml` | Dependencies, pytest markers, coverage config |

## The pipeline

The pipeline runs in `src/services/pipeline_viewer.py`. Each phase is an `async def _step_*` method that reads the previous version's markdown and writes a new one.

| # | Phase | AI? | Purpose |
|---|---|---|---|
| 1 | Docling extraction | No | PDF → markdown + page images via IBM Docling |
| 1a | Docling OCR re-extraction | No | Conditional: re-run with Tesseract when classifier flags a scanned document |
| 2 | Structure analysis | Yes | Identify headings, footnotes, page types, document structure |
| 3 | Heading reconciliation | Yes | Reconcile heading candidates against the outline |
| 4 | Heading levels | Yes | Normalise heading hierarchy (H1 → H2 → H3) |
| 5 | Page content corrections | Yes | Per-page corrections; invokes **table**, **list**, and **image** subagents |
| 6 | Code block languages | Yes | Identify programming languages in fenced code blocks |
| 7 | Cross-page boundary fixes | Yes | Rejoin split content; invokes **footnote relocation** subagent |
| 8 | Final cleanup | No | Normalise whitespace, lint markdown |

Eight distinct `Agent(...)` call sites total, each backed by a system prompt in `src/agents/`.

## Model tiers

Two tiers defined in `src/agents/model_tiers.py`:

- **`ModelTier.EFFICIENT`** — Claude Haiku 4.5. Default for every call site today. Fast, cheap, validated against integration fixtures.
- **`ModelTier.REASONING`** — Claude Sonnet 4.5. Reserved for heavier analysis. Not wired into pipeline call sites yet; available for new agents that measurably benefit.

Both tiers resolve to Bedrock inference profile IDs (the `us.` prefix is required for Claude 4.5 models). Default to Efficient; promote to Reasoning only when fixtures prove it's needed.

## Iterating on a prompt

1. **Find the prompt.** Each agent's system prompt lives in `src/agents/*.py` as a module-level constant. Grep for the constant name or the `Agent(` call site in `pipeline_viewer.py`.
2. **Reproduce the failing case.** Start the stack with `make dev`, upload a small PDF via the pipeline viewer at http://localhost:8080/viewer, and step through per-phase output.
3. **Edit the prompt.** Hot reload picks up changes inside the running container.
4. **Re-run the pipeline.** Because each phase is versioned, you can resubmit the same document and inspect the diff for just the phase you changed.
5. **Run the tests.** `make test-fast` for quick signal, `make test-integration` for behaviour parity, `make test-e2e` for regression safety. If a prompt change breaks tests, the fix is usually a coordinated update to both the prompt and the fixtures.
6. **Include the diff in the PR body.** Reviewers should see the before/after markdown change, not just the prompt change.

## Adding a new agent

1. Create a new module under `src/agents/` with your system prompt constant and Pydantic output model.
2. Add a call site in the appropriate `_step_*` method in `src/services/pipeline_viewer.py`.
3. Resolve the model through `MODEL_TIER_MAP[ModelTier.EFFICIENT]` — do not hardcode model IDs.
4. Add unit tests (mock the model response) and integration tests (real agent against a small fixture).
5. Update the pipeline table above.

## Provider abstraction (in progress)

Today every call site uses `BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])` directly. A `src/providers/ai/` abstraction is planned that introduces an `AIProvider` protocol so contributors can run the pipeline against Anthropic direct, Bedrock, or other providers without editing agent call sites. Until that lands, new agent work should follow the existing pattern — the rewrite will touch all call sites in a single pass.

## Running tests

Three tiers, each with a pytest marker defined in `pyproject.toml`:

- **`make test-fast`** → `@pytest.mark.unit` — no network, all external I/O mocked, parallelized (`-n auto`), <100ms per test. ~30s total.
- **`make test-integration`** → `@pytest.mark.integration` — real Redis + LocalStack S3, AI responses still mocked. ~2min.
- **`make test-e2e`** → `@pytest.mark.slow` — full stack with real Bedrock calls against small fixtures. ~5min.

Reuse fixtures from `tests/conftest_fixtures/` rather than inventing new ones.

## Conventions

- **Python tooling:** `uv` only. Never `pip` or system `python`. `uv run script.py` for scripts; `uvx tool-name` for tools.
- **Commits:** semantic prefixes (`feat:`, `fix:`, `chore:`, `docs:`, `test:`) — history uses these consistently.
- **Prose:** British `licence` when referring to the AGPL LICENCE file (matches PRD docs). No emojis in source or docs.
- **Async everywhere:** every FastAPI endpoint and service method that touches I/O is `async`. Do not block the event loop.
- **Structured outputs:** every agent uses `output_type=<PydanticModel>` — never parse free text from agent responses.
- **No hidden state:** agents do not share memory across phases. State lives in versioned pipeline outputs.
- **Security:** never log API keys, PII, or full user content. Redaction happens in middleware.

## Debugging quick-reference

| Problem | First thing to try |
|---|---|
| Stack seems broken | `make health`, then `make logs-api` |
| Redis state looks wrong | `make redis-cli`, inspect `eq-pdf:*` keys |
| S3 upload failing | Check circuit breaker state in Grafana; search api-gateway logs for `circuit` |
| Test fails only in CI | Run `make test-integration` locally against LocalStack — often an ordering issue |
| Container won't start | `docker compose ps`, then `docker compose logs <service>` |
| Stale container from pre-rename squatting on ports | `docker compose -p equalify-pdf-converter down --remove-orphans` |
| Agent returning garbage | `GET /api/v1/documents/{job_id}/ledger` shows raw agent output |
| Prompt regression hunt | Enable Logfire (`LOGFIRE_ENABLED=true`) for full agent traces |

## Documentation index

| Doc | Purpose |
|---|---|
| [README.md](README.md) | Project overview, quick start, features |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev workflow, branch strategy, PR conventions |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards |
| [SECURITY.md](SECURITY.md) | Vulnerability reporting |
| [LICENSE](LICENSE) | AGPL-3.0-or-later |
| [docs/architecture.md](docs/architecture.md) | Service diagram, data flows, service layer |
| [docs/environment-setup.md](docs/environment-setup.md) | Full local setup guide |
| [docs/ci-cd.md](docs/ci-cd.md) | GitHub Actions, test tiers, CI gates |
| [docs/rate-limiting.md](docs/rate-limiting.md) | Rate-limit configuration |
| [docs/authentication.md](docs/authentication.md) | API key auth, docs auth, middleware stack |
| [docs/s3-resilience.md](docs/s3-resilience.md) | Circuit breakers, retry logic, metrics |
| [docs/testing.md](docs/testing.md) | Test strategy, fixtures, markers |
| [docs/development.md](docs/development.md) | Adding features, debugging, common issues |

## Context7 library IDs

For MCP context7 lookups:

| Library | ID |
|---|---|
| PydanticAI | `/pydantic/pydantic-ai` |
| FastAPI | `/tiangolo/fastapi` |
| LocalStack | `/localstack/localstack` |
| Boto3 | `/boto/boto3` |
| Microsoft Presidio | `/microsoft/presidio` |
| Docling | `/docling-project/docling` |
| Redis | `/redis/redis-py` |
