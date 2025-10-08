# BUG-001: API Integration Fixes

## Problem Statement

Multiple critical bugs exist in the API integration layer causing 404 errors and service method mismatches:

### Bug 1: Incorrect Approval API Route Paths
**Issue:** The approval API routes are registered with `/api` prefix in the router, but the endpoints use paths that don't match the documented API specification.

**Actual Routes:**
- GET `/api/review/{token}` (should be `/api/approval/review/{token}`)
- POST `/api/approve/{token}` (should be `/api/approval/{token}/approve`)

**Error:** When clients call documented endpoints, they receive 404 errors because the routes don't exist.

**Evidence:**
```python
# src/api/approval.py:18
router = APIRouter(prefix="/api", tags=["Approval"])

# Lines 87-91
@router.get(
    "/review/{token}",
    response_model=ReviewDetailsResponse,
    ...
)

# Lines 165-170
@router.post(
    "/approve/{token}",
    response_model=ApprovalResponse,
    ...
)
```

### Bug 2: Non-existent StorageService Method Calls in ProcessingService
**Issue:** `ProcessingService` calls `storage.upload_to_s3()` which doesn't exist in `StorageService`.

**Actual Method:** `upload_result()` (lines 140-185 of storage_service.py)
**Called Method:** `upload_to_s3()` (line 148 of processing_service.py)

**Error:** AttributeError when processing service attempts to upload results.

**Evidence:**
```python
# src/services/processing_service.py:148-153
await self.storage.upload_to_s3(
    bucket=settings.s3_results_bucket,
    key=result_key,
    content=final_markdown.encode("utf-8"),
    content_type="text/markdown",
)

# But StorageService only has:
# src/services/storage_service.py:140-145
async def upload_result(
    self,
    job_id: str,
    content: str,
    format: str
) -> str:
```

### Bug 3: Non-existent JobService Method Calls in ProcessingService
**Issue:** `ProcessingService` calls `job.mark_job_failed()` which doesn't exist in `JobService`.

**Actual Method:** `update_job_status()` (lines 73-102 of job_service.py)
**Called Method:** `mark_job_failed()` (lines 125, 193 of processing_service.py)

**Error:** AttributeError when processing fails and service tries to update job status.

**Evidence:**
```python
# src/services/processing_service.py:125
await self.job.mark_job_failed(job.job_id, error_msg)

# src/services/processing_service.py:193
await self.job.mark_job_failed(job.job_id, error_msg)

# But JobService only has:
# src/services/job_service.py:73-102
async def update_job_status(
    self,
    job_id: str,
    status: str,
    **additional_fields
) -> None:
```

### Bug 4: Documents Status Endpoint Path Inconsistency
**Issue:** The documents status endpoint is registered at `/{job_id}/status` but based on the prefix and typical REST patterns, it should be `/status/{job_id}` to match the approval pattern.

**Current:** GET `/api/documents/{job_id}/status`
**Expected (based on PRD):** GET `/api/documents/status/{job_id}`

**Evidence:**
```python
# src/api/documents.py:92-96
@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    job_service: JobService = Depends(get_job_service)
):
```

## Root Cause Analysis

### Bug 1: Route Registration Issue
The `APIRouter` prefix is set to `/api` but the route decorators don't include the full path structure. When the router is included in main.py, FastAPI combines the prefix with the route path, resulting in incorrect final paths.

**Fix Approach:** Change router prefix to `/api/approval` or adjust individual route paths.

### Bug 2 & 3: Service API Mismatch
`ProcessingService` was implemented calling methods that were planned but never implemented in the service layer. The actual service methods have different signatures and names.

**Root Cause:** Missing synchronization between service interface design and implementation. The processing service was written assuming certain method names that don't match the actual implementations.

### Bug 4: Route Path Inconsistency
The documents router uses resource-first path pattern `/{job_id}/status` while the approval router uses action-first pattern `/review/{token}`. This inconsistency makes the API harder to understand and use.

**Root Cause:** Different developers or implementation phases used different REST conventions.

## Proposed Fixes

### Fix 1: Correct Approval API Route Paths

**File:** `/Users/dylanisaac/Projects/equalify-pdf-converter/src/api/approval.py`

**Change 1 - Update Router Prefix:**
```python
# OLD (line 18):
router = APIRouter(prefix="/api", tags=["Approval"])

# NEW:
router = APIRouter(prefix="/api/approval", tags=["Approval"])
```

