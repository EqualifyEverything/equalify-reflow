# BUG-006: Result File Format Mismatch (HTML/MDX URLs)

**Priority:** MEDIUM
**Severity:** Moderate - API returns broken URLs
**Discovered:** 2025-10-03 (E2E Testing)
**Status:** Open

---

## Problem Statement

The API endpoint `/api/documents/{job_id}/result` returns URLs for HTML and MDX files, but the processing worker only generates Markdown (.md) files. This causes broken links and confuses clients expecting the documented output formats.

**Expected:** MDX files for frontend rendering, MD files for intermediate processing
**Actual:** Only MD files generated, but API returns HTML/MDX URLs

---

## Root Cause Analysis

### Issue 1: API Returns Wrong File Extensions

**Location:** [src/api/documents.py:168-169](src/api/documents.py#L168-169)

```python
return JobResultResponse(
    job_id=job_id,
    status="completed",
    html_url=storage.get_result_url(job_id, "html"),  # ❌ No HTML file created
    mdx_url=storage.get_result_url(job_id, "mdx"),    # ❌ No MDX file created
    confidence_score=float(job_data.get("confidence_score", 0.0)),
    processing_time_seconds=int(job_data.get("processing_time_seconds", 0))
)
```

### Issue 2: Processing Worker Generates MD File Only

**Location:** [src/services/processing_service.py](src/services/processing_service.py) (need to verify exact location)

Processing worker calls `storage.upload_result()` with `.md` extension, but API expects `.mdx` and `.html`.

### Issue 3: Misalignment with Project Requirements

Per project requirements:
- **Docling output**: Markdown (.md) for intermediate processing
- **AI enhancement output**: MDX (.mdx) for frontend rendering with components
- **HTML output**: Not in scope for Phase 2 (Astro generates HTML from MDX)

**The API should return:**
- `markdown_url`: Base Markdown from Docling
- `mdx_url`: Enhanced MDX from AI processing
- ~~`html_url`~~: Remove (not generated in Phase 2)

---

## Evidence

### E2E Test Results

**Job ID:** 002c8222-099c-48d8-9f16-b2f641e5679b

**Redis Job Metadata:**
```json
{
  "metadata": {
    "markdown_url": "http://localstack:4566/equalify-pdf-results/002c8222-099c-48d8-9f16-b2f641e5679b.md",
    "confidence_score": 0.95,
    "processing_time_seconds": 27
  }
}
```

**API Response:**
```json
{
  "job_id": "002c8222-099c-48d8-9f16-b2f641e5679b",
  "status": "completed",
  "html_url": "http://localstack:4566/equalify-pdf-results/002c8222-099c-48d8-9f16-b2f641e5679b.html",
  "mdx_url": "http://localstack:4566/equalify-pdf-results/002c8222-099c-48d8-9f16-b2f641e5679b.mdx",
  "confidence_score": 0.0,  // ❌ Also incorrect - should be 0.95
  "processing_time_seconds": 0  // ❌ Also incorrect - should be 27
}
```

**S3 Bucket Contents:**
```bash
$ awslocal s3 ls s3://equalify-pdf-results/
2025-10-03 15:31:37    4663 002c8222-099c-48d8-9f16-b2f641e5679b.md
```

**Only `.md` file exists** - HTML and MDX URLs return 404.

---

## Impact Assessment

### Functional Impact
- ❌ **Broken links**: Clients get 404 when accessing HTML/MDX URLs
- ❌ **API contract violation**: Documented response doesn't match actual output
- ⚠️ **Metadata loss**: Confidence score and processing time not returned
- ⚠️ **Frontend confusion**: Which URL to use for rendering?

### User Impact
- Demo UI cannot display results (broken URLs)
- External integrations (Canvas LMS) cannot retrieve processed content
- Faculty cannot preview converted documents

### System Impact
- Integration tests fail when checking result URLs
- E2E tests show API/worker misalignment

---

## Dependencies

**Blocking:**
- PRD-007 ✅ (Processing Worker implementation)

**Blocked by:**
- None (can be fixed immediately)

**Related:**
- PRD-009B (Demo UI) - needs correct URLs to display results

---

## Technical Solution

### Solution Overview

**Phase 2 Scope:** Generate and return MD and MDX files
1. Processing worker outputs both `.md` (Docling) and `.mdx` (AI-enhanced)
2. API returns `markdown_url` and `mdx_url`
3. Remove `html_url` from Phase 2 (HTML generation is Phase 3 with Astro)
4. Fix metadata retrieval from Redis

### Implementation Steps

#### Step 1: Update JobResultResponse Model

**File:** [src/api/documents.py](src/api/documents.py)

**Change lines 42-50:**
```python
# BEFORE:
class JobResultResponse(BaseModel):
    """Response for completed job result."""
    job_id: str
    status: str
    html_url: Optional[str] = None
    mdx_url: Optional[str] = None
    confidence_score: Optional[float] = None
    processing_time_seconds: Optional[int] = None
    estimated_completion_at: Optional[str] = None

# AFTER:
class JobResultResponse(BaseModel):
    """Response for completed job result."""
    job_id: str
    status: str
    markdown_url: Optional[str] = None  # Docling Markdown output
    mdx_url: Optional[str] = None       # AI-enhanced MDX output
    confidence_score: Optional[float] = None
    processing_time_seconds: Optional[int] = None
    estimated_completion_at: Optional[str] = None

    # Removed: html_url (Phase 3 - Astro generation)
```

#### Step 2: Fix Result URL Generation in API

**File:** [src/api/documents.py](src/api/documents.py)

**Change lines 163-172:**
```python
# BEFORE:
if job_data["status"] == "completed":
    return JobResultResponse(
        job_id=job_id,
        status="completed",
        html_url=storage.get_result_url(job_id, "html"),
        mdx_url=storage.get_result_url(job_id, "mdx"),
        confidence_score=float(job_data.get("confidence_score", 0.0)),
        processing_time_seconds=int(job_data.get("processing_time_seconds", 0))
    )

# AFTER:
if job_data["status"] == "completed":
    # Parse metadata JSON from Redis
    metadata = {}
    if "metadata" in job_data:
        import json
        metadata = json.loads(job_data["metadata"])

    return JobResultResponse(
        job_id=job_id,
        status="completed",
        markdown_url=metadata.get("markdown_url") or storage.get_result_url(job_id, "md"),
        mdx_url=metadata.get("mdx_url") or storage.get_result_url(job_id, "mdx"),
        confidence_score=metadata.get("confidence_score", 0.0),
        processing_time_seconds=metadata.get("processing_time_seconds", 0)
    )
```

#### Step 3: Update Processing Worker to Generate MDX

**File:** [src/services/processing_service.py](src/services/processing_service.py) (need to verify exact location)

**Current:** Worker generates only MD file
**Updated:** Worker generates both MD (Docling output) and MDX (AI-enhanced output)

```python
# After Docling PDF → Markdown conversion
markdown_content = await self.pdf_converter.convert_to_markdown(pdf_path)

# Upload Markdown (Docling output)
markdown_url = await self.storage.upload_result(
    job_id=job_id,
    content=markdown_content,
    format="md"
)

# Apply AI enhancement to generate MDX
mdx_content = await self.ai_enhancement.enhance_accessibility(markdown_content)

# Upload MDX (AI-enhanced output)
mdx_url = await self.storage.upload_result(
    job_id=job_id,
    content=mdx_content,
    format="mdx"
)

# Store both URLs in metadata
metadata = {
    "markdown_url": markdown_url,
    "mdx_url": mdx_url,
    "confidence_score": confidence_score,
    "processing_time_seconds": processing_time,
    "total_pages": page_count
}
```

#### Step 4: Update Job Metadata Storage

**File:** [src/services/job_service.py](src/services/job_service.py)

Ensure metadata is stored as JSON string in Redis:

```python
async def update_job_metadata(
    self,
    job_id: str,
    metadata: dict
) -> None:
    """Update job metadata in Redis.

    Args:
        job_id: Job identifier
        metadata: Metadata dictionary (will be JSON-serialized)
    """
    import json
    await self.redis.hset(
        f"eq-pdf:job:{job_id}",
        "metadata",
        json.dumps(metadata)
    )
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] API returns `markdown_url` and `mdx_url` (not `html_url`)
- [ ] Processing worker generates both `.md` and `.mdx` files
- [ ] Markdown file contains Docling output
- [ ] MDX file contains AI-enhanced output with component markers
- [ ] Confidence score correctly retrieved from Redis metadata
- [ ] Processing time correctly retrieved from Redis metadata
- [ ] S3 bucket contains both `.md` and `.mdx` files after processing

### Verification Tests

#### Test 1: API Response Format
```python
async def test_result_response_format():
    """Verify API returns correct file URLs."""
    # Submit and process job
    job_id = await submit_test_job()
    await wait_for_completion(job_id)

    # Get result
    response = await client.get(f"/api/documents/{job_id}/result")
    data = response.json()

    # Verify response structure
    assert "markdown_url" in data
    assert "mdx_url" in data
    assert "html_url" not in data  # Removed in Phase 2
    assert data["markdown_url"].endswith(".md")
    assert data["mdx_url"].endswith(".mdx")
```

#### Test 2: Files Exist in S3
```python
async def test_result_files_exist():
    """Verify both MD and MDX files created in S3."""
    job_id = await submit_test_job()
    await wait_for_completion(job_id)

    # Check S3 bucket
    s3_client = boto3.client("s3", endpoint_url=settings.aws_endpoint_url)

    # MD file should exist
    md_response = s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"{job_id}.md"
    )
    assert md_response["ContentType"] == "text/markdown"

    # MDX file should exist
    mdx_response = s3_client.get_object(
        Bucket=settings.s3_results_bucket,
        Key=f"{job_id}.mdx"
    )
    assert mdx_response["ContentType"] == "text/markdown"
