# Equalify Reflow Feedback Service - Project Plan

## Brief

A standalone feedback collection service (`equalify-reflow-feedback`) that aggregates user-reported issues and document edits from across the Equalify ecosystem. Stores feedback in a persistent database, exposes a REST API for collection, and uses Metabase for dashboarding.

**Clients:** equalify-reflow (Pipeline Viewer), equalify-reflow-wp (WordPress plugin), future apps.

**Not replacing:** The pdf-converter's in-session edit/comment feedback loop stays as-is. This service captures the *persistent record* of what users found wrong and what they fixed.

---

## Phase 1: Repo Scaffold & Core API

**Goal:** Working FastAPI service with SQLite, Docker, and the feedback collection endpoint.

### 1.1 Repository Setup

- [ ] Create `equalify-reflow-feedback` repo on GitHub (EqualifyEverything org)
- [ ] Clone to `~/Projects/equalify-reflow-feedback`

**Files to create:**

```
equalify-reflow-feedback/
├── CLAUDE.md                    # Claude Code instructions
├── CONTRIBUTING.md              # Dev workflow
├── .env.example                 # Config template
├── .gitignore                   # Python + Docker ignores
├── Dockerfile                   # Multi-stage (base + deps + dev + prod)
├── docker-compose.yml           # Base: api + metabase
├── docker-compose.dev.yml       # Dev overrides: volume mounts, hot-reload
├── Makefile                     # Dev commands
├── pyproject.toml               # uv, deps, pytest, ruff, mypy
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # Settings (pydantic-settings)
│   ├── database.py              # SQLAlchemy engine + session factory
│   ├── models.py                # SQLAlchemy ORM models
│   ├── schemas.py               # Pydantic request/response schemas
│   ├── api/
│   │   ├── __init__.py
│   │   ├── feedback.py          # POST/GET /api/v1/feedback
│   │   ├── stats.py             # GET /api/v1/feedback/stats
│   │   └── health.py            # GET /health
│   ├── services/
│   │   ├── __init__.py
│   │   └── feedback_service.py  # Business logic
│   └── middleware/
│       ├── __init__.py
│       └── api_key_auth.py      # API key auth (per source app)
└── tests/
    ├── conftest.py              # Fixtures (test DB, test client)
    ├── unit/
    │   ├── test_models.py
    │   ├── test_schemas.py
    │   └── test_feedback_service.py
    └── integration/
        ├── test_feedback_api.py
        └── test_stats_api.py
```

**Verify:** `make dev` starts the service, `curl localhost:8090/health` returns OK.

### 1.2 Data Model

```python
# src/models.py
class FeedbackEntry(Base):
    __tablename__ = "feedback_entries"

    id: Mapped[str]              # UUID primary key
    created_at: Mapped[datetime] # Auto-set, indexed

    # Source identification
    source_app: Mapped[str]      # "pdf-converter", "reflow-wp", etc.
    api_key_id: Mapped[str]      # Which API key submitted this (for audit)

    # Document reference
    document_id: Mapped[str | None]  # Job ID or attachment ID from source app
    document_title: Mapped[str | None]  # Human-readable doc name

    # Feedback content
    feedback_type: Mapped[str]   # "issue_report" | "user_edit"
    category: Mapped[str]        # "content" | "formatting" | "accessibility" | "structure" | "other"
    description: Mapped[str | None]  # Freeform description (issue reports)

    # Edit tracking (user_edit type)
    original_text: Mapped[str | None]  # Before text
    corrected_text: Mapped[str | None] # After text
    page: Mapped[int | None]
    section: Mapped[str | None]

    # Flexible context
    metadata: Mapped[dict | None]  # JSON column for app-specific data

    # Indexes: created_at, source_app, feedback_type, category
```

**Verify:** `make shell` then `uv run python -c "from src.models import FeedbackEntry; print('OK')"` succeeds.

### 1.3 API Endpoints

**POST /api/v1/feedback** — Submit feedback (single or batch)

```json
// Request (single)
{
  "document_id": "abc-123",
  "document_title": "CHEM 101 Syllabus",
  "feedback_type": "user_edit",
  "category": "content",
  "original_text": "Hydrogen has 2 protons",
  "corrected_text": "Hydrogen has 1 proton",
  "page": 3,
  "section": "Chapter 1",
  "metadata": {"session_id": "xyz", "version": "v4"}
}

// Request (batch)
{
  "items": [/* array of above */]
}

// Response
{
  "received": 1,
  "ids": ["uuid-1"]
}
```

**GET /api/v1/feedback** — List/filter feedback

