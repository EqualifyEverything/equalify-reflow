# Multi-Tenant Canvas Integration Architecture

## Summary

Equalify Reflow is a Canvas LMS integration intended to run as a single
hosted service serving many Canvas-hosted schools simultaneously. This
document describes the authentication, data, and onboarding architecture
that makes that possible. The core shift is from a per-instructor
Canvas API token (the current MVP) to LTI Advantage client-credentials
service tokens. The tool has one identity (its Developer Key); each
school's admin grants that identity a defined set of Canvas API scopes
at install time; and the same Reflow code runs unchanged against every
Canvas instance.

The migration described in section 9 is roughly 1.5 dev-days of work,
broken into seven incremental phases that can each ship behind a feature
flag without taking the existing single-tenant path offline.

## Problem statement

The current Canvas client is hardcoded to one Canvas instance and one
bearer token (`settings.canvas_api_token`). This works for an MVP being
demoed in a single test course, but it has three structural problems
for a real integration:

1. A long-lived user-impersonating token is the wrong audit surface for
   a vendor integration. Every call appears to come from the user who
   minted the token, with that user's full permission scope, with no
   indication that the call was on behalf of a tool. Canvas IT teams
   uniformly refuse to deploy vendors that ask for one.

2. The token's permissions follow that user's enrollments. If the user
   leaves the institution, loses admin rights, or has their token
   rotated, the integration silently breaks for every course that was
   relying on it. There is no admin-visible "Reflow has the following
   permissions" surface.

3. The model does not generalize to N institutions. A second school
   would need a second token in a second env var, with a second Canvas
   base URL, and either a second Reflow deployment or careful per-call
   tenant routing that nothing in the current code does. The `tk()`
   helper hints at this future but doesn't actually implement it.

LTI Advantage client-credentials solves all three. The tool is the
principal, scopes are admin-granted and visible in the Developer Key
UI, and the same code routes to whichever platform issued the launch.

## High-level architecture

The auth surface has three actors and four flows.

The actors are: the Reflow tool (us); a Canvas platform instance (one
per school, e.g. `canvas.csueastbay.edu`); and a Canvas user whose
browser is initiating the launch.

The flows are:

**LTI launch (already implemented).** Canvas redirects the user's
browser to `/lti/login`, we redirect to Canvas's auth endpoint, Canvas
POSTs a signed `id_token` back to `/lti/launch`. We validate signature,
issuer, audience, and `deployment_id`. We create a session cookie
keyed to the platform + user + course and hand off to the review UI.

**Service-token request (new).** When the watcher or bridge needs to
call Canvas, it asks `canvas/oauth.py` for a bearer token for the
specific platform and scope. The OAuth module signs a JWT with our
private key claiming `iss=our-client-id`, `sub=our-client-id`,
`aud=<platform-token-url>`, and POSTs it to the platform's
`/login/oauth2/token` endpoint with `grant_type=client_credentials` and
`scope=<requested-scopes>`. The platform validates against the public
JWK we host at `/lti/jwks`, returns a short-lived bearer token
(typically 1 hour), which we cache by `(platform_id, scope_set)`.

**Canvas API call (refactored).** `CanvasClient` looks up the calling
context's platform, requests a token for the scopes the call needs,
and makes the HTTP request against that platform's API base URL. The
client itself becomes platform-agnostic; the platform identity is
threaded in from the caller.

**JWKS publication (already implemented).** Canvas fetches our public
JWK from `/lti/jwks` to verify the JWT we send during service-token
requests, and also to verify our outgoing LTI Advantage assertions
(NRPS, AGS) for the same reason. The same key pair signs both.

## Data model

The new persistence primitive is `PlatformInstall`. One record per
unique Canvas instance that has launched our tool. Schema:

```python
@dataclass
class PlatformInstall:
    # Identity (composite primary key).
    issuer: str               # e.g. "https://canvas.csueastbay.edu"
    client_id: str            # the Developer Key id this platform issued us
    deployment_id: str        # tool placement instance (one per install)

    # Endpoints we use.
    auth_token_url: str       # client-credentials endpoint
    auth_login_url: str       # OIDC auth endpoint (for the launch dance)
    jwks_url: str             # platform's JWKS, for validating launches
    canvas_api_base: str      # https://canvas.csueastbay.edu/api/v1

    # Discovered or pre-shared metadata.
    canvas_domain: str        # bare hostname for logging
    granted_scopes: list[str] # what this platform actually approved
    label: str | None         # human-friendly name for ops dashboards

    # Audit.
    first_seen_at: datetime
    last_launch_at: datetime
```

