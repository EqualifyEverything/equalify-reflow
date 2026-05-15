# Self-host Equalify Reflow on your own infrastructure

This guide is for operators who want to run Reflow somewhere other than the
bundled `make dev` stack — a single VM, a Kubernetes cluster, ECS, Nomad,
Docker Swarm, anywhere. It is deliberately vendor-neutral. Reflow uses
boto3 against the S3 API and Redis for queues; you can satisfy both with AWS,
or you can satisfy both with services you already operate.

If you're just trying things locally, use [`set-up-dev-environment.md`](set-up-dev-environment.md)
instead. Come back here when you want a real deployment.

## What you're standing up

```
                ┌───────────────────────────────────────────────┐
                │ Your container runtime (k8s / ECS / compose)  │
                │                                               │
   HTTPS in ───►│ api-gateway (FastAPI)  ──HTTP──►  docling-serve│
                │   │             ▲                             │
                └───┼─────────────┼─────────────────────────────┘
                    │             │
                    ▼             ▼
              ┌─────────┐   ┌──────────┐        ┌───────────────────────┐
              │  Redis  │   │ S3-compat│        │ AI backend            │
              │ (queue, │   │ (uploads,│        │ AWS Bedrock OR        │
              │ rate-   │   │ results, │        │ Anthropic API direct  │
              │ limit)  │   │ figures) │        │                       │
              └─────────┘   └──────────┘        └───────────────────────┘
```

Three internal components (api-gateway, docling-serve, Redis) and two external
dependencies (an S3-compatible object store and an AI provider). All of them
can be self-hosted or rented.

## Required pieces

