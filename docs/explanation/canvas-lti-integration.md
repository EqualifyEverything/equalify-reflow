# Canvas LTI Integration — Architecture and Plan

## Goal

Eliminate the manual step between "professor uploads a PDF to Canvas" and "students have an accessible, reflowable version to read." The professor's workflow should not change. Inaccessible PDFs should automatically become draft Canvas Pages that the professor reviews and publishes.

## What faculty actually do today

A typical course materials workflow in Canvas:

1. Professor drags a lecture PDF into **Course Files** or a **Module**.
2. PDF is published, students click the link, and anyone using a screen reader, a phone, or simply zooming in struggles.
3. Accessibility office may flag it weeks later; remediation is reactive.

Anything that asks the professor to "open another tool to remediate this" loses. Faculty-first design means: no install, no extra app, no extra click during upload. The professor only acts at the *review* step, when there is something to look at.

## User journeys

### Professor (primary user)

1. Uploads `lecture-3.pdf` to Canvas as they always have.
2. ~5–10 minutes later, an in-Canvas message arrives: *"Your accessible version of lecture-3.pdf is ready for review."*
3. Clicks the link → lands in the LTI tool (still inside Canvas, no separate login) → sees the proposed Canvas Page side-by-side with the original PDF.
4. Either:
   - **Approve & publish** — Canvas Page goes live, optionally linked in the same Module as the PDF.
   - **Edit in Reflow viewer** — opens the existing pipeline viewer (handed an LTI launch session), corrects, comes back, approves.
   - **Reject** — page is deleted, file gets a "needs manual remediation" tag.

### Canvas admin (one-time setup)

1. Creates a Developer Key in Canvas → registers the tool's LTI 1.3 config JSON.
2. Generates an API access token for the watcher service (scoped to read files and write pages).
3. Subscribes (optionally) to Canvas Live Events for file uploads, or accepts polling.
4. Places the tool in **Course Navigation** as *Accessible Documents*.

### Student (incidental)

Sees the Canvas Page wherever the professor placed it. The original PDF can still be downloaded; the Page is the primary route.

## Components

```
                       ┌─────────────────────────────────┐
                       │            Canvas LMS           │
                       │                                 │
   PDF upload ───────► │   Files  Modules  Pages  Convo  │
                       │     ▲       ▲       ▲      ▲    │
                       └─────┼───────┼───────┼──────┼────┘
                             │       │       │      │
              ┌──────────────┘       │       │      │
              │ poll / Live Event    │       │      │
              ▼                      │       │      │
      ┌──────────────┐               │       │      │
      │ Canvas       │ create draft  │       │      │
      │ Watcher      │ ──────────────┘       │      │
      │ (worker)     │                       │      │
      └──────┬───────┘                       │      │
             │ submit PDF                    │      │
             ▼                               │      │
      ┌──────────────┐    SSE / poll  ┌─────────────┴──┐
      │ Reflow API   │ ─────────────► │ Reflow Bridge  │
      │ (existing)   │                │ (worker)       │
      └──────────────┘                └──────┬─────────┘
                                             │ create/publish page,
                                             │ send conversation
                                             ▼
                                      ┌──────────────┐
                                      │ Canvas API   │
                                      └──────────────┘

      ┌────────────────────────┐
      │ LTI Tool (FastAPI)     │  ◄── professor's browser
      │  - /lti/login          │      launches from Canvas
      │  - /lti/launch         │
      │  - /lti/jwks           │
      │  - /canvas/review/*    │ ──► pulls state from Redis,
      │  - /canvas/review UI   │     calls Canvas API to publish
      └────────────────────────┘
```

Three logical pieces, all inside the existing `src/` monolith:

| Piece | Module | Role |
|---|---|---|
| **LTI Tool** | `src/lti/` | OIDC login, signed launch, JWKS, tool config JSON. Renders the review UI when faculty launch from Canvas. |
| **Canvas Watcher** | `src/workers/canvas_watcher.py` | Detects new PDFs (poll first, Live Events later). Submits to Reflow API. Records mapping in Redis. |
| **Reflow Bridge** | `src/services/reflow_bridge.py` + `src/workers/reflow_bridge_worker.py` | Watches Reflow job completion. Converts markdown → Canvas HTML. Creates draft Page. Sends Conversation. |

`src/canvas/` is the shared Canvas REST client used by all three pieces.

## Data flow

