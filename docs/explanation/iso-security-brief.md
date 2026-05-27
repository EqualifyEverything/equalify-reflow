# ISO Brief: Access Requests + Security Review

For the conversation with the Information Security Officer. Asks (AWS
resources, AI provider) plus answers to the questions an ISO will care about.

## Updates since v1 (May 2026)

This brief was updated to reflect three architectural decisions that
strengthen the security/compliance posture:

1. **Hosting target: AWS Fargate.** Reflow runs on ECS Fargate with an
   IAM task role inside a dedicated VPC — no long-lived credentials on a
   VM, no OS-patching burden for CSUEB IT, multi-AZ failover. (Previously
   considered: campus VM. That option is now fallback only.)
2. **Faculty consent flow (built and wired).** Before processing any
   document, faculty acknowledge an in-app disclaimer that explains PII
   handling, Claude API usage, and their responsibility for content.
   The consent record (user, timestamp, IP, disclaimer version) is
   audit-logged in Redis. Subsequent launches skip the prompt until the
   disclaimer version is bumped. This is the strongest possible
   data-handling story: explicit, documented user authorization rather
   than silent automatic processing.
3. **Scope: new uploads only, faculty-uploaded materials only.** Reflow
   processes documents as faculty add them to Canvas — no backfill of
   the existing course catalog, no student submissions. The trust
   boundary is the instructor.

See architecture diagrams in `briefs/diagrams/`:

- `01-fargate-arch.png` — Production architecture on AWS Fargate.
- `02-data-flow.png` — End-to-end data flow with PII redaction trust boundary.
- `03-consent-flow.png` — Faculty consent flow (first launch + subsequent launches).

## TL;DR

I need provisioning for these things:

1. **AWS account** for hosting on Fargate plus storing PDFs in S3 and
   Polly for audio generation. Specific services: ECS Fargate, ALB,
   ElastiCache Redis, S3, ECR, CloudWatch, ACM.
2. **One AI provider**: either an Anthropic API key OR AWS Bedrock model
   access for Claude Sonnet 4.6 (with Haiku 4.5 and Opus 4.7 available
   via smart routing).
3. **ISO sign-off** that the consent flow + PII redaction + IAM-scoped
   task role design meets CSUEB's AI / data governance posture.

All three vendors (AWS, Anthropic) are widely used in higher-ed and
have signed Cal-State-friendly data agreements available. The data
flows are described below.

## 1. AWS access request

### Resources I need created (Fargate-based)

| Resource | Purpose | Cost estimate |
|---|---|---|
| Dedicated AWS sub-account or VPC (`us-west-2`) | Reflow runs in its own AWS landing zone for clean IAM/billing boundary | Free |
| ECS Fargate service (api-gateway) | Runs the main Reflow API container | Pilot ~$30/mo, Campus ~$400/mo |
| ECS Fargate service (docling-serve) | OCR/document extraction sidecar | Included in Fargate baseline |
| Application Load Balancer | HTTPS termination, multi-AZ routing | $22/mo |
| ElastiCache Redis (cache.t4g.micro single → multi-AZ) | Job state, consent records, faculty edits cache | Pilot ~$15/mo, Campus ~$80/mo |
| S3 bucket `csueb-reflow-temp` (in `us-west-2`) | Holds uploaded PDFs for the ~5 min of processing | Auto-deletes after 24h |
| S3 bucket `csueb-reflow-results` (in `us-west-2`) | Stores converted markdown + alt-format outputs | ~$5–50/mo |
| ECR repository | Container image registry for api-gateway + docling-serve | ~$1/mo |
| **IAM task role** (not IAM user) | Scoped credentials given to Fargate tasks at runtime — no long-lived access keys | Free |
| Polly access (audio MP3 feature) | Text-to-speech | $16 / 1M chars Neural |
| Bedrock access (optional alternative to Anthropic direct) | Claude Sonnet 4.6 / Haiku 4.5 / Opus 4.7 inference | Token-metered |
| ACM cert for `reflow.csueb.edu` | TLS termination at ALB | Free |
| CloudWatch log group + alarms | Logs, `/health` alarm, cost budget alarms | Pilot ~$10/mo |

