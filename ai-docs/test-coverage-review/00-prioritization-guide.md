# Test Prioritization Guide

**Project**: Equalify PDF Converter
**Date**: 2025-12-10
**Purpose**: Practical guidance on what tests to add without over-engineering

---

## Philosophy: Test What's Expensive to Debug

Not all missing tests are worth writing. Prioritize based on:

1. **Production incident cost** - Would this bug cause downtime, data loss, or compliance issues?
2. **Debugging difficulty** - Would this bug be obvious or take hours to trace?
3. **Test simplicity** - Can we catch this with 20 lines or does it need 200?

**Anti-pattern to avoid**: Testing that frameworks work (Pydantic validates, PydanticAI retries, FastAPI routes). Trust your dependencies.

---

## Tier 1: High ROI Tests (Add These)

These tests catch real bugs with minimal code. **~150 lines total, ~2-3 hours of work.**

### 1. Rate Limiting Middleware

**File**: `tests/unit/middleware/test_rate_limit.py`
**Effort**: ~50 lines, 2 tests

**Why it matters**:
- Broken rate limiter = either blocks all users OR allows abuse
- Both scenarios are production incidents
- Redis failure behavior (fail-open) is critical for availability

**What to test**:
```python
async def test_rate_limit_fails_open_on_redis_error():
    """If Redis dies, requests should still succeed (fail-open)."""

async def test_rate_limit_blocks_after_threshold():
    """Request after limit exceeded returns 429."""
```

**What NOT to test**:
- Exact sliding window math (trust Redis ZADD/ZRANGEBYSCORE)
- Every rate limit tier (one is enough to verify wiring)
- Header formatting (low-value edge case)

---

### 2. Retry Error Categorization

**File**: `tests/unit/utils/test_retry_helpers.py`
**Effort**: ~30 lines, 1 parameterized test

**Why it matters**:
- Wrong categorization = infinite retries (resource exhaustion) or premature failure (availability loss)
- Every S3 and Redis operation uses this code
- Bug here affects entire system

**What to test**:
```python
@pytest.mark.parametrize("error,should_retry", [
    # Non-retryable (permanent failures)
    (ClientError({'Error': {'Code': 'NoSuchKey'}}, 'op'), False),
    (ClientError({'Error': {'Code': 'AccessDenied'}}, 'op'), False),
    (ClientError({'Error': {'Code': 'InvalidRequest'}}, 'op'), False),
    # Retryable (transient failures)
    (ClientError({'Error': {'Code': 'RequestTimeout'}}, 'op'), True),
    (ClientError({'Error': {'Code': 'ServiceUnavailable'}}, 'op'), True),
    (RedisConnectionError("Connection refused"), True),
    (asyncio.TimeoutError(), True),
])
def test_is_retryable_error(error, should_retry):
    assert is_retryable_error(error) == should_retry
```

**What NOT to test**:
- Exponential backoff timing (trust asyncio.sleep)
- Jitter randomness (non-deterministic, low value)
- Logging output (implementation detail)

---

### 3. PII Detection Service Routing

**File**: `tests/unit/services/test_pii_service.py`
**Effort**: ~40 lines, 2 tests

**Why it matters**:
- Wrong routing = compliance violation (PII processed without approval)
- Core business logic with zero current coverage
- Two code paths, both critical

**What to test**:
```python
async def test_pii_found_routes_to_approval_queue():
    """PDF with detected PII goes to approval queue, not processing."""
    # Mock Presidio to return findings
    # Verify: job status = awaiting_approval
    # Verify: enqueued to APPROVAL_QUEUE
    # Verify: timeout tracking added

async def test_clean_pdf_routes_to_processing_queue():
    """PDF without PII skips approval, goes directly to processing."""
    # Mock Presidio to return no findings
    # Verify: job status = processing
    # Verify: enqueued to PROCESSING_QUEUE
```

**What NOT to test**:
- Presidio's detection accuracy (that's Presidio's job)
- Every PII entity type (one is enough to verify wiring)
- Token generation details (test separately if needed)

---

### 4. Worker Shutdown Requeueing

**File**: Add to existing `tests/unit/workers/test_*_worker.py` (or create)
**Effort**: ~20 lines per worker, 2-3 workers

**Why it matters**:
- Job loss during deployments is invisible until users complain
- Critical for zero-downtime deployments
- Currently 0% tested across all workers

