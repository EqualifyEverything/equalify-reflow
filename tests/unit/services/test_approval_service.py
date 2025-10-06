"""Tests for approval service."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.approval_service import ApprovalService
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.shared.constants.queues import APPROVAL_TIMEOUT_KEY, PROCESSING_QUEUE
from src.shared.constants.statuses import STATUS_PROCESSING, STATUS_DENIED


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
def mock_job_service():
    """Mock JobService."""
    return AsyncMock(spec=JobService)


@pytest.fixture
def mock_queue_service():
    """Mock QueueService."""
    return AsyncMock(spec=QueueService)


@pytest.fixture
def approval_service(mock_redis_client, mock_s3_client, mock_job_service, mock_queue_service):
    """ApprovalService instance with mocked dependencies."""
    return ApprovalService(
        redis_client=mock_redis_client,
        s3_client=mock_s3_client,
        job_service=mock_job_service,
        queue_service=mock_queue_service
    )


# Token Validation Tests

@pytest.mark.asyncio
async def test_validate_approval_token_valid(approval_service, mock_redis_client, mock_job_service):
    """Test successful token validation."""
    # Arrange
    token = "valid-token-abc123"
    job_id = "test-job-123"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": job_id,
        "approval_token": token,
        "approval_expires_at": expires_at,
        "status": "awaiting_approval"
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is not None
    assert result["job_id"] == job_id
    mock_job_service.get_job_by_approval_token.assert_called_once_with(token)


@pytest.mark.asyncio
async def test_validate_approval_token_expired(approval_service, mock_redis_client, mock_job_service):
    """Test expired token validation."""
    # Arrange
    token = "expired-token-xyz"
    expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()  # Past

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-456",
        "approval_token": token,
        "approval_expires_at": expires_at
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_not_found(approval_service, mock_redis_client, mock_job_service):
    """Test token validation when no matching job found."""
    # Arrange
    token = "nonexistent-token"

    # Mock O(1) token lookup returning None (not found)
    mock_job_service.get_job_by_approval_token.return_value = None

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_missing_expires_at(approval_service, mock_redis_client, mock_job_service):
    """Test token validation when job missing expires_at field."""
    # Arrange
    token = "token-missing-expiry"

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-789",
        "approval_token": token
        # Missing approval_expires_at
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is None


@pytest.mark.asyncio
async def test_validate_approval_token_handles_string_keys(approval_service, mock_redis_client, mock_job_service):
    """Test token validation uses O(1) lookup (no longer needs to handle key formats)."""
    # Arrange
    token = "valid-token"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "test-job-999",
        "approval_token": token,
        "approval_expires_at": expires_at
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is not None
    assert result["job_id"] == "test-job-999"


# Approval Decision Tests

@pytest.mark.asyncio
async def test_process_approval_decision_approved(
    approval_service,
    mock_redis_client,
    mock_job_service,
    mock_queue_service
):
    """Test processing approved decision."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440001"
    s3_key = "temp/approved-doc.pdf"
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": s3_key,
        "status": "awaiting_approval"
    }

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="approved",
        justification="Instructor contact info is acceptable",
        reviewed_by="faculty@uic.edu"
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    mock_queue_service.enqueue.assert_called_once()
    queue_call = mock_queue_service.enqueue.call_args
    assert queue_call[0][0] == PROCESSING_QUEUE
    # ProcessingQueuePayload is a Pydantic model
    payload = queue_call[0][1]
    assert payload.job_id == job_id
    assert payload.s3_key == s3_key
    mock_job_service.update_job_status.assert_called_once()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_PROCESSING


@pytest.mark.asyncio
async def test_process_approval_decision_denied(
    approval_service,
    mock_redis_client,
    mock_job_service,
    mock_s3_client
):
    """Test processing denied decision."""
    # Arrange
    job_id = "denied-job-456"
    s3_key = "temp/denied-doc.pdf"
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": s3_key,
        "status": "awaiting_approval"
    }
    mock_s3_client.delete_object.return_value = {}

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="denied",
        justification="Contains student PII - cannot process",
        reviewed_by="admin@uic.edu"
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    mock_s3_client.delete_object.assert_called_once()
    mock_job_service.update_job_status.assert_called_once()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_DENIED


@pytest.mark.asyncio
async def test_process_approval_decision_job_not_found(
    approval_service,
    mock_job_service
):
    """Test processing decision when job doesn't exist."""
    # Arrange
    mock_job_service.get_job.return_value = None

    # Act & Assert
    with pytest.raises(ValueError, match="Job .* not found"):
        await approval_service.process_approval_decision(
            job_id="nonexistent-job",
            decision="approved",
            justification="Test",
            reviewed_by="test@test.com"
        )


@pytest.mark.asyncio
async def test_process_approval_decision_missing_s3_key(
    approval_service,
    mock_job_service
):
    """Test processing decision when job missing s3_key."""
    # Arrange
    mock_job_service.get_job.return_value = {
        "job_id": "test-job"
        # Missing s3_key
    }

    # Act & Assert
    with pytest.raises(ValueError, match="missing s3_key"):
        await approval_service.process_approval_decision(
            job_id="test-job",
            decision="approved",
            justification="Test",
            reviewed_by="test@test.com"
        )


@pytest.mark.asyncio
async def test_process_approval_stores_decision_metadata(
    approval_service,
    mock_redis_client,
    mock_job_service,
    mock_queue_service
):
    """Test that approval decision stores metadata correctly."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440003"
    justification = "Test justification for approval"
    reviewed_by = "reviewer@uic.edu"
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": "temp/test.pdf",
        "status": "awaiting_approval"
    }

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="approved",
        justification=justification,
        reviewed_by=reviewed_by
    )

    # Assert
    status_call = mock_job_service.update_job_status.call_args
    approval_metadata = status_call[1]["approval_decision"]
    assert approval_metadata["decision"] == "approved"
    assert approval_metadata["justification"] == justification
    assert approval_metadata["reviewed_by"] == reviewed_by
    assert "reviewed_at" in approval_metadata


@pytest.mark.asyncio
async def test_process_denial_continues_on_cleanup_failure(
    approval_service,
    mock_redis_client,
    mock_job_service,
    mock_s3_client
):
    """Test that denial continues even if S3 cleanup fails."""
    # Arrange
    job_id = "cleanup-fail-job"
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": "temp/fail.pdf",
        "status": "awaiting_approval"
    }
    # Simulate S3 cleanup failure
    mock_s3_client.delete_object.side_effect = Exception("S3 error")

    # Act - Should not raise exception
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="denied",
        justification="Test denial",
        reviewed_by="test@test.com"
    )

    # Assert - Status still updated despite cleanup failure
    mock_job_service.update_job_status.assert_called_once()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_DENIED
