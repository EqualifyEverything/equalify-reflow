# Canvas LTI 1.3 Local Development Setup

Guide for connecting the PDF Converter to a local Canvas LMS instance via LTI 1.3.

## Architecture Overview

```
Canvas LMS (Docker)          ngrok                  PDF Converter (Docker)
http://localhost:3000   -->  HTTPS tunnel  -->      http://localhost:8080
                             (public URL)

1. User clicks "Open in Equalify Reflow" on a PDF in Canvas
2. Canvas POSTs to ngrok URL /lti/login (OIDC initiation)
3. PDF Converter redirects browser to Canvas auth endpoint
4. Canvas authenticates user and POSTs JWT to ngrok URL /lti/launch
5. PDF Converter validates JWT, downloads PDF via Canvas API, creates job
6. User is redirected to the pipeline viewer
```

**Why ngrok?** Canvas runs in Docker and needs to reach the PDF Converter's LTI endpoints over HTTPS (LTI 1.3 requirement). Since both services run locally, ngrok provides the HTTPS tunnel that Canvas can reach.

## Prerequisites

- Local Canvas LMS running at `http://localhost:3000` (see [Canvas Setup](../../../equalify-reflow-canvas/CANVAS-SETUP.md))
- ngrok installed and authenticated (`brew install ngrok`, then `ngrok config add-authtoken <token>`)
- PDF Converter `.env` configured with LTI settings (see below)

## Startup Sequence

### Step 1: Start Canvas

```bash
cd ~/Projects/equalify-reflow-canvas/canvas-lms
docker compose up -d
```

Wait for Canvas to be accessible at `http://localhost:3000`.

### Step 2: Start PDF Converter

```bash
cd ~/Projects/equalify-pdf-converter
make dev
```

**Note:** Grafana uses port 3001 to avoid conflict with Canvas on port 3000. If Grafana fails to start, that's fine -- it's not needed for LTI.

### Step 3: Start ngrok

```bash
ngrok http 8080
```

Copy the HTTPS URL from the output (e.g., `https://abc-xyz.ngrok-free.dev`).

You can also get it programmatically:
```bash
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])"
```

### Step 4: Update Canvas Developer Key

Each time ngrok restarts (free tier), you get a new URL. Update the Developer Key in Canvas:

1. Open `http://localhost:3000/accounts/1/developer_keys`
2. Click the wrench icon on the **Equalify Reflow** key (Client ID: `10000000000001`)
3. Switch to **Paste JSON** method
4. Fetch the auto-generated config from your ngrok URL:
   ```bash
   curl -s https://YOUR-NGROK-URL/lti/config -H "ngrok-skip-browser-warning: true" | python3 -m json.tool
   ```
5. Paste that JSON into the Developer Key configuration
6. Click **Save**
7. Make sure the key is **ON** (enabled)

Alternatively, use **Enter URL** method and paste:
```
https://YOUR-NGROK-URL/lti/config
```

### Step 5: Verify

Test the endpoints are reachable through ngrok:
```bash
# JWKS endpoint (Canvas fetches this to verify JWTs)
curl -s https://YOUR-NGROK-URL/lti/jwks -H "ngrok-skip-browser-warning: true"

# Config endpoint (auto-generated Developer Key JSON)
curl -s https://YOUR-NGROK-URL/lti/config -H "ngrok-skip-browser-warning: true"

# Canvas API connectivity
curl -s http://localhost:3000/api/v1/users/self \
  -H "Authorization: Bearer YOUR_CANVAS_API_TOKEN"
```

### Step 6: Test the LTI Launch

1. Log into Canvas at `http://localhost:3000`
2. Navigate to a course with the app installed
3. Go to **Files**
4. Upload a PDF (if none exist)
5. Click the **...** menu on the PDF
6. Click **Open in Equalify Reflow**
7. The LTI launch should redirect through the OIDC flow and open the viewer

## Environment Variables

These are already configured in `.env` for local Canvas:

```bash
# Canvas API (direct container networking, bypasses host port conflicts)
# Requires canvas-network in docker-compose.dev.yml
CANVAS_API_URL=http://canvas-lms-web-1/api/v1
CANVAS_API_TOKEN=<your-canvas-api-token>
CANVAS_HOST_HEADER=localhost:3000

# LTI 1.3 Core
LTI_ENABLED=true
LTI_ISSUER=https://canvas.instructure.com
LTI_CLIENT_ID=10000000000001
LTI_DEPLOYMENT_ID=2:4dde05e8ca1973bcca9bffc13e1548820eee93a3

# Canvas OIDC/OAuth Endpoints
# LTI_AUTH_LOGIN_URL uses localhost:3000 because the browser follows this redirect
# LTI_AUTH_TOKEN_URL and LTI_JWKS_URL use canvas-lms-web-1 (container-direct networking)
LTI_AUTH_LOGIN_URL=http://localhost:3000/api/lti/authorize_redirect
LTI_AUTH_TOKEN_URL=http://canvas-lms-web-1/login/oauth2/token
LTI_JWKS_URL=http://canvas-lms-web-1/api/lti/security/jwks

# RSA Keys (mounted into container via docker-compose.dev.yml)
LTI_PRIVATE_KEY_PATH=keys/lti_private.pem
LTI_PUBLIC_KEY_PATH=keys/lti_public.pem
```