**Change 2 - Update GET route path:**
```python
# OLD (line 88):
    "/review/{token}",

# NEW:
    "/review/{token}",
# No change needed - this becomes /api/approval/review/{token}
```

**Change 3 - Update POST route path:**
```python
# OLD (line 166):
    "/approve/{token}",

# NEW:
    "/{token}/approve",
# This becomes /api/approval/{token}/approve
```

**Verification:**
```bash
make dev
curl -X GET http://localhost:8080/api/approval/review/test-token
# Should return 404 with proper error message, not route not found

curl -X POST http://localhost:8080/api/approval/test-token/approve \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","justification":"Test reason","reviewed_by":"test@example.com"}'
# Should return 404 with proper error message, not route not found
```

### Fix 2: Correct StorageService Method Call in ProcessingService

**File:** `/Users/dylanisaac/Projects/equalify-pdf-converter/src/services/processing_service.py`

**Change - Replace upload_to_s3 with upload_result:**
```python
# OLD (lines 143-153):
# Step 7: Upload results to S3 with versioning
timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
result_key = f"results/{job.job_id}/v{timestamp}/output.md"

logger.info(f"Uploading markdown to s3://{settings.s3_results_bucket}/{result_key}")
await self.storage.upload_to_s3(
    bucket=settings.s3_results_bucket,
    key=result_key,
    content=final_markdown.encode("utf-8"),
    content_type="text/markdown",
)

# NEW:
# Step 7: Upload results to S3 with versioning
logger.info(f"Uploading markdown result for job {job.job_id}")
result_url = await self.storage.upload_result(
    job_id=job.job_id,
    content=final_markdown,
    format="mdx"
)
logger.info(f"Markdown uploaded to {result_url}")
```

**Change - Update metadata storage:**
```python
# OLD (lines 158-169):
await self.job.update_job_status(
    job.job_id,
    "completed",
    metadata={
        "markdown_url": f"s3://{settings.s3_results_bucket}/{result_key}",
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "processing_time_seconds": processing_time,
        "total_pages": conversion_result.total_pages,
        "version": f"v{timestamp}",
    },
)

# NEW:
await self.job.update_job_status(
    job.job_id,
    "completed",
    confidence_score=confidence_score,
    confidence_level=confidence_level,
    processing_time_seconds=processing_time,
    total_pages=conversion_result.total_pages,
    mdx_url=result_url,
)
```

**Change - Update return value:**
```python
# OLD (lines 176-182):
return ProcessingResult(
    job_id=job.job_id,
    markdown_url=f"s3://{settings.s3_results_bucket}/{result_key}",
    confidence_score=confidence_score,
    processing_time_seconds=processing_time,
    error_message=None,
)

# NEW:
return ProcessingResult(
    job_id=job.job_id,
    markdown_url=result_url,
    confidence_score=confidence_score,
    processing_time_seconds=processing_time,
    error_message=None,
)
```

**Verification:**
```bash
# After fix, submit a document and verify it processes successfully
curl -X POST http://localhost:8080/api/documents/submit \
  -F "file=@test.pdf"
# Note the job_id

# Wait for processing, then check result
curl http://localhost:8080/api/documents/{job_id}/result
# Should show completed status with mdx_url
```

### Fix 3: Correct JobService Method Calls in ProcessingService

**File:** `/Users/dylanisaac/Projects/equalify-pdf-converter/src/services/processing_service.py`

**Change 1 - First mark_job_failed call (line 125):**
```python
# OLD:
await self.job.mark_job_failed(job.job_id, error_msg)

# NEW:
await self.job.update_job_status(
    job.job_id,
    "failed",
    error=error_msg
)
```

**Change 2 - Second mark_job_failed call (line 193):**
```python
# OLD:
await self.job.mark_job_failed(job.job_id, error_msg)

# NEW:
await self.job.update_job_status(
    job.job_id,
    "failed",
    error=error_msg
)
```

**Verification:**
```bash
# Submit a document that will fail (e.g., corrupted PDF)
curl -X POST http://localhost:8080/api/documents/submit \
  -F "file=@corrupted.pdf"

# Check job status should show failed with error message
curl http://localhost:8080/api/documents/{job_id}/status
# Should return: {"status": "failed", "error": "..."}
```

### Fix 4: Make Documents Status Endpoint Consistent (Optional)

**File:** `/Users/dylanisaac/Projects/equalify-pdf-converter/src/api/documents.py`

**Note:** This is a breaking change. Only implement if willing to update clients.

**Change - Update endpoint path:**
```python
# OLD (line 92):
@router.get("/{job_id}/status", response_model=JobStatusResponse)

# NEW:
@router.get("/status/{job_id}", response_model=JobStatusResponse)
```

