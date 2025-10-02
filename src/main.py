"""FastAPI application for API Gateway Service."""
# Test comment for hot-reload verification

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import settings
from .middleware import (
    ErrorHandlerMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    add_cors_middleware,
)
from .middleware.metrics import setup_metrics
from .api import documents, health, approval
from .workers.pii_worker import start_pii_worker
from .workers.processing_worker import start_processing_worker
from .workers.timeout_worker import start_timeout_worker

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager.

    Starts background workers when the application starts
    and ensures cleanup on shutdown.
    """
    # Startup: Launch background workers
    logger.info("Starting background workers...")

    # Create shutdown event to signal graceful shutdown
    shutdown_event = asyncio.Event()

    # Pass shutdown event to workers
    pii_worker_task = asyncio.create_task(start_pii_worker(shutdown_event))
    processing_worker_task = asyncio.create_task(start_processing_worker(shutdown_event))
    timeout_worker_task = asyncio.create_task(start_timeout_worker(shutdown_event))
    logger.info("PII, Processing, and Timeout worker tasks created")

    yield

    # Shutdown: Graceful shutdown with timeout
    logger.info("Initiating graceful shutdown of background workers...")
    shutdown_event.set()

    # Wait for workers to finish current job (max 30 seconds)
    try:
        await asyncio.wait_for(
            asyncio.gather(
                pii_worker_task,
                processing_worker_task,
                timeout_worker_task,
                return_exceptions=True
            ),
            timeout=30.0
        )
        logger.info("All background workers stopped gracefully")
    except asyncio.TimeoutError:
        logger.warning("Graceful shutdown timeout, forcing cancellation")
        pii_worker_task.cancel()
        processing_worker_task.cancel()
        timeout_worker_task.cancel()
        try:
            await asyncio.gather(
                pii_worker_task,
                processing_worker_task,
                timeout_worker_task,
                return_exceptions=True
            )
        except Exception as e:
            logger.error(f"Error during forced shutdown: {e}")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Equalify PDF Converter API",
    description="API Gateway for PDF to accessible HTML conversion",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Set up metrics collection (must be called before adding middleware)
setup_metrics(app)

# Add middleware (order matters: last added = first executed)
app.add_middleware(ErrorHandlerMiddleware)  # Catch all errors
app.add_middleware(RateLimitMiddleware)     # Rate limit before processing
app.add_middleware(LoggingMiddleware)       # Log all requests
add_cors_middleware(app)                     # CORS headers

# Include routers
app.include_router(health.router)
app.include_router(documents.router)
app.include_router(approval.router)

# Conditionally import dev monitoring endpoints (only in development)
if settings.environment == "dev":
    from .api import dev_monitoring
    app.include_router(dev_monitoring.router)
    logger.info("✅ Dev monitoring endpoints enabled at /api/dev/monitoring/queues")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Equalify PDF Converter API Gateway",
        "version": "0.1.0",
        "docs": "/docs"
    }