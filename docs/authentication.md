# Authentication

The API has a single authentication layer — API key auth on `/api/*` endpoints. Everything else (the Pipeline Viewer SPA, Swagger UI, OpenAPI spec, ReDoc, health checks, metrics) is publicly accessible.

## API Key Authentication

**Purpose:** Secure API endpoints for programmatic access
**Header:** `X-API-Key` (configurable via `API_KEY_HEADER_NAME`)
**Implementation:** `src/middleware/api_key_auth.py`

### Configuration

```bash
# .env
ENABLE_API_KEY_AUTH=true
API_KEY_HEADER_NAME=X-API-Key
API_KEYS=uic-2bd2c716-bc67-4032-ba66-e4f35c441759
```

### Public endpoints (no API key required)

- `/` and every SPA deep link — viewer HTML
- `/docs`, `/openapi.json`, `/redoc` — API documentation
- `/health`, `/health/ready` — load-balancer health checks
- `/metrics` — Prometheus scrape target
- `/api/dev/monitoring/*`, `/api/dev/minimal/*`, `/api/dev/pipeline-viewer/*` — public when `ENVIRONMENT=dev`
- `/api/v1/documents/{job_id}/stream?token=...` — SSE stream endpoints with a valid token (see below)
- `/lti/*` — authenticated via the Canvas LTI JWT flow, not by API key

### Same-origin viewer fetches

The Pipeline Viewer SPA is served from the same origin as the API. Its JS calls `/api/v1/*` without injecting an `X-API-Key` header; the middleware recognises these as same-origin by checking the browser-set `Sec-Fetch-Site: same-origin` header combined with the absence of an API key. This is safe because:

1. CORS prevents external sites from reading responses or forging cross-origin requests as same-origin.
2. External API clients always send `X-API-Key` and take the normal-auth branch.
3. `Sec-Fetch-Site` is a browser-controlled header — page scripts cannot set or spoof it.

See `_is_demo_ui_request` in `src/middleware/api_key_auth.py`.

### Stream token authentication

**Purpose:** Allow browser `EventSource` connections without exposing API key in URLs

Browser's native EventSource API cannot send custom headers. Stream tokens provide a secure alternative:

1. Client requests token via `POST /api/v1/documents/{job_id}/stream/token` (with API key header)
2. Server returns short-lived, single-use token (5-minute TTL)
3. Client connects to `GET /api/v1/documents/{job_id}/stream?token={token}`
4. Token is consumed on first use (`GETDEL` in Redis)

**Security properties:**

- Single-use — token deleted after first validation
- Job-scoped — token only valid for the specific `job_id`
- Short TTL — 5-minute expiration via Redis
- Not logged — short-lived tokens are less sensitive than API keys

**Implementation:** `src/services/job_service.py` (create/validate), `src/middleware/api_key_auth.py` (bypass)

### Approval endpoints

Approval endpoints (`/api/v1/approval/*`) require BOTH:

1. **API key** (Layer 1) — ensures only authorized systems can make requests
2. **Approval token** (Layer 2) — ensures the requester has permission for the specific job

Both layers must pass for access — defense in depth.

### Security features

- Constant-time comparison (`secrets.compare_digest()`) prevents timing attacks
- Multiple keys supported (comma-separated in env var)
- Keys stored as `SecretStr` to prevent accidental logging
- Whitespace automatically stripped from configured keys
- API keys cached at middleware initialization for optimal performance (loaded once, not per-request)
- Authentication events logged for security auditing (missing/invalid keys)

### Client IP extraction

The API key middleware supports reverse proxy setups:

- Checks `X-Forwarded-For` header (load balancers, CDNs)
- Falls back to `X-Real-IP` header (nginx)
- Uses direct connection IP as final fallback
- Extracted IP included in authentication logs for audit trails

### Generating API keys

```bash
python3 -c "import uuid; print(f'uic-{uuid.uuid4()}')"
```

## Middleware stack order

Middleware executes in reverse order of registration (last added = first executed):

```
1. CORS
2. Security Headers
3. Logging
4. Rate Limit
5. Error Handler
6. API Key Auth
7. Endpoint
```

## Testing authentication

**Unit tests:**

- `tests/unit/middleware/test_api_key_auth.py` — API key middleware
- `tests/unit/services/test_job_service.py::TestStreamTokens` — stream token methods

**Integration tests:**

- `tests/integration/api/test_api_authentication.py` — full auth flow
- `tests/integration/api/test_stream_auth.py` — stream token authentication

**Manual testing:**

```bash
# Start services
make dev

# Test API endpoint without key (should fail)
curl http://localhost:8080/api/v1/documents/test-id
# → 401 Unauthorized

# Test with valid key (should work or 404 if job not found)
curl -H "X-API-Key: uic-2bd2c716-bc67-4032-ba66-e4f35c441759" \
  http://localhost:8080/api/v1/documents/test-id

# Test Swagger UI (public, no prompt)
open http://localhost:8080/docs

# Test health endpoint still works
curl http://localhost:8080/health
# → 200 OK (no auth required)
```

## Notes for integration tests

Integration tests use the app instance from `src.main`, which is configured at startup based on `.env` settings. The API key middleware is enabled by default; there is no separate docs-auth gate any more.

```bash
# .env (required for integration tests)
ENABLE_API_KEY_AUTH=true
API_KEYS=uic-2bd2c716-bc67-4032-ba66-e4f35c441759
```
