# E2E API Workflow Demo

Execute a complete end-to-end PDF processing workflow.

## Arguments

`$ARGUMENTS`

**Parsing:**
- `--job=UUID`: Resume an existing job (skip submit)
- First non-flag arg: PDF path (default: `project-docs/pdfs/11_structured_programming.pdf`)
- `production`: Use production API
- `--force`: Force-apply reviews without asking (default: interactive collaboration)
- `--verbose`: Show full API responses

---

## What to Expect

**Duration:** 5-10 minutes total (interactive) or 3-5 minutes (with --force)
- Preflight: instant
- PII scan: ~20 seconds
- Processing: 2-4 minutes (longest phase - be patient)
- Reviews: 2-5 minutes interactive, or ~10 seconds with --force

**Cost:** $0.15-0.50 per document (depends on size/complexity)

**Phases:**
```
[00:00] Preflight → [00:05] Submit → [00:20] PII → [00:25] Processing...
[03:30] Interactive Review (you decide each item) → [05:00] Download → Done
```

**Normal behaviors:**
- "processing" status for several minutes (AI working)
- 10-50 review items (more = more thorough, not a problem)
- Confidence 85%+ is good, 95%+ is excellent

---

## Phase 1: Setup

**Configuration:**
- API_URL: `http://localhost:8080` (or production if specified)
- API_KEY: from `.env`
- PDF_FILE: from args or default

**Get API key:**
```bash
grep '^API_KEYS=' .env | cut -d= -f2 | cut -d, -f1
```

**Report:**
```
════════════════════════════════════════════════════════════
  EQUALIFY E2E DEMO
════════════════════════════════════════════════════════════
  Environment: LOCAL | PDF: {filename} | ~3-5 min
════════════════════════════════════════════════════════════
```

---

## Phase 2: Preflight

Run all three checks. STOP if any fail.

```bash
# Check 1: API health
curl -s http://localhost:8080/health | jq -r '.status'

# Check 2: AWS credentials (CRITICAL)
aws sts get-caller-identity --profile uic 2>&1 | head -1

# Check 3: PDF exists
ls project-docs/pdfs/11_structured_programming.pdf
```

**If AWS check fails:**
```
PREFLIGHT FAILED: AWS credentials expired
Run: aws sso login --profile uic
```

Report: `[00:00] Preflight passed`

---

## Phase 3: Submit

Skip if `--job=UUID` provided.

```bash
curl -s -X POST "http://localhost:8080/api/documents/submit" \
  -H "X-API-Key: {API_KEY}" \
  -F "file=@{PDF_FILE}" \
  -F "metadata={\"title\":\"E2E Demo\",\"source\":\"cli\"}"
```

**Extract job_id:**
```bash
# Save response, extract job_id
JOB_ID=$(cat response.json | jq -r '.job_id')
```

Report: `[00:05] Submitted: {JOB_ID}`

---

## Phase 4: PII Scan

Poll every 5 seconds until status changes from `pii_scanning`:

```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}" | jq -r '.status'
```

**When status is `awaiting_approval`:**
```bash
# Extract token and approve
APPROVAL_TOKEN=$(cat status.json | jq -r '.approval_token')

curl -s -X POST "http://localhost:8080/api/approval/{APPROVAL_TOKEN}/decision" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"decision\":\"approved\",\"reviewed_by\":\"e2e-demo@cli\",\"justification\":\"E2E demo  auto-approved\"}"
```

Report: `[00:20] PII approved`

---

## Phase 5: Processing

This is the longest phase. Poll every 15-20 seconds:

```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}" | jq -r '.status'
```

**Terminal states:** `needs_review`, `completed`, `failed`

**If taking >2 minutes, check progress:**
```bash
docker logs equalify-reflow-api-gateway --tail 15 2>&1 | grep -E "Phase|Agent|complete|cost"
```

**If status is `failed`:**
- Check error: `jq -r '.error'`
- If `ExpiredTokenException`: run `aws sso login --profile uic`
- STOP and report error

Report progress: `[02:00] Processing... (Phase 2 complete)`

---

## Phase 6: Review Checklist (Interactive by Default)

**Default behavior (collaborative):**

1. Open the source PDF in VS Code for user reference: `code {PDF_PATH}`
2. Load the PDF into AI context using the Read tool
3. Get the checklist: `curl -s ... /checklist`
4. For EACH item, present context and use **AskUserQuestion** to ask the user:
   - Show: question, context, agent reasoning, confidence
   - Options: Accept recommendation, Use alternative, Skip, Custom input
   - Wait for user response
   - Apply their choice
5. After all items reviewed, apply reviews:
```bash
curl -s -X POST "http://localhost:8080/api/documents/{JOB_ID}/apply-reviews" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{}"
```

Report: `[03:30] Reviews applied: {count} corrections`

**Only if `--force` specified:**
Skip the interactive review and force-apply all recommended options:
```bash
curl -s -X POST "http://localhost:8080/api/documents/{JOB_ID}/apply-reviews" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"force\":true}"
```

---

## Phase 7: Download Result

**Get result and save markdown:**
```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}/result" | jq -r '.markdown' > ~/Downloads/equalify_result.md
```

**Extract metrics for report:**
```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}" | jq '{
  confidence: .confidence_score,
  tokens: .llm_cost.total_tokens,
  cost: .llm_cost.estimated_cost_cents
}'
```

Open the file: `open ~/Downloads/equalify_result.md`

---

## Phase 8: Final Report

```
════════════════════════════════════════════════════════════
  E2E DEMO COMPLETE
════════════════════════════════════════════════════════════

TIMELINE
────────
[00:00] Preflight passed
[00:05] Submitted: {job_id}
[00:20] PII approved
[03:30] Processing complete
[03:35] Reviews applied
[03:40] Downloaded

METRICS
───────
Confidence:  {confidence_score}%
Tokens:      {total_tokens}
Cost:        ${cost_cents / 100}

OUTPUT
──────
~/Downloads/equalify_result.md

════════════════════════════════════════════════════════════
```

---

## Quick Reference

| Error | Fix |
|-------|-----|
| `ExpiredTokenException` | `aws sso login --profile uic` |
| `Connection refused` | `make dev` |
| `401 Unauthorized` | Check `.env` API_KEYS |

**Resume a job:**
```
/e2e/run --job=550e8400-e29b-41d4-a716-446655440000
```
