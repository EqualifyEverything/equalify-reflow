# Environment Setup

Local development setup for the Equalify PDF Converter.

## TL;DR

```bash
make dev          # Everything just works
```

The stack (API Gateway, Redis, LocalStack-as-S3, docling-serve, observability) runs in Docker Compose and hot-reloads on source changes.

## Prerequisites

- Docker (Desktop or Engine)
- `uv` (Python package manager) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Optional: AWS SSO profile for Bedrock access (see "AI / Bedrock" below)

## Daily Workflow

```bash
make dev          # Start everything
make test-fast    # Unit tests (~30s)
make logs-api     # API logs
make shell        # Exec into API container
make down         # Stop everything
```

See `make help` for the full command list.

**Hot reload:** Edit files under `src/` on the host; the API container picks them up automatically.

## GPU-Accelerated Development (Apple Silicon)

On macOS with Apple Silicon, docling-serve can run natively with MPS (Metal Performance Shaders), roughly 3-5x faster than the CPU-only Docker container.

**One-time setup:**

```bash
make docling-install
```

**Workflow:**

```bash
make dev          # Auto-detects native docling if installed, falls back to Docker
```

`make dev-docker` forces the CPU-only Docker path regardless.

## LocalStack

LocalStack provides a local S3 (and related AWS APIs) inside the Docker network. The app talks to it at `localstack:4566` automatically. You almost never need to interact with LocalStack directly — the app and tests handle it.

For occasional host-side debugging, see `make localstack-debug` for AWS CLI examples against LocalStack.

## AI / Bedrock

The pipeline uses AWS Bedrock (Claude Haiku) for text correction. For local dev against real Bedrock:

1. Configure an AWS SSO profile locally (any name works — the Makefile defaults to `uic`, override with `AWS_PROFILE=<name> make dev`).
2. Run `aws sso login --profile <name>` when your token expires.
3. `make dev` exports the credentials into the API container automatically.

If Bedrock credentials are not available, the stack still starts but LLM-dependent code paths will fail.

## Environment Variables

The app reads configuration via Pydantic Settings (see `src/config.py`). The Docker Compose files wire sensible defaults for local dev; you generally don't need a `.env` file unless you want to override something.

**Never source `.env` into your shell** — it's Docker Compose-only. `AWS_ENDPOINT_URL=http://localstack:4566` only resolves inside the Docker network.

## Troubleshooting

- **"Could not connect to endpoint URL: http://localstack:4566"** — you're running a command from the host that expects Docker DNS. Use `docker exec` or the `localstack` profile mapped to `http://localhost:4566`.
- **Bedrock calls failing** — SSO token expired. `aws sso login --profile <name>` and restart the stack.
- **Dependency changes not picked up** — `make clean && make dev` rebuilds the image.

## Platform Support

- macOS (native)
- Linux (native)
- Windows (WSL2 + Docker Desktop)
