# Workers Layer Test Review - Equalify PDF Converter

**Review Date**: 2025-12-10
**Scope**: `src/workers/` and `tests/unit/workers/`
**Reviewer**: Automated Test Coverage Analysis

---

## Executive Summary

**Critical Finding: No unit tests exist for the workers layer.** While integration tests exist for ProcessingWorker and TimeoutWorker in `/tests/integration/workers/`, there are zero unit tests in `/tests/unit/workers/`. The integration tests that do exist are minimal and have significant coverage gaps that would fail to catch real production issues.

---

## Current Test Coverage

### What Exists:
1. **ProcessingWorker** - `/tests/integration/workers/test_processing_worker.py` (321 lines, 16 tests)
2. **TimeoutWorker** - `/tests/integration/workers/test_timeout_worker.py` (304 lines, multiple test classes)
3. **PIIWorker** - **NO TESTS AT ALL**

### What's Missing:
- **No unit tests** for any worker in `/tests/unit/workers/`
- **No tests** for PIIWorker whatsoever
- Limited integration coverage with critical gaps

---

## Detailed Test Critique by Worker

### 1. PIIWorker - `src/workers/pii_worker.py` (180 lines)

**Current Coverage: 0% - NO TESTS EXIST**

#### Critical Missing Tests:

**A. Queue Processing Tests**
- Lines 69-90: Queue dequeue → job validation → PII service processing flow
- No test verifies the worker correctly calls `PIIDetectionService.process_pii_job()`
- No test for queue timeout behavior (line 69: `timeout=settings.pii_worker_queue_timeout_seconds`)
- No test for Pydantic validation error handling on line 73 (`PIIQueuePayload.model_validate`)

**B. Shutdown/Requeueing Tests**
- Lines 76-80: **Critical requeueing logic** - if shutdown requested mid-job, worker should requeue
- This is a critical safety feature preventing job loss, yet **completely untested**
- No test verifies job is actually requeued before shutdown
- No test verifies worker breaks loop after requeueing

**C. Error Handling Tests**
- Lines 95-105: Exception handling with error sleep
- No test verifies metrics are incremented on error (lines 98-103)
- No test verifies error sleep duration (line 105: `settings.worker_error_sleep_seconds`)
- No test for continuous error scenarios (tight error loop prevention)

**D. Metrics Tests**
- Lines 63, 87-89, 98-103, 109: Prometheus metrics
- No test verifies `worker_active_gauge` is set to 1 on start (line 63)
- No test verifies `worker_active_gauge` is set to 0 on shutdown (line 109)
- No test verifies success counter incremented (lines 87-89)
- No test verifies error counter incremented with correct error type (lines 98-100)

**E. Presidio Pre-loading Tests**
- Lines 138-140: Critical cold-start prevention
- No test verifies Presidio analyzer is eagerly loaded during startup
- This could cause first-request timeouts in production

**F. Global Worker Instance Tests**
- Lines 119-179: Global singleton pattern
- No test for `get_pii_worker()` returning correct instance
- No test for multiple `start_pii_worker()` calls
- No test verifying service dependency injection

---

### 2. ProcessingWorker - `src/workers/processing_worker.py` (174 lines)

**Current Coverage: Partial (integration tests only)**

#### What Integration Tests Cover (test_processing_worker.py):

**Good Coverage:**
- ✅ Worker initialization (tests lines 1-2)
- ✅ `running` flag lifecycle (tests lines 3-4)
- ✅ Queue dequeue calls (test line 59)
- ✅ Payload validation (tests lines 107-124)
- ✅ Stop method (tests lines 147-158)
- ✅ Metrics gauge lifecycle (tests lines 240-281)

**Critical Gaps:**

**A. Shutdown/Requeueing Tests (Lines 84-88)**
```python
# Check shutdown before processing
if shutdown_event and shutdown_event.is_set():
    logger.info("Shutdown requested, requeueing job and stopping")
    # Requeue job for next worker
    await self.queue.enqueue(PROCESSING_QUEUE, job)
    break
```
- **NO TEST** verifies job is requeued on shutdown
- **NO TEST** verifies worker breaks loop after requeueing
- This is a **critical data loss prevention feature** - jobs could be lost if this fails

