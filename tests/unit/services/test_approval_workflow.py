"""Comprehensive tests for approval workflow fixes.

Tests cover:
- Bug #3: Missing timeout tracking for PII-flagged jobs
- Bug #7: O(1) approval token validation (no O(N) scan)
- Bug #14: Validation on approval expiration parse
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.pii_service import PIIDetectionService
from src.services.approval_service import ApprovalService
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.services.storage_service import StorageService
from src.shared.models.queue import PIIQueuePayload
from src.shared.models.pii import PIIFinding
from src.shared.constants.statuses import STATUS_AWAITING_APPROVAL


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_s3_client():
    """Mock S3 client."""
    client = AsyncMock()
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = type('NoSuchKey', (Exception,), {})
    return client


@pytest.fixture
def mock_storage_service():
    """Mock StorageService."""
    return AsyncMock(spec=StorageService)


@pytest.fixture
def mock_queue_service():
    """Mock QueueService with timeout tracking."""
    service = AsyncMock(spec=QueueService)
    service.add_to_timeout_tracking = AsyncMock()
    return service


@pytest.fixture
def mock_job_service():
    """Mock JobService with token mapping methods."""
    service = AsyncMock(spec=JobService)
    service.store_approval_token_mapping = AsyncMock()
    service.get_job_by_approval_token = AsyncMock()
    return service


@pytest.fixture
def pii_service(mock_storage_service, mock_queue_service, mock_job_service):
    """PIIDetectionService instance with mocked dependencies."""
    return PIIDetectionService(
        storage_service=mock_storage_service,
        queue_service=mock_queue_service,
        job_service=mock_job_service
    )


@pytest.fixture
def approval_service(mock_redis_client, mock_s3_client, mock_job_service, mock_queue_service):
    """ApprovalService instance with mocked dependencies."""
    return ApprovalService(
        redis_client=mock_redis_client,
        s3_client=mock_s3_client,
        job_service=mock_job_service,
        queue_service=mock_queue_service
    )


# Bug #3: Missing Timeout Tracking Tests

@pytest.mark.asyncio
async def test_queue_for_approval_adds_timeout_tracking(pii_service, mock_queue_service):
    """Test that PII service adds job to timeout tracking when queueing for approval."""
    # Arrange
    job_payload = PIIQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        s3_key="temp/test.pdf",
        created_at=datetime.now(timezone.utc)
    )
    pii_findings = [
        PIIFinding(
            entity_type="PERSON",
            text="John Doe",
            score=0.95,
            start=10,
            end=18
        )
    ]

    # Act
    await pii_service._queue_for_approval(job_payload, pii_findings)

    # Assert - timeout tracking should be called
    mock_queue_service.add_to_timeout_tracking.assert_called_once()
    call_args = mock_queue_service.add_to_timeout_tracking.call_args
    assert call_args[0][0] == "550e8400-e29b-41d4-a716-446655440000"  # job_id
    # expires_at should be a datetime
    assert isinstance(call_args[0][1], datetime)


@pytest.mark.asyncio
async def test_queue_for_approval_timeout_tracking_with_correct_expiration(
    pii_service,
    mock_queue_service
):
    """Test that timeout tracking uses correct expiration time (4 hours from now)."""
    # Arrange
    job_payload = PIIQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440001",
        s3_key="temp/test2.pdf",
        created_at=datetime.now(timezone.utc)
    )
    pii_findings = [
        PIIFinding(
            entity_type="EMAIL_ADDRESS",
            text="test@example.com",
            score=0.99,
            start=0,
            end=16
        )
    ]

    before_time = datetime.now(timezone.utc)

    # Act
    await pii_service._queue_for_approval(job_payload, pii_findings)

    after_time = datetime.now(timezone.utc)

    # Assert
    call_args = mock_queue_service.add_to_timeout_tracking.call_args
    expires_at = call_args[0][1]

    # Should be approximately 4 hours from now
    expected_min = before_time + timedelta(hours=4)
    expected_max = after_time + timedelta(hours=4)

    assert expected_min <= expires_at <= expected_max


# Bug #7: O(1) Token Lookup Tests

@pytest.mark.asyncio
async def test_queue_for_approval_stores_token_mapping(pii_service, mock_job_service):
    """Test that PII service stores token-to-job mapping for O(1) lookup."""
    # Arrange
    job_payload = PIIQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440002",
        s3_key="temp/test3.pdf",
        created_at=datetime.now(timezone.utc)
    )
    pii_findings = [
        PIIFinding(
            entity_type="PHONE_NUMBER",
            text="555-1234",
            score=0.88,
            start=20,
            end=28
        )
    ]

    # Act
    await pii_service._queue_for_approval(job_payload, pii_findings)

    # Assert - token mapping should be stored
    mock_job_service.store_approval_token_mapping.assert_called_once()
    call_args = mock_job_service.store_approval_token_mapping.call_args

    # First arg is approval_token (should be non-empty string)
    approval_token = call_args[0][0]
    assert isinstance(approval_token, str)
    assert len(approval_token) > 0

    # Second arg is job_id
    assert call_args[0][1] == "550e8400-e29b-41d4-a716-446655440002"

    # TTL should be 4 hours
    assert call_args[1]["ttl_hours"] == 4


@pytest.mark.asyncio
async def test_validate_approval_token_uses_o1_lookup(approval_service, mock_job_service):
    """Test that token validation uses O(1) Redis lookup, not O(N) scan."""
    # Arrange
    token = "test-token-abc123"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-999",
        "approval_token": token,
        "approval_expires_at": expires_at,
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should use O(1) lookup method
    mock_job_service.get_job_by_approval_token.assert_called_once_with(token)

    # Should NOT call Redis keys() method (O(N) operation)
    # approval_service.redis.keys should not be called at all
    approval_service.redis.keys.assert_not_called()

    # Result should be valid
    assert result is not None
    assert result["job_id"] == "test-job-999"


@pytest.mark.asyncio
async def test_token_lookup_performance_vs_scan(approval_service, mock_job_service):
    """Test that O(1) token lookup is used instead of scanning all jobs."""
    # Arrange
    token = "performance-test-token"
    mock_job_service.get_job_by_approval_token.return_value = None

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should use O(1) method exactly once
    assert mock_job_service.get_job_by_approval_token.call_count == 1

    # Should NOT iterate through job keys
    approval_service.redis.keys.assert_not_called()

    # Result should be None (token not found)
    assert result is None


@pytest.mark.asyncio
async def test_job_service_stores_token_mapping_with_ttl(mock_redis_client):
    """Test that JobService stores token mapping with correct TTL."""
    # Arrange
    job_service = JobService(mock_redis_client)
    approval_token = "secure-token-xyz789"
    job_id = "job-uuid-123"
    ttl_hours = 4

    # Act
    await job_service.store_approval_token_mapping(approval_token, job_id, ttl_hours)

    # Assert
    expected_key = f"eq-pdf:approval-token:{approval_token}"
    expected_ttl = ttl_hours * 3600  # 4 hours in seconds

    mock_redis_client.set.assert_called_once_with(
        expected_key,
        job_id,
        ex=expected_ttl
    )


@pytest.mark.asyncio
async def test_job_service_retrieves_job_by_token(mock_redis_client):
    """Test that JobService retrieves job using token mapping."""
    # Arrange
    job_service = JobService(mock_redis_client)
    approval_token = "lookup-token-456"
    job_id = "job-uuid-456"

    # Mock Redis responses
    mock_redis_client.get.return_value = job_id.encode('utf-8')
    mock_redis_client.hgetall.return_value = {
        "job_id": job_id,
        "status": STATUS_AWAITING_APPROVAL,
        "s3_key": "temp/test.pdf"
    }

    # Act
    result = await job_service.get_job_by_approval_token(approval_token)

    # Assert
    expected_token_key = f"eq-pdf:approval-token:{approval_token}"
    mock_redis_client.get.assert_called_once_with(expected_token_key)

    # Should retrieve full job data
    assert result is not None
    assert result["job_id"] == job_id


# Bug #14: Expiration Timestamp Validation Tests

@pytest.mark.asyncio
async def test_validate_approval_token_handles_invalid_iso_format(
    approval_service,
    mock_job_service
):
    """Test that validation handles malformed ISO timestamp gracefully."""
    # Arrange
    token = "invalid-timestamp-token"
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-invalid",
        "approval_token": token,
        "approval_expires_at": "not-a-valid-timestamp",  # Invalid format
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should return None without raising exception
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_handles_none_timestamp(
    approval_service,
    mock_job_service
):
    """Test that validation handles None expiration timestamp."""
    # Arrange
    token = "none-timestamp-token"
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-none",
        "approval_token": token,
        "approval_expires_at": None,  # Missing expiration
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should return None
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_handles_missing_expires_field(
    approval_service,
    mock_job_service
):
    """Test that validation handles missing expires_at field."""
    # Arrange
    token = "missing-expires-token"
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-missing",
        "approval_token": token,
        # Missing approval_expires_at field entirely
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should return None
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_handles_z_suffix_timestamp(
    approval_service,
    mock_job_service
):
    """Test that validation handles timestamps with Z suffix (UTC indicator)."""
    # Arrange
    token = "z-suffix-token"
    # Use Z suffix (common in some ISO formats)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-z",
        "approval_token": token,
        "approval_expires_at": expires_at,
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should handle Z suffix correctly
    assert result is not None
    assert result["job_id"] == "test-job-z"


@pytest.mark.asyncio
async def test_validate_approval_token_rejects_naive_datetime(
    approval_service,
    mock_job_service
):
    """Test that validation rejects naive datetime timestamps."""
    # Arrange
    token = "naive-datetime-token"
    # Create naive datetime (no timezone info) - this should NEVER happen in production
    naive_time = datetime.now() + timedelta(hours=2)
    expires_at = naive_time.isoformat()  # No timezone

    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-naive",
        "approval_token": token,
        "approval_expires_at": expires_at,
        "status": STATUS_AWAITING_APPROVAL
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert - should reject naive datetime and return None
    assert result is None


# Integration Tests

@pytest.mark.asyncio
async def test_full_approval_workflow_with_all_fixes(
    pii_service,
    mock_queue_service,
    mock_job_service
):
    """Integration test: Full approval workflow uses all three bug fixes."""
    # Arrange
    job_payload = PIIQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440003",
        s3_key="temp/integration-test.pdf",
        created_at=datetime.now(timezone.utc)
    )
    pii_findings = [
        PIIFinding(
            entity_type="PERSON",
            text="Integration Test",
            score=0.90,
            start=0,
            end=16
        )
    ]

    # Act
    await pii_service._queue_for_approval(job_payload, pii_findings)

    # Assert all three fixes are applied:

    # Fix #3: Timeout tracking is added
    assert mock_queue_service.add_to_timeout_tracking.called
    timeout_call = mock_queue_service.add_to_timeout_tracking.call_args
    assert timeout_call[0][0] == "550e8400-e29b-41d4-a716-446655440003"

    # Fix #7: Token mapping is stored for O(1) lookup
    assert mock_job_service.store_approval_token_mapping.called
    token_call = mock_job_service.store_approval_token_mapping.call_args
    assert token_call[0][1] == "550e8400-e29b-41d4-a716-446655440003"
    assert token_call[1]["ttl_hours"] == 4

    # Both should be called exactly once
    assert mock_queue_service.add_to_timeout_tracking.call_count == 1
    assert mock_job_service.store_approval_token_mapping.call_count == 1


@pytest.mark.asyncio
async def test_multiple_approvals_work_correctly(pii_service, mock_queue_service, mock_job_service):
    """Test that multiple approval jobs are tracked independently."""
    # Arrange
    job_ids = [
        "550e8400-e29b-41d4-a716-446655440004",
        "550e8400-e29b-41d4-a716-446655440005",
        "550e8400-e29b-41d4-a716-446655440006"
    ]
    jobs = [
        PIIQueuePayload(
            job_id=job_id,
            s3_key=f"temp/file-{i}.pdf",
            created_at=datetime.now(timezone.utc)
        )
        for i, job_id in enumerate(job_ids)
    ]
    # Act - Queue multiple jobs
    for i, job in enumerate(jobs):
        findings = [
            PIIFinding(
                entity_type="PERSON",
                text=f"Person {i}",
                score=0.90,
                start=0,
                end=8
            )
        ]
        await pii_service._queue_for_approval(job, findings)

    # Assert - Each job should have timeout tracking and token mapping
    assert mock_queue_service.add_to_timeout_tracking.call_count == 3
    assert mock_job_service.store_approval_token_mapping.call_count == 3

    # Verify each job_id was tracked
    timeout_job_ids = [
        call[0][0] for call in mock_queue_service.add_to_timeout_tracking.call_args_list
    ]
    assert set(timeout_job_ids) == set(job_ids)

    # Verify each job_id has token mapping
    token_job_ids = [
        call[0][1] for call in mock_job_service.store_approval_token_mapping.call_args_list
    ]
    assert set(token_job_ids) == set(job_ids)


@pytest.mark.asyncio
async def test_token_expiration_matches_timeout_tracking(pii_service, mock_queue_service, mock_job_service):
    """Test that token TTL matches timeout tracking expiration."""
    # Arrange
    job_payload = PIIQueuePayload(
        job_id="550e8400-e29b-41d4-a716-446655440007",
        s3_key="temp/ttl-test.pdf",
        created_at=datetime.now(timezone.utc)
    )
    findings = [
        PIIFinding(entity_type="PERSON", text="Test", score=0.9, start=0, end=4)
    ]

    # Act
    await pii_service._queue_for_approval(job_payload, findings)

    # Assert - Both should use same expiration time
    timeout_expires = mock_queue_service.add_to_timeout_tracking.call_args[0][1]
    token_ttl_hours = mock_job_service.store_approval_token_mapping.call_args[1]["ttl_hours"]

    # TTL should be 4 hours
    assert token_ttl_hours == 4

    # Timeout expiration should be approximately 4 hours from now
    expected_timeout = datetime.now(timezone.utc) + timedelta(hours=4)
    time_diff = abs((timeout_expires - expected_timeout).total_seconds())
    assert time_diff < 5  # Within 5 seconds tolerance
