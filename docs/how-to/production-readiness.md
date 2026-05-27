# Production-readiness checklist + runbook

This is the canonical checklist for deploying Equalify Reflow into a
real institution. It covers the auth architecture you actually need
(per-user OAuth2, not LTI Advantage client_credentials), the secret
management story, the operational signals you need on day one, and
the specific gotchas Canvas Cloud has that we discovered the hard way.

## Architecture summary

After the multi-tenant migration and Phase 8 (per-user OAuth2), Reflow
has three distinct authentication paths against Canvas. **Production
deployments use Path 2 for general API access**; the others exist as
specific-purpose or fallback channels.

| Path | When used | Validity in Canvas Cloud |
|---|---|---|
| LTI 1.3 OIDC launch | Faculty arrives at the tool | works everywhere |
| **Per-user OAuth2** (Phase 8) | All Canvas REST API calls | **only this works** for `/api/v1/...` |
| LTI Advantage client_credentials | NRPS roster, AGS grades | works ONLY for IMS service endpoints |
| Personal API token (env) | Local dev only | works (token is user-bound) but not deployable |

If you find yourself relying on the env-token path in a production
deployment, treat that as a bug. It does not scale, audits poorly,
and breaks when the user who minted the token leaves the institution.

### Dual Developer Key requirement

Canvas Cloud requires **two separate Developer Keys** per institution,
not one. Discovered the hard way (2026-05-19) when the LTI Key
returned `invalid_client` on every `authorization_code` exchange even
with scopes correctly granted.

| Key | Type in Canvas | Purpose | Auth scheme |
|---|---|---|---|
| **LTI Key** | LTI Key (JSON paste) | OIDC launches, NRPS / AGS service calls | JWT bearer (public JWK at `/lti/jwks`) |
| **API Key** | API Key (form fields) | `/api/v1/...` per-user OAuth2 only | `client_secret` (shared secret) |

The LTI Key has a `public_jwk_url`, so Canvas verifies its assertions
against our JWKS. The API Key has **no JWK on file** — Canvas requires
client_secret POST authentication for it, and rejects any JWT
assertion sent against it with `invalid_client`. The two keys
authenticate two different grant types and are not interchangeable.

## Secret management

These secrets must be present in any production deployment. None of
them should ever appear in a git history or container layer.

| Secret | What it is | Where it lives in production |
|---|---|---|
| `LTI_PRIVATE_KEY` | RSA-2048 private key that signs every outgoing assertion | AWS Secrets Manager, mounted at `/app/keys/lti_private.pem` by the Fargate task definition |
| `LTI_PUBLIC_KEY` | The matching public key, published at `/lti/jwks` | Same secret as private, derived on demand |
| `CANVAS_OAUTH_CLIENT_ID` | client_id of the **non-LTI API Key** that authorizes the per-user OAuth2 flow. Distinct from the LTI Key's client_id. | Env var. Plain (not secret) — it's just an integer ID. |
| `CANVAS_OAUTH_CLIENT_SECRET` | Shared secret Canvas generates for the API Key. Required because non-LTI Developer Keys have no JWK on file and only accept client_secret auth. | AWS Secrets Manager |
| `ANTHROPIC_API_KEY` | AI conversion auth | Secrets Manager |
| `REDIS_AUTH` | Redis password | Secrets Manager + ElastiCache config |
| `CANVAS_API_TOKEN` | Personal API token (env-fallback) | **Do not set in production**. Leaving it unset forces all calls through Path 2. |

The Fargate Terraform module (`infrastructure/fargate/main.tf`) already
provisions the Secrets Manager entries and IAM grants. Apply that
before the first deployment, then place the actual secret values via
the AWS CLI or console — they should never be committed.

### Keypair rotation

The JWKS endpoint at `/lti/jwks` serves whatever public key currently
sits at `LTI_PUBLIC_KEY_PATH`. Canvas refetches our JWKS on signature
failure (and on a periodic cache TTL of ~5-10 minutes), so rotation
is a two-step:

1. Add the new key to JWKS first; keep the old key live for the cache
   window (24 hours is the safe number).
2. Switch the active signing key to the new one. Canvas refetches and
   accepts assertions signed by the new key.
3. After 24 hours, remove the old key from JWKS.

The current implementation publishes a single-key JWKS. For
production, this needs a small expansion to a multi-key JWKS with
overlapping validity windows. Tracked as a follow-up.

## Per-institution onboarding (admin steps)

These steps run **once per institution** the first time Reflow is
installed there. Two Developer Keys are created (see "Dual Developer
Key requirement" above for why). Everything else is self-serve from
the faculty side.

### Part A — Create the **LTI Key** (handles launches)

1. **Admin creates the LTI Key** in Canvas: `<canvas>/accounts/<acct>/developer_keys`
   → + Developer Key → **LTI Key** → Method: Paste JSON → paste our
   `/lti/config.json` output → Save.
2. **Admin enables the LTI Key** ("ON" state toggle) — keys default to OFF.
3. **Admin installs the tool** at the desired scope (sub-account or
   account-level): Settings → Apps → + App → By Client ID → paste the
   LTI Key's client_id → Install.
4. **Admin notes the LTI client_id** and shares it with Reflow ops so
   the institution gets registered in our platform registry.

### Part B — Create the **API Key** (handles per-user OAuth2)

5. **Admin creates the API Key** in Canvas: same Developer Keys page
   → + Developer Key → **API Key** (not LTI Key this time).
6. **Set Redirect URIs** to `https://<your-reflow-host>/canvas/oauth/callback`
   (newline-separated if multiple deployments share one key).
7. **Grant scopes** — the API Key needs all 15 scopes listed in
   `multi-tenant-diagnostics.md` step 3. The "Enforce Scopes" toggle
   should be ON; without it Canvas silently grants nothing.
8. **Save the API Key**, then **enable** it ("ON" toggle).
9. **Copy the client_id and the client_secret** (the secret is shown
   in full only once on the key-details page — capture it via the
   "Show Key" button immediately, or regenerate later via the
   `regenerate_secret` API). Stash them in your deployment's secret
   store as `CANVAS_OAUTH_CLIENT_ID` and `CANVAS_OAUTH_CLIENT_SECRET`.

### Part C — Verify

10. Click the tool from a course as an Instructor. Expected:
    * LTI launch upserts a `PlatformInstall` record (verify via
      `scripts.list_platforms`).
    * Canvas's OAuth2 consent screen appears with 15 scopes listed.
    * After approval, Redis contains `eq-pdf:lti:user-token:*` and
      `eq-pdf:lti:course:*:owner` for the course.
    * `scripts.smoke_user_token` lists PDFs in the course without 401.

Subsequent launches by the same user reuse the stored token and skip
consent. Tokens auto-refresh silently via the stored refresh_token.

## Deployment topology

Reflow runs as two services in production:

  * **`api-gateway` ECS service**: 2+ Fargate tasks behind an ALB.
    Runs the HTTP API, the LTI handler, the OAuth callback, and the
    watcher/bridge background asyncio tasks. Stateless.
  * **`redis` ElastiCache cluster**: cluster-mode disabled, single
    primary + one replica. Holds the platform registry, session
    cookies, user tokens, audit-log dedup keys, and the job state.

The Fargate task definition mounts the LTI keypair from Secrets
Manager and reads everything else from environment-variable secrets.
No persistent local disk. No long-lived container state.

### Scaling

  * **API tier**: scale out on CPU. The watcher/bridge run inside
    every api-gateway task; cap at 4 tasks until you add a leader-
    election scheme (otherwise four watchers will each scan every
    course on every tick).
  * **Redis**: scale up before scaling out. Cluster mode requires
    careful key-distribution review; until then run a single shard
    with read replicas for ops queries.
  * **Reflow conversion backend**: separate from the Canvas
    integration; scaled independently per the existing capacity plan.

## Monitoring + alerting

Day-one signals to wire up before going live:

| Signal | Source | Page on |
|---|---|---|
| `/health` 200 rate | ALB + Datadog synthetic | <99.5% in 5 min |
| Canvas API 4xx rate | `reflow.canvas.audit` event=api_call status>=400 | >5% in 5 min |
| Canvas API 5xx rate | same | >0% in 1 min |
| User token refresh failures | `reflow.canvas.audit` event=user_token_grant after a 401 retry | spike vs. baseline |
| Bridge job stuck in `processing` | watcher's stale-job sweep counter | non-zero |
| LTI launch validation failures | api-gateway log warning `LTI launch validation failed` | spike vs. baseline |
| Reflow conversion error rate | existing | (use existing thresholds) |

The `audit.emit` calls in `src/canvas/audit.py` are the structured
events to point Datadog (or whatever you're using) at. Each event has
fields like `platform`, `user`, `course`, `scope_hash`, `latency_ms`,
`status`. Index `platform` first; that's the most common slice for
"is school X having a problem".

## Privacy + audit data retention

The `reflow.canvas.audit` log carries `user_id` and `course_id`. By
default no PII (names, emails, file contents) lands in audit records,
but `user_id` is technically identifying when combined with Canvas's
own records. Recommend:

  * Retain audit logs for **90 days hot**, then archive to S3
    Glacier for the legally-required period (varies by institution —
    most US universities use 7 years for student records).
  * Encrypt at rest with a CMK distinct from the application
    encryption key.
  * Restrict access to a named SRE group; access through the log
    viewer is itself audited.

## Common Canvas Cloud gotchas (we hit each of these in dev)

1. **`canvas.instructure.com` is the SSO realm for ALL Canvas Cloud
   institutions.** The actual institutional API host is
   `<inst>.instructure.com`. Token mints go to the SSO; API calls go
   to the institutional host. Our `derive_endpoints_from_issuer`
   handles this. If a school is on a non-standard Cloud setup,
   override via `platform_overrides.yaml` (not yet implemented;
   tracked as a follow-up).

2. **LTI Advantage client_credentials tokens are NOT valid for the
   general `/api/v1/...` REST API in Canvas Cloud.** They work only
   for LTI Advantage services (NRPS, AGS, Deep Linking). This was
   discovered the hard way; the architectural fix is Phase 8 (per-
   user OAuth2). Confirmed against canvas.test.instructure.com on
   2026-05-19.

3. **The `url:GET|/api/v1/...` scopes ARE visible on the dev key's
   admin page but require the user-OAuth2 flow to actually be
   useful.** Granting them via the API does work, but it does not
   bless the client_credentials tokens; only user-bound bearers
   honor them.

4. **The dev key's `Redirect URIs` textarea is newline-separated.**
   Each redirect URI you want Canvas to accept must appear on its
   own line. The admin UI textbox is the source of truth.

5. **Canvas's JWKS cache TTLs for ~5-10 minutes.** After a keypair
   rotation, expect 5-10 min of "invalid_client" before Canvas
   refetches. Trigger an immediate refetch by editing the dev key
   and re-saving (any field change works).

6. **Test Canvas instances reset monthly.** Build your scaffolding
   so a full reinstall takes under an hour: client_id + scopes +
   redirect_uris + install at the account level + first launch.

7. **LTI Keys cannot authenticate `authorization_code` grants.** Even
   with all scopes granted and the key enabled, posting an
   authorization_code to `/login/oauth2/token` using the LTI Key's
   client_id returns `invalid_client`. This is by Canvas Cloud's
   design — LTI Keys only authenticate JWT-bearer client_credentials.
   The fix is the dual-key model documented in "Per-institution
   onboarding"; the API Key handles OAuth, the LTI Key handles
   launches. Discovered 2026-05-19 on csueb.test.instructure.com.

8. **Non-LTI API Keys do not accept JWT bearer assertions.** They
   have no `public_jwk_url` field, so Canvas has no way to verify a
   signature against them. Code-exchange and refresh both must POST
   `client_secret`. Our callback in `src/api/canvas_oauth.py`
   picks the auth scheme automatically based on whether
   `CANVAS_OAUTH_CLIENT_SECRET` is configured.

## Smoke tests before declaring a deployment healthy

Run these in sequence. All must pass.

```powershell
# 1. JWKS reachable + serves the active public key
curl https://<your-reflow-host>/lti/jwks | jq '.keys[0].kid'

# 2. /health returns 200
curl https://<your-reflow-host>/health

# 3. A faculty launch creates a platform record
docker compose exec api-gateway uv run python -m scripts.list_platforms

# 4. Service token mint works
docker compose exec api-gateway uv run python -m scripts.test_service_token

# 5. After OAuth consent, a user_token is stored
docker compose exec redis redis-cli --scan --pattern "eq-pdf:lti:user-token:*"

# 6. A Canvas API call via the user token succeeds
#    (lists PDFs in the course; defaults to 50594, override with arg)
docker compose exec api-gateway uv run python -m scripts.smoke_user_token <COURSE_ID>
"
```

If step 6 returns a non-zero count of files, your deployment is
end-to-end working. If it 401s, check `audit.emit` log records to
see which token was used and what scope the failure was on.

## Incident response

**A platform is misbehaving** (returning 5xx, leaking PII, anything
that should be paused immediately): mark it soft-revoked:

```powershell
docker compose exec api-gateway uv run python -c "
import asyncio
from redis.asyncio import Redis
from src.config import settings
from src.lti.platform_store import mark_revoked
asyncio.run(__import__('asyncio').get_event_loop().run_until_complete(
    mark_revoked(Redis.from_url(settings.redis_url), '<platform_id>')
))
"
```

Subsequent service-token requests refuse to mint. Existing user tokens
also stop being used (the watcher won't pick the platform up). To
restore, use `clear_revoked` in the same way.

**A faculty member revokes consent on Canvas's side**: their stored
user token will start returning 401 on every refresh attempt. The
client invalidates the cached entry and surfaces a 401 to the caller.
The bridge worker will log a warning and the next LTI launch by that
faculty member will route them back through the consent screen.

**The container restarts and Canvas's cached JWKS is stale**: see
section "Keypair rotation" above; wait the TTL or force a refetch via
the dev key edit-and-save trick.

## Open follow-ups (not blocking production but should land soon)

  * Multi-key JWKS for graceful keypair rotation (currently a single
    key, restart is a brief outage).
  * `platform_overrides.yaml` for non-standard Canvas Cloud setups
    (currently every install must match the canonical URL scheme).
  * Leader election for the watcher/bridge so we can scale api-gateway
    above 4 tasks without N watchers each doing the same scan.
  * RDS-backed platform registry. Redis-only is fine through MVP +
    early production; persistent registry needs durable storage and
    point-in-time recovery for compliance review.
  * Audit log shipper for the structured `reflow.canvas.audit`
    events. Right now they go to stdout; in production they need to
    land in the same indexed store as everything else.

## ADA Title II framing (added 2026-05-20)

Equalify Reflow is positioned as a **WCAG-oriented PDF-to-Canvas-HTML
remediation workflow**, not a "PDF accessibility compliance tool."
The accessible artifact is the **approved Canvas HTML page**, not the
original PDF. We do not claim ADA Title II / WCAG 2.1 AA compliance
by default — compliance is established per-document, per-institution,
by the combination of:

  * Automated conversion (Reflow PDF → markdown → HTML)
  * Automated WCAG checks (`GET /canvas/panorama/wcag/{job_id}` — see
    `src/canvas/wcag_checks.py`)
  * Manual reviewer checklist (headings, alt text, tables, reading
    order — enforced by the publication gate when
    `REQUIRE_WCAG_GATE=true` is set)
  * Faculty publish action recorded with reviewer identity + timestamp

### What's a "conversion quality" score, and why not call it WCAG?

The percentage shown on the panorama dial is a **conversion-quality
heuristic** derived from the markdown the pipeline produced (heading
structure, image-alt coverage, table semantics, language detection).
A high score means "the pipeline extracted accessible structure
well" — it does NOT mean "this document is WCAG-conformant." A
separate review is required to publish.

### Why Canvas HTML, not the original PDF?

  * Canvas's native HTML page rendering respects user accessibility
    settings (zoom, screen reader, etc.) in a way no PDF viewer can
    universally guarantee.
  * Faculty edits land in the HTML, not the PDF, so the published
    version stays in sync with the reviewed accessible content.
  * The PDF stays available as a source-copy link, labeled
    "Original PDF (source copy)" in the panorama overlay.

### What is and is NOT a tagged accessible PDF?

`src/canvas/alt_formats.py:render_ocr_pdf` produces a **Searchable
OCR PDF** (PDF/A archival format with a text layer). This is NOT
the same as a PDF/UA tagged accessible PDF — PDF/UA tagging
requires a proper structure tree, alt text on figures, reading
order metadata, and language markers, none of which `ocrmypdf`
produces. The label in the panorama modal reflects this honestly.

## Production hardening checklist (added 2026-05-20)

The following env vars and configuration steps are *required* for a
defensible production deployment:

| Setting | Why | Where |
|---|---|---|
| `CANVAS_ALLOWED_ORIGINS` | Origin allowlist for state-changing routes. Fail-closed when set. | `.env` |
| `CSRF_SECRET_KEY` | HMAC secret for per-session CSRF tokens. 32+ chars random. | secrets store |
| `TOKEN_ENCRYPTION_KEY` | Encrypts OAuth tokens at rest in Redis. 32+ chars random. | secrets store |
| `REQUIRE_WCAG_GATE=true` | Refuses to publish until automated WCAG checks pass and reviewer checklist is complete. | `.env` |
| Redis auth + TLS | Tokens, sessions, and audit records all live in Redis. | infra |
| `enable_api_key_auth=true` | Layered auth: API key + LTI session. | `.env` |

## What's audited and where

Every approve/reject/request-edits/pii-decision/delete action lands
in `eq-pdf:canvas:approval:audit` as an immutable Redis list entry
with: job_id, action, actor_user_id, actor_name, course_id, IP,
comment, timestamp. Exported via `GET /canvas/panorama/audit/approvals.csv`
(admin-only — see Phase 4 in the roadmap).

## How to run the test suite

```powershell
docker compose exec api-gateway uv run pytest tests/unit/canvas/ -v
```

The unit suite under `tests/unit/canvas/` covers:

  * `test_sanitize.py` — XSS payloads, academic structure preservation,
    idempotency, URL allowlist.
  * `test_wcag_checks.py` — pristine doc passes; lang/title/alt/th/link
    failures fire expected rule IDs; heading-skip warnings.
  * `test_signals.py` — heading detection, image-alt counting, table
    detection, score function differentiates across profiles.
  * `test_csrf_helpers.py` — token stability per session, distinct
    across sessions, well-formed hex.
  * `test_privacy_crypto.py` — token encryption round-trip, legacy
    plaintext pass-through, nonce randomness.

## Known follow-ups (not blocking institutional pilot)

  * **Conversion-quality score tuning** — current thresholds let
    documents with one missing alt-text image still reach "green."
    See task #103.
  * **Polly opt-in** — MP3 audio rendering requires real AWS Polly
    credentials separate from Floci stubs; defaults to UnrecognizedClientException
    in dev. Either set real AWS creds or document the feature as
    "production-only."
  * **Syllabus scope** — the watcher's syllabus scan fires 401
    because the API Key's scope list omits `url:GET|/api/v1/courses/:course_id`.
    Either add the scope or suppress the syllabus scan.
  * **Favicon 500** — `/favicon.ico` returns a stack trace because
    `/app/static/viewer/index.html` doesn't exist. Cosmetic.
  * **Multi-key JWKS** — current LTI keypair serving is single-key;
    rotation requires brief downtime. Multi-key with overlapping
    windows is the proper fix.
