"""Tests for health check endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from src.dependencies import get_queue_service, get_storage_service
from src.main import app

pytestmark = pytest.mark.integration


def _override(storage_ok: bool, redis_ok: bool, queue_depth: int):
    """Helper: install dependency overrides with given outcomes."""
    mock_storage = MagicMock()
    mock_storage.check_s3_access = AsyncMock(return_value=storage_ok)

    mock_queue = MagicMock()
    mock_queue.check_redis_connection = AsyncMock(return_value=redis_ok)
    mock_queue.check_queue_depth = AsyncMock(return_value=queue_depth)

    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue


def _patched_docling(healthy: bool):
    """Helper: patch the docling client to report given health."""
    mock_docling = MagicMock()
    mock_docling.check_health = AsyncMock(return_value=healthy)
    return patch(
        "src.services.docling_serve_client.get_docling_client",
        return_value=mock_docling,
    )


# ---------- /health (tolerant) ----------


@pytest.mark.asyncio
async def test_health_check_healthy(client):
    """All checks pass → 200 healthy."""
    _override(storage_ok=True, redis_ok=True, queue_depth=5)
    try:
        with _patched_docling(True):
            response = client.get("/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["redis"] is True
        assert data["checks"]["s3"] is True
        assert data["checks"]["queue_depth"] == 5
        assert data["checks"]["docling_serve"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_degraded_when_docling_down(client):
    """/health stays 200 with status=degraded when only docling-serve is down."""
    _override(storage_ok=True, redis_ok=True, queue_depth=0)
    try:
        with _patched_docling(False):
            response = client.get("/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["docling_serve"] is False
        assert data["checks"]["redis"] is True
        assert data["checks"]["s3"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_unhealthy_when_core_down(client):
    """/health returns 503 when a core store is unreachable."""
    _override(storage_ok=False, redis_ok=False, queue_depth=-1)
    try:
        response = client.get("/health")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    finally:
        app.dependency_overrides.clear()


# ---------- /health/ready (strict) ----------


@pytest.mark.asyncio
async def test_readiness_ready_when_all_deps_ok(client):
    """/health/ready returns 200 ready only when every dep is reachable."""
    _override(storage_ok=True, redis_ok=True, queue_depth=0)
    try:
        with _patched_docling(True):
            response = client.get("/health/ready")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["docling_serve"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_readiness_not_ready_when_docling_down(client):
    """/health/ready returns 503 when docling-serve is unhealthy."""
    _override(storage_ok=True, redis_ok=True, queue_depth=0)
    try:
        with _patched_docling(False):
            response = client.get("/health/ready")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["detail"]["status"] == "not_ready"
        assert data["detail"]["checks"]["docling_serve"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_readiness_not_ready_when_redis_down(client):
    """/health/ready returns 503 when Redis is unreachable, even if other deps are up."""
    _override(storage_ok=True, redis_ok=False, queue_depth=0)
    try:
        with _patched_docling(True):
            response = client.get("/health/ready")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        data = response.json()
        assert data["detail"]["status"] == "not_ready"
        assert data["detail"]["checks"]["redis"] is False
    finally:
        app.dependency_overrides.clear()
