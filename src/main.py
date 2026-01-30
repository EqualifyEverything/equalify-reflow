"""FastAPI application for API Gateway Service."""
# Test comment for hot-reload verification

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from .api import approval, corrections, documents, health, review, review_checklist
from .config import settings
from .dependencies import get_redis_client
from .middleware import (
    APIKeyAuthMiddleware,
    DocsAuthMiddleware,
    ErrorHandlerMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    add_cors_middleware,
)
from .middleware.metrics import setup_metrics
from .services.rate_limit_service import RateLimitService
from .telemetry import init_telemetry, shutdown_telemetry
from .workers.canvas_file_worker import start_canvas_file_worker
from .workers.pii_worker import start_pii_worker
from .workers.timeout_worker import start_timeout_worker

# Configure logging
logging.basicConfig(level=settings.log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager.

    Starts background workers when the application starts
    and ensures cleanup on shutdown.
    """
    # Initialize OpenTelemetry (if enabled)
    if settings.telemetry_enabled:
        logger.info("Initializing OpenTelemetry...")
        init_telemetry(app)
        logger.info("✅ OpenTelemetry initialized")

    # Startup: Initialize shared services
    logger.info("Initializing shared services...")
    redis_gen = get_redis_client()
    redis_client = await anext(redis_gen)
    app.state.rate_limiter = RateLimitService(redis=redis_client)
    logger.info("Rate limiter initialized")

    # Track worker tasks for cleanup
    worker_tasks: list[asyncio.Task[Any]] = []
    shutdown_event: asyncio.Event | None = None

    # Startup: Launch background workers (unless disabled for testing)
    if settings.disable_workers:
        logger.warning("⚠️  Background workers DISABLED (DISABLE_WORKERS=true)")
        logger.warning("   This should only be used for integration testing")
    else:
        logger.info("Starting background workers...")

        # Create shutdown event to signal graceful shutdown
        shutdown_event = asyncio.Event()

        # Pass shutdown event to workers
        # Note: Processing Worker removed - DocumentProcessingService runs
        # inline via BackgroundTasks instead of queue-based processing
        worker_tasks = [
            asyncio.create_task(start_pii_worker(shutdown_event)),
            asyncio.create_task(start_timeout_worker(shutdown_event)),
            asyncio.create_task(start_canvas_file_worker(shutdown_event)),
        ]
        logger.info("PII, Timeout, and Canvas file discovery worker tasks created")

    yield

    # Shutdown: Graceful shutdown with timeout (only if workers were started)
    if worker_tasks and shutdown_event is not None:
        logger.info("Initiating graceful shutdown of background workers...")
        shutdown_event.set()

        # Wait for workers to finish current job (max 30 seconds)
        try:
            await asyncio.wait_for(asyncio.gather(*worker_tasks, return_exceptions=True), timeout=30.0)
            logger.info("All background workers stopped gracefully")
        except TimeoutError:
            logger.warning("Graceful shutdown timeout, forcing cancellation")
            for task in worker_tasks:
                task.cancel()
            try:
                await asyncio.gather(*worker_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Error during forced shutdown: {e}")

    # Shutdown OpenTelemetry (if enabled)
    if settings.telemetry_enabled:
        logger.info("Shutting down OpenTelemetry...")
        shutdown_telemetry()
        logger.info("✅ OpenTelemetry shutdown complete")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Equalify PDF Converter API",
    description="API Gateway for PDF to accessible HTML conversion",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Set up metrics collection (must be called before adding middleware)
setup_metrics(app)

# Add middleware (order matters: last added = first executed)
# Authentication middleware added first (executes before others)
if settings.enable_api_key_auth:
    app.add_middleware(APIKeyAuthMiddleware)
    logger.info("✅ API key authentication enabled")

if settings.enable_docs_auth:
    app.add_middleware(DocsAuthMiddleware)
    logger.info("✅ Documentation authentication enabled")

app.add_middleware(ErrorHandlerMiddleware)  # Catch all errors
app.add_middleware(RateLimitMiddleware)  # Rate limit before processing
app.add_middleware(LoggingMiddleware)  # Log all requests
add_cors_middleware(app)  # CORS headers

# Include routers
app.include_router(health.router)
app.include_router(documents.router)
app.include_router(approval.router)
app.include_router(corrections.router)
app.include_router(review.router)
app.include_router(review_checklist.router)

# Conditionally import dev-only endpoints (only in development)
if settings.environment == "dev":
    from .api import dev_monitoring

    app.include_router(dev_monitoring.router)
    logger.info("✅ Dev monitoring endpoints enabled at /api/dev/monitoring/queues")

# Conditionally enable Canvas config endpoints
if settings.canvas_autopublish_enabled:
    from .api.canvas_config import router as canvas_config_router

    app.include_router(canvas_config_router)
    logger.info("✅ Canvas config endpoints enabled at /api/v1/canvas/courses/*")

# Conditionally enable LTI 1.3 integration
if settings.lti_enabled:
    from .lti import router as lti_router

    app.include_router(lti_router)
    logger.info("✅ LTI 1.3 endpoints enabled at /lti/*")


def custom_openapi() -> dict[str, object]:
    """Generate custom OpenAPI schema with API key security."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Add API key security scheme
    openapi_schema["components"] = openapi_schema.get("components", {})
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": settings.api_key_header_name,
            "description": "API key for authentication. Get your key from the system administrator.",
        }
    }

    # Apply security globally to all endpoints
    openapi_schema["security"] = [{"APIKeyHeader": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


# Override the default OpenAPI schema
app.openapi = custom_openapi  # type: ignore[method-assign]


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"service": "Equalify PDF Converter API Gateway", "version": "0.1.0", "docs": "/docs"}


# Mount Pipeline Viewer (standalone viewer app)
_viewer_path = Path(__file__).parent.parent / "static" / "viewer"
if _viewer_path.exists():
    from fastapi.responses import FileResponse

    @app.get("/viewer/{full_path:path}")
    async def serve_viewer_spa(full_path: str) -> FileResponse:
        """Serve index.html for Pipeline Viewer routes (SPA fallback)."""
        if "." in full_path.split("/")[-1]:
            file_path = _viewer_path / full_path
            if file_path.exists():
                return FileResponse(file_path)
        return FileResponse(_viewer_path / "index.html")

    @app.get("/viewer")
    async def serve_viewer_root() -> FileResponse:
        """Serve Pipeline Viewer root."""
        return FileResponse(_viewer_path / "index.html")

    app.mount("/viewer", StaticFiles(directory=_viewer_path, html=True), name="pipeline-viewer")
    logger.info("✅ Pipeline Viewer mounted at /viewer")
