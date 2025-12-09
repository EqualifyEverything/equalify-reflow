"""
Test data factories to eliminate hardcoded values.

Provides generators for:
- UUIDs (job_id, document_id)
- Job models (with realistic defaults)
- Document metadata
- Queue payloads
- S3 keys

All functions return unique values per call unless specified.
"""

import uuid
from datetime import UTC, datetime
from io import BytesIO

from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


def generate_job_id() -> str:
    """Generate unique UUID for test jobs.

    Returns:
        str: UUID v4 string
    """
    return str(uuid.uuid4())


def generate_document_id() -> str:
    """Generate unique UUID for test documents.

    Returns:
        str: UUID v4 string
    """
    return str(uuid.uuid4())


def create_test_job_dict(
    job_id: str | None = None,
    status: str = "pending",
    confidence_score: float | None = None,
    created_at: datetime | None = None,
    **kwargs
) -> dict:
    """Create test job dictionary with realistic defaults.

    Args:
        job_id: Job UUID (generated if not provided)
        status: Job status string (default: "pending")
        confidence_score: Confidence score 0-100 (optional)
        created_at: Creation timestamp (defaults to now)
        **kwargs: Additional job fields to override

    Returns:
        dict: Test job data
    """
    job_id = job_id or generate_job_id()
    created_at = created_at or datetime.now(UTC)

    defaults = {
        "job_id": job_id,
        "status": status,
        "s3_temp_key": f"temp/{job_id}/document.pdf",
        "created_at": created_at.isoformat(),
        "updated_at": created_at.isoformat(),
    }

    if confidence_score is not None:
        defaults["confidence_score"] = confidence_score

    # Merge with any additional kwargs
    defaults.update(kwargs)

    return defaults


def create_pii_queue_payload(
    job_id: str | None = None,
    s3_key: str | None = None,
    created_at: datetime | None = None,
) -> PIIQueuePayload:
    """Create test PII queue payload.

    Args:
        job_id: Job UUID (generated if not provided)
        s3_key: S3 key for document (generated if not provided)
        created_at: Creation timestamp (defaults to now)

    Returns:
        PIIQueuePayload: Test payload
    """
    job_id = job_id or generate_job_id()
    s3_key = s3_key or f"temp/{job_id}/document.pdf"
    created_at = created_at or datetime.now(UTC)

    return PIIQueuePayload(
        job_id=job_id,
        s3_key=s3_key,
        created_at=created_at,
    )


def create_processing_queue_payload(
    job_id: str | None = None,
    s3_key: str | None = None,
    pii_approved: bool = True,
    created_at: datetime | None = None,
) -> ProcessingQueuePayload:
    """Create test processing queue payload.

    Args:
        job_id: Job UUID (generated if not provided)
        s3_key: S3 key for document (generated if not provided)
        pii_approved: Whether PII scan approved
        created_at: Creation timestamp (defaults to now)

    Returns:
        ProcessingQueuePayload: Test payload
    """
    job_id = job_id or generate_job_id()
    s3_key = s3_key or f"temp/{job_id}/document.pdf"
    created_at = created_at or datetime.now(UTC)

    return ProcessingQueuePayload(
        job_id=job_id,
        s3_key=s3_key,
        pii_approved=pii_approved,
        created_at=created_at,
    )


def create_s3_key(job_id: str, stage: str = "temp", filename: str = "document.pdf") -> str:
    """Generate S3 key following project conventions.

    Args:
        job_id: Job UUID
        stage: Stage prefix (temp/results)
        filename: Document filename

    Returns:
        str: S3 key (e.g., "temp/uuid/document.pdf")
    """
    return f"{stage}/{job_id}/{filename}"


def create_test_pdf_content() -> bytes:
    """Generate minimal valid PDF content for testing.

    Returns:
        bytes: Minimal PDF file content (> 100 bytes)
    """
    pdf_content = b"%PDF-1.4\n" + b"%Test PDF content line\n" * 10 + b"%%EOF"
    return pdf_content


def create_test_upload_file(mocker, filename: str = "test.pdf", content: bytes | None = None):
    """Create mock UploadFile for FastAPI endpoints.

    Args:
        mocker: pytest-mock mocker fixture
        filename: Filename for upload
        content: File content (uses create_test_pdf_content if not provided)

    Returns:
        Mock UploadFile instance
    """
    content = content or create_test_pdf_content()
    file_obj = BytesIO(content)

    upload_file = mocker.Mock()
    upload_file.filename = filename
    upload_file.file = file_obj
    upload_file.content_type = "application/pdf"

    return upload_file
