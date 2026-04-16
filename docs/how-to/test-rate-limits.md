# How to test rate limits locally

The running stack respects the configured limits. To observe a 429 response, submit faster than the per-IP tier permits.

## Trigger a submission 429

The default per-IP submission limit is 10 requests per hour. Eleven requests from the same `X-Forwarded-For` IP should give you one 429:

```bash
for i in {1..11}; do
  curl -X POST http://localhost:8080/api/v1/documents/submit \
    -F "file=@tests/fixtures/small.pdf" \
    -H "X-API-Key: $API_KEY" \
    -H "X-Forwarded-For: 203.0.113.1" \
    -w "\n%{http_code}\n" \
    -o /dev/null -s
done
# Expected: ten 201s, one 429
```

## Inspect rate-limit state directly

```bash
make redis-cli
> ZRANGE eq-pdf:ratelimit:submit:ip:203.0.113.1 0 -1 WITHSCORES
> TTL   eq-pdf:ratelimit:submit:ip:203.0.113.1
```

## Clear a tier for a specific IP

If you've tripped a limit during development:

```bash
make redis-cli
> DEL eq-pdf:ratelimit:submit:ip:203.0.113.1
```

Or programmatically from the Python shell (`make shell`):

```python
from src.services.rate_limit_service import RateLimitService
await rate_limiter.reset_rate_limit("203.0.113.1", "submit")
```

## Verify fail-open behaviour

Stop Redis and confirm the rate limiter allows requests (logs should show `rate_limiter_unavailable`):

```bash
docker compose stop redis
curl -X POST http://localhost:8080/api/v1/documents/submit \
  -F "file=@tests/fixtures/small.pdf" \
  -H "X-API-Key: $API_KEY"
# Expected: 201, plus a WARN log line noting the rate limiter failed open
docker compose start redis
```

For the rationale behind fail-open, see [rate limiting design](../explanation/rate-limiting.md).