```
Query params:
  ?source_app=pdf-converter
  ?feedback_type=user_edit
  ?category=accessibility
  ?document_id=abc-123
  ?since=2026-02-01T00:00:00Z
  ?until=2026-02-28T00:00:00Z
  ?page=1&page_size=50

Response: { items: [...], total: 142, page: 1, page_size: 50 }
```

**GET /api/v1/feedback/stats** — Aggregated statistics

```json
{
  "total": 342,
  "by_source": {"pdf-converter": 280, "reflow-wp": 62},
  "by_type": {"issue_report": 120, "user_edit": 222},
  "by_category": {"content": 150, "formatting": 80, "accessibility": 70, "structure": 42},
  "recent_7_days": 45,
  "recent_30_days": 180
}
```

**GET /health** — Health check (public, no auth)

```json
{"status": "healthy", "version": "0.1.0", "database": "connected"}
```

**Verify:** Submit feedback via curl, retrieve via GET, check stats reflect the submission.

### 1.4 Configuration

```python
# src/config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "equalify-reflow-feedback"
    version: str = "0.1.0"
    environment: str = "production"  # "dev" or "production"
    api_port: int = 8090  # Different from pdf-converter's 8080

    # Database
    database_url: str = "sqlite:///data/feedback.db"  # EFS mount in prod

    # Auth
    enable_api_key_auth: bool = True
    api_key_header_name: str = "X-API-Key"
    api_keys: SecretStr | None = None  # Comma-separated: "pdf-converter-key,reflow-wp-key"

    # Metabase
    metabase_port: int = 3000
```

**Verify:** `.env.example` documents all variables. `make dev` picks them up.

### 1.5 Auth Middleware

- Reuse pdf-converter's `APIKeyAuthMiddleware` pattern
- `secrets.compare_digest()` for timing-safe comparison
- Public endpoints: `/health`, `/docs`, `/openapi.json`
- `source_app` derived from which API key was used (key → app mapping in config)

**API Key Config Pattern:**
```env
# .env
API_KEYS=pdf-converter:key-abc-123,reflow-wp:key-def-456
```

Parse as `{"key-abc-123": "pdf-converter", "key-def-456": "reflow-wp"}` so each request automatically tags `source_app` and `api_key_id`.

**Verify:** Request without key returns 401. Request with wrong key returns 403. Request with valid key succeeds and `source_app` is auto-populated.

### 1.6 Docker Setup

**Dockerfile** (3-stage, simplified from pdf-converter):

```dockerfile
# Stage 1: Base
FROM python:3.11-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

# Stage 2: Development (hot-reload)
FROM base AS development
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY src/ src/
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8090", "--reload"]

# Stage 3: Production
FROM base AS production
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src/ src/
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8090"]
```

**docker-compose.yml:**

```yaml
services:
  api:
    build:
      context: .
      target: production
    ports:
      - "${API_PORT:-8090}:8090"
    volumes:
      - feedback-data:/app/data  # SQLite DB location
    env_file: .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"]
      interval: 30s
      timeout: 10s
      retries: 3

  metabase:
    image: metabase/metabase:latest
    ports:
      - "${METABASE_PORT:-3000}:3000"
    volumes:
      - feedback-data:/app/data:ro    # Read-only access to SQLite
      - metabase-data:/metabase-data  # Metabase's own DB
    environment:
      MB_DB_FILE: /metabase-data/metabase.db
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

volumes:
  feedback-data:
  metabase-data:
```

**docker-compose.dev.yml:**

```yaml
services:
  api:
    build:
      target: development
    volumes:
      - ./src:/app/src:ro          # Hot-reload
      - feedback-data:/app/data
    environment:
      ENVIRONMENT: dev
      ENABLE_API_KEY_AUTH: "false"  # Easier local dev
```

**Verify:** `make dev` starts both api and metabase. API on :8090, Metabase on :3000.

### 1.7 Makefile

```makefile
.PHONY: dev down logs health test-fast shell clean

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

down:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml down

logs:
	docker compose logs -f api

logs-metabase:
	docker compose logs -f metabase

health:
	curl -s http://localhost:8090/health | python -m json.tool

test-fast:
	docker compose exec api uv run pytest tests/unit -x -q

shell:
	docker compose exec api bash

clean:
	docker compose down -v
```

**Verify:** Each make target works as expected.

### 1.8 Tests

**Unit tests** (~20 tests):
- `test_models.py` — FeedbackEntry creation, field validation
- `test_schemas.py` — Pydantic schema validation (required fields, enums, batch)
- `test_feedback_service.py` — Service logic (create, list, filter, stats)

