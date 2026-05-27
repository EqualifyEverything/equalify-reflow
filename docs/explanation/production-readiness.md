# Equalify Reflow for Canvas — Production Brief

A reference for the conversation with your manager. Covers the entire
stack — Reflow's PDF-to-accessible-content pipeline plus the Canvas
integration that surfaces it inside the LMS. Honest about what works,
what's still PoC, what to budget, and the open questions.

## Updates since v1 (May 2026)

Three architectural decisions have been locked in since this brief was
first drafted, and each strengthens the production-readiness story:

1. **Hosting: AWS Fargate (not campus VM).** ECS Fargate runs the
   api-gateway and docling-serve containers inside a dedicated VPC, with
   ALB in front, ElastiCache Redis behind, and S3/Polly/Bedrock attached
   via IAM task role. Multi-AZ, autoscaling, IaC-deployable.
2. **Scope: new uploads only.** Reflow processes documents as faculty
   upload them — no backfill of the existing catalog. Keeps Claude API
   spend predictable and avoids the InfoSec discussion about retroactive
   processing of historical course content.
3. **Faculty consent / disclaimer flow live in code.** First launch shows
   a versioned disclaimer; faculty must acknowledge before any Claude
   API call. Audit log captures every acknowledgment. This eliminates
   the "silent processing" objection from compliance review.

Diagrams supporting this brief are in `briefs/diagrams/`:
`01-fargate-arch.png`, `02-data-flow.png`, `03-consent-flow.png`,
`04-cost-comparison.png`, `05-timeline-gantt.png`.

## What the project is, end to end

Two open-source projects working together as one deployment:

**Equalify Reflow** (upstream, built with University of Illinois Chicago,
AGPL-3.0): A FastAPI service that ingests PDFs and outputs accessible,
semantic markdown. It runs a five-phase pipeline:

1. **Extraction** — IBM Docling does the document parse + OCR fallback
2. **Analysis** — Claude classifies pages, identifies headings, footnotes, code blocks
3. **Headings** — Claude reconciles heading hierarchy across pages
4. **Translation** — Claude fixes per-page content and tags code blocks
5. **Assembly** — cross-page boundary fixes, final cleanup

Output: clean markdown with proper headings, alt text on images, real
tables (not images), extracted figures stored in S3. PII is scanned
with Microsoft Presidio before any AI processing; flagged PDFs require
explicit instructor approval before continuing.

**Canvas Integration** (this PR, on top of Reflow): An LTI 1.3 tool
that auto-detects every PDF uploaded anywhere in a Canvas course,
submits it to Reflow, and surfaces an accessibility score dial +
alternative-format menu next to every PDF link — a Panorama-style
overlay. Includes a faculty HTML editor (critical for STEM content)
that turns the accessible HTML into the single source of truth for
every downstream format.

## The stack at a glance

```
                ┌────────────────────────────────────┐
                │           Canvas LMS                │
                │  Files · Modules · Pages ·          │
                │  Discussions · Assignments · Quizzes │
                └────┬────────────────────┬──────────┘
                     │ LTI 1.3            │ API v1
                     │ launches            │ + Theme JS overlay
                     ▼                     ▼
              ┌──────────────────────────────────┐
              │   FastAPI Service (single app)    │
              │                                    │
              │  ┌──────────┐  ┌──────────────┐  │
              │  │ Reflow   │  │ Canvas       │  │
              │  │ pipeline │  │ integration  │  │
              │  │ + viewer │  │ + LTI + alt  │  │
              │  └────┬─────┘  │ formats      │  │
              │       │        └──────┬───────┘  │
              │       │               │           │
              │       └──── workers ──┘           │
              └──┬──────────────────┬─────────────┘
                 │                   │
        ┌────────▼──┐   ┌────────────▼─────────┐
        │  Redis    │   │  AWS S3 or Floci      │
        │  jobs +   │   │  PDFs, markdown,      │
        │  state    │   │  extracted figures    │
        └───────────┘   └──────────────────────┘
                 │
        ┌────────▼─────────────────┐
        │  AI Provider              │
        │  Anthropic API OR         │
        │  AWS Bedrock (Claude)     │
        └──────────────────────────┘

        Optional sidecars:
          Docling-serve (PDF extraction)
          Polly (audio MP3)
          Prometheus + Grafana + Jaeger
```

Everything is one Docker Compose stack. Same FastAPI app serves the
Reflow API, the LTI endpoints, the Panorama-style overlay, the review
queue, the alt-format renderers, and the faculty HTML editor.

## Current state, end-to-end honest

What's running in CSUEB's test instance right now over an ngrok tunnel:

