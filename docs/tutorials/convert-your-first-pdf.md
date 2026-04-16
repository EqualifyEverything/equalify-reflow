# Tutorial: Convert your first PDF

By the end of this walkthrough you will have run a PDF through the full Equalify Reflow pipeline on your own machine, approved a PII-flagged document, and downloaded the resulting accessible markdown. Plan for **~15 minutes of real time**, most of it waiting for the pipeline to finish.

This tutorial uses `curl` so every step is reproducible in a terminal. The viewer UI at `http://localhost:8080/` does the same thing in a browser — after you finish this, try it there too.

## What you need

- **Docker** (Desktop or Engine) with Compose v2
- **Git**
- An **Anthropic API key** — grab one at [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
- ~5 GB free disk for the Docker images
- A PDF to convert — if you don't have one, we'll use one of the fixtures in the repo

## 1. Clone the repo and configure credentials

```bash
git clone https://github.com/EqualifyEverything/equalify-reflow.git
cd equalify-reflow
cp .env.example .env
```

Open `.env` and set your API key (`sk-ant-...`). The other values in `.env.example` have sensible defaults for local dev; you don't need to change them.

```bash
# .env (the only line you need to edit)
ANTHROPIC_API_KEY=sk-ant-...
```

The pipeline auto-detects: if `ANTHROPIC_API_KEY` is set it uses Anthropic direct; otherwise it falls back to AWS Bedrock. For this tutorial, the direct path is simpler.

## 2. Boot the stack

```bash
make dev
```

The first run pulls and builds images (~3–5 minutes). Subsequent runs are ~30 seconds.

When the output settles, verify in a second terminal:

```bash
curl -s http://localhost:8080/health
```

You should see:

```json
{"status":"healthy","checks":{"redis":true,"s3":true,"queue_depth":0,"docling_serve":true}}
```

If any check is `false`, run `make logs-api` and see the troubleshooting section at the bottom of this tutorial.

## 3. Grab your API key

The stack generated an API key for local dev. Pull it out of `.env` and store it in a shell variable so the rest of this tutorial is copy-pasteable:

```bash
export API_KEY=$(grep "^API_KEYS=" .env | cut -d= -f2 | cut -d, -f1)
echo "$API_KEY"
# → uic-<uuid>
```

## 4. Submit a PDF

The repo ships with sample PDFs under `project-docs/pdfs/`. Pick a small one — documents under ~10 pages complete in 2–4 minutes:

```bash
curl -s -X POST http://localhost:8080/api/v1/documents/submit \
  -H "X-API-Key: $API_KEY" \
  -F "file=@project-docs/pdfs/14_watson_crick_dna.pdf" \
  | tee /tmp/reflow_submit.json | python3 -m json.tool
```

Expected response:

```json
{
  "job_id": "8c53b1d7-da0e-44b0-acbe-9c46096d6527",
  "status": "pii_scanning",
  "estimated_completion_minutes": 5,
  "created_at": "2026-04-16T20:04:51.789757Z",
  "stream_url": null
}
```

Capture the `job_id` for the next steps:

```bash
export JOB_ID=$(python3 -c "import json; print(json.load(open('/tmp/reflow_submit.json'))['job_id'])")
```

**File-size limits to know about:** the API rejects PDFs larger than **100 MB** up front (HTTP 413), and the pipeline itself refuses anything over **50 pages** — you'll get a `failed` status with an error telling you to split the document.

## 5. Check PII scan status

Every document goes through Microsoft Presidio PII detection before AI processing. This usually takes 5–10 seconds and is not optional.

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/documents/$JOB_ID" \
  | python3 -m json.tool
```

Two possible outcomes:

### No PII found → pipeline starts automatically

```json
{ "status": "processing", ... }
```

Skip ahead to **Step 7**.

### PII found → job awaits approval

```json
{
  "status": "awaiting_approval",
  "pii_findings": [
    {"entity_type": "EMAIL_ADDRESS", "text": "probst@uni-mainz.de", "score": 1.0}
  ],
  "approval_token": "_aVnWJV7CCiHEs2XfAzk2h1bV9dY-IeW7fouls-7KoQ",
  "approval_url": "/api/v1/approval/_aVnWJV7.../decision",
  "approval_expires_at": "2026-04-17T00:04:56Z"
}
```

The token is job-scoped, has a 4-hour TTL, and is consumed on first use. Reflow won't process the document until somebody makes an approve/deny decision.

The Watson–Crick sample PDF reliably trips the email detector — that's by design for this tutorial.

## 6. Approve the PII-flagged job

```bash
export TOKEN=$(curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/documents/$JOB_ID" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['approval_token'])")

curl -s -X POST -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  "http://localhost:8080/api/v1/approval/$TOKEN/decision" \
  -d '{
    "decision": "approved",
    "justification": "test document, email is public contact info",
    "reviewed_by": "tutorial-runner"
  }'
