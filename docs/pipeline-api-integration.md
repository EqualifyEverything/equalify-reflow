# V5 API Integration Guide

Complete guide for integrating with the V5 PDF processing API.

## API Endpoints

Base URL: `http://localhost:8080/api/v5`

### POST /api/v5/process

Submit a PDF for processing.

**Request:**
```http
POST /api/v5/process HTTP/1.1
Content-Type: multipart/form-data

file: (binary PDF data)
optimized: false (optional query param)
```

**cURL Example:**
```bash
curl -X POST http://localhost:8080/api/v5/process \
  -F "file=@document.pdf" \
  -F "optimized=false"
```

**Python Example:**
```python
import requests

with open("document.pdf", "rb") as f:
    response = requests.post(
        "http://localhost:8080/api/v5/process",
        files={"file": f},
        params={"optimized": False}
    )

job_id = response.json()["job_id"]
print(f"Job started: {job_id}")
```

**Response (200 OK):**
```json
{
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "pending",
  "stream_url": "/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000/stream",
  "status_url": "/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000"
}
```

**Error Responses:**
- `400 Bad Request` - Invalid file format (must be .pdf)
- `413 Payload Too Large` - File exceeds size limit
- `500 Internal Server Error` - Processing initialization failed

---

### GET /api/v5/jobs/{job_id}

Get current job status and progress.

**Request:**
```http
GET /api/v5/jobs/{job_id} HTTP/1.1
```

**cURL Example:**
```bash
curl http://localhost:8080/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000
```

**Python Example:**
```python
response = requests.get(
    f"http://localhost:8080/api/v5/jobs/{job_id}"
)
status = response.json()
print(f"Status: {status['status']} - Progress: {status['progress']*100:.1f}%")
```

**Response (200 OK):**
```json
{
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "status": "executing",
  "progress": 0.65,
  "total_pages": 10,
  "jobs_total": 25,
  "jobs_complete": 16,
  "ledger_entries": 42,
  "created_at": "2025-01-07T10:30:00Z",
  "error": null
}
```

**Status Values:**
- `pending` - Job queued
- `docling` - PDF conversion
- `vision_extraction` - OCR processing (scanned PDFs)
- `planning` - Structure analysis
- `executing` - Worker processing
- `verifying` - Quality checks
- `recovering` - Error recovery
- `complete` - Success
- `failed` - Unrecoverable error

**Error Responses:**
- `404 Not Found` - Job ID doesn't exist

---

### GET /api/v5/jobs/{job_id}/stream

Stream real-time processing events via Server-Sent Events (SSE).

**Request:**
```http
GET /api/v5/jobs/{job_id}/stream HTTP/1.1
Accept: text/event-stream
```

**cURL Example:**
```bash
curl -N http://localhost:8080/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000/stream
```

**JavaScript Example:**
```javascript
const eventSource = new EventSource(
  `http://localhost:8080/api/v5/jobs/${jobId}/stream`
);

eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`[${data.event_type}]`, data);

  // Update UI based on event type
  switch(data.event_type) {
    case 'planning:started':
      updateStatus('Analyzing document structure...');
      break;
    case 'job:started':
      updateProgress(data.job_number, data.total_jobs);
      break;
    case 'edit:committed':
      logEdit(data.target, data.action);
      break;
    case 'processing:complete':
      fetchResult(jobId);
      eventSource.close();
      break;
    case 'processing:error':
      showError(data.error);
      eventSource.close();
      break;
  }
};

eventSource.onerror = (error) => {
  console.error('SSE connection error:', error);
  eventSource.close();
};
```

**Python Example (with sseclient):**
```python
from sseclient import SSEClient

messages = SSEClient(f'http://localhost:8080/api/v5/jobs/{job_id}/stream')

for msg in messages:
    if msg.data:
        event = json.loads(msg.data)
        print(f"[{event['event_type']}] {event.get('message', '')}")

        if event['event_type'] == 'processing:complete':
            break
```

**Event Stream Format:**
```
event: message
id: 42
data: {"event_type":"planning:started","timestamp":"2025-01-07T10:30:05Z","message":"Starting document planning"}

