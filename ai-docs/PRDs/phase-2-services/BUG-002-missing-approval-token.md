# BUG-002: Missing Approval Token in Status Response

## Problem Statement

The `/api/documents/{job_id}/status` endpoint does not return the `approval_token` field when a job is in `awaiting_approval` status, despite the token being correctly stored in Redis. This forces API clients to manually query Redis to retrieve the token needed for approval API calls.

### Bug: approval_token Not Returned in API Response

**Issue:** When a document is flagged for PII approval, the status endpoint does not include the `approval_token` in the response, preventing clients from making approval API calls.

**Expected Behavior:**
```json
{
  "job_id": "38b154dc-d761-4f5f-8dd2-57a202fa17f1",
  "status": "awaiting_approval",
  "created_at": "2025-10-01T20:30:22Z",
  "updated_at": "2025-10-01T20:31:51Z",
  "pii_findings": [
    {
      "entity_type": "EMAIL_ADDRESS",
      "text": "kyungsikhan@hanyang.ac.kr",
      "score": 1.0
    }
  ],
  "approval_token": "_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM"
}
```

**Actual Behavior (Before Fix):**
```json
{
  "job_id": "38b154dc-d761-4f5f-8dd2-57a202fa17f1",
  "status": "awaiting_approval",
  "created_at": "2025-10-01T20:30:22Z",
  "updated_at": "2025-10-01T20:31:51Z",
  "pii_findings": [...],
  "approval_url": null
}
```

Note: The response model had `approval_url` field but no `approval_token` field.

**Current Workaround:**
Users must manually query Redis to retrieve the token:
```bash
docker exec equalify-pdf-redis redis-cli HGET eq-pdf:job:{job_id} approval_token
# Returns: _JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM
```

**Impact:**
- **Severity:** High
- **Priority:** High
- API clients cannot complete approval workflow through documented API
- Breaks secure token-based authentication pattern
- Requires direct Redis access (security/architecture violation)
- Blocks Equalify Platform and Canvas LMS integrations

## Root Cause Analysis

### Problem: Response Model Missing approval_token Field

**File:** `src/api/documents.py` (lines 32-39)

The `JobStatusResponse` model defines `approval_url` but not `approval_token`:

```python
class JobStatusResponse(BaseModel):
    """Response for job status."""
    job_id: str
    status: str
    created_at: str
    updated_at: str
    pii_findings: Optional[list[PIIFinding]] = None
    approval_url: Optional[str] = None  # ❌ Wrong field - frontend URL
    # approval_token: Optional[str] = None  # ✗ Missing - needed for API calls
```

The endpoint logic (lines 126-131) checks for the wrong Redis field:

```python
if job_data["status"] == "awaiting_approval" and "pii_findings" in job_data:
    response.pii_findings = [
        PIIFinding(**finding) for finding in job_data["pii_findings"]
    ]
    if "approval_url" in job_data:  # ❌ Checks for URL (doesn't exist in Redis)
        response.approval_url = job_data["approval_url"]
```

**Evidence from Redis:**
```bash
$ docker exec equalify-pdf-redis redis-cli HGETALL eq-pdf:job:38b154dc-d761-4f5f-8dd2-57a202fa17f1

approval_token
_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM  ← Token EXISTS in Redis
approval_expires_at
2025-10-01T23:31:51.015758+00:00
job_id
38b154dc-d761-4f5f-8dd2-57a202fa17f1
status
awaiting_approval
pii_findings
[{"entity_type": "EMAIL_ADDRESS", ...}]
# ✗ No approval_url field exists (and shouldn't - that's a frontend concern)
```

### Design Confusion: Token vs. URL

The original bug report confused two separate concerns:

1. **`approval_token`** (API authentication) - **Required for API clients**
   - 256-bit secure token for approval API calls
   - Stored in Redis by PII service
   - Used directly by API: `/api/approval/{token}/approve`

2. **`approval_url`** (Frontend convenience) - **Optional for Phase 3**
   - Full URL for frontend approval interface
   - Format: `http://localhost:3000/approve/{token}`
   - Only needed when standalone approval emails are sent (Phase 3)

**The real bug:** API clients need the **token**, not the URL. The URL can be constructed client-side if needed.

## Solution Implemented

### Fix: Return approval_token Instead of approval_url

**File:** `src/api/documents.py`

**Change 1 - Update response model (line 39):**

```python
# OLD:
class JobStatusResponse(BaseModel):
    """Response for job status."""
    job_id: str
    status: str
    created_at: str
    updated_at: str
    pii_findings: Optional[list[PIIFinding]] = None
    approval_url: Optional[str] = None  # ❌ Frontend concern

# NEW:
class JobStatusResponse(BaseModel):
    """Response for job status."""
    job_id: str
    status: str
    created_at: str
    updated_at: str
    pii_findings: Optional[list[PIIFinding]] = None
    approval_token: Optional[str] = None  # ✓ API authentication token
```

**Change 2 - Update endpoint logic (lines 130-131):**

```python
# OLD:
if job_data["status"] == "awaiting_approval" and "pii_findings" in job_data:
    response.pii_findings = [
        PIIFinding(**finding) for finding in job_data["pii_findings"]
    ]
    if "approval_url" in job_data:  # ❌ Checks wrong field
        response.approval_url = job_data["approval_url"]

# NEW:
if job_data["status"] == "awaiting_approval" and "pii_findings" in job_data:
    response.pii_findings = [
        PIIFinding(**finding) for finding in job_data["pii_findings"]
    ]
    if "approval_token" in job_data:  # ✓ Returns token from Redis
        response.approval_token = job_data["approval_token"]
```

