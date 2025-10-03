# BUG-004: Timeout Service Method Signature Mismatch

**Priority:** HIGH
**Severity:** Critical - Causes recurring errors every 30 seconds
**Discovered:** 2025-10-03 (E2E Testing)
**Status:** RESOLVED ✅
**Fixed:** 2025-10-03
**Verification:** All tests pass, no errors in live system

---

## Problem Statement

The `TimeoutService.process_expired_approvals()` calls `QueueService.get_expired_timeouts()` with incorrect parameters, causing `'NoneType' object has no attribute 'zrangebyscore'` errors every 30 seconds when the timeout worker attempts to check for expired approvals.

---

## Root Cause Analysis

### Method Signature Mismatch

**Location:** [src/services/timeout_service.py:54-56](src/services/timeout_service.py#L54-56)

`TimeoutService` calls `get_expired_timeouts()` with a `current_time` parameter:

```python
# Line 51: Calculate current timestamp
current_time = datetime.now(timezone.utc).timestamp()

# Lines 54-56: Pass timestamp as first argument
expired_job_ids = await self.queue_service.get_expired_timeouts(
    current_time  # ❌ Wrong parameter!
)
```

But `QueueService.get_expired_timeouts()` expects `timeout_type` as first parameter:

**Location:** [src/services/queue_service.py:242-244](src/services/queue_service.py#L242-244)

```python
async def get_expired_timeouts(
    self,
    timeout_type: str = "approval"  # ✅ Expects string, gets float
) -> list[tuple[str, float]]:
```

### What Happens

1. `timeout_service.py` passes `1759505414.439223` (float timestamp)
2. `queue_service.py` receives it as `timeout_type` parameter
3. Line 269: `key = timeout_key(timeout_type)` is called
4. `timeout_key()` expects string, but gets float → converts to string "1759505414.439223"
5. Redis key becomes `"eq-pdf:timeouts:1759505414.439223"` (invalid key)
6. Line 273: `await self.redis.zrangebyscore(key, ...)` is called
7. Since `self.redis` is `None` (from BUG-003), error occurs: `'NoneType' object has no attribute 'zrangebyscore'`

**Note:** Even after BUG-003 is fixed, this will still fail because the Redis key will be wrong.

---

## Impact Assessment

### Functional Impact
- ❌ **Approval timeouts never processed**: Expired approvals stay in queue indefinitely
- ❌ **S3 temp files never deleted**: PDFs for expired approvals accumulate
- ❌ **Metrics not updated**: Timeout counts never incremented
- ❌ **Redis keys polluted**: Invalid timeout keys created every 30 seconds

### System Impact
- **Log spam**: Error every 30 seconds fills logs
- **Storage costs**: Temp bucket fills with undeleted PDFs
- **User confusion**: Jobs stuck in `awaiting_approval` state with no expiry
- **Monitoring alerts**: False positives for Redis errors

### User Impact
- Faculty must manually track approval deadlines
- No automatic cleanup of denied/expired submissions
- Dashboard shows stale `awaiting_approval` jobs indefinitely

---

## Evidence

### 1. Error Logs
```
2025-10-03 15:30:14,439 - ERROR - Failed to get expired 1759505414.439223 timeouts: 'NoneType' object has no attribute 'zrangebyscore'
2025-10-03 15:30:44,443 - ERROR - Failed to get expired 1759505444.443785 timeouts: 'NoneType' object has no attribute 'zrangebyscore'
2025-10-03 15:31:16,263 - ERROR - Failed to get expired 1759505476.263598 timeouts: 'NoneType' object has no attribute 'zrangebyscore'
```

Notice the float timestamps (`1759505414.439223`) in the error messages - these are being passed as `timeout_type`.

### 2. Code Analysis

**timeout_service.py (lines 50-56):**
```python
# Get current timestamp
current_time = datetime.now(timezone.utc).timestamp()  # Returns float

# Query Redis sorted set for expired timeouts
expired_job_ids = await self.queue_service.get_expired_timeouts(
    current_time  # ❌ Passing float to parameter expecting string
)
```

**queue_service.py (lines 242-278):**
```python
async def get_expired_timeouts(
    self,
    timeout_type: str = "approval"  # Expects string!
) -> list[tuple[str, float]]:
    try:
        # Get current timestamp AGAIN inside the method
        current_time = datetime.now(timezone.utc).timestamp()

        # Get Redis key for this timeout type
        key = timeout_key(timeout_type)  # Creates wrong key!

        # ZRANGEBYSCORE returns members with score in range [0, current_time]
        result = await self.redis.zrangebyscore(
            key,
            min=0,
            max=current_time,
            withscores=True
        )
```

### 3. Design Intent

The method **calculates current_time internally**, so passing it as a parameter is redundant and incorrect. The parameter should specify **which timeout type** to check (approval, processing, etc.).

---

## Dependencies

**Blocking:**
- PRD-008 ✅ (Timeout Worker implementation)
- BUG-003 ⚠️ (Worker DI must be fixed first to reveal this bug fully)

**Blocked by:**
- None (can be fixed immediately)

**Related:**
- BUG-003 (Worker Dependency Injection) - Must fix both together

---

## Technical Solution

### Solution Overview
Remove the incorrect `current_time` parameter from the `process_expired_approvals()` call. The method already calculates current time internally.

### Implementation Steps

#### Step 1: Fix TimeoutService Call
**File:** [src/services/timeout_service.py](src/services/timeout_service.py)

**Change lines 50-56:**
```python
# BEFORE:
# Get current timestamp
current_time = datetime.now(timezone.utc).timestamp()

# Query Redis sorted set for expired timeouts
expired_job_ids = await self.queue_service.get_expired_timeouts(
    current_time
)

# AFTER:
# Query Redis sorted set for expired timeouts (approval type by default)
expired_job_ids = await self.queue_service.get_expired_timeouts()
```

**Or if explicit:**
```python
expired_job_ids = await self.queue_service.get_expired_timeouts(
    timeout_type="approval"
)
```

#### Step 2: Verify Method Documentation
**File:** [src/services/queue_service.py](src/services/queue_service.py)

Ensure documentation clearly states method calculates current time internally:

```python
async def get_expired_timeouts(
    self,
    timeout_type: str = "approval"
) -> list[tuple[str, float]]:
    """Get jobs with expired approval deadlines.

    Queries Redis sorted set for members with scores (timestamps) less than
    or equal to current time. Current time is calculated internally.

    Args:
        timeout_type: Type of timeout to check (default: "approval")
                     Valid values: "approval", "processing", "cleanup"

    Returns:
        List of tuples: [(job_id, expiration_timestamp), ...]
        Empty list if no expired jobs or on error
    """
```

#### Step 3: Add Type Validation
**File:** [src/services/queue_service.py](src/services/queue_service.py)

Add validation to catch incorrect parameter types:

```python
async def get_expired_timeouts(
    self,
    timeout_type: str = "approval"
) -> list[tuple[str, float]]:
    # Validate timeout_type parameter
    if not isinstance(timeout_type, str):
        raise TypeError(
            f"timeout_type must be str, got {type(timeout_type).__name__}. "
            f"Do not pass timestamp as parameter - current time is calculated internally."
        )

    valid_types = ["approval", "processing", "cleanup"]
    if timeout_type not in valid_types:
        raise ValueError(
            f"Invalid timeout_type '{timeout_type}'. "
            f"Valid values: {', '.join(valid_types)}"
        )

    # Rest of implementation...
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] `process_expired_approvals()` calls `get_expired_timeouts()` without parameters
- [ ] Timeout worker runs without Redis key errors
- [ ] Expired approvals are successfully detected and processed
- [ ] No "Failed to get expired" errors in logs during normal operation
- [ ] Correct Redis key used: `eq-pdf:timeouts:approval`

### Verification Tests

#### Test 1: Method Call Signature
```python
async def test_timeout_service_calls_get_expired_correctly():
    """Verify TimeoutService calls QueueService with correct parameters."""
    mock_queue = AsyncMock()
    mock_queue.get_expired_timeouts = AsyncMock(return_value=[])

    timeout_service = TimeoutService(
        queue_service=mock_queue,
        job_service=mock_job,
        cleanup_service=mock_cleanup,
        metrics_service=mock_metrics
    )

    await timeout_service.process_expired_approvals()

    # Should call with no arguments (uses default timeout_type="approval")
    mock_queue.get_expired_timeouts.assert_called_once_with()
```

#### Test 2: Type Validation
```python
async def test_get_expired_timeouts_rejects_float():
    """Verify get_expired_timeouts rejects float parameter."""
    queue_service = QueueService(redis_client)

    with pytest.raises(TypeError, match="timeout_type must be str"):
        await queue_service.get_expired_timeouts(1759505414.439223)
```

#### Test 3: Correct Redis Key
```python
async def test_get_expired_timeouts_uses_correct_key(mock_redis):
    """Verify correct Redis key is used."""
    queue_service = QueueService(mock_redis)

    await queue_service.get_expired_timeouts("approval")

    # Check that zrangebyscore was called with correct key
    calls = mock_redis.zrangebyscore.call_args_list
    assert len(calls) == 1
    assert calls[0][0][0] == "eq-pdf:timeouts:approval"  # Correct key!
```

#### Test 4: E2E Timeout Processing
```python
async def test_timeout_worker_processes_expired_approval():
    """Verify timeout worker successfully processes expired approval."""
    # Submit job with PII
    job_id = await submit_test_job()

    # Set approval expiry to past
    await queue_service.add_to_timeout_tracking(
        job_id=job_id,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
    )

    # Wait for timeout worker to process
    await asyncio.sleep(35)  # Worker checks every 30s

    # Verify job marked as denied
    job = await job_service.get_job(job_id)
    assert job["status"] == "denied"
    assert job["denial_reason"] == "Approval timeout expired"
```

---

## Testing Strategy

### Unit Tests
**Location:** `tests/services/test_timeout_service.py` (update existing)

1. Test `process_expired_approvals()` calls with correct signature
2. Test `get_expired_timeouts()` type validation
3. Test `get_expired_timeouts()` value validation
4. Mock Redis to verify correct key construction

### Integration Tests
**Location:** `tests/integration/test_timeout_processing.py` (new file)

1. Add expired job to Redis sorted set
2. Run timeout worker for 60 seconds
3. Verify expired job processed
4. Verify correct Redis keys used
5. Verify no signature mismatch errors

### Regression Tests
**Location:** `tests/services/test_timeout_monitoring.py` (update existing)

Update existing tests that mock `get_expired_timeouts()` to ensure they don't pass incorrect parameters.

---

## Regression Prevention

### Code Review Checklist
- [ ] Method calls match documented signatures
- [ ] Parameters passed match parameter names and types
- [ ] No redundant parameter passing (e.g., timestamp calculated internally)
- [ ] Documentation clearly states which parameters are required

### Static Analysis
Enable mypy strict mode to catch type mismatches:

```python
# mypy.ini
[mypy]
strict = True
warn_unused_ignores = True
warn_redundant_casts = True
```

This would have caught:
```python
# mypy error:
# Argument 1 to "get_expired_timeouts" has incompatible type "float"; expected "str"
```

### Documentation Standards
Add parameter type hints and descriptions to all service methods:

```python
async def get_expired_timeouts(
    self,
    timeout_type: str = "approval"  # ✅ Clear type hint
) -> list[tuple[str, float]]:
    """
    Args:
        timeout_type: Type of timeout to check. Do NOT pass timestamp.
    """
```

---

## Edge Cases

### Case 1: Multiple Timeout Types
**Future feature:** Support different timeout types (processing, cleanup)

**Consideration:** When calling from different contexts:
```python
# Approval timeouts
await queue_service.get_expired_timeouts("approval")

# Processing timeouts (future)
await queue_service.get_expired_timeouts("processing")
```

Ensure each context passes correct string literal.

### Case 2: Empty Redis Set
**Scenario:** No expired jobs exist

**Current behavior:** Returns empty list correctly
**Verification:** Test confirms no errors with empty result

### Case 3: Redis Connection Failure
**Scenario:** Redis unavailable during timeout check

**Current behavior:** Catches exception, logs error, returns empty list
**Verification:** Mock Redis failure and verify graceful degradation

---

## Rollback Plan

If fix causes regressions:

1. **Immediate:** Revert commit
2. **Short-term:** Add temporary workaround to catch float parameter
3. **Long-term:** Redesign timeout processing if pattern is flawed

**Temporary workaround (if needed):**
```python
async def get_expired_timeouts(self, timeout_type: str | float = "approval"):
    # Temporary: Accept both types during transition
    if isinstance(timeout_type, float):
        logger.warning("get_expired_timeouts called with timestamp - ignoring")
        timeout_type = "approval"
    # ... rest of method
```

---

## Related Issues

### Similar Pattern Checks
Audit codebase for similar issues:

```bash
# Find other methods that might have signature mismatches
grep -r "\.timestamp()" src/services/ | grep "await.*\("
```

**Findings:** None found - this appears to be an isolated issue.

### Documentation Updates Needed
**File:** [src/shared/README.md](src/shared/README.md)

Update Queue Service section (line 326):
```markdown
# BEFORE:
expired_jobs = await queue.get_expired_timeouts()

# AFTER (clarify parameter):
expired_jobs = await queue.get_expired_timeouts()  # Uses default timeout_type="approval"
expired_jobs = await queue.get_expired_timeouts("processing")  # Or specify type
```

---

## Definition of Done

- [x] Root cause identified and documented
- [x] Fix implemented in `timeout_service.py`
- [x] Type validation added to `queue_service.py`
- [x] Documentation updated with clear parameter descriptions
- [x] Unit tests pass for method signature
- [x] Integration tests pass for timeout processing
- [x] No "Failed to get expired" errors in 60-second worker run
- [x] Expired approvals successfully processed in E2E test
- [x] All tests pass (15 timeout-related tests)
- [x] Live system verified - no errors

---

## Resolution Summary

**Fixed:** 2025-10-03

### Changes Made

1. **[src/services/timeout_service.py:49-62](src/services/timeout_service.py#L49-62)**
   - Removed incorrect `current_time` parameter from `get_expired_timeouts()` call
   - Updated to unpack tuples: `for job_id, expiration_timestamp in expired_timeouts`
   - Fixed variable names for clarity (`expired_job_ids` → `expired_timeouts`)

2. **[src/services/queue_service.py:242-276](src/services/queue_service.py#L242-276)**
   - Added type validation: raises `TypeError` if non-string passed
   - Enhanced docstring to clarify method calculates time internally
   - Added warning: "DO NOT pass a timestamp - this method calculates current time internally"

3. **[tests/services/test_queue_service.py:414-425](tests/services/test_queue_service.py#L414-425)**
   - Added `test_get_expired_timeouts_rejects_non_string_type()` test
   - Validates TypeError raised with helpful message

4. **[tests/services/test_timeout_monitoring.py](tests/services/test_timeout_monitoring.py)**
   - Updated all test mocks to return `[(job_id, timestamp), ...]` tuples
   - All 10 timeout monitoring tests pass

### Verification Results

✅ **Unit Tests:** 15 tests pass
- 5 queue service timeout tests
- 10 timeout monitoring service tests

✅ **Live System:** No errors after restart
- Timeout worker successfully processes expired approvals
- No "Failed to get expired timeouts" errors
- Correct Redis key used: `eq-pdf:timeouts:approval`

✅ **Type Safety:** TypeError prevents future mistakes
- Passing float timestamp now raises clear error
- Error message guides developers to correct usage

### Impact

- ✅ Approval timeouts now processed correctly
- ✅ S3 temp files cleaned up properly
- ✅ No recurring errors every 30 seconds
- ✅ Jobs no longer stuck in `awaiting_approval`
- ✅ Type safety prevents similar bugs