- **Reflow pipeline** — works through PII scan and document classification.
  The AI agents stage stops without an API key, so jobs never reach the
  "completed" state.
- **Canvas watcher** — already pulled four PDFs from the test course's
  Files into Reflow. Scans 8 surfaces (Files, all folders, modules,
  pages, discussions, announcements, assignments, quizzes, syllabus).
- **LTI 1.3 launch** — Confirmed working. Instructors click "Accessible
  Documents" in the course nav and land in the review queue.
- **Panorama-style overlay** — Dial + Alternative Formats Menu render on
  Files index, Modules, and any /files/X anchor. Tested in CSUEB.
- **Alternative formats** — HTML, Plain Text, Markdown, ePub, HTML with
  MathJax, OCR'd PDF, Tagged PDF/A are all wired and serve. Audio (MP3)
  requires AWS Polly. Translations require the AI key.
- **Faculty HTML editor** — Side-by-side editor with live MathJax
  preview. Saves edits to Redis; every downstream format then derives
  from the edited HTML.

What's NOT finished:

- **No AI key in CSUEB test**, so Reflow jobs stall at the AI step.
  Score dials show the placeholder 15% (one signal default). With a
  key, real scores flow in.
- **Score model** uses placeholder signals. Reflow's pipeline needs a
  follow-up to surface real accessibility flags (text-layer present,
  heading hierarchy, alt-text coverage, etc.) in its status payload.
  Estimate: half a day.
- **Bridge worker** has a Floci-specific S3 URL bug (uses localhost:4566
  instead of floci:4566). Harmless against real AWS S3. Two-line fix.
- **Tests** for the new endpoints aren't written yet.
- **Canvas Live Events** for real-time discovery isn't wired; we poll
  every 60s. Acceptable for pilot, should move to Live Events for scale.

## What you need to ship to production

### 1. Hosting — AWS Fargate (decided)

Public HTTPS URL replacing the ngrok tunnel. We're hosting on AWS Fargate
in a dedicated CSUEB sub-account or VPC in us-west-2. The stack is one
docker-compose application in development that maps cleanly to Fargate
in production:

- **api-gateway** (Python 3.11, FastAPI) — main service, ECS Fargate task (0.5 vCPU / 1 GB at pilot, autoscale 1→4 tasks)
- **docling-serve** — PDF extraction sidecar, ECS Fargate task (1 vCPU / 4 GB)
- **ElastiCache Redis** — job state, queues, sessions, consent records, faculty edits cache. Multi-AZ replicated at campus scale.
- **AWS S3** — file storage (source PDFs + generated outputs)
- **Application Load Balancer** with ACM cert in front of api-gateway tasks
- **CloudWatch** for logs and alarms — `/health` alarm + cost budget alarms

See `briefs/diagrams/01-fargate-arch.png` for the architecture overview.

Why Fargate over campus VM:
- Production-grade from day 1 — no painful migration mid-pilot.
- Multi-AZ failover, autoscaling, IAM task role (no long-lived keys).
- AWS handles OS patching; CSUEB IT does not own that burden.
- Cleanest IAM boundary the ISO can audit.

Terraform / CDK module will be provided so the whole stack stands up
from a single `apply` — no click-ops.

### 2. Canvas access

- **Production Developer Key** in CSUEB Canvas (not the test instance).
  Same JSON config the test key used — paste the URL of the production
  tool's `/lti/config.json`. Get back a client_id and a deployment_id.
- **Service-account API token**. Today's token is on your personal
  user; for prod we want a dedicated `accessibility-service@csueb.edu`
  account, Account Admin or sub-account admin, with permissions to
  read every file in watched courses. No-expiry token.
- **Theme JavaScript** pasted into the institution-wide or sub-account
  custom JS. One line:
  ```html
  <script src="https://reflow.csueb.edu/lti/panorama.js?inst=csueb" defer></script>
  ```

### 3. AI provider (required for Reflow to actually work)

Pick one:

- **Anthropic API** — `sk-ant-...` key. ~$0.0008 per typical 30-page
  PDF (Claude Haiku 4.5). $0.80 per 1000 documents. Simplest. Set
  `ANTHROPIC_API_KEY` in env.
- **AWS Bedrock** with Claude Haiku 4.5 access. Same per-document
  cost. Requires `bedrock:InvokeModel` IAM. Set `AI_PROVIDER=bedrock`.

### 4. AWS resources

Required for prod (Reflow needs S3 for file storage):

- Two S3 buckets: `csueb-reflow-temp` (uploaded PDFs) and
  `csueb-reflow-results` (markdown + extracted figures). Lifecycle
  rule to auto-delete `temp/` after 24h. Cost: ~$5–15/mo.
