# S3 Operations Resilience

All S3 operations use exponential backoff retry and circuit breakers to handle transient failures gracefully.

## Retry Behavior

S3 operations automatically retry on transient errors:

- **Retriable errors**: ServiceUnavailable, SlowDown, RequestTimeout, InternalError, ThrottlingException, RequestThrottled, TooManyRequestsException, ProvisionedThroughputExceededException
- **Retriable HTTP codes**: 408, 429, 500, 502, 503, 504
- **Non-retriable errors**: NoSuchKey, NoSuchBucket, AccessDenied, InvalidRequest, InvalidArgument, MalformedXML, InvalidBucketName
- **Max attempts**: 3 (with exponential backoff: 1s, 2s, 4s)
- **Boto3 adaptive retry**: Enabled (intelligent client-side rate limiting)

## Circuit Breakers

Circuit breakers prevent cascading failures when S3 is degraded:

- **Failure threshold**: 5 consecutive failures → circuit opens
- **Timeout**: 60 seconds before testing recovery
- **Recovery**: 2 successes in half-open → circuit closes
- **Half-open max calls**: 1 concurrent request allowed during recovery testing

### Service Split

- **StorageService** - Has `upload_circuit` and `download_circuit` for critical upload/download operations
- **S3CleanupService** - Has **NO circuit breakers** by design (cleanup is best-effort, non-blocking)

### Circuit States

- **CLOSED**: Normal operation, requests pass through
- **OPEN**: Service failing, requests blocked immediately (raises `CircuitBreakerOpen`)
- **HALF_OPEN**: Testing recovery, limited requests allowed

## Monitoring S3 Health

View S3 health in Prometheus metrics:

```bash
# Check S3 operation success/failure rates
curl http://localhost:8080/metrics | grep s3_operations_total

# Monitor circuit breaker states (0=closed, 1=half-open, 2=open)
curl http://localhost:8080/metrics | grep s3_circuit_breaker_state

# Track operation latency
curl http://localhost:8080/metrics | grep s3_operation_duration_seconds
```

### Available Metrics

- `s3_operations_total{operation, bucket, result}` - Total operations (result: success/error/circuit_open)
- `s3_operation_duration_seconds{operation, bucket}` - Operation latency histogram
- `s3_circuit_breaker_state{circuit_name}` - Circuit state (s3-upload, s3-download, s3-delete)
- `s3_retry_attempts_total{operation, attempt}` - Retry attempt counters

## Adding New S3 Operations

When adding new StorageService methods that call S3:

### 1. Choose appropriate circuit breaker

- `self.upload_circuit` - For put_object, upload_fileobj
- `self.download_circuit` - For get_object, head_object, list_objects
- For delete operations, use `S3CleanupService` (no circuit breakers - cleanup is best-effort)

### 2. Check circuit before operation

```python
self.upload_circuit.check_state()
if self.upload_circuit.is_open:
    raise CircuitBreakerOpen("S3 upload circuit breaker is open")
```

### 3. Wrap S3 call with retry

```python
try:
    result = await retry_with_backoff_for_sync_func(
        lambda: self.s3_client.put_object(...),
        max_attempts=3,
        base_delay=1.0,
        operation_name="upload result"
    )
    self.upload_circuit.record_success()
    return result
except CircuitBreakerOpen:
    raise
except Exception:
    self.upload_circuit.record_failure()
    raise
```

### 4. Add metrics (optional but recommended)

```python
from ..services.metrics_service import s3_operations_total, s3_operation_duration_seconds
import time

start_time = time.time()
try:
    # ... S3 operation ...
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='success').inc()
    s3_operation_duration_seconds.labels(operation='put_object', bucket=bucket).observe(time.time() - start_time)
except CircuitBreakerOpen:
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='circuit_open').inc()
    raise
except Exception:
    s3_operations_total.labels(operation='put_object', bucket=bucket, result='error').inc()
    raise
```

## Testing S3 Resilience

### Unit Tests

Mock S3 errors to test retry and circuit breaker logic:

```python
from src.utils.circuit_breaker import CircuitBreakerOpen
from botocore.exceptions import ClientError

# Test retry on transient error
mock_s3.put_object.side_effect = [
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    ClientError({'Error': {'Code': 'SlowDown'}}, 'put_object'),
    {'ETag': 'success'}  # Succeeds on 3rd attempt
]

# Test circuit breaker opens after failures
for _ in range(5):
    storage.upload_circuit.record_failure()
with pytest.raises(CircuitBreakerOpen):
    await storage.upload_result(job_id, content, 'md')
```

### Integration Tests

Use testcontainers with Floci to simulate real S3 behavior. See `tests/unit/utils/test_circuit_breaker.py` and `tests/unit/services/test_storage_service.py` for examples.