event: message
id: 43
data: {"event_type":"page:scanned","page":1,"total_pages":10,"page_type":"title"}
```

**Key Event Types:**

| Event Type | Description |
|------------|-------------|
| `docling:started` | PDF conversion started |
| `docling:complete` | PDF conversion complete |
| `planning:started` | Planning phase started |
| `page:scanned` | Page analyzed (quick scan) |
| `structure:inferred` | Document structure determined |
| `page:summarized` | Page detailed analysis complete |
| `job:created` | Worker job created |
| `planning:complete` | Planning phase complete |
| `job:started` | Worker job started |
| `edit:proposed` | Agent proposed an edit |
| `edit:validated` | Edit passed validation |
| `edit:committed` | Edit written to ledger |
| `job:completed` | Worker job finished |
| `verification:started` | Verification phase started |
| `page:verified` | Page verification complete |
| `verification:complete` | Verification phase complete |
| `recovery:started` | Recovery phase started |
| `recovery:complete` | Recovery phase complete |
| `processing:complete` | Job successfully completed |
| `processing:error` | Job failed |

**Reconnection Support:**

If the connection drops, you can reconnect and resume from the last event:

```javascript
const lastEventId = localStorage.getItem('lastEventId');
const eventSource = new EventSource(
  `http://localhost:8080/api/v5/jobs/${jobId}/stream`,
  { headers: { 'Last-Event-ID': lastEventId } }
);

eventSource.onmessage = (event) => {
  localStorage.setItem('lastEventId', event.lastEventId);
  // ... handle event
};
```

---

### GET /api/v5/jobs/{job_id}/result

Get final processing result (only available when status is `complete` or `failed`).

**Request:**
```http
GET /api/v5/jobs/{job_id}/result HTTP/1.1
```

**cURL Example:**
```bash
curl http://localhost:8080/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000/result | jq
```

**Python Example:**
```python
response = requests.get(
    f"http://localhost:8080/api/v5/jobs/{job_id}/result"
)
result = response.json()

print(f"Success: {result['success']}")
print(f"Pages: {result['total_pages']}")
print(f"Edits: {result['total_edits']}")
print(f"Cost: ${result['total_cost']:.4f}")
print(f"Duration: {result['total_duration_ms']/1000:.1f}s")
```

**Response (200 OK):**
```json
{
  "document_id": "123e4567-e89b-12d3-a456-426614174000",
  "success": true,
  "final_markdown": "# Document Title\n\n## Introduction\n...",
  "total_pages": 10,
  "total_edits": 47,
  "total_cost": 0.0823,
  "total_duration_ms": 45230,
  "verification_passed": true,
  "verification_issues": []
}
```

**Error Responses:**
- `404 Not Found` - Job ID doesn't exist
- `409 Conflict` - Job still processing (not complete/failed yet)

---

### GET /api/v5/jobs/{job_id}/ledger

Get complete change ledger (all edits with reasoning).

**Request:**
```http
GET /api/v5/jobs/{job_id}/ledger HTTP/1.1
```

**cURL Example:**
```bash
curl http://localhost:8080/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000/ledger | jq
```

**Python Example:**
```python
response = requests.get(
    f"http://localhost:8080/api/v5/jobs/{job_id}/ledger"
)
ledger = response.json()

for entry in ledger['entries']:
    print(f"[{entry['action']}] {entry['target']} on page {entry['page']}")
    print(f"  Reasoning: {entry['reasoning']}")
    print(f"  Confidence: {entry['confidence']:.2%}")