**What to test**:
```python
async def test_processing_worker_requeues_on_shutdown():
    """Job in progress gets requeued when shutdown event fires."""
    # Start worker with job in queue
    # Signal shutdown mid-processing
    # Verify: job requeued to same queue
    # Verify: worker exits cleanly

async def test_pii_worker_requeues_on_shutdown():
    """Same pattern for PII worker."""
```

**What NOT to test**:
- Graceful vs forceful shutdown (OS handles SIGTERM)
- Multiple concurrent jobs (one is enough)
- Metrics updates during shutdown (low value)

---

## Tier 2: Medium ROI (Consider Later)

These have value but aren't urgent. Add if you have time or hit related bugs.

### 5. One True E2E Workflow Test

**File**: `tests/e2e/workflows/test_happy_path.py`
**Effort**: ~100 lines, 1 comprehensive test

**Why it matters**:
- Catches integration issues invisible to unit tests
- Provides confidence for major releases
- Documents expected system behavior

**What to test**:
```python
async def test_clean_pdf_submission_to_completion():
    """Full workflow: upload → PII scan → process → result available."""
    # Use real Redis, real S3 (LocalStack)
    # Mock only: Bedrock/Docling (expensive, slow)
    # Poll status until completion
    # Verify result URL works
```

**Why not Tier 1**: Integration tests already catch most routing issues. This is insurance, not critical.

---

### 6. Circuit Breaker State Visibility

**Alternative to testing**: Add observability instead.

**Why testing is hard**:
- State transitions are time-based
- Requires simulating 5+ failures in sequence
- Tests become slow and flaky

**Better approach** - Add metrics (already have Prometheus):
```python
# In circuit_breaker.py - verify this exists
circuit_breaker_state_gauge.labels(name=self.name).set(self.state.value)
```

Then verify in Grafana/alerting that circuit breakers are visible.

---

### 7. API skip_pii_scan Flow

**File**: Add to `tests/integration/test_documents.py`
**Effort**: ~30 lines, 1 test

**Why it matters**:
- Alternate code path with zero coverage
- Used for trusted/pre-scanned documents

**What to test**:
```python
async def test_submit_with_skip_pii_scan():
    """skip_pii_scan=True bypasses PII queue, goes to processing."""
    # Verify: status = processing (not pii_scanning)
    # Verify: enqueued to PROCESSING_QUEUE
    # Verify: pii_skipped metadata stored
```

**Why not Tier 1**: Feature is likely less used than main flow. Lower incident probability.

---

## Tier 3: Low ROI (Skip These)

These were identified as "gaps" but aren't worth the test code.

### Skip: API Layer Unit Tests

**Why skip**:
- Integration tests already verify routing, validation, responses
- Unit tests would duplicate coverage with more mocking
- FastAPI routing rarely breaks

**The reports say**: "0 unit tests for API layer"
**Reality**: Integration tests in `tests/integration/test_documents.py` cover this adequately.

---

### Skip: LLM Response Variation Tests

**Why skip**:
- PydanticAI already validates structured outputs
- PydanticAI already retries on validation failure
- Testing this = testing the framework

**The reports say**: "LLM response variations 0% tested"
**Reality**: PydanticAI handles malformed responses. If it doesn't, that's a PydanticAI bug to report upstream.

---

### Skip: Comprehensive Model Boundary Tests

**Why skip**:
- Pydantic already validates min/max/patterns
- Testing every boundary = testing Pydantic
- Fix bugs as they appear (rare)

**The reports say**: "Missing boundary tests for string fields"
**Reality**: If `max_length=255` doesn't work, Pydantic is broken. It's not.

---

### Skip: Agent Prompt Construction Tests

**Why skip**:
- Prompts are tested implicitly by agent behavior tests
- Format string edge cases (`{` in title) are rare
- Fix with escaping if it ever happens

**The reports say**: "What if document_title contains `{`?"
**Reality**: Escape it when/if a user reports it. Premature optimization otherwise.

---

### Skip: Unicode/Special Character Tests

**Why skip**:
- Python 3 handles Unicode well
- Redis/S3 handle Unicode well
- Fix specific bugs as reported

**The reports say**: "Missing Unicode tests for all string fields"
**Reality**: Low probability, easy to fix if it happens.

---

### Skip: Concurrent Thread Safety Tests

**Why skip**:
- Python GIL prevents most threading issues
- Async code uses cooperative multitasking
- True race conditions are rare in this architecture

