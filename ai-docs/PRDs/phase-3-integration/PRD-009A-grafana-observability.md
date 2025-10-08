# PRD-009A: Grafana Observability Stack

## Overview
**Epic**: System Monitoring & Observability
**Phase**: 3 - Integration & Demo
**Estimated Effort**: 6-8 hours
**Dependencies**: PRD-008 (Timeout Worker - all workers complete)
**Blocks**: PRD-010 (End-to-End Integration)

## Problem Statement

Currently, the system has no built-in observability for monitoring system health, queue depths, job throughput, or worker performance. When debugging issues or validating performance, developers must:
- Manually query Redis for queue depths
- Check logs for job status
- SSH into containers to inspect state
- No visibility into processing times, error rates, or system load

This lack of observability makes debugging difficult, performance validation impossible, and production operations risky.

**Architecture Note:** We need industry-standard observability using OpenTelemetry, Prometheus, and Grafana - the standard monitoring stack used in production environments.

## Success Criteria
- [ ] Prometheus collecting metrics from FastAPI, Redis, and workers
- [ ] Grafana dashboards for system overview, queues, jobs, and workers
- [ ] Real-time queue depth monitoring
- [ ] Job processing metrics (throughput, latency, success rate)
- [ ] Worker health monitoring (PII, Processing, Timeout workers)
- [ ] System health dashboard (Redis, S3, API status)
- [ ] All metrics accessible at http://localhost:3000 (Grafana)
- [ ] Zero application code changes (instrumentation via middleware)
- [ ] Dev and production compatible (same stack for both environments)

## Technical Requirements

### Observability Stack Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Docker Network                          │
│                                                             │
│  ┌──────────────┐                                          │
│  │ api-gateway  │────┐                                     │
│  │ (FastAPI)    │    │                                     │
│  │ :8000        │    │ Prometheus                          │
│  │              │    │ scrape                              │
│  │ /metrics     │◄───┤ (HTTP pull)                         │
│  └──────────────┘    │                                     │
│                      │                                     │
│  ┌──────────────┐    │    ┌──────────────┐                │
│  │ redis        │────┼───►│ prometheus   │                │
│  │ :6379        │    │    │ :9090        │                │
│  │              │    │    │              │                │
│  │ (exporter)   │────┘    │ Time-series  │                │
│  └──────────────┘         │ database     │                │
│                           └──────┬───────┘                │
│                                  │                         │
│                                  │ PromQL                  │
│                                  │ queries                 │
│                                  ▼                         │
│                           ┌──────────────┐                │
│                           │ grafana      │                │
│                           │ :3000        │◄───── User     │
│                           │              │                │
│                           │ Dashboards   │                │
│                           └──────────────┘                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

**Metrics Collection:**
- OpenTelemetry Python SDK (instrumentation)
- Prometheus Client (metrics export)
- Redis Exporter (Redis metrics)

**Metrics Storage:**
- Prometheus (time-series database)

**Visualization:**
- Grafana (dashboards and alerting)

### Integration Strategy

**1. FastAPI Metrics (OpenTelemetry)**
```python
# src/middleware/metrics.py
"""
OpenTelemetry metrics middleware for FastAPI.

Automatically instruments:
- HTTP request duration
- Request count by endpoint
- Error rate by status code
- Active requests (in-flight)
"""

from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import start_http_server

# Service identification
resource = Resource.create({
    "service.name": "equalify-pdf-api",
    "service.version": "0.1.0",
})

# Prometheus exporter on :8001/metrics
start_http_server(8001)
reader = PrometheusMetricReader()
provider = MeterProvider(resource=resource, metric_readers=[reader])
metrics.set_meter_provider(provider)

meter = metrics.get_meter(__name__)

# Custom metrics
job_processing_time = meter.create_histogram(
    name="job_processing_seconds",
    description="Time to process a job",
    unit="s"
)

queue_depth = meter.create_up_down_counter(
    name="queue_depth",
    description="Number of jobs in queue",
)

worker_status = meter.create_up_down_counter(
    name="worker_active",
    description="Worker active status (1=active, 0=stopped)",
)
```

