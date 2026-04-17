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
