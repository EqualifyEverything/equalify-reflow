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

Poll for processing completion (AI processing takes 2-8 minutes):

```bash
# Poll for completion
for i in {1..40}; do
  echo "=== Check $i ($(date +%H:%M:%S)) ==="

  RESPONSE=$(curl -s -H "X-API-Key: $API_KEY" "$API_URL/api/documents/$JOB_ID")
  echo "$RESPONSE" | jq . | tee /tmp/processing_status.json

  STATUS=$(echo "$RESPONSE" | jq -r '.status')

  if [ "$STATUS" == "awaiting_correction_approval" ]; then
    echo "AI processing complete! Ready for correction review."
    break
  elif [ "$STATUS" == "completed" ]; then
    echo "Job completed (corrections may have been auto-applied)."
    break
  elif [ "$STATUS" == "failed" ]; then
    echo "Job failed!"
    break
  fi

  sleep 15
done
```

**What's happening during processing:**
1. **Docling Conversion**: PDF to Markdown + Page Images (PNG)
   - Extracts text and structure from PDF
   - Generates high-resolution page images for visual comparison
2. **Text Correction via AWS Bedrock** (Claude Haiku):
   - Processes pages concurrently (max 5 at once)
   - For each page:
     - Sends page image + extracted markdown to Claude
     - Claude compares visual layout to markdown structure
     - Identifies corrections: heading levels, list types, tables, paragraph breaks
     - Returns corrections with confidence scores (0.0-1.0)
   - Applies corrections to markdown
   - Calculates overall document confidence

**Status when corrections are ready:** `awaiting_correction_approval`
```json
{
  "job_id": "...",
  "status": "awaiting_correction_approval",
  "created_at": "2025-01-06T10:00:00Z",
  "updated_at": "2025-01-06T10:15:00Z",
  "correction_approval": {
    "token": "secure-token-abc...",
    "expires_at": "2025-01-06T14:15:00Z",
    "total_corrections": 5,
    "confidence_score": 0.89,
    "corrections_by_type": {
      "heading_level": 2,
      "list_structure": 3
    },
    "review_url": "/api/corrections/{job_id}/review?token={token}"
  },
  "urls": {
    "original_markdown": "https://s3.../original.md",
    "corrected_markdown": "https://s3.../corrected.md",
    "page_images": ["https://s3.../page-1.png", "..."]
  },
  "llm_cost": {
    "total_cost_cents": 0.15,
    "total_cost_dollars": 0.0015,
    "page_costs": [...]
  }
}
```

---

## Step 6: Review Text Corrections

When status is `awaiting_correction_approval`, review the AI-suggested corrections:

```bash
# Extract correction token from status response
CORRECTION_TOKEN=$(cat /tmp/processing_status.json | jq -r '.correction_approval.token')
echo "Correction Token: $CORRECTION_TOKEN"

# Get correction details for review
curl -s -H "X-API-Key: $API_KEY" \
  "$API_URL/api/corrections/$JOB_ID/review?token=$CORRECTION_TOKEN" | jq . | tee /tmp/corrections.json
```

**Expected Response:**
```json
{
  "job_id": "abc-123",
  "total_corrections": 5,
  "overall_confidence": 0.89,
  "by_type": {
    "heading_level": 2,
    "list_structure": 3
  },
  "by_page": {
    "1": 3,
    "2": 2
  },
  "corrections": [
    {
      "page": 1,
      "type": "heading_level",
      "original_snippet": "Course Schedule",
      "corrected_snippet": "## Course Schedule",
      "confidence": 0.95,
      "explanation": "Visual layout shows level 2 heading with larger font"
    },
    {
      "page": 1,
      "type": "list_structure",
      "original_snippet": "- Item 1\n- Item 2",
      "corrected_snippet": "1. Item 1\n2. Item 2",
      "confidence": 0.87,
      "explanation": "Numbered list detected in visual layout"
    }
  ],
  "urls": {
    "original_markdown": "https://s3.../original.md",
    "corrected_markdown": "https://s3.../corrected.md",
    "page_images": [
      "https://s3.../page-1.png",
      "https://s3.../page-2.png"
    ]
  },
  "expires_at": "2025-01-06T14:00:00Z"
}
```