**2. Custom Application Metrics**
```python
# src/services/metrics_service.py (EXTEND EXISTING)
"""
Enhanced metrics service with Prometheus export.

Extends existing daily metrics with real-time Prometheus counters.
"""

from prometheus_client import Counter, Histogram, Gauge

# Job metrics
jobs_submitted_total = Counter(
    'jobs_submitted_total',
    'Total jobs submitted',
    ['source']  # webhook, api, etc.
)

jobs_completed_total = Counter(
    'jobs_completed_total',
    'Total jobs completed',
    ['status']  # success, failed, denied
)

job_duration_seconds = Histogram(
    'job_duration_seconds',
    'Job processing duration',
    ['stage'],  # pii_scan, processing, total
    buckets=[10, 30, 60, 120, 300, 600]  # 10s to 10min
)

# Queue metrics
queue_depth_gauge = Gauge(
    'queue_depth',
    'Current queue depth',
    ['queue_name']  # pii_scan, processing, approval_pending
)

# Worker metrics
worker_active_gauge = Gauge(
    'worker_active',
    'Worker active status',
    ['worker_name']  # pii, processing, timeout
)

worker_errors_total = Counter(
    'worker_errors_total',
    'Worker error count',
    ['worker_name', 'error_type']
)

# System health
redis_up = Gauge('redis_up', 'Redis connectivity (1=up, 0=down)')
s3_up = Gauge('s3_up', 'S3 connectivity (1=up, 0=down)')
```

**3. Worker Instrumentation**
```python
# src/workers/pii_worker.py (UPDATE EXISTING)
async def start_pii_worker(shutdown_event: asyncio.Event) -> None:
    """PII worker with Prometheus metrics."""
    from src.services.metrics_service import worker_active_gauge, worker_errors_total

    worker_active_gauge.labels(worker_name='pii').set(1)

    try:
        while not shutdown_event.is_set():
            # ... existing logic ...
            pass
    except Exception as e:
        worker_errors_total.labels(worker_name='pii', error_type=type(e).__name__).inc()
        raise
    finally:
        worker_active_gauge.labels(worker_name='pii').set(0)
```

### Docker Compose Integration

**Add to docker-compose.dev.yml:**
```yaml
services:
  # Redis Exporter - Exposes Redis metrics to Prometheus
  redis-exporter:
    image: oliver006/redis_exporter:latest
    container_name: equalify-pdf-redis-exporter
    restart: unless-stopped
    ports:
      - "9121:9121"
    environment:
      - REDIS_ADDR=redis://redis:6379
    depends_on:
      - redis
    networks:
      - equalify-network

  # Prometheus - Metrics collection and storage
  prometheus:
    image: prom/prometheus:latest
    container_name: equalify-pdf-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./infrastructure/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'
    depends_on:
      - api-gateway
      - redis-exporter
    networks:
      - equalify-network

  # Grafana - Metrics visualization
  grafana:
    image: grafana/grafana:latest
    container_name: equalify-pdf-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - ./infrastructure/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./infrastructure/grafana/datasources:/etc/grafana/provisioning/datasources:ro
      - grafana-data:/var/lib/grafana
    depends_on:
      - prometheus
    networks:
      - equalify-network

volumes:
  prometheus-data:
    name: equalify-pdf-prometheus-data
  grafana-data:
    name: equalify-pdf-grafana-data
```

**Update api-gateway service:**
```yaml
services:
  api-gateway:
    # ... existing config ...
    ports:
      - "8000:8000"
      - "8001:8001"  # NEW: Prometheus metrics endpoint
    environment:
      - ENABLE_METRICS=true
      - METRICS_PORT=8001
```

### Configuration Files

**1. Prometheus Configuration**
```yaml
# infrastructure/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # FastAPI application metrics
  - job_name: 'equalify-api'
    static_configs:
      - targets: ['api-gateway:8001']
        labels:
          service: 'api-gateway'
          environment: 'dev'

  # Redis metrics
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
        labels:
          service: 'redis'
          environment: 'dev'

  # Prometheus self-monitoring
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

**2. Grafana Datasource**
```yaml
# infrastructure/grafana/datasources/prometheus.yml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

