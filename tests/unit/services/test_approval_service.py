"""Tests for approval service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.services.approval_service import ApprovalService
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.shared.constants.queues import APPROVAL_TIMEOUT_KEY
from src.shared.constants.statuses import (
    STATUS_DENIED,
    STATUS_PROCESSING,
    STATUS_PROCESSING_QUEUED,
)


@pytest.fixture
def mock_redis_client():
    """Mock Redis client.

    Uses MagicMock as container with AsyncMock for async methods.
    This prevents unawaited coroutine warnings when tests set return_value.
    """
    mock = MagicMock()
    mock.set = AsyncMock(return_value=True)
    mock.zrem = AsyncMock(return_value=1)
    mock.delete = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def mock_s3_client():
    """Mock S3 client."""
    client = AsyncMock()
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
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
def mock_storage_service():
    """Mock StorageService."""
    return AsyncMock()


@pytest.fixture
def mock_s3_url_service():
    """Mock S3URLService."""
    return MagicMock()


@pytest.fixture
def approval_service(
    mock_redis_client, mock_s3_client, mock_job_service, mock_queue_service, mock_storage_service, mock_s3_url_service
):
    """ApprovalService instance with mocked dependencies."""
    return ApprovalService(
        redis_client=mock_redis_client,
        s3_client=mock_s3_client,
        job_service=mock_job_service,
        queue_service=mock_queue_service,
        storage_service=mock_storage_service,
        s3_url_service=mock_s3_url_service,
    )


# Token Validation Tests


@pytest.mark.asyncio
async def test_validate_approval_token_valid(approval_service, mock_redis_client, mock_job_service):
    """Test successful token validation."""
    # Arrange
    token = "valid-token-abc123"
    job_id = "550e8400-e29b-41d4-a716-446655440010"
    expires_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": job_id,
        "approval_token": token,
        "approval_expires_at": expires_at,
        "status": "awaiting_approval",
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
    expires_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()  # Past

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "550e8400-e29b-41d4-a716-446655440011",
        "approval_token": token,
        "approval_expires_at": expires_at,
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
        "job_id": "550e8400-e29b-41d4-a716-446655440012",
        "approval_token": token,
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
    expires_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    # Mock O(1) token lookup
    mock_job_service.get_job_by_approval_token.return_value = {
        "job_id": "550e8400-e29b-41d4-a716-446655440013",
        "approval_token": token,
        "approval_expires_at": expires_at,
    }

    # Act
    result = await approval_service.validate_approval_token(token)

    # Assert
    assert result is not None
    assert result["job_id"] == "550e8400-e29b-41d4-a716-446655440013"


# Approval Decision Tests


@pytest.mark.asyncio
async def test_process_approval_decision_approved(
    approval_service, mock_redis_client, mock_job_service, mock_queue_service
):
    """Test processing approved decision triggers inline processing."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440001"
    s3_key = "temp/approved-doc.pdf"
    mock_job_service.get_job.return_value = {"job_id": job_id, "s3_key": s3_key, "status": "awaiting_approval"}

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="approved",
        justification="Instructor contact info is acceptable",
        reviewed_by="faculty@uic.edu",
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    # Processing is now triggered inline via DocumentProcessingService, not enqueued
    # So we verify the status update instead of queue enqueue
    mock_job_service.update_job_status.assert_called()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_PROCESSING


@pytest.mark.asyncio
async def test_process_approval_decision_denied(approval_service, mock_redis_client, mock_job_service, mock_s3_client):
    """Test processing denied decision."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440014"
    s3_key = "temp/denied-doc.pdf"
    mock_job_service.get_job.return_value = {"job_id": job_id, "s3_key": s3_key, "status": "awaiting_approval"}
    mock_s3_client.delete_object.return_value = {}

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id,
        decision="denied",
        justification="Contains student PII - cannot process",
        reviewed_by="admin@uic.edu",
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    mock_s3_client.delete_object.assert_called_once()
    mock_job_service.update_job_status.assert_called_once()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_DENIED


@pytest.mark.asyncio
async def test_process_approval_decision_job_not_found(approval_service, mock_job_service):
    """Test processing decision when job doesn't exist."""
    # Arrange
    mock_job_service.get_job.return_value = None

    # Act & Assert
    with pytest.raises(ValueError, match="Job .* not found"):
        await approval_service.process_approval_decision(
            job_id="550e8400-e29b-41d4-a716-446655440015",
            decision="approved",
            justification="Test",
            reviewed_by="test@test.com",
        )


