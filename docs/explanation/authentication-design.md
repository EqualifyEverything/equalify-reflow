# Authentication design

The API is a single public surface with a narrow authenticated lane: `/api/*`. Everything else — the viewer SPA, Swagger, OpenAPI, health, metrics — is intentionally public. Two design decisions are worth understanding because they are not obvious from the code alone.

For configuration, header names, and public-endpoint lists, see the [authentication reference](../reference/authentication.md).

## Why the viewer can call `/api/v1/*` without an API key

The Pipeline Viewer SPA is served from the same origin as the API. Its JavaScript calls `/api/v1/*` without injecting `X-API-Key`, and the middleware recognises these as same-origin by inspecting the browser-set `Sec-Fetch-Site: same-origin` header combined with the absence of an API key. This is safe because:

1. **CORS prevents spoofing.** External origins cannot read responses or forge cross-origin requests that masquerade as same-origin. The same-origin bypass only applies to traffic that the browser itself stamps as same-origin.
2. **External clients take the normal branch.** Any client that sends `X-API-Key` goes through the usual validation path. The bypass is a fallback for headerless same-origin fetches, not a replacement for auth.
3. **`Sec-Fetch-Site` is browser-controlled.** Page scripts cannot set or spoof it; only the browser chrome can. An attacker cannot forge the header from within a malicious page.

Implementation: `_is_demo_ui_request` in `src/middleware/api_key_auth.py`.

## Why SSE needs stream tokens

The browser's native `EventSource` API cannot send custom headers. That rules out using `X-API-Key` for streaming. Two alternatives were weighed:

- **API key in the URL** — logged by proxies and servers, cached by history, leaked in referrer headers. Not acceptable.
- **Short-lived single-use token in the URL** — leaked-token exposure is bounded to five minutes, one job, and one consumption.

Stream tokens take the second path. They are short (5-minute TTL), job-scoped, and deleted on first validation (`GETDEL` in Redis). This trades a tiny bit of complexity — one extra round-trip to exchange an API key for a token — for a much smaller blast radius when a token does leak.

## Why approval endpoints require two credentials

`/api/v1/approval/*` requires **both** an API key and a valid approval token. This is defense-in-depth, not redundancy:

- **API key** proves the caller is an authorised system.
- **Approval token** proves the caller has permission for the specific job under review.

Either layer alone is not enough. A leaked API key should not grant the ability to approve or deny arbitrary PII-flagged documents — only the approver who received the token for a specific job can act on it.

## Why keys live as `SecretStr`

Accidental logging is the most common way secrets escape. Pydantic's `SecretStr` wraps key values so that default `str()` and `repr()` output `**********`. The actual value is only available via `.get_secret_value()`, which forces a conscious choice at every read site. Combined with constant-time comparison (`secrets.compare_digest()`), this prevents both log leaks and timing-based discovery attacks.

## Why viewer auth is layered on top, not bolted in

The OSS default is `AUTH_MODE=none`: anyone who can reach the URL can use the viewer. That's deliberate — the project is deployed by groups who specifically want a low-friction tool for course materials. Forcing every deployment through a login layer would push them onto the API directly, which is harder to use accessibly.

Operators who want to lock the deployment down get an opt-in. Three constraints shaped the design:

1. **Off must mean off.** No middleware registered, no cookies set, no `request.state.identity` populated. The accept paths in `APIKeyAuthMiddleware` are bit-identical to before this layer existed when `AUTH_MODE=none`. Any operator who never touches `AUTH_MODE` is unaffected.
2. **Both auth paths must coexist.** Programmatic clients (CI, curl, headless integrations) authenticate with API keys today. Forcing them onto an OIDC service-account flow as the price of turning on user auth would break every existing integration. So when sessions are on, an `X-API-Key` still works in parallel; the request needs **either** a valid session **or** a valid API key.
3. **Pluggable by config, not by code.** Microsoft Entra is the named SSO requirement, but other deployments will want Google, Okta, or generic OIDC. The implementation is one `OIDCAuthProvider` parametrised by a discovery URL — Entra is just the URL `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`. Adding a provider is one JSON entry in `AUTH_OIDC_PROVIDERS`.

Two middlewares (`SessionAuth` ahead of `APIKeyAuth`) instead of a unified middleware kept each piece single-responsibility and made the test matrix tractable. The session middleware's only job is to read the cookie and stamp `request.state.identity`; the API-key middleware's only job is to accept-or-reject. The combined accept rule emerges from their composition: pass if any of (a) valid session, (b) valid API key, (c) `?token=` for SSE, (d) dev-only routes in dev. The same-origin shortcut from before this PR — `Sec-Fetch-Site: same-origin` with no API key — is gated on `AUTH_MODE=none` from PR1 onwards, since otherwise it would silently defeat the new gate.

## Why argon2id instead of bcrypt for basic mode

OWASP currently lists argon2id first among password-storage recommendations. `passlib`'s bcrypt path has versioning issues against modern bcrypt distributions, and the library itself is in maintenance mode. `argon2-cffi` is small, focused, and exposes a one-line verify API (`PasswordHasher().verify(hash, password)`) that handles parameter parsing and constant-time comparison internally.

The basic provider also runs a verify against a known-bad reference hash on the **unknown-user** path so that timing for unknown-user matches wrong-password — preventing the timing oracle that says "yes, this username exists, the password is just wrong."

## Why stateless cookies, not Redis-backed sessions

Phase 1 + 2 use a stateless signed cookie via `itsdangerous`. The cookie *is* the session — there's no server-side lookup per request. Trade-offs:

- **Pro:** zero round-trip cost on every request; Redis can be down without breaking auth.
- **Pro:** trivial to scale horizontally — no shared session store needed across replicas.
- **Con:** can't revoke a single user's sessions without rotating `AUTH_SECRET_KEY`, which kicks every user out.
- **Con:** sessions can't be queried by an admin ("who's logged in right now?") without a separate index.

For the OSS default and self-hosted deployments, stateless wins by a comfortable margin. The `SessionStore` Protocol is in place from day one so a Redis-backed implementation can be swapped in for Phase 3 with a one-line change in `factory.py`. Operators who need per-user revocation (e.g. compliance teams) can opt in then.

## Why `SameSite=Lax` (and not `Strict`)

SSO via OIDC redirects out to the IdP and back. The browser's first request after the redirect-back is a top-level GET to our callback — `SameSite=Strict` would *not* send our session cookie on that request, silently breaking login on a user's first visit. `Lax` permits the cookie on top-level navigations, which is exactly the OIDC flow's pattern. CSRF on POST endpoints is handled separately via the `X-CSRF-Token` double-submit cookie, so we get the SSO compatibility without sacrificing CSRF defence.

## Why no signup endpoint for basic mode

Self-signup implies a user database, an email-verification flow (or an open-by-default surface), and an admin UI for moderation. That's an entire product surface that doesn't belong in an OSS tool deployed by small operators who want a *locked-down* viewer. Operator-provisioned users via env keep the threat model the same as API keys: the people with access are the people the operator deliberately gave access to.

For deployments that need self-signup, OIDC is the right answer — federate to an IdP that already handles signup, password reset, and account hygiene.
