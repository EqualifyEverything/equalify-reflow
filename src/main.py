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

from .api import approval, documents, feedback, health
from .auth.middleware import SessionAuthMiddleware
from .auth.routes import router as auth_router
from .config import settings
from .dependencies import get_redis_client
from .middleware import (
    APIKeyAuthMiddleware,
    ErrorHandlerMiddleware,
    LoggingMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    add_cors_middleware,
)
from .middleware.metrics import setup_metrics
from .services.rate_limit_service import RateLimitService
from .telemetry import init_telemetry, shutdown_telemetry
from .workers.pii_worker import start_pii_worker
from .workers.timeout_worker import start_timeout_worker

# Configure logging
logging.basicConfig(level=settings.log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Silence noisy third-party loggers (they flood DEBUG with base64 payloads, auth signatures, etc.)
for _noisy_logger in ("botocore", "boto3", "urllib3", "httpcore", "httpx", "s3transfer", "python_multipart"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

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

    # Initialize Logfire (if enabled) — auto-instruments PydanticAI agents
    if settings.logfire_enabled:
        try:
            import logfire

            # If token provided via env, use it; otherwise logfire picks up local config
            if settings.logfire_token:
                logfire.configure(token=settings.logfire_token)
            else:
                logfire.configure()
            logfire.instrument_pydantic_ai()
            logger.info("✅ Logfire initialized (PydanticAI instrumentation active)")
        except Exception as e:
            logger.warning(f"Failed to initialize Logfire: {e}")

    # Startup: Initialize shared services
    logger.info("Initializing shared services...")

    # Initialize docling-serve client
    from .services.docling_serve_client import init_docling_client
    init_docling_client(settings.docling_serve_url, settings.docling_serve_timeout)
    logger.info("✅ Docling-serve client initialized")

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
        ]
        logger.info("PII and Timeout worker tasks created")

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

    # Shutdown docling-serve client
    from .services.docling_serve_client import close_docling_client
    await close_docling_client()
    logger.info("✅ Docling-serve client closed")

    # Shutdown OpenTelemetry (if enabled)
    if settings.telemetry_enabled:
        logger.info("Shutting down OpenTelemetry...")
        shutdown_telemetry()
        logger.info("✅ OpenTelemetry shutdown complete")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Equalify Reflow API",
    description=(
        "Open-source pipeline for converting PDF documents into accessible, "
        "semantic markdown. Combines IBM Docling document extraction with "
        "AI text correction. Designed to be provider-agnostic (AWS Bedrock "
        "today, Anthropic direct and other providers in progress)."
    ),
    version="0.1.0b6",
    docs_url="/docs",
    redoc_url="/redoc",
    license_info={
        "name": "AGPL-3.0-or-later",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    },
    lifespan=lifespan,
)

# Set up metrics collection (must be called before adding middleware)
setup_metrics(app)

# Add middleware (order matters: last added = first executed)
# Authentication middlewares added first (execute before others). Session
# middleware runs ahead of API-key middleware so the latter can short-circuit
# when ``request.state.identity`` is set. Both coexist by design — API keys
# remain a parallel auth path for programmatic clients regardless of AUTH_MODE.
if settings.enable_api_key_auth:
    app.add_middleware(APIKeyAuthMiddleware)
    logger.info("✅ API key authentication enabled")

if settings.auth_mode != "none":
    app.add_middleware(SessionAuthMiddleware)
    logger.info("✅ Session authentication enabled (mode=%s)", settings.auth_mode)

app.add_middleware(ErrorHandlerMiddleware)  # Catch all errors
app.add_middleware(RateLimitMiddleware)  # Rate limit before processing
app.add_middleware(LoggingMiddleware)  # Log all requests
app.add_middleware(SecurityHeadersMiddleware)  # Frame security
add_cors_middleware(app)  # CORS headers

# Include routers
app.include_router(health.router)
app.include_router(documents.router)
app.include_router(approval.router)
app.include_router(feedback.router)
app.include_router(auth_router)

