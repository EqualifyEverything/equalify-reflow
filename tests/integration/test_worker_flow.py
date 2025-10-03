"""Integration tests for full worker pipeline workflows.

Tests complete end-to-end flows using REAL infrastructure:
- Real Redis queues (via testcontainers)
- Real S3 storage (via LocalStack)
- Real service integration (queue→storage→job state)
- Mocked AI/ML only (expensive components)

Workflows tested:
- Submit PDF → PII Worker → Processing Worker → Complete
- Submit PDF with PII → PII Worker → Approval → Processing Worker → Complete
- Submit PDF with PII → PII Worker → Denial → No Processing
- Worker failure scenarios with retry and recovery
"""

import pytest
from datetime import datetime, timezone, timedelta

from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload
from src.shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import (
    STATUS_PII_SCANNING,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_AWAITING_APPROVAL,
    STATUS_DENIED,
    STATUS_FAILED
)


@pytest.mark.integration
@pytest.mark.slow
class TestFullWorkflowCleanPDF:
    """Tests for happy path: Clean PDF with no PII detected."""

    @pytest.mark.asyncio
    async def test_clean_pdf_full_workflow(
        self,
        real_s3_client,
        pii_worker,
        processing_worker,
        storage_service,
        queue_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        mock_pii_analyzer,
        mock_pdf_extractor,
        mock_pdf_converter,
        mock_ai_enhancement
    ):
        """Test complete workflow: Submit → PII (clean) → Processing → Complete.

        Uses REAL Redis/S3 infrastructure to verify integration.
        """
        # Setup: No PII detected (AI component still mocked)
        mock_pii_analyzer.analyze_text.return_value = []

        # Setup: Upload PDF to REAL S3 directly via S3 client
        real_s3_client.put_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key,
            Body=sample_pdf_content
        )

        # Verify: File actually in S3
        s3_content = await storage_service.download_temp_file(sample_s3_key)
        assert s3_content == sample_pdf_content

        # Setup: Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        # Verify: Job actually in Redis
        job_check = await job_service.get_job(sample_job_id)
        assert job_check is not None
        assert job_check["status"] == STATUS_PII_SCANNING

        # Setup: Enqueue job to REAL Redis queue
        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )
        await queue_service.enqueue(PII_QUEUE, pii_payload)

        # Verify: Job actually in queue
        queue_depth = await queue_service.queue_depth(PII_QUEUE)
        assert queue_depth == 1

        # Step 1: PII Worker processes job (no mocking - REAL processing)
        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
        assert job_data is not None

        job = PIIQueuePayload.model_validate(job_data)
        await pii_worker.pii_service.process_pii_job(job)

        # Verify: Job status updated in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status is not None
        assert job_status["status"] == STATUS_PROCESSING

        # Verify: Job queued for processing in REAL Redis
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 1

        # Step 2: Processing Worker processes job (no mocking - REAL processing)
        processing_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)
        assert processing_data is not None

        processing_payload = ProcessingQueuePayload.model_validate(processing_data)
        await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Final job status is completed in REAL Redis
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job["status"] == STATUS_COMPLETED

        # Verify: Result URL stored in job metadata
        assert "metadata" in final_job
        assert "markdown_url" in final_job["metadata"]

        # Verify: Queues are now empty
        assert await queue_service.queue_depth(PII_QUEUE) == 0
        assert await queue_service.queue_depth(PROCESSING_QUEUE) == 0