| Component | What it does | Self-host option | Hosted option |
|---|---|---|---|
| `api-gateway` | FastAPI app + background workers | Image you build from this repo's `Dockerfile` | — |
| `docling-serve` | PDF extraction (CPU or GPU) | `quay.io/docling-project/docling-serve-cpu:latest` (CPU) or the official CUDA variant | — |
| Redis | Job queue, status, rate limits | `redis:7-alpine` | Upstash, Redis Cloud, ElastiCache, Cloud Memorystore |
| S3-compatible store | PDF uploads + markdown results + figures | [MinIO](https://min.io), [Floci](https://github.com/floci-io/floci) (dev), Garage, SeaweedFS | AWS S3, Cloudflare R2, Backblaze B2, Wasabi |
| AI provider | Pipeline agents (Claude) | — | Anthropic API (direct) or AWS Bedrock |

Two pieces — Redis and the S3-compatible store — are completely
provider-agnostic. The api uses boto3, which works against any service that
implements the S3 API: point it at MinIO or R2 by setting
`AWS_ENDPOINT_URL_S3` (and the matching `S3_PUBLIC_URL` for client-facing
presigned links).

## Container layout

`docling-serve` and `api-gateway` are separate processes that communicate over
HTTP. You have three reasonable layouts:

1. **Sidecar in the same pod / task / compose service group.** They share a
   network namespace, so the api reaches docling-serve at `http://localhost:5001`.
   Cheapest, easiest to coordinate. Use this unless you have a specific
   reason not to.
2. **Separate services on the same network.** Two deployments, point the api at
   `http://docling-serve.<your-namespace>:5001` or whatever the orchestrator
   gives you. Useful if you want to scale them independently.
3. **Separate boxes, ALB in between.** Required if docling-serve is on a GPU
   instance and the api is on something cheaper. The api speaks plain HTTP
   to an internal ALB / LB.

In all three, you set `DOCLING_SERVE_URL` to the right address. The
[`DOCLING_SERVE_URL` field description](../../src/config.py) calls out the
shape it expects in each case.

## Environment variables

The full reference is in [`src/config.py`](../../src/config.py). The
minimum set you must provide for a working deployment:

```bash
# --- API process ---
API_HOST=0.0.0.0                       # Bind interface inside the container
API_PORT=8080                          # Listen port
ENVIRONMENT=production                 # Stops dev-only endpoints from being exposed
LOG_LEVEL=INFO

# --- Data plane ---
REDIS_URL=redis://<host>:6379
S3_TEMP_BUCKET=<your-temp-bucket>
S3_RESULTS_BUCKET=<your-results-bucket>

# --- PDF extraction (REQUIRED — there is no in-process fallback) ---
DOCLING_SERVE_URL=http://localhost:5001     # sidecar; full DNS otherwise

# --- AI provider: pick ONE path ---
# (a) Anthropic API direct
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# (b) AWS Bedrock (uses ambient AWS credentials / IAM role)
# AI_PROVIDER=bedrock
# BEDROCK_REGION=us-east-1

# --- S3-compatible store (set only when NOT using AWS S3) ---
# Example: MinIO running on the same host
# AWS_ENDPOINT_URL_S3=http://minio:9000
# S3_PUBLIC_URL=https://files.yourdomain.example
# AWS_ACCESS_KEY_ID=<minio-key>
# AWS_SECRET_ACCESS_KEY=<minio-secret>
# AWS_REGION=us-east-1                   # any value; the SDK requires one

# --- Auth (pick one mode) ---
AUTH_MODE=basic                        # or 'oidc' or 'none'
AUTH_SECRET_KEY=<32+ random bytes>     # required when AUTH_MODE != none
AUTH_BASIC_USERS=<user:argon2hash;...> # required when AUTH_MODE=basic
# AUTH_OIDC_PROVIDERS=<json array>     # required when AUTH_MODE=oidc

# --- API key gate (parallel to viewer auth; always recommended for /api/v1/*) ---
ENABLE_API_KEY_AUTH=true
API_KEYS=<comma-separated tokens>
API_KEY_HEADER_NAME=X-API-Key
```

See [`docs/reference/authentication.md`](../reference/authentication.md) for
auth modes; see [`docs/how-to/enable-basic-auth.md`](enable-basic-auth.md) for
generating the argon2 hashes for `AUTH_BASIC_USERS`.

## Secrets

Six values are sensitive and should be injected via your platform's secret
mechanism (Kubernetes Secrets, ECS Secrets Manager refs, Docker secrets,
sealed-secrets, etc.) rather than baked into images or compose files:

- `ANTHROPIC_API_KEY` *(if using Anthropic direct)*
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` *(if not using an instance/role-based IAM)*
- `AUTH_SECRET_KEY`
- `AUTH_BASIC_USERS` *(contains password hashes)*
- `API_KEYS`

Everything else (bucket names, endpoint URLs, region, port numbers) is
configuration, not secret.

## Quick start: single VM with docker compose

Smallest possible production-shaped deployment. Suitable for evaluating the
project or a low-traffic internal tool. Requires a VM with ~8 GB RAM (docling
needs ~4 GB once models are loaded).

Create a `docker-compose.self-host.yml`:

```yaml
services:
  api-gateway:
    image: ghcr.io/<your-org>/equalify-reflow:latest   # or build from this repo
    restart: unless-stopped
    ports:
      - "8080:8080"
    env_file: [.env]
    environment:
      - DOCLING_SERVE_URL=http://docling-serve:5001
      - REDIS_URL=redis://redis:6379
    depends_on:
      docling-serve: {condition: service_healthy}
      redis:        {condition: service_healthy}

  docling-serve:
    image: quay.io/docling-project/docling-serve-cpu:latest
    restart: unless-stopped
    environment:
      - DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true
      - DOCLING_SERVE_ENG_LOC_SHARE_MODELS=true
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 300s          # CPU model load takes a few minutes

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes: [redis-data:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s

volumes:
  redis-data:
```

Populate `.env` with the [environment variables above](#environment-variables),
then:

```bash
docker compose -f docker-compose.self-host.yml up -d
```

Put a reverse proxy (Caddy, nginx, Traefik) in front to terminate TLS. Point
its health check at `/health/ready` (returns 503 the moment the pipeline
can't process documents) rather than `/health` (tolerant — survives docling
warmup).

## Deploying to a generic container orchestrator

The principles below apply across Kubernetes, ECS, Nomad, Swarm — substitute
the right object names for your platform.

1. **Build and push the api-gateway image** to your registry. The repo's
   `Dockerfile` builds a `production` stage:
   ```bash
   docker buildx build --platform linux/amd64 -t <your-registry>/equalify-reflow:<tag> --push .
   ```
2. **Provision Redis** (managed service or a stateful set). One shard is
   enough for low to moderate traffic; horizontal scale needs Redis Cluster.
3. **Provision the S3-compatible bucket(s)** for `S3_TEMP_BUCKET` and
   `S3_RESULTS_BUCKET`. Two separate buckets, both private. Add a lifecycle
   rule on the temp bucket to expire objects after a few days.
4. **Run docling-serve** as a sidecar in the same task/pod as api-gateway
   (recommended) or as a separate workload. CPU-only is fine for most
   workloads; size it with at least 2 vCPU and 8 GB RAM. Set
   `DOCLING_SERVE_LOAD_MODELS_AT_BOOT=true` so the first request after boot
   isn't slowed down by a cold model load.
5. **Inject secrets** through the platform mechanism. Wire env vars from
   Secrets, not from plaintext config maps.
6. **Wire health probes**:
   - liveness → `GET /health` (tolerant)
   - readiness → `GET /health/ready` (strict; 503 when any dependency is
     unreachable, including docling-serve)
7. **Run a single replica** to start. The app's background workers (PII scan,
   timeout reaper) are designed to be safe with one replica per Redis. Scaling
   beyond one needs care — see [architecture.md](../explanation/architecture.md).

## Choosing storage and Redis

Two pieces with the widest range of vendor-neutral options.

**S3-compatible storage.** Reflow does not depend on AWS-specific features.
Any service that implements the S3 API works:

- [MinIO](https://min.io) — self-hosted, single binary or k8s operator
- [Garage](https://garagehq.deuxfleurs.fr/) — self-hosted, gossip-based, designed for small clusters
- [Cloudflare R2](https://developers.cloudflare.com/r2/) — hosted, no egress fees
- [Backblaze B2](https://www.backblaze.com/cloud-storage) — hosted, low cost
- [Wasabi](https://wasabi.com) — hosted, flat pricing
- [Floci](https://github.com/floci-io/floci) — single-binary S3 mock, perfect for dev or tiny self-host
- AWS S3 — works if you happen to be on AWS

Set `AWS_ENDPOINT_URL_S3` to the service's endpoint and provide an access
key + secret key. The buckets must exist before the api starts (it doesn't
create them).

**Redis.** Any Redis 6+ that speaks RESP works. Self-hosted is fine for
single-instance deployments; managed (Upstash, ElastiCache, Memorystore) is
fine if you'd rather not run it.

## Choosing the AI provider

Set `AI_PROVIDER=anthropic` to call the Anthropic API directly (requires
`ANTHROPIC_API_KEY`), or `AI_PROVIDER=bedrock` to route through AWS Bedrock
(requires AWS credentials and Bedrock model access in your account). The
pipeline agents and the model tier mapping are identical either way —
the choice is a matter of where you'd rather see the spend, what
compliance posture you need, and what credentials you already have.

`bedrock` mode requires Claude Haiku 4.5 (and Sonnet 4.5 for some agents) to
be enabled in the chosen region. See
[`docs/reference/model-tiers.md`](../reference/model-tiers.md).

## Going further

- [`docs/explanation/architecture.md`](../explanation/architecture.md) —
  internals, why the docling-serve sidecar is required, what each background
  worker does.
- [`docs/reference/authentication.md`](../reference/authentication.md) —
  every auth knob with examples.
- [`docs/how-to/configure-sso.md`](configure-sso.md) — Entra and other OIDC
  providers, for `AUTH_MODE=oidc`.
- [`docs/reference/rate-limits.md`](../reference/rate-limits.md) — how to
  tune the public-facing throttles.

Found a step that didn't work as written? Open an issue or a PR — keeping this
guide accurate for operators outside UIC is the whole point.