```

#### Test 3: Metadata Retrieval
```python
async def test_metadata_in_response():
    """Verify confidence score and processing time returned."""
    job_id = await submit_test_job()
    await wait_for_completion(job_id)

    # Get result
    response = await client.get(f"/api/documents/{job_id}/result")
    data = response.json()

    # Verify metadata present
    assert data["confidence_score"] > 0.0, "Confidence score should be set"
    assert data["processing_time_seconds"] > 0, "Processing time should be set"
```

#### Test 4: MDX Contains Component Markers
```python
async def test_mdx_has_component_markers():
    """Verify MDX file contains Astro/React component markers."""
    job_id = await submit_test_job()
    await wait_for_completion(job_id)

    # Download MDX file
    response = await client.get(f"/api/documents/{job_id}/result")
    mdx_url = response.json()["mdx_url"]
    mdx_content = await download_s3_file(mdx_url)

    # Check for component markers (if AI enhancement adds them)
    # Example: <AccessibleImage src="..." alt="..." />
    # This depends on AI enhancement implementation
    assert len(mdx_content) > 0, "MDX file should not be empty"
```

#### Test 5: URL Accessibility
```python
async def test_result_urls_accessible():
    """Verify result URLs return 200 OK."""
    job_id = await submit_test_job()
    await wait_for_completion(job_id)

    # Get URLs
    response = await client.get(f"/api/documents/{job_id}/result")
    markdown_url = response.json()["markdown_url"]
    mdx_url = response.json()["mdx_url"]

    # Test accessibility
    md_response = requests.get(markdown_url)
    assert md_response.status_code == 200

    mdx_response = requests.get(mdx_url)
    assert mdx_response.status_code == 200
