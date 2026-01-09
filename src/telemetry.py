"""OpenTelemetry configuration and initialization.

Provides distributed tracing for the application with:
- Automatic FastAPI instrumentation
- Custom spans for agent execution
- Optional OTLP export for Jaeger/etc.
"""

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Global tracer instance
_tracer: trace.Tracer | None = None
_initialized: bool = False


def get_tracer(name: str = "equalify-pdf-converter") -> trace.Tracer:
    """Get a tracer instance.

    Args:
        name: Tracer name for instrumentation scope

    Returns:
        OpenTelemetry Tracer instance
    """
    return trace.get_tracer(name)


def init_telemetry(app: "FastAPI | None" = None) -> None:
    """Initialize OpenTelemetry with configured exporters.

    Args:
        app: Optional FastAPI app instance for automatic instrumentation
    """
    global _initialized

    if _initialized:
        logger.debug("Telemetry already initialized")
        return

    from src.config import settings

    if not settings.telemetry_enabled:
        logger.info("Telemetry disabled")
        _initialized = True
        return

    # Create resource with service info
    resource = Resource.create({
        SERVICE_NAME: "equalify-pdf-converter",
        "service.version": "1.0.0",
        "deployment.environment": settings.environment,
    })

    # Set up trace provider
    provider = TracerProvider(resource=resource)

    # Add console exporter for development (uses SimpleSpanProcessor for immediate output)
    if settings.telemetry_console_export:
        provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter())
        )
        logger.info("OpenTelemetry console exporter enabled (immediate export)")

    # Add OTLP exporter if configured (for Jaeger, etc.)
    if settings.telemetry_otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=settings.telemetry_otlp_endpoint,
                        insecure=True,  # Required for Docker networking without TLS
                    )
                )
            )
            logger.info(f"OpenTelemetry OTLP exporter enabled: {settings.telemetry_otlp_endpoint}")
        except ImportError:
            logger.warning(
                "OTLP exporter not available. Install opentelemetry-exporter-otlp "
                "to enable OTLP export."
            )

    trace.set_tracer_provider(provider)

    # Instrument FastAPI automatically
    if app:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            # Check if already instrumented (e.g., from hot reload)
            if not getattr(app, "_otel_instrumented", False):
                FastAPIInstrumentor.instrument_app(app)
                app._otel_instrumented = True  # type: ignore[attr-defined]
                logger.info("FastAPI instrumented with OpenTelemetry")
            else:
                logger.info("FastAPI already instrumented, skipping")
        except ImportError:
            logger.warning(
                "FastAPI instrumentation not available. Install "
                "opentelemetry-instrumentation-fastapi to enable."
            )

    _initialized = True
    logger.info(
        f"OpenTelemetry initialized "
        f"(console={settings.telemetry_console_export}, "
        f"otlp={settings.telemetry_otlp_endpoint or 'disabled'})"
    )


def shutdown_telemetry() -> None:
    """Shutdown telemetry and flush pending spans."""
    global _initialized

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
        logger.info("OpenTelemetry shutdown complete")

    _initialized = False
