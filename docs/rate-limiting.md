# Rate Limiting Documentation

## Overview

Equalify Reflow implements **Redis-based sliding window rate limiting** to prevent abuse and control processing costs while maintaining system availability.

## Use Case Context

The rate-limit defaults below were calibrated for the original UIC faculty and accessibility-team deployment — a small, trusted user population uploading course materials in batches. They are **not** tuned for a public API with unknown traffic. Anyone deploying Reflow for a different user population should revisit these numbers.

- **Traffic Pattern**: Batch uploads of course materials
- **Cost Sensitivity**: ~$0.20/document AI processing cost
- **Processing Time**: 2-8 minutes per document
- **Critical Requirement**: Prevent accidental cost overruns from automated scripts or misconfigured integrations

## Rate Limit Tiers

### 1. Per-IP Submission Limits
**Limit**: 10 submissions per hour per IP address

**Purpose**: Prevent individual user abuse or runaway scripts

**Use Case**: Faculty member accidentally starts infinite upload loop

**Response**: HTTP 429 with retry-after header

### 2. Per-IP Status Check Limits
**Limit**: 100 status checks per hour per IP address

**Purpose**: Prevent aggressive polling storms

**Use Case**: Frontend with 1-second polling interval

**Response**: HTTP 429 with retry-after header

### 3. Global Submission Limits
**Limit**: 1000 submissions per day (system-wide)

**Purpose**: Cost control and capacity planning

**Use Case**: Prevent system-wide processing cost explosion

**Response**: HTTP 429 with retry-after header

## Implementation Details

### Algorithm: Sliding Window

Uses Redis Sorted Sets with timestamps as scores:

```python
# Each request adds entry with current timestamp
ZADD rate_limit_key {timestamp} {request_id}

# Remove entries outside time window
ZREMRANGEBYSCORE rate_limit_key 0 {window_start}

# Count remaining entries
count = ZCARD rate_limit_key

# Allow if under limit
if count < limit:
    ZADD rate_limit_key {now} {request_id}
    return ALLOWED
else:
    return DENIED
```

**Advantages**:
- Precise sliding window (not fixed buckets)
- Distributed rate limiting across multiple ECS tasks
- Automatic cleanup via ZREMRANGEBYSCORE
- Atomic operations prevent race conditions

### Fail-Open Philosophy

**Critical Design Decision**: Rate limiter **fails open** when Redis is unavailable.

**Rationale**:
- Availability > strict rate limiting for academic use case
- Faculty deadlines are inflexible
- Rate limiter should not be single point of failure
- Monitoring will alert on Redis issues

**Implementation**:
```python
try:
    rate_limiter = get_rate_limit_service()
    allowed, retry_after = await rate_limiter.check_submit_rate_limit(ip)
    if not allowed:
        return 429
except Exception:
    logger.error("Rate limiter unavailable")
    return ALLOW_REQUEST  # Fail open
```

## API Response Headers

### Rate Limit Information Headers

All rate-limited endpoints include these headers:

```http
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1704124800
```

**Header Descriptions**:
- `X-RateLimit-Limit`: Maximum requests allowed in window
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

### Rate Limit Exceeded Response

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

## Exempt Endpoints

The following endpoints are **NOT** rate limited:

- `/health` - Health checks
- `/health/ready` - Readiness checks
- `/` - Root endpoint
- `/docs` - OpenAPI documentation
- `/redoc` - ReDoc documentation
- `/openapi.json` - OpenAPI schema

**Rationale**: Infrastructure monitoring and documentation must always be available.

## Redis Data Structures

### Rate Limit Keys

```
# Per-IP submission limits
eq-pdf:ratelimit:submit:ip:{client_ip}

# Per-IP status check limits
eq-pdf:ratelimit:status:ip:{client_ip}

# Global submission limits
eq-pdf:ratelimit:submit:global
```

### Data Format

```python
# Sorted set with timestamp scores
ZADD eq-pdf:ratelimit:submit:ip:192.168.1.1 1704120000.123 "req-1"
ZADD eq-pdf:ratelimit:submit:ip:192.168.1.1 1704120005.456 "req-2"
ZADD eq-pdf:ratelimit:submit:ip:192.168.1.1 1704120010.789 "req-3"

# Automatic expiration (cleanup)
EXPIRE eq-pdf:ratelimit:submit:ip:192.168.1.1 3600
```

## Client IP Detection

### Reverse Proxy Support

Rate limiting correctly handles reverse proxies (AWS ALB, Nginx, Cloudflare):

```python
# Priority order:
1. X-Forwarded-For: 203.0.113.1, 198.51.100.1  # Take first IP
2. X-Real-IP: 203.0.113.1
3. request.client.host  # Direct connection
```

**Security Note**: In production, configure ALB/reverse proxy to set trusted `X-Forwarded-For` headers.

## Administrative Operations

### Reset Rate Limit for User

```python
from src.services.rate_limit_service import RateLimitService

# Reset submission limit for specific IP
await rate_limiter.reset_rate_limit("192.168.1.100", "submit")

# Reset status check limit
await rate_limiter.reset_rate_limit("192.168.1.100", "status")
```

