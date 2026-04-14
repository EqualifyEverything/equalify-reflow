# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. It is intentionally scoped to Claude-Code-session conventions: commands, ports, and things not to do. For pipeline architecture and agent/prompt work, follow the "See also" links below.

## See also

- [AGENTS.md](AGENTS.md) — canonical reference for the AI agents in the pipeline, prompt iteration workflow, and model tier selection. If you are working on anything in `src/agents/` or touching prompts in `src/services/pipeline_viewer.py`, read AGENTS.md first.
- [CONTRIBUTING.md](CONTRIBUTING.md) — developer workflow, testing tiers, and conventions.
- [docs/architecture.md](docs/architecture.md) — overall system design and data flow.

## Project Overview

Equalify Reflow transforms PDF documents into accessible, semantic markup. The system processes course materials only — strict architectural boundary against student records or PII.

Architecture overview lives in [AGENTS.md](AGENTS.md) and [docs/architecture.md](docs/architecture.md).

## Essential Commands

| Command | Purpose | When to Use |
|---------|---------|-------------|
| `make dev` | Start all services (auto-detects GPU) | Default for development |
| `make dev-docker` | Start with Docker docling (CPU only) | Force CPU mode |
| `make down` | Stop all services | End of session |
| `make test-fast` | Run unit tests (~30s) | Before commits |
| `make test-integration` | Run integration tests (~2min) | Before PRs |
| `make test-e2e` | Run E2E tests (~5min) | Before merges |
| `make logs` / `make logs-api` | View service logs | Debugging |
| `make shell` | Access container bash | Interactive debugging |
| `make redis-cli` | Connect to Redis CLI | Redis operations |
| `make health` | Verify infrastructure | Health checks |
| `make coverage` | Run tests with coverage | Coverage reports |
| `make docling-install` | Install native docling-serve | One-time GPU setup |

## Never Do These

- DO NOT run `uv run uvicorn` directly on host
- DO NOT install dependencies locally with `uv sync`
- DO NOT use `localhost:6379` in code (use `redis:6379`)
- DO NOT run `python` or `pytest` directly on host

## Default Ports

**API Gateway:** `http://localhost:8080`
- FastAPI docs: `http://localhost:8080/docs`
- Reflow Viewer: `http://localhost:8080/viewer`

**Other Services:**
- Redis: `localhost:6379`
- LocalStack: `localhost:4566`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`
- Jaeger: `http://localhost:16686`
- Native Docling: `localhost:5001` (when using `make dev-gpu`)

## Development Workflow

1. `make dev` — starts all services in Docker
2. Edit code in `src/` on host machine
3. Code auto-reloads in container (hot reload enabled)
4. `make test-fast` — quick feedback before commit
5. `make shell` — debug inside container if needed

## Detailed Documentation

- [Architecture](.claude/docs/architecture.md) — system design, data flow, service layer, AWS Bedrock setup
- [Authentication](.claude/docs/authentication.md) — API key auth, docs auth, middleware stack
- [Testing](.claude/docs/testing.md) — 3-tier strategy, fixtures, markers, running tests
- [S3 Resilience](.claude/docs/s3-resilience.md) — circuit breakers, retry logic, metrics
- [Development](.claude/docs/development.md) — adding features, debugging, common issues
- [Environment Setup](docs/environment-setup.md) — complete setup guide
- [CI/CD](docs/ci-cd.md) — GitHub Actions workflows

## Context7 Library IDs

For MCP integration, use these library IDs:

- **PydanticAI:** `/pydantic/pydantic-ai`
- **FastAPI:** `/tiangolo/fastapi`
- **LocalStack:** `/localstack/localstack`
- **Boto3:** `/boto/boto3`
- **Microsoft Presidio:** `/microsoft/presidio`
- **Docling:** `/docling-project/docling`
- **Redis:** `/redis/redis-py`
