"""Tests for queue payload models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from shared.models import ApprovalQueuePayload, PIIFinding, PIIQueuePayload, ProcessingQueuePayload


@pytest.mark.unit

class TestPIIQueuePayload:
    """Test PIIQueuePayload model validation."""

    def test_valid_pii_queue_payload(self):
        """Test creation with valid data."""
        payload = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            created_at=datetime.now(UTC)
        )
        assert payload.job_id == "550e8400-e29b-41d4-a716-446655440000"
        assert payload.s3_key.startswith("temp/")

    def test_invalid_job_id(self):
        """Test rejection of invalid UUID format."""
        with pytest.raises(ValidationError) as exc_info:
            PIIQueuePayload(
                job_id="invalid-uuid",
                s3_key="temp/test.pdf",
                created_at=datetime.now(UTC)
            )
        assert "job_id" in str(exc_info.value)

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        created_time = datetime(2024, 1, 15, 10, 30, 0)
        payload = PIIQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            created_at=created_time
        )

        json_data = payload.model_dump_json()
        restored = PIIQueuePayload.model_validate_json(json_data)

        assert restored.job_id == payload.job_id
        assert restored.s3_key == payload.s3_key


class TestApprovalQueuePayload:
    """Test ApprovalQueuePayload model validation."""

    def test_valid_approval_queue_payload(self):
        """Test creation with valid data."""
        findings = [
            PIIFinding(
                entity_type="PERSON",
                start=10,
                end=20,
                score=0.95,
                text="John Doe"
            )
        ]

        expires = datetime.now(UTC) + timedelta(days=1)
        payload = ApprovalQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            pii_findings=findings,
            approval_token="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
            expires_at=expires
        )

        assert len(payload.pii_findings) == 1
        assert payload.approval_token == "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
        assert payload.expires_at == expires

    def test_requires_pii_findings(self):
        """Test that approval payload requires at least one PII finding."""
        with pytest.raises(ValidationError) as exc_info:
            ApprovalQueuePayload(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="temp/test.pdf",
                pii_findings=[],  # Empty list should fail
                approval_token="abc123def456ghi789jkl012mno345pqr",
                expires_at=datetime.now(UTC) + timedelta(days=1)
            )
        assert "pii_findings" in str(exc_info.value)

    def test_approval_token_length_validation(self):
        """Test approval token length constraints."""
        findings = [
            PIIFinding(
                entity_type="EMAIL_ADDRESS",
                start=0,
                end=20,
                score=0.95,
                text="test@example.com"
            )
        ]

        # Token too short
        with pytest.raises(ValidationError):
            ApprovalQueuePayload(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="temp/test.pdf",
                pii_findings=findings,
                approval_token="short",  # Less than 32 chars
                expires_at=datetime.now(UTC) + timedelta(days=1)
            )

        # Token too long
        with pytest.raises(ValidationError):
            ApprovalQueuePayload(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="temp/test.pdf",
                pii_findings=findings,
                approval_token="a" * 65,  # More than 64 chars
                expires_at=datetime.now(UTC) + timedelta(days=1)
            )

    def test_multiple_pii_findings(self):
        """Test payload with multiple PII findings."""
        findings = [
            PIIFinding(
                entity_type="PERSON",
                start=10,
                end=20,
                score=0.95,
                text="John Doe"
            ),
            PIIFinding(
                entity_type="EMAIL_ADDRESS",
                start=50,
                end=70,
                score=0.99,
                text="john@example.com"
            ),
            PIIFinding(
                entity_type="PHONE_NUMBER",
                start=100,
                end=112,
                score=0.88,
                text="555-123-4567"
            )
        ]

        payload = ApprovalQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            pii_findings=findings,
            approval_token="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
            expires_at=datetime.now(UTC) + timedelta(days=1)
        )

        assert len(payload.pii_findings) == 3
        assert payload.pii_findings[0].entity_type == "PERSON"
        assert payload.pii_findings[1].entity_type == "EMAIL_ADDRESS"
        assert payload.pii_findings[2].entity_type == "PHONE_NUMBER"

    def test_json_serialization_with_findings(self):
        """Test JSON serialization with nested PII findings."""
        findings = [
            PIIFinding(
                entity_type="PERSON",
                start=10,
                end=20,
                score=0.95,
                text="John Doe"
            )
        ]

        payload = ApprovalQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/test.pdf",
            pii_findings=findings,
            approval_token="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
            expires_at=datetime.now(UTC) + timedelta(days=1)
        )

        json_data = payload.model_dump_json()
        restored = ApprovalQueuePayload.model_validate_json(json_data)

        assert len(restored.pii_findings) == 1
        assert restored.pii_findings[0].text == "John Doe"


class TestProcessingQueuePayload:
    """Test ProcessingQueuePayload model validation."""

    def test_valid_processing_queue_payload(self):
        """Test creation with valid data."""
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            approved_at=None
        )
        assert payload.job_id == "550e8400-e29b-41d4-a716-446655440000"
        assert payload.approved_at is None

    def test_processing_with_approval_timestamp(self):
        """Test payload for approved document."""
        approved_time = datetime.now(UTC)
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            approved_at=approved_time
        )
        assert payload.approved_at == approved_time

    def test_processing_without_approval(self):
        """Test payload for document that passed PII scan."""
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf"
        )
        assert payload.approved_at is None

    def test_json_serialization(self):
        """Test JSON serialization and deserialization."""
        approved_time = datetime(2024, 1, 15, 11, 0, 0)
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/input.pdf",
            approved_at=approved_time
        )

        json_data = payload.model_dump_json()
        restored = ProcessingQueuePayload.model_validate_json(json_data)

        assert restored.job_id == payload.job_id
        assert restored.approved_at == payload.approved_at


class TestQueuePayloadInterop:
    """Test queue payload models work together in workflow."""

    def test_workflow_no_pii_detected(self):
        """Test workflow when no PII is detected."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"
        s3_key = "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf"

        # Step 1: PII scan
        pii_payload = PIIQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            created_at=datetime.now(UTC)
        )

        # Step 2: No PII found, go directly to processing
        processing_payload = ProcessingQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            approved_at=None
        )

        assert pii_payload.job_id == processing_payload.job_id
        assert processing_payload.approved_at is None

    def test_workflow_with_approval(self):
        """Test workflow when PII requires approval."""
        job_id = "550e8400-e29b-41d4-a716-446655440000"
        s3_key = "temp/550e8400-e29b-41d4-a716-446655440000/input.pdf"

        # Step 1: PII scan
        pii_payload = PIIQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            created_at=datetime.now(UTC)
        )

        # Step 2: PII detected, create approval payload
        findings = [
            PIIFinding(
                entity_type="PERSON",
                start=10,
                end=20,
                score=0.95,
                text="Student Name"
            )
        ]

        approval_payload = ApprovalQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            pii_findings=findings,
            approval_token="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
            expires_at=datetime.now(UTC) + timedelta(days=1)
        )

        # Step 3: After approval, create processing payload
        approval_time = datetime.now(UTC)
        processing_payload = ProcessingQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            approved_at=approval_time
        )

        assert pii_payload.job_id == approval_payload.job_id == processing_payload.job_id
        assert processing_payload.approved_at is not None
