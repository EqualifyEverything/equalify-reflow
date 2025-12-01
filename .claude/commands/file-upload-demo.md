 File Upload Demo - Complete API Workflow Walkthrough

Walk through the complete end-to-end API workflow by uploading and processing a PDF document.

## Arguments

`<PDF_FILE_PATH>` - Optional path to PDF file (defaults to `project-docs/pdfs/11_structured_programming.pdf`)
`<production>` - Optional flag: if set to "production", uses production URL (https://pdf.equalify.uic.edu/), otherwise uses localhost

Example: `/file-upload-demo` (local with default PDF)
Example: `/file-upload-demo /path/to/custom.pdf` (local with custom PDF)
Example: `/file-upload-demo production` (production with default PDF)
Example: `/file-upload-demo /path/to/custom.pdf production` (production with custom PDF)

---

## Environment Setup

First, determine the API URL based on environment:

```bash
# Set API_URL based on whether "production" argument is present
if [[ "$1" == "production" ]] || [[ "$2" == "production" ]]; then
  API_URL="https://pdf.equalify.uic.edu"
  ENV_NAME="PRODUCTION"
else
  API_URL="http://localhost:8080"
  ENV_NAME="LOCAL"
fi

echo "🌍 Running against: $ENV_NAME ($API_URL)"
```

**IMPORTANT - Curl Usage Notes:**

Due to shell escaping issues with curl, follow these guidelines:
1. **Never use inline JSON with spaces in -d flag** - Use files or shell variables instead
2. **For file uploads with metadata** - Keep metadata simple or use variables
3. **For approval requests** - Always write JSON to file first, then use -d @file
4. **Test commands** - If curl gives "blank argument" errors, refactor to use files

Example of what NOT to do:
```bash
# ❌ FAILS - Spaces in JSON string confuse curl
curl -d '{"key": "value with spaces"}' ...
```

Example of what TO do:
```bash
# ✅ WORKS - Write JSON to file first
cat > /tmp/payload.json << 'EOF'
{"key": "value with spaces"}
EOF
curl -d @/tmp/payload.json ...
```

---

## Step 1: Pre-Flight Checks

Verify system is ready:

```bash
# Check API health
curl -s $API_URL/health | jq .

# For LOCAL only - Check Docker containers
if [ "$ENV_NAME" == "LOCAL" ]; then
  docker ps --format "table {{.Names}}\t{{.Status}}" | grep equalify-pdf

  # Check queue status (dev endpoint only available locally)
  curl -s $API_URL/api/dev/monitoring/queues | jq .
fi
```

Expected: All services healthy, queues empty

**Note:** Production does not expose dev monitoring endpoints for security reasons.

---

## Step 2: Submit PDF Document

Upload the PDF to start processing:

**IMPORTANT:** Use a shell script to avoid curl escaping issues with file uploads:

```bash
# Set PDF file path (use argument or default)
PDF_FILE="${1:-project-docs/pdfs/11_structured_programming.pdf}"

# Create submission script
cat > /tmp/submit_pdf.sh << SCRIPT
#!/bin/bash
curl -X POST $API_URL/api/documents/submit \
  -F "file=@$PDF_FILE" \
  -F 'metadata={"title":"Demo","source":"walkthrough"}' \
  -s | jq .
SCRIPT

chmod +x /tmp/submit_pdf.sh
/tmp/submit_pdf.sh | tee /tmp/submit_response.json
```

Alternative approach (if script method fails):
```bash
# Keep metadata simple - no spaces
curl -X POST $API_URL/api/documents/submit \
  -F "file=@$PDF_FILE" \
  -F 'metadata={"title":"Demo","source":"test"}' \
  -s | jq .
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

```bash
# Extract job_id from response
JOB_ID=$(cat /tmp/submit_response.json | jq -r '.job_id')
echo "Job ID: $JOB_ID"
```

**What happened**:
- PDF uploaded to S3 temp storage
- Job record created
- Queued in `pii_scan` queue
- PII worker picks up job

---

## Step 3: Monitor PII Scanning

Check job status (wait 5-10 seconds first for PII scan to complete):

```bash
# Wait for PII scan to complete
sleep 5

# Check status
curl -s "$API_URL/api/documents/$JOB_ID" | jq .

# If still scanning, wait and check again
sleep 10
curl -s "$API_URL/api/documents/$JOB_ID" | jq . | tee /tmp/pii_status.json
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

**CRITICAL:** Must use file-based JSON to avoid curl escaping errors:

```bash
# Extract approval token from PII status
APPROVAL_TOKEN=$(cat /tmp/pii_status.json | jq -r '.approval_token')
echo "Approval Token: $APPROVAL_TOKEN"

# Create approval JSON file
cat > /tmp/approval.json << 'EOF'
{
  "decision": "approved",
  "reviewed_by": "demo@equalify.uic.edu",
  "justification": "Course material - instructor contact information is acceptable"
}
EOF

# Submit approval using file
curl -X POST "$API_URL/api/approval/$APPROVAL_TOKEN/decision" \
  -H "Content-Type: application/json" \
  -d @/tmp/approval.json \
  -s | jq . | tee /tmp/approval_response.json
```

**Note:** The approval endpoint may take 10-30 seconds to respond, especially in production. Be patient.

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
# Poll for completion (AI processing takes 2-8 minutes)
for i in {1..30}; do
  echo "=== Check $i ($(date +%H:%M:%S)) ==="
  curl -s "$API_URL/api/documents/$JOB_ID" | jq .

  STATUS=$(curl -s "$API_URL/api/documents/$JOB_ID" | jq -r '.status')

  if [ "$STATUS" == "completed" ] || [ "$STATUS" == "failed" ]; then
    echo "✅ Processing finished with status: $STATUS"
    break
  fi

  sleep 15
done

# For LOCAL only - Check queue depth and logs
if [ "$ENV_NAME" == "LOCAL" ]; then
  curl -s $API_URL/api/dev/monitoring/queues | jq .
  docker logs equalify-pdf-api-gateway --tail 50
fi
```

**Status**: `processing`

**What's happening**:
1. **Docling Conversion**: PDF → Markdown + Page Images (PNG)
   - Extracts text and structure from PDF
   - Generates high-resolution page images for visual comparison
2. **Text Correction via AWS Bedrock** (Claude Haiku):
   - Lazy-initializes TextCorrectionAgent on first job
   - Processes pages concurrently (max 5 at once)
   - For each page:
     - Sends page image + extracted markdown to Claude Haiku
     - Claude compares visual layout to markdown structure
     - Identifies corrections needed:
       - Heading levels (e.g., H1 vs H2 based on font size/weight)
       - List types (bullets vs numbers vs nested)
       - Table structure (column headers, cell alignment)
       - Paragraph breaks (spacing, indentation)
     - Returns corrections with confidence scores (0.0-1.0)
   - Applies corrections to markdown
   - Calculates overall confidence (average of page confidences)
3. Uploads corrected Markdown to S3 results bucket
4. Updates job status with confidence score

**Typical duration**: 1-3 minutes (depends on page count and AWS Bedrock API latency)

**Expected Results:**
- **Confidence score**: 0.85-0.95 (typical range for well-formatted documents)
- **Processing time**: 60-180 seconds (varies with page count and API latency)
- **Corrections found**: 1-5 per page on average (heading levels, list formatting)
- **Concurrent processing**: Up to 5 pages analyzed simultaneously via AWS Bedrock

**⚠️ If you see unexpected results:**
- Confidence score = 0.0 → Text correction was skipped (check AWS credentials)
- Processing time < 30 seconds → AI step may have failed (check logs)
- No corrections found → Document may already be perfectly formatted
- Check logs: `docker logs equalify-pdf-api-gateway | grep "text correction"`

---

## Step 6: Retrieve Results

Once status changes to `completed`:

```bash
# Check final status
curl -s "$API_URL/api/documents/$JOB_ID" | jq .

# Get results
curl -s "$API_URL/api/documents/$JOB_ID/result" | jq . | tee /tmp/final_result.json
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
# Extract markdown URL from result
MARKDOWN_URL=$(cat /tmp/final_result.json | jq -r '.markdown_url')
echo "Markdown URL: $MARKDOWN_URL"

# Generate output filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE="~/Downloads/equalify_result_${TIMESTAMP}.md"

# Download Markdown to Downloads folder
curl -s "$MARKDOWN_URL" -o "$OUTPUT_FILE"

# Verify file was downloaded
ls -lh "$OUTPUT_FILE"

# Preview first 30 lines
echo "=== First 30 lines of output ==="
head -30 "$OUTPUT_FILE"

# Open in default application
open "$OUTPUT_FILE"
```

**Verify**:
- ✅ Corrected heading levels (based on visual font size/weight)
- ✅ Proper list formatting (bullets vs numbers, nesting preserved)
- ✅ Table structure improvements (headers, alignment)
- ✅ Paragraph breaks matching visual spacing
- ✅ High confidence score (0.85-0.95 typical)
- ✅ File saved to Downloads folder

---

## Complete Workflow Summary

```
1. Submit PDF → API uploads to S3
2. PII Scan → Presidio detects sensitive data
3. Approval → Human reviews PII findings (if PII found)
4. Processing:
   a. Docling converts PDF → Markdown + Page Images (PNG)
   b. AWS Bedrock (Claude Haiku) compares images to markdown
   c. Identifies layout/structure corrections
   d. Applies corrections, calculates confidence
5. Results → Corrected Markdown available via S3 URL
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
curl -s "$API_URL/api/documents/$JOB_ID/result" | jq .
```

**Common failures**:
- **AWS Bedrock API errors**:
  - 403 Forbidden: Invalid/expired AWS credentials (run `aws sso login --profile uic`)
  - 429 Throttling: Too many concurrent requests to Bedrock
  - 500 Internal Error: Bedrock service issues (retry helps)
  - Connection timeout: Network issues or IMDS hang (check AWS_EC2_METADATA_DISABLED=true)
- **Processing failures**:
  - Invalid PDF format (corrupted or encrypted PDFs)
  - OCR failures (scanned PDFs with poor image quality)
  - Text correction timeouts (large documents with many pages)
- **Infrastructure failures**:
  - S3 upload/download failures (LocalStack or AWS connectivity)
  - Redis connection errors (worker communication)

Check logs (LOCAL only):
```bash
docker logs equalify-pdf-api-gateway --tail 100
```

For production errors, check AWS CloudWatch logs:
```bash
# From terraform directory
make aws-logs
```

---

## Implementation Notes

**Execution Style**: Act as a tour guide walking the user through the system. Explain what's happening at each step and why it matters.

**Execute these steps**:
1. Determine environment (local vs production) from arguments
2. Set API_URL and ENV_NAME variables
3. Run pre-flight checks and explain system readiness
4. Submit PDF using shell script method to avoid curl escaping issues
5. Extract job_id and save to variable
6. Poll status until not `pii_scanning` and explain PII detection purpose
7. If `awaiting_approval`, extract approval_token and submit approval using file-based JSON
8. Poll status until not `processing` (expect 2-8 minutes for AI)
9. Retrieve results and save to /tmp files
10. Download markdown to ~/Downloads with timestamped filename
11. Report complete workflow with timestamps and insights

**Critical Implementation Details**:

1. **Always use shell scripts or files for curl with JSON**
   - Direct curl with -d and JSON strings containing spaces WILL FAIL
   - Use `cat > file.json << 'EOF'` then `curl -d @file.json`
   - Or create .sh scripts with curl commands

2. **Variable expansion in heredocs**
   - Use `<< 'EOF'` (quoted) to prevent variable expansion
   - Use `<< EOF` (unquoted) if you need variable substitution
   - For API_URL in scripts, use unquoted heredocs or direct variable substitution

3. **Save all responses to /tmp files**
   - Use `| tee /tmp/response.json` to save and display
   - Extract values with jq: `$(cat /tmp/file.json | jq -r '.field')`
   - Makes debugging easier and allows data flow between steps

4. **Production timing differences**
   - Production approval endpoint may take 10-30s to respond (be patient)
   - AI processing should take 2-8 minutes (if faster, it failed silently)
   - Check confidence_score and processing_time_seconds to verify AI ran

**Narrative Guidelines**:
- Use friendly, explanatory language ("Now we're submitting the PDF to the API...")
- Explain the "why" behind each step ("We scan for PII to ensure student data privacy...")
- Point out what's happening in the background ("The worker is now processing in the `pii_scan` queue...")
- Celebrate milestones ("✅ PII scan complete! Found 1 email address...")
- Provide context about timing ("This typically takes 2-5 seconds...")
- Flag anomalies ("⚠️ Completed in 60s - expected 2-8min for AI processing")

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

---

## Troubleshooting

### Curl "blank argument where content is expected" Error

**Problem:** When running curl with inline JSON containing spaces:
```bash
# ❌ This FAILS
curl -d '{"key": "value with spaces"}' ...
```

**Root cause:** Shell escapes and special characters in JSON confuse curl argument parsing

**Solution:** Always use file-based JSON:
```bash
# ✅ This WORKS
cat > /tmp/data.json << 'EOF'
{"key": "value with spaces"}
EOF
curl -d @/tmp/data.json ...
```

### AWS Bedrock Text Correction Not Working

**Symptoms:**
- Job completes in <30 seconds (too fast for AI processing)
- `confidence_score: 0.0` in result
- No "text correction" messages in logs

**Investigation steps:**

1. **Check AWS credentials are valid:**
   ```bash
   # Login to AWS SSO
   aws sso login --profile uic

   # Verify credentials loaded
   aws sts get-caller-identity --profile uic
   ```

2. **Check logs for text correction activity:**
   ```bash
   docker logs equalify-pdf-api-gateway 2>&1 | grep -E "text correction|Lazy-initializing|BedrockConverseModel|Processing page"
   ```

   Expected logs:
   - "Starting AI text correction analysis"
   - "Lazy-initializing TextCorrectionAgent on first use"
   - "BedrockConverseModel created successfully"
   - "Processing page X for text corrections with bedrock"
   - "Page X analyzed: Y corrections found (confidence: 0.XX)"

3. **Check for AWS Bedrock errors:**
   ```bash
   docker logs equalify-pdf-api-gateway 2>&1 | grep -E "403|Forbidden|Throttling|AccessDenied|IMDS"
   ```

4. **Verify environment variables:**
   ```bash
   docker exec equalify-pdf-api-gateway env | grep -E "AWS_|AI_PROVIDER|BEDROCK"
   ```

   Expected:
   - `AI_PROVIDER=bedrock`
   - `AWS_DEFAULT_REGION=us-east-1`
   - `AWS_EC2_METADATA_DISABLED=true`
   - `AWS_ENDPOINT_URL_S3=http://localstack:4566`
   - `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` should be UNSET (not pointing to LocalStack)

5. **Restart with fresh credentials:**
   ```bash
   ./restart-and-test.sh
   ```

**Common fixes:**
- Expired AWS SSO session → Run `aws sso login --profile uic`
- IMDS hang → Ensure `AWS_EC2_METADATA_DISABLED=true` in docker-compose
- Bedrock routing to LocalStack → Check no global `AWS_ENDPOINT_URL` variable

### File Upload Metadata Issues

**Problem:** File upload with complex metadata fails

**Solution:** Keep metadata simple or use a shell script:
```bash
# Simple metadata (works)
curl -F "file=@path.pdf" -F 'metadata={"title":"Demo","source":"test"}' ...

# Complex metadata (use script)
cat > /tmp/upload.sh << 'SCRIPT'
#!/bin/bash
curl -F "file=@$PDF_FILE" \
  -F 'metadata={"title":"Complex Title","source":"walkthrough"}' \
  $API_URL/api/documents/submit
SCRIPT
chmod +x /tmp/upload.sh
/tmp/upload.sh
```

### Approval Endpoint Slow Response

**Symptom:** Approval request hangs or takes 10-30 seconds

**Explanation:** This is normal behavior in production. The endpoint:
1. Records approval decision in Redis
2. Moves job to processing queue
3. May wait for worker to pick up job
4. Returns success response

**Solution:** Be patient, don't kill the request. Use timeouts:
```bash
curl --max-time 60 -d @/tmp/approval.json ...
```

### Variables Not Expanding in Heredocs

**Problem:** `$API_URL` appears literally in output

**Root cause:** Quoted heredoc `<< 'EOF'` prevents variable expansion

**Solution:** Use unquoted heredoc for variable substitution:
```bash
# Variables NOT expanded
cat > file.sh << 'EOF'
curl $API_URL/health
EOF

# Variables expanded
cat > file.sh << EOF
curl $API_URL/health
EOF
```
