# Authentication reference

The API supports two complementary authentication paths:

1. **API key** on `/api/*` endpoints — always available, governed by `ENABLE_API_KEY_AUTH` and `API_KEYS`.
2. **Optional viewer auth** — when `AUTH_MODE != none`, browser sessions are established via username/password (`basic`) or OIDC SSO (`oidc`, PR2). API keys remain valid in parallel — programmatic clients are unaffected.

Everything outside `/api/*` (the Pipeline Viewer SPA shell, Swagger UI, OpenAPI spec, ReDoc, health checks, metrics) is publicly accessible regardless of mode.

For the rationale behind the same-origin bypass, stream-token flows, and the layered auth design, see [authentication design](../explanation/authentication-design.md).

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
6. Session Auth         (only when AUTH_MODE != none)
7. API Key Auth
8. Endpoint
```

`SessionAuthMiddleware` runs ahead of `APIKeyAuthMiddleware` so the latter can short-circuit when ``request.state.identity`` is set. Both middlewares coexist on purpose — API keys remain a parallel auth path for programmatic clients regardless of `AUTH_MODE`.

## Viewer authentication

| Variable | Default | Required when | Notes |
|---|---|---|---|
| `AUTH_MODE` | `none` | — | One of `none`, `basic`, `oidc`. `none` preserves today's behaviour. |
| `AUTH_SECRET_KEY` | — | `AUTH_MODE != none` | HMAC key for signing session and CSRF cookies. >= 32 chars; rotation invalidates all sessions. |
| `AUTH_SESSION_TTL_SECONDS` | `28800` (8h) | — | Sliding re-issue at half-life. |
| `AUTH_SESSION_COOKIE_NAME` | `reflow_session` | — | CSRF companion cookie is named `<this>_csrf`. |
| `AUTH_COOKIE_SECURE` | `true` | — | Disable only for local HTTP dev. |
| `AUTH_BASIC_USERS` | — | `AUTH_MODE=basic` | Semicolon-separated `username:argon2hash` pairs (commas collide with argon2 parameter blocks). Generate hashes with `make auth-hash-password`. |
| `AUTH_OIDC_PROVIDERS` | — | `AUTH_MODE=oidc` (PR2) | JSON array of `{id, display_name, discovery_url, client_id, client_secret, scopes?}` entries. |
| `AUTH_POST_LOGIN_REDIRECT` | `/` | — | Where to send the browser after login when no `?next=` is present. |

### Endpoints under `/api/v1/auth/*`

| Method & path | When available | Notes |
|---|---|---|
| `GET /auth/config` | always | Public. SPA reads on mount; reports `mode` and providers. |
| `POST /auth/login` | basic | JSON `{username, password}`. Sets session + CSRF cookies. |
| `GET /auth/login/{provider_id}` | oidc (PR2) | 302 to IdP authorisation endpoint with PKCE. |
| `GET /auth/callback/{provider_id}` | oidc (PR2) | Handles redirect-back, sets cookies, 302 to `next`. |
| `POST /auth/logout` | basic + oidc | CSRF required (`X-CSRF-Token` header). Clears cookies. |
| `GET /auth/me` | basic + oidc | Returns identity or 401. |

### Cookies

- `reflow_session` — `HttpOnly`, `Secure` (configurable), `SameSite=Lax`. Stateless signed cookie carrying `{sub, email, name, provider_id, issued_at, expires_at}`. The ID token itself is **not** stored in the cookie.
- `reflow_session_csrf` — NOT `HttpOnly`. HMAC of the session-cookie value. SPA echoes as `X-CSRF-Token` on non-GET requests under `/api/v1/auth/*`.

### Audit logging

When auth is on, `LoggingMiddleware` adds `user_sub`, `user_email`, `user_provider` fields to every Response log line. Auth-state transitions emit a separate structured record with `category="auth_event"` covering `login_success`, `login_failure`, `logout`, `session_expired`. Failure reasons are categorical (`invalid_password`, `csrf_mismatch`, …) — never the username attempted, never PII.

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
