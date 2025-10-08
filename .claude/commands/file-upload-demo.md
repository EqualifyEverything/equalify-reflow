# File Upload Demo - Complete API Workflow Walkthrough

Walk through the complete end-to-end API workflow by uploading and processing a PDF document.

## Arguments

`<PDF_FILE_PATH>` - Optional path to PDF file (defaults to `project-docs/pdfs/11_structured_programming.pdf`)

Example: `/file-upload-demo`
Example: `/file-upload-demo project-docs/pdfs/01_plos_one_covid_xray.pdf`

---

## Step 1: Pre-Flight Checks

Verify system is ready:

```bash
# Check Docker containers
docker ps --format "table {{.Names}}\t{{.Status}}" | grep equalify-pdf

# Check API health
curl -s http://localhost:8080/health | jq .

# Check queue status
curl -s http://localhost:8080/api/dev/monitoring/queues | jq .
```

Expected: All services healthy, queues empty

---

## Step 2: Submit PDF Document

Upload the PDF to start processing:

```bash
PDF_FILE="<PDF_FILE_PATH or project-docs/pdfs/11_structured_programming.pdf>"

curl -X POST http://localhost:8080/api/documents/submit \
  -F "file=@$PDF_FILE" \
  -F 'metadata={"title":"Demo Document","source":"walkthrough"}' \
  2>/dev/null | jq .
```

**Expected Response**:
```json
{
  "job_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "pii_scanning",
  "estimated_completion_minutes": 5,
  "created_at": "2025-10-06T..."
}
```

**Save the job_id** for next steps.

**What happened**:
- PDF uploaded to S3 temp storage
- Job record created
- Queued in `pii_scan` queue
- PII worker picks up job

---

## Step 3: Monitor PII Scanning

Check job status (wait 2-5 seconds first):

```bash
JOB_ID="<job_id from step 2>"

curl -s http://localhost:8080/api/documents/$JOB_ID/status | jq .
```

**Possible outcomes**:

**A) PII Found** - Status: `awaiting_approval`
```json
{
  "job_id": "...",
  "status": "awaiting_approval",
  "pii_findings": [
    {
      "entity_type": "EMAIL_ADDRESS",
      "text": "example@email.com",
      "score": 1.0
    }
  ],
  "approval_token": "xxxxxxxxxxxxx"
}
```

**B) No PII** - Status: `processing` (auto-approved)

**What happened**:
- Microsoft Presidio scanned document text
- Detected PII entities (emails, phone numbers, etc.)
- If PII found: moved to `approval_pending` queue
- If no PII: auto-approved, moved to `processing` queue

---

## Step 4: Approve PII (if needed)

If status is `awaiting_approval`, submit approval:

```bash
APPROVAL_TOKEN="<approval_token from step 3>"

curl -X POST http://localhost:8080/api/approval/$APPROVAL_TOKEN/approve \
  -H "Content-Type: application/json" \
  -d '{
    "decision": "approved",
    "reviewed_by": "demo@example.com",
    "justification": "Course material - instructor contact info is acceptable"
  }' \
  2>/dev/null | jq .
```

**Expected Response**:
```json
{
  "message": "Job approved for processing successfully",
  "job_id": "...",
  "decision": "approved"
}
```

**What happened**:
- Approval decision recorded
- Job moved from `approval_pending` to `processing` queue
- Processing worker picks up job

---

## Step 5: Monitor AI Processing

Check processing status (poll every 10-15 seconds):

```bash
# Check job status
curl -s http://localhost:8080/api/documents/$JOB_ID/status | jq .

# Check queue depth
curl -s http://localhost:8080/api/dev/monitoring/queues | jq .

# Watch logs (optional)
docker logs equalify-pdf-api-gateway --tail 50 --follow
```

**Status**: `processing`