@pytest.mark.asyncio
async def test_process_approval_decision_missing_s3_key(approval_service, mock_job_service):
    """Test processing decision when job missing s3_key."""
    # Arrange
    mock_job_service.get_job.return_value = {
        "job_id": "550e8400-e29b-41d4-a716-446655440016"
        # Missing s3_key
    }

    # Act & Assert
    with pytest.raises(ValueError, match="missing s3_key"):
        await approval_service.process_approval_decision(
            job_id="550e8400-e29b-41d4-a716-446655440016",
            decision="approved",
            justification="Test",
            reviewed_by="test@test.com",
        )


@pytest.mark.asyncio
async def test_process_approval_stores_decision_metadata(
    approval_service, mock_redis_client, mock_job_service, mock_queue_service
):
    """Test that approval decision stores metadata correctly."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440003"
    justification = "Test justification for approval"
    reviewed_by = "reviewer@uic.edu"
    mock_job_service.get_job.return_value = {"job_id": job_id, "s3_key": "temp/test.pdf", "status": "awaiting_approval"}

    # Act
    await approval_service.process_approval_decision(
        job_id=job_id, decision="approved", justification=justification, reviewed_by=reviewed_by
    )

    # Assert - check that update_job_status was called with approval_decision metadata
    mock_job_service.update_job_status.assert_called()
    # Find the call that includes approval_decision (may be called multiple times)
    approval_call = None
    for call in mock_job_service.update_job_status.call_args_list:
        if "approval_decision" in call[1]:
            approval_call = call
            break

    assert approval_call is not None, "update_job_status should be called with approval_decision"
    approval_metadata = approval_call[1]["approval_decision"]
    assert approval_metadata["decision"] == "approved"
    assert approval_metadata["justification"] == justification
    assert approval_metadata["reviewed_by"] == reviewed_by
    assert "reviewed_at" in approval_metadata


@pytest.mark.asyncio
async def test_process_denial_continues_on_cleanup_failure(
    approval_service, mock_redis_client, mock_job_service, mock_s3_client
):
    """Test that denial continues even if S3 cleanup fails."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440017"
    mock_job_service.get_job.return_value = {"job_id": job_id, "s3_key": "temp/fail.pdf", "status": "awaiting_approval"}
    # Simulate S3 cleanup failure
    mock_s3_client.delete_object.side_effect = Exception("S3 error")

    # Act - Should not raise exception
    await approval_service.process_approval_decision(
        job_id=job_id, decision="denied", justification="Test denial", reviewed_by="test@test.com"
    )

    # Assert - Status still updated despite cleanup failure
    mock_job_service.update_job_status.assert_called_once()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_DENIED


# Instant Response Methods Tests (quick_approve, quick_deny)


@pytest.mark.asyncio
async def test_quick_approve_sets_processing_queued_status(approval_service, mock_job_service):
    """Test quick_approve sets status to processing_queued immediately."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440018"

    # Act
    await approval_service.quick_approve(job_id)

    # Assert
    mock_job_service.update_job_status.assert_called_once()
    call_args = mock_job_service.update_job_status.call_args
    assert call_args[0][0] == job_id
    assert call_args[0][1] == STATUS_PROCESSING_QUEUED
    assert "approved_at" in call_args[1]


@pytest.mark.asyncio
async def test_quick_deny_sets_denied_status(approval_service, mock_job_service):
    """Test quick_deny sets status to denied immediately."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440019"

    # Act
    await approval_service.quick_deny(job_id)

    # Assert
    mock_job_service.update_job_status.assert_called_once()
    call_args = mock_job_service.update_job_status.call_args
    assert call_args[0][0] == job_id
    assert call_args[0][1] == STATUS_DENIED
    assert "denied_at" in call_args[1]