Records are written on every successful LTI launch (created if absent,
updated otherwise). Redis storage with key
`eq-pdf:platforms:{sha256(issuer+client_id+deployment_id)}` and a
secondary index by `issuer` for the watcher to enumerate.

The endpoint URLs come from one of three sources, in priority order:

1. The launch JWT's `https://purl.imsglobal.org/spec/lti/claim/tool_platform`
   claim, when present. Canvas includes `name` and `version` but not
   the API URLs.

2. The `iss` claim's host, mapped to canonical Canvas URLs:
   `https://<host>/login/oauth2/token`,
   `https://<host>/api/lti/authorize_redirect`,
   `https://<host>/api/lti/security/jwks`,
   `https://<host>/api/v1` for the data API.
   This works for every Canvas instance because the URL conventions are
   identical across Instructure-hosted and self-hosted Canvas.

3. Manual override via a `platform_overrides.yaml` file shipped with the
   container, for the rare self-hosted Canvas with non-standard URLs.

## Service-token flow (detail)

The client-credentials flow uses the JWT bearer client authentication
profile (RFC 7521 + RFC 7523), which is what LTI Advantage requires.
The request shape:

```
POST {platform.auth_token_url}
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer
&client_assertion=<signed JWT>
&scope=<space-separated scopes>
```

The signed JWT's claims:

```json
{
  "iss": "<our-client-id-for-this-platform>",
  "sub": "<our-client-id-for-this-platform>",
  "aud": "<platform.auth_token_url>",
  "iat": <now>,
  "exp": <now + 300>,
  "jti": "<uuid4>"
}
```

Signed with our RS256 private key, kid pointing at a key in our public
JWKS. The platform validates the signature, confirms `iss == sub ==
known client_id`, confirms `aud` matches its own token endpoint, and
checks that the JTI has not been replayed within the last few minutes.

Response on success:

```json
{
  "access_token": "<bearer>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "<actual-granted-scopes>"
}
```

The access token is cached in Redis with key
`eq-pdf:service-token:{platform_id}:{scope_hash}` and TTL set to
`expires_in - 30` seconds. The 30-second buffer avoids handing out a
token that expires mid-request.

Cache misses request a fresh token. Cache hits skip the network entirely
and return the cached bearer. A 401 from Canvas invalidates the cache
entry and retries once with a fresh token before surfacing the failure
to the caller.

## Per-tenant data isolation

The current `tk()` helper prepends a fixed prefix (`eq-pdf:`) to every
Redis key. This prevents collision with other apps on the same Redis
but doesn't prevent collision between platforms.

The refactor extends `tk()` to be platform-aware:

```python
def tk(suffix: str, *, platform: PlatformInstall | None = None) -> str:
    """Build a tenant-prefixed Redis key.

    Without a platform, returns the legacy prefix for ops keys
    (queues, dead-letter, deployment metadata) that are not per-tenant.

    With a platform, sandboxes the suffix under that platform's
    issuer hash so two schools cannot read each other's data.
    """
    if platform is None:
        return f"eq-pdf:{suffix}"
    return f"eq-pdf:p:{platform.platform_id}:{suffix}"
```

Existing callsites pass `platform=...` from the request context. The
session cookie carries the platform_id so per-request code can look it
up without re-deriving from the issuer claim.

Keys that stay shared (the alt-formats cache by content hash, the
service-token cache by `platform_id` already, and rate-limit counters)
remain at the unscoped prefix. Everything user-facing or course-facing
(jobs, file processing state, consent records, NRPS rosters, dial
scores) becomes platform-scoped.

This is checked by a unit test that walks every callsite and asserts
that "course_id" or "user_id" never appears in a key that isn't
platform-prefixed.

## Scope inventory

These are the Canvas API scopes Reflow needs. Each is granted
individually in the Developer Key UI and surfaced in `/lti/config.json`
so admins see them at registration time:

| Purpose | Scope |
|---|---|
| Discover PDFs in a course | `url:GET\|/api/v1/courses/:course_id/files` |
| File metadata (size, type, modified_at) | `url:GET\|/api/v1/files/:id` |
| List modules + module items | `url:GET\|/api/v1/courses/:course_id/modules` |
| List pages + their bodies (for inline file refs) | `url:GET\|/api/v1/courses/:course_id/pages` |
| Create accessible Page | `url:POST\|/api/v1/courses/:course_id/pages` |
| Update accessible Page (faculty edits) | `url:PUT\|/api/v1/courses/:course_id/pages/:url_or_id` |
| Publish accessible Page (approval) | `url:PUT\|/api/v1/courses/:course_id/pages/:url_or_id` |
| Notify faculty | `url:POST\|/api/v1/conversations` |
| Roster (NRPS, for pre-warming - Batch 6) | `https://purl.imsglobal.org/spec/lti-nrps/scope/contextmembership.readonly` |
| Deep Linking (RCE picker, Batch 6) | `https://purl.imsglobal.org/spec/lti-dl/scope/lineitem` (or its readonly counterpart) |

The first two are LTI Advantage standard scopes that come from IMS, not
Canvas. The Canvas-specific scopes use Canvas's `url:METHOD|/api/...`
shorthand.

A first launch at a new school may arrive with **none** of these
granted (the admin pasted the Developer Key but hasn't reviewed scopes
yet). The tool surfaces this gracefully: the launch still succeeds, the
review UI loads, and any API call that would require an ungranted scope
returns a structured "permission_missing" response that the UI renders
as "Ask your admin to grant Reflow the X scope" with a deep link to
the Canvas Developer Key admin page.

## Onboarding flow (admin perspective)

A new school installs Reflow once:

1. Admin gets the Reflow Developer Key JSON from us (production:
   `https://reflow.equalify.app/lti/config.json`).

2. Admin opens **Canvas → Admin → Developer Keys → + Developer Key →
   LTI Key → Paste JSON**. Reviews the scopes list, accepts.

3. Admin notes the resulting `client_id`, sends it to us so we can
   record it in our platform registry (or, in the self-serve model,
   our portal accepts the client_id and verifies via a test launch).

4. Admin installs the tool at the desired scope (sub-account or
   account-level) via **Settings → Apps → + App → By Client ID →
   paste client_id → Install**.

5. Admin places the tool wherever they want students/faculty to see it:
   course navigation, the RCE toolbar, account-level redirects.

6. The first launch from anywhere in that institution creates a
   `PlatformInstall` record on our side. Subsequent launches just
   update `last_launch_at`. Faculty consent flow runs once per faculty.

What we never ask for at any point: an admin user's API token, an SSH
key, a webhook receiver URL on their network, or anything that grants
us privileges beyond the scopes they reviewed in step 2. This is the
property that gets us through procurement.

## Migration plan (from current state)

Seven phases, sequenced by dependency. Each phase can land behind a
feature flag (`MULTI_TENANT_AUTH=true` or per-component flags) so the
existing single-tenant path keeps working until the new path is
proven.

**Phase 1: PlatformInstall data model.** Build the dataclass, the
Redis storage helpers, the upsert call from the LTI launch handler.
No behavior change yet — the records are written but nothing reads
them. Includes a CLI (`scripts/list_platforms.py`) for ops visibility.
*Estimate: 2 hours.*

**Phase 2: `canvas/oauth.py` service-token client.** Implement the JWT
bearer client-credentials flow against a single platform (the one
configured in env). Add the Redis token cache. Add a `--test` flag to
the CLI to mint a token and dump the response for diagnostic use.
*Estimate: 3 hours.*

**Phase 3: `CanvasClient` refactor.** Add a constructor that takes a
`PlatformInstall` + a required scope, internally calls the oauth
module, drops the constructor-level api_token in favor of per-call
scoped tokens. Keep the env-token constructor signature alive for one
release as the dev fallback. *Estimate: 2 hours.*

**Phase 4: Developer Key scope list.** Update `/lti/config.json` with
the full scope inventory from section 7. Mint a new Developer Key in
canvas.test.instructure.com with the new scopes. Verify a service
token request returns the expected `scope` field. *Estimate: 30 min
+ Canvas UI work.*

**Phase 5: Watcher refactor.** Watcher iterates `PlatformInstall`
records × the courses each has touched (derived from the
`canvas:course:{course_id}:processed` set we already maintain, now
namespaced by platform). Each course scan uses a `CanvasClient`
constructed for that platform. *Estimate: 3 hours.*