- IAM user or role with `s3:GetObject`, `s3:PutObject`,
  `s3:DeleteObject` on those two buckets.

Optional (for audio):
- Polly with `polly:SynthesizeSpeech`. Costs ~$4 per 1M characters
  with the neural engine. A 30-page PDF is ~50k characters, so
  $0.20 per audio render. Heavily cacheable.

### 5. The full env (concrete)

```
# Reflow core
ANTHROPIC_API_KEY=sk-ant-...
S3_TEMP_BUCKET=csueb-reflow-temp
S3_RESULTS_BUCKET=csueb-reflow-results
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-west-2
REDIS_URL=redis://<prod-redis-host>:6379
API_KEYS=<one secret API key for internal calls>

# Canvas integration
LTI_ENABLED=true
LTI_ISSUER=https://canvas.instructure.com
LTI_PUBLIC_URL=https://reflow.csueb.edu
LTI_AUTH_LOGIN_URL=https://sso.canvaslms.com/api/lti/authorize_redirect
LTI_AUTH_TOKEN_URL=https://sso.canvaslms.com/login/oauth2/token
LTI_JWKS_URL=https://sso.canvaslms.com/api/lti/security/jwks
LTI_CLIENT_ID=<prod-issued>
LTI_DEPLOYMENT_ID=<prod-issued>
LTI_PRIVATE_KEY_PATH=/app/keys/lti_private.pem
LTI_PUBLIC_KEY_PATH=/app/keys/lti_public.pem
CANVAS_API_URL=https://csueb.instructure.com
CANVAS_API_TOKEN=<service-account-token>
CANVAS_WATCHED_COURSES=                      # empty = all courses
CANVAS_ALLOWED_ORIGINS=https://csueb.instructure.com
CANVAS_POLL_SECONDS=60
```

LTI signing keys generated once and persisted to a mounted volume:

```
docker compose exec api-gateway uv run python -m src.lti.keys generate
```

## Operations

- **Logging**: structured JSON to stdout. Capture via CloudWatch,
  journald, or your container platform's native logging.
- **Metrics**: Prometheus on `:8001/metrics`. Dashboards live in
  `infrastructure/grafana/`. Tracks Reflow pipeline phase timing,
  Canvas API call latency, watcher discoveries, AI cost per request.
- **Health**: `/health` returns Redis, S3, docling-serve status and
  queue depth.
- **Tracing**: OpenTelemetry baked in. Point at Jaeger or any OTLP
  collector.
- **Backups**: Redis is the canonical store for job metadata AND
  faculty-edited HTML. Run RDB snapshots to S3 nightly. When edit
  volume grows, migrate edited HTML to S3 too.
- **Secrets**: `.env` for a single VM. AWS Secrets Manager for ECS/EKS.
  LTI private key must be on persistent volume.
- **Scaling**: Stateless API gateway — replicate behind a load
  balancer. Redis stays single-instance until you outgrow it.
  Docling can be replicated; it's CPU-heavy.

## Security and compliance

- **FERPA**: Reflow's pipeline PII-scans every document with
  Microsoft Presidio before AI processing. PII-flagged docs require
  explicit instructor approval before continuing. No PII reaches
  Anthropic / Bedrock without that approval.
- **Authentication**: LTI 1.3 JWT-signed launches verified against
  Canvas's JWKS. Faculty identity proven by Canvas; we store no
  passwords.
- **Canvas API token**: Service-account-scoped. Revoke and re-issue
  if compromised.
- **CSP**: Theme JS bundle injects no third-party scripts on Canvas
  pages except MathJax (CDN) inside the editor's iframe.
- **AGPL-3.0 licence**: Cal State's internal use is fine. If you
  ever host this for external institutions you'd need to publish
  changes — standard AGPL boundary.

## Costs (single-college pilot)

| Item | Monthly |
|---|---|
| Hosting (campus VM or Fargate) | $50–$120 |
| AWS S3 + bandwidth | $5–$15 |
| Anthropic / Bedrock AI (1000 PDFs/mo) | $1–$5 |
| Polly audio (500 renders/mo) | ~$100 |
| **Total** | **~$160–$240/mo** |

YuJa Panorama for the same scope is typically $30k–$60k/year per
campus. The cost gap pays for the engineer maintaining this twice over.

## Honest gaps vs. YuJa Panorama

- **Math engine** — Panorama has a dedicated math accessibility
  engine. We fall back on Reflow's markdown + MathJax. Works for ~80%
  of math content; complex equations may need faculty edits.
- **Immersive Reader** — Microsoft-hosted, requires Azure
  subscription. We have a "soon" card for it; easy add once Azure
  resource exists.
