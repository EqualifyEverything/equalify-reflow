"""Tests for health check endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status

from src.main import app
from src.dependencies import get_storage_service, get_queue_service


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
        # Check health
        response = client.get("/health")

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["redis"] is True
        assert data["checks"]["s3"] is True
        assert data["checks"]["queue_depth"] == 5
    finally:
        # Cleanup overrides
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check_unhealthy(client):
    """Test health check when services are unhealthy."""
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