**Important notes:**
- `LTI_ISSUER` is `https://canvas.instructure.com` even for local Canvas. This is the issuer value Canvas puts in its JWTs (configured in Canvas's `config/security.yml`).
- **Browser vs Container URLs:** `LTI_AUTH_LOGIN_URL` uses `localhost:3000` because the *browser* follows this redirect. Server-to-server URLs (`LTI_AUTH_TOKEN_URL`, `LTI_JWKS_URL`, `CANVAS_API_URL`) use the Canvas container hostname (`canvas-lms-web-1`) via Docker network. The `CANVAS_HOST_HEADER` setting ensures all server-to-server requests send the correct `Host: localhost:3000` header that Canvas requires for authentication.
- **Canvas API networking:** `CANVAS_API_URL` uses the Canvas container hostname (`canvas-lms-web-1`) directly via Docker network. This avoids port conflicts on the host (e.g., if another service uses port 3000). `CANVAS_HOST_HEADER` tells the client to send `Host: localhost:3000` on all requests, which Canvas requires for authentication. The client also rewrites Canvas-generated `localhost:3000` URLs (e.g., file download links) to the container hostname.
- The `docker-compose.dev.yml` connects the api-gateway to the `canvas-lms_default` network and uses `REDIS_URL=redis://equalify-reflow-redis:6379` to avoid DNS collision with Canvas's own Redis service on the shared network.

## LTI 1.3 Launch Flow (Detailed)

```
Browser                  Canvas (localhost:3000)         ngrok          PDF Converter (localhost:8080)
  |                            |                          |                       |
  |-- Click "Open in Reflow"-->|                          |                       |
  |                            |                          |                       |
  |                            |--POST /lti/login-------->|----POST /lti/login--->|
  |                            |  (iss, login_hint,       |                       |
  |                            |   target_link_uri,       |                       |
  |                            |   lti_message_hint)      |                       |
  |                            |                          |                       |
  |                            |                          |<--302 Redirect--------|
  |                            |                          |   to Canvas auth      |
  |<---302 Redirect to Canvas auth endpoint---------------|   with state/nonce    |
  |                            |                          |                       |
  |--GET /api/lti/authorize--->|                          |                       |
  |                            | (validates user session) |                       |
  |                            |                          |                       |
  |<--POST /lti/launch (form)--|------------------------->|---POST /lti/launch--->|
  |    (id_token JWT + state)  |                          |                       |
  |                            |                          |                       |
  |                            |                          |  1. Validate state    |
  |                            |                          |  2. Verify JWT sig    |
  |                            |                          |     (fetch Canvas JWKS)|
  |                            |                          |  3. Check nonce       |
  |                            |                          |  4. Extract file info |
  |                            |                          |  5. Download PDF      |
  |                            |                          |     (Canvas API)      |
  |                            |                          |  6. Upload to S3      |
  |                            |                          |  7. Create job        |
  |                            |                          |                       |
  |<--HTML redirect to viewer--|--------------------------|<---HTMLResponse-------|
  |                            |                          |                       |
```

## Key Files

| File | Purpose |
|------|---------|
| `src/lti/router.py` | LTI endpoints (/login, /launch, /jwks, /config) |
| `src/lti/service.py` | File download from Canvas, job creation |
| `src/lti/adapters.py` | FastAPI-to-pylti1p3 bridge (Redis state storage) |
| `src/lti/config.py` | Tool configuration for pylti1p3 |
| `src/lti/keys.py` | RSA key management and JWKS generation |
| `src/lti/models.py` | Pydantic models for LTI data |
| `keys/lti_private.pem` | Tool's RSA private key (signing) |
| `keys/lti_public.pem` | Tool's RSA public key (served via /lti/jwks) |

## Generating New RSA Keys

If keys are missing or need regeneration:

```bash
docker exec equalify-reflow-api-gateway uv run python -m src.lti.keys generate
```

Or from the host (keys are volume-mounted):
```bash
openssl genrsa -out keys/lti_private.pem 2048
openssl rsa -in keys/lti_private.pem -pubout -out keys/lti_public.pem
chmod 600 keys/lti_private.pem
```

## Canvas Developer Key Setup (First Time)

If you need to create the Developer Key from scratch:

1. Open `http://localhost:3000/accounts/1/developer_keys`
2. Click **+ Developer Key** > **+ LTI Key**
3. Choose **Enter URL** and paste: `https://YOUR-NGROK-URL/lti/config`
4. Click **Save**
5. Toggle the key to **ON**
6. Note the **Client ID** (e.g., `10000000000001`)
7. Install in a course:
   - Go to the course > **Settings** > **Apps**
   - Click **+ App** > **By Client ID**
   - Enter the Client ID
   - Click **Submit**
8. Note the **Deployment ID** from the installation
9. Update `.env` with `LTI_CLIENT_ID` and `LTI_DEPLOYMENT_ID`

## Troubleshooting

### ERR_NGROK_3200 (Endpoint offline)

ngrok tunnel is not running. Start it:
```bash
ngrok http 8080
```

Then update the Developer Key in Canvas with the new URL (Step 4 above).

### ngrok free tier URL changes

Every time ngrok restarts, you get a new random URL. You must update the Developer Key in Canvas each time. To avoid this, use a paid ngrok plan with a static domain, or use `ngrok http 8080 --domain=your-static-domain.ngrok-free.dev`.

### "Invalid or expired state" on launch

The OIDC state is stored in Redis with a TTL. If too much time passes between the login and launch steps, the state expires. Try launching again from Canvas.

### JWT validation fails

Check that `LTI_ISSUER` matches what Canvas puts in its JWTs. For local Canvas, this is usually `https://canvas.instructure.com` (set in Canvas's `config/security.yml`).

### File download fails after launch

Ensure `CANVAS_API_URL` and `CANVAS_API_TOKEN` are set in `.env`. The token needs permission to read files in the course. Generate one at `http://localhost:3000/profile/settings` > **+ New Access Token**.

### Canvas file download redirect loop (sf_verifier)

Canvas local Docker serves files through a redirect chain using `sf_verifier` JWT tokens. The PDF Converter handles this by:
1. Using `CanvasAPIClient.rewrite_canvas_url()` to rewrite `localhost:3000` URLs to the Canvas container hostname
2. Setting `Host: localhost:3000` header (via `CANVAS_HOST_HEADER`) so Canvas recognizes the request as local
3. Stripping the `Authorization` header on redirect hops (letting `sf_verifier` handle auth)

If you see infinite 302 redirects or `414 Request-URI Too Large` errors in the logs, check that `CANVAS_HOST_HEADER` is set correctly in `.env`.

### Canvas API returns 403 Forbidden

Canvas validates the `Host` header on API requests. Ensure `CANVAS_HOST_HEADER=localhost:3000` is set in `.env` so the client sends the correct Host header when connecting via container name.

### Redis DNS collision with Canvas

When the api-gateway is on both the equalify and Canvas Docker networks, the hostname `redis` becomes ambiguous (both projects have a Redis service). The `docker-compose.dev.yml` overrides `REDIS_URL` to use the explicit container name `equalify-reflow-redis` to avoid this. If you see unexpected empty data from Redis, verify the container is connecting to the correct Redis instance.

### LTI endpoints return 404

Check that `LTI_ENABLED=true` in `.env` and restart the api-gateway:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate api-gateway
```

### Config endpoint returns HTTP URLs instead of HTTPS

The uvicorn command in `docker-compose.dev.yml` includes `--proxy-headers` and `--forwarded-allow-ips *` so it trusts ngrok's `X-Forwarded-Proto` header. If URLs still show `http://`, restart the api-gateway.

### Canvas can't reach JWKS endpoint

Canvas needs to fetch `https://YOUR-NGROK-URL/lti/jwks` to verify JWT signatures. Make sure ngrok is running and the URL in the Developer Key matches the current tunnel.

## Quick Reference

```bash
# Start everything
cd ~/Projects/equalify-reflow-canvas/canvas-lms && docker compose up -d
cd ~/Projects/equalify-pdf-converter && make dev
ngrok http 8080

# Get ngrok URL
curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])"

# Verify LTI endpoints
curl -s https://YOUR-NGROK-URL/lti/jwks -H "ngrok-skip-browser-warning: true"
curl -s https://YOUR-NGROK-URL/lti/config -H "ngrok-skip-browser-warning: true" | python3 -m json.tool

# Update Developer Key: http://localhost:3000/accounts/1/developer_keys

# Check logs during launch
make logs-api

# ngrok request inspector
open http://127.0.0.1:4040
```