**B. Actual Processing Integration (Lines 92-96)**
```python
# Process job
await self.processing_service.process_document(job)

# Track successful processing
worker_jobs_processed_total.labels(
    worker_name="processing", result="success"
).inc()
```
- Test line 99-104 mocks `process_document` but **doesn't verify it's called**
- **NO TEST** verifies success metric is incremented **only after** successful processing
- **NO TEST** verifies metric is **not** incremented if processing fails

**C. Error Recovery (Lines 103-113)**
```python
except Exception as e:
    logger.error(f"Processing worker error: {e}", exc_info=True)
    # Track error
    worker_errors_total.labels(
        worker_name="processing", error_type=type(e).__name__
    ).inc()
    worker_jobs_processed_total.labels(
        worker_name="processing", result="error"
    ).inc()
    # Brief pause before retry to avoid tight error loop
    await asyncio.sleep(settings.worker_error_sleep_seconds)
```
- Test line 289-310 checks exception is raised, but **doesn't test error handling**
- **NO TEST** verifies error metrics are incremented with correct labels
- **NO TEST** verifies error sleep duration
- **NO TEST** verifies worker continues polling after error
- **NO TEST** for rapid error scenarios

**D. Queue Timeout Behavior (Line 76)**
```python
job_data = await self.queue.dequeue(
    PROCESSING_QUEUE, timeout=settings.processing_worker_queue_timeout_seconds
)
```
- **NO TEST** verifies correct timeout value is used
- **NO TEST** for long-running queue wait scenarios

**E. Global Singleton (Lines 168-174)**
```python
def stop_processing_worker() -> None:
    """Stop the processing worker gracefully."""
    global _worker_instance

    if _worker_instance:
        _worker_instance.stop()
```
- **NO TEST** for `stop_processing_worker()` function
- **NO TEST** for calling stop when `_worker_instance` is None

---

### 3. TimeoutWorker - `src/workers/timeout_worker.py` (316 lines)

**Current Coverage: Better, but still gaps**

#### What Integration Tests Cover (test_timeout_worker.py):

**Good Coverage:**
- ✅ Service initialization (lines 56-66)
- ✅ Initial state (lines 67-73)
- ✅ `_should_run_task` logic (lines 79-99)
- ✅ Individual task methods (lines 104-177)
- ✅ Task scheduling intervals (lines 264-303)

**Critical Gaps:**

**A. Error Recovery in Main Loop (Lines 135-142)**
```python
except Exception as e:
    logger.error(f"Error in timeout worker loop: {e}", exc_info=True)
    # Track error
    worker_errors_total.labels(
        worker_name="timeout", error_type=type(e).__name__
    ).inc()
    # Sleep longer on error to avoid rapid error loops
    await asyncio.sleep(settings.timeout_worker_error_sleep_seconds)
```
- Test line 232-258 tests error in `_run_approval_check`, but **not in main loop**
- **NO TEST** verifies error metrics in main loop
- **NO TEST** verifies error sleep duration in main loop
- **NO TEST** verifies worker continues after main loop error

**B. Cancellation Handling (Lines 144-146)**
```python
except asyncio.CancelledError:
    logger.info("Timeout worker received cancellation signal")
    raise
```
- Test line 212-228 tests cancellation, but **doesn't verify clean state**
- **NO TEST** verifies metrics are set to 0 on cancellation
- **NO TEST** verifies in-progress tasks are not corrupted

**C. Task Execution Timing Tests**
- **NO TEST** verifies all 4 tasks run on first iteration (when `last_*` is None)
- **NO TEST** verifies task timestamps are updated **only on success**
- **NO TEST** verifies failed task doesn't update timestamp (should retry next iteration)

**D. Real-world Scenario Tests**
- **NO TEST** for task taking longer than check interval
- **NO TEST** for all tasks failing simultaneously
- **NO TEST** for worker running for 24+ hours (daily metrics cleanup)

