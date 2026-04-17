# S3 resilience design

Every S3 operation sits behind two protections: exponential-backoff retry and a circuit breaker. Together they handle two different failure modes. For the recipe to wire a new operation up, see [how to add a new S3 operation](../how-to/add-s3-operations.md).

## Retry handles transient errors

S3 has a well-documented set of "try again in a moment" errors. Retry attempts those up to 3 times with 1s / 2s / 4s exponential backoff:

- **Retriable errors**: `ServiceUnavailable`, `SlowDown`, `RequestTimeout`, `InternalError`, `ThrottlingException`, `RequestThrottled`, `TooManyRequestsException`, `ProvisionedThroughputExceededException`
- **Retriable HTTP codes**: 408, 429, 500, 502, 503, 504
- **Non-retriable errors**: `NoSuchKey`, `NoSuchBucket`, `AccessDenied`, `InvalidRequest`, `InvalidArgument`, `MalformedXML`, `InvalidBucketName` — retrying these won't help; fail fast

Boto3's adaptive retry is also enabled, which adds client-side rate limiting when S3 pushes back.

## Circuit breakers handle sustained degradation

Retry is pointless when S3 is actually down. Three consecutive 3-retry chains of `SlowDown` isn't a transient blip — it's a signal to stop hammering and back off. That's the circuit breaker's job.

**Thresholds** (tuned for S3's usual behaviour; tweak in `src/utils/circuit_breaker.py`):

- 5 consecutive failures → circuit opens
- 60 seconds in open state before we try again
- 2 successes in half-open → circuit closes
- Half-open allows 1 concurrent request (probe, not resume)

**States:**

- **CLOSED** — normal, requests pass through
- **OPEN** — failing fast, every request raises `CircuitBreakerOpen` without attempting S3
- **HALF_OPEN** — probing recovery with one request at a time

## Why StorageService has breakers but S3CleanupService does not

Two different reliability requirements:

- **StorageService** handles critical-path uploads and downloads. If it fails, a job fails — the user sees an error. We want fast-fail behaviour so the user gets a retry-able 503 in hundreds of milliseconds, not a 30-second timeout after 3 retry attempts. Circuit breakers give us that.
- **S3CleanupService** handles best-effort deletes (temp files, expired results). If a delete fails, the file lingers briefly and gets picked up on the next sweep. There's no user waiting for it. Adding a circuit breaker here would just add dead code — nothing downstream cares about the failure.

Putting a circuit breaker on cleanup would pretend the delete matters more than it does. The absence of one is deliberate.

## Available metrics

The metrics service exposes these to Prometheus:

- `s3_operations_total{operation, bucket, result}` — `result` is `success` / `error` / `circuit_open`
- `s3_operation_duration_seconds{operation, bucket}` — latency histogram
- `s3_circuit_breaker_state{circuit_name}` — 0=closed, 1=half-open, 2=open
- `s3_retry_attempts_total{operation, attempt}` — retry attempt counters

Prometheus scrapes `/metrics` every 15 seconds, so these values are queryable in Grafana's Explore view. For a quick look without a chart, hit the endpoint directly:

```bash
curl http://localhost:8080/metrics | grep s3_operations_total
curl http://localhost:8080/metrics | grep s3_circuit_breaker_state
```

## Monitoring in practice

The common failure modes, in order of likelihood:

1. **Floci container restart in local dev** — circuit opens, 60s later closes as Floci recovers. `s3_circuit_breaker_state` tracks this in Prometheus; the `circuit` log lines on the api-gateway container tell the same story with more detail.
2. **ECS IAM role drift in production** — manifests as `AccessDenied` (non-retriable), surfaces as user-visible errors. Check CloudWatch for the IAM event.
3. **Region-wide S3 throttling** — very rare but catastrophic. Circuit breakers keep the service up in read-only mode while you coordinate with AWS support.

For the recipe to wire new S3 operations into this system, see [how to add a new S3 operation](../how-to/add-s3-operations.md).