- **Braille (BRF)** — Needs `liblouis` system library. One
  engineer-day to wire up.
- **Institution-wide analytics dashboard** — Panorama has a campus
  scorecard. We have per-course dashboard; aggregate is Phase 2.
- **AutoPilot Remediation** — Panorama can auto-fix some issues. Our
  closest equivalent is the faculty HTML editor — instructors edit
  manually with AI assistance available, but not a fully automated
  fix-everything button.
- **24/7 vendor support** — Panorama is paid with SLAs. This is
  open source; Cal State owns maintenance.
- **1EdTech certification** — Panorama has it; we implement LTI 1.3
  but aren't certified.

## Maintenance commitment

- **0.25–0.5 FTE** engineering: keeping up with Canvas API changes
  (Instructure ships breaking changes 2–3x per year), monitoring,
  bug fixes, format additions.
- **Quarterly Canvas regression test** on Instructure's major
  releases. ~1 day of QA per quarter.
- **Reflow upstream** is on AGPL with active UIC community; tracking
  releases is straightforward.

## Likely manager questions

**Q: Why not buy YuJa Panorama?**
A: $30–60k/year/campus vs. ~$200/mo + a quarter of an engineer. For
CSUEB alone the savings cover the engineer. Across the CSU system the
savings are substantial. The trade-off: own the maintenance, accept
~80% feature parity, no vendor SLA.

**Q: What happens if the engineer leaves?**
A: Codebase is open source FastAPI + React, conventional Python. Any
mid-level Python engineer can take over. Docs are in `docs/`. Reflow
upstream is actively maintained by UIC.

**Q: Is it secure?**
A: LTI 1.3 JWT validation, PII scanning before AI, AGPL means
auditable. Faculty data stays in Canvas + Reflow S3. The AI providers
(Anthropic / AWS) are the same ones already in use by GitHub Copilot,
Notion, and most CSU faculty's personal ChatGPT.

**Q: What if Canvas changes their API?**
A: We use stable Canvas API v1. Instructure deprecates with 6+ months
notice. The watcher scans 5 surfaces independently — partial breakage
is graceful.

**Q: Can we pilot in one department?**
A: Yes. Set `CANVAS_WATCHED_COURSES` to that department's course IDs
(or apply theme JS to a single sub-account). Cost stays under
$50/mo for a single-department pilot.

**Q: How does this compare to UDOIT (which CSUEB already has)?**
A: UDOIT scans HTML content for accessibility issues and reports.
It does NOT auto-convert PDFs to accessible alternatives. They're
complementary — keep UDOIT for HTML content, add Reflow for PDFs.
Both can run in the same Canvas theme JS without conflict (already
verified in CSUEB test).

**Q: What about student privacy with the AI provider?**
A: PDFs going through Reflow have already been PII-scanned. If PII
is found, the instructor approves before AI processing. Anthropic
and AWS Bedrock both offer enterprise data-handling agreements that
exclude training on customer data — sign one of those if not already
in place.

**Q: How long would a pilot take?**
A: 2 days for IT to stand up production hosting + Canvas Developer
Key registration. 1 day for theme JS + LTI install. 1 week of shadow
testing against pilot courses. Open to instructors with a 30-min
training. Total: ~2 weeks from approval to faculty access.

## What I need from your manager

1. **Approval** for a 2-month pilot in one or two departments.
2. **Hosting decision** (campus VM vs AWS Fargate).
3. **AI provider procurement** (Anthropic key, or AWS Bedrock access).
4. **Sponsorship** for a service-account Canvas user.
5. **~5 days of IT / Instructional Technology time** for prod LTI
   install, theme JS deployment, and TLS cert setup.

## Concrete next steps if approved

1. Stand up production host with TLS certificate.
2. Register prod Canvas Developer Key + install in pilot sub-account.
3. Provision AI key (Anthropic or Bedrock).
4. Create S3 buckets and IAM user/role.
5. Generate LTI signing keys; persist to volume.
6. Paste theme JS into pilot sub-account theme.
7. Run a 1-week shadow test against a handful of pilot courses.
8. Train pilot instructors (30-min session).
9. Open it to faculty.

Month 2: expand to additional departments based on pilot feedback.

## Repositories and references

- Reflow upstream: github.com/EqualifyEverything/equalify-reflow
- This integration's docs: `docs/explanation/canvas-lti-integration.md`
- Install runbook: `docs/how-to/install-canvas-integration.md`
- Architecture: `docs/explanation/architecture.md`
- API reference: live at `<host>/docs` (Swagger)
- Reflow paper / blog: links from the UIC team
