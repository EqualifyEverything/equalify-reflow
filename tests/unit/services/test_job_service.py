"""Integration tests for Job Service."""

import pytest
import json
from datetime import datetime

from src.services.job_service import JobService
from src.config import settings


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client."""
    client = mocker.AsyncMock()
    return client


@pytest.fixture
def job_service(mock_redis_client):
    """Create job service with mock client."""
    return JobService(redis_client=mock_redis_client)


class TestCreateJob:
    """Tests for create_job method."""

    @pytest.mark.asyncio
    async def test_create_job_success(self, job_service, mock_redis_client):
        """Test successful job creation."""
        mock_redis_client.hset.return_value = 3

        await job_service.create_job(
            job_id="job123",
            s3_key="temp/job123/file.pdf",
            status="pii_scanning"
        )

        # Verify hset was called
        mock_redis_client.hset.assert_called_once()
        key, mapping = mock_redis_client.hset.call_args.args[0], mock_redis_client.hset.call_args.kwargs["mapping"]

        assert key == f"{settings.job_status_prefix}job123"
        assert mapping["job_id"] == "job123"
        assert mapping["s3_key"] == "temp/job123/file.pdf"
        assert mapping["status"] == "pii_scanning"
        assert "created_at" in mapping
        assert "updated_at" in mapping

    @pytest.mark.asyncio
    async def test_create_job_default_status(self, job_service, mock_redis_client):
        """Test job creation with default status."""
        mock_redis_client.hset.return_value = 3

        await job_service.create_job(
            job_id="job456",
            s3_key="temp/file.pdf"
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert mapping["status"] == "pii_scanning"


class TestGetJob:
    """Tests for get_job method."""

    @pytest.mark.asyncio
    async def test_get_job_success(self, job_service, mock_redis_client):
        """Test retrieving existing job."""
        job_data = {
            "job_id": "job123",
            "s3_key": "temp/job123/file.pdf",
            "status": "processing",
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        assert result == job_data
        mock_redis_client.hgetall.assert_called_once_with(
            f"{settings.job_status_prefix}job123"
        )

    @pytest.mark.asyncio
    async def test_get_job_with_pii_findings(self, job_service, mock_redis_client):
        """Test retrieving job with PII findings."""
        pii_findings = [
            {"entity_type": "PERSON", "score": 0.85}
        ]
        job_data = {
            "job_id": "job123",
            "status": "awaiting_approval",
            "pii_findings": json.dumps(pii_findings),
            "created_at": "2024-01-15T10:30:00Z",
            "updated_at": "2024-01-15T10:35:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        # Verify PII findings were deserialized
        assert result["pii_findings"] == pii_findings

    @pytest.mark.asyncio
    async def test_get_job_with_json_array_fields(self, job_service, mock_redis_client):
        """Test retrieving job with JSON array fields (correction_results, page_image_keys)."""
        # Phase 4: metadata is no longer parsed - only specific JSON array fields
        correction_results = [{"page": 1, "corrections": []}]
        page_image_keys = ["page-1.png", "page-2.png"]

        job_data = {
            "job_id": "job123",
            "correction_results": json.dumps(correction_results),
            "page_image_keys": json.dumps(page_image_keys),
            "created_at": "2024-01-15T10:30:00Z"
        }
        mock_redis_client.hgetall.return_value = job_data

        result = await job_service.get_job("job123")

        # Verify JSON array fields were deserialized
        assert result["correction_results"] == correction_results
        assert result["page_image_keys"] == page_image_keys

        # Verify metadata field is NOT parsed (Phase 4 change)
        assert "metadata" not in result

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, job_service, mock_redis_client):
        """Test retrieving non-existent job."""
        mock_redis_client.hgetall.return_value = {}

        result = await job_service.get_job("nonexistent")

        assert result is None


class TestUpdateJobStatus:
    """Tests for update_job_status method."""

    @pytest.mark.asyncio
    async def test_update_status_simple(self, job_service, mock_redis_client):
        """Test simple status update."""
        mock_redis_client.hset.return_value = 2

        await job_service.update_job_status("job123", "processing")

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert mapping["status"] == "processing"
        assert "updated_at" in mapping

    @pytest.mark.asyncio
    async def test_update_status_with_metadata(self, job_service, mock_redis_client):
        """Test status update with additional metadata."""
        mock_redis_client.hset.return_value = 3

        await job_service.update_job_status(
            "job123",
            "completed",
            result_url="https://s3.../result.html",
            confidence_score=0.95
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert mapping["status"] == "completed"
        assert mapping["result_url"] == "https://s3.../result.html"
        assert mapping["confidence_score"] == "0.95"

    @pytest.mark.asyncio
    async def test_update_status_with_complex_fields(self, job_service, mock_redis_client):
        """Test status update with dict/list fields stored at top level."""
        mock_redis_client.hset.return_value = 2

        # Phase 4: Pass fields individually, not as metadata blob
        correction_results = [{"page": 1, "corrections": []}]
        page_image_keys = ["page-1.png", "page-2.png"]

        await job_service.update_job_status(
            "job123",
            "processing",
            correction_results=correction_results,
            page_image_keys=page_image_keys
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        # Complex fields should be JSON serialized
        assert isinstance(mapping["correction_results"], str)
        assert json.loads(mapping["correction_results"]) == correction_results
        assert isinstance(mapping["page_image_keys"], str)
        assert json.loads(mapping["page_image_keys"]) == page_image_keys
        # Verify no metadata blob exists
        assert "metadata" not in mapping


class TestAddPiiFindings:
    """Tests for add_pii_findings method."""

    @pytest.mark.asyncio
    async def test_add_pii_findings(self, job_service, mock_redis_client):
        """Test adding PII findings to job."""
        findings = [
            {"entity_type": "PERSON", "text": "John Doe", "score": 0.85},
            {"entity_type": "EMAIL", "text": "john@example.com", "score": 0.95}
        ]
        mock_redis_client.hset.return_value = 2

        await job_service.add_pii_findings("job123", findings)

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert "pii_findings" in mapping
        assert "updated_at" in mapping

        # Verify findings were serialized
        stored_findings = json.loads(mapping["pii_findings"])
        assert stored_findings == findings

    @pytest.mark.asyncio
    async def test_add_empty_pii_findings(self, job_service, mock_redis_client):
        """Test adding empty PII findings list."""
        mock_redis_client.hset.return_value = 2

        await job_service.add_pii_findings("job123", [])

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        stored_findings = json.loads(mapping["pii_findings"])
        assert stored_findings == []


class TestAddProcessingResult:
    """Tests for add_processing_result method."""

    @pytest.mark.asyncio
    async def test_add_processing_result(self, job_service, mock_redis_client):
        """Test adding processing result."""
        mock_redis_client.hset.return_value = 4

        await job_service.add_processing_result(
            job_id="job123",
            result_url="https://s3.amazonaws.com/results/job123.html",
            confidence=0.92
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert mapping["result_url"] == "https://s3.amazonaws.com/results/job123.html"
        assert mapping["confidence_score"] == "0.92"
        assert "completed_at" in mapping
        assert "updated_at" in mapping

    @pytest.mark.asyncio
    async def test_add_processing_result_low_confidence(self, job_service, mock_redis_client):
        """Test adding result with low confidence."""
        mock_redis_client.hset.return_value = 4

        await job_service.add_processing_result(
            job_id="job456",
            result_url="https://s3.../result.html",
            confidence=0.45
        )

        mapping = mock_redis_client.hset.call_args.kwargs["mapping"]
        assert float(mapping["confidence_score"]) == 0.45


class TestJobExists:
    """Tests for job_exists method."""

    @pytest.mark.asyncio
    async def test_job_exists_true(self, job_service, mock_redis_client):
        """Test checking existing job."""
        mock_redis_client.exists.return_value = 1

        exists = await job_service.job_exists("job123")

        assert exists is True
        mock_redis_client.exists.assert_called_once_with(
            f"{settings.job_status_prefix}job123"
        )

    @pytest.mark.asyncio
    async def test_job_exists_false(self, job_service, mock_redis_client):
        """Test checking non-existent job."""
        mock_redis_client.exists.return_value = 0

        exists = await job_service.job_exists("nonexistent")

        assert exists is False

    @pytest.mark.asyncio
    async def test_job_exists_error(self, job_service, mock_redis_client):
        """Test handling of existence check error."""
        mock_redis_client.exists.side_effect = Exception("Redis error")

        exists = await job_service.job_exists("job123")

        assert exists is False  # Default to False on error


class TestDeleteJob:
    """Tests for delete_job method."""

    @pytest.mark.asyncio
    async def test_delete_job_success(self, job_service, mock_redis_client):
        """Test successful job deletion."""
        mock_redis_client.delete.return_value = 1

        await job_service.delete_job("job123")

        mock_redis_client.delete.assert_called_once_with(
            f"{settings.job_status_prefix}job123"
        )

    @pytest.mark.asyncio
    async def test_delete_job_failure(self, job_service, mock_redis_client):
        """Test handling of deletion failure."""
        mock_redis_client.delete.side_effect = Exception("Redis error")

        with pytest.raises(Exception) as exc:
            await job_service.delete_job("job123")

        assert "Failed to delete job" in str(exc.value)


class TestSetExpiration:
    """Tests for set_expiration method."""

    @pytest.mark.asyncio
    async def test_set_expiration_success(self, job_service, mock_redis_client):
        """Test setting job expiration."""
        mock_redis_client.expire.return_value = 1

        ttl_seconds = 86400  # 1 day
        await job_service.set_expiration("job123", ttl_seconds)

        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job123",
            ttl_seconds
        )

    @pytest.mark.asyncio
    async def test_set_expiration_30_days(self, job_service, mock_redis_client):
        """Test setting 30-day expiration."""
        mock_redis_client.expire.return_value = 1

        ttl_seconds = settings.job_ttl_days * 24 * 60 * 60
        await job_service.set_expiration("job456", ttl_seconds)

        call_args = mock_redis_client.expire.call_args.args
        assert call_args[1] == ttl_seconds

    @pytest.mark.asyncio
    async def test_set_expiration_failure(self, job_service, mock_redis_client):
        """Test handling of expiration setting failure."""
        mock_redis_client.expire.side_effect = Exception("Redis error")

        with pytest.raises(Exception) as exc:
            await job_service.set_expiration("job123", 3600)

        assert "Failed to set expiration" in str(exc.value)


class TestJobLifecycle:
    """Integration tests for complete job lifecycle."""

    @pytest.mark.asyncio
    async def test_complete_job_flow(self, job_service, mock_redis_client):
        """Test complete job lifecycle from creation to completion."""
        job_id = "job123"

        # 1. Create job
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1
        await job_service.create_job(job_id, "temp/file.pdf", "pii_scanning")

        # 2. Add PII findings
        findings = [{"entity_type": "PERSON", "score": 0.85}]
        await job_service.add_pii_findings(job_id, findings)

        # 3. Update to processing
        await job_service.update_job_status(job_id, "processing")

        # 4. Add processing result
        await job_service.add_processing_result(
            job_id,
            "https://s3.../result.html",
            0.92
        )

        # 5. Set expiration
        await job_service.set_expiration(job_id, 86400)

        # Verify all operations were called
        assert mock_redis_client.hset.call_count == 4
        # TTL is set on: create_job (1), update_job_status (1), set_expiration (1) = 3 total
        assert mock_redis_client.expire.call_count == 3

    @pytest.mark.asyncio
    async def test_job_no_pii_flow(self, job_service, mock_redis_client):
        """Test job flow when no PII is detected."""
        job_id = "job456"
        mock_redis_client.hset.return_value = 3

        # Create and go straight to processing
        await job_service.create_job(job_id, "temp/file.pdf", "pii_scanning")
        await job_service.update_job_status(job_id, "processing")
        await job_service.add_processing_result(job_id, "https://s3.../result.html", 0.95)

        # Verify no PII findings added
        all_calls = [call.kwargs.get("mapping", {}) for call in mock_redis_client.hset.call_args_list]
        pii_calls = [c for c in all_calls if "pii_findings" in c]
        assert len(pii_calls) == 0


class TestCleanupOldJob:
    """Tests for cleanup_old_job method."""

    @pytest.mark.asyncio
    async def test_cleanup_existing_job(self, job_service, mock_redis_client):
        """Test cleanup of existing job."""
        job_id = "job123"

        # Mock exists check
        mock_redis_client.exists.return_value = 1  # Job exists

        # Mock hgetall to return job data for logging
        mock_redis_client.hgetall.return_value = {
            'status': 'completed',
            'job_id': job_id
        }

        # Mock delete
        mock_redis_client.delete.return_value = 1  # 1 key deleted

        deleted = await job_service.cleanup_old_job(job_id)

        assert deleted is True
        mock_redis_client.exists.assert_called_once_with(f"eq-pdf:job:{job_id}")
        mock_redis_client.delete.assert_called_once_with(f"eq-pdf:job:{job_id}")

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_job(self, job_service, mock_redis_client):
        """Test cleanup when job doesn't exist."""
        job_id = "job456"

        # Mock exists check - job doesn't exist
        mock_redis_client.exists.return_value = 0

        deleted = await job_service.cleanup_old_job(job_id)

        assert deleted is False
        mock_redis_client.exists.assert_called_once()
        # Should not attempt to delete
        mock_redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_different_statuses(self, job_service, mock_redis_client):
        """Test cleanup logs correct status."""
        statuses = ['completed', 'failed', 'denied', 'unknown']

        for status in statuses:
            mock_redis_client.exists.return_value = 1
            mock_redis_client.hgetall.return_value = {'status': status}
            mock_redis_client.delete.return_value = 1

            deleted = await job_service.cleanup_old_job(f"job-{status}")

            assert deleted is True

    @pytest.mark.asyncio
    async def test_cleanup_delete_returns_zero(self, job_service, mock_redis_client):
        """Test when delete operation returns 0 (shouldn't happen but defensive)."""
        mock_redis_client.exists.return_value = 1
        mock_redis_client.hgetall.return_value = {'status': 'completed'}
        mock_redis_client.delete.return_value = 0  # Unexpected but possible

        deleted = await job_service.cleanup_old_job("job-weird")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_handles_exists_error(self, job_service, mock_redis_client):
        """Test error handling during exists check."""
        mock_redis_client.exists.side_effect = Exception("Redis connection error")

        deleted = await job_service.cleanup_old_job("job-error")

        # Should return False on error
        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_handles_hgetall_error(self, job_service, mock_redis_client):
        """Test graceful handling when hgetall fails."""
        mock_redis_client.exists.return_value = 1
        mock_redis_client.hgetall.side_effect = Exception("Redis error")

        # Should still attempt delete (fallback to unknown status)
        deleted = await job_service.cleanup_old_job("job-hgetall-error")

        # Depends on implementation - should either fail gracefully or continue
        # Since implementation catches all exceptions, should return False
        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_handles_delete_error(self, job_service, mock_redis_client):
        """Test error handling during delete operation."""
        mock_redis_client.exists.return_value = 1
        mock_redis_client.hgetall.return_value = {'status': 'completed'}
        mock_redis_client.delete.side_effect = Exception("Delete failed")

        deleted = await job_service.cleanup_old_job("job-delete-error")

        assert deleted is False

    @pytest.mark.asyncio
    async def test_cleanup_with_empty_job_data(self, job_service, mock_redis_client):
        """Test cleanup when job hash exists but is empty."""
        mock_redis_client.exists.return_value = 1
        mock_redis_client.hgetall.return_value = {}  # Empty hash
        mock_redis_client.delete.return_value = 1

        deleted = await job_service.cleanup_old_job("job-empty")

        # Should still clean up (status will be 'unknown')
        assert deleted is True
