# Environment Setup

Local development setup for Equalify Reflow.

## TL;DR

```bash
make dev          # Everything just works
```

The stack (API Gateway, Redis, LocalStack-as-S3, docling-serve, observability) runs in Docker Compose and hot-reloads on source changes.

## Prerequisites

- Docker (Desktop or Engine)
- `uv` (Python package manager) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Optional: AWS credentials for the current Bedrock-backed AI path (see "AI Model Backend" below). A provider-abstraction effort is in progress that will let you run against Anthropic direct or other backends instead; until that lands, Bedrock is the only exercised code path.

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

## Storage (LocalStack)

The current default for local S3 is LocalStack, running inside the Docker network. The app talks to it at `localstack:4566` automatically. You almost never need to interact with LocalStack directly — the app and tests handle it.

For occasional host-side debugging, see `make localstack-debug` for AWS CLI examples against LocalStack.

LocalStack is the current implementation, not a hard requirement of the design. A provider-abstraction effort is in progress that will introduce a `StorageProvider` interface and a local-filesystem backend so contributors can run the stack without LocalStack at all. Once that lands, LocalStack becomes opt-in (via an override compose file) rather than the default.

## AI Model Backend

The pipeline currently uses AWS Bedrock (Claude Haiku 4.5) for text correction and structure analysis. Bedrock is the current implementation — a provider-abstraction effort is in progress that will introduce an `AIProvider` interface, making Anthropic direct and other backends pluggable at startup via environment variables.

For local dev against real Bedrock today:

1. Configure an AWS SSO profile locally. The Makefile's historical default profile name is `uic`; override with `AWS_PROFILE=<name> make dev` to use any other profile.
2. Run `aws sso login --profile <name>` when your token expires.
3. `make dev` exports the credentials into the API container automatically.

If Bedrock credentials are not available, the stack still starts but LLM-dependent code paths will fail until the Anthropic-direct provider lands.

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