**Integration tests** (~10 tests):
- `test_feedback_api.py` — POST/GET endpoints via TestClient
- `test_stats_api.py` — Stats aggregation accuracy
- Auth middleware (401/403 responses)

**Verify:** `make test-fast` passes all tests.

---

## Phase 2: Metabase Dashboard Setup

**Goal:** Metabase connected to SQLite with pre-configured questions and a dashboard.

### 2.1 Metabase Initial Setup

After `make dev`:
1. Open `http://localhost:3000`
2. Complete Metabase setup wizard
3. Add database connection:
   - Type: SQLite
   - Path: `/app/data/feedback.db`

### 2.2 Dashboard: Feedback Overview

Create saved questions in Metabase:

| Question | SQL / Visual |
|----------|-------------|
| **Feedback Volume (30 days)** | Line chart: count by day, grouped by source_app |
| **By Category** | Pie chart: count grouped by category |
| **By Type** | Bar chart: issue_report vs user_edit counts |
| **By Source App** | Bar chart: count by source_app |
| **Recent Feedback** | Table: last 50 entries, all columns |
| **Top Edited Pages** | Table: page + count, ordered desc (user_edit type only) |
| **Issue Reports** | Table: filtered to feedback_type=issue_report, recent first |

Assemble into a single "Feedback Overview" dashboard.

### 2.3 Metabase Persistence

Metabase stores its config (questions, dashboards, users) in its own H2 database at `/metabase-data/metabase.db`. The `metabase-data` Docker volume persists this across restarts.

For production: Consider using the same EFS mount for Metabase's config DB so it survives container replacements.

**Verify:** Dashboard loads with seed data, survives `make down && make dev`.

---

## Phase 3: PDF Converter Integration

**Goal:** Add optional feedback forwarding from pdf-converter to the feedback service.

### 3.1 Configuration (in equalify-reflow)

Add to `src/config.py`:

```python
# Feedback service (optional)
feedback_service_url: str | None = None  # e.g., "http://feedback:8090"
feedback_service_api_key: SecretStr | None = None
feedback_enabled: bool = False
```

Add to `.env.example`:

```env
# Feedback Service (optional - set URL to enable)
FEEDBACK_ENABLED=false
FEEDBACK_SERVICE_URL=http://feedback:8090
FEEDBACK_SERVICE_API_KEY=pdf-converter-key-here
```

### 3.2 Feedback Client (in equalify-reflow)

Create `src/services/feedback_client.py`:

```python
class FeedbackClient:
    """Fire-and-forget client for the feedback collection service."""

    async def submit_feedback(self, entries: list[FeedbackPayload]) -> None:
        """POST to feedback service. Logs errors but never raises."""

    async def track_edit(self, document_id, original, corrected, page, section) -> None:
        """Convenience: submit a user_edit entry."""

    async def report_issue(self, document_id, category, description) -> None:
        """Convenience: submit an issue_report entry."""
```

Key design:
- **Fire-and-forget:** Never block or fail the main app
- **httpx.AsyncClient** with short timeout (5s)
- Errors logged, never raised
- No-op when `feedback_enabled=False`

### 3.3 Integration Points (in equalify-reflow)

Hook into existing feedback flow in `src/api/pipeline_feedback.py`:

1. **On review accept** (`POST /{session_id}/review`): For each accepted `CandidateChange`, forward as a `user_edit` entry with `original_text=old_text`, `corrected_text=new_text`
2. **On session finalize** (`POST /{session_id}/approve`): Optionally forward a summary

This captures what users actually changed without modifying the existing feedback UX.

### 3.4 "Report Issue" UI (in Pipeline Viewer)

Add a minimal "Report Issue" button to the viewer toolbar:
- Category dropdown (content, formatting, accessibility, structure)
- Description textarea
- Optional: text selection for context
- Submits to pdf-converter's new endpoint, which forwards to feedback service

New endpoint in pdf-converter: `POST /api/v1/feedback/report`
- Thin proxy that adds `source_app=pdf-converter` and forwards to feedback service
- Only registered when `FEEDBACK_ENABLED=true`

**Verify:** Process a PDF, make an edit, accept it. Check feedback service received the entry. Report an issue. Check it shows in Metabase.

---

## Phase 4: WordPress Plugin Integration

**Goal:** Add feedback collection to the WP plugin viewer.

### 4.1 Plugin Settings (in equalify-reflow-wp)