```
1. PDF uploaded in Canvas
      → Watcher sees it (poll or Live Event)
      → Watcher GET /api/v1/files/{file_id}/download (Canvas)
      → Watcher POST /api/v1/documents (Reflow, with X-API-Key)
      → Watcher writes Redis key:
            eq-pdf:canvas:job:{reflow_job_id} = {
              canvas_file_id, canvas_course_id, canvas_user_id,
              status: "processing", created_at
            }
      → Watcher writes index:
            eq-pdf:canvas:course:{course_id}:pending → SADD reflow_job_id

2. Reflow processes the PDF (existing pipeline, ~5 min)

3. Reflow emits completion event (SSE) → Bridge worker
      → Bridge GETs Reflow results (markdown + figure URLs)
      → Bridge rewrites image URLs (Reflow S3 presigned → uploaded to Canvas Files, OR proxied)
      → Bridge renders markdown → Canvas-flavoured HTML
      → Bridge POST /api/v1/courses/{course_id}/pages
            with { wiki_page: { title, body, published: false } }
      → Bridge updates Redis:
            status: "awaiting_review", canvas_page_url, canvas_page_id
      → Bridge POST /api/v1/conversations
            recipient: canvas_user_id
            body: "Your accessible version of <filename>.pdf is ready for review: <launch URL>"

4. Professor clicks link → Canvas issues LTI 1.3 launch
      → /lti/login → redirect to Canvas auth → /lti/launch
      → /lti/launch validates JWT, establishes session, redirects to /canvas/review/{job_id}

5. Review screen shows:
      - Original PDF (Canvas-hosted preview)
      - Proposed Canvas Page (rendered HTML)
      - "Open in Reflow Viewer" → existing pipeline viewer with LTI context
      - "Approve & publish" → PUT /pages/{url} { published: true }, optional module insert
      - "Reject" → DELETE /pages/{url}, mark file in Redis as "rejected"
```

## Auth model

| Boundary | Mechanism |
|---|---|
| Canvas → Tool (browser launches) | LTI 1.3: OIDC login + signed `id_token` JWT, validated against Canvas JWKS. |
| Tool → Canvas (API calls) | Developer Key OAuth2 client credentials, or admin-issued long-lived API token. Stored as `CANVAS_API_TOKEN` (secret). |
| Tool → Reflow API | Existing `X-API-Key` header. |
| Faculty session inside tool | Short-lived signed cookie issued at LTI launch, scoped to `(course_id, user_id)` from the JWT claims. |

The tool never asks the professor to log in. The LTI launch JWT *is* the identity proof; the cookie is just a session container so they don't have to re-launch for every click.

## Canvas API surface

Minimum endpoints we hit (all under `/api/v1/`):

- `GET /courses/:course_id/files?content_types[]=application/pdf` — watcher poll.
- `GET /files/:id/public_url` or `GET /files/:id` with download URL — fetch PDF bytes.
- `POST /courses/:course_id/files` — upload extracted figures (or skip and proxy through Reflow S3).
- `POST /courses/:course_id/pages` — create draft Page.
- `PUT /courses/:course_id/pages/:url` — publish / update.
- `DELETE /courses/:course_id/pages/:url` — reject path.
- `POST /conversations` — notify professor.

The watcher subscribes to Canvas Live Events (via AWS SQS) later, replacing polling. Polling is fine for the MVP and for institutions that haven't enabled Data Services.

## Redis keys

Reuse the existing `eq-pdf:` prefix:

| Key | Type | Purpose |
|---|---|---|
| `eq-pdf:canvas:job:{reflow_job_id}` | Hash | Mapping from a Reflow job back to the originating Canvas file, course, and user. |
| `eq-pdf:canvas:course:{course_id}:pending` | Set | Reflow job IDs awaiting review in a course (drives the review list). |
| `eq-pdf:canvas:course:{course_id}:processed` | Set | Canvas file IDs already submitted (idempotency guard for the watcher). |
| `eq-pdf:canvas:session:{session_id}` | Hash | LTI launch session (user_id, course_id, roles, ttl). |
| `eq-pdf:canvas:state:{nonce}` | String | OIDC state nonce for the login flow (10 min TTL). |

All Redis access goes through the existing job_service patterns — async, with the same circuit breaker conventions.

## Deployment topology

The integration adds **no new containers** in the MVP. Routes mount inside the existing `api-gateway` FastAPI app; the watcher and bridge run as background tasks alongside the existing `pii_worker` and `timeout_worker` in `lifespan`. This matches the project's monolith convention.

When Canvas Live Events comes online (Phase 4), the SQS consumer becomes a separate worker process. Until then: one container, three new endpoints, two new background tasks.

Public URL requirement: the tool needs to be reachable from Canvas via HTTPS. UIC already has `reflow.equalify.uic.edu` for the API; the LTI endpoints sit under the same hostname:

- `https://reflow.equalify.uic.edu/lti/login`
- `https://reflow.equalify.uic.edu/lti/launch`
- `https://reflow.equalify.uic.edu/lti/jwks`
- `https://reflow.equalify.uic.edu/lti/config.json` (XML or JSON config for admin install)

## Phased build plan