```

---

## Testing Strategy

### Unit Tests
**Location:** `tests/api/test_documents.py` (update existing)

1. Test `JobResultResponse` model excludes `html_url`
2. Test metadata JSON parsing
3. Mock Redis to verify correct field retrieval

### Integration Tests
**Location:** `tests/integration/test_result_generation.py` (new file)

1. Submit test job
2. Wait for processing completion
3. Verify both MD and MDX files in S3
4. Verify API returns correct URLs
5. Verify URLs are accessible

### E2E Tests
**Location:** `tests/integration/test_full_pipeline.py` (update existing)

Update existing E2E test to verify:
- Result response contains `markdown_url` and `mdx_url`
- Both URLs return 200 OK
- Metadata (confidence, processing time) is accurate

---

## Edge Cases

### Case 1: AI Enhancement Fails

**Scenario:** Docling succeeds, but AI enhancement fails

**Current behavior:** Entire job fails
**Improved behavior:** Fall back to MD-only output

```python
try:
    mdx_content = await ai_enhancement.enhance(markdown_content)
    mdx_url = await storage.upload_result(job_id, mdx_content, "mdx")
except Exception as e:
    logger.warning(f"AI enhancement failed for {job_id}, using MD only: {e}")
    mdx_url = None  # MDX unavailable