### Check Remaining Quota

```python
# Get quota info for IP
quota = await rate_limiter.get_remaining_quota("192.168.1.100", "submit")

# Returns:
{
    "limit": 10,
    "remaining": 3,
    "reset_at": 1704124800,
    "window_seconds": 3600
}
```

## Configuration

### Environment Variables

```bash
# Redis connection (required for rate limiting)
REDIS_URL=redis://redis:6379
REDIS_MAX_CONNECTIONS=10
```

### Adjusting Limits

Limits are configured in `src/services/rate_limit_service.py`:

```python
class RateLimitService:
    # Adjust these values based on usage patterns
    SUBMIT_PER_IP_LIMIT = 10        # Submissions per hour per IP
    SUBMIT_PER_IP_WINDOW = 3600     # 1 hour

    STATUS_PER_IP_LIMIT = 100       # Status checks per hour per IP
    STATUS_PER_IP_WINDOW = 3600     # 1 hour

    GLOBAL_SUBMIT_LIMIT = 1000      # Global submissions per day
    GLOBAL_SUBMIT_WINDOW = 86400    # 24 hours
```

## Monitoring

### Key Metrics to Track

1. **Rate Limit Hits**: How often 429 responses occur
2. **Top Rate-Limited IPs**: Identify problematic clients
3. **Global Quota Usage**: Track toward daily limit
4. **Redis Availability**: Monitor fail-open scenarios

### Recommended CloudWatch Alarms

```yaml
- Metric: HTTP 429 responses > 100/hour
  Action: Alert operations team

- Metric: Global quota > 80% of daily limit
  Action: Alert stakeholders

- Metric: Rate limiter exceptions > 10/hour
  Action: Check Redis health
```

## Testing

### Unit Tests

Located in `tests/services/test_rate_limit_service.py`:

- Sliding window algorithm correctness
- Per-IP and global limit enforcement
- Fail-open behavior on Redis errors
- Quota calculation accuracy

### Integration Tests

Located in `tests/test_rate_limit_middleware.py`:

- Middleware installation verification
- Exempt endpoint behavior
- Fail-open on Redis unavailability

### Manual Testing

```bash
# Test submission rate limit (11 requests should trigger limit)
for i in {1..11}; do
  curl -X POST http://localhost:8080/api/v1/documents/submit \
    -F "file=@test.pdf" \
    -H "X-Forwarded-For: 203.0.113.1" \
    -w "\n%{http_code}\n"
done

# Expected: First 10 succeed (201), 11th fails (429)
```

## Production Recommendations

### 1. Enable Redis Persistence

Rate limiting uses ephemeral data, but consider persistence for monitoring:

```yaml
# redis.conf
save 60 1  # Snapshot every 60s if 1 key changed
```

### 2. Configure Trusted Proxies

Ensure ALB/reverse proxy sets correct `X-Forwarded-For`:

```python
# AWS ALB automatically sets X-Forwarded-For
# No additional configuration needed
```

### 3. Monitor and Adjust

**Week 1**: Log all rate limit hits without blocking
**Week 2-4**: Gradually enforce limits based on actual usage
**Ongoing**: Adjust limits based on semester patterns

### 4. Whitelist Internal Systems

For automated integrations:

```python
# Add to middleware:
WHITELISTED_IPS = ["10.0.0.0/8"]  # Internal network
if client_ip in whitelist:
    return await call_next(request)
```

## Future Enhancements

1. **User-Based Rate Limiting**: Limit by authenticated user ID instead of IP
2. **Dynamic Limits**: Adjust limits based on system load
3. **Rate Limit Bypass Tokens**: Administrative bypass for urgent requests
4. **Distributed Rate Limiting**: Share limits across multiple data centers
5. **Analytics Dashboard**: Real-time rate limit visualization

## FAQ

### Q: What happens if Redis goes down?

**A**: Rate limiter fails open - all requests are allowed. CloudWatch alarm should alert operations team.

### Q: Can users see their rate limit status?

**A**: Yes, via `X-RateLimit-*` headers on every response.

### Q: How do I temporarily increase limits for a user?

**A**: Use administrative `reset_rate_limit()` function or whitelist their IP.

### Q: Do rate limits affect health checks?

**A**: No, health checks are explicitly exempted.

### Q: What if multiple users share an IP (NAT)?

**A**: Consider implementing user-based rate limiting (future enhancement).

### Q: How accurate is the sliding window?

**A**: Very accurate - uses actual timestamps, not fixed buckets.

---

## Implementation Files

- Service: `src/services/rate_limit_service.py`
- Middleware: `src/middleware/rate_limit.py`
- Tests: `tests/services/test_rate_limit_service.py`
- Dependency Injection: `src/dependencies.py::get_rate_limit_service`

## Related Documentation

- [Architecture Overview](../docs/architecture.md)
- [Redis Data Structures](../docs/redis-schema.md)
- [API Documentation](http://localhost:8080/docs)