Add to Settings page (`class-settings.php`):
- **Feedback Service URL** (text input)
- **Feedback API Key** (password input)
- **Enable Feedback** (checkbox)

Stored as WordPress options: `equalify_reflow_feedback_url`, `equalify_reflow_feedback_api_key`, `equalify_reflow_feedback_enabled`.

### 4.2 PHP Feedback Client (in equalify-reflow-wp)

Create `includes/class-feedback-client.php`:

```php
class Equalify_Reflow_Feedback_Client {
    public function submit_feedback(array $entries): bool { /* wp_remote_post */ }
    public function track_edit(string $doc_id, string $original, string $corrected, ...): bool { }
    public function report_issue(string $doc_id, string $category, string $description): bool { }
}
```

Same fire-and-forget pattern as Python client.

### 4.3 WP REST Endpoint (in equalify-reflow-wp)

Add to `class-media-library.php`:
- `POST /equalify-reflow/v1/feedback/{id}` — Proxy feedback submission for attachment
- Only registered when feedback is enabled

### 4.4 Viewer UI (in equalify-reflow-wp)

Add "Report Issue" button to the React viewer (`src/components/ViewerApp.js`):
- Same minimal form as pdf-converter: category + description
- Posts to the WP REST endpoint, which forwards to feedback service
- Only rendered when feedback is enabled (pass setting via `wp_localize_script`)

**Verify:** View a converted document in WordPress, report an issue. Check it appears in Metabase dashboard.

---

## Phase 5: Production Deployment

**Goal:** Deploy feedback service to ECS with EFS-backed SQLite.

### 5.1 Terraform (in equalify-reflow-feedback)

Create `terraform/` directory:

```
terraform/
├── main.tf              # Provider, backend
├── ecs.tf               # Task definition, service
├── efs.tf               # EFS filesystem + mount targets
├── ecr.tf               # Container registry
├── alb.tf               # Load balancer (or target group on shared ALB)
├── security_groups.tf   # Network rules
├── secrets.tf           # API keys in Secrets Manager
├── cloudwatch.tf        # Log group
├── variables.tf
├── outputs.tf
└── terraform.tfvars.example
```

**EFS for SQLite:**
```hcl
resource "aws_efs_file_system" "feedback_data" {
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"
  encrypted        = true
}

# Mount in ECS task definition:
# volume { name = "feedback-data", efs_volume_configuration { file_system_id = aws_efs_file_system.feedback_data.id } }
# container mount: { sourceVolume = "feedback-data", containerPath = "/app/data" }
```

**Metabase as sidecar** in same task definition (shares EFS volume) or as a separate ECS service.

### 5.2 Deployment Scripts

Replicate from pdf-converter:
- `scripts/deploy-infrastructure.sh` — Terraform plan + apply
- `scripts/deploy-app.sh` — Build, push to ECR, update ECS service

### 5.3 CI/CD

`.github/workflows/ci.yml`:
- Unit tests on every push
- Integration tests on PR
- Auto-deploy to ECS on merge to main (optional)

### 5.4 DNS & Networking

- Subdomain: `feedback.equalify.uic.edu` or similar
- ALB listener rule routing to feedback service target group
- Security group: allow inbound from pdf-converter and WP plugin origins

**Verify:** `curl https://feedback.equalify.uic.edu/health` returns healthy. Submit feedback from both apps in production. Metabase dashboard accessible.

---

## Implementation Order

| Step | Phase | Effort | Dependencies |
|------|-------|--------|-------------|
| 1 | 1.1-1.8 | Repo scaffold + core API | None |
| 2 | 2.1-2.3 | Metabase setup | Phase 1 |
| 3 | 3.1-3.4 | PDF converter integration | Phase 1 |
| 4 | 4.1-4.4 | WP plugin integration | Phase 1 |
| 5 | 5.1-5.4 | Production deployment | Phases 1-2 |

Phases 3 and 4 can run in parallel. Phase 5 can start as soon as Phase 2 is complete.

---

## Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite (SQLAlchemy) | Low volume, simple ops, swap to Postgres via connection string later |
| Dashboard | Metabase (Docker sidecar) | No custom UI to build/maintain, powerful out-of-box |
| Auth | API key per source app | Simple, consistent with pdf-converter pattern |
| Port | 8090 | Avoids conflict with pdf-converter (8080) |
| Feedback forwarding | Fire-and-forget async | Never block or degrade the source app |
| Source app tagging | Derived from API key | No trust in client-provided source_app |
| Storage (prod) | EFS-mounted SQLite | ~$0.30/GB/mo vs $12-15/mo for RDS |
