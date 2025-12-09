"""Metrics middleware for FastAPI with OpenTelemetry and Prometheus.

This module provides automatic instrumentation for HTTP requests and
custom business metrics using OpenTelemetry and Prometheus.

Metrics exported on port 8001 (configurable via METRICS_PORT env var):
- HTTP request duration (histogram)
- HTTP request count by endpoint (counter)
- HTTP error rate by status code (counter)
- Active requests (gauge)
"""

import logging
from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from ..config import settings

logger = logging.getLogger(__name__)

# Prometheus Metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method", "endpoint"],
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect HTTP metrics."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        """Process request and collect metrics.

        Args:
            request: FastAPI request object
            call_next: Next middleware/handler in chain

        Returns:
            Response from downstream handler
        """
        # Skip metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method

        # Get endpoint pattern (e.g., /api/documents/{job_id}/status)
        endpoint = self._get_endpoint_pattern(request)

        # Track in-progress requests
        http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()

        try:
            # Time the request
            with http_request_duration_seconds.labels(
                method=method, endpoint=endpoint
            ).time():
                response = await call_next(request)

            # Record request
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status=response.status_code,
            ).inc()

            return response

        except Exception:
            # Record error
            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status=500,
            ).inc()
            raise

        finally:
            # Decrement in-progress counter
            http_requests_in_progress.labels(method=method, endpoint=endpoint).dec()

    def _get_endpoint_pattern(self, request: Request) -> str:
        """Get endpoint pattern from request (e.g., /api/documents/{job_id}).

        Args:
            request: FastAPI request object

        Returns:
            Endpoint pattern string (or path if no pattern found)
        """
        try:
            for route in request.app.routes:
                match, _ = route.matches(request.scope)
                if match == Match.FULL:
                    return route.path

            # Fallback to raw path if no route matches
            return request.url.path

        except Exception as e:
            logger.warning(f"Failed to get endpoint pattern: {e}")
            return request.url.path


def setup_metrics(app: FastAPI) -> None:
    """Set up metrics collection for FastAPI application.

    This function:
    1. Registers the metrics middleware
    2. Adds a /metrics endpoint for Prometheus scraping

    Args:
        app: FastAPI application instance
    """
    if not settings.enable_metrics:
        logger.info("Metrics collection disabled")
        return

    logger.info(f"Setting up metrics on port {settings.metrics_port}")

    # Add metrics middleware
    app.add_middleware(MetricsMiddleware)

    # Add metrics endpoint
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus metrics endpoint.

        Returns:
            Response with Prometheus format metrics
        """
        return Response(
            content=generate_latest(REGISTRY),
            media_type="text/plain; version=0.0.4",
        )

    logger.info("Metrics collection enabled at /metrics")