```

**Response (200 OK):**
```json
{
  "document_id": "123e4567-e89b-12d3-a456-426614174000",
  "entries": [
    {
      "entry_id": "entry-001",
      "job_id": "job-001",
      "page": 1,
      "timestamp": "2025-01-07T10:30:15Z",
      "action": "alt_text",
      "target": "fig:1",
      "before": "<!-- image 1 -->",
      "after": "![Bar chart showing student enrollment by year from 2020-2025](image1.png)",
      "reasoning": "Figure shows a bar chart with enrollment data. Alt text describes the chart type, data shown, and time range.",
      "confidence": 0.95,
      "validated": true,
      "validation_feedback": null
    },
    {
      "entry_id": "entry-002",
      "job_id": "job-002",
      "page": 3,
      "action": "table_transcription",
      "target": "table:1",
      "before": "<!-- table 1 -->",
      "after": "| Course | Credits | Grade |\n|--------|---------|-------|\n| CS 101 | 3 | A |\n| MATH 220 | 4 | B+ |",
      "reasoning": "Transcribed table with course information including headers.",
      "confidence": 0.98,
      "validated": true,
      "validation_feedback": null
    }
  ],
  "total_entries": 47
}
```

**Error Responses:**
- `404 Not Found` - Job ID doesn't exist
- `409 Conflict` - Job still processing

---

### GET /api/v5/jobs/{job_id}/markdown

Download final markdown as plain text file.

**Request:**
```http
GET /api/v5/jobs/{job_id}/markdown HTTP/1.1
```

**cURL Example:**
```bash
curl http://localhost:8080/api/v5/jobs/123e4567-e89b-12d3-a456-426614174000/markdown \
  -o result.md
```

**Python Example:**
```python
response = requests.get(
    f"http://localhost:8080/api/v5/jobs/{job_id}/markdown"
)
with open("result.md", "wb") as f:
    f.write(response.content)
```

**Response (200 OK):**
```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename="document.md"; filename*=UTF-8''document.md

# Document Title

## Introduction

This is the processed markdown content...
```

**Error Responses:**
- `404 Not Found` - Job ID doesn't exist
- `409 Conflict` - Job still processing

---

## Integration Patterns

### Pattern 1: Poll for Status

Simple polling loop for status updates:

```python
import time
import requests

def wait_for_completion(job_id, poll_interval=2):
    """Poll job status until complete or failed."""
    while True:
        response = requests.get(f"http://localhost:8080/api/v5/jobs/{job_id}")
        status_data = response.json()

        status = status_data["status"]
        progress = status_data["progress"]

        print(f"Status: {status} ({progress*100:.1f}%)")

        if status in ["complete", "failed"]:
            return status

        time.sleep(poll_interval)

# Usage
job_id = submit_document("document.pdf")
final_status = wait_for_completion(job_id)

if final_status == "complete":
    result = requests.get(f"http://localhost:8080/api/v5/jobs/{job_id}/result")
    print(result.json())
```

### Pattern 2: SSE with Async/Await

React/TypeScript example with SSE streaming:

```typescript
async function processDocument(file: File): Promise<ProcessingResult> {
  // 1. Submit document
  const formData = new FormData();
  formData.append('file', file);

  const submitResponse = await fetch('http://localhost:8080/api/v5/process', {
    method: 'POST',
    body: formData,
  });

  const { job_id } = await submitResponse.json();

  // 2. Stream events
  return new Promise((resolve, reject) => {
    const eventSource = new EventSource(
      `http://localhost:8080/api/v5/jobs/${job_id}/stream`
    );

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      // Emit to UI
      onProgressUpdate(data);

      // Check for completion
      if (data.event_type === 'processing:complete') {
        eventSource.close();
        // 3. Fetch final result
        fetch(`http://localhost:8080/api/v5/jobs/${job_id}/result`)
          .then(res => res.json())
          .then(resolve)
          .catch(reject);
      } else if (data.event_type === 'processing:error') {
        eventSource.close();
        reject(new Error(data.error));
      }
    };

    eventSource.onerror = (error) => {
      eventSource.close();
      reject(error);
    };
  });
}
```

### Pattern 3: Background Job with Webhook

For long-running jobs, use webhooks for completion notification:

```python
# Backend service
def submit_with_callback(pdf_path, webhook_url):
    """Submit document and register webhook for completion."""

    # Submit document
    with open(pdf_path, "rb") as f:
        response = requests.post(
            "http://localhost:8080/api/v5/process",
            files={"file": f}
        )
    job_id = response.json()["job_id"]

    # Start background monitoring task
    asyncio.create_task(monitor_and_callback(job_id, webhook_url))

    return job_id

