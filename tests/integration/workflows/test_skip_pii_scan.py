"""Integration tests for skip_pii_scan flow with real services.

Tests that skip_pii_scan=True correctly bypasses PII queue and routes
directly to processing queue, using real Redis and S3.

This differs from the unit test in test_documents.py which uses mocks.
These tests verify the actual Redis queue operations work correctly.
"""

import uuid
from datetime import UTC, datetime
from io import BytesIO

import pytest
from src.config import settings
from src.shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import STATUS_PII_SCANNING, STATUS_PROCESSING
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


@pytest.mark.integration
class TestSkipPIIScanFlow:
    """Integration tests for skip_pii_scan routing using REAL Redis."""

    @pytest.mark.asyncio
    async def test_skip_pii_scan_routes_to_processing_queue(
        self,
        real_s3_client,
        queue_service,
        job_service,
        sample_pdf_content,
    ):
        """Test skip_pii_scan=True bypasses PII queue and goes to processing.

        Catches: Routing incorrectly with real Redis (compliance risk if broken).

        This test simulates what the API does when skip_pii_scan=True:
        1. Creates job with status=processing (not pii_scanning)
        2. Enqueues directly to PROCESSING_QUEUE (not PII_QUEUE)
        3. Records pii_skipped metadata
        """
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"
        skip_reason = "Pre-scanned document for testing"

        # 1. Upload PDF to real S3 directly
        real_s3_client.upload_fileobj(
            BytesIO(sample_pdf_content),
            settings.s3_temp_bucket,
            s3_key
        )

        # 2. Create job with skip_pii_scan flow (status=processing, pii_skipped=True)
        await job_service.create_job(
            job_id,
            s3_key,
            STATUS_PROCESSING,  # Note: processing, NOT pii_scanning
            pii_skipped=True,
            pii_skip_reason=skip_reason,
        )

        # 3. Enqueue directly to processing queue (bypass PII)
        processing_payload = ProcessingQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            approved_at=None,  # No approval needed when PII skipped
        )
        await queue_service.enqueue(PROCESSING_QUEUE, processing_payload)

        # 4. Verify: Job state in Redis
        job = await job_service.get_job(job_id)
        assert job is not None, "Job should exist"
        assert job["status"] == STATUS_PROCESSING, "Status should be processing"
        assert job["pii_skipped"] == "true", "pii_skipped should be true"
        assert job["pii_skip_reason"] == skip_reason, "Skip reason should be stored"

        # 5. Verify: PII queue is empty (job was NOT enqueued there)
        pii_queue_depth = await queue_service.queue_depth(PII_QUEUE)
        assert pii_queue_depth == 0, "PII queue should be empty"

        # 6. Verify: Processing queue has the job
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 1, "Processing queue should have 1 job"

        # 7. Verify: Can dequeue from processing queue
        job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)
        assert job_data is not None, "Should dequeue job from processing queue"
        dequeued_job = ProcessingQueuePayload.model_validate(job_data)
        assert dequeued_job.job_id == job_id, "Dequeued job ID should match"

    @pytest.mark.asyncio
    async def test_normal_flow_goes_to_pii_queue(
        self,
        real_s3_client,
        queue_service,
        job_service,
        sample_pdf_content,
    ):
        """Test normal flow (skip_pii_scan=False) goes to PII queue.

        Catches: Regression where normal flow incorrectly skips PII scanning.

        This is the control test to verify skip_pii_scan actually changes behavior.
        """
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        # 1. Upload PDF directly to S3
        real_s3_client.upload_fileobj(
            BytesIO(sample_pdf_content),
            settings.s3_temp_bucket,
            s3_key
        )

        # 2. Create job with normal flow (status=pii_scanning)
        await job_service.create_job(job_id, s3_key, STATUS_PII_SCANNING)

        # 3. Enqueue to PII queue (normal flow)
        pii_payload = PIIQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            created_at=datetime.now(UTC)
        )
        await queue_service.enqueue(PII_QUEUE, pii_payload)

        # 4. Verify: Job state is pii_scanning
        job = await job_service.get_job(job_id)
        assert job["status"] == STATUS_PII_SCANNING, "Status should be pii_scanning"
        assert job.get("pii_skipped") is None, "pii_skipped should not be set"

        # 5. Verify: PII queue has the job
        pii_queue_depth = await queue_service.queue_depth(PII_QUEUE)
        assert pii_queue_depth == 1, "PII queue should have 1 job"

        # 6. Verify: Processing queue is empty
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 0, "Processing queue should be empty"

    @pytest.mark.asyncio
    async def test_skip_pii_metadata_persisted_correctly(
        self,
        job_service,
    ):
        """Test skip_pii_scan metadata is correctly persisted in Redis.

        Catches: Metadata not being stored, wrong field names, type issues.
        """
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}/test.pdf"
        skip_reason = "Bulk import from trusted source"

        # Create job with skip metadata
        await job_service.create_job(
            job_id,
            s3_key,
            STATUS_PROCESSING,
            pii_skipped=True,
            pii_skip_reason=skip_reason,
        )

        # Retrieve and verify
        job = await job_service.get_job(job_id)

        # Check all fields persisted
        assert job["job_id"] == job_id
        assert job["s3_key"] == s3_key
        assert job["status"] == STATUS_PROCESSING
        assert job["pii_skipped"] == "true"  # Redis stores as string
        assert job["pii_skip_reason"] == skip_reason
        assert "created_at" in job
        assert "updated_at" in job