| Phase | Scope | Done when |
|---|---|---|
| **0 — Foundation** | LTI 1.3 plumbing (login, launch, JWKS, key generation), config JSON, smoke test against Canvas test tenant. | A test admin can install the tool, launch it from a course, and see a "Hello, {user}" page. |
| **1 — Polling watcher** | Poll one course every N seconds, detect new PDFs, submit to Reflow, persist mapping. | Uploading a PDF results in a Reflow job within 1 polling interval. |
| **2 — Bridge + draft Page** | Listen to Reflow SSE; on success create draft Canvas Page; update Redis. | Completed Reflow job leaves an unpublished Page in Canvas. |
| **3 — Review UI** | LTI nav placement, pending list, side-by-side preview, approve/reject. | Professor can approve a draft and the Page goes live. |
| **4 — Notifications** | Canvas Conversation message to the professor on completion. | Professor gets the inbox ping without checking the LTI nav. |
| **5 — Live Events** | Replace polling with Canvas Live Events via SQS. | New uploads trigger processing in <30 seconds, no polling. |
| **6 — Module rewiring** | Optional: when approved, also insert the Page next to the PDF in any Module that references it. | Approval offers "Also link in Module X" checkboxes. |
| **7 — Multi-tenancy hardening** | Per-institution config, multi-tenant key isolation, admin onboarding UI. | A second institution can install without code changes. |

The MVP (Phases 0–3) is the smallest thing that delivers faculty value. Phase 4 (conversation) is the magic that makes faculty actually *find* the review queue.

## Open questions

- **Conversion strategy for figures.** Two options: (a) upload extracted figures from Reflow into Canvas Files so the Page is self-contained, or (b) leave figures on Reflow S3 with long-lived presigned URLs. (a) is more portable, (b) is cheaper and avoids cluttering Course Files. The MVP picks (b) and revisits.
- **Who pays for the API call?** The Reflow API rate-limits per API key. If every Canvas institution shares one key, one popular tenant could starve others. Phase 7 introduces per-institution keys; until then we assume single-tenant.
- **PII flow.** Reflow already PII-scans every document. If a PDF fails the PII gate, the current Reflow approval workflow asks the *uploader* to confirm. In the Canvas flow, the uploader is the professor — but the approval token currently goes by email. We need to route that approval through the LTI tool too. Probably Phase 2.5.
- **Localisation of the Canvas Page title.** Default is the original PDF filename minus extension. Configurable per course later.
- **What happens to the original PDF.** MVP leaves it alone. Phase 6 offers (but does not force) replacing the link in modules.

## Why this shape and not alternatives

- **Browser extension** would put the burden on every professor to install it. Adoption falls off a cliff.
- **Pure API poller with no LTI** would force the review step into an external dashboard. Faculty already live in Canvas; adding a second login is a non-starter.
- **LTI alone, no watcher** would require the professor to actively *send* the PDF to the tool (e.g., a deep link picker). That's an extra step at upload time, exactly the thing the design constraint forbids.

Hybrid is the only shape where the professor's workflow is genuinely unchanged.

## Panorama-style overlay

The pieces above (watcher, bridge, draft Page, review queue) cover the *conversion* path. To match what tools like YuJa Panorama and Anthology Ally do, the integration also needs to be *visible* inside the native Canvas UI — students see formats, instructors see scores, all without leaving Canvas. That's a separate surface, layered on top of the same backend.

### What it looks like for users

| User | Where | What they see |
|---|---|---|
| Student | Files page, Modules, Assignments, anywhere a PDF link appears | A small format-picker icon next to the filename. Clicking it offers: original PDF, accessible HTML, tagged PDF, ePub, audio (MP3), translated, BeeLine Reader. |
| Instructor | Same places, plus their Files index | A coloured gauge (red / amber / green / dark green) next to each file showing 0–100% accessibility. Clicking the gauge opens a panel listing the top issues and how to fix them. |
| Instructor | Course Navigation → *Accessible Documents* (the LTI nav item) | Course-wide accessibility dashboard: aggregate score, file-by-file table, sortable, with a link to remediate each. |
| Admin | Cross-course dashboard | Institution-level trend, breakdown by department, by file type, by severity. |

### How it works

The visible UI is not an iframe — it is a **JavaScript bundle injected via Canvas Theme Editor**. The admin pastes one line into *Admin → Themes → Edit current theme → JavaScript file*:

```html
<script src="https://reflow.equalify.uic.edu/lti/panorama.js?inst=uic" defer></script>
```

That script runs on every Canvas page load. It:

1. Reads ``window.ENV`` to learn the current user, course, and role.
2. Walks the DOM for file links matching Canvas's predictable selectors (``a[href*="/files/"]``, file rows in Modules, etc.).
3. Batches the file IDs and calls ``GET /canvas/panorama/score?inst=uic&course_id=…&file_ids=…``.
4. Injects a small gauge or dropdown element next to each matched link.

