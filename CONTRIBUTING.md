# Contributing to Equalify Reflow

Thanks for your interest in contributing. Equalify Reflow is an open source project that converts PDF documents into accessible, semantic markdown. Whatever brought you here — a bug fix, a prompt improvement, a new provider integration, or curiosity about how the pipeline works — you're welcome.

This document is the 5-minute path from "just cloned it" to "my change is in a PR".

## Quick start

### Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+)
- **Git**
- An **Anthropic API key** (or AWS Bedrock credentials) — the pipeline needs a Claude model to run end-to-end. Local structural tests don't require this.

No AWS account or Python install on the host is required; everything runs in Docker.

### Get the stack running

```bash
git clone https://github.com/EqualifyEverything/equalify-reflow.git
cd equalify-reflow
make dev
curl http://localhost:8080/health
```

`make dev` starts the API, Redis, LocalStack (S3 emulation), Docling, Prometheus, Grafana, and Jaeger. Code in `src/` hot-reloads automatically inside the container.

Open the Swagger UI at http://localhost:8080/docs (user: `dase`, password: `a11y`) and the Pipeline Viewer at http://localhost:8080/viewer.

### Verify your environment

```bash
make health          # Infrastructure health check
make test-fast       # Unit tests (~30s)
```

If the unit tests pass, you're good to start making changes.

## Architecture orientation

Before making a non-trivial change, skim these three documents — they'll save you from redoing work:

- [README.md](README.md) — project overview, what Reflow does, what it doesn't
- [docs/architecture.md](docs/architecture.md) — service diagram, data flows, pipeline phases, circuit-breaker strategy
- [AGENTS.md](AGENTS.md) — the canonical guide for agents (human and AI) working on this repo. Covers commands, code layout, key files, pipeline phases, prompt iteration, conventions, and debugging

`AGENTS.md` is the single source of truth for day-to-day conventions. `CLAUDE.md` is a symlink to it — editing `AGENTS.md` updates both.

## Making changes

1. **Create a feature branch.** Name it `feat/short-description`, `fix/short-description`, or `docs/short-description`.
2. **Edit code in `src/`.** Changes hot-reload inside the running container.
3. **Run the fast tests frequently:** `make test-fast`.
4. **Run integration tests before you open a PR:** `make test-integration`.
5. **Run E2E tests before merging if your change touches the pipeline:** `make test-e2e`.
6. **Commit** using Conventional Commits (see below).
7. **Push and open a PR.** The PR template will prompt you for the essentials.

### Running everything in containers

Every Python command runs inside the `api-gateway` container. Do not run `uv`, `python`, or `pytest` on the host.

```bash
# Good
make test-fast
make shell
docker compose exec api-gateway uv add <package>

# Bad — will fail or produce stale results
uv run uvicorn ...
python ...
pytest ...
```

Service hostnames inside the Docker network are `redis`, `localstack`, `api-gateway`, etc. Never hardcode `localhost:6379` or `localhost:4566` in source code — use the Docker hostnames (`redis:6379`, `localstack:4566`) so tests and production both work.

## Testing

The project uses a three-tier test strategy with pytest markers. Every new test must declare a tier via `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.slow`.

| Tier | Command | When to run | Notes |
|---|---|---|---|
| Unit | `make test-fast` | Before every commit | No network, all external I/O mocked. <100ms per test. Parallelized. |
| Integration | `make test-integration` | Before opening a PR | Real Redis + LocalStack S3; AI responses still mocked. |
| E2E | `make test-e2e` | Before merge for pipeline changes | Full stack with real Bedrock calls against small fixtures. |

### Shared fixtures

Fixtures live in `tests/conftest_fixtures/`. Reuse them — don't reinvent. The most common ones:

```python
from tests.conftest_fixtures import (
    mock_redis_client,       # AsyncMock for Redis operations
    mock_s3_client,          # MagicMock for S3 operations
    mock_ai_service,         # AsyncMock for the AI layer
    mock_presidio_analyzer,  # MagicMock for PII detection
    generate_job_id,         # UUID factory
    create_test_pdf_content, # Minimal valid PDF bytes
)
```

Use `@pytest.mark.parametrize` for multi-scenario coverage instead of copy-pasting test functions.

### Coverage expectations

- Target: >80% overall
- New code should ship with tests
- Bug fixes should ship with a regression test
- Critical business logic should be near 100%

Run `make coverage` for a report.

## Commit conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<optional body>

