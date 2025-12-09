# File Upload Demo - Complete API Workflow Walkthrough

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

First, determine the API URL and set up authentication:

```bash
# Set API_URL based on whether "production" argument is present
if [[ "$1" == "production" ]] || [[ "$2" == "production" ]]; then
  API_URL="https://pdf.equalify.uic.edu"
  ENV_NAME="PRODUCTION"
else
  API_URL="http://localhost:8080"
  ENV_NAME="LOCAL"
fi

# Get API key from .env file (first key if multiple)
API_KEY=$(grep '^API_KEYS=' .env | sed 's/API_KEYS=//' | cut -d',' -f1)

echo "Environment: $ENV_NAME ($API_URL)"
echo "API Key loaded from .env"
```

**IMPORTANT - Curl Usage Notes:**

Due to shell escaping issues with curl, follow these guidelines:
1. **Never use inline JSON with spaces in -d flag** - Use files or shell variables instead
2. **For file uploads with metadata** - Keep metadata simple or use variables
3. **For approval requests** - Always write JSON to file first, then use -d @file
4. **Test commands** - If curl gives "blank argument" errors, refactor to use files

---

## Step 1: Pre-Flight Checks

Verify system is ready using API endpoints only:

```bash
# Check API health
curl -s $API_URL/health | jq .
```

**Expected Response (Healthy):**
```json
{
  "status": "healthy",
  "checks": {
    "redis": true,
    "s3": true,
    "queue_depth": 0
  }
}
```

For LOCAL environment only - check queue status:
```bash
if [ "$ENV_NAME" == "LOCAL" ]; then
  curl -s $API_URL/api/dev/monitoring/queues | jq .
fi
```

---

## Step 2: Submit PDF Document

Upload the PDF to start processing:

```bash
# Set PDF file path (use argument or default)
PDF_FILE="${1:-project-docs/pdfs/11_structured_programming.pdf}"

# Skip if first arg is "production"
if [[ "$PDF_FILE" == "production" ]]; then
  PDF_FILE="project-docs/pdfs/11_structured_programming.pdf"
fi

# Create submission script to avoid curl escaping issues
cat > /tmp/submit_pdf.sh << SCRIPT
#!/bin/bash
curl -X POST $API_URL/api/documents/submit \
  -H "X-API-Key: $API_KEY" \
  -F "file=@$PDF_FILE" \
  -F 'metadata={"title":"Demo","source":"walkthrough"}' \
  -s
SCRIPT

chmod +x /tmp/submit_pdf.sh
/tmp/submit_pdf.sh | jq . | tee /tmp/submit_response.json
```

**Expected Response:**
```json
{
  "job_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "status": "pii_scanning",
  "estimated_completion_minutes": 5,
  "created_at": "2025-01-06T..."
}
```

**Save the job_id** for next steps:
```bash
JOB_ID=$(cat /tmp/submit_response.json | jq -r '.job_id')
echo "Job ID: $JOB_ID"
```

**What happened:**
- PDF uploaded to S3 temp storage
- Job record created in Redis
- Queued in `pii_scan` queue
- PII worker picks up job for scanning

---

## Step 3: Monitor PII Scanning

Check job status (wait 5-10 seconds for PII scan to complete):

```bash
# Wait for PII scan to start processing
sleep 5

# Check status
curl -s -H "X-API-Key: $API_KEY" "$API_URL/api/documents/$JOB_ID" | jq . | tee /tmp/pii_status.json
```

**Possible outcomes:**

**A) PII Found** - Status: `awaiting_approval`
```json
{
  "job_id": "...",
  "status": "awaiting_approval",
  "created_at": "2025-01-06T10:00:00Z",
  "updated_at": "2025-01-06T10:05:00Z",
  "pii_findings": [
    {
      "entity_type": "EMAIL_ADDRESS",
      "text": "example@email.com",
      "score": 1.0
    }
  ],
  "approval_token": "xxxxxxxxxxxxx",
  "approval_expires_at": "2025-01-06T14:00:00Z"
}
```

**B) No PII** - Status: `processing` (auto-approved)
```json
{
  "job_id": "...",
  "status": "processing",
  "created_at": "2025-01-06T10:00:00Z",
  "updated_at": "2025-01-06T10:05:00Z",
  "estimated_completion_minutes": 5
}
```