metadata = {
    "markdown_url": markdown_url,
    "mdx_url": mdx_url,  # May be None
    "ai_enhancement_status": "success" if mdx_url else "failed"
}
```

### Case 2: Large Documents

**Issue:** Storing both MD and MDX doubles storage

**Mitigation:**
- S3 lifecycle policy: Delete temp files after 7 days
- Compression: gzip markdown files before upload
- Cost analysis: $0.023/GB/month = negligible for text files

### Case 3: MDX vs MD Content Differences

**Question:** What if MD and MDX content diverge significantly?

**Solution:** Store diff metrics in metadata
```python
metadata = {
    "markdown_url": markdown_url,
    "mdx_url": mdx_url,
    "enhancement_changes": {
        "alt_texts_added": 5,
        "headings_restructured": 2,
        "math_equations_converted": 3
    }
}
```

---

## Migration Plan

### Backward Compatibility

**API Version:** Maintain v1 API with both formats temporarily

```python
class JobResultResponse(BaseModel):
    # ... existing fields ...

    # Phase 2: New fields
    markdown_url: Optional[str] = None
    mdx_url: Optional[str] = None

    # Deprecated: Remove in v2 API
    html_url: Optional[str] = Field(
        None,
        deprecated=True,
        description="Deprecated: HTML generation moved to Phase 3. Use mdx_url instead."
    )
```

### Existing Jobs

**Issue:** Jobs processed before fix only have `.md` files

**Solution:** Return `.md` URL for both fields if `.mdx` missing
```python
if mdx_url is None:
    logger.warning(f"Job {job_id} missing MDX file, using MD for both URLs")
    mdx_url = markdown_url  # Fallback to MD
```

---

## Performance Implications

### Storage Impact
- **Before:** 1 file per job (.md)
- **After:** 2 files per job (.md + .mdx)
- **Size increase:** ~10% (MDX adds component markers)
- **Cost:** Negligible ($0.023/GB/month on S3)

### Processing Time Impact
- **MD generation:** ~3-4 seconds (Docling)
- **MDX generation:** ~2-3 seconds (AI enhancement)
- **Total:** ~5-7 seconds (parallel processing possible)

### Network Impact
- Clients now request MDX instead of MD
- File size difference: <10%
- No significant bandwidth impact

---

## Documentation Updates

### API Documentation

**File:** [README.md](README.md) or API docs

Update `/api/documents/{job_id}/result` endpoint documentation:

```markdown
## GET /api/documents/{job_id}/result

Returns result URLs and metadata for a completed job.

**Response Fields:**
- `markdown_url`: Docling-generated Markdown (base conversion)
- `mdx_url`: AI-enhanced MDX with accessibility improvements
- ~~`html_url`~~: *Removed in Phase 2. HTML generation via Astro in Phase 3.*
- `confidence_score`: AI confidence in accessibility enhancements (0.0-1.0)
- `processing_time_seconds`: Total processing duration

**Usage:**
- Use `mdx_url` for frontend rendering with React/Astro components
- Use `markdown_url` for raw text extraction or debugging
```

### Integration Guide

**File:** [docs/integration-guide.md](docs/integration-guide.md) (new file)

```markdown
## Consuming Processing Results

### Recommended Approach
Use `mdx_url` for rendering converted documents:

```typescript
const response = await fetch(`/api/documents/${jobId}/result`);
const { mdx_url, confidence_score } = await response.json();

// Fetch MDX content
const mdxContent = await fetch(mdx_url).then(r => r.text());

// Render with Astro/React
<MDXRenderer content={mdxContent} />
```

### Fallback Strategy
If MDX generation failed, use `markdown_url`:

```typescript
const mdxUrl = result.mdx_url || result.markdown_url;
```
```

---

## Rollback Plan

If MDX generation causes issues:

1. **Immediate:** Disable AI enhancement, generate MD only
2. **Short-term:** Return `markdown_url` for both fields
3. **Long-term:** Fix AI enhancement and re-enable MDX generation

**Rollback configuration:**
```python
# settings
ENABLE_AI_ENHANCEMENT = False  # Disable MDX generation temporarily

# processing_service.py
if settings.ENABLE_AI_ENHANCEMENT:
    mdx_content = await ai_enhancement.enhance(markdown_content)
    mdx_url = await storage.upload_result(job_id, mdx_content, "mdx")
else:
    mdx_url = markdown_url  # Use MD for both
```

---

## Definition of Done

- [ ] `JobResultResponse` model updated (remove `html_url`, add `markdown_url`)
- [ ] API retrieves metadata from Redis correctly
- [ ] Processing worker generates both .md and .mdx files
- [ ] S3 bucket contains both files after processing
- [ ] Confidence score and processing time returned in API
- [ ] Unit tests pass for response model
- [ ] Integration tests pass for file generation
- [ ] E2E test shows correct URLs and accessible files
- [ ] API documentation updated
- [ ] Integration guide created
- [ ] Code review completed
- [ ] PR merged to main branch