## Verification

### Test 1: Token Returned in Status Response ✅

```bash
$ curl -s http://localhost:8000/api/documents/38b154dc-d761-4f5f-8dd2-57a202fa17f1/status | \
  python3 -c "import sys, json; data = json.load(sys.stdin); \
  print(f'Status: {data[\"status\"]}'); \
  print(f'Has PII: {data[\"pii_findings\"] is not None}'); \
  print(f'Approval token: {data.get(\"approval_token\", \"NOT PRESENT\")}')"

Status: awaiting_approval
Has PII: True
Approval token: _JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM
```

### Test 2: Token Works with Review Endpoint ✅

```bash
$ curl -s http://localhost:8000/api/approval/review/_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM | \
  jq '{job_id, status, pii_count: (.pii_findings | length)}'

{
  "job_id": "38b154dc-d761-4f5f-8dd2-57a202fa17f1",
  "status": "awaiting_approval",
  "pii_count": 443
}
```

### Test 3: Token Works with Approval Endpoint ✅

```bash
$ curl -X POST http://localhost:8000/api/approval/_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM/approve \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","reviewed_by":"test@example.com","justification":"Testing"}' | \
  jq '.'

{
  "message": "Job approved for processing successfully",
  "job_id": "38b154dc-d761-4f5f-8dd2-57a202fa17f1",
  "decision": "approved"
}
```

## Secure API Handshake (Now Working)

The intended workflow now works without Redis access:

```bash
# Step 1: Submit document
curl -X POST http://localhost:8000/api/documents/submit -F "file=@doc.pdf"
# Returns: {"job_id": "abc-123", "status": "pii_scanning"}

# Step 2: Get status and token
curl http://localhost:8000/api/documents/abc-123/status
# Returns: {
#   "status": "awaiting_approval",
#   "approval_token": "_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM"
# }

# Step 3: Review PII findings (token = authentication)
curl http://localhost:8000/api/approval/review/_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM
# Returns: {"job_id": "abc-123", "pii_findings": [...]}

# Step 4: Approve (token = authentication)
curl -X POST http://localhost:8000/api/approval/_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM/approve \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","reviewed_by":"faculty@uic.edu"}'
# Returns: {"message": "Job approved for processing successfully"}
```

## Files Modified

### File: `src/api/documents.py`

**Line 39:** Changed response model field from `approval_url` to `approval_token`
**Lines 130-131:** Changed logic to return `approval_token` from Redis

## Acceptance Criteria

### Functional Requirements ✅

- ✅ **Status endpoint returns approval_token** when job status is `awaiting_approval`
- ✅ **Token works with approval endpoints**:
  - `/api/approval/review/{token}` returns job details
  - `/api/approval/{token}/approve` processes approval decision
- ✅ **Token is null** for jobs not requiring approval
- ✅ **No Redis access required** by API clients

### Technical Requirements ✅

- ✅ No breaking changes (approval_url was unused)
- ✅ No Redis schema changes required
- ✅ No changes to PII service or worker code
- ✅ Backwards compatible with existing tokens
- ✅ Secure token-based authentication preserved

## Future: approval_url Field (Phase 3)

If/when a frontend approval interface is needed (Phase 3), we can **add** `approval_url` alongside `approval_token`:

```json
{
  "status": "awaiting_approval",
  "approval_token": "_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM",
  "approval_url": "http://localhost:3000/approve/_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM"
}
```

This would require:
1. Adding `frontend_base_url` to config
2. Importing `create_approval_url` utility
3. Generating URL from token in status endpoint
4. Adding `approval_url` field back to response model (alongside `approval_token`)

But for Phase 2 (API-only integrations), **only the token is needed**.

## Related Issues

- **PRD-004:** API Endpoints specification
- **PRD-005:** PII Detection Worker (generates tokens)
- **PRD-006:** Approval Workflow API (consumes tokens)
- **BUG-001:** API Integration Fixes (routing issues)

## Priority

**HIGH** - This bug blocks all API-based approval workflows. Required for:
- ✅ Equalify Platform integration (API client needs token)
- ✅ Canvas LMS integration (API client needs token)
- ✅ Webhook integrations (must include token in payload)

## References

### Code References
- [src/api/documents.py:32-39](src/api/documents.py#L32-L39) - Response model
- [src/api/documents.py:126-131](src/api/documents.py#L126-L131) - Status endpoint logic
- [src/services/pii_service.py:127-149](src/services/pii_service.py#L127-L149) - Token generation
- [src/api/approval.py](src/api/approval.py) - Approval endpoints that consume tokens

### Test Evidence
```bash
# Before fix: approval_url field exists but is null
$ curl http://localhost:8000/api/documents/{job_id}/status | jq .
{
  "status": "awaiting_approval",
  "approval_url": null  # ❌ Wrong field, always null
}

# After fix: approval_token field exists and contains token
$ curl http://localhost:8000/api/documents/{job_id}/status | jq .
{
  "status": "awaiting_approval",
  "approval_token": "_JndzZOoRYI2tv14tDHCaZEEegmavbwT0ytzADQqJPM"  # ✅ Correct
}
```

## Status

**RESOLVED** - 2025-10-01

The bug is fixed. API clients can now retrieve the `approval_token` from the status endpoint and use it for approval API calls without requiring direct Redis access.