**The reports say**: "Circuit breaker thread safety not tested"
**Reality**: The circuit breaker uses `threading.Lock`. It works.

---

## Tier 4: Fix in Code, Not Tests

Some "missing tests" indicate code that should be improved instead.

### Fix: State Consistency Validation

**Problem**: "Completed jobs could have error_message set"

**Don't**: Write tests for every invalid state combination

**Do**: Add a Pydantic validator
```python
# src/shared/models/job.py
@model_validator(mode='after')
def validate_state_consistency(self):
    if self.status == "completed" and self.error_message:
        raise ValueError("Completed jobs cannot have error_message")
    if self.status == "completed" and not self.markdown_url:
        raise ValueError("Completed jobs must have markdown_url")
    if self.status == "awaiting_approval" and not self.pii_findings:
        raise ValueError("Awaiting approval requires pii_findings")
    return self
```

One validator replaces 10+ tests and prevents the bug at the source.

---

### Fix: Status Enumeration Mismatch

**Problem**: `JobStatus.status` Literal doesn't match `statuses.py` constants

**Don't**: Write tests to verify they match

**Do**: Use a single source of truth
```python
# src/shared/models/job.py
from shared.constants.statuses import JobStatusType

class JobStatus(BaseModel):
    status: JobStatusType  # Use the canonical type, not a duplicate Literal
```

---

### Fix: Field Dependency Documentation

**Problem**: "Should `awaiting_approval` require `approval_token`?"

**Don't**: Write tests for implicit requirements

**Do**: Make requirements explicit in the model
```python
class JobStatus(BaseModel):
    approval_token: str | None = Field(
        default=None,
        description="Required when status is awaiting_approval"
    )

    @model_validator(mode='after')
    def validate_approval_fields(self):
        if self.status == "awaiting_approval" and not self.approval_token:
            raise ValueError("approval_token required for awaiting_approval status")
        return self
```

---

## Summary: The 80/20 Test Plan

### Add (~150 lines, ~2-3 hours)

| Test | Lines | Catches |
|------|-------|---------|
| Rate limit fail-open | ~25 | All users blocked on Redis failure |
| Rate limit threshold | ~25 | Abuse/cost overruns |
| Retry error categorization | ~30 | Infinite retries, premature failures |
| PII routes to approval | ~20 | Compliance violations |
| Clean PDF routes to processing | ~20 | Unnecessary approval delays |
| Worker shutdown requeue | ~30 | Job loss on deploy |

### Consider Later (~100 lines)

| Test | Lines | Catches |
|------|-------|---------|
| E2E happy path | ~100 | Integration issues |
| skip_pii_scan flow | ~30 | Alternate path bugs |

### Skip

- API unit tests (integration tests sufficient)
- LLM response tests (PydanticAI handles it)
- Model boundary tests (Pydantic handles it)
- Unicode tests (fix bugs as reported)
- Thread safety tests (GIL + locks sufficient)

### Fix in Code

- State consistency → Pydantic validators
- Status mismatch → Single source of truth
- Field dependencies → Explicit validators

---

## Decision Framework for Future Tests

When considering a new test, ask:

1. **What production incident does this prevent?**
   - If you can't name one, skip it.

2. **How would we debug this without the test?**
   - If logs/metrics make it obvious, skip it.

3. **Is this testing our code or a framework?**
   - If it's Pydantic/PydanticAI/FastAPI behavior, skip it.

4. **Can we prevent this with better code instead?**
   - Validators > tests for invariants.

5. **What's the probability × impact?**
   - Rare + low impact = skip
   - Common OR high impact = test

---

## Appendix: Test ROI Matrix

| Category | Probability | Impact | Debuggability | Test ROI |
|----------|-------------|--------|---------------|----------|
| Rate limiting broken | Medium | High | Hard | **HIGH** |
| Retry logic wrong | Medium | High | Hard | **HIGH** |
| PII routing wrong | Low | Critical | Medium | **HIGH** |
| Job loss on shutdown | Medium | High | Hard | **HIGH** |
| Circuit breaker stuck | Low | High | Easy (metrics) | Medium |
| LLM malformed response | Medium | Medium | Easy (logs) | Low |
| Model validation bypass | Low | Medium | Easy | Low |
| Unicode handling | Low | Low | Easy | **Skip** |
| API routing broken | Very Low | Medium | Easy | **Skip** |
