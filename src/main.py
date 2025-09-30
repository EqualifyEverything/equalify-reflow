"""FastAPI application for API Gateway Service."""

import logging

from fastapi import FastAPI

from .config import settings
from .middleware import (
    ErrorHandlerMiddleware,
    LoggingMiddleware,
    add_cors_middleware,
)
from .api import documents, health

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Create FastAPI app
app = FastAPI(
    title="Equalify PDF Converter API",
    description="API Gateway for PDF to accessible HTML conversion",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add middleware
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(LoggingMiddleware)
add_cors_middleware(app)

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