**Review the corrections:**
- Check total_corrections and overall_confidence
- Review individual corrections with before/after snippets
- Compare page images with markdown if needed
- Decide whether to approve (use corrected) or reject (use original)

---

## Step 7: Approve Corrections

Submit your decision on the AI corrections:

```bash
# Create correction decision JSON
cat > /tmp/correction_decision.json << EOF
{
  "token": "$CORRECTION_TOKEN",
  "decision": "approved",
  "reviewed_by": "demo@equalify.uic.edu",
  "justification": "AI corrections improve document structure and heading hierarchy"
}
EOF

# Submit correction decision
curl -X PATCH -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d @/tmp/correction_decision.json \
  "$API_URL/api/corrections/$JOB_ID" | jq . | tee /tmp/correction_response.json
```

**Expected Response (Approved):**
```json
{
  "job_id": "abc-123",
  "status": "completed",
  "decision": "approved",
  "message": "Corrections approved successfully - corrected markdown is now final"
}
```

**Expected Response (Rejected):**
```json
{
  "job_id": "abc-123",
  "status": "completed",
  "decision": "rejected",
  "message": "Corrections rejected successfully - original markdown is now final"
}
```

**What happened:**
- Decision recorded in job metadata
- If approved: corrected markdown becomes final output
- If rejected: original markdown becomes final output
- Job status updated to `completed`

---

## Step 8: Retrieve and Download Results

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
  "confidence_score": 0.89,
  "processing_time_seconds": 145,
  "correction_decision": {
    "decision": "approved",
    "reviewed_by": "demo@equalify.uic.edu",
    "reviewed_at": "2025-01-06T10:20:00Z"
  },
  "llm_cost": {
    "total_cost_cents": 0.15,
    "total_cost_dollars": 0.0015,
    "page_costs": [
      {
        "page": 1,
        "input_tokens": 2048,
        "output_tokens": 256,
        "cost_cents": 0.08
      }
    ]
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
- Corrected heading levels (based on visual font size/weight)
- Proper list formatting (bullets vs numbers, nesting preserved)
- Table structure improvements (headers, alignment)
- Paragraph breaks matching visual spacing
- File saved to Downloads folder

---

## Complete Workflow Summary

```
1. Submit PDF → API uploads to S3
2. PII Scan → Presidio detects sensitive data
3. PII Approval → Human reviews findings (if PII found)
4. Processing → Docling + AWS Bedrock text correction
5. Correction Review → Human reviews AI corrections
6. Correction Approval → Accept or reject corrections
7. Results → Final markdown available via S3 URL
8. Download → File saved to user's Downloads folder
```

**Queue Progression:**
```
pii_scan → approval_pending → processing → awaiting_correction_approval → completed
```

**Status Progression:**
```
pii_scanning → awaiting_approval → processing → awaiting_correction_approval → completed
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
  - OCR failures (scanned PDFs with poor image quality)
  - Text correction timeouts (large documents with many pages)
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

### Check Text Correction Activity (LOCAL only)
```bash
docker logs equalify-pdf-api-gateway 2>&1 | grep -E "text correction|BedrockConverseModel|Processing page"
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
8. Poll status until not `processing` (expect 2-8 minutes)
9. If `awaiting_correction_approval`, display correction summary
10. Pause to let user review corrections before approving
11. Submit correction approval decision
12. Retrieve final results
13. Download markdown to ~/Downloads with timestamp
14. Report complete workflow with cost summary

**Report format:**
```
Step 1: Health check passed (services healthy)
Step 2: PDF submitted (job_id: xxx)
Step 3: PII scan complete (found X entities)
Step 4: PII approval submitted
Step 5: AI processing (2-8 minutes)...
Step 6: Corrections ready for review:
        - Total: 5 corrections
        - By type: heading_level (2), list_structure (3)
        - Confidence: 89%
Step 7: Corrections approved
Step 8: Results downloaded to ~/Downloads/equalify_result_xxx.md

Total time: X minutes
LLM Cost: $0.00XX (0.XX cents)
Confidence: 89%

Your processed document is ready!
```
