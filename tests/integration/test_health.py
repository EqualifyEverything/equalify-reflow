"""Tests for health check endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from src.dependencies import get_queue_service, get_storage_service
from src.main import app


@pytest.mark.asyncio
async def test_health_check_healthy(client):
    """Test health check when all services are healthy."""
    # Create mock services
    mock_storage = MagicMock()
    mock_storage.check_s3_access = AsyncMock(return_value=True)

    mock_queue = MagicMock()
    mock_queue.check_redis_connection = AsyncMock(return_value=True)
    mock_queue.check_queue_depth = AsyncMock(return_value=5)

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue

    try:
        # Mock docling client as healthy
        mock_docling = MagicMock()
        mock_docling.check_health = AsyncMock(return_value=True)
        with patch("src.services.docling_serve_client.get_docling_client", return_value=mock_docling):
            response = client.get("/health")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["redis"] is True
        assert data["checks"]["s3"] is True
        assert data["checks"]["queue_depth"] == 5
        assert data["checks"]["docling_serve"] is True
    finally:
        # Cleanup overrides
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_degraded_when_docling_down(client):
    """Test health returns 200 with degraded status when docling-serve is down."""
    mock_storage = MagicMock()
    mock_storage.check_s3_access = AsyncMock(return_value=True)

    mock_queue = MagicMock()
    mock_queue.check_redis_connection = AsyncMock(return_value=True)
    mock_queue.check_queue_depth = AsyncMock(return_value=0)

    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue

    try:
        # Mock docling client as unhealthy (e.g. during model loading)
        mock_docling = MagicMock()
        mock_docling.check_health = AsyncMock(return_value=False)
        with patch("src.services.docling_serve_client.get_docling_client", return_value=mock_docling):
            response = client.get("/health")

        # Should return 200 (not 503) — docling_serve is non-fatal
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["docling_serve"] is False
        assert data["checks"]["redis"] is True
        assert data["checks"]["s3"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_unhealthy(client):
    """Test health check when core services are unhealthy."""
    # Create mock services
    mock_storage = MagicMock()
    mock_storage.check_s3_access = AsyncMock(return_value=False)

    mock_queue = MagicMock()
    mock_queue.check_redis_connection = AsyncMock(return_value=False)
    mock_queue.check_queue_depth = AsyncMock(return_value=-1)

    # Override dependencies
    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_queue_service] = lambda: mock_queue

    try:
        # Check health
        response = client.get("/health")

        # Assertions
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    finally:
        # Cleanup overrides
        app.dependency_overrides.clear()


def test_readiness_check(client):
    """Test readiness check endpoint."""
    response = client.get("/health/ready")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ready"