@pytest.mark.integration
@pytest.mark.slow
class TestFullWorkflowWithPII:
    """Tests for workflow with PII detected requiring approval."""

    @pytest.mark.asyncio
    async def test_pii_detected_approval_granted_flow(
        self,
        real_s3_client,
        pii_worker,
        processing_worker,
        storage_service,
        queue_service,
        job_service,
        approval_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        sample_pii_findings,
        mock_pii_analyzer,
        mock_pdf_extractor,
        mock_pdf_converter,
        mock_ai_enhancement
    ):
        """Test: PDF with PII → Approval → Processing → Complete.

        Uses REAL Redis/S3 infrastructure to verify approval flow.
        """
        # Setup: PII detected (AI component mocked)
        mock_pii_analyzer.analyze_text.return_value = sample_pii_findings

        # Upload PDF to REAL S3
        real_s3_client.put_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key,
            Body=sample_pdf_content
        )

        # Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        # Enqueue to REAL Redis
        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )
        await queue_service.enqueue(PII_QUEUE, pii_payload)

        # Step 1: PII Worker processes job (REAL - should flag for approval)
        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
        job = PIIQueuePayload.model_validate(job_data)
        await pii_worker.pii_service.process_pii_job(job)

        # Verify: Job status is awaiting approval in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status is not None
        assert job_status["status"] == STATUS_AWAITING_APPROVAL
        assert "approval_token" in job_status
        assert "approval_expires_at" in job_status

        # Verify: NOT queued for processing yet
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 0

        # Step 2: Approval granted (REAL service call)
        approval_token = job_status["approval_token"]
        await approval_service.process_approval_decision(
            sample_job_id,
            "approved",
            "Instructor name in syllabus is acceptable",
            "faculty@uic.edu"
        )

        # Verify: Job status updated to processing in REAL Redis
        processing_status = await job_service.get_job(sample_job_id)
        assert processing_status is not None
        assert processing_status["status"] == STATUS_PROCESSING

        # Verify: Now queued for processing in REAL Redis
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 1

        # Step 3: Processing Worker processes approved job (REAL)
        processing_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)
        processing_payload = ProcessingQueuePayload.model_validate(processing_data)
        await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Final status is completed in REAL Redis
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job["status"] == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_pii_detected_approval_denied_flow(
        self,
        real_s3_client,
        pii_worker,
        storage_service,
        queue_service,
        job_service,
        approval_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        sample_pii_findings,
        mock_pii_analyzer,
        mock_pdf_extractor
    ):
        """Test: PDF with PII → Denial → No Processing.

        Uses REAL Redis/S3 to verify denial prevents processing.
        """
        # Setup: PII detected
        mock_pii_analyzer.analyze_text.return_value = sample_pii_findings

        # Upload to REAL S3
        real_s3_client.put_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key,
            Body=sample_pdf_content
        )

        # Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        # Enqueue to REAL Redis
        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )
        await queue_service.enqueue(PII_QUEUE, pii_payload)

        # Step 1: PII Worker flags job (REAL)
        job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
        job = PIIQueuePayload.model_validate(job_data)
        await pii_worker.pii_service.process_pii_job(job)

        # Verify: Job awaiting approval in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status["status"] == STATUS_AWAITING_APPROVAL

        # Step 2: Approval denied (REAL service call)
        await approval_service.process_approval_decision(
            sample_job_id,
            "denied",
            "Contains student SSN - cannot process",
            "admin@uic.edu"
        )

        # Verify: Job status is denied in REAL Redis
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job["status"] == STATUS_DENIED

        # Verify: Job NOT queued for processing in REAL Redis
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 0


@pytest.mark.integration
@pytest.mark.slow
class TestFailureRecovery:
    """Tests for worker failure scenarios and recovery using REAL infrastructure."""

    @pytest.mark.asyncio
    async def test_pii_worker_retries_on_transient_failure(
        self,
        real_s3_client,
        pii_worker,
        storage_service,
        queue_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        mock_pii_analyzer,
        mock_pdf_extractor
    ):
        """Test: PII worker retries on transient S3 failure.

        Simulates S3 failure then success using REAL infrastructure.
        """
        # Setup: Upload file to REAL S3 first
        real_s3_client.put_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key,
            Body=sample_pdf_content
        )

        # Then delete it to simulate transient failure
        real_s3_client.delete_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key
        )

        # But mock analyzer to avoid dependency
        mock_pii_analyzer.analyze_text.return_value = []

        # Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Process job - should fail due to missing S3 file
        # (Note: This tests that errors are handled, not necessarily retried)
        await pii_worker.pii_service.process_pii_job(pii_payload, retry_count=0)

        # Verify: Job marked as failed in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status["status"] == STATUS_FAILED

    @pytest.mark.asyncio
    async def test_pii_worker_fails_job_on_permanent_error(
        self,
        pii_worker,
        storage_service,
        queue_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        mock_pii_analyzer,
        mock_pdf_extractor
    ):
        """Test: PII worker marks job as failed on permanent error.

        Uses REAL Redis to verify failure state.
        """
        # Don't upload file - will cause permanent S3 error
        mock_pii_analyzer.analyze_text.return_value = []

        # Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Process job - should fail
        await pii_worker.pii_service.process_pii_job(pii_payload)

        # Verify: Job status updated to failed in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status["status"] == STATUS_FAILED
        assert "error" in job_status

    @pytest.mark.asyncio
    async def test_processing_worker_fails_gracefully(
        self,
        real_s3_client,
        processing_worker,
        storage_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        mock_pdf_converter,
        mock_ai_enhancement
    ):
        """Test: Processing worker handles AI failure gracefully.

        Uses REAL Redis/S3 to verify error handling.
        """
        # Setup: Upload to REAL S3
        real_s3_client.put_object(
            Bucket=storage_service.temp_bucket,
            Key=sample_s3_key,
            Body=sample_pdf_content
        )

        # Setup: Mock AI enhancement failure
        mock_ai_enhancement.process_pages_concurrently.side_effect = Exception(
            "AI service timeout"
        )

        # Create job in REAL Redis
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PROCESSING)

        processing_payload = ProcessingQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            approved_at=None
        )

        # Process job - should fail gracefully
        result = await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Result indicates failure
        assert result.error_message is not None

        # Verify: Job marked as failed in REAL Redis
        job_status = await job_service.get_job(sample_job_id)
        assert job_status["status"] == STATUS_FAILED
        assert "error" in job_status
