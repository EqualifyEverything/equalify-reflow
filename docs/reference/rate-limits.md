# Rate limits reference

Configured limits, Redis key patterns, and response headers. For the why, see [rate limiting design](../explanation/rate-limiting.md).

## Configured tiers

| Tier | Scope | Limit | Window | Purpose |
|---|---|---|---|---|
| Per-IP submission | `X-Forwarded-For` first IP | 10 | 1 hour | Individual abuse / runaway scripts |
| Per-IP status check | `X-Forwarded-For` first IP | 100 | 1 hour | Aggressive polling |
| Global submission | System-wide | 1000 | 24 hours | Cost ceiling |

All tiers return **HTTP 429** with a `Retry-After` header on violation. Adjust values in `src/services/rate_limit_service.py`:

```python
class RateLimitService:
    SUBMIT_PER_IP_LIMIT = 10        # Submissions per hour per IP
    SUBMIT_PER_IP_WINDOW = 3600

    STATUS_PER_IP_LIMIT = 100       # Status checks per hour per IP
    STATUS_PER_IP_WINDOW = 3600

    GLOBAL_SUBMIT_LIMIT = 1000      # Global submissions per day
    GLOBAL_SUBMIT_WINDOW = 86400
```

## Response headers

Every rate-limited response includes:

```http
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1704124800
```

On 429:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 300
X-RateLimit-Remaining: 0

{
  "detail": "Rate limit exceeded for submission",
  "retry_after": 300,
  "limit_type": "submission"
}
```

## Exempt endpoints

The following bypass rate limiting:

- `/` — viewer SPA
- `/health`, `/health/ready` — load-balancer checks
- `/docs`, `/redoc`, `/openapi.json` — API documentation
- `/metrics` — Prometheus scrape

## Redis keys

```
eq-pdf:ratelimit:submit:ip:{client_ip}
eq-pdf:ratelimit:status:ip:{client_ip}
eq-pdf:ratelimit:submit:global
```

Each key is a sorted set with timestamp scores and an `EXPIRE` cleanup TTL matching the window. Example payload:

```
ZADD eq-pdf:ratelimit:submit:ip:192.168.1.1 1704120000.123 "req-1"
ZADD eq-pdf:ratelimit:submit:ip:192.168.1.1 1704120005.456 "req-2"
EXPIRE eq-pdf:ratelimit:submit:ip:192.168.1.1 3600
```

## Client IP detection

Priority order in the middleware:

1. `X-Forwarded-For` — first IP in the chain
2. `X-Real-IP`
3. `request.client.host` — direct connection fallback

In production, ensure the ALB or reverse proxy sets trusted `X-Forwarded-For` headers.

## Environment variables

```bash
REDIS_URL=redis://redis:6379        # Required
REDIS_MAX_CONNECTIONS=10
```

## Administrative operations

```python
from src.services.rate_limit_service import RateLimitService

# Reset a tier for a specific IP
await rate_limiter.reset_rate_limit("192.168.1.100", "submit")
await rate_limiter.reset_rate_limit("192.168.1.100", "status")

# Check remaining quota
quota = await rate_limiter.get_remaining_quota("192.168.1.100", "submit")
# → {"limit": 10, "remaining": 3, "reset_at": 1704124800, "window_seconds": 3600}
```

## Implementation

| File | Role |
|---|---|
| `src/services/rate_limit_service.py` | Sliding-window logic, Redis operations |
| `src/middleware/rate_limit.py` | FastAPI middleware, fail-open handling |
| `src/dependencies.py::get_rate_limit_service` | DI factory |
| `tests/unit/services/test_rate_limit_service.py` | Algorithm tests |
| `tests/unit/middleware/test_rate_limit.py` | Middleware tests (exemption, fail-open) |