# Pipeline viewer — always available (primary processing API)
from .api import pipeline_viewer  # noqa: E402

app.include_router(pipeline_viewer.router)
logger.info("✅ Pipeline endpoint enabled at /api/v1/pipeline/process")

# Conditionally import dev-only endpoints (only in development)
if settings.environment == "dev":
    from .api import minimal_pipeline

    app.include_router(minimal_pipeline.router)
    logger.info("✅ Minimal pipeline endpoint enabled at /api/dev/minimal/process")


def custom_openapi() -> dict[str, object]:
    """Generate custom OpenAPI schema with API key security."""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        license_info=app.license_info,
    )

    # Add API key + session cookie security schemes. Either path satisfies
    # the global security requirement; clients pick whichever fits.
    openapi_schema["components"] = openapi_schema.get("components", {})
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": settings.api_key_header_name,
            "description": "API key for authentication. Get your key from the system administrator.",
        },
        "CookieAuth": {
            "type": "apiKey",
            "in": "cookie",
            "name": settings.auth_session_cookie_name,
            "description": "Signed session cookie set by /api/v1/auth/login or the OIDC callback. Active only when AUTH_MODE != 'none'.",
        },
    }

    # Apply security globally to all endpoints — either scheme accepted.
    openapi_schema["security"] = [{"APIKeyHeader": []}, {"CookieAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


# Override the default OpenAPI schema
app.openapi = custom_openapi  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Pipeline Viewer — served at root
# ---------------------------------------------------------------------------
# The viewer React SPA is mounted at `/`. Registration order matters: all API
# routers (health, documents, approval, pipeline_viewer, ...) are included
# earlier in this file so they claim their paths first. The catch-all below
# only matches paths no router handled, serving either a real static asset
# from disk or falling back to index.html for React Router deep links.
#
# Legacy `/viewer` and `/viewer/*` paths are permanently 301-redirected to
# their root equivalents, preserving path and query string so bookmarks and
# published references keep working.

_viewer_path = Path(__file__).parent.parent / "static" / "viewer"
if _viewer_path.exists():
    from fastapi import HTTPException, Request
    from fastapi.responses import FileResponse, RedirectResponse

    @app.get("/viewer/{full_path:path}")
    async def redirect_legacy_viewer_path(full_path: str, request: Request) -> RedirectResponse:
        """Permanent redirect from /viewer/<path> to /<path>, preserving query."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"/{full_path}{query}", status_code=301)

    @app.get("/viewer")
    async def redirect_legacy_viewer_root(request: Request) -> RedirectResponse:
        """Permanent redirect from /viewer to /, preserving query."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"/{query}", status_code=301)

    @app.get("/")
    async def serve_viewer_root() -> FileResponse:
        """Serve the viewer's index.html at the site root."""
        return FileResponse(_viewer_path / "index.html")

    @app.get("/{full_path:path}")
    async def serve_viewer_spa(full_path: str) -> FileResponse:
        """Serve viewer static files, falling back to index.html for SPA deep links.

        Unknown API/docs/health paths get a JSON 404 instead of being shadowed
        by the SPA fallback — programmatic clients deserve the right content type.
        """
        if (
            full_path.startswith("api/")
            or full_path.startswith("health")
            or full_path.startswith("lti/")
            or full_path.startswith("metrics")
            or full_path in ("docs", "redoc", "openapi.json")
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        file_path = _viewer_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)

        return FileResponse(_viewer_path / "index.html")

    logger.info("✅ Pipeline Viewer mounted at / (legacy /viewer paths redirect here)")
else:

    @app.get("/")
    async def serve_root_placeholder() -> dict[str, str]:
        """Fallback when the viewer hasn't been built."""
        return {
            "service": "Equalify Reflow API Gateway",
            "version": app.version,
            "docs": "/docs",
            "note": "Viewer not built — run `cd clients/viewer && pnpm run build`",
        }

    logger.warning("⚠️  Pipeline Viewer dist not found; only the API is served")
