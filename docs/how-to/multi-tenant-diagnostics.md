# Multi-tenant migration: tomorrow's diagnostic guide

All 7 phases of the multi-tenant migration are implemented and unit-verified
against fake Redis and mocked Canvas. This document is the runbook for
flipping it into the live stack and diagnosing whatever Canvas tells us
when real traffic hits it.

## What landed

The full list of files changed or created tonight:

| File | Status | Purpose |
|---|---|---|
| `src/lti/platform.py` | new | `PlatformInstall` dataclass + endpoint derivation |
| `src/lti/platform_store.py` | new | Redis CRUD + course-platform map |
| `src/lti/routes.py` | edited | Launch handler auto-upserts platform + course |
| `src/canvas/oauth.py` | new | LTI Advantage client-credentials token client |
| `src/canvas/client.py` | rewritten | Adds `CanvasClient.from_platform()` + 401 retry |
| `src/canvas/state.py` | edited | `CanvasJob.platform_id` field added |
| `src/canvas/tenant.py` | edited | `tk(suffix, platform=...)` per-tenant prefix |
| `src/workers/canvas_watcher.py` | edited | Iterates platforms x courses behind flag |
| `src/workers/reflow_bridge_worker.py` | edited | Picks per-job Canvas client |
| `src/config.py` | edited | `multi_tenant_watcher` boolean flag |
| `scripts/list_platforms.py` | new | Ops CLI: `python -m scripts.list_platforms` |
| `scripts/test_service_token.py` | new | Ops CLI: mint a token to test the flow |

## Step 1: restart the stack

> **Architecture note**: the watcher and reflow-bridge run as background
> asyncio tasks inside the api-gateway process, not as separate services.
> One `docker compose up api-gateway` restarts all three.


```powershell
$env:COMPOSE_FILE = "docker-compose.yml;docker-compose.dev.yml"
docker compose up -d --force-recreate api-gateway
Start-Sleep 10
docker compose exec api-gateway sh -c "openssl genrsa -out /tmp/lti_private.pem 2048 && openssl rsa -in /tmp/lti_private.pem -pubout -out /tmp/lti_public.pem"
docker compose logs --tail=20 api-gateway
```

If any of the three containers crash on boot, paste the traceback. Most
likely failure mode: a Pydantic validation error because the new
`multi_tenant_watcher` field collided with something in `.env`. Should
be defaulted to `False` so unset is fine.

## Step 2: trigger a launch + confirm Phase 1 still works

Click **Accessible Documents** in Canvas. Then:

```powershell
docker compose exec api-gateway uv run python -m scripts.list_platforms
```

Expected: one row showing the canvas.test.instructure.com platform.
The `LAST LAUNCH` column updates on every click. The `platform_id`
will be a stable 16-char hash; copy it for step 3.

If you see zero rows, the launch upsert failed silently. Search logs
for `PlatformInstall upsert failed`:

```powershell
docker compose logs api-gateway | Select-String "PlatformInstall"
```

## Step 3: test the service-token exchange (Phase 2)

This is the critical step — it's where real Canvas first sees our
multi-tenant work.

```powershell
docker compose exec api-gateway uv run python -m scripts.test_service_token
```

Three outcomes:

**Outcome A — OK.** You see `granted_scope:` populated with the scopes
that were granted in the Developer Key. Everything works end-to-end.
Proceed to step 4.

**Outcome B — `invalid_client` or `unauthorized_client`.** The
Developer Key in Canvas doesn't know about our current JWKS. This
happens because:

  - The container was restarted, regenerating `/tmp/lti_private.pem`,
    which gave the JWKS endpoint a new public key with a new `kid`.
  - Canvas's cached JWKS still has the old key, so signature
    verification fails.

Fix: in Canvas, go to **Admin → Developer Keys → find the Reflow key →
edit → click the public_jwk_url** to force a fresh fetch. Or wait
~10 minutes for Canvas's TTL to expire. Or stop regenerating the
keypair on every restart (Step 6 below).

**Outcome C — `invalid_scope` / `unauthorized_scope`.** The token
exchange is working but no scopes are granted. Open the Developer Key
in Canvas, click **Edit**, scroll to the **Scopes** tab. You should
see the full list from `/lti/config.json` (Phase 4 work). Check each
of the 14 scopes we declared:

```
url:GET|/api/v1/courses/:course_id/files
url:GET|/api/v1/courses/:course_id/folders
url:GET|/api/v1/courses/:course_id/modules
url:GET|/api/v1/courses/:course_id/modules/:module_id/items
url:GET|/api/v1/courses/:course_id/pages
url:GET|/api/v1/courses/:course_id/pages/:url_or_id
url:GET|/api/v1/courses/:course_id/discussion_topics
url:GET|/api/v1/courses/:course_id/discussion_topics/:topic_id/entries
url:GET|/api/v1/courses/:course_id/assignments
url:GET|/api/v1/courses/:course_id/quizzes
url:GET|/api/v1/files/:id
url:GET|/api/v1/folders/:id/files
url:POST|/api/v1/courses/:course_id/pages
url:PUT|/api/v1/courses/:course_id/pages/:url_or_id
url:POST|/api/v1/conversations
```

Plus two IMS standard scopes:

```
https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly
https://purl.imsglobal.org/spec/lti-ags/scope/lineitem.readonly
```

(If the scopes list shows the OLD empty array, Canvas is caching the
old `/lti/config.json`. You may need to **delete + recreate the
Developer Key**, pasting the fresh JSON.)

Save the Developer Key. Re-run `test_service_token` — should now
return `OK` with `granted_scope:` populated.

## Step 4: flip the watcher into multi-tenant mode (Phase 5)

In `.env`, add:

```
MULTI_TENANT_WATCHER=true
```

Then restart just the watcher:

```powershell
docker compose up -d --force-recreate api-gateway
docker compose logs --tail=15 api-gateway | Select-String watcher
```

Expected log line on boot:

```
INFO Canvas watcher: multi-tenant mode enabled
```

The watcher will now iterate every platform × every course mapped to
that platform. To verify which courses it found:

```powershell
docker compose exec redis redis-cli SMEMBERS "eq-pdf:lti:platform:<your-pid>:courses"
```

Substitute `<your-pid>` from `list_platforms`. Should contain `50594`
(the course you launched from).

If no scan happens, check the watcher logs:

```powershell
docker compose logs api-gateway --tail=60 | Select-String "scan|Failed|enumerate"
```

The most likely issue is that the bridge needs `manage_wiki` scope on
the Developer Key to create Pages. If you skipped that check in
step 3, it'll fail again here when the bridge tries to create the
draft Page. The patch in `canvas_review.py` from earlier (approve
without a page) covers this — approval still works, the page just
doesn't get created.

## Step 5: verify a new PDF flows through the multi-tenant path

In Canvas, upload a brand-new small PDF to a module in course 50594.
Within a minute, the watcher should pick it up, and you should see in
the bridge logs:

```
INFO service token minted: platform_id=<pid> scope_hash=<hash> granted=...
```

That's Phase 2 + Phase 5 + Phase 6 all firing together. The bridge is
now talking to Canvas with a service token, not the personal API
token.

If you see `Bridge picks platform from job.platform_id` in the logs,
the per-job multi-tenant routing is working as designed. If you see
`Bridge env-token fallback`, the watcher created the job without
stamping the `platform_id`, which means `multi_tenant_watcher` is not
actually true — recheck step 4.

## Step 6 (later): persist the LTI keypair

The keypair regenerates on every container restart because it lives in
`/tmp/lti_private.pem`. That's fine for dev but will keep breaking the
Developer Key trust loop. Two fixes, easiest first:

1. Move the key path to a mounted volume:
   ```yaml
   # docker-compose.dev.yml
   volumes:
     - ./keys:/app/keys
   environment:
     - LTI_PRIVATE_KEY_PATH=/app/keys/lti_private.pem
     - LTI_PUBLIC_KEY_PATH=/app/keys/lti_public.pem
   ```
   Generate once: `docker compose exec api-gateway python -m src.lti.keys generate`
   The keys persist across restarts. Canvas's cached JWKS stays valid.

2. For Fargate (production), the Terraform module in
   `infrastructure/fargate/main.tf` already provisions an AWS Secrets
   Manager secret; the container reads from there at boot.

## Common failure modes I anticipate

**`ServiceTokenError: platform is soft-revoked`.** Someone called
`mark_revoked` on the platform via `platform_store`. Clear it:

```powershell
docker compose exec api-gateway python -c "
import asyncio
from redis.asyncio import Redis
from src.config import settings
from src.lti.platform_store import clear_revoked
async def main():
    r = Redis.from_url(settings.redis_url)
    print(await clear_revoked(r, '<pid>'))
    await r.aclose()
asyncio.run(main())
"
```

**`No platforms registered yet.`** The launch upsert never ran or
failed silently. Either the LTI launch isn't actually happening (404 /
401 on `/lti/launch` first) or the upsert path errored. Check
api-gateway logs for `PlatformInstall upsert failed`.

**Watcher scans the wrong course because it's in both lists.**
Multi-tenant precedence is: if a course is registered under a
platform, that wins. The legacy `canvas_watched_courses` list is only
used for courses NOT yet associated with any platform. If you want a
course strictly platform-routed, remove it from
`canvas_watched_courses`.

**401 retry storm.** If a service token keeps getting rejected, the
client invalidates + refreshes, then gets rejected again, then surfaces
the error. There is no infinite-retry loop. If you see the error
repeating in logs, the issue is on the Canvas side (Developer Key
revoked, scopes removed, key rotated).

## Tomorrow's first command

```powershell
$env:COMPOSE_FILE = "docker-compose.yml;docker-compose.dev.yml"
docker compose up -d --force-recreate api-gateway
Start-Sleep 10
docker compose exec api-gateway sh -c "openssl genrsa -out /tmp/lti_private.pem 2048 && openssl rsa -in /tmp/lti_private.pem -pubout -out /tmp/lti_public.pem"
docker compose exec api-gateway uv run python -m scripts.list_platforms
```

If that shows zero platforms, click the Reflow link in Canvas once and
re-run. Then:

```powershell
docker compose exec api-gateway uv run python -m scripts.test_service_token
```

That single command tells us where we are. Paste its output and we'll
go from there.
