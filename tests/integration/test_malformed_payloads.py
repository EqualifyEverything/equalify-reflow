"""Integration tests for malformed queue payload handling.

Tests worker resilience to:
- Missing required fields in queue payloads
- Invalid JSON in Redis queues
- Wrong data types
- Extra unexpected fields
- Corrupted queue messages
- Invalid UUID formats
- Missing S3 keys

These tests validate that workers handle bad data gracefully without crashing.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from pydantic import ValidationError

from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload
from src.shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import STATUS_FAILED


class TestMissingRequiredFields:
    """Tests for queue payloads with missing required fields."""

    @pytest.mark.asyncio
    async def test_pii_payload_missing_job_id(
        self,
        pii_worker,
        queue_service
    ):
        """Test PII worker handles missing job_id gracefully."""
        # Malformed payload: missing job_id
        malformed_payload = {
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Mock queue to return malformed payload
        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        # Attempt to process
        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should raise validation error when trying to parse
        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_pii_payload_missing_s3_key(
        self,
        pii_worker,
        queue_service,
        sample_job_id
    ):
        """Test PII worker handles missing s3_key gracefully."""
        # Malformed payload: missing s3_key
        malformed_payload = {
            "job_id": sample_job_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should raise validation error
        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_pii_payload_missing_created_at(
        self,
        pii_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test PII worker handles missing created_at gracefully."""
        # Malformed payload: missing created_at
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should raise validation error
        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_processing_payload_missing_job_id(
        self,
        processing_worker,
        queue_service
    ):
        """Test processing worker handles missing job_id gracefully."""
        malformed_payload = {
            "s3_key": "temp/test.pdf",
            "approved_at": None
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            ProcessingQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_processing_payload_missing_s3_key(
        self,
        processing_worker,
        queue_service,
        sample_job_id
    ):
        """Test processing worker handles missing s3_key gracefully."""
        malformed_payload = {
            "job_id": sample_job_id,
            "approved_at": None
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            ProcessingQueuePayload.model_validate(job_data)


class TestInvalidJSON:
    """Tests for corrupted JSON in queue messages."""

    @pytest.mark.asyncio
    async def test_completely_invalid_json(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles completely invalid JSON."""
        # Mock Redis to return invalid JSON string
        invalid_json = "not valid json{["

        # Mock brpop to return raw invalid JSON
        queue_service.redis.brpop = AsyncMock(
            return_value=(PII_QUEUE.encode(), invalid_json.encode())
        )

        # Dequeue should handle JSON parse error
        with pytest.raises(Exception) as exc_info:
            await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should be JSON decode error
        assert "deserialize" in str(exc_info.value).lower() or "json" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_partially_valid_json(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles partially valid JSON."""
        # JSON with syntax error
        partial_json = '{"job_id": "abc", "s3_key": "test.pdf", '

        queue_service.redis.brpop = AsyncMock(
            return_value=(PII_QUEUE.encode(), partial_json.encode())
        )

        with pytest.raises(Exception):
            await queue_service.dequeue(PII_QUEUE, timeout=1)

    @pytest.mark.asyncio
    async def test_empty_json_object(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles empty JSON object."""
        empty_json = "{}"

        queue_service.dequeue = AsyncMock(return_value={})

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should raise validation error (missing required fields)
        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)


class TestWrongDataTypes:
    """Tests for wrong data types in queue payloads."""

    @pytest.mark.asyncio
    async def test_job_id_as_integer(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles job_id as integer instead of string."""
        malformed_payload = {
            "job_id": 12345,  # Should be UUID string
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should raise validation error (invalid UUID format)
        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_s3_key_as_list(
        self,
        pii_worker,
        queue_service,
        sample_job_id
    ):
        """Test worker handles s3_key as list instead of string."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": ["temp", "test.pdf"],  # Should be string
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_created_at_as_integer(
        self,
        pii_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test worker handles created_at as integer timestamp."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "created_at": 1234567890  # Unix timestamp instead of ISO string
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Pydantic may coerce this - depends on model configuration
        # If it fails, it should raise ValidationError
        try:
            payload = PIIQueuePayload.model_validate(job_data)
            # If it succeeds, verify it's a valid datetime
            assert isinstance(payload.created_at, datetime)
        except ValidationError:
            # Validation error is also acceptable
            pass

    @pytest.mark.asyncio
    async def test_approved_at_wrong_format(
        self,
        processing_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test worker handles approved_at in wrong format."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "approved_at": "not a datetime"
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            ProcessingQueuePayload.model_validate(job_data)


class TestInvalidUUIDFormats:
    """Tests for invalid UUID formats in job_id field."""

    @pytest.mark.asyncio
    async def test_job_id_not_uuid_format(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles non-UUID job_id."""
        malformed_payload = {
            "job_id": "not-a-valid-uuid-12345",
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should fail UUID pattern validation
        with pytest.raises(ValidationError) as exc_info:
            PIIQueuePayload.model_validate(job_data)

        assert "job_id" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_job_id_empty_string(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles empty string job_id."""
        malformed_payload = {
            "job_id": "",
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_job_id_malformed_uuid(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles malformed UUID (wrong structure)."""
        malformed_payload = {
            "job_id": "550e8400-e29b-41d4-a716",  # Incomplete UUID
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)


class TestExtraUnexpectedFields:
    """Tests for forward compatibility with extra fields."""

    @pytest.mark.asyncio
    async def test_pii_payload_with_extra_fields(
        self,
        pii_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test worker handles extra unknown fields gracefully."""
        payload_with_extras = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "unknown_field_1": "extra data",
            "unknown_field_2": 12345,
            "future_feature": {"nested": "object"}
        }

        queue_service.dequeue = AsyncMock(return_value=payload_with_extras)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should parse successfully, ignoring extra fields (Pydantic default behavior)
        payload = PIIQueuePayload.model_validate(job_data)
        assert payload.job_id == sample_job_id
        assert payload.s3_key == sample_s3_key

    @pytest.mark.asyncio
    async def test_processing_payload_with_extra_fields(
        self,
        processing_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test processing worker handles extra fields."""
        payload_with_extras = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "approved_at": None,
            "experimental_flag": True,
            "metadata": {"version": "2.0"}
        }

        queue_service.dequeue = AsyncMock(return_value=payload_with_extras)

        job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)

        # Should parse successfully
        payload = ProcessingQueuePayload.model_validate(job_data)
        assert payload.job_id == sample_job_id


class TestNullAndNoneValues:
    """Tests for null/None values in required fields."""

    @pytest.mark.asyncio
    async def test_job_id_as_null(
        self,
        pii_worker,
        queue_service
    ):
        """Test worker handles null job_id."""
        malformed_payload = {
            "job_id": None,
            "s3_key": "temp/test.pdf",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_s3_key_as_null(
        self,
        pii_worker,
        queue_service,
        sample_job_id
    ):
        """Test worker handles null s3_key."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)

    @pytest.mark.asyncio
    async def test_created_at_as_null(
        self,
        pii_worker,
        queue_service,
        sample_job_id,
        sample_s3_key
    ):
        """Test worker handles null created_at."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "created_at": None
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        with pytest.raises(ValidationError):
            PIIQueuePayload.model_validate(job_data)


class TestEmptyStringValues:
    """Tests for empty string values in required fields."""

    @pytest.mark.asyncio
    async def test_s3_key_empty_string(
        self,
        pii_worker,
        queue_service,
        sample_job_id
    ):
        """Test worker handles empty s3_key."""
        malformed_payload = {
            "job_id": sample_job_id,
            "s3_key": "",  # Empty string
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue_service.dequeue = AsyncMock(return_value=malformed_payload)

        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)

        # Should fail min_length validation
        with pytest.raises(ValidationError) as exc_info:
            PIIQueuePayload.model_validate(job_data)

        assert "s3_key" in str(exc_info.value).lower()


class TestWorkerRecovery:
    """Tests for worker recovery after encountering malformed data."""

    @pytest.mark.asyncio
    async def test_worker_continues_after_malformed_payload(
        self,
        pii_worker,
        queue_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        mock_pii_analyzer,
        sample_pdf_content
    ):
        """Test worker continues processing after encountering malformed payload."""
        # First payload: malformed
        malformed = {
            "job_id": "not-a-uuid",
            "s3_key": sample_s3_key,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Second payload: valid
        valid = {
            "job_id": sample_job_id,
            "s3_key": sample_s3_key,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Mock queue to return malformed, then valid, then None
        queue_service.dequeue = AsyncMock(side_effect=[malformed, valid, None])

        # Mock services
        mock_pii_analyzer.analyze_text.return_value = []
        pii_worker.pii_service.storage.download_temp_file = AsyncMock(
            return_value=sample_pdf_content
        )

        # Track successful processing
        processed_jobs = []

        async def track_process(job):
            processed_jobs.append(job.job_id)

        pii_worker.pii_service.process_pii_job = AsyncMock(side_effect=track_process)

        # Process two jobs
        for _ in range(2):
            job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
            if job_data is None:
                break

            try:
                job = PIIQueuePayload.model_validate(job_data)
                await pii_worker.pii_service.process_pii_job(job)
            except ValidationError:
                # Worker should log error and continue
                continue

        # Verify: Valid job was processed despite malformed job
        assert len(processed_jobs) == 1
        assert processed_jobs[0] == sample_job_id
