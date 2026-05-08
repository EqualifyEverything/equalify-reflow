# Configure SSO (Microsoft Entra and other OIDC providers)

This how-to switches the viewer from password-based local logins to single sign-on against an OpenID Connect identity provider. Microsoft Entra is the worked example because it's the named requirement; the same recipe works for Google, Okta, Auth0, Keycloak, or any IdP that publishes an OIDC discovery document.

For local username/password auth without an IdP, see [enable basic auth](enable-basic-auth.md) instead.

## What changes when SSO is on

- Users log in via the IdP. **First-time users are auto-provisioned** — the OIDC callback validates their ID token and a session is created on the spot. There is no separate "sign up" step and no user database to maintain on our side; the IdP is the source of truth for who exists.
- API calls under `/api/*` accept either a session cookie (browser flow) or an `X-API-Key` (programmatic clients). API keys keep working untouched.
- Identity (sub, email, name, provider_id) lands in every request log line, plus a structured `auth_event` record on each login/logout.

## Prerequisites

You need **admin access in the IdP's tenant** to register an application. For Entra that means an Azure AD admin role (Application Administrator or Global Administrator) in the tenant the users will sign in from.

The deployment must terminate HTTPS at a known hostname. The IdP redirects users back to a registered URL — wildcards are not allowed in OAuth, so you must register the exact callback URL.

## Microsoft Entra walkthrough

### 1. Register the application in Entra