See `briefs/diagrams/01-fargate-arch.png` for the architecture overview.

### Exact IAM policy (ECS task role)

The ECS Fargate task role needs the following — least-privilege, scoped to
Reflow resources. This is attached directly to the running container at
runtime; no long-lived access keys exist anywhere:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3RW",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::csueb-reflow-temp",
        "arn:aws:s3:::csueb-reflow-temp/*",
        "arn:aws:s3:::csueb-reflow-results",
        "arn:aws:s3:::csueb-reflow-results/*"
      ]
    },
    {
      "Sid": "PollyOptional",
      "Effect": "Allow",
      "Action": ["polly:SynthesizeSpeech"],
      "Resource": "*"
    },
    {
      "Sid": "BedrockOptional",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": [
        "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-haiku-4-5-*",
        "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-5-*"
      ]
    }
  ]
}
```

If Cal State prefers, drop the Polly + Bedrock statements; the app degrades gracefully (audio returns 503, AI uses Anthropic direct).

### Bucket configuration the ISO should require

- **Encryption at rest**: SSE-S3 (AES-256) minimum; SSE-KMS with a CSU-managed key if preferred.
- **Versioning**: enabled on `results` bucket so a bad pipeline run doesn't lose history.
- **Public access**: blocked at the bucket level (`BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets` all `true`). Our app uses presigned URLs for short-lived sharing.
- **Lifecycle**: auto-delete `temp/*` after 24 hours; the results bucket retains as long as the source PDF remains in Canvas (cleaned up on Canvas file deletion via webhook in Phase 2).
- **Logging**: server access logging enabled, logged to an audit bucket.

### Region

`us-west-2` (Oregon) for proximity to Cal State infrastructure. All data stays in this region.

## 2. AI provider request

Pick one path:

### Path A — Anthropic API direct (simplest)

I need an **Anthropic API key** for an **Anthropic Console Team or Enterprise plan**.

- Plan: Team (~$25/user/mo) or Enterprise (custom). Either provides the data-handling commitments below.
- Cost at our usage: ~$1/mo for 1000 PDFs converted (we use Claude Haiku 4.5, $0.0008 per typical 30-page PDF).
- Data Processing Addendum (DPA) is **standard** — Anthropic has signed equivalents with many universities. Cal State Procurement should ask for it explicitly.

### Path B — AWS Bedrock (data stays in AWS)

If Cal State prefers data to never leave AWS, use Bedrock instead:

- Enable Claude Haiku 4.5 and Claude Sonnet 4.5 in the AWS account, region `us-west-2`.
- Add the Bedrock statement to the IAM policy above.
- Same cost per document.
- No separate vendor contract — covered by your existing AWS agreement.

Path B is simpler from a compliance standpoint because Anthropic doesn't see the requests directly; AWS hosts the model in your tenant.

## Security questions an ISO will ask, with answers

### What data leaves the institution?

**Going to AWS (S3):** PDF files, extracted markdown, extracted images. Stored encrypted at rest. Stays in `us-west-2`. Lifecycle deletes within 24h for temp / on Canvas deletion for results.

**Going to the AI provider (Anthropic or Bedrock):** Per-page text and page images extracted from the PDF, plus prompts asking the model to fix accessibility (heading hierarchy, alt text, etc.). PII is scanned BEFORE this step with Microsoft Presidio; PII-flagged documents do not proceed to AI without explicit instructor approval.

**Going to Polly (if enabled):** Plain-text version of the accessible HTML, for TTS conversion. Cached output stored in our S3 bucket.

**Going to MathJax CDN:** Nothing leaves the institution — MathJax runs in the user's browser. No data sent outbound.

### What does the AI provider do with our data?

- **Anthropic** (direct): Per their commercial Terms, customer inputs are not used for training. Data is retained for up to 30 days for abuse monitoring, then deleted. Anthropic has signed FERPA-compatible agreements with universities; specific BAA-equivalent paperwork available on request to procurement.
- **AWS Bedrock**: AWS's Service Terms apply. Inputs are not used to train AWS or third-party models. Data stays in your AWS region. No separate vendor relationship beyond AWS.

### FERPA: is student data going to a third party?

PDFs in Canvas can contain student data (e.g., graded papers, returned assignments). To control this:

- Reflow runs Microsoft Presidio (open-source PII scanner) on every PDF *before* AI processing. PII findings flag the document and require instructor approval before AI calls happen.
- Our default config sets `PII_DETECTION_ENABLED=true` and `PII_CONFIDENCE_THRESHOLD=0.7`. Lowering the threshold catches more PII at the cost of false positives.
- For Cal State's pilot scope, recommend limiting `CANVAS_WATCHED_COURSES` to instructor-uploaded course material only (syllabi, lecture handouts, readings) — not assignment submissions or feedback files. The scanner walks every Canvas surface, but you can restrict by course ID list during the pilot.

If your FERPA officer needs documentation of how PII handling works, point them to `docs/reference/pipeline-phases.md` and the Presidio section.

### Encryption

- **In transit**: HTTPS / TLS 1.2+ everywhere. LTI launches are JWT-signed and validated against Canvas's JWKS.
- **At rest in S3**: SSE-S3 (AES-256) or SSE-KMS with a CSU key.
- **At rest in Redis**: not encrypted by default. Redis stores job metadata (file IDs, status), faculty-edited HTML, and session cookies. For production, run Redis with TLS and require AUTH. AWS ElastiCache Redis can be the prod option with encryption at rest enabled.
- **Secrets**: API keys / Canvas tokens in environment variables for dev, AWS Secrets Manager (or HashiCorp Vault) for production.

### Authentication and authorization

- **Canvas → Tool**: LTI 1.3 OIDC + JWT-signed launch. Validated against Canvas JWKS on every launch. No passwords.
- **Tool → Canvas**: A single Canvas API token belonging to a dedicated service account `accessibility-service@csueb.edu`. Scoped via Canvas's permission system. Rotatable; revocable from Canvas admin.
- **Tool → AWS**: IAM user or IAM role (preferred for instances/Fargate). Credentials never in source code.
- **Tool → AI provider**: API key in env. Rotatable.

### Audit logging

- Every Reflow pipeline run logs structured JSON: which Canvas file, which instructor, what AI calls, what cost. Shipped to CloudWatch or campus log aggregation.
- Faculty HTML edits are logged with timestamp + (LTI session) user id.
- Canvas API calls logged with request id for traceability.
- AI calls have token-level traceability via Logfire (optional, can be disabled).
- **Faculty consent acknowledgments are logged append-only** (see consent flow section below) with user id, timestamp, IP address, and disclaimer version.

### Faculty consent / authorization flow (built and wired)

On first launch, before Reflow processes any document, the faculty member
sees an in-app disclaimer explaining how the tool handles content and PII.
They must check three acknowledgment boxes ("I authorize processing", "I
understand PII handling", "I remain responsible for content") and click
"I Agree" before the tool will proceed. The acknowledgment is recorded in
Redis with user id, timestamp, IP address, and disclaimer version, plus
an append-only entry in the audit log key `eq-pdf:canvas:consent:audit`.

Subsequent launches skip the prompt — the Alt Formats modal shows a small
footer reminder ("✓ Authorized on May 14, 2026 · View terms") and faculty
can revisit the disclaimer page at any time. If the disclaimer language
ever changes, we bump `CURRENT_CONSENT_VERSION` in
`src/canvas/state.py` and every faculty member is re-prompted until they
acknowledge the new version. The audit log preserves all historical
acknowledgments so the ISO can pull a complete record at any time.

**Why this matters for the security review.** Explicit user consent is a
recognized mitigation under FERPA, NIST AI RMF (Govern function 1.3 and
3.1), and most university AI policies. It changes the data-handling story
from "silent automatic processing of faculty content" to "documented
user-authorized processing with audit trail" — typically the difference
between a long ISO review and a fast conditional approval.

**Files involved.** Backend: `src/api/canvas_consent.py` (routes),
`src/canvas/state.py` (Redis persistence + audit log). Frontend:
`src/web/canvas_review/panorama.js` (status probe + banner/footer in
Alt Formats modal). See `briefs/diagrams/03-consent-flow.png` for the
sequence diagram.

### Vendor security posture

- **Anthropic**: SOC 2 Type II, HIPAA-compliant agreements available, ISO 27001 in progress. Published list of certifications at trust.anthropic.com.
- **AWS**: SOC 1/2/3, ISO 27001, FedRAMP Moderate (us-east, us-west). Cal State likely already has an AWS GovCloud or commercial agreement.
- **Reflow upstream (UIC)**: open-source, AGPL-3.0. Source auditable. Maintained by University of Illinois Chicago.
- **Equalify Reflow Docker image**: built from source by Cal State; you own the SBOM.

### Incident response

- **Compromised Canvas API token**: revoke in Canvas admin, generate new, redeploy.
- **Compromised AI key**: rotate in Anthropic/AWS console, redeploy.
- **Compromised AWS credentials**: rotate IAM access key (or detach IAM role policy if role-based), redeploy.
- **Suspected breach of S3 bucket**: enable S3 access analyzer, audit access logs, rotate KMS keys if SSE-KMS is used.
- **Faculty data deletion request**: redirect to Canvas's normal file-deletion flow; we follow Canvas's lead.

### Data retention

- **S3 `temp` bucket**: 24-hour lifecycle.
- **S3 `results` bucket**: held while the source PDF exists in Canvas. Phase 2 adds a Canvas Live Events webhook so we clean up when files are deleted from Canvas.
- **Redis (job metadata + edited HTML)**: ~30 days for completed jobs, configurable via `JOB_RETENTION_DAYS`.
- **Anthropic / Bedrock**: see vendor sections above (30 days at Anthropic; not retained at Bedrock beyond the API call).
- **Logs**: per Cal State's log retention policy (typically 90 days).

### Network exposure

- The tool sits at one public HTTPS URL. Only the following are exposed externally:
  - `/lti/login`, `/lti/launch`, `/lti/jwks`, `/lti/config.json` — LTI handshake endpoints
  - `/lti/panorama.js` — the Theme JS bundle
  - `/canvas/panorama/*` — score, alt-format, edit endpoints (CORS-restricted to CSUEB Canvas)
  - `/canvas/review/*` — instructor review UI (requires LTI session cookie)
  - `/health`, `/metrics` — bound to localhost or behind auth in prod
- All other endpoints (`/api/v1/*`) are protected by API-key auth.
- No SSH on the public surface; admin access via campus VPN only.

### Open security gaps (be upfront)

- **Tests** for the new Canvas-integration endpoints aren't written yet. We have Reflow's existing test suite; the integration code is hand-tested.
- **CSP on Theme JS** is currently permissive (allow_origins=`*` with credentials=false). Production should narrow to CSUEB Canvas origins specifically (`CANVAS_ALLOWED_ORIGINS` env var supports this).
- **Rate limiting** on the Canvas API client is implicit (60s poll cadence); a malicious Canvas token would not have rate limit guards on our side.
- **Faculty HTML edits** are stored unencrypted in Redis. For sensitive content, recommend running Redis with TLS + AUTH in prod (we can switch to AWS ElastiCache).

## Specific paperwork I'd ask the ISO to confirm or kick off

1. **AWS Data Processing Addendum**: confirm Cal State's master agreement covers this use case, or add a project-specific DPA.
2. **Anthropic DPA / Enterprise Terms** (if using Path A): request from Anthropic if not already on file at the system level.
3. **FERPA review**: confirm PII-scanning + instructor-approval flow satisfies your FERPA officer for instructor-uploaded course materials.
4. **Penetration test / security review** of the deployed instance before opening to general faculty. The codebase is small enough for a 1-day review.

## Concrete asks summary

For the ISO to provision and approve, in order:

1. **AWS IAM service account** with the policy above, region `us-west-2`.
2. **Two S3 buckets** with the configuration above (encryption, no public access, lifecycle, logging).
3. **AI access** — either an Anthropic API key (Team or Enterprise plan) OR enabled Bedrock models in the AWS account.
4. **TLS certificate** for the public hostname (e.g., `reflow.csueb.edu`).
5. **Sign-off** on the PII + FERPA approach.
6. **Allowlist** the production public hostname for Canvas Custom JS (some institutions restrict which domains can be used as Theme JS sources).

Total provisioning time, typical higher-ed pace: 2–3 weeks for AWS + AI access; 1 day for TLS cert; 1 week for FERPA officer sign-off (parallelizable).

## AI Governance

ISO will want to see this covered. AI governance for higher-ed in 2026 is shaped by NIST AI RMF, the CSU system's evolving AI guidance, and FERPA/Title IV obligations. Below are the categories an AI governance review typically checks, with our answers.

### Approved-vendor and intended-use scope

- **Vendor**: Anthropic (PBC, San Francisco) or AWS Bedrock-hosted Claude. Both are widely used in the CSU system; check whether CSU's Office of the Chancellor has a system-level master agreement before procuring fresh.
- **Model**: Claude Haiku 4.5 (efficient tier, ~95% of calls) and Claude Sonnet 4.5 (reasoning tier, ~5% of calls). Both are general-purpose LLMs, **not** specifically certified for student data or healthcare.
- **Intended use**: Convert instructor-uploaded PDFs into accessible alternative formats. Specifically: reorder heading hierarchies, propose alt text for images, normalize table structure, fix reading order. **Not** intended for: grading, evaluating student work, generating original instructional content, scoring student writing, or any high-stakes decision.
- **Out-of-scope explicitly**: No AI calls touch student submissions, grades, or feedback. The watcher's scope can be limited to specific courses or sub-accounts to enforce this at deployment.

### Data classification

Every PDF processed falls into one of these categories — Cal State should classify before the pilot:

| Class | Example | Send to AI? | Notes |
|---|---|---|---|
| Public / instructional | Syllabus, lecture notes, public readings | Yes | Low-risk; the design target |
| Restricted / FERPA | Graded papers, transcripts, accommodations letters | No | PII scan flags; instructor must approve |
| Confidential | Personnel docs, legal materials in a course folder | No | Should not be in course Files; watcher will still flag |

The pipeline runs Microsoft Presidio against every PDF *before* AI calls. Documents that trip thresholds for SSN, credit card, names+addresses, etc. are paused and require instructor approval. The instructor sees what PII was found before deciding.

### Human-in-the-loop

Two human-review gates:

1. **PII gate** (before AI runs): if Presidio finds PII above threshold, the document doesn't reach AI until the instructor explicitly clicks "approve" on the PII findings.
2. **Faculty HTML editor** (after AI runs): instructors can edit the accessible HTML directly. Especially important for STEM (chemistry, math, physics) where the AI might mishandle notation. Edited HTML becomes the source of truth for every alternative format.

Neither is bypassed automatically; both are explicit user actions logged with timestamp and user id.

### Hallucination and accuracy risk

- **Type of task limits risk**: the AI's job is *transformation* of existing text, not generation of new claims. It rewrites headings, proposes alt text for images, normalizes tables. It does not invent facts or generate original course content.
- **Known weak spots**:
  - Complex math / chemistry notation: AI may mangle MathML/LaTeX. Mitigation: faculty editor + the HTML-with-MathJax format. STEM faculty should review every page.
  - Alt text on figures: may be overconfident or generic ("a graph showing data"). Mitigation: instructor reviews/edits via the editor.
  - OCR fallback on scanned PDFs: Tesseract introduces OCR errors. Mitigation: confidence scores are exposed, instructor can review.
- **No high-stakes use**: nothing in the pipeline affects grades, eligibility, or any decision about a student. Errors degrade accessibility output quality, they don't harm anyone.

### Bias and fairness

- **Where bias could appear**: AI-generated alt text could reflect societal biases from the training corpus (e.g., gender-coded descriptions of people in photos, racial assumptions). Translation quality may be lower for less-resourced languages.
- **Mitigation**:
  - Alt text is reviewed by the instructor before publication (faculty editor).
  - Translation supports a configurable language list; default to the languages CSUEB's student body actually uses.
  - The Reflow project (UIC upstream) has an active research interest in alt-text bias evaluation — we can track their findings.
- **What we do not claim**: this tool has not been independently audited for disparate impact across protected classes. If Cal State requires that audit, schedule it as part of the pilot.

### Audit trail and transparency

- Every AI call is logged with: timestamp, instructor id, file id, model used, input token count, output token count, cost. Logged in structured JSON to CloudWatch or campus log aggregation.
- Logfire (optional but recommended for AI workloads) captures full agent traces — prompts, responses, retries. Toggle via `LOGFIRE_ENABLED=true`.
- Faculty HTML edits are versioned in Redis with timestamp + user id.
- Disclosable to faculty on request: every AI call made against their material.

### Faculty opt-out

- The tool runs on instructor-uploaded materials, not student submissions, so the consent boundary is the instructor.
- Per-course opt-out: an instructor can ask their admin to exclude their course id from `CANVAS_WATCHED_COURSES`. Future Phase 2: a per-instructor toggle in the LTI tool itself.
- Per-document opt-out: at the PII gate, instructors can deny AI processing for that specific document; only PII scan and standard extraction run.

### Student-facing disclosure

- Students don't see AI-generated content unless the instructor has explicitly approved/published it via the review queue (or the Files page natively displays the original PDF).
- The alternative formats students download are clearly marked as accessible versions. The footer says "Powered by Equalify Reflow" so attribution is in-band.
- Recommend Cal State adopt a course-level syllabus statement disclosing the tool's use, similar to Cal State's existing AI-tutor disclosures. Template available on request.

### Output ownership and IP

- **Input PDFs**: owned by the instructor (or whoever uploaded them to Canvas). We don't claim any rights.
- **Output**: derivative of the input. Owned by the instructor. We store it in our S3 but they can request deletion via the normal Canvas file-deletion flow.
- **AI provider position**: both Anthropic and AWS Bedrock contractually disclaim ownership of outputs and don't train on inputs.
- **Cal State retains all rights**: nothing about deploying this transfers IP to a vendor.

### NIST AI Risk Management Framework alignment

Where this maps:

- **MAP function**: AI use is documented (this doc), intended use bounded, stakeholders identified (instructors, students, accessibility office, ISO, FERPA officer).
- **MEASURE function**: cost per call, accuracy via human review, error rates trackable via logs. Hallucination measurement: instructor approval rate as a proxy.
- **MANAGE function**: opt-out paths exist, human-in-the-loop at two points, audit trail, vendor agreements in place.
- **GOVERN function**: this brief plus the production-readiness doc constitute the governance baseline. CSU-system-level governance may add requirements.

### Cal State / CSU system alignment

CSU's evolving AI use guidance (Chancellor's Office / ITAC) may require:

- Inclusion in an approved-AI-use registry — register the tool there.
- Privacy threshold assessment (PTA) — start one before pilot launch.
- Annual review by a campus AI governance committee — if CSUEB has one, brief them.
- Faculty Senate / academic governance approval — depending on use scope, the Senate may want to weigh in on whether AI-assisted accessibility tooling falls under their AI policy purview.

I don't have visibility into CSUEB's specific AI governance committee or process — your ISO will. List that as an explicit step in the rollout plan.

### Ongoing AI governance commitments

If this ships to production, recommend:

- **Quarterly AI usage review**: cost, volume, error rates, instructor satisfaction. ~half-day per quarter.
- **Annual model review**: Anthropic releases new Claude models every ~6 months. Evaluate before swapping. Document any change in `ai/model_tiers.py`.
- **Annual bias audit**: sample alt-text outputs across course types. Flag if any group's content is consistently under-described.
- **Incident response for AI failure**: if a hallucinated alt text becomes a Title IX / harassment issue, the editor + audit log let us trace and fix quickly.

### What's NOT covered (be honest)

- No formal model risk management plan beyond this brief.
- No third-party audit yet.
- No automated bias testing (manual sampling only).
- We rely on Anthropic / AWS for foundation-model evaluation; we do no in-house red-teaming.
- The OCR step (Tesseract) is a non-AI process but introduces error rates that compound with AI hallucination risk on top.

If your AI governance committee requires any of those, schedule them as part of the pilot.
