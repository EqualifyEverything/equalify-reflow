# Enable basic auth on the viewer

This how-to switches the viewer from "open to anyone who can reach the URL" (the OSS default) to "username + password required." There is intentionally no signup endpoint: users are operator-provisioned via env, exactly like API keys are today. Adding or removing a user means editing env and restarting.

For SSO via Microsoft Entra (or other OIDC providers), see [configure SSO](configure-sso.md) instead — that lands in PR2.

## What changes when you turn this on

- The SPA loads `/login` first and won't show the pipeline UI until the user has a valid session cookie.
- API calls under `/api/*` require either the session cookie (browser flow) or `X-API-Key` (programmatic clients). API keys keep working untouched, so existing curl/CI consumers don't break.
- Every request log line gains `user_sub`, `user_email`, `user_provider` fields once the session cookie is present.

## Steps

### 1. Generate a signing key

```bash
openssl rand -hex 32
```

This 64-character string is your `AUTH_SECRET_KEY`. Treat it like an API key — leak it, sessions are forgeable. **Rotating it invalidates every active session** (every user has to log back in), so do it on a maintenance window.

### 2. Hash a password for the first user

```bash
make auth-hash-password
```

You'll be prompted twice (no echo). Copy the output line — it starts with `$argon2id$…`.

### 3. Set the environment

In your `.env` (dev) or your deployment's secret store (prod):

```env
AUTH_MODE=basic
AUTH_SECRET_KEY=<the openssl output from step 1>
AUTH_BASIC_USERS=alice:<the argon2 hash from step 2>
```

Multiple users are separated by `;` (not `,` — argon2 hashes contain commas in their parameter block, so a comma-separated list would corrupt every entry):

```env
AUTH_BASIC_USERS=alice:$argon2id$…;bob:$argon2id$…
```

For a deployment that terminates HTTPS at a load balancer (the production layout), leave `AUTH_COOKIE_SECURE=true` (default). For local HTTP-only dev, set `AUTH_COOKIE_SECURE=false` so your browser will accept the cookie.

### 4. Restart and verify

```bash
make down && make dev
```

Open `http://localhost:8080/`. You should land on `/login`. Sign in with the username and the plaintext password you hashed in step 2. After login, the pipeline runs as it did before.

To confirm the API-key path still works in parallel:

```bash
curl -H "X-API-Key: $YOUR_KEY" http://localhost:8080/api/v1/feedback/config
```

Should return `200`.

## Adding more users

Append a `;`-separated entry to `AUTH_BASIC_USERS`:

```env
AUTH_BASIC_USERS=alice:$argon2id$…;bob:$argon2id$…
```

Restart. There is no live-reload — `APIKeyAuthMiddleware` and the basic-mode user table are loaded at startup.

## Removing a user

Delete their entry from `AUTH_BASIC_USERS` and restart. Their existing sessions remain valid until expiry (default 8h). To kill them sooner, rotate `AUTH_SECRET_KEY` — that invalidates all sessions, not just one user's.

## Common pitfalls

- **`AUTH_SECRET_KEY must be at least 32 characters`** at startup — your key is too short. `openssl rand -hex 32` produces 64 chars; anything from any other source must meet 32+.
- **Cookie not set in browser DevTools** — `AUTH_COOKIE_SECURE=true` blocks the cookie on plain HTTP. Either run dev with `AUTH_COOKIE_SECURE=false` or test against an HTTPS deployment.
- **`AUTH_BASIC_USERS must contain at least one 'username:$argon2…' entry`** — the `Settings` validator rejected your CSV. The hash must start with `$argon2`. Use `make auth-hash-password`; don't bcrypt it by hand.
- **Argon2 hash arrives mangled inside the Docker container.** When the value is in a `.env` file consumed by docker-compose, the compose runtime treats `$argon2id`, `$v`, `$m`, etc. as variable references and substitutes them with empty strings — turning `alice:$argon2id$v=19$…` into `alice:=19=…`. **Escape every `$` as `$$` in the compose `.env`** (compose collapses `$$` back to a single literal `$` when loading the value into the container). The hash you paste therefore looks like `alice:$$argon2id$$v=19$$m=65536,t=3,p=4$$<salt>$$<hash>`. This is purely a docker-compose `.env` quirk; native shells, Kubernetes secrets, and AWS Secrets Manager pass the value through verbatim.
- **Login works locally but fails behind the ALB** — check that the ALB sets `X-Forwarded-Proto: https` and that Starlette is reading it (otherwise the `Secure` cookie may be silently dropped). See [authentication design](../explanation/authentication-design.md).

## Audit trail

Once auth is on, every log line in CloudWatch under `/ecs/equalify-pdf` carries `user_sub` after the session is established. To get a clean login/logout timeline, query Logs Insights for `category="auth_event"`:

```
fields @timestamp, auth_event, sub, reason, client_ip
| filter category = "auth_event"
| sort @timestamp desc
```