**E. Metrics Gauge Lifecycle**
- Test exists for active gauge (implied), but **NO EXPLICIT TEST** verifying:
  - Gauge set to 1 on start (line 93)
  - Gauge set to 0 on normal shutdown (line 150)
  - Gauge set to 0 on error shutdown (line 150)
  - Gauge set to 0 on cancellation (line 150)

---

## Common Test Gaps Across All Workers

### 1. **Concurrent Job Processing**
- No tests verify worker behavior with multiple jobs in queue
- No tests for race conditions in shutdown logic
- No tests for job interleaving scenarios

### 2. **Queue Blocking Behavior**
- No tests verify workers actually block on empty queue (not tight polling)
- No tests measure CPU usage on empty queue
- No tests for queue timeout edge cases

### 3. **Shutdown Sequence Testing**
```python
# All workers follow this pattern:
while self.running and (shutdown_event is None or not shutdown_event.is_set()):
    try:
        job_data = await self.queue.dequeue(...)
        if job_data:
            if shutdown_event and shutdown_event.is_set():
                await self.queue.enqueue(..., job)  # Requeue
                break
```
**Missing tests:**
- Shutdown during dequeue (blocking call)
- Shutdown between dequeue and processing
- Shutdown during processing
- Shutdown during metrics update

### 4. **Metrics Correctness**
- No tests verify metric labels are correct (`worker_name="pii"` vs `"processing"` vs `"timeout"`)
- No tests verify error_type in metrics matches exception class name
- No tests verify counters are monotonically increasing

### 5. **Service Dependency Injection**
- No tests verify workers fail gracefully if service initialization fails
- No tests for partial service availability (e.g., Redis up, S3 down)

### 6. **Memory Leak Prevention**
- No tests for worker running for extended periods
- No tests verify old jobs are garbage collected
- No tests for metric cardinality explosion

---

## Real Production Bugs These Tests Would Miss

### Scenario 1: Job Loss on Shutdown
**Code:** PIIWorker line 76-80, ProcessingWorker line 84-88
```python
if shutdown_event and shutdown_event.is_set():
    await self.queue.enqueue(PROCESSING_QUEUE, job)  # BUG: What if enqueue fails?
    break
```
**Missing Test:** Worker should retry enqueue or mark job as failed
**Impact:** Jobs lost during deployment rollouts

### Scenario 2: Tight Error Loop
**Code:** All workers
```python
except Exception as e:
    await asyncio.sleep(settings.worker_error_sleep_seconds)  # BUG: What if sleep=0?
```
**Missing Test:** Verify sleep > 0 or exponential backoff
**Impact:** CPU spike, Redis connection exhaustion

### Scenario 3: Metrics Mislabeling
**Code:** ProcessingWorker line 87-89
```python
worker_jobs_processed_total.labels(
    worker_name="processing", result="success"  # BUG: Hardcoded string
).inc()
```
**Missing Test:** Verify label matches worker name constant
**Impact:** Incorrect monitoring alerts

### Scenario 4: Double Processing
**Code:** ProcessingWorker line 84-88
```python
if shutdown_event and shutdown_event.is_set():
    await self.queue.enqueue(PROCESSING_QUEUE, job)  # BUG: Job might be processed twice
    break
```
**Missing Test:** Verify job state prevents double processing
**Impact:** Duplicate S3 uploads, wasted Bedrock tokens

### Scenario 5: Presidio Cold Start
**Code:** PIIWorker line 138-140
```python
get_pii_analyzer()  # BUG: What if this fails?
```
**Missing Test:** Verify worker fails fast if Presidio fails to load
**Impact:** First request timeout (30s+), poor UX

### Scenario 6: Task Scheduling Drift
**Code:** TimeoutWorker line 98-130
```python
if self._should_run_task(self.last_approval_check, settings.approval_check_interval_seconds):
    await self._run_approval_check()
    self.last_approval_check = current_time  # BUG: Updates even if task fails
```
**Missing Test:** Verify timestamp updated only on success
**Impact:** Approval timeouts not processed, jobs stuck

---

## Recommendations

### Priority 1: Create Unit Tests Directory
```bash
mkdir -p tests/unit/workers/
touch tests/unit/workers/__init__.py
```