**What happened:**
- Microsoft Presidio scanned document text
- Detected PII entities (emails, phone numbers, SSNs, etc.)
- If PII found: moved to `awaiting_approval` status
- If no PII: auto-approved, moved to `processing` status

---

## Step 4: Approve PII (if needed)

If status is `awaiting_approval`, submit approval decision:

```bash
# Extract approval token from PII status
APPROVAL_TOKEN=$(cat /tmp/pii_status.json | jq -r '.approval_token')
echo "Approval Token: $APPROVAL_TOKEN"

# Create approval JSON file (avoids curl escaping issues)
cat > /tmp/approval.json << 'EOF'
{
  "decision": "approved",
  "reviewed_by": "demo@equalify.uic.edu",
  "justification": "Course material - instructor contact information is acceptable"
}
EOF

# Submit approval (requires both API key AND approval token)
curl -X POST "$API_URL/api/approval/$APPROVAL_TOKEN/decision" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d @/tmp/approval.json \
  -s | jq . | tee /tmp/approval_response.json
```

**Expected Response:**
```json
{
  "message": "Job approved for processing successfully",
  "job_id": "...",
  "decision": "approved"
}
```

**Note:** The approval endpoint may take 10-30 seconds to respond. Be patient.

**What happened:**
- Approval decision recorded in Redis
- Job moved to `processing` status
- Processing worker picks up job for AI conversion

---

## Step 5: Monitor AI Processing

Poll for processing completion (AI processing takes 1-5 minutes depending on page count):

```bash
# Poll for completion
for i in {1..40}; do
  echo "=== Check $i ($(date +%H:%M:%S)) ==="

  RESPONSE=$(curl -s -H "X-API-Key: $API_KEY" "$API_URL/api/documents/$JOB_ID")
  echo "$RESPONSE" | jq . | tee /tmp/processing_status.json

  STATUS=$(echo "$RESPONSE" | jq -r '.status')

  if [ "$STATUS" == "completed" ]; then
    echo "Job completed!"
    break
  elif [ "$STATUS" == "failed" ]; then
    echo "Job failed!"
    break
  fi

  sleep 15
done
```

**What's happening during processing:**

The FullDocumentAgent uses a **two-phase extraction approach**:

1. **Docling Conversion**: PDF to Page Images (PNG)
   - Generates high-resolution page images for AI processing
   - No OCR or markdown extraction (AI handles this directly)

2. **Phase 1 - Structure Analysis** (AWS Bedrock / Claude):
   - Receives all page images
   - Analyzes document structure
   - Builds a HeadingTree with document outline
   - Detects layout type (single_column, two_column, mixed)

3. **Phase 2 - Guided Transcription** (AWS Bedrock / Claude):
   - Receives all page images + HeadingTree from Phase 1
   - Transcribes document content guided by the heading structure
   - Produces complete markdown with proper hierarchy

**Status when complete:** `completed`
```json
{
  "job_id": "...",
  "status": "completed",
  "created_at": "2025-01-06T10:00:00Z",
  "updated_at": "2025-01-06T10:15:00Z",
  "result": {
    "markdown_url": "https://s3.../results/550e8400.../output.md",
    "confidence_score": 0.92,
    "processing_time_seconds": 145
  },
  "llm_usage": {
    "input_tokens": 15000,
    "output_tokens": 3000,
    "total_tokens": 18000,
    "estimated_cost_cents": 1.5
  }
}
```

**Page Limit:** Documents are limited to 15 pages for full-context processing. Larger documents will fail with an error message.

---

## Step 6: Retrieve and Download Results

Get the final result and download the markdown:

```bash
# Get final result
curl -s -H "X-API-Key: $API_KEY" "$API_URL/api/documents/$JOB_ID/result" | jq . | tee /tmp/final_result.json
```

**Expected Response:**
```json
{
  "job_id": "...",
  "status": "completed",
  "markdown_url": "https://s3.../results/550e8400.../output.md",
  "confidence_score": 0.92,
  "processing_time_seconds": 145,
  "llm_usage": {
    "input_tokens": 15000,
    "output_tokens": 3000,
    "total_tokens": 18000,
    "estimated_cost_cents": 1.5
  }
}
```

**Download the markdown file:**

