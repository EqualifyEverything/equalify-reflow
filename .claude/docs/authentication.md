# Authentication

The API implements two independent authentication layers for different security purposes.

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

### Public Endpoints (no API key required)

- `/health`, `/health/ready`, `/metrics` - Always public for monitoring
- `/api/dev/monitoring/*` - Public in dev environment only

### Approval Endpoints Security

Approval endpoints (`/api/v1/approval/*`) require BOTH:
1. **API Key** (Layer 1) - Ensures only authorized systems (UIC infrastructure) can make requests
2. **Approval Token** (Layer 2) - Ensures the requester has permission for the specific job

Both layers must pass for access - defense in depth.

### Security Features

- Constant-time comparison (`secrets.compare_digest()`) prevents timing attacks
- Multiple keys supported (comma-separated in env var)
- Keys stored as `SecretStr` to prevent accidental logging
- Whitespace automatically stripped from configured keys
- **API keys cached at middleware initialization** for optimal performance (loaded once, not per-request)
- Authentication events logged for security auditing (missing/invalid keys)

### Client IP Extraction

Both authentication middleware implementations support reverse proxy setups:
- Checks `X-Forwarded-For` header (load balancers, CDNs)
- Falls back to `X-Real-IP` header (nginx)
- Uses direct connection IP as final fallback
- Extracted IP included in authentication logs for audit trails

### Generating API Keys

```bash
python3 -c "import uuid; print(f'uic-{uuid.uuid4()}')"
```

## Swagger/Docs Authentication (HTTP Basic)

**Purpose:** Protect API documentation in production
**Endpoints:** `/docs`, `/openapi.json`, `/redoc`
**Implementation:** `src/middleware/docs_auth.py`

### Configuration

```bash
# .env
ENABLE_DOCS_AUTH=true
DOCS_USERNAME=dase
DOCS_PASSWORD=a11y
```

Environment-dependent: disabled in dev for easy testing, enabled in prod.

### Security Features

- HTTP Basic Authentication (RFC 7617)
- Constant-time credential comparison
- `WWW-Authenticate` header triggers browser login prompt
- Password stored as `SecretStr`

## Middleware Stack Order

Middleware executes in reverse order of registration (last added = first executed):

```python
# src/main.py execution order:
# 1. CORS
# 2. Logging
# 3. Rate Limit
# 4. Error Handler
# 5. Docs Auth
# 6. API Key Auth
# 7. Endpoint
```

## Testing Authentication

**Unit Tests:**
- `tests/unit/middleware/test_api_key_auth.py` - API key middleware
- `tests/unit/middleware/test_docs_auth.py` - Docs auth middleware

**Integration Tests:**
- `tests/integration/api/test_api_authentication.py` - Full auth flow

**Manual Testing:**

```bash
# Start services with auth enabled
make dev

# Test API endpoint without key (should fail)
curl http://localhost:8080/api/v1/documents/test-id
# → 401 Unauthorized

# Test with valid key (should work or 404 if job not found)
curl -H "X-API-Key: uic-2bd2c716-bc67-4032-ba66-e4f35c441759" \
  http://localhost:8080/api/v1/documents/test-id

# Test Swagger UI (should prompt for username/password)
open http://localhost:8080/docs
# → Browser login prompt: dase / a11y

# Test public endpoints still work
curl http://localhost:8080/health
# → 200 OK (no auth required)
```

## Important Notes

**Integration tests require authentication to be enabled** in `.env`:

```bash
# .env (required for integration tests)
ENABLE_API_KEY_AUTH=true
ENABLE_DOCS_AUTH=true
API_KEYS=uic-2bd2c716-bc67-4032-ba66-e4f35c441759
DOCS_USERNAME=dase
DOCS_PASSWORD=a11y
```

The authentication integration tests use the app instance from `src.main`, which is configured at startup based on `.env` settings. Always keep authentication enabled in `.env` to ensure integration tests pass!
