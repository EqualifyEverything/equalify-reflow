# API Reference

Base URL: `http://localhost:8080` (development)

All endpoints require authentication via `X-API-Key` header unless otherwise noted.

## Core Endpoints

### Submit Document

**POST** `/api/v1/documents/submit`

Upload a PDF document for processing.

#### Request

```
Content-Type: multipart/form-data
X-API-Key: <api-key>
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | File | Yes | PDF file to process |
| `skip_pii_scan` | boolean | No | Bypass PII scanning (default: false) |
| `skip_reason` | string | No | Audit trail reason for skipping PII scan |
| `review_mode` | string | No | `"auto"` (default) or `"human"` |
| `max_rounds` | integer | No | Maximum processing rounds (1-5, default: 1). Use 2+ for iterative refinement. |
| `generate_debug_bundle` | boolean | No | Generate debug bundle (default: false) |

#### Response (201 Created)

```json
{
  "job_id": "abc123",
  "status": "processing",
  "estimated_completion_minutes": 5,
  "created_at": "2025-01-09T10:00:00Z",
  "stream_url": "/api/v1/documents/abc123/stream"
}
```

#### Example

```bash
curl -X POST http://localhost:8080/api/v1/documents/submit \
  -H "X-API-Key: your-api-key" \
  -F "file=@document.pdf" \
  -F "skip_pii_scan=true" \
  -F "review_mode=auto"
```

---

### Get Job Status

**GET** `/api/v1/documents/{job_id}`

Get current status of a processing job.

#### Response by Status

**Processing:**
```json
{
  "job_id": "abc123",
  "status": "processing",
  "filename": "document.pdf",
  "review_mode": "auto",
  "processing_phase": "execution",
  "jobs_total": 15,
  "jobs_complete": 8,
  "stream_url": "/api/v1/documents/abc123/stream",
  "created_at": "2025-01-09T10:00:00Z",
  "updated_at": "2025-01-09T10:02:30Z"
}
```

**Completed:**
```json
{
  "job_id": "abc123",
  "status": "completed",
  "filename": "document.pdf",
  "review_mode": "auto",
  "markdown_url": "https://s3.../results/abc123/result.md",
  "confidence_score": 0.87,
  "ledger_url": "/api/v1/documents/abc123/ledger",
  "total_pages": 12,
  "total_edits": 45,
  "llm_cost": {
    "input_tokens": 125000,
    "output_tokens": 8500,
    "total_tokens": 133500,
    "estimated_cost_cents": 18.5,
    "estimated_cost_dollars": 0.185,
    "calls": [...]
  },
  "created_at": "2025-01-09T10:00:00Z",
  "updated_at": "2025-01-09T10:05:00Z"
}
```

**Failed:**
```json
{
  "job_id": "abc123",
  "status": "failed",
  "filename": "document.pdf",
  "error": "Processing timeout after 10 minutes",
  "created_at": "2025-01-09T10:00:00Z",
  "updated_at": "2025-01-09T10:10:00Z"
}
```

---

### Stream Events (SSE)

**GET** `/api/v1/documents/{job_id}/stream`

Real-time event stream via Server-Sent Events.

#### Authentication Options

1. **API Key Header:** Standard `X-API-Key` header
2. **Stream Token:** Query parameter `?token=<token>` (for browser EventSource)

#### Generate Stream Token

```bash
curl -X POST http://localhost:8080/api/v1/documents/abc123/stream/token \
  -H "X-API-Key: your-api-key"
```

Response:
```json
{
  "token": "stream_xyz789",
  "expires_in_seconds": 300,
  "stream_url": "/api/v1/documents/abc123/stream?token=stream_xyz789"
}
```

#### Event Types

| Category | Events | Key Data |
|----------|--------|----------|
| Extraction | `docling:started`, `docling:complete` | `{page_count}` |
| Planning | `planning:started`, `planning:structure`, `planning:page_summarized`, `planning:complete` | `{outline}`, `{job_count}` |
| Jobs | `job:created`, `job:started`, `job:completed` | `{job_id, type, page}` |
| Agent | `agent:thinking` | `{agent, tool, args}` |
| Edits | `edit:committed`, `edit:validated` | `{before, after, confidence}` |
| Verification | `verification:started`, `verification:page`, `verification:complete` | `{page, passed}` |
| Recovery | `recovery:started`, `recovery:complete` | `{recovered, failed}` |
| Terminal | `processing:complete`, `processing:error`, `done` | `{markdown_url}` or `{error}` |

#### JavaScript Example

```javascript
const eventSource = new EventSource(
  '/api/v1/documents/abc123/stream?token=stream_xyz789'
);