**Phase 6: Bridge worker refactor.** Same shape as the watcher.
Bridge looks up the platform from the job record, constructs a
`CanvasClient` for it, creates the page. *Estimate: 2 hours.*

**Phase 7: Per-platform Redis prefixing.** Extend `tk()` to accept
a platform, audit every callsite, update tests. This is the riskiest
phase because it touches every read and write; it lands last and
behind a "dual-read" flag (read from both old and new prefixes
during the cutover window). *Estimate: 4 hours including the audit.*

**Total: ~17 hours, sequenced over 2-3 working days.**

The single-tenant code path is removed in a follow-up release after at
least one real second school is running on the multi-tenant path in
production, to avoid burning a bridge before the new one is load-tested.

## Security considerations

**Private key storage.** The RSA-2048 key that signs everything lives
in `/tmp/lti_private.pem` today, regenerated on every container start
— fine for a dev MVP, fatal for production. The Fargate Terraform
module (Batch 7) already wires up an AWS Secrets Manager secret
intended to hold this key. The migration to production should set
that up *before* the first non-test deployment so we don't end up
with a long-lived key floating in a container layer.

**Key rotation.** The JWKS endpoint should serve multiple keys with
overlapping validity windows so we can rotate without a flag day.
Concretely: a new key gets published in JWKS 24 hours before it
becomes the signing key, and the previous key remains in JWKS for
24 hours after a new one starts signing. Canvas refetches JWKS on
signature failure, so this gives us a smooth rotation. Implementation
is a small daemon that rolls the active key kid in Redis and the
JWKS endpoint reads the union of active and recently-retired keys.

**Scope creep.** Every scope we add to `/lti/config.json` after
release will require every existing institution's admin to manually
re-approve the Developer Key. Treat the scope list as an immutable
public API and only add scopes through a deliberate versioning bump.
The internal review process for "do we need this scope" should be
gated on the same severity as a database migration.

**Per-platform incident isolation.** If Canvas at school A starts
returning malformed responses or invalid tokens, the bridge worker
should not stall the queue for schools B-Z. Each platform gets its
own work queue partition (Redis stream) so backpressure on one
doesn't propagate. This is a Phase 5/6 implementation detail but
worth calling out as a design constraint.

**Audit log.** Every service-token request, every Canvas API call,
and every Page creation/publish should write a structured log line
with `(platform_id, user_id, course_id, scope, http_status,
latency_ms, request_id)`. Ship those to whatever log aggregator the
production environment uses. The motivation is that when an admin
asks "what did Reflow do in our Canvas yesterday", we need to answer
within five minutes from data, not from memory.

## Open questions

A few decisions to make before Phase 1 starts:

**Should `PlatformInstall` records be revocable from our side?** A
school can revoke us by deleting the Developer Key on their end, but
should we also be able to mark a platform as "frozen" from the ops
console (e.g. during a security incident at that school)? Recommend
yes, with a `revoked_at` field that causes the launch handler to
refuse new launches but lets the bridge finish in-flight jobs.

**Where does the platform registry live in production?** Redis is
fine for the cache layer but the canonical registry should arguably
be a real database (Postgres) for backup, point-in-time recovery, and
external query. The MVP can run on Redis; the production cutover
should migrate the registry to RDS at the same time as the rest of
the persistence layer moves off Redis-only.

**Self-serve install vs. white-glove install for the first few
schools?** A self-serve portal where admins paste their client_id and
verify via a test launch is the right end state, but for the first
3-5 schools we should do white-glove (we accept the client_id over
email, manually create the record) so we can sit in on each
institution's procurement conversation and learn what they actually
ask about. Recommend white-glove until at least 5 schools are
running, then build the self-serve portal.

**Deep Linking placement decisions.** The Batch 6 deferred work
includes both RCE Deep Linking and an account-level redirect. The
multi-tenant model should make those installable independently — a
school that only wants the RCE picker shouldn't be forced to enable
the redirect. This is a Developer Key config question more than an
auth-architecture question, but worth flagging.

**LTI Names and Roles Service (NRPS) caching.** NRPS pulls a course
roster. If the watcher pre-warms by fetching NRPS for every active
course on every platform, that's a lot of API calls. Recommend
caching NRPS results for 1 hour and only refreshing on cache miss
or explicit roster-change events. The cache key includes the platform
and course; the cache invalidator listens for `context_membership`
events from Canvas Live Events when those become available.