**What's happening**:
1. Docling converts PDF → Markdown
2. AI agent processes each page:
   - Generates contextual alt text for images
   - Fixes heading hierarchy
   - Converts math to MathML
   - Enhances table accessibility
   - Adds semantic structure
3. Generates final enhanced Markdown
4. Uploads results to S3

**Typical duration**: 2-8 minutes (depends on page count)

---

## Step 6: Retrieve Results

Once status changes to `completed`:

```bash
# Check final status
curl -s http://localhost:8080/api/documents/$JOB_ID/status | jq .

# Get results
curl -s http://localhost:8080/api/documents/$JOB_ID/result | jq .
```

**Expected Response**:
```json
{
  "job_id": "...",
  "status": "completed",
  "markdown_url": "https://s3.../results/<job_id>/output.md",
  "confidence_score": 0.87,
  "processing_metadata": {
    "pages_processed": 15,
    "processing_time_seconds": 145,
    "ai_model": "claude-3-5-haiku-20241022"
  }
}
```

---

## Step 7: Download and Inspect

Download the processed markdown file to your Downloads folder:

```bash
# Download Markdown to Downloads folder
curl -s "<markdown_url>" -o ~/Downloads/result.md

# Verify file was downloaded
ls -lh ~/Downloads/result.md

# Open in default application
open ~/Downloads/result.md
```

**Verify**:
- ✅ Proper heading hierarchy
- ✅ Alt text on images
- ✅ Semantic structure
- ✅ Accessible formatting
- ✅ File saved to Downloads folder

---

## Complete Workflow Summary

```
1. Submit PDF → API uploads to S3
2. PII Scan → Presidio detects sensitive data
3. Approval → Human reviews PII findings
4. Processing → Docling + AI enhance accessibility
5. Results → Markdown available via S3 URL
6. Download → File saved to user's Downloads folder
```

**Queue Progression**:
```
pii_scan → approval_pending → processing → completed
```

**Status Progression**:
```
submitted → pii_scanning → awaiting_approval → processing → completed
```

---

## Error Handling

If status is `failed`:

```bash
curl -s http://localhost:8080/api/documents/$JOB_ID/result | jq .
```

**Common failures**:
- API rate limits (429 errors)
- Invalid PDF format
- OCR failures (scanned PDFs)
- AI processing timeouts

Check logs:
```bash
docker logs equalify-pdf-api-gateway --tail 100
```

---

## Implementation Notes

**Execution Style**: Act as a tour guide walking the user through the system. Explain what's happening at each step and why it matters.

**Execute these steps**:
1. Run pre-flight checks and explain system readiness
2. Submit PDF, capture job_id, and explain what happens behind the scenes
3. Poll status until not `pii_scanning` and explain PII detection purpose
4. If `awaiting_approval`, explain why human review is required, then submit approval
5. Poll status until not `processing` and explain AI enhancement process
6. Retrieve results and explain what was generated
7. Download file to ~/Downloads and confirm location
8. Report complete workflow with timestamps and insights

**Narrative Guidelines**:
- Use friendly, explanatory language ("Now we're submitting the PDF to the API...")
- Explain the "why" behind each step ("We scan for PII to ensure student data privacy...")
- Point out what's happening in the background ("The worker is now processing in the `pii_scan` queue...")
- Celebrate milestones ("✅ PII scan complete! Found 1 email address...")
- Provide context about timing ("This typically takes 2-5 seconds...")

**Report format**:
```
✅ Step 1: Services healthy (all containers up and running)
✅ Step 2: PDF submitted (job_id: xxx, queued in pii_scan)
✅ Step 3: PII scan complete (found X entities - emails, phone numbers)
✅ Step 4: Approval submitted (justified as course material)
🔄 Step 5: Processing (AI enhancing accessibility...)
✅ Step 6: Results ready (markdown generated, uploaded to S3)
✅ Step 7: Downloaded to ~/Downloads/result.md

Total time: X minutes
Confidence: 87% (High)

📝 Your processed document is now in your Downloads folder!
```