eventSource.addEventListener('edit:committed', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Edit: ${data.before} → ${data.after}`);
});

eventSource.addEventListener('processing:complete', (e) => {
  const data = JSON.parse(e.data);
  console.log(`Done! Markdown: ${data.markdown_url}`);
  eventSource.close();
});

eventSource.addEventListener('done', () => {
  eventSource.close();
});
```

---

### Get Ledger

**GET** `/api/v1/documents/{job_id}/ledger`

Get complete change ledger for review. Only available after job completes.

#### Response

```json
{
  "job_id": "abc123",
  "document_title": "document.pdf",
  "total_pages": 12,
  "pages_with_changes": 8,
  "total_edits": 45,
  "entries_needing_review": 3,
  "pages": [
    {
      "page": 1,
      "edit_count": 5,
      "entries": [
        {
          "entry_id": "edit_001",
          "page": 1,
          "action": "ALT_TEXT",
          "target": "figure",
          "before": "<!-- image placeholder -->",
          "after": "![Bar chart showing enrollment trends from 2020-2024](image.png)",
          "reasoning": "Generated alt-text describing chart content and data trends",
          "confidence": 0.92,
          "timestamp": "2025-01-09T10:03:15Z",
          "needs_review": false
        }
      ]
    }
  ],
  "final_markdown_url": "https://s3.../results/abc123/result.md"
}
```

---

### Get Processing Phases

**GET** `/api/v1/documents/{job_id}/phases`

Get detailed outputs from each processing phase.

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `show_raw` | boolean | Include raw JSON from artifacts |

#### Response

```json
{
  "job_id": "abc123",
  "filename": "document.pdf",
  "status": "completed",
  "analysis": {
    "status": "completed",
    "document_title": "Course Syllabus",
    "document_type": "syllabus",
    "total_pages": 12,
    "layout_type": "single_column",
    "required_agents": ["structure", "typography"],
    "analysis_confidence": 0.95,
    "page_features": [...],
    "heading_tree": {...}
  },
  "extraction": {
    "status": "completed",
    "markdown_url": "https://s3.../abc123-v0.md",
    "confidence_score": 0.7
  },
  "agents": {
    "status": "completed",
    "agents_run": ["worker", "paragraph"],
    "observation_count": 23,
    "observations": [...]
  },
  "remediation": {
    "status": "completed",
    "auto_correction_count": 45,
    "applied_count": 42,
    "pending_count": 3
  },
  "verification": {
    "status": "completed",
    "total_pages": 12,
    "corrections_applied": 42,
    "corrections_failed": 0,
    "issues_found": 0,
    "all_pages_accurate": true
  },
  "total_llm_cost": {...}
}
```

---

### Download Debug Bundle

**GET** `/api/v1/documents/{job_id}/debug-bundle`

Download debug bundle as ZIP file. Only available if `generate_debug_bundle=true` was set on submission.

#### Response

```
Content-Type: application/zip
Content-Disposition: attachment; filename=debug_abc123.zip
```

#### Bundle Contents

```
debug_abc123.zip
├── README.md                    # Analysis instructions
├── input/
│   ├── original.pdf            # Original PDF
│   └── pages/
│       ├── page_001.png        # Page images
│       └── ...
├── phase_planning/
│   └── page_chain.json         # Planning prompts/responses
├── phase_execution/
│   ├── worker_001.json         # Worker agent traces
│   └── paragraph_001.json      # Paragraph agent traces
└── output/
    ├── manifest.json           # Processing manifest
    ├── observations.json       # All observations
    └── final_markdown.md       # Final output
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created (document submitted) |
| 400 | Bad request (e.g., job not complete for ledger) |
| 401 | Unauthorized (invalid/missing API key or token) |
| 404 | Not found (job doesn't exist) |
| 500 | Server error |

## Rate Limiting

- **Default:** 100 requests/minute per API key
- **SSE streams:** 1 concurrent stream per job
- **File uploads:** 10 MB max file size

Rate limit headers:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1704805200
```

## OpenAPI Documentation

Interactive API documentation available at:
- **Swagger UI:** `http://localhost:8080/docs`
- **ReDoc:** `http://localhost:8080/redoc`