```

Response:

```json
{
  "message": "Job approved - processing started",
  "job_id": "...",
  "decision": "approved"
}
```

Status flips to `processing`. The pipeline starts.

## 7. Watch the five phases run

Poll status every 15 seconds, or stream live with SSE. Polling is simpler for a first tutorial:

```bash
while :; do
  SNAP=$(curl -s -H "X-API-Key: $API_KEY" "http://localhost:8080/api/v1/documents/$JOB_ID")
  echo "$SNAP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['status']:<22} phase={d.get('current_phase') or '—'}\")"
  grep -qE '"status":\s*"(completed|failed)"' <<< "$SNAP" && break
  sleep 15
done
```

You'll see the status progress through the 5 public phases — see [pipeline phases reference](../reference/pipeline-phases.md) for what each one actually does:

1. **Extraction** — Docling pulls text and layout out of the PDF (~30–90 s depending on page count)
2. **Analysis** — AI classifies the document and maps its structure
3. **Headings** — AI reconciles the heading hierarchy
4. **Translation** — AI fixes per-page content and tags code blocks
5. **Assembly** — Cross-page boundary fixes + cleanup

Typical end-to-end time for a 9-page document: **2–4 minutes after approval.**

**Viewer option:** open `http://localhost:8080/` in a browser at any point during steps 4–7. Find your job in the list and click through to see the viewer step through each phase with live diffs between versions. It's the same pipeline; just a different UI.

## 8. Retrieve the result

When `status: completed`:

```bash
curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/documents/$JOB_ID" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"Status: {d['status']}\")
print(f\"Pages:  {d.get('total_pages')}\")
print(f\"Markdown URL: {d.get('markdown_url')}\")
print(f\"Bundle URL:   {d.get('bundle_url')}\")
print(f\"Ledger URL:   {d.get('ledger_url')}\")
print(f\"Figures: {len(d.get('figures', []))}\")
"
```

Download the markdown:

```bash
MARKDOWN_URL=$(curl -s -H "X-API-Key: $API_KEY" \
  "http://localhost:8080/api/v1/documents/$JOB_ID" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['markdown_url'])")

curl -s "$MARKDOWN_URL" -o /tmp/reflow_result.md
head -50 /tmp/reflow_result.md
```

You should see semantic markdown — `##` headings, paragraphs, inline `![](figures/...)` references to extracted images, markdown tables where the source had tables. Compare it to the original PDF to see what the AI corrected.

## 9. Explore what just happened

- **Change ledger** — every edit the AI made, with its reasoning:

  ```bash
  curl -s -H "X-API-Key: $API_KEY" \
    "http://localhost:8080/api/v1/documents/$JOB_ID/ledger" \
    | python3 -m json.tool | head -40
  ```

- **Bundle** — a zip of everything (markdown, figures, ledger, source PDF):

  ```bash
  curl -s -H "X-API-Key: $API_KEY" \
    "http://localhost:8080/api/v1/documents/$JOB_ID/bundle" \
    -o /tmp/reflow_bundle.zip
  unzip -l /tmp/reflow_bundle.zip
  ```

- **Viewer** — the same document with interactive diffs per phase at `http://localhost:8080/` → click your job.

## 10. Tear down

```bash
make down
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `curl: (7) Failed to connect to localhost port 8080` | Stack isn't ready. Watch `make logs-api`; the API waits for Redis + Floci + Docling to report healthy before binding :8080. |
| `health` reports `"docling_serve": false` | Docling can be slow to start on first boot (pulls large models). Wait 60 seconds and retry. |
| `{"detail": "Invalid API key"}` on your first request | Your shell lost the `API_KEY` variable. Re-run `export API_KEY=$(grep ...)` from step 3. |
| Job stuck in `pii_scanning` for more than a minute | `make logs-api | grep pii` — the PII worker may have crashed. Restart with `make down && make dev`. |
| Job completes but the markdown is empty or full of garbage | Open the viewer at `http://localhost:8080/`, find your job, and step through phases to see where the output went wrong. Grab the ledger (`/ledger`) for the raw agent output. |
| `PDF has N pages, which exceeds the maximum of 50` | By design. Split the PDF externally and submit the pieces separately. |

## Where to go next

- [How to iterate on a prompt](../how-to/iterate-on-a-prompt.md) — if you want to improve what the AI produces
- [How to add a new agent](../how-to/add-a-new-agent.md) — for adding a new step to the pipeline
- [Architecture](../explanation/architecture.md) — the service diagram and data flows behind what you just watched
- [Pipeline phases reference](../reference/pipeline-phases.md) — canonical mapping of the 5 public phases to internal step methods
