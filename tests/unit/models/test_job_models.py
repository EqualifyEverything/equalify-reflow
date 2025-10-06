"""Tests for job-related data models."""

import pytest
from datetime import datetime, timedelta
from pydantic import ValidationError

from shared.models import (
    JobSubmission,
    JobStatus,
    PIIFinding,
    ApprovalRequest,
    VALID_TRANSITIONS
)


@pytest.mark.unit

class TestJobSubmission:
    """Test JobSubmission model validation and serialization."""

    def test_valid_job_submission(self):
        """Test creation with valid data."""
        submission = JobSubmission(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/test.pdf",
            created_at=datetime.utcnow(),
            file_size_bytes=1024000,
            original_filename="test_document.pdf"
        )
        assert submission.job_id == "550e8400-e29b-41d4-a716-446655440000"
        assert submission.s3_key.startswith("temp/")
        assert submission.file_size_bytes == 1024000

    def test_invalid_job_id_format(self):
        """Test rejection of non-UUID job IDs."""
        with pytest.raises(ValidationError) as exc_info:
            JobSubmission(
                job_id="not-a-uuid",
                s3_key="temp/test.pdf",
                created_at=datetime.utcnow(),
                file_size_bytes=1024,
                original_filename="test.pdf"
            )
        assert "job_id" in str(exc_info.value)

    def test_invalid_s3_key_prefix(self):
        """Test rejection of S3 keys without temp/ prefix."""
        with pytest.raises(ValidationError) as exc_info:
            JobSubmission(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="permanent/test.pdf",
                created_at=datetime.utcnow(),
                file_size_bytes=1024,
                original_filename="test.pdf"
            )
        assert "temp/" in str(exc_info.value)

    def test_file_size_validation(self):
        """Test file size limits."""
        # Too small
        with pytest.raises(ValidationError):
            JobSubmission(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="temp/test.pdf",
                created_at=datetime.utcnow(),
                file_size_bytes=0,
                original_filename="test.pdf"
            )

        # Too large (>100MB)
        with pytest.raises(ValidationError):
            JobSubmission(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                s3_key="temp/test.pdf",
                created_at=datetime.utcnow(),
                file_size_bytes=101_000_000,
                original_filename="test.pdf"
            )

    def test_json_serialization(self):
        """Test model serialization to JSON."""
        created_time = datetime(2024, 1, 15, 10, 30, 0)
        submission = JobSubmission(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/550e8400-e29b-41d4-a716-446655440000/test.pdf",
            created_at=created_time,
            file_size_bytes=1024000,
            original_filename="test_document.pdf"
        )
        json_data = submission.model_dump_json()
        assert "550e8400-e29b-41d4-a716-446655440000" in json_data
        assert "temp/" in json_data

        # Test round-trip
        restored = JobSubmission.model_validate_json(json_data)
        assert restored.job_id == submission.job_id
        assert restored.file_size_bytes == submission.file_size_bytes


