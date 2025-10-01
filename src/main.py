"""FastAPI application for API Gateway Service."""

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
from .api import documents, health
from .workers.pii_worker import start_pii_worker

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
    # Startup: Launch PII worker as background task
    logger.info("Starting background workers...")
    pii_worker_task = asyncio.create_task(start_pii_worker())
    logger.info("PII worker task created")

    yield

    # Shutdown: Cancel worker tasks
    logger.info("Shutting down background workers...")
    pii_worker_task.cancel()
    try:
        await pii_worker_task
    except asyncio.CancelledError:
        logger.info("PII worker stopped")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Equalify PDF Converter API",
    description="API Gateway for PDF to accessible HTML conversion",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Add middleware (order matters: last added = first executed)
app.add_middleware(ErrorHandlerMiddleware)  # Catch all errors
app.add_middleware(RateLimitMiddleware)     # Rate limit before processing
app.add_middleware(LoggingMiddleware)       # Log all requests
add_cors_middleware(app)                     # CORS headers

# Include routers
app.include_router(health.router)
app.include_router(documents.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Equalify PDF Converter API Gateway",
        "version": "0.1.0",
        "docs": "/docs"
    }