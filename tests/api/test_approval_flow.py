"""Integration tests for approval workflow API endpoints."""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from src.main import app


@pytest.fixture
def valid_job_data():
    """Valid job data for approval workflow."""
    return {
        "job_id": "550e8400-e29b-41d4-a716-446655440000",
        "s3_key": "temp/test-doc.pdf",
        "status": "awaiting_approval",
        "approval_token": "valid-token-abc123",
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": [
            {
                "entity_type": "EMAIL_ADDRESS",
                "text": "student@example.com",
                "score": 0.95
            }
        ]
    }


@pytest.fixture
def expired_job_data():
    """Job data with expired approval token."""
    return {
        "job_id": "expired-job-456",
        "s3_key": "temp/expired-doc.pdf",
        "status": "awaiting_approval",
        "approval_token": "expired-token-xyz",
        "approval_expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": []
    }


@pytest.mark.asyncio
async def test_get_review_details_valid_token(valid_job_data):
    """Test GET /api/approval/review/{token} with valid token."""
    token = valid_job_data["approval_token"]

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class:

        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:test-job-123"]
        mock_redis_dep.return_value = mock_redis

        # Mock S3 client
        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job_data
        mock_job_service_class.return_value = mock_job_service

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(f"/api/approval/review/{token}")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == valid_job_data["job_id"]
        assert data["status"] == "awaiting_approval"
        assert len(data["pii_findings"]) == 1
        assert data["pii_findings"][0]["entity_type"] == "EMAIL_ADDRESS"


@pytest.mark.asyncio
async def test_get_review_details_invalid_token():
    """Test GET /api/approval/review/{token} with invalid token."""
    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        # Mock Redis - no matching job
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get("/api/approval/review/invalid-token-999")

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_review_details_expired_token(expired_job_data):
    """Test GET /api/approval/review/{token} with expired token."""
    token = expired_job_data["approval_token"]

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class:

        # Mock Redis with expired job
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:expired-job-456"]
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = expired_job_data
        mock_job_service_class.return_value = mock_job_service

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(f"/api/approval/review/{token}")

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_approval_approved_decision(valid_job_data):
    """Test POST /api/approval/{token}/approve with approved decision."""
    token = valid_job_data["approval_token"]
    decision_payload = {
        "decision": "approved",
        "justification": "Instructor contact information is acceptable for course materials",
        "reviewed_by": "faculty@uic.edu"
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class, \
         patch("src.api.approval.QueueService") as mock_queue_service_class:

        # Mock Redis client
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:test-job-123"]
        mock_redis.lpush.return_value = 1
        mock_redis.zrem.return_value = 1
        mock_redis.hset.return_value = 1
        mock_redis_dep.return_value = mock_redis

        # Mock S3 client
        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job_data
        mock_job_service_class.return_value = mock_job_service

        # Mock QueueService
        mock_queue_service = AsyncMock()
        mock_queue_service.enqueue.return_value = None
        mock_queue_service_class.return_value = mock_queue_service

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/approval/{token}/approve",
                json=decision_payload
            )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "approved"
        assert data["job_id"] == valid_job_data["job_id"]
        assert "approved for processing" in data["message"]

        # Verify queue service was called (via mock)
        mock_queue_service.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_submit_approval_denied_decision(valid_job_data):
    """Test POST /api/approval/{token}/approve with denied decision."""
    token = valid_job_data["approval_token"]
    decision_payload = {
        "decision": "denied",
        "justification": "Contains student PII that cannot be processed per university policy",
        "reviewed_by": "admin@uic.edu"
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class, \
         patch("src.api.approval.QueueService") as mock_queue_service_class, \
         patch("src.services.cleanup_service.CleanupService.cleanup_job_files", new_callable=AsyncMock) as mock_cleanup:

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
        mock_job_service_class.return_value = mock_job_service

        # Mock QueueService
        mock_queue_service = AsyncMock()
        mock_queue_service_class.return_value = mock_queue_service

        # Mock cleanup service
        mock_cleanup.return_value = True

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/approval/{token}/approve",
                json=decision_payload
            )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "denied"
        assert data["job_id"] == valid_job_data["job_id"]
        assert "denied and cleaned up" in data["message"]

        # Verify cleanup was called
        mock_cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_submit_approval_invalid_token():
    """Test POST /api/approval/{token}/approve with invalid token."""
    decision_payload = {
        "decision": "approved",
        "justification": "Test justification",
        "reviewed_by": "test@test.com"
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        # Mock Redis - no matching job
        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/approval/invalid-token/approve",
                json=decision_payload
            )

        # Assert
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_approval_validation_errors():
    """Test POST /api/approval/{token}/approve with invalid payload."""
    token = "valid-token-abc"

    # Missing required field
    invalid_payload = {
        "decision": "approved",
        "justification": "Too short"  # Less than 10 characters
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approval/{token}/approve",
            json=invalid_payload
        )

    # Assert validation error
    assert response.status_code == 422  # Unprocessable Entity