**Verification:**
```bash
# Submit document
curl -X POST http://localhost:8080/api/documents/submit -F "file=@test.pdf"
# Returns: {"job_id": "abc-123", ...}

# Check status with new path
curl http://localhost:8080/api/documents/status/abc-123
# Should return job status
```

**Alternative:** Keep current path and document it clearly. Update approval routes to match this pattern instead:
- GET `/api/approval/{token}/review`
- POST `/api/approval/{token}/approve`

## Acceptance Criteria

- [x] Approval endpoints accessible at documented paths:
  - GET `/api/approval/review/{token}` returns 404 with valid error (not route not found)
  - POST `/api/approval/{token}/approve` returns 404 with valid error (not route not found)

- [x] StorageService method calls in ProcessingService use correct method names:
  - `upload_result()` instead of `upload_to_s3()`
  - Method signature matches actual implementation

- [x] JobService method calls in ProcessingService use correct method names:
  - `update_job_status(job_id, "failed", error=msg)` instead of `mark_job_failed()`

- [x] Processing service successfully:
  - Downloads PDFs from temp storage
  - Processes documents with AI
  - Uploads results to results bucket
  - Updates job status correctly on success and failure

- [x] Integration tests pass for complete workflow:
  - Submit document → PII scan → Processing → Results available
  - Submit document → PII scan → Requires approval → Approve → Processing → Results
  - Submit document → PII scan → Requires approval → Deny → Cleanup complete

## Files to Modify

1. `/Users/dylanisaac/Projects/equalify-pdf-converter/src/api/approval.py`
   - Update router prefix to `/api/approval`
   - Update POST route path to `/{token}/approve`

2. `/Users/dylanisaac/Projects/equalify-pdf-converter/src/services/processing_service.py`
   - Replace `storage.upload_to_s3()` with `storage.upload_result()`
   - Replace two instances of `job.mark_job_failed()` with `job.update_job_status()`
   - Update metadata storage to use kwargs instead of metadata dict

3. *(Optional)* `/Users/dylanisaac/Projects/equalify-pdf-converter/src/api/documents.py`
   - Update status endpoint path from `/{job_id}/status` to `/status/{job_id}`
   - Only if committing to this breaking change

## Testing Plan

### Unit Tests
1. Test approval route path resolution
2. Test processing service with mocked storage service
3. Test processing service error handling

### Integration Tests
1. Submit document → verify PII scanning works
2. Submit document with PII → verify approval workflow
3. Approve document → verify processing completes
4. Deny document → verify cleanup occurs
5. Processing failure → verify error status recorded

### Manual Testing
```bash
# Start services
make dev

# Test 1: Submit and process clean document
curl -X POST http://localhost:8080/api/documents/submit -F "file=@clean.pdf"
# Wait, check status, verify completion

# Test 2: Submit document requiring approval
curl -X POST http://localhost:8080/api/documents/submit -F "file=@pii.pdf"
# Check status, get approval URL
curl http://localhost:8080/api/approval/review/{token}
curl -X POST http://localhost:8080/api/approval/{token}/approve \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","justification":"Valid reason","reviewed_by":"test@uic.edu"}'
# Verify processing completes

# Test 3: Verify error handling
# Submit corrupted PDF, verify error status propagates correctly
```

## Implementation Notes

1. **Order of Implementation:** Fix bugs in this order to avoid cascading issues:
   - Fix 3 (JobService calls) - enables error handling
   - Fix 2 (StorageService calls) - enables successful processing
   - Fix 1 (API routes) - enables external access
   - Fix 4 (Optional consistency) - last if doing at all

2. **Backward Compatibility:** Fixes 1, 2, 3 are not breaking changes if no clients are currently calling the APIs (since they're broken). Fix 4 is a breaking change.

3. **Documentation Updates:** After fixes, update:
   - API documentation in `/docs`
   - Integration examples
   - Client SDK (if exists)
   - Postman collection (if exists)

4. **Monitoring:** After deployment, monitor:
   - 404 rates on approval endpoints (should drop to zero except invalid tokens)
   - Processing success rates (should increase)
   - Error logs for AttributeError (should disappear completely)

## Related Issues

- PRD-006: Approval Workflow API (implementation complete but has bugs)
- This bug report should be resolved before marking PRD-006 as fully operational

## Priority

**HIGH** - These bugs prevent the approval workflow from functioning entirely. All three fixes are required for the feature to work.
