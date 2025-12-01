"""Tests for correction approval service (Phase 7 refactoring)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from io import BytesIO

from src.services.correction_approval_service import CorrectionApprovalService
from src.services.job_service import JobService
from src.services.storage_service import StorageService
from src.shared.constants.statuses import STATUS_COMPLETED


@pytest.fixture
def mock_redis_client():
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def mock_s3_client():
    """Mock S3 client."""
    client = MagicMock()
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = type('NoSuchKey', (Exception,), {})
    return client


@pytest.fixture
def mock_job_service():
    """Mock JobService."""
    return AsyncMock(spec=JobService)


@pytest.fixture
def mock_storage_service(mock_s3_client):
    """Mock StorageService."""
    storage = AsyncMock(spec=StorageService)
    storage.s3_client = mock_s3_client
    storage.results_bucket = "test-results-bucket"
    storage.temp_bucket = "test-temp-bucket"
    return storage


@pytest.fixture
def correction_approval_service(mock_redis_client, mock_job_service, mock_storage_service):
    """CorrectionApprovalService instance with mocked dependencies."""
    return CorrectionApprovalService(
        redis_client=mock_redis_client,
        job_service=mock_job_service,
        storage_service=mock_storage_service
    )


# Phase 7 Tests - Approval Path

@pytest.mark.asyncio
async def test_process_approval_downloads_corrected_key(
    correction_approval_service,
    mock_redis_client,
    mock_job_service,
    mock_storage_service
):
    """Test approval downloads corrected markdown using top-level key."""
    # Arrange
    job_id = "test-job-123"
    corrected_key = "results/test-job-123-awaiting-approval.md"
    corrected_content = b"# Corrected Markdown\n\nThis is the corrected version."

    # Mock job data with top-level fields (not metadata)
    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": corrected_key,
        "original_markdown_key": "results/test-job-123-original.md",
        "correction_approval_token": "test-token",
    }

    # Mock Redis lock
    mock_redis_client.set.return_value = True
    mock_redis_client.zrem.return_value = 1
    mock_redis_client.delete.return_value = 1

    # Mock job service
    mock_job_service.get_job.return_value = job_data

    # Mock S3 download (corrected markdown)
    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, key: BytesIO(corrected_content) if key == 'Body' else None
    mock_storage_service.s3_client.get_object.return_value = mock_response

    # Mock upload_result to return final key
    mock_storage_service.upload_result.return_value = f"{job_id}.md"

    decision = {
        "decision": "approved",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Corrections improve structure"
    }

    # Act
    await correction_approval_service.process_correction_approval_decision(job_id, decision)

    # Assert - Downloaded from corrected key
    mock_storage_service.s3_client.get_object.assert_called_once_with(
        Bucket="test-results-bucket",
        Key=corrected_key
    )

    # Assert - Uploaded to final location
    mock_storage_service.upload_result.assert_called_once_with(
        job_id=job_id,
        content=corrected_content,
        format="md"
    )

    # Assert - Decision fields stored at top level (not as blob)
    mock_job_service.update_job_status.assert_called_once()
    call_args = mock_job_service.update_job_status.call_args
    assert call_args[0][0] == job_id
    assert call_args[0][1] == STATUS_COMPLETED
    assert call_args[1]["final_markdown_key"] == f"{job_id}.md"
    assert call_args[1]["correction_decision"] == "approved"
    assert call_args[1]["correction_reviewed_by"] == "instructor@uic.edu"
    assert call_args[1]["correction_justification"] == "Corrections improve structure"
    assert "correction_reviewed_at" in call_args[1]


@pytest.mark.asyncio
async def test_process_approval_missing_corrected_key(
    correction_approval_service,
    mock_redis_client,
    mock_job_service
):
    """Test approval fails if corrected_markdown_key is missing."""
    # Arrange
    job_id = "test-job-456"

    # Job missing corrected_markdown_key
    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "original_markdown_key": "results/test-job-456-original.md",
    }

    mock_redis_client.set.return_value = True
    mock_job_service.get_job.return_value = job_data

    decision = {
        "decision": "approved",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Test"
    }

    # Act & Assert
    with pytest.raises(ValueError, match="missing corrected_markdown_key"):
        await correction_approval_service.process_correction_approval_decision(job_id, decision)


# Phase 7 Tests - Rejection Path

@pytest.mark.asyncio
async def test_process_rejection_downloads_original_key(
    correction_approval_service,
    mock_redis_client,
    mock_job_service,
    mock_storage_service
):
    """Test rejection downloads original markdown using top-level key."""
    # Arrange
    job_id = "test-job-789"
    original_key = "results/test-job-789-original.md"
    original_content = b"# Original Markdown\n\nThis is the original version."

    # Mock job data with top-level fields
    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": "results/test-job-789-awaiting-approval.md",
        "original_markdown_key": original_key,
        "correction_approval_token": "test-token",
    }

    # Mock Redis lock
    mock_redis_client.set.return_value = True
    mock_redis_client.zrem.return_value = 1
    mock_redis_client.delete.return_value = 1

    # Mock job service
    mock_job_service.get_job.return_value = job_data

    # Mock S3 download (original markdown)
    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, key: BytesIO(original_content) if key == 'Body' else None
    mock_storage_service.s3_client.get_object.return_value = mock_response

    # Mock upload_result to return final key
    mock_storage_service.upload_result.return_value = f"{job_id}.md"

    decision = {
        "decision": "rejected",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Keep original structure"
    }

    # Act
    await correction_approval_service.process_correction_approval_decision(job_id, decision)

    # Assert - Downloaded from original key
    mock_storage_service.s3_client.get_object.assert_called_once_with(
        Bucket="test-results-bucket",
        Key=original_key
    )

    # Assert - Uploaded to final location
    mock_storage_service.upload_result.assert_called_once_with(
        job_id=job_id,
        content=original_content,
        format="md"
    )

    # Assert - Decision fields stored at top level
    mock_job_service.update_job_status.assert_called_once()
    call_args = mock_job_service.update_job_status.call_args
    assert call_args[0][0] == job_id
    assert call_args[0][1] == STATUS_COMPLETED
    assert call_args[1]["final_markdown_key"] == f"{job_id}.md"
    assert call_args[1]["correction_decision"] == "rejected"
    assert call_args[1]["correction_reviewed_by"] == "instructor@uic.edu"
    assert call_args[1]["correction_justification"] == "Keep original structure"
    assert "correction_reviewed_at" in call_args[1]


@pytest.mark.asyncio
async def test_process_rejection_missing_original_key(
    correction_approval_service,
    mock_redis_client,
    mock_job_service
):
    """Test rejection fails if original_markdown_key is missing."""
    # Arrange
    job_id = "test-job-999"

    # Job missing original_markdown_key
    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": "results/test-job-999-awaiting-approval.md",
    }

    mock_redis_client.set.return_value = True
    mock_job_service.get_job.return_value = job_data

    decision = {
        "decision": "rejected",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Test"
    }

    # Act & Assert
    with pytest.raises(ValueError, match="missing original_markdown_key"):
        await correction_approval_service.process_correction_approval_decision(job_id, decision)


# Phase 7 Tests - Error Handling

@pytest.mark.asyncio
async def test_process_approval_s3_download_failure(
    correction_approval_service,
    mock_redis_client,
    mock_job_service,
    mock_storage_service
):
    """Test approval handles S3 download failures gracefully."""
    # Arrange
    job_id = "test-job-s3-fail"
    corrected_key = "results/test-job-s3-fail-awaiting-approval.md"

    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": corrected_key,
        "original_markdown_key": "results/test-job-s3-fail-original.md",
        "correction_approval_token": "test-token",
    }

    mock_redis_client.set.return_value = True
    mock_job_service.get_job.return_value = job_data

    # Mock S3 failure
    mock_storage_service.s3_client.get_object.side_effect = Exception("S3 connection timeout")

    decision = {
        "decision": "approved",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Test"
    }

    # Act & Assert
    with pytest.raises(Exception, match="S3 connection timeout"):
        await correction_approval_service.process_correction_approval_decision(job_id, decision)

    # Lock should be released even on error
    mock_redis_client.delete.assert_called()


@pytest.mark.asyncio
async def test_process_rejection_s3_download_failure(
    correction_approval_service,
    mock_redis_client,
    mock_job_service,
    mock_storage_service
):
    """Test rejection handles S3 download failures gracefully."""
    # Arrange
    job_id = "test-job-s3-fail-2"
    original_key = "results/test-job-s3-fail-2-original.md"

    job_data = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": "results/test-job-s3-fail-2-awaiting-approval.md",
        "original_markdown_key": original_key,
        "correction_approval_token": "test-token",
    }

    mock_redis_client.set.return_value = True
    mock_job_service.get_job.return_value = job_data

    # Mock S3 failure
    mock_storage_service.s3_client.get_object.side_effect = Exception("NoSuchKey")

    decision = {
        "decision": "rejected",
        "reviewed_by": "instructor@uic.edu",
        "justification": "Test"
    }

    # Act & Assert
    with pytest.raises(Exception, match="NoSuchKey"):
        await correction_approval_service.process_correction_approval_decision(job_id, decision)

    # Lock should be released even on error
    mock_redis_client.delete.assert_called()


# Phase 7 Tests - Final Markdown Key Verification

@pytest.mark.asyncio
async def test_both_paths_use_same_final_key(
    correction_approval_service,
    mock_redis_client,
    mock_job_service,
    mock_storage_service
):
    """Test that both approval and rejection result in same final key format."""
    job_id = "test-job-final-key"

    # Test approval path
    job_data_approval = {
        "job_id": job_id,
        "status": "awaiting_correction_approval",
        "corrected_markdown_key": "results/test-job-final-key-awaiting-approval.md",
        "original_markdown_key": "results/test-job-final-key-original.md",
        "correction_approval_token": "test-token",
    }

    mock_redis_client.set.return_value = True
    mock_redis_client.zrem.return_value = 1
    mock_redis_client.delete.return_value = 1
    mock_job_service.get_job.return_value = job_data_approval

    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, key: BytesIO(b"content") if key == 'Body' else None
    mock_storage_service.s3_client.get_object.return_value = mock_response
    mock_storage_service.upload_result.return_value = f"{job_id}.md"

    decision_approval = {
        "decision": "approved",
        "reviewed_by": "test@test.com",
        "justification": "Test"
    }

    # Act - Approval
    await correction_approval_service.process_correction_approval_decision(job_id, decision_approval)

    # Assert - Approval uses final key
    approval_call = mock_job_service.update_job_status.call_args
    assert approval_call[1]["final_markdown_key"] == f"{job_id}.md"

    # Reset mocks
    mock_job_service.reset_mock()
    mock_storage_service.reset_mock()
    mock_redis_client.reset_mock()

    # Test rejection path
    mock_redis_client.set.return_value = True
    mock_redis_client.zrem.return_value = 1
    mock_redis_client.delete.return_value = 1
    mock_job_service.get_job.return_value = job_data_approval
    mock_storage_service.s3_client.get_object.return_value = mock_response
    mock_storage_service.upload_result.return_value = f"{job_id}.md"

    decision_rejection = {
        "decision": "rejected",
        "reviewed_by": "test@test.com",
        "justification": "Test"
    }

    # Act - Rejection
    await correction_approval_service.process_correction_approval_decision(job_id, decision_rejection)

    # Assert - Rejection uses same final key format
    rejection_call = mock_job_service.update_job_status.call_args
    assert rejection_call[1]["final_markdown_key"] == f"{job_id}.md"

    # Both should use identical final key format
    assert approval_call[1]["final_markdown_key"] == rejection_call[1]["final_markdown_key"]