async def monitor_and_callback(job_id, webhook_url):
    """Monitor job and call webhook on completion."""
    while True:
        response = requests.get(f"http://localhost:8080/api/v5/jobs/{job_id}")
        status = response.json()["status"]

        if status in ["complete", "failed"]:
            # Fetch result
            result_response = requests.get(
                f"http://localhost:8080/api/v5/jobs/{job_id}/result"
            )

            # Call webhook
            requests.post(webhook_url, json={
                "job_id": job_id,
                "status": status,
                "result": result_response.json()
            })
            break

        await asyncio.sleep(5)
```

### Pattern 4: Batch Processing

Process multiple documents in parallel:

```python
import asyncio
import aiohttp

async def process_document_async(session, file_path):
    """Process single document asynchronously."""
    # Submit
    with open(file_path, "rb") as f:
        data = aiohttp.FormData()
        data.add_field('file', f, filename=os.path.basename(file_path))

        async with session.post(
            'http://localhost:8080/api/v5/process',
            data=data
        ) as response:
            submit_result = await response.json()
            job_id = submit_result['job_id']

    # Poll for completion
    while True:
        async with session.get(
            f'http://localhost:8080/api/v5/jobs/{job_id}'
        ) as response:
            status_data = await response.json()

            if status_data['status'] in ['complete', 'failed']:
                break

        await asyncio.sleep(2)

    # Fetch result
    async with session.get(
        f'http://localhost:8080/api/v5/jobs/{job_id}/result'
    ) as response:
        return await response.json()