class TestJobStatus:
    """Test JobStatus model validation and state machine."""

    def test_valid_job_status(self):
        """Test creation with valid data."""
        now = datetime.utcnow()
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="pii_scanning",
            created_at=now,
            updated_at=now
        )
        assert status.status == "pii_scanning"
        assert status.pii_findings is None
        assert status.error_message is None

    def test_status_with_pii_findings(self):
        """Test status with PII findings populated."""
        now = datetime.utcnow()
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
            pii_findings=findings,
            approval_token="abc123def456",
            expires_at=now + timedelta(days=1)
        )
        assert len(status.pii_findings) == 1
        assert status.approval_token == "abc123def456"

    def test_completed_status_with_results(self):
        """Test completed status with processing results."""
        now = datetime.utcnow()
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            created_at=now,
            updated_at=now,
            html_url="https://s3.amazonaws.com/output.html",
            mdx_url="https://s3.amazonaws.com/output.mdx",
            confidence_score=0.87
        )
        assert status.status == "completed"
        assert status.html_url is not None
        assert status.confidence_score == 0.87

    def test_failed_status_with_error(self):
        """Test failed status with error message."""
        now = datetime.utcnow()
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="failed",
            created_at=now,
            updated_at=now,
            error_message="PDF parsing failed: corrupted file"
        )
        assert status.status == "failed"
        assert status.error_message is not None

    def test_state_machine_valid_transitions(self):
        """Test valid state transitions."""
        now = datetime.utcnow()
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="pii_scanning",
            created_at=now,
            updated_at=now
        )

        # Valid transitions from pii_scanning
        assert status.can_transition_to("awaiting_approval") is True
        assert status.can_transition_to("processing") is True
        assert status.can_transition_to("failed") is True

        # Invalid transitions
        assert status.can_transition_to("completed") is False
        assert status.can_transition_to("denied") is False

    def test_state_machine_terminal_states(self):
        """Test terminal states have no valid transitions."""
        now = datetime.utcnow()

        # Test completed terminal state
        completed_status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            created_at=now,
            updated_at=now
        )
        assert completed_status.can_transition_to("processing") is False
        assert completed_status.can_transition_to("failed") is False

        # Test failed terminal state
        failed_status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="failed",
            created_at=now,
            updated_at=now
        )
        assert failed_status.can_transition_to("processing") is False

    def test_confidence_score_bounds(self):
        """Test confidence score validation."""
        now = datetime.utcnow()

        # Valid score
        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="completed",
            created_at=now,
            updated_at=now,
            confidence_score=0.85
        )
        assert status.confidence_score == 0.85

        # Score too high
        with pytest.raises(ValidationError):
            JobStatus(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                status="completed",
                created_at=now,
                updated_at=now,
                confidence_score=1.5
            )

        # Score too low
        with pytest.raises(ValidationError):
            JobStatus(
                job_id="550e8400-e29b-41d4-a716-446655440000",
                status="completed",
                created_at=now,
                updated_at=now,
                confidence_score=-0.1
            )

    def test_json_serialization_with_nested_models(self):
        """Test serialization with nested PII findings and approval."""
        now = datetime.utcnow()

        findings = [
            PIIFinding(
                entity_type="EMAIL_ADDRESS",
                start=50,
                end=70,
                score=0.99,
                text="student@example.com"
            )
        ]

        approval = ApprovalRequest(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            decision="approved",
            justification="Instructor email in syllabus",
            reviewed_by="faculty@uic.edu",
            reviewed_at=now
        )

        status = JobStatus(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            status="processing",
            created_at=now,
            updated_at=now,
            pii_findings=findings,
            approval_decision=approval
        )

        json_data = status.model_dump_json()

        # Test round-trip
        restored = JobStatus.model_validate_json(json_data)
        assert len(restored.pii_findings) == 1
        assert restored.pii_findings[0].entity_type == "EMAIL_ADDRESS"
        assert restored.approval_decision.decision == "approved"


class TestValidTransitions:
    """Test the VALID_TRANSITIONS state machine dictionary."""

    def test_all_statuses_defined(self):
        """Test all statuses have transition rules."""
        expected_statuses = {
            "pii_scanning",
            "awaiting_approval",
            "processing",
            "completed",
            "failed",
            "denied"
        }
        assert set(VALID_TRANSITIONS.keys()) == expected_statuses

    def test_terminal_states_have_no_transitions(self):
        """Test terminal states have empty transition lists."""
        terminal_states = ["completed", "failed", "denied"]
        for state in terminal_states:
            assert VALID_TRANSITIONS[state] == []

    def test_pii_scanning_transitions(self):
        """Test pii_scanning can transition to approval or processing."""
        valid_next = VALID_TRANSITIONS["pii_scanning"]
        assert "awaiting_approval" in valid_next
        assert "processing" in valid_next
        assert "failed" in valid_next
        assert "completed" not in valid_next

    def test_awaiting_approval_transitions(self):
        """Test awaiting_approval state transitions."""
        valid_next = VALID_TRANSITIONS["awaiting_approval"]
        assert "processing" in valid_next
        assert "denied" in valid_next
        assert "failed" in valid_next

    def test_processing_transitions(self):
        """Test processing state transitions."""
        valid_next = VALID_TRANSITIONS["processing"]
        assert "completed" in valid_next
        assert "failed" in valid_next
        assert len(valid_next) == 2