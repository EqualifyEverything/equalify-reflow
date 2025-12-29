"""Integration tests for approval workflow API endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app


@pytest.fixture
def valid_job_data():
    """Valid job data for approval workflow."""
    return {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test-doc.pdf",
        "status": "awaiting_approval",
        "approval_token": "valid-token-abc123",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "pii_findings": [{"entity_type": "EMAIL_ADDRESS", "text": "student@example.com", "score": 0.95}],
    }


@pytest.fixture
def expired_job_data():
    """Job data with expired approval token."""
    return {
        "job_id": "expired-job-456",
        "s3_key": "temp/expired-doc.pdf",
        "status": "awaiting_approval",
        "approval_token": "expired-token-xyz",
        "approval_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "pii_findings": [],
    }


@pytest.mark.asyncio
async def test_get_review_details_valid_token(valid_job_data, api_key_headers):
    """Test GET /api/approval/{token}/review with valid token."""
    token = valid_job_data["approval_token"]

    with (
        patch("src.api.approval.get_redis_client") as mock_redis_dep,
        patch("src.api.approval.get_s3_client") as mock_s3_dep,
        patch("src.api.approval.JobService") as mock_job_service_class,
    ):
        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:test-job-123"]
        mock_redis_dep.return_value = mock_redis

        # Mock S3 client
        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService (with new O(1) lookup method)
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job_data
        mock_job_service.get_job_by_approval_token.return_value = valid_job_data
        mock_job_service_class.return_value = mock_job_service

        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/approval/{token}/review", headers=api_key_headers)

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == valid_job_data["job_id"]
        assert data["status"] == "awaiting_approval"
        assert len(data["pii_findings"]) == 1
        assert data["pii_findings"][0]["entity_type"] == "EMAIL_ADDRESS"


@pytest.mark.asyncio
async def test_get_review_details_invalid_token(api_key_headers):
    """Test GET /api/approval/{token}/review with invalid token."""
    with (
        patch("src.api.approval.get_redis_client") as mock_redis_dep,
        patch("src.api.approval.get_s3_client") as mock_s3_dep,
    ):
        # Mock Redis - no matching job
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/approval/invalid-token-999/review", headers=api_key_headers)

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_review_details_expired_token(expired_job_data, api_key_headers):
    """Test GET /api/approval/{token}/review with expired token."""
    token = expired_job_data["approval_token"]

    with (
        patch("src.api.approval.get_redis_client") as mock_redis_dep,
        patch("src.api.approval.get_s3_client") as mock_s3_dep,
        patch("src.api.approval.JobService") as mock_job_service_class,
    ):
        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis_dep.return_value = mock_redis

        # Mock S3
        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService with expired data
        mock_job_service = AsyncMock()
        mock_job_service.get_job_by_approval_token.return_value = expired_job_data
        mock_job_service_class.return_value = mock_job_service

        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/approval/{token}/review", headers=api_key_headers)

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_approval_approved_decision(valid_job_data, api_key_headers):
    """Test POST /api/approval/{token}/decision with approved decision."""
    import json

    from src.dependencies import get_redis_client, get_s3_client

    # Add updated_at field required by Job model
    valid_job_data["updated_at"] = valid_job_data["created_at"]

    token = valid_job_data["approval_token"]
    job_id = valid_job_data["job_id"]
    decision_payload = {
        "decision": "approved",
        "justification": "Instructor contact information is acceptable for course materials",
        "reviewed_by": "faculty@uic.edu",
    }

    # Prepare job data for Redis (pii_findings must be JSON string)
    redis_job_data = valid_job_data.copy()
    redis_job_data["pii_findings"] = json.dumps(valid_job_data["pii_findings"])

    # Mock Redis client with proper method returns
    mock_redis = AsyncMock()
    # For get_job_by_approval_token -> get job_id from token
    mock_redis.get.return_value = job_id

    # For get_job -> get full job data (with pii_findings as JSON string)
    # Note: hgetall gets called multiple times, so we use side_effect to return fresh copies
    def return_redis_job(*args, **kwargs):
        data = valid_job_data.copy()
        data["pii_findings"] = json.dumps(valid_job_data["pii_findings"])
        return data

    mock_redis.hgetall.side_effect = return_redis_job
    # For decision submission
    mock_redis.lpush.return_value = 1
    mock_redis.zrem.return_value = 1
    mock_redis.hset.return_value = 1
    mock_redis.set.return_value = True  # For distributed lock acquisition
    mock_redis.delete.return_value = 1  # For lock release

    # Mock S3 client
    mock_s3 = AsyncMock()

    # Override dependencies (only Redis and S3, not services)
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_s3_client] = lambda: mock_s3

    try:
        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/approval/{token}/decision", json=decision_payload, headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "approved"
        assert data["job_id"] == valid_job_data["job_id"]
        assert "approved" in data["message"]

        # Verify Redis operations were called
        mock_redis.lpush.assert_called_once()  # Queue for processing
    finally:
        # Clean up overrides
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_submit_approval_denied_decision(valid_job_data, api_key_headers):
    """Test POST /api/approval/{token}/decision with denied decision."""
    token = valid_job_data["approval_token"]
    decision_payload = {
        "decision": "denied",
        "justification": "Contains student PII that cannot be processed per university policy",
        "reviewed_by": "admin@uic.edu",
    }

    with (
        patch("src.api.approval.get_redis_client") as mock_redis_dep,
        patch("src.api.approval.get_s3_client") as mock_s3_dep,
        patch("src.api.approval.JobService") as mock_job_service_class,
        patch("src.api.approval.QueueService") as mock_queue_service_class,
        patch("src.services.cleanup_service.CleanupService.cleanup_job_files", new_callable=AsyncMock) as mock_cleanup,
    ):
        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:test-job-123"]
        mock_redis.zrem.return_value = 1
        mock_redis.hset.return_value = 1
        mock_redis_dep.return_value = mock_redis

        # Mock S3 client
        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job_data
        mock_job_service.get_job_by_approval_token.return_value = valid_job_data
        mock_job_service_class.return_value = mock_job_service

        # Mock QueueService
        mock_queue_service = AsyncMock()
        mock_queue_service_class.return_value = mock_queue_service

        # Mock cleanup service
        mock_cleanup.return_value = True

        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                f"/api/approval/{token}/decision", json=decision_payload, headers=api_key_headers
            )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "denied"
        assert data["job_id"] == valid_job_data["job_id"]
        assert "denied" in data["message"]

        # Verify cleanup was called
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_submit_approval_invalid_token(api_key_headers):
    """Test POST /api/approval/{token}/decision with invalid token."""
    decision_payload = {"decision": "approved", "justification": "Test justification", "reviewed_by": "test@test.com"}

    with (
        patch("src.api.approval.get_redis_client") as mock_redis_dep,
        patch("src.api.approval.get_s3_client") as mock_s3_dep,
    ):
        # Mock Redis - no matching job
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request with API key headers
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/approval/invalid-token/decision", json=decision_payload, headers=api_key_headers
            )

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_approval_validation_errors(api_key_headers):
    """Test POST /api/approval/{token}/decision with invalid payload."""
    token = "valid-token-abc"

    # Missing required field
    invalid_payload = {
        "decision": "approved",
        "justification": "Too short",  # Less than 10 characters
    }

    # Make request with API key headers
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/approval/{token}/decision", json=invalid_payload, headers=api_key_headers)

    # Assert validation error
    assert response.status_code == 422  # Unprocessable Entity