**3. Grafana Dashboard Provisioning**
```yaml
# infrastructure/grafana/dashboards/dashboard.yml
apiVersion: 1

providers:
  - name: 'Equalify Dashboards'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

### Grafana Dashboards

**Dashboard 1: System Overview**
```json
{
  "title": "Equalify PDF Converter - System Overview",
  "panels": [
    {
      "title": "API Health",
      "type": "stat",
      "targets": [
        {
          "expr": "up{job='equalify-api'}",
          "legendFormat": "API Status"
        },
        {
          "expr": "redis_up",
          "legendFormat": "Redis Status"
        },
        {
          "expr": "s3_up",
          "legendFormat": "S3 Status"
        }
      ]
    },
    {
      "title": "Request Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(http_requests_total[5m])",
          "legendFormat": "{{method}} {{endpoint}}"
        }
      ]
    },
    {
      "title": "Error Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(http_requests_total{status=~\"5..\"}[5m])",
          "legendFormat": "5xx Errors"
        }
      ]
    },
    {
      "title": "Response Time (p95)",
      "type": "graph",
      "targets": [
        {
          "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))",
          "legendFormat": "p95 latency"
        }
      ]
    }
  ]
}
```

**Dashboard 2: Queue Monitoring**
```json
{
  "title": "Equalify PDF Converter - Queue Monitor",
  "panels": [
    {
      "title": "Queue Depths",
      "type": "graph",
      "targets": [
        {
          "expr": "queue_depth{queue_name='pii_scan_queue'}",
          "legendFormat": "PII Scan Queue"
        },
        {
          "expr": "queue_depth{queue_name='processing_queue'}",
          "legendFormat": "Processing Queue"
        },
        {
          "expr": "queue_depth{queue_name='approval_pending_queue'}",
          "legendFormat": "Approval Queue"
        }
      ]
    },
    {
      "title": "Queue Processing Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(jobs_completed_total[5m])",
          "legendFormat": "{{status}}"
        }
      ]
    },
    {
      "title": "Average Queue Wait Time",
      "type": "stat",
      "targets": [
        {
          "expr": "avg(queue_depth) / rate(jobs_completed_total[5m])",
          "legendFormat": "Wait Time (est)"
        }
      ]
    }
  ]
}
```

**Dashboard 3: Job Processing**
```json
{
  "title": "Equalify PDF Converter - Job Processing",
  "panels": [
    {
      "title": "Jobs by Status",
      "type": "piechart",
      "targets": [
        {
          "expr": "jobs_completed_total",
          "legendFormat": "{{status}}"
        }
      ]
    },
    {
      "title": "Processing Duration by Stage",
      "type": "graph",
      "targets": [
        {
          "expr": "histogram_quantile(0.95, rate(job_duration_seconds_bucket[5m]))",
          "legendFormat": "{{stage}} (p95)"
        }
      ]
    },
    {
      "title": "Jobs per Hour",
      "type": "stat",
      "targets": [
        {
          "expr": "rate(jobs_submitted_total[1h]) * 3600",
          "legendFormat": "Submission Rate"
        }
      ]
    },
    {
      "title": "Success Rate",
      "type": "gauge",
      "targets": [
        {
          "expr": "sum(rate(jobs_completed_total{status='success'}[5m])) / sum(rate(jobs_completed_total[5m])) * 100",
          "legendFormat": "Success %"
        }
      ]
    }
  ]
}
```

**Dashboard 4: Worker Health**
```json
{
  "title": "Equalify PDF Converter - Worker Health",
  "panels": [
    {
      "title": "Worker Status",
      "type": "stat",
      "targets": [
        {
          "expr": "worker_active{worker_name='pii'}",
          "legendFormat": "PII Worker"
        },
        {
          "expr": "worker_active{worker_name='processing'}",
          "legendFormat": "Processing Worker"
        },
        {
          "expr": "worker_active{worker_name='timeout'}",
          "legendFormat": "Timeout Worker"
        }
      ]
    },
    {
      "title": "Worker Errors",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(worker_errors_total[5m])",
          "legendFormat": "{{worker_name}} - {{error_type}}"
        }
      ]
    },
    {
      "title": "Redis Operations",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(redis_commands_total[5m])",
          "legendFormat": "{{cmd}}"
        }
      ]
    }
  ]
}
```

## Deliverables

### Files to Create

```
/infrastructure/
  /prometheus/
    prometheus.yml                     # Prometheus configuration
  /grafana/
    /datasources/
      prometheus.yml                   # Grafana datasource config
    /dashboards/
      dashboard.yml                    # Dashboard provisioning
      system-overview.json             # System health dashboard
      queue-monitor.json               # Queue monitoring dashboard
      job-processing.json              # Job metrics dashboard
      worker-health.json               # Worker status dashboard

/src/
  /middleware/
    metrics.py                         # OpenTelemetry instrumentation (NEW)

/pyproject.toml                        # Add metrics dependencies
docker-compose.dev.yml                 # Add Prometheus, Grafana services
.env.dev                               # Add metrics configuration
```

### Files to Update

```
/src/
  main.py                              # Register metrics middleware
  services/metrics_service.py          # Add Prometheus counters
  workers/pii_worker.py                # Add worker metrics
  workers/processing_worker.py         # Add worker metrics
  workers/timeout_worker.py            # Add worker metrics
  services/queue_service.py            # Add queue depth metrics
  services/job_service.py              # Add job lifecycle metrics