### Priority 2: Critical Tests to Add

**For PIIWorker:**
1. Test shutdown requeueing (lines 76-80)
2. Test error metrics increment (lines 98-103)
3. Test Presidio pre-loading (lines 138-140)
4. Test queue timeout behavior (line 69)

**For ProcessingWorker:**
1. Test shutdown requeueing (lines 84-88)
2. Test processing service called (line 92)
3. Test success metric incremented (lines 95-97)
4. Test error recovery loop (lines 103-113)

**For TimeoutWorker:**
1. Test main loop error recovery (lines 135-142)
2. Test task timestamp updated only on success
3. Test all tasks run on first iteration
4. Test task failures don't block other tasks

### Priority 3: Test Patterns to Follow

**Use existing service test patterns:**
- See `/tests/unit/services/test_queue_service.py` for queue testing patterns
- Use `mock_redis_client`, `mock_s3_client` fixtures from `tests/conftest_fixtures/clients.py`
- Use data factories from `tests/conftest_fixtures/data_factories.py`

**Example test structure:**
```python
# tests/unit/workers/test_pii_worker.py

@pytest.mark.asyncio
async def test_pii_worker_requeues_job_on_shutdown():
    """Test worker requeues job when shutdown requested mid-processing."""
    mock_queue = AsyncMock()
    worker = PIIWorker(
        storage_service=AsyncMock(),
        queue_service=mock_queue,
        job_service=AsyncMock()
    )

    job_data = {"job_id": "test-123", "s3_key": "temp/test.pdf"}
    mock_queue.dequeue.return_value = job_data

    shutdown_event = asyncio.Event()

    # Start worker
    task = asyncio.create_task(worker.start(shutdown_event))
    await asyncio.sleep(0.1)  # Let it dequeue

    # Signal shutdown
    shutdown_event.set()
    await task

    # Verify job was requeued
    mock_queue.enqueue.assert_called_once_with(PII_QUEUE, ANY)
```

---

## Test Quality Issues in Existing Integration Tests

### test_processing_worker.py

**Line 15:** Test marked as `@pytest.mark.unit` but in `/tests/integration/` directory
```python
pytestmark = pytest.mark.unit  # WRONG: Should be pytest.mark.integration
```

**Lines 99-104:** Test doesn't verify processing service is called
```python
worker.processing_service.process_document = AsyncMock()
await worker.processing_service.process_document(job)
# MISSING: worker.processing_service.process_document.assert_called_once_with(job)
```

**Lines 252-258:** Weak assertion - should verify gauge value
```python
calls = mock_gauge.labels.return_value.set.call_args_list
assert calls[0][0][0] == 1  # WEAK: Should verify labels too
```

### test_timeout_worker.py

**Lines 183-209:** Test doesn't verify worker actually stopped
```python
timeout_worker.stop()
# Wait for worker to finish
try:
    await asyncio.wait_for(worker_task, timeout=1.0)
except TimeoutError:
    worker_task.cancel()  # BUG: Test passes even if worker didn't stop
# MISSING: assert timeout_worker.running is False
```

**Lines 232-258:** Test "recovers_from_task_errors" but uses timeout fallback
```python
try:
    await asyncio.wait_for(worker_task, timeout=2.0)
except TimeoutError:
    worker_task.cancel()  # BUG: Test passes even if worker hung
```

---

## Conclusion

The Workers layer is **critically under-tested**. While ProcessingWorker and TimeoutWorker have some integration tests, they miss critical failure modes. PIIWorker has **zero tests**. The existing tests would **fail to catch**:

1. **Job loss on shutdown** (no requeueing tests)
2. **Tight error loops** (no error sleep tests)
3. **Metrics corruption** (no label verification)
4. **Double processing** (no idempotency tests)
5. **Cold start failures** (no initialization tests)
6. **Task scheduling drift** (no timestamp update tests)

**Recommendation:** Before merging any worker changes, add comprehensive unit tests following the patterns in `/tests/unit/services/`. Focus on the critical paths identified above, especially shutdown/requeueing logic and error handling.