# Background Processing Methods Tests


@pytest.mark.asyncio
async def test_process_approval_background_triggers_processing(
    approval_service, mock_redis_client, mock_job_service, mock_queue_service
):
    """Test background approval triggers inline processing."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440020"
    s3_key = "temp/bg-test.pdf"
    mock_redis_client.set.return_value = True  # Lock acquired
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": s3_key,
        "status": STATUS_PROCESSING_QUEUED,
    }

    # Act
    await approval_service.process_approval_background(
        job_id=job_id,
        s3_key=s3_key,
        justification="Background test approval",
        reviewed_by="bg-test@uic.edu",
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    # Processing is now triggered inline via DocumentProcessingService, not enqueued
    # Verify final status update to STATUS_PROCESSING
    mock_job_service.update_job_status.assert_called()
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_PROCESSING


@pytest.mark.asyncio
async def test_process_approval_background_skips_if_lock_not_acquired(
    approval_service, mock_redis_client, mock_job_service, mock_queue_service
):
    """Test background approval skips processing if lock already held."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440021"
    mock_redis_client.set.return_value = False  # Lock NOT acquired

    # Act
    await approval_service.process_approval_background(
        job_id=job_id,
        s3_key="temp/test.pdf",
        justification="Test",
        reviewed_by="test@test.com",
    )

    # Assert - Should not enqueue or update status
    mock_queue_service.enqueue.assert_not_called()
    mock_job_service.update_job_status.assert_not_called()


@pytest.mark.asyncio
async def test_process_approval_background_skips_if_wrong_status(
    approval_service, mock_redis_client, mock_job_service, mock_queue_service
):
    """Test background approval skips if job status changed."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440022"
    mock_redis_client.set.return_value = True  # Lock acquired
    mock_job_service.get_job.return_value = {
        "job_id": job_id,
        "s3_key": "temp/test.pdf",
        "status": STATUS_PROCESSING,  # Already processing (not processing_queued)
    }

    # Act
    await approval_service.process_approval_background(
        job_id=job_id,
        s3_key="temp/test.pdf",
        justification="Test",
        reviewed_by="test@test.com",
    )

    # Assert - Should not enqueue or update status
    mock_queue_service.enqueue.assert_not_called()
    mock_job_service.update_job_status.assert_not_called()


@pytest.mark.asyncio
async def test_process_denial_background_cleans_up_s3(
    approval_service, mock_redis_client, mock_job_service, mock_s3_client
):
    """Test background denial cleans up S3 files."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440023"
    s3_key = "temp/bg-deny-test.pdf"
    mock_s3_client.delete_object.return_value = {}

    # Act
    await approval_service.process_denial_background(
        job_id=job_id,
        s3_key=s3_key,
        justification="Background denial test",
        reviewed_by="bg-deny@uic.edu",
    )

    # Assert
    mock_redis_client.zrem.assert_called_once_with(APPROVAL_TIMEOUT_KEY, job_id)
    mock_s3_client.delete_object.assert_called_once()

    # Verify status update with decision metadata
    status_call = mock_job_service.update_job_status.call_args
    assert status_call[0][1] == STATUS_DENIED
    assert "denial_decision" in status_call[1]
    denial_metadata = status_call[1]["denial_decision"]
    assert denial_metadata["decision"] == "denied"
    assert denial_metadata["justification"] == "Background denial test"
    assert denial_metadata["reviewed_by"] == "bg-deny@uic.edu"


@pytest.mark.asyncio
async def test_process_denial_background_continues_on_s3_failure(
    approval_service, mock_redis_client, mock_job_service, mock_s3_client
):
    """Test background denial continues even if S3 cleanup fails."""
    # Arrange
    job_id = "550e8400-e29b-41d4-a716-446655440024"
    mock_s3_client.delete_object.side_effect = Exception("S3 error")

    # Act - Should not raise
    await approval_service.process_denial_background(
        job_id=job_id,
        s3_key="temp/fail.pdf",
        justification="Test",
        reviewed_by="test@test.com",
    )

    # Assert - Status still updated
    mock_job_service.update_job_status.assert_called_once()
