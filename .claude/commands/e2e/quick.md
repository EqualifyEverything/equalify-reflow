# Quick E2E Demo

Minimal single-line progress. Auto-approves everything, no report.

## Arguments

`$ARGUMENTS`

- First arg: PDF path (default: `project-docs/pdfs/11_structured_programming.pdf`)
- `production`: Use production API

---

## Execution

**Setup:**
```bash
API_KEY=$(grep '^API_KEYS=' .env | cut -d= -f2 | cut -d, -f1)
API_URL="http://localhost:8080"  # or production URL
PDF="project-docs/pdfs/11_structured_programming.pdf"  # or from args
```

**Run each step, output single line per step:**

```
Preflight... OK
Submitting... {job_id}
PII scan... approved
Processing... (2-4 min)
Processing... done
Applying... 10 corrections
Saving... ~/Downloads/result.md
Done (92% confidence, $0.34)
```

**The steps:**

1. `curl -s $API_URL/health | jq -r '.status'` → "Preflight... OK"

2. Submit PDF, extract job_id → "Submitting... {job_id}"

3. Poll until not `pii_scanning`, auto-approve with justification ≥10 chars → "PII scan... approved"

4. Poll every 20 seconds until terminal state → "Processing... done"

5. `curl -s -X POST ".../apply-reviews" -d '{"force":true}'` → "Applying... {n} corrections"

6. `curl -s ".../result" | jq -r '.markdown' > ~/Downloads/result.md` → "Saving..."

7. Extract confidence + cost → "Done (92% confidence, $0.34)"

8. `open ~/Downloads/result.md`

---

## Example Output

```
$ /e2e/quick

Preflight... OK
Submitting... 77e95ccd-efac-49d4-a636-a8dbf7c3dab9
PII scan... approved (1 finding)
Processing... (be patient)
Processing... done
Applying... 10 corrections
Saving... ~/Downloads/result.md
Done (92% confidence, $0.34)
```

No dashboard, no timeline, no metrics breakdown. Just progress and result.