In the [Azure portal](https://portal.azure.com) → **Entra ID** → **App registrations** → **New registration**:

- **Name**: anything memorable (e.g. `equalify-reflow-prod`).
- **Supported account types**: usually "Accounts in this organisational directory only" (single-tenant). Multi-tenant works too if you need cross-org users.
- **Redirect URI**: select **Web**, value `https://<your-host>/api/v1/auth/callback/entra`. The `entra` segment matches the `id` you'll use in `AUTH_OIDC_PROVIDERS`. For local testing add `http://localhost:8080/api/v1/auth/callback/entra` too.

Click **Register**. Capture the values shown on the Overview page:

- **Application (client) ID** → goes into `client_id` below.
- **Directory (tenant) ID** → goes into the discovery URL.

### 2. Generate a client secret

In your registered app → **Certificates & secrets** → **Client secrets** → **New client secret**. Pick an expiry (Microsoft caps this at 24 months; 6–12 months is a sensible cadence for rotation). Copy the secret **value** immediately — Azure only shows it once.

This goes into `client_secret` below. Store it in your deployment's secret manager (AWS Secrets Manager, Vault, Kubernetes Secret) — never in version control.

### 3. Configure ID-token claims

In **Token configuration** → **Add optional claim** → **ID** → tick `email`, `family_name`, `given_name`, `preferred_username`. Without these the user's display name and email won't make it into the ID token, and our app falls back to showing just the `sub` (an opaque GUID).

Under **API permissions**, the default `openid` + `profile` + `email` Microsoft Graph delegated permissions are what we use. If your tenant requires admin consent for these, click **Grant admin consent**.

### 4. Set the environment

```env
AUTH_MODE=oidc
AUTH_SECRET_KEY=<32+ random bytes from `openssl rand -hex 32`>
AUTH_OIDC_PROVIDERS=[{"id":"entra","display_name":"Sign in with Microsoft","discovery_url":"https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration","client_id":"<client-id>","client_secret":"<client-secret>","scopes":"openid email profile"}]
```

Substitute `<tenant-id>`, `<client-id>`, `<client-secret>`. The `id` field (`entra`) determines the URL path the IdP redirects back to (`/api/v1/auth/callback/entra`) — must match what you registered in step 1.

### 5. Restart, log in once, verify

```bash
make down && make dev
```

Open the deployment in a browser. You should land on `/login`, see a "Sign in with Microsoft" button, click it, complete the Entra prompts, and arrive on the pipeline viewer with your name in the top-right.

In your terminal, confirm parallel API-key access still works:

```bash
curl -H "X-API-Key: $YOUR_KEY" https://<your-host>/api/v1/feedback/config
```

## Local round-trip before flipping prod

Smoke-test the OIDC config end-to-end on your laptop before you flip a real deployment. This catches client-secret typos, missing optional claims, scope misconfig, and tenant-policy surprises while a broken config only affects you.

Two ways to set this up:

1. **Register a separate "localdev" Entra app** with redirect URI `http://localhost:8080/api/v1/auth/callback/entra` and its own client secret. Two apps means two secrets to rotate, but a leaked dev secret never compromises prod. Recommended.
2. **Register one app with two redirect URIs** — list `https://<your-prod-host>/api/v1/auth/callback/entra` *and* `http://localhost:8080/api/v1/auth/callback/entra` on the same app and reuse one secret. Faster to set up, but a dev-laptop `.env` leak is a prod incident.

Steps for path 1:

1. Repeat steps 1–3 of the Entra walkthrough above against a new app named `equalify-reflow-localdev` (or similar). Use `http://localhost:8080/api/v1/auth/callback/entra` as the redirect URI. Capture the dev tenant ID, client ID, and client secret.
2. In your local `.env`:

   ```env
   AUTH_MODE=oidc
   AUTH_SECRET_KEY=<openssl rand -hex 32>
   AUTH_COOKIE_SECURE=false
   AUTH_OIDC_PROVIDERS=[{"id":"entra","display_name":"Sign in with Microsoft","discovery_url":"https://login.microsoftonline.com/<dev-tenant-id>/v2.0/.well-known/openid-configuration","client_id":"<dev-client-id>","client_secret":"<dev-client-secret>","scopes":"openid email profile"}]
   ```

   Note `AUTH_COOKIE_SECURE=false` — local dev runs over HTTP, so the `Secure` flag would otherwise drop the cookie before it lands. Production must keep it `true`.
3. `make down && make dev`. Open `http://localhost:8080/` and click **Sign in with Microsoft**. You should bounce to `login.microsoftonline.com`, complete sign-in/MFA, and arrive on the pipeline viewer with your name in the top-right.
4. Inspect the request chain in DevTools (Network tab, "Preserve log") to confirm:
   - `GET /api/v1/auth/login/entra` → 302 to `login.microsoftonline.com/<tenant>/.../authorize?...`
   - IdP → 302 back to `/api/v1/auth/callback/entra?code=…&state=…`
   - Our callback → 302 to `/` with `Set-Cookie: reflow_session` and `reflow_session_csrf` on the response
   - `GET /api/v1/auth/me` returns your `sub`, `email`, `name`, `provider_id: "entra"`
5. Confirm the API-key path still works in parallel:

   ```bash
   curl -H "X-API-Key: $YOUR_KEY" http://localhost:8080/api/v1/feedback/config
   ```

If any step fails, fix the config and retry — the broken state never reaches the prod deployment.

## Generic OIDC (Google, Okta, Auth0, Keycloak, ...)

The provider implementation is IdP-agnostic — the discovery URL is the only thing that distinguishes one from another. Reference URLs:

| Provider | Discovery URL pattern |
|---|---|
| Microsoft Entra | `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration` |
| Google | `https://accounts.google.com/.well-known/openid-configuration` |
| Okta | `https://{your-domain}.okta.com/.well-known/openid-configuration` |
| Auth0 | `https://{your-tenant}.auth0.com/.well-known/openid-configuration` |
| Keycloak | `https://{host}/realms/{realm}/.well-known/openid-configuration` |

The redirect URI must end in `/api/v1/auth/callback/<id>` where `<id>` matches your `AUTH_OIDC_PROVIDERS[].id`. Pick something stable — changing it later means re-registering with the IdP.

Multi-provider config (Phase 3 will surface a chooser UI; the backend already supports it):

```env
AUTH_OIDC_PROVIDERS=[
  {"id":"entra","display_name":"Sign in with Microsoft", "discovery_url":"...", "client_id":"...", "client_secret":"..."},
  {"id":"google","display_name":"Sign in with Google",  "discovery_url":"https://accounts.google.com/.well-known/openid-configuration", "client_id":"...", "client_secret":"..."}
]
```

## Common pitfalls

- **AADSTS50011: redirect URI does not match.** The URI registered with Entra must match the request's host + scheme + path *exactly*. If your deployment is behind an ALB that terminates HTTPS, register `https://...` even though the FastAPI process internally sees HTTP. Confirm the ALB sets `X-Forwarded-Proto: https` and that Starlette is reading it (otherwise our `_redirect_uri_for` builds the wrong URL).
- **Login succeeds but `email` and `name` are blank.** Step 3 (optional ID-token claims) was skipped. Without those Entra omits the claims from the ID token, and our `Identity` falls back to the `sub` GUID. Add the claims and re-test.
- **`docker-compose .env` mangles the JSON.** The `$` characters inside an argon2-style hash also bite OIDC: if any of your secret values happen to contain `$`, double them to `$$` in the compose `.env` (compose collapses `$$` back to `$` inside the container). Most OIDC `client_secret` values don't include `$`, but it's worth knowing if yours does.
- **`SameSite=Strict` would silently break login.** Don't change `auth_cookie_secure` or `_samesite` defaults without reading the [authentication design](../explanation/authentication-design.md) — `Lax` is load-bearing for the redirect-back flow.
- **JWKS rotation.** IdPs rotate signing keys (Entra ≈ daily). Our provider caches JWKS for an hour and force-refreshes once on a signature failure. If you see sustained `id_token signature invalid` errors after a rotation, restart the container to clear the cache; otherwise wait an hour for the natural refresh.
- **Open-redirect attempts.** A malicious link `/login?next=https://evil.example/steal` is sanitised — only paths starting with a single `/` and lacking `://` survive. Anything else falls back to `AUTH_POST_LOGIN_REDIRECT` (default `/`).

## Just-in-time provisioning (no signup, no user table)

Worth restating because operators often expect a "users" admin page:

> The first time a user successfully completes the OIDC callback, a session is created from their ID-token claims. We do not maintain a user database. We do not have an admin UI to add or remove users. Access control happens **at the IdP**: anyone the IdP authenticates and authorises for our app is in.

If you need to revoke access, do it in the IdP — disable the user in Entra, remove them from the app's group, or revoke their session. Existing in-flight session cookies remain valid until expiry (default 8 hours); rotate `AUTH_SECRET_KEY` to kill all sessions immediately.

## What's next (PR3)

- Multi-provider chooser UI on the login page when `len(providers) > 1`.
- IdP-side logout: when the discovery doc advertises `end_session_endpoint`, clicking "sign out" navigates the user there after clearing local cookies.
- Optional Redis-backed session store (`AUTH_SESSION_STORE=redis`) for per-user revocation, queryable session lists, and admin tooling.

## Testing

Local mock IdP for end-to-end testing without round-tripping the real Entra:

- `tests/integration/auth/test_oidc_roundtrip.py` covers config endpoint, kickoff redirect with PKCE, callback success path, callback with IdP error, missing tx cookie, tampered state, and open-redirect rejection.
- `tests/unit/auth/test_oidc_provider.py` covers state mismatch, provider-id binding, nonce/aud/iss mismatch, expired ID token, JWKS rotation, and token-endpoint error surfacing.

Both run under `make test-integration` / `make test-fast` against a `respx`-mocked IdP — no live calls.
