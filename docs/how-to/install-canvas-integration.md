# How to install the Canvas integration

This recipe walks a Canvas admin through enabling the Equalify Reflow integration on a Canvas instance. End state: PDFs uploaded into watched courses are automatically converted into accessible Canvas Pages awaiting instructor review, and Canvas pages decorate file links with accessibility gauges and an alternative-format picker.

You will need: admin access to the Canvas instance, an Equalify Reflow API key, and the ability to set environment variables on the Reflow deployment.

## 1. Generate the LTI signing keys

Inside a running api-gateway container:

```bash
docker compose exec api-gateway uv run python -m src.lti.keys generate
```

This writes `lti_private.pem` and `lti_public.pem` under the path set by `LTI_PRIVATE_KEY_PATH` / `LTI_PUBLIC_KEY_PATH` (defaults: `/app/keys/`). Mount that directory as a persistent volume in production — losing the private key forces a re-registration.

## 2. Configure environment

Edit your `.env` (or environment secrets store) with at minimum:

```
LTI_ENABLED=true
LTI_ISSUER=https://<your-canvas-host>
LTI_CLIENT_ID=<set after step 3>
LTI_DEPLOYMENT_ID=<set after step 3>
LTI_AUTH_LOGIN_URL=https://<your-canvas-host>/api/lti/authorize_redirect
LTI_AUTH_TOKEN_URL=https://<your-canvas-host>/login/oauth2/token
LTI_JWKS_URL=https://<your-canvas-host>/api/lti/security/jwks
LTI_PUBLIC_URL=https://reflow.<your-domain>

CANVAS_API_URL=https://<your-canvas-host>
CANVAS_API_TOKEN=<long-lived token from step 4>
CANVAS_WATCHED_COURSES=<csv of course ids — leave empty until you have a pilot course>
CANVAS_ALLOWED_ORIGINS=https://<your-canvas-host>
```

Restart the api-gateway container so the new settings load.

## 3. Register the LTI 1.3 Developer Key in Canvas

In Canvas, go to **Admin → Developer Keys → + Developer Key → + LTI Key**.

Choose **Method = Paste JSON** and paste the body of:

```
https://reflow.<your-domain>/lti/config.json
```

Save. Copy the resulting **Client ID** into `LTI_CLIENT_ID`. Restart the api-gateway. Then, still under Developer Keys, set the **State** of the new key to **On**.

In **Admin → Settings → Apps → +App**, install the tool using the Client ID just created. The deployment ID Canvas shows on the install page goes into `LTI_DEPLOYMENT_ID`. Restart again.

## 4. Create a service Access Token

A Canvas user (best practice: a dedicated `accessibility-service@yourschool.edu` account, with the Account Admin role) generates a long-lived token via **Account → Settings → New Access Token**. Paste it into `CANVAS_API_TOKEN`.

## 5. Enable the Panorama-style overlay (optional but recommended)

In Canvas, go to **Admin → Themes → Edit current theme → JavaScript file** and paste:

```html
<script src="https://reflow.<your-domain>/lti/panorama.js?inst=<your-inst-slug>" defer></script>
```

Save and apply to a sub-account or the whole institution. Every page load now decorates PDF links with accessibility gauges (for instructors) and an alternative-format picker (for everyone).

## 6. Pilot on a single course

Add a Canvas course id to `CANVAS_WATCHED_COURSES`. Within `CANVAS_POLL_SECONDS` (default 60), uploaded PDFs in that course start moving through Reflow. When a job completes, an unpublished Page is created and the uploader gets a Canvas Inbox message linking back to the LTI review UI.

## 7. Validate end to end

1. Upload a sample PDF to the pilot course.
2. Within ~60 seconds, confirm `GET /canvas/review/api/pending?course_id=<id>` (called from the LTI launch) lists the file.
3. Wait for Reflow to finish (~5 minutes).
4. Confirm an unpublished Canvas Page appears and the instructor receives an Inbox message.
5. Launch the LTI tool from Canvas Navigation, review, approve, and confirm the Page is published.
6. Reload any Files page in the pilot course and confirm the gauge appears next to the PDF.

## Phase 5 (later): switch from polling to Canvas Live Events

Canvas's Live Events ship file-upload events to an SQS queue. Once your Canvas Data Services account is provisioned, set `CANVAS_LIVE_EVENTS_QUEUE_URL` and disable the poller by setting `CANVAS_WATCHED_COURSES=` (empty). The bridge worker continues to drive Reflow jobs to completion regardless of which source delivered them.
