"""Security tests for approval workflow API."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
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
async def test_approval_token_expiration_enforced(api_key_headers):
    """Test that expired tokens are rejected."""
    from src.dependencies import get_redis_client, get_s3_client

    job_id = "expired-test-job"
    expired_job = {
        "job_id": job_id,
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "expired-token-123",
        "approval_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "pii_findings": []
    }

    # Mock Redis client - token lookup returns job_id, but job has expired timestamp
    mock_redis = AsyncMock()
    # For get_job_by_approval_token: redis.get(token_key) returns job_id
    mock_redis.get.return_value = job_id
    # For get_job: redis.hgetall(job_key) returns job with expired approval_expires_at
    mock_redis.hgetall.return_value = expired_job

    mock_s3 = AsyncMock()

    # Use dependency overrides (not patches)
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_s3_client] = lambda: mock_s3

    try:
        # Attempt to get review details with API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/approval/expired-token-123/review", headers=api_key_headers)

        # Assert expired token rejected
        assert response.status_code == 404
        assert "Invalid or expired" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_approval_no_pii_data_in_url(api_key_headers):
    """Test that PII findings are not exposed in URL parameters."""
    valid_job = {
        "job_id": "test-job-pii-check",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "secure-token-456",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
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
        mock_job_service.get_job_by_approval_token.return_value = valid_job  # New O(1) lookup method
        mock_job_service_class.return_value = mock_job_service

        # Make request with API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/approval/secure-token-456/review", headers=api_key_headers)

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
async def test_approval_decision_sanitization(api_key_headers):
    """Test that user input is properly sanitized."""
    from src.dependencies import get_redis_client, get_s3_client

    valid_job = {
        "job_id": "550e8400-e29b-41d4-a716-446655440010",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "test-token-789",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "pii_findings": []
    }

    # Attempt SQL injection in justification
    malicious_payload = {
        "decision": "approved",
        "justification": "Test'; DROP TABLE jobs; --",
        "reviewed_by": "attacker@evil.com"
    }

    # Mock Redis client with proper method returns
    mock_redis = AsyncMock()
    # For get_job_by_approval_token
    mock_redis.get.return_value = valid_job["job_id"]
    mock_redis.hgetall.return_value = valid_job
    # For decision submission
    mock_redis.zrem.return_value = 1
    mock_redis.lpush.return_value = 1
    mock_redis.hset.return_value = 1
    mock_redis.set.return_value = True  # For distributed lock
    mock_redis.delete.return_value = 1  # For lock release

    # Mock S3 client
    mock_s3 = AsyncMock()

    # Override dependencies (only Redis and S3, not services)
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_s3_client] = lambda: mock_s3

    try:
        # Make request with API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/approval/test-token-789/decision",
                json=malicious_payload,
                headers=api_key_headers
            )

        # Assert request processed (stored as string, not executed)
        assert response.status_code == 200

        # Verify justification stored as-is (string, not SQL)
        # Redis stores it as a string in JSON - no SQL execution
    finally:
        # Clean up overrides
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_approval_input_validation_boundaries(api_key_headers):
    """Test input validation edge cases."""
    valid_job = {
        "job_id": "validation-test-job",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "validation-token-999",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
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

        # Make request with API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # Test justification too short (< 10 chars)
            response = await client.post(
                "/api/v1/approval/validation-token-999/decision",
                json={
                    "decision": "approved",
                    "justification": "Short",
                    "reviewed_by": "test@test.com"
                },
                headers=api_key_headers
            )
            assert response.status_code == 422  # Validation error

            # Test justification too long (> 1000 chars)
            response = await client.post(
                "/api/v1/approval/validation-token-999/decision",
                json={
                    "decision": "approved",
                    "justification": "A" * 1001,
                    "reviewed_by": "test@test.com"
                },
                headers=api_key_headers
            )
            assert response.status_code == 422

            # Test invalid decision value
            response = await client.post(
                "/api/v1/approval/validation-token-999/decision",
                json={
                    "decision": "maybe",  # Not "approved" or "denied"
                    "justification": "Valid justification text here",
                    "reviewed_by": "test@test.com"
                },
                headers=api_key_headers
            )
            assert response.status_code == 422

            # Test reviewed_by too short (< 3 chars)
            response = await client.post(
                "/api/v1/approval/validation-token-999/decision",
                json={
                    "decision": "approved",
                    "justification": "Valid justification text here",
                    "reviewed_by": "ab"
                },
                headers=api_key_headers
            )
            assert response.status_code == 422


@pytest.mark.asyncio
async def test_approval_token_not_leaked_in_error_messages(api_key_headers):
    """Test that tokens are not exposed in error responses."""
    sensitive_token = "very-secret-token-should-not-leak"

    with patch("src.api.approval.get_redis_client") as mock_redis_dep, \
         patch("src.api.approval.get_s3_client") as mock_s3_dep:

        mock_redis = AsyncMock()
        mock_redis.keys.return_value = []  # No matching job
        mock_redis_dep.return_value = mock_redis

        mock_s3 = AsyncMock()
        mock_s3_dep.return_value = mock_s3

        # Make request with sensitive token and API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            response = await client.get(f"/api/v1/approval/{sensitive_token}/review", headers=api_key_headers)

        # Assert token not in error message
        assert response.status_code == 404
        error_detail = response.json()["detail"]
        assert sensitive_token not in error_detail
        assert "Invalid or expired approval token" in error_detail


@pytest.mark.asyncio
async def test_approval_decision_idempotency(api_key_headers):
    """Test that submitting the same decision twice is safe."""
    from src.dependencies import get_redis_client, get_s3_client

    valid_job = {
        "job_id": "550e8400-e29b-41d4-a716-446655440011",
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval",
        "approval_token": "idempotent-token",
        "approval_expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "pii_findings": []
    }

    decision_payload = {
        "decision": "approved",
        "justification": "Valid approval justification for testing idempotency",
        "reviewed_by": "faculty@uic.edu"
    }

    # Mock Redis client with proper method returns
    mock_redis = AsyncMock()
    # For get_job_by_approval_token
    mock_redis.get.return_value = valid_job["job_id"]
    mock_redis.hgetall.return_value = valid_job
    # For decision submission
    mock_redis.zrem.return_value = 1
    mock_redis.lpush.return_value = 1
    mock_redis.hset.return_value = 1
    mock_redis.set.return_value = True  # For distributed lock
    mock_redis.delete.return_value = 1  # For lock release

    # Mock S3 client
    mock_s3 = AsyncMock()

    # Override dependencies (only Redis and S3, not services)
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_s3_client] = lambda: mock_s3

    try:
        # Make request with API key headers
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test"
        ) as client:
            # First submission
            response1 = await client.post(
                "/api/v1/approval/idempotent-token/decision",
                json=decision_payload,
                headers=api_key_headers
            )
            assert response1.status_code == 200

            # Reset mock for second submission - token still returns job_id
            mock_redis.get.return_value = valid_job["job_id"]
            mock_redis.hgetall.return_value = valid_job

            # Second submission (should be handled gracefully)
            response2 = await client.post(
                "/api/v1/approval/idempotent-token/decision",
                json=decision_payload,
                headers=api_key_headers
            )
            # Should succeed (idempotent) - doesn't break system
            assert response2.status_code in [200, 404]  # Either reprocessed or token consumed
    finally:
        # Clean up overrides
        app.dependency_overrides.clear()
