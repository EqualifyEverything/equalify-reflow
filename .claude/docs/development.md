# Development Guide

## Adding New Features

### Adding an API Endpoint

1. **Define route** in `src/api/v1/documents.py`:

```python
@router.post("/documents/submit")
async def submit_document(
    file: UploadFile,
    storage: StorageService = Depends(get_storage_service)
):
    # Implementation
```

2. **Add business logic** in `src/services/`:

```python
# src/services/document_service.py
class DocumentService:
    def __init__(self, storage: StorageService, queue: QueueService):
        self.storage = storage
        self.queue = queue
```

3. **Create Pydantic models** in `src/shared/models/`:

```python
# src/shared/models/document.py
class DocumentSubmitRequest(BaseModel):
    filename: str
    content_type: str
```

4. **Write tests**:

- Unit: `tests/unit/api/test_documents.py`
- Integration: `tests/integration/test_document_flow.py`

5. **Update dependency injection** in `src/dependencies.py` if needed

### Adding a Background Worker

Workers use a **class-based pattern** with manual service initialization (NOT `Depends()`).

1. **Create worker class** in `src/workers/new_worker.py`:

```python
class NewWorker:
    def __init__(self, queue_service: QueueService, job_service: JobService):
        self.queue = queue_service
        self.job_service = job_service
        self.running = False

    async def start(self, shutdown_event: asyncio.Event):
        """Main worker loop."""
        self.running = True
        while self.running and not shutdown_event.is_set():
            try:
                job_data = await self.queue.dequeue(
                    QUEUE_NEW,
                    timeout=settings.pii_worker_queue_timeout_seconds
                )
                if job_data:
                    await self._process_job(job_data)
            except Exception as e:
                logger.error(f"Worker error: {e}")
                await asyncio.sleep(settings.worker_error_sleep_seconds)
```

2. **Create startup function** (workers manually create services, NOT via Depends):

```python
async def start_new_worker(shutdown_event: asyncio.Event = None) -> None:
    """Initialize services and start worker."""
    from ..dependencies import get_redis_client, get_s3_client

    # Manually get clients (NOT using Depends)
    redis_client = await anext(get_redis_client())
    s3_client = await anext(get_s3_client())

    # Create service instances directly
    queue_service = QueueService(redis_client=redis_client)
    job_service = JobService(redis_client=redis_client)

    # Create and start worker
    worker = NewWorker(queue_service=queue_service, job_service=job_service)
    await worker.start(shutdown_event)
```

3. **Start worker in** `src/main.py` lifespan:

```python
new_worker_task = asyncio.create_task(start_new_worker(shutdown_event))
```

4. **Add queue constant** in `src/shared/constants/queues.py`:

```python
QUEUE_NEW = "eq-pdf:queue:new"
```

5. **Test worker** in `tests/unit/workers/test_new_worker.py`

**Important:** Workers bypass `Depends()` because they run in background threads, not HTTP request context.

### Adding a Service

1. **Create service** in `src/services/new_service.py`:

```python
class NewService:
    def __init__(self, redis_client, s3_client):
        self.redis = redis_client
        self.s3_client = s3_client
```

2. **Add dependency injection** in `src/dependencies.py`:

```python
# For services that should be singletons (e.g., with circuit breakers):
@lru_cache
def _get_new_service_singleton() -> NewService:
    s3_client = _get_s3_client_singleton()
    return NewService(s3_client=s3_client, ...)

async def get_new_service() -> NewService:
    return _get_new_service_singleton()

# For services that can be created per-request:
async def get_new_service(
    redis_client = Depends(get_redis_client)
) -> NewService:
    return NewService(redis_client=redis_client)
```

3. **Write tests** in `tests/unit/services/test_new_service.py`

**Note:** Use singletons (`@lru_cache`) for services that maintain state like circuit breakers. Use per-request creation for stateless services.

## Package Management

ALL Python development uses `uv`:

```bash
# Add dependency (from inside container)
docker exec -it equalify-reflow-api-gateway uv add <package>

# Add dev dependency
docker exec -it equalify-reflow-api-gateway uv add --dev <package>

# Remove dependency
docker exec -it equalify-reflow-api-gateway uv remove <package>

# Update dependency
docker exec -it equalify-reflow-api-gateway uv add <package>@latest
```

## Debugging

### View Logs

```bash
make logs           # All services
make logs-api       # API only
docker logs equalify-reflow-redis -f
```

### Access Container Shell

```bash
make shell          # API container bash
make redis-cli      # Redis CLI
```

### Check Infrastructure Health

```bash
make health         # Run health checks
curl http://localhost:8080/health
curl http://localhost:8080/metrics
```

## Native Docling-Serve (GPU Acceleration)

On Apple Silicon Macs, docling-serve can run natively with MPS (Metal Performance Shaders) for significantly faster PDF processing, especially OCR.

### Setup (one-time)

```bash
make docling-install   # Installs docling-serve with EasyOCR support
```

### Usage

```bash
make docling-native    # Start native docling-serve with MPS
make dev-gpu           # Start Docker stack using native docling
```

This replaces the dockerized docling-serve (CPU-only) with a host-native instance accessible via `host.docker.internal:5001`. The `docker-compose.native-docling.yml` override handles the routing.

To check status:
```bash
make docling-health    # Check if docling-serve is responding
curl http://localhost:5001/health
```

Logs are written to `/tmp/docling-serve.log`.

### Stopping

`make down` automatically stops both Docker services and the native docling-serve process. You can also stop it independently:

```bash
make docling-native-stop
```

## Common Issues

### Tests failing with Redis connection refused

- Check Docker is running: `docker ps`
- Restart services: `make down && make dev`
- Verify Redis is healthy: `make redis-cli` then `PING`

### Hot reload not working

- Check logs: `make logs-api`
- Verify volume mounts in `docker-compose.dev.yml`
- Restart: `make down && make dev`

### Container not starting

- Check logs: `docker logs equalify-reflow-api-gateway`
- Verify `.env` file exists
- Clean and rebuild: `make clean && make build && make dev`

## Environment Configuration

### Local Development

- `.env` contains application config (NO AWS credentials)
- `docker-compose.dev.yml` sets AWS credentials for containers
- LocalStack runs inside Docker network

**Never:** Source `.env` manually in your shell - it's for Docker Compose only