```bash
# Extract markdown URL
MARKDOWN_URL=$(cat /tmp/final_result.json | jq -r '.markdown_url')
echo "Markdown URL: $MARKDOWN_URL"

# Generate output filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE=~/Downloads/equalify_result_${TIMESTAMP}.md

# Download to Downloads folder
curl -s "$MARKDOWN_URL" -o "$OUTPUT_FILE"

# Verify download
ls -lh "$OUTPUT_FILE"

# Preview first 30 lines
echo "=== First 30 lines of output ==="
head -30 "$OUTPUT_FILE"

# Open in default application
open "$OUTPUT_FILE"
```

**Verify the output:**
- Proper heading hierarchy based on visual analysis
- Correct list formatting (bullets vs numbers)
- Table structure preserved
- Paragraph breaks matching visual spacing
- File saved to Downloads folder

---

## Complete Workflow Summary

```
1. Submit PDF → API uploads to S3
2. PII Scan → Presidio detects sensitive data
3. PII Approval → Human reviews findings (if PII found)
4. Processing → Docling page images + FullDocumentAgent two-phase extraction
5. Results → Final markdown available via S3 URL
6. Download → File saved to user's Downloads folder
```

**Queue Progression:**
```
pii_scan → approval_pending → processing → completed
```

**Status Progression:**
```
pii_scanning → awaiting_approval → processing → completed
```

---

## Error Handling

If status is `failed`:

```bash
curl -s -H "X-API-Key: $API_KEY" "$API_URL/api/documents/$JOB_ID/result" | jq .
```

**Common failures:**
- **AWS Bedrock API errors**:
  - 403 Forbidden: Invalid/expired AWS credentials (run `aws sso login --profile uic`)
  - 429 Throttling: Too many concurrent requests to Bedrock
  - 500 Internal Error: Bedrock service issues (retry helps)
- **Processing failures**:
  - Invalid PDF format (corrupted or encrypted PDFs)
  - Document exceeds 15 pages (max_pages_full_context limit)
  - Docling page image generation failures
- **Infrastructure failures**:
  - S3 upload/download failures
  - Redis connection errors

---

## Troubleshooting

### Check Service Health
```bash
curl -s $API_URL/health | jq .
```

### Check Queue Status (LOCAL only)
```bash
curl -s $API_URL/api/dev/monitoring/queues | jq .
```

### View Container Logs (LOCAL only, for debugging)
```bash
docker logs equalify-pdf-api-gateway --tail 100
```

### Check Processing Activity (LOCAL only)
```bash
docker logs equalify-pdf-api-gateway 2>&1 | grep -E "FullDocumentAgent|Phase 1|Phase 2|BedrockConverseModel"
```

### Curl Escaping Issues

**Problem:** curl with inline JSON gives "blank argument" errors

**Solution:** Always use file-based JSON:
```bash
cat > /tmp/data.json << 'EOF'
{"key": "value with spaces"}
EOF
curl -d @/tmp/data.json ...
```

### AWS Bedrock Not Working

**Symptoms:**
- Job completes in <30 seconds (too fast)
- `confidence_score: 0.0` in result

**Solution:**
```bash
# Login to AWS SSO
aws sso login --profile uic

# Restart services (LOCAL)
./restart-and-test.sh
```

---

## Implementation Notes

**Execution Style**: Act as a tour guide walking the user through the system. Explain what's happening at each step.

**Execute these steps:**
1. Determine environment (local vs production) from arguments
2. Set API_URL, ENV_NAME, and API_KEY variables
3. Run pre-flight health check via API
4. Submit PDF with API key authentication
5. Extract job_id and save for subsequent calls
6. Poll status until not `pii_scanning`
7. If `awaiting_approval`, submit PII approval decision
8. Poll status until `completed` or `failed` (expect 1-5 minutes)
9. Retrieve final results
10. Download markdown to ~/Downloads with timestamp
11. Report complete workflow with cost summary

**Report format:**
```
Step 1: Health check passed (services healthy)
Step 2: PDF submitted (job_id: xxx)
Step 3: PII scan complete (found X entities)
Step 4: PII approval submitted
Step 5: AI processing (1-5 minutes)...
        - Phase 1: Structure analysis
        - Phase 2: Guided transcription
Step 6: Results downloaded to ~/Downloads/equalify_result_xxx.md

Total time: X minutes
LLM Cost: $0.0XXX (X.X cents)
Confidence: 92%

Your processed document is ready!
```