async def batch_process(file_paths, max_concurrent=5):
    """Process multiple documents with concurrency limit."""
    async with aiohttp.ClientSession() as session:
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_with_limit(file_path):
            async with semaphore:
                return await process_document_async(session, file_path)

        tasks = [process_with_limit(fp) for fp in file_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return results

# Usage
file_paths = ["doc1.pdf", "doc2.pdf", "doc3.pdf", "doc4.pdf", "doc5.pdf"]
results = asyncio.run(batch_process(file_paths, max_concurrent=3))

for i, result in enumerate(results):
    if isinstance(result, Exception):
        print(f"Document {i+1} failed: {result}")
    else:
        print(f"Document {i+1} completed: {result['total_edits']} edits")
```

---

## Error Handling

### Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| `400 Bad Request: File must be a PDF` | Wrong file format | Ensure file has .pdf extension |
| `404 Not Found` | Invalid job_id | Verify job_id from submit response |
| `409 Conflict: Job still processing` | Result requested too early | Wait for status=complete/failed |
| `500 Internal Server Error` | Processing failure | Check logs, may be corrupt PDF |
| `SSE connection closed` | Network interruption | Implement reconnection with Last-Event-ID |

### Robust Error Handling Example

```python
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

def create_session_with_retries():
    """Create session with automatic retries."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def process_with_error_handling(pdf_path):
    """Process document with comprehensive error handling."""
    session = create_session_with_retries()

    try:
        # Submit
        with open(pdf_path, "rb") as f:
            response = session.post(
                "http://localhost:8080/api/v5/process",
                files={"file": f},
                timeout=30
            )
        response.raise_for_status()
        job_id = response.json()["job_id"]

        # Wait for completion
        max_attempts = 180  # 6 minutes (2s interval)
        for attempt in range(max_attempts):
            try:
                response = session.get(
                    f"http://localhost:8080/api/v5/jobs/{job_id}",
                    timeout=10
                )
                response.raise_for_status()
                status_data = response.json()

                if status_data["status"] == "complete":
                    # Fetch result
                    result_response = session.get(
                        f"http://localhost:8080/api/v5/jobs/{job_id}/result",
                        timeout=30
                    )
                    result_response.raise_for_status()
                    return result_response.json()

                elif status_data["status"] == "failed":
                    error_msg = status_data.get("error", "Unknown error")
                    raise Exception(f"Processing failed: {error_msg}")

                time.sleep(2)

            except requests.exceptions.RequestException as e:
                print(f"Status check failed (attempt {attempt+1}): {e}")
                if attempt >= 2:  # Retry up to 3 times
                    raise
                time.sleep(5)

        raise TimeoutError("Job did not complete within 6 minutes")

    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Response: {e.response.text}")
        raise
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}")
        raise
    except requests.exceptions.Timeout as e:
        print(f"Timeout error: {e}")
        raise
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise
```

---

## Testing

### Unit Test Example (pytest)

```python
import pytest
import requests
from pathlib import Path

BASE_URL = "http://localhost:8080/api/v5"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

@pytest.fixture
def sample_pdf():
    return FIXTURES_DIR / "sample.pdf"

def test_submit_document(sample_pdf):
    """Test document submission."""
    with open(sample_pdf, "rb") as f:
        response = requests.post(
            f"{BASE_URL}/process",
            files={"file": f}
        )

    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "pending"
    assert "stream_url" in data

def test_get_job_status(sample_pdf):
    """Test job status endpoint."""
    # Submit first
    with open(sample_pdf, "rb") as f:
        submit_response = requests.post(
            f"{BASE_URL}/process",
            files={"file": f}
        )
    job_id = submit_response.json()["job_id"]

    # Get status
    status_response = requests.get(f"{BASE_URL}/jobs/{job_id}")
    assert status_response.status_code == 200

    status_data = status_response.json()
    assert status_data["job_id"] == job_id
    assert status_data["status"] in [
        "pending", "docling", "vision_extraction",
        "planning", "executing", "verifying", "recovering",
        "complete", "failed"
    ]
    assert 0 <= status_data["progress"] <= 1

def test_invalid_file_format():
    """Test submission with non-PDF file."""
    # Create a fake .txt file
    with open("test.txt", "w") as f:
        f.write("This is not a PDF")

    try:
        with open("test.txt", "rb") as f:
            response = requests.post(
                f"{BASE_URL}/process",
                files={"file": ("test.pdf", f)}  # Fake .pdf extension
            )

        assert response.status_code == 400
    finally:
        os.remove("test.txt")

def test_complete_workflow(sample_pdf):
    """Test complete workflow from submit to result."""
    # Submit
    with open(sample_pdf, "rb") as f:
        submit_response = requests.post(
            f"{BASE_URL}/process",
            files={"file": f}
        )
    job_id = submit_response.json()["job_id"]

    # Wait for completion
    max_attempts = 90  # 3 minutes
    for _ in range(max_attempts):
        status_response = requests.get(f"{BASE_URL}/jobs/{job_id}")
        status = status_response.json()["status"]

        if status == "complete":
            break
        elif status == "failed":
            pytest.fail(f"Job failed: {status_response.json().get('error')}")

        time.sleep(2)
    else:
        pytest.fail("Job did not complete in time")

    # Get result
    result_response = requests.get(f"{BASE_URL}/jobs/{job_id}/result")
    assert result_response.status_code == 200

    result = result_response.json()
    assert result["success"] is True
    assert len(result["final_markdown"]) > 0
    assert result["total_pages"] > 0

    # Get ledger
    ledger_response = requests.get(f"{BASE_URL}/jobs/{job_id}/ledger")
    assert ledger_response.status_code == 200

    ledger = ledger_response.json()
    assert len(ledger["entries"]) > 0

    # Get markdown
    markdown_response = requests.get(f"{BASE_URL}/jobs/{job_id}/markdown")
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")
```

---

## Performance Considerations

### Recommended Practices

1. **Use SSE for Real-Time Updates**
   - More efficient than polling
   - Immediate feedback
   - Automatic reconnection support

2. **Implement Timeouts**
   - Set reasonable timeouts for all requests
   - Typical processing: 1-3 minutes per document
   - Longer for large/scanned documents

3. **Handle Reconnections**
   - Store Last-Event-ID for SSE reconnection
   - Implement exponential backoff for retries

4. **Batch Processing**
   - Process multiple documents concurrently
   - Limit concurrency to avoid overload (recommend 3-5)

5. **Cache Results**
   - Job results are immutable once complete
   - Cache final markdown/result to avoid redundant requests

### Rate Limiting

Current implementation has no explicit rate limiting, but consider:
- Max concurrent jobs: depends on server resources
- Recommended: 3-5 concurrent processing jobs
- SSE connections: lightweight, can handle many

---

## Next Steps

- Review [Data Models Reference](./pipeline-data-models.md) for complete schema documentation
- Explore [Phase Documentation](./pipeline-phase-1-planning.md) for implementation details
- Check [Troubleshooting Guide](./pipeline-system-overview.md#troubleshooting) for common issues
