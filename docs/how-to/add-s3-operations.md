# How to add a new S3 operation

Use this recipe when adding a `StorageService` method that calls S3. For the design rationale (why circuit breakers, why `S3CleanupService` is excluded), see [S3 resilience](../explanation/s3-resilience.md).

## 1. Pick the right circuit breaker

| Operation class | Use | File |
|---|---|---|
| Uploads (`put_object`, `upload_fileobj`) | `self.upload_circuit` | `src/services/storage_service.py` |
| Reads (`get_object`, `head_object`, `list_objects`) | `self.download_circuit` | same |
| Deletes | Put it in `S3CleanupService` | `src/services/s3_cleanup_service.py` |

`S3CleanupService` has **no** circuit breakers on purpose — cleanup is best-effort and non-blocking. If you need a delete operation that's critical-path, that's a design-review conversation, not a code change.

## 2. Check the circuit before the operation

```python
self.upload_circuit.check_state()
if self.upload_circuit.is_open:
    raise CircuitBreakerOpen("S3 upload circuit breaker is open")
```

## 3. Wrap the S3 call with retry

```python
from src.utils.retry_helpers import retry_with_backoff_for_sync_func

try:
    result = await retry_with_backoff_for_sync_func(
        lambda: self.s3_client.put_object(...),
        max_attempts=3,
        base_delay=1.0,
        operation_name="upload result",
    )
    self.upload_circuit.record_success()
    return result
except CircuitBreakerOpen:
    raise
except Exception:
    self.upload_circuit.record_failure()
    raise
```

Defaults: 3 attempts with 1s / 2s / 4s backoff. Boto3 adaptive retry is already enabled in the client factory.

## 4. Add metrics (recommended)

```python
from ..services.metrics_service import (
    s3_operations_total,
    s3_operation_duration_seconds,
)
import time

start = time.time()
try:
    # ... S3 operation ...
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='success').inc()
    s3_operation_duration_seconds.labels(operation='put_object', bucket=bucket).observe(time.time() - start)
except CircuitBreakerOpen:
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='circuit_open').inc()
    raise
except Exception:
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='error').inc()
    raise
```

These metrics are exposed to Prometheus at `/metrics`. There is no dedicated Grafana dashboard for S3 operations yet — query them via PromQL in Grafana's Explore view, or scrape `/metrics` directly. Filing S3 dashboards is tracked as a follow-up.

## 5. Test retry and circuit behaviour

Unit tests mock S3 error responses:

```python
from botocore.exceptions import ClientError
from src.utils.circuit_breaker import CircuitBreakerOpen

# Retry: fails twice, succeeds on third attempt
mock_s3.put_object.side_effect = [
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    {'ETag': 'success'},
]

# Circuit opens after 5 consecutive failures
for _ in range(5):
    storage.upload_circuit.record_failure()
with pytest.raises(CircuitBreakerOpen):
    await storage.upload_result(job_id, content, 'md')
```

Integration tests use Floci to exercise the real wire protocol — see `tests/unit/utils/test_circuit_breaker.py` and `tests/unit/services/test_storage_service.py` for examples.
