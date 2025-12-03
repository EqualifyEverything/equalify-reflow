"""CORS configuration."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def add_cors_middleware(app: FastAPI) -> None:
    """
    Add CORS middleware to FastAPI app.

    Args:
        app: FastAPI application instance
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        # Explicit headers required when allow_credentials=True (wildcards don't work)
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-API-Key",
            "X-Request-ID",
            "Accept",
            "Origin",
            "Cache-Control",
        ],
    )
