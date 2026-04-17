# Authentication reference

The API has a single authentication layer — API key auth on `/api/*` endpoints. Everything else (the Pipeline Viewer SPA, Swagger UI, OpenAPI spec, ReDoc, health checks, metrics) is publicly accessible.

For the rationale behind the same-origin bypass and stream-token flows, see [authentication design](../explanation/authentication-design.md).

## Configuration

```bash
# .env — match the style in .env.example
ENABLE_API_KEY_AUTH=true
API_KEY_HEADER_NAME=X-API-Key
API_KEYS=your-secret-key-here
```

Generate a real value via the `uic-<uuid>` recipe at the bottom of this page, or follow whatever convention your deployment uses. Multiple keys are supported via a comma-separated list — useful for rolling rotations without downtime. Keys are stored as `SecretStr` internally and compared with `secrets.compare_digest()` for constant-time comparison. The header name is configurable via `API_KEY_HEADER_NAME`.

Implementation: `src/middleware/api_key_auth.py`.

## Public endpoints (no API key required)

- `/` and every SPA deep link — viewer HTML
- `/docs`, `/openapi.json`, `/redoc` — API documentation
- `/health`, `/health/ready` — load-balancer health checks
- `/metrics` — Prometheus scrape target
- `/api/dev/monitoring/*`, `/api/dev/minimal/*`, `/api/dev/pipeline-viewer/*` — public when `ENVIRONMENT=dev`
- `/api/v1/documents/{job_id}/stream?token=...` — SSE stream endpoints with a valid short-lived token
- `/lti/*` — authenticated via the Canvas LTI JWT flow, not by API key

## Stream tokens

Browser `EventSource` connections cannot send custom headers. For SSE, exchange an API key for a short-lived stream token:

1. `POST /api/v1/documents/{job_id}/stream/token` (with `X-API-Key`)
2. Server returns a single-use token with a 5-minute TTL
3. Client opens `GET /api/v1/documents/{job_id}/stream?token={token}`
4. Token is consumed on first use (`GETDEL` in Redis)

Tokens are job-scoped and deleted after first validation. Implementation: `src/services/job_service.py` creates and validates; `src/middleware/api_key_auth.py` recognises the `?token=` query parameter as an alternative credential.

## Approval endpoints

`/api/v1/approval/*` requires both an API key and a valid approval token — see [authentication design](../explanation/authentication-design.md) for the defense-in-depth rationale.

## Middleware stack order

Middleware executes in reverse registration order (last added = first executed):

```
1. CORS
2. Security Headers
3. Logging
4. Rate Limit
5. Error Handler
6. API Key Auth
7. Endpoint
```

## Client IP extraction

The API key middleware handles reverse proxy setups (AWS ALB, Nginx, Cloudflare). Priority order:

1. `X-Forwarded-For` — take the first IP
2. `X-Real-IP`
3. `request.client.host` — direct connection

Extracted IPs are included in authentication logs for audit trails.

## Generating API keys

```bash
python3 -c "import uuid; print(f'uic-{uuid.uuid4()}')"
```

## Testing

Unit tests: `tests/unit/middleware/test_api_key_auth.py`, `tests/unit/services/test_job_service.py::TestStreamTokens`

Integration tests: `tests/integration/api/test_api_authentication.py`, `tests/integration/api/test_stream_auth.py`

Quick manual check (requires `make dev`):

```bash
# No key → 401
curl http://localhost:8080/api/v1/documents/test-id

# Valid key → 200 (or 404 if job not found)
curl -H "X-API-Key: $API_KEY" http://localhost:8080/api/v1/documents/test-id

# Swagger UI — public, no prompt
open http://localhost:8080/docs

# Health — public
curl http://localhost:8080/health
```
