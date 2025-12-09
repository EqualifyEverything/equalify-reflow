"""Tests for Redis integration and key generation."""

from datetime import UTC, datetime

import pytest

from shared.models import (
    APPROVAL_QUEUE,
    APPROVAL_TIMEOUTS,
    DAILY_METRICS,
    JOB_STATUS_PREFIX,
    PII_QUEUE,
    PROCESSING_QUEUE,
    JobStatus,
    JobSubmission,
    job_status_key,
    metrics_key,
    queue_key,
    timeout_key,
)


@pytest.mark.unit
@pytest.mark.requires_redis

class TestRedisKeyGeneration:
    """Test Redis key generation functions."""

    def test_job_status_key_format(self):
        """Test job status key follows correct format."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"
        key = job_status_key(job_id)

        assert key == f"eq-pdf:job:{job_id}"
        assert key.startswith("eq-pdf:job:")

    def test_queue_key_formats(self):
        """Test queue key generation for all queue types."""
        pii_key = queue_key("pii")
        approval_key = queue_key("approval")
        processing_key = queue_key("processing")

        assert pii_key == "eq-pdf:queue:pii"
        assert approval_key == "eq-pdf:queue:approval"
        assert processing_key == "eq-pdf:queue:processing"

    def test_timeout_key_format(self):
        """Test timeout key generation."""
        key = timeout_key("approval")
        assert key == "eq-pdf:timeouts:approval"

    def test_metrics_key_format(self):
        """Test metrics key generation."""
        key = metrics_key("daily")
        assert key == "eq-pdf:metrics:daily"

    def test_key_prefix_consistency(self):
        """Test all keys use eq-pdf prefix."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"

        keys = [
            job_status_key(job_id),
            queue_key("pii"),
            timeout_key("approval"),
            metrics_key("daily")
        ]

        for key in keys:
            assert key.startswith("eq-pdf:")


class TestRedisKeyConstants:
    """Test predefined Redis key constants."""

    def test_queue_constants(self):
        """Test queue name constants."""
        assert PII_QUEUE == "eq-pdf:queue:pii"
        assert APPROVAL_QUEUE == "eq-pdf:queue:approval"
        assert PROCESSING_QUEUE == "eq-pdf:queue:processing"

    def test_timeout_constants(self):
        """Test timeout key constants."""
        assert APPROVAL_TIMEOUTS == "eq-pdf:timeouts:approval"

    def test_metrics_constants(self):
        """Test metrics key constants."""
        assert DAILY_METRICS == "eq-pdf:metrics:daily"

    def test_job_status_prefix(self):
        """Test job status key prefix."""
        assert JOB_STATUS_PREFIX == "eq-pdf:job:"

        # Verify prefix works with job IDs
        job_id = "550e8400-e29b-41d4-a716-446655440000"
        full_key = f"{JOB_STATUS_PREFIX}{job_id}"
        assert full_key == job_status_key(job_id)


class TestModelRedisCompatibility:
    """Test that models serialize properly for Redis storage."""

    def test_job_submission_redis_serialization(self):
        """Test JobSubmission model serializes for Redis."""
        submission = JobSubmission(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/test.pdf",
            created_at=datetime.now(UTC),
            file_size_bytes=1024000,
            original_filename="test.pdf"
        )

        # Serialize to JSON for Redis storage
        json_data = submission.model_dump_json()
        assert isinstance(json_data, str)

        # Deserialize from Redis
        restored = JobSubmission.model_validate_json(json_data)
        assert restored.job_id == submission.job_id
        assert restored.s3_key == submission.s3_key

    def test_job_status_redis_serialization(self):
        """Test JobStatus model serializes for Redis."""
        now = datetime.now(UTC)
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="processing",
            created_at=now,
            updated_at=now,
            markdown_url="https://s3.amazonaws.com/output.md",
            confidence_score=0.87
        )

        # Serialize to JSON for Redis storage
        json_data = status.model_dump_json()
        assert isinstance(json_data, str)

        # Deserialize from Redis
        restored = JobStatus.model_validate_json(json_data)
        assert restored.status == "processing"
        assert restored.confidence_score == 0.87

    def test_datetime_serialization(self):
        """Test datetime fields serialize correctly."""
        now = datetime(2024, 1, 15, 10, 30, 0)
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            created_at=now,
            updated_at=now
        )

        json_data = status.model_dump_json()
        restored = JobStatus.model_validate_json(json_data)

        # Datetime should round-trip correctly
        assert restored.created_at == now
        assert restored.updated_at == now

    def test_optional_fields_serialization(self):
        """Test optional fields serialize correctly as None."""
        now = datetime.now(UTC)
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="pii_scanning",
            created_at=now,
            updated_at=now
        )

        json_data = status.model_dump_json()
        restored = JobStatus.model_validate_json(json_data)

        # Optional fields should be None
        assert restored.pii_findings is None
        assert restored.approval_token is None
        assert restored.error_message is None

    def test_nested_model_serialization(self):
        """Test nested models serialize correctly."""
        from shared.models import PIIFinding

        now = datetime.now(UTC)
        findings = [
            PIIFinding(
                entity_type="PERSON",
                start=10,
                end=20,
                score=0.95,
                text="John Doe"
            )
        ]

        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="awaiting_approval",
            created_at=now,
            updated_at=now,
            pii_findings=findings
        )

        json_data = status.model_dump_json()
        restored = JobStatus.model_validate_json(json_data)

        assert len(restored.pii_findings) == 1
        assert restored.pii_findings[0].text == "John Doe"


class TestRedisKeyNamespacing:
    """Test Redis key namespacing prevents collisions."""

    def test_different_job_ids_unique_keys(self):
        """Test different job IDs generate unique keys."""
        job_id_1 = "550e8400-e29b-41d4-a716-446655440000"
        job_id_2 = "660e8400-e29b-41d4-a716-446655440001"

        key_1 = job_status_key(job_id_1)
        key_2 = job_status_key(job_id_2)

        assert key_1 != key_2
        assert job_id_1 in key_1
        assert job_id_2 in key_2

    def test_queue_keys_unique(self):
        """Test queue keys are unique."""
        queues = [
            queue_key("pii"),
            queue_key("approval"),
            queue_key("processing")
        ]

        # All keys should be unique
        assert len(queues) == len(set(queues))

    def test_key_patterns_for_scanning(self):
        """Test key patterns work for Redis SCAN operations."""
        # Pattern to find all job status keys

        # Generate some example keys
        job_ids = [
            "550e8400-e29b-41d4-a716-446655440000",
            "660e8400-e29b-41d4-a716-446655440001",
            "770e8400-e29b-41d4-a716-446655440002"
        ]

        keys = [job_status_key(job_id) for job_id in job_ids]

        # All keys should match the pattern
        for key in keys:
            assert key.startswith(JOB_STATUS_PREFIX)