The injected widget is a single small custom element (no framework) styled to blend with Canvas — no iframe, no shadow DOM unless we hit class collisions.

### New backend pieces

| Endpoint | Purpose |
|---|---|
| ``GET /lti/panorama.js`` | Serves the injection bundle. Cached at the CDN edge with a short max-age so admins don't have to re-paste on every release. |
| ``GET /canvas/panorama/score`` | Returns ``{ file_id: { score, severity, available_formats[] } }`` for a batch of file IDs in a course. |
| ``GET /canvas/panorama/issues/{file_id}`` | Returns the ranked list of issues for one file (used by the instructor popover). |
| ``GET /canvas/panorama/alt/{job_id}/{format}`` | Serves an alternative format: ``html``, ``pdf-tagged``, ``epub``, ``mp3``, ``txt``. Validates that the requester is enrolled in the originating course (delegates the check back to Canvas via a tiny ``GET /courses/:id/enrollments`` call cached for 60s). |
| ``GET /canvas/dashboard`` | Course accessibility dashboard. Same auth as ``/canvas/review`` — LTI session cookie. |

### Score model

The score is a single 0–100 number with a severity bucket. The MVP composes it from signals Reflow already produces, so we don't have to build a separate accessibility scanner:

| Signal | Weight | Source |
|---|---|---|
| PDF has a text layer (not a pure scan) | 30 | Docling extraction phase |
| Headings present and hierarchical | 20 | Headings reconciliation phase |
| All images have alt text | 20 | Translation phase (per-page) |
| Tables are real tables, not images | 15 | Structure analysis |
| Reading order is linearised | 10 | Boundary fixes |
| Language is set | 5 | Metadata |

Severity:

- 0–32: red — unusable
- 33–66: amber — needs work
- 67–89: green — usable
- 90–100: dark green — gold standard

The exact weights live in ``src/canvas/panorama.py`` and are tuneable per institution; do not hardcode them at call sites.

### Alternative formats

Reflow already produces *semantic markdown*. Every other format derives from it:

| Format | How it's produced | Phase |
|---|---|---|
| ``html`` | Render markdown → HTML (existing converter in `markdown_to_html.py`). | MVP |
| ``txt`` | Strip markdown formatting. | MVP |
| ``pdf-tagged`` | Render markdown → tagged PDF via WeasyPrint with the structure tree retained. | Phase 2 |
| ``epub`` | Render markdown → ePub via pandoc or ebooklib. | Phase 2 |
| ``mp3`` | TTS over the plain-text version. Probably Amazon Polly given the AWS stack. | Phase 3 |
| ``translated/{lang}`` | Run the markdown through the existing AI pipeline with a translation prompt. | Phase 3 |
| ``beeline`` | HTML rendering with BeeLine Reader's stylesheet (licence required). | Phase 4 |

Formats are computed lazily on first request and cached in S3 under ``s3_results_bucket/canvas-alt-formats/{job_id}/{format}``.

### Auth boundary for the injected script

The script runs in a Canvas-origin browser; calls cross to ``reflow.equalify.uic.edu``. Three things keep that safe:

1. **CORS allow-list** of Canvas hostnames per institution, configured server-side via ``CANVAS_ALLOWED_ORIGINS``.
2. **No PII in score responses.** Scores are about files, not students. Anyone who can see a file in Canvas can see its score.
3. **Alt-format downloads are enrollment-checked.** Before streaming the alt format, the server calls Canvas's ``/courses/:id/enrollments`` with the current user's id (read from ``window.ENV.current_user_id`` and signed into the request) and 403s if not enrolled. The signing key is a short-lived token issued by ``GET /canvas/panorama/handshake`` when the script first loads.

### Updated phased build plan (additions)

| Phase | Scope |
|---|---|
| **3a — Theme injection bundle** | Panorama.js MVP: gauges next to file links, format dropdown opens to HTML/TXT only. |
| **3b — Instructor popover** | Click-through panel with ranked issues sourced from Reflow's per-phase outputs. |
| **3c — Course dashboard** | LTI nav item shows aggregate course score and file table. |
| **3d — Institution dashboard** | Admin-only view aggregating across courses. |
| **6 — Tagged PDF + ePub** | Add WeasyPrint / ebooklib alt-format generators. |
| **7 — TTS + translation** | Polly-backed MP3, Bedrock-backed translation. |

The Theme-Editor approach is what Panorama and Ally use; it survives Canvas UI redesigns better than people fear because Canvas's selectors for file links have been stable for years. The fallback is a Custom CSS-only treatment that adds a "View accessible formats" link via ``::after`` content — uglier, but still useful.