<optional footer>
```

**Types used in this repo:**

- `feat:` — user-facing feature
- `fix:` — bug fix
- `docs:` — documentation changes
- `test:` — test additions or refactors
- `refactor:` — internal refactor, no behaviour change
- `chore:` — tooling, build, or housekeeping
- `ci:` — CI/CD pipeline changes

**Example:**

```
feat(api): add document submission endpoint

Implements POST /api/v1/documents/submit with PDF validation,
S3 storage, and Redis queue integration.

Closes #123
```

Keep PRs focused. A single PR that changes one thing is much easier to review than a bundle.

## Pull requests

When you open a PR, the template at [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) will populate automatically. Fill in the summary, the related issue (or explain why there isn't one), the type of change, and the testing section. The checklist is a real checklist — please tick the boxes you've actually completed.

**Review expectations:**

- CI must be green before a PR can merge
- Coverage should not regress significantly
- For pipeline / prompt changes, include a before/after markdown diff (or a link to a Pipeline Viewer session) so reviewers can see the behaviour change, not just the prompt edit
- Keep PRs small; if your change touches more than ~500 lines, consider whether it can be split

## Working on AI agents and prompts

The accuracy of Reflow is driven by a handful of PydanticAI agents in `src/agents/` and call sites in `src/services/pipeline_viewer.py`. The workflow is:

1. **Find the prompt** as a module-level constant in `src/agents/*.py`.
2. **Reproduce the failing case** via the Pipeline Viewer at http://localhost:8080/viewer.
3. **Edit the prompt** (hot reload picks up the change inside the container).
4. **Re-run the pipeline** against the same document — each phase is versioned, so you can see the diff for just the phase you changed.
5. **Run `make test-fast`**, then `make test-integration`, then `make test-e2e` if the change is material.
6. **Include the before/after markdown diff in your PR body** so reviewers can evaluate the behaviour change.

See the "Agents and prompts" section of [AGENTS.md](AGENTS.md) for the full guide, including model tiers and how to add a new agent.

## Adding a new provider (storage or AI model)

Reflow is in the middle of introducing a `StorageProvider` / `AIProvider` abstraction. Until that lands, new provider proposals should go through the [new provider issue template](.github/ISSUE_TEMPLATE/new_provider.yml) so we can track integration status and avoid duplicate work.

Once the abstraction lands, implementations will live in `src/providers/storage/` and `src/providers/ai/`. Each new provider needs: a module implementing the Protocol, registration in `src/dependencies.py`, config fields in `src/config.py`, unit + integration tests, and a documentation update.

## Code standards

- **Python:** `uv` only, never `pip` or system `python`. Type hints on public functions. Docstrings on public functions and classes. Max line length 100.
- **Imports:** standard library → third-party → local, one group per block.
- **Async:** every FastAPI endpoint and service method that touches I/O is `async`. Don't block the event loop.
- **Structured outputs:** every agent uses PydanticAI's `output_type=<PydanticModel>` — never parse free text from agent responses.
- **Security:** never log API keys, PII, or full user content. Redaction happens in middleware.
- **Documentation:** if your change touches user-visible behaviour, update `README.md`. If it touches architecture, update `docs/architecture.md`. If it touches dev workflow, update `AGENTS.md`.

## Troubleshooting

### Tests failing locally but passing in CI (or vice versa)

Usually an ordering or fixture-state issue. `make test-integration` locally against LocalStack is the closest mirror of CI.

### Container won't start

```bash
docker compose ps
docker compose logs <service>
make down && make dev
```

### Stale container from before the repo was renamed

If ports are taken but `docker compose ps` shows nothing:

```bash
docker compose -p equalify-pdf-converter down --remove-orphans
make dev
```

### Pipeline agent returning garbage

Check the change ledger at `GET /api/v1/documents/{job_id}/ledger` for raw agent output. Enable Logfire (`LOGFIRE_ENABLED=true`) for full agent traces.

### More detail

See [AGENTS.md](AGENTS.md) for the full debugging quick-reference table.

## Getting help

- **Documentation:** browse the [`docs/`](docs/) directory and [AGENTS.md](AGENTS.md)
- **Existing issues:** search [GitHub Issues](https://github.com/EqualifyEverything/equalify-reflow/issues) before opening a new one
- **Security reports:** follow [SECURITY.md](SECURITY.md) — never report vulnerabilities via public issues

## Code of conduct

All contributors and maintainers are expected to follow the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). Please read it before participating.

## Licence

By contributing to Equalify Reflow you agree that your contributions will be licensed under the AGPL-3.0-or-later (see [LICENSE](LICENSE)).

Thank you for contributing.
