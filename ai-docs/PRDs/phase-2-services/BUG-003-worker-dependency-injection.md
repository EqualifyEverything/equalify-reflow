# BUG-003: Worker Dependency Injection Failures

**Priority:** HIGH
**Severity:** Critical - Blocks worker initialization
**Discovered:** 2025-10-03 (E2E Testing)
**Status:** Open

---

## Problem Statement

Background workers (PII, Processing, Timeout) are incorrectly calling dependency injection functions with `None` parameters, causing service initialization to fail. This prevents workers from properly accessing Redis and S3 resources.

---

## Root Cause Analysis

### Issue 1: Incorrect Dependency Function Usage in Workers

**Location:** [src/workers/timeout_worker.py:280-282](src/workers/timeout_worker.py#L280-282)

Workers are calling async dependency functions directly with `None` parameters instead of using the async generator pattern required by FastAPI dependencies:

```python
# INCORRECT (current code):
storage_service = await get_storage_service(s3_client=None)
queue_service = await get_queue_service(redis_client=None)
job_service = await get_job_service(redis_client=None)
```

This fails because:
1. Dependency functions expect actual client instances, not `None`
2. Passing `None` bypasses the dependency injection system
3. Services initialize with `None` clients, causing `AttributeError` on first use

**Error manifestation:**
```
ERROR - 'NoneType' object has no attribute 'zrangebyscore'
ERROR - 'NoneType' object has no attribute 'pipeline'
```

### Issue 2: Async Generator Pattern Mismatch

**Location:** [src/dependencies.py:40-58](src/dependencies.py#L40-58)

The dependency functions (`get_redis_client`, `get_s3_client`) are async generators designed for FastAPI's dependency injection system with automatic cleanup. Workers are calling them as regular async functions.

**Correct pattern documented in dependencies.py:**
```python
# For workers, do NOT use this function. Instead:
redis_client = await anext(get_redis_client())
queue_service = QueueService(redis_client=redis_client)
```

But workers are not following this documented pattern.

---

## Impact Assessment

### Functional Impact
- ❌ **Timeout Worker**: Cannot query expired approvals (Redis errors)
- ❌ **Rate Limiting**: Cannot check request limits (Redis errors)
- ⚠️ **PII Worker**: May fail on edge cases (not consistently reproduced)
- ⚠️ **Processing Worker**: May fail on edge cases (not consistently reproduced)

### System Impact
- **Approval timeouts not processed**: Jobs stuck in `awaiting_approval` state indefinitely
- **Rate limiting failures**: All requests fail open (security risk)
- **S3 cleanup blocked**: Temp files never deleted
- **Metrics collection broken**: Cannot track system health

### User Impact
- Jobs with PII require manual cleanup after approval expires
- No protection against submission abuse
- Storage costs increase due to temp file accumulation

---

## Evidence

### 1. Error Logs from E2E Test
```
2025-10-03 15:27:23,983 - ERROR - Rate limit check failed: 'Depends' object has no attribute 'pipeline'
2025-10-03 15:27:23,983 - ERROR - Failed to get quota info: 'Depends' object has no attribute 'zremrangebyscore'
2025-10-03 15:30:14,439 - ERROR - Failed to get expired 1759505414.439223 timeouts: 'NoneType' object has no attribute 'zrangebyscore'
```

### 2. Code Analysis

**timeout_worker.py (lines 280-282):**
```python
# Create service instances using dependency injection pattern
storage_service = await get_storage_service(s3_client=None)  # ❌ Passing None
queue_service = await get_queue_service(redis_client=None)   # ❌ Passing None
job_service = await get_job_service(redis_client=None)       # ❌ Passing None
```

**pii_worker.py (lines 231-240):**
```python
# Same pattern - all workers affected
storage_service = await get_storage_service(s3_client=None)
queue_service = await get_queue_service(redis_client=None)
job_service = await get_job_service(redis_client=None)
```

**processing_worker.py (lines 305-313):**
```python
# Same pattern - all workers affected
storage_service = await get_storage_service(s3_client=None)
queue_service = await get_queue_service(redis_client=None)
job_service = await get_job_service(redis_client=None)
```

### 3. Correct Pattern Example

**dependencies.py documentation (lines 76-78):**
```python
# For workers, do NOT use this function. Instead:
s3_client = await anext(get_s3_client())
storage_service = StorageService(s3_client=s3_client, ...)
```

---

## Dependencies

**Blocking:**
- PRD-005 ✅ (PII Worker implementation)
- PRD-007 ✅ (Processing Worker implementation)
- PRD-008 ✅ (Timeout Worker implementation)

**Blocked by:**
- None (can be fixed immediately)

**Related:**
- BUG-004 (Timeout Service Method Signature) - Both affect timeout worker
- BUG-005 (Result File Format Mismatch) - May fail due to S3 client issues

---

## Technical Solution

### Solution Overview
Replace incorrect dependency function calls with proper async generator consumption pattern. Create Redis and S3 clients explicitly in worker initialization, then pass to service constructors.

### Implementation Steps

#### Step 1: Fix Timeout Worker
**File:** [src/workers/timeout_worker.py](src/workers/timeout_worker.py)

**Change lines 270-282:**
```python
# BEFORE (lines 272-282):
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=settings.redis_max_connections,
)

storage_service = await get_storage_service(s3_client=None)
queue_service = await get_queue_service(redis_client=None)
job_service = await get_job_service(redis_client=None)

# AFTER:
# Create Redis client directly
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=settings.redis_max_connections,
)

# Create S3 client using async generator pattern
s3_client_gen = get_s3_client()
s3_client = await anext(s3_client_gen)

# Create service instances with actual clients
storage_service = StorageService(
    s3_client=s3_client,
    temp_bucket=settings.s3_temp_bucket,
    results_bucket=settings.s3_results_bucket
)
queue_service = QueueService(redis_client=redis_client)
job_service = JobService(redis_client=redis_client)
```

#### Step 2: Fix PII Worker
**File:** [src/workers/pii_worker.py](src/workers/pii_worker.py)

Apply same pattern as timeout worker (lines 231-240).

#### Step 3: Fix Processing Worker
**File:** [src/workers/processing_worker.py](src/workers/processing_worker.py)

Apply same pattern as timeout worker (lines 305-313).

#### Step 4: Fix Rate Limit Middleware
**File:** [src/middleware/rate_limit.py](src/middleware/rate_limit.py)

**Issue:** Lines 46-49 use async for loop incorrectly

**Change:**
```python
# BEFORE (lines 46-49):
rate_limiter = None
try:
    async for rate_limiter in get_rate_limit_service():
        break

# AFTER:
rate_limiter = None
try:
    rate_limit_gen = get_rate_limit_service()
    rate_limiter = await anext(rate_limit_gen)
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] All three workers (PII, Processing, Timeout) initialize successfully with valid Redis/S3 clients
- [ ] Timeout worker successfully queries and processes expired approvals
- [ ] Rate limiting middleware successfully checks Redis for request limits
- [ ] No `NoneType` attribute errors in logs during normal operation
- [ ] Services can perform Redis operations (zadd, zrangebyscore, pipeline)
- [ ] Services can perform S3 operations (put_object, get_object, delete_object)

### Verification Tests

#### Test 1: Worker Initialization
```python
async def test_timeout_worker_initialization():
    """Verify timeout worker initializes with valid clients."""
    shutdown_event = asyncio.Event()

    # Start worker in background
    task = asyncio.create_task(start_timeout_worker(shutdown_event))
    await asyncio.sleep(2)  # Let it initialize

    worker = get_timeout_worker()
    assert worker is not None
    assert worker.queue_service is not None
    assert worker.queue_service.redis is not None
    assert worker.storage_service is not None
    assert worker.storage_service.s3_client is not None

    # Cleanup
    shutdown_event.set()
    await task
```

#### Test 2: Timeout Service Redis Operations
```python
async def test_timeout_service_redis_operations(timeout_worker):
    """Verify timeout service can query Redis."""
    # Add mock expired approval
    await timeout_worker.queue_service.add_to_timeout_tracking(
        job_id="test-job-123",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    # Process expired approvals - should not raise AttributeError
    count = await timeout_worker.timeout_service.process_expired_approvals()
    assert count >= 0  # Should execute without error
```

#### Test 3: Rate Limiting Redis Operations
```python
async def test_rate_limiting_redis_operations():
    """Verify rate limiting can check Redis."""
    app = create_app()
    client = TestClient(app)

    # Make request that triggers rate limit check
    response = client.post("/api/documents/submit", files={"file": dummy_pdf})

    # Should not error with AttributeError
    assert response.status_code in [201, 429]  # Success or rate limited
```

#### Test 4: No NoneType Errors in Logs
```python
async def test_no_nonetype_errors_in_worker_loop():
    """Verify workers run without NoneType errors."""
    shutdown_event = asyncio.Event()

    # Start all workers
    pii_task = asyncio.create_task(start_pii_worker(shutdown_event))
    processing_task = asyncio.create_task(start_processing_worker(shutdown_event))
    timeout_task = asyncio.create_task(start_timeout_worker(shutdown_event))

    # Run for 60 seconds
    await asyncio.sleep(60)

    # Check logs for NoneType errors
    with open("logs/app.log") as f:
        logs = f.read()
        assert "'NoneType' object has no attribute" not in logs

    # Cleanup
    shutdown_event.set()
    await asyncio.gather(pii_task, processing_task, timeout_task)
```

---

## Testing Strategy

### Unit Tests
**Location:** `tests/workers/test_worker_initialization.py` (new file)

1. Test each worker initializes with valid clients
2. Test service methods can call Redis operations
3. Test service methods can call S3 operations
4. Mock Redis/S3 to verify correct client passed

### Integration Tests
**Location:** `tests/integration/test_worker_dependencies.py` (new file)

1. Start all workers and verify no errors in first 60 seconds
2. Submit job and verify timeout worker can process it after expiry
3. Make multiple API requests and verify rate limiting works
4. Check Prometheus metrics for worker errors

### E2E Tests
**Location:** `tests/integration/test_full_pipeline.py` (existing)

1. Run complete job submission → PII → approval → processing flow
2. Verify no dependency-related errors in logs
3. Verify timeout worker processes expired job correctly
4. Verify rate limiting blocks excessive requests

---

## Regression Prevention

### Code Review Checklist
- [ ] Workers never call dependency functions directly with parameters
- [ ] Workers always use `await anext(get_*_client())` pattern
- [ ] Service constructors receive actual client instances
- [ ] Middleware uses correct async generator consumption

### Documentation Updates
**File:** [src/workers/README.md](src/workers/README.md) (new file)

Add section:
```markdown
## Worker Dependency Injection Pattern

Workers must create clients manually and pass to services:

✅ CORRECT:
```python
redis_client = redis.from_url(settings.redis_url)
s3_client = await anext(get_s3_client())
service = Service(redis_client=redis_client)
```

❌ INCORRECT:
```python
service = await get_service(redis_client=None)  # Will fail!
```
```

### Static Analysis
Add type hints to catch None parameters:
```python
class QueueService:
    def __init__(self, redis_client: Redis):  # Type hint enforces non-None
        assert redis_client is not None, "Redis client required"
        self.redis = redis_client
```

---

## Edge Cases

### Case 1: S3 Client Cleanup
**Issue:** Async generators expect cleanup via `aclose()`, but workers don't call it

**Solution:** Store generator reference and close in worker shutdown:
```python
# In worker initialization:
self.s3_client_gen = get_s3_client()
self.s3_client = await anext(self.s3_client_gen)

# In worker shutdown:
await self.s3_client_gen.aclose()
```

### Case 2: Redis Connection Pool Exhaustion
**Issue:** Each worker creates separate Redis client, may exhaust connection pool

**Mitigation:** Use shared Redis client across services:
```python
# Single client for all services
redis_client = redis.from_url(settings.redis_url, max_connections=50)
queue_service = QueueService(redis_client)
job_service = JobService(redis_client)
metrics_service = MetricsService(redis_client)
```

### Case 3: Worker Restart Mid-Initialization
**Issue:** If worker crashes during startup, clients may leak

**Solution:** Wrap initialization in try/finally:
```python
s3_client_gen = None
try:
    s3_client_gen = get_s3_client()
    s3_client = await anext(s3_client_gen)
    # ... rest of initialization
except Exception:
    if s3_client_gen:
        await s3_client_gen.aclose()
    raise
```

---

## Rollback Plan

If fix causes regressions:

1. **Immediate:** Revert PR
2. **Short-term:** Disable affected workers via environment variable
3. **Long-term:** Redesign dependency injection for workers

**Rollback command:**
```bash
git revert <commit-hash>
docker-compose -f docker-compose.yml -f docker-compose.dev.yml restart api-gateway
```

---

## Definition of Done

- [x] Root cause identified and documented
- [ ] Fix implemented for all three workers (PII, Processing, Timeout)
- [ ] Fix implemented for rate limit middleware
- [ ] Unit tests pass for worker initialization
- [ ] Integration tests pass for worker loops (60 second run)
- [ ] E2E test completes without NoneType errors
- [ ] Timeout worker successfully processes expired approvals
- [ ] Rate limiting successfully blocks requests
- [ ] Worker README.md documentation added
- [ ] Code review completed
- [ ] PR merged to main branch