```

## Acceptance Criteria

### 1. Infrastructure Setup
- [ ] Prometheus accessible at http://localhost:9090
- [ ] Grafana accessible at http://localhost:3000 (admin/admin)
- [ ] Redis exporter providing metrics
- [ ] All services healthy in docker-compose

### 2. Metrics Collection
- [ ] FastAPI HTTP metrics (requests, duration, errors)
- [ ] Queue depth metrics (all 3 queues)
- [ ] Job processing metrics (submitted, completed, failed)
- [ ] Worker status metrics (active, errors)
- [ ] Redis metrics (operations, memory, connections)
- [ ] System health metrics (Redis up, S3 up)

### 3. Dashboards Functional
- [ ] System Overview dashboard shows API health
- [ ] Queue Monitor shows real-time queue depths
- [ ] Job Processing shows throughput and success rate
- [ ] Worker Health shows all 3 workers active
- [ ] All graphs update in real-time (15s scrape interval)

### 4. Integration
- [ ] Zero regressions in existing tests
- [ ] Metrics endpoint doesn't affect API performance
- [ ] Workers continue functioning with metrics
- [ ] Docker Compose starts all services cleanly
- [ ] `make dev` includes Grafana stack

### 5. Production Readiness
- [ ] Metrics work in both dev and production
- [ ] Grafana dashboards exported as JSON
- [ ] Documentation for adding custom metrics
- [ ] Alert rules defined (optional for MVP)

## Testing Strategy

### Immediate Verification (After Setup)
```bash
# Start full stack
make dev

# Verify Prometheus targets
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'
# Expected: "up" for all targets

# Verify metrics endpoint
curl http://localhost:8001/metrics
# Expected: Prometheus format metrics

# Verify Grafana
open http://localhost:3000
# Login: admin/admin
# Expected: 4 dashboards visible
```

### Functional Verification
```bash
# Submit a test job
curl -F "file=@test.pdf" http://localhost:8080/api/documents/submit

# Watch queue depth in Grafana
# Navigate to Queue Monitor dashboard
# Expected: See pii_scan_queue depth increase to 1

# Watch job processing
# Navigate to Job Processing dashboard
# Expected: See jobs_submitted_total increase
```

### Load Testing
```bash
# Submit 10 jobs
for i in {1..10}; do
  curl -F "file=@test.pdf" http://localhost:8080/api/documents/submit &
done

# Grafana validation:
# - Queue depths increase
# - Processing rate shows 10 jobs
# - No worker errors
# - Response time stays < 1s
```

## Definition of Done

- [ ] Prometheus collecting metrics from API, Redis, workers
- [ ] Grafana showing 4 dashboards (System, Queue, Jobs, Workers)
- [ ] All metrics updating in real-time (15s interval)
- [ ] Redis exporter providing queue metrics
- [ ] Worker instrumentation reporting status
- [ ] Docker Compose integration complete
- [ ] Makefile updated with Grafana targets
- [ ] Documentation in infrastructure/README.md
- [ ] Zero regressions in existing functionality
- [ ] All 237 existing tests passing
- [ ] Metrics endpoint performance validated (< 10ms overhead)
- [ ] Grafana accessible at http://localhost:3000
- [ ] Admin can create custom dashboards
- [ ] Dashboard JSON files committed to git

## Implementation Notes

### Minimal Code Changes
- **Existing code**: 99% unchanged
- **New middleware**: One file (metrics.py)
- **Service updates**: Add 2-3 Prometheus counters per service
- **Worker updates**: Add gauge.set(1) at start, gauge.set(0) at end

### Performance Impact
- **Metrics collection**: < 1ms per request
- **Prometheus scrape**: Every 15s (not per-request)
- **Storage**: ~10MB per day for metrics data
- **Network**: Minimal (internal Docker network)

### Security
- **Dev environment**: No authentication (Docker network only)
- **Production**: Add Grafana OAuth, firewall Prometheus port
- **Metrics data**: No PII (only aggregate counts and durations)

### Maintenance
- **Dashboard updates**: Edit JSON files, reload Grafana
- **New metrics**: Add to metrics_service.py, restart API
- **Retention**: Prometheus keeps 15 days by default
- **Backup**: Grafana dashboards in git, Prometheus data ephemeral

## Dependencies

### Python Packages (Add to pyproject.toml)
```toml
dependencies = [
    # ... existing ...
    "opentelemetry-api>=1.20.0",
    "opentelemetry-sdk>=1.20.0",
    "opentelemetry-instrumentation-fastapi>=0.41b0",
    "opentelemetry-exporter-prometheus>=0.41b0",
    "prometheus-client>=0.19.0",
]
```

### Docker Images
- `prom/prometheus:latest` (~200MB)
- `grafana/grafana:latest` (~350MB)
- `oliver006/redis_exporter:latest` (~20MB)

### Total Additional Storage
- Docker images: ~570MB
- Prometheus data: ~10MB/day
- Grafana data: ~5MB

## Unblocks

- **PRD-010**: End-to-end integration testing (needs metrics for validation)
- **Production deployment**: Observability required before AWS ECS deployment
- **Performance optimization**: Metrics identify bottlenecks
- **Debugging**: Real-time visibility into system state

## References

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Dashboards](https://grafana.com/docs/grafana/latest/dashboards/)
- [OpenTelemetry Python](https://opentelemetry.io/docs/instrumentation/python/)
- [Redis Exporter](https://github.com/oliver006/redis_exporter)
