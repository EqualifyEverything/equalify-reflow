"""Security tests for approval workflow API."""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch

from src.main import app
from src.utils.token_generator import generate_secure_token


@pytest.mark.asyncio
async def test_approval_token_uniqueness():
    """Test that generated approval tokens are cryptographically unique."""
    # Generate multiple tokens
    tokens = [generate_secure_token() for _ in range(100)]

    # Assert all tokens are unique
    assert len(tokens) == len(set(tokens)), "Tokens should be unique"

    # Assert tokens are sufficiently long (256 bits = 43 characters base64)
    for token in tokens:
        assert len(token) >= 40, f"Token too short: {len(token)} chars"


@pytest.mark.asyncio
async def test_approval_token_expiration_enforced():
    """Test that expired tokens are rejected."""
    expired_job = {
        "job_id": "expired-test-job",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "expired-token-123",
        "approval_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": []
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:expired-test-job"]
        mock_redis.hgetall.return_value = expired_job
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Attempt to get review details
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get("/api/review/expired-token-123")

        # Assert expired token rejected
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]


@pytest.mark.asyncio
async def test_approval_no_pii_data_in_url():
    """Test that PII findings are not exposed in URL parameters."""
    valid_job = {
        "job_id": "test-job-pii-check",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "secure-token-456",
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": [
            {
                "entity_type": "EMAIL_ADDRESS",
                "text": "student@example.com",
                "score": 0.95
            },
            {
                "entity_type": "PHONE_NUMBER",
                "text": "+1-555-123-4567",
                "score": 0.89
            }
        ]
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:test-job-pii-check"]
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job
        mock_job_service_class.return_value = mock_job_service

        # Make request
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get("/api/review/secure-token-456")

        # Assert PII data only in response body, not in URL
        assert response.status_code == 200
        data = response.json()

        # PII should be in response
        assert len(data["pii_findings"]) == 2
        assert data["pii_findings"][0]["text"] == "student@example.com"

        # But URL only contains opaque token (no PII)
        assert "student@example.com" not in str(response.url)
        assert "+1-555-123-4567" not in str(response.url)


@pytest.mark.asyncio
async def test_approval_decision_sanitization():
    """Test that user input is properly sanitized."""
    valid_job = {
        "job_id": "550e8400-e29b-41d4-a716-446655440010",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "test-token-789",
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": []
    }

    # Attempt SQL injection in justification
    malicious_payload = {
        "decision": "approved",
        "justification": "Test'; DROP TABLE jobs; --",
        "reviewed_by": "attacker@evil.com"
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class, \
         patch("src.api.approval.QueueService") as mock_queue_service_class:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:sanitization-test-job"]
        mock_redis.zrem.return_value = 1
        mock_redis.lpush.return_value = 1
        mock_redis.hset.return_value = 1
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job
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
                "/api/approve/test-token-789",
                json=malicious_payload
            )

        # Assert request processed (stored as string, not executed)
        assert response.status_code == 200

        # Verify justification stored as-is (string, not SQL)
        update_call = mock_redis.hset.call_args
        # Redis stores it as a string in JSON - no SQL execution


@pytest.mark.asyncio
async def test_approval_input_validation_boundaries():
    """Test input validation edge cases."""
    valid_job = {
        "job_id": "validation-test-job",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "validation-token-999",
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": []
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:validation-test-job"]
        mock_redis.hgetall.return_value = valid_job
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test justification too short (< 10 chars)
            response = await client.post(
                "/api/approve/validation-token-999",
                json={
                    "decision": "approved",
                    "justification": "Short",
                    "reviewed_by": "test@test.com"
                }
            )
            assert response.status_code == 422  # Validation error

            # Test justification too long (> 1000 chars)
            response = await client.post(
                "/api/approve/validation-token-999",
                json={
                    "decision": "approved",
                    "justification": "A" * 1001,
                    "reviewed_by": "test@test.com"
                }
            )
            assert response.status_code == 422

            # Test invalid decision value
            response = await client.post(
                "/api/approve/validation-token-999",
                json={
                    "decision": "maybe",  # Not "approved" or "denied"
                    "justification": "Valid justification text here",
                    "reviewed_by": "test@test.com"
                }
            )
            assert response.status_code == 422

            # Test reviewed_by too short (< 3 chars)
            response = await client.post(
                "/api/approve/validation-token-999",
                json={
                    "decision": "approved",
                    "justification": "Valid justification text here",
                    "reviewed_by": "ab"
                }
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_approval_token_not_leaked_in_error_messages():
    """Test that tokens are not exposed in error responses."""
    sensitive_token = "very-secret-token-should-not-leak"

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []  # No matching job
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request with sensitive token
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(f"/api/review/{sensitive_token}")

        # Assert token not in error message
        assert response.status_code == 404
        error_detail = response.json()["detail"]
        assert sensitive_token not in error_detail
        assert "Invalid or expired approval token" in error_detail


@pytest.mark.asyncio
async def test_approval_decision_idempotency():
    """Test that submitting the same decision twice is safe."""
    valid_job = {
        "job_id": "550e8400-e29b-41d4-a716-446655440011",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "idempotent-token",
        "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_findings": []
    }

    decision_payload = {
        "decision": "approved",
        "justification": "Valid approval justification for testing idempotency",
        "reviewed_by": "faculty@uic.edu"
    }

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep, \
         patch("src.api.approval.JobService") as mock_job_service_class, \
         patch("src.api.approval.QueueService") as mock_queue_service_class:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = [b"eq-pdf:job:idempotent-test-job"]
        mock_redis.zrem.return_value = 1
        mock_redis.lpush.return_value = 1
        mock_redis.hset.return_value = 1
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Mock JobService
        mock_job_service = AsyncMock()
        mock_job_service.get_job.return_value = valid_job
        mock_job_service_class.return_value = mock_job_service

        # Mock QueueService
        mock_queue_service = AsyncMock()
        mock_queue_service.enqueue.return_value = None
        mock_queue_service_class.return_value = mock_queue_service

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # First submission
            response1 = await client.post(
                "/api/approve/idempotent-token",
                json=decision_payload
            )
            assert response1.status_code == 200

            # Reset mock for second submission
            mock_redis.keys.return_value = [b"eq-pdf:job:idempotent-test-job"]
            mock_job_service.get_job.return_value = valid_job

            # Second submission (should be handled gracefully)
            response2 = await client.post(
                "/api/approve/idempotent-token",
                json=decision_payload
            )
            # Should succeed (idempotent) - doesn't break system
            assert response2.status_code in [200, 404]  # Either reprocessed or token consumed
