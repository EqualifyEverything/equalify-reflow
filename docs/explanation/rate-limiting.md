# Rate limiting design

Rate limiting exists for two reasons, which pull in different directions: **block abuse** and **control cost**. The current defaults are tuned for the original UIC faculty-and-accessibility-team use case (small, trusted user population uploading course materials in batches). Any deployment with a different audience should revisit the numbers.

For configured limits, Redis keys, headers, and env vars, see the [rate limits reference](../reference/rate-limits.md).

## Why a sliding window (not a fixed bucket)

Fixed-bucket rate limiting has a classic edge case: a user can burst `2 × limit` requests across a bucket boundary (full quota at second 59, full quota again at second 60 of a one-minute window). For a cost-sensitive system where one "request" costs ~$0.20 of AI processing, that edge case is expensive.

The sliding window implementation uses Redis sorted sets:

```
ZADD   rate_limit_key {timestamp} {request_id}   # record the request
ZREMRANGEBYSCORE rate_limit_key 0 {window_start}  # drop expired entries
ZCARD  rate_limit_key                             # count remaining
```

`ZCARD` reflects actual request times, not bucket alignment. Redis atomicity prevents race conditions across multiple API tasks. Cost: one small sorted set per rate-limit scope, auto-expired via `EXPIRE`.

## Why fail-open

When Redis is unavailable, the rate limiter **allows the request** rather than blocking it. This looks counterintuitive — surely safer to block? — but the original use case changes the math:

- **Faculty deadlines are inflexible.** A syllabus that doesn't convert today is a problem for a human course; a missed rate limit is not.
- **Rate limiter should not be a single point of failure.** If Redis goes down, the AI pipeline still works (S3 + Bedrock are independent). Blocking at the rate limit when the rest of the system is healthy is a self-inflicted outage.
- **Cost protection has other layers.** Global quotas alert separately; CloudWatch watches for 429 storms. Even a Redis outage can't cause infinite cost overrun — processing is slow enough (2–8 min/doc) that the pipeline itself is rate-limiting.

The fail-open path logs a WARN line in the api-gateway when Redis is unreachable, and the underlying Redis health shows up in the existing Grafana dashboards (System Overview, Worker Health). There is no dedicated rate-limiter dashboard yet — if you're running Reflow in a context where fail-open windows matter, wire an alert off the WARN log line (or a Prometheus counter if you add one) before relying on fail-open as a safety valve.

For a different deployment with a less trusted user population or tighter cost controls, invert this: return 503 when Redis is unreachable and configure stricter limits. The code path is in `src/middleware/rate_limit.py`; the exception catch is the hook.

## Why three separate tiers

Per-IP submission, per-IP status checks, and global submission limits address different failure modes:

- **Per-IP submission (10/hr)** — catches a single misbehaving integration (faculty member accidentally loops, a misconfigured script). Low enough to notice quickly; high enough that legitimate batch uploads don't hit it.
- **Per-IP status polling (100/hr)** — status polls shouldn't cost money but can flood Redis. This limit catches frontends with aggressive poll intervals without throttling submit.
- **Global submission (1000/day)** — the cost cap. If many legitimate IPs each submit under their per-IP limit but collectively blow through the budget, this is the backstop.

Each tier uses its own Redis key-space and its own counter, so a hot IP never depletes another IP's quota. The global tier is the only cross-cutting one.

## Why some endpoints are exempt

Health checks, `/docs`, `/redoc`, and `/openapi.json` are explicitly exempt from rate limiting. The reason: **infrastructure must always be available**. Load balancers poll `/health` dozens of times per minute. Monitoring scrapes `/metrics`. Documentation is the first thing new users hit. Rate-limiting any of these creates more problems than it solves.
