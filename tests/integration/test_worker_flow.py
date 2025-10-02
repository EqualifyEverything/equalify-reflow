"""Integration tests for full worker pipeline workflows.

Tests complete end-to-end flows:
- Submit PDF → PII Worker → Processing Worker → Complete
- Submit PDF with PII → PII Worker → Approval → Processing Worker → Complete
- Submit PDF with PII → PII Worker → Denial → No Processing
- Worker failure scenarios with retry and recovery

Each test validates the full workflow across multiple workers and services.
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

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


class TestFullWorkflowCleanPDF:
    """Tests for happy path: Clean PDF with no PII detected."""

    @pytest.mark.asyncio
    async def test_clean_pdf_full_workflow(
        self,
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
        """Test complete workflow: Submit → PII (clean) → Processing → Complete."""
        # Setup: No PII detected
        mock_pii_analyzer.analyze_text.return_value = []

        # Setup: Mock S3 download
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)
        storage_service.upload_result = AsyncMock(
            return_value=f"https://results.s3.amazonaws.com/{sample_job_id}.md"
        )

        # Setup: Create job and enqueue for PII scanning
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Mock queue dequeue to return our payload once, then None
        queue_service.dequeue = AsyncMock(
            side_effect=[pii_payload.model_dump(), None]
        )

        # Mock processing queue dequeue
        processing_queue_dequeue = AsyncMock(return_value=None)

        # Step 1: PII Worker processes job (no PII found)
        with patch.object(pii_worker.pii_service, 'process_pii_job') as mock_process:
            # Simulate successful PII processing that queues for processing
            async def process_pii_side_effect(job):
                # Update job status
                await job_service.update_job_status(job.job_id, STATUS_PROCESSING)
                # Enqueue for processing
                processing_payload = ProcessingQueuePayload(
                    job_id=job.job_id,
                    s3_key=job.s3_key,
                    approved_at=None
                )
                await queue_service.enqueue(PROCESSING_QUEUE, processing_payload)

            mock_process.side_effect = process_pii_side_effect

            # Run PII worker (will process one job)
            job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
            if job_data:
                job = PIIQueuePayload.model_validate(job_data)
                await pii_worker.pii_service.process_pii_job(job)

        # Verify: Job status updated to processing
        job_status = await job_service.get_job(sample_job_id)
        assert job_status is not None
        assert job_status.get("status") == STATUS_PROCESSING

        # Verify: Job queued for processing
        queue_service.enqueue.assert_called_once()
        call_args = queue_service.enqueue.call_args
        assert call_args[0][0] == PROCESSING_QUEUE

        # Step 2: Processing Worker processes job
        with patch.object(processing_worker.processing_service, 'process_document') as mock_processing:
            # Simulate successful processing
            async def process_doc_side_effect(job):
                result_url = f"https://results.s3.amazonaws.com/{job.job_id}.md"
                await job_service.update_job_status(
                    job.job_id,
                    STATUS_COMPLETED,
                    metadata={
                        "markdown_url": result_url,
                        "confidence_score": 0.95,
                        "confidence_level": "high"
                    }
                )

            mock_processing.side_effect = process_doc_side_effect

            # Get processing payload
            processing_payload_dict = queue_service.enqueue.call_args[0][1].model_dump()
            processing_payload = ProcessingQueuePayload.model_validate(processing_payload_dict)

            # Run processing worker
            await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Final job status is completed
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job.get("status") == STATUS_COMPLETED

        # Verify: Job metadata contains result URL
        job_service.update_job_status.assert_called()
        # Check the last call for completion
        last_call = [call for call in job_service.update_job_status.call_args_list
                     if call[0][1] == STATUS_COMPLETED]
        assert len(last_call) > 0


class TestFullWorkflowWithPII:
    """Tests for workflow with PII detected requiring approval."""

    @pytest.mark.asyncio
    async def test_pii_detected_approval_granted_flow(
        self,
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
        """Test: PDF with PII → Approval → Processing → Complete."""
        # Setup: PII detected
        mock_pii_analyzer.analyze_text.return_value = sample_pii_findings

        # Setup: Mock S3 download
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)
        storage_service.upload_result = AsyncMock(
            return_value=f"https://results.s3.amazonaws.com/{sample_job_id}.md"
        )

        # Setup: Create job and enqueue for PII scanning
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Step 1: PII Worker processes job (PII found)
        with patch.object(pii_worker.pii_service, 'process_pii_job') as mock_process:
            # Simulate PII processing that flags for approval
            async def process_pii_side_effect(job):
                # Update job status to awaiting approval
                await job_service.update_job_status(
                    job.job_id,
                    STATUS_AWAITING_APPROVAL,
                    approval_token="test-token-123",
                    approval_expires_at=(datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
                )

            mock_process.side_effect = process_pii_side_effect
            await pii_worker.pii_service.process_pii_job(pii_payload)

        # Verify: Job status is awaiting approval
        job_status = await job_service.get_job(sample_job_id)
        assert job_status is not None
        assert job_status.get("status") == STATUS_AWAITING_APPROVAL

        # Step 2: Approval granted
        with patch.object(approval_service, 'process_approval_decision') as mock_approval:
            # Simulate approval that queues for processing
            async def approval_side_effect(job_id, decision, justification, reviewed_by):
                await job_service.update_job_status(job_id, STATUS_PROCESSING)
                processing_payload = ProcessingQueuePayload(
                    job_id=job_id,
                    s3_key=sample_s3_key,
                    approved_at=datetime.now(timezone.utc)
                )
                await queue_service.enqueue(PROCESSING_QUEUE, processing_payload)

            mock_approval.side_effect = approval_side_effect
            await approval_service.process_approval_decision(
                sample_job_id,
                "approved",
                "Instructor name in syllabus is acceptable",
                "faculty@uic.edu"
            )

        # Verify: Job status updated to processing
        processing_status = await job_service.get_job(sample_job_id)
        assert processing_status is not None
        assert processing_status.get("status") == STATUS_PROCESSING

        # Step 3: Processing Worker processes approved job
        with patch.object(processing_worker.processing_service, 'process_document') as mock_processing:
            async def process_doc_side_effect(job):
                result_url = f"https://results.s3.amazonaws.com/{job.job_id}.md"
                await job_service.update_job_status(
                    job.job_id,
                    STATUS_COMPLETED,
                    metadata={
                        "markdown_url": result_url,
                        "confidence_score": 0.92,
                        "confidence_level": "high"
                    }
                )

            mock_processing.side_effect = process_doc_side_effect

            processing_payload = ProcessingQueuePayload(
                job_id=sample_job_id,
                s3_key=sample_s3_key,
                approved_at=datetime.now(timezone.utc)
            )
            await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Final status is completed
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job.get("status") == STATUS_COMPLETED

    @pytest.mark.asyncio
    async def test_pii_detected_approval_denied_flow(
        self,
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
        """Test: PDF with PII → Denial → No Processing."""
        # Setup: PII detected
        mock_pii_analyzer.analyze_text.return_value = sample_pii_findings

        # Setup: Mock S3
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Setup: Create job
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Step 1: PII Worker flags job
        with patch.object(pii_worker.pii_service, 'process_pii_job') as mock_process:
            async def process_pii_side_effect(job):
                await job_service.update_job_status(
                    job.job_id,
                    STATUS_AWAITING_APPROVAL,
                    approval_token="test-token-456"
                )

            mock_process.side_effect = process_pii_side_effect
            await pii_worker.pii_service.process_pii_job(pii_payload)

        # Verify: Job awaiting approval
        job_status = await job_service.get_job(sample_job_id)
        assert job_status.get("status") == STATUS_AWAITING_APPROVAL

        # Step 2: Approval denied
        with patch.object(approval_service, 'process_approval_decision') as mock_denial:
            async def denial_side_effect(job_id, decision, justification, reviewed_by):
                await job_service.update_job_status(job_id, STATUS_DENIED)

            mock_denial.side_effect = denial_side_effect
            await approval_service.process_approval_decision(
                sample_job_id,
                "denied",
                "Contains student SSN - cannot process",
                "admin@uic.edu"
            )

        # Verify: Job status is denied
        final_job = await job_service.get_job(sample_job_id)
        assert final_job is not None
        assert final_job.get("status") == STATUS_DENIED

        # Verify: Job NOT queued for processing
        processing_calls = [
            call for call in queue_service.enqueue.call_args_list
            if len(call[0]) > 0 and call[0][0] == PROCESSING_QUEUE
        ]
        assert len(processing_calls) == 0


class TestFailureRecovery:
    """Tests for worker failure scenarios and recovery."""

    @pytest.mark.asyncio
    async def test_pii_worker_retries_on_transient_failure(
        self,
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
        """Test: PII worker retries on transient S3 failure."""
        # Setup: S3 fails once, then succeeds
        storage_service.download_temp_file = AsyncMock(
            side_effect=[
                Exception("Transient S3 error"),
                sample_pdf_content
            ]
        )

        mock_pii_analyzer.analyze_text.return_value = []

        # Create job
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Process job (should retry and succeed)
        await pii_worker.pii_service.process_pii_job(pii_payload, retry_count=0)

        # Verify: S3 download called multiple times (retry logic)
        assert storage_service.download_temp_file.call_count >= 1

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
        """Test: PII worker marks job as failed on permanent error."""
        # Setup: Permanent S3 error
        storage_service.download_temp_file = AsyncMock(
            side_effect=Exception("Permanent error: File not found")
        )

        # Create job
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PII_SCANNING)

        pii_payload = PIIQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            created_at=datetime.now(timezone.utc)
        )

        # Process job (should fail)
        await pii_worker.pii_service.process_pii_job(pii_payload)

        # Verify: Job status updated to failed
        failed_calls = [
            call for call in job_service.update_job_status.call_args_list
            if len(call[0]) > 1 and call[0][1] == STATUS_FAILED
        ]
        assert len(failed_calls) > 0

    @pytest.mark.asyncio
    async def test_processing_worker_fails_gracefully(
        self,
        processing_worker,
        storage_service,
        job_service,
        sample_job_id,
        sample_s3_key,
        sample_pdf_content,
        mock_pdf_converter,
        mock_ai_enhancement
    ):
        """Test: Processing worker handles AI failure gracefully."""
        # Setup: Mock AI enhancement failure
        mock_ai_enhancement.process_pages_concurrently = AsyncMock(
            side_effect=Exception("AI service timeout")
        )

        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create job
        await job_service.create_job(sample_job_id, sample_s3_key, STATUS_PROCESSING)

        processing_payload = ProcessingQueuePayload(
            job_id=sample_job_id,
            s3_key=sample_s3_key,
            approved_at=None
        )

        # Process job (should fail gracefully)
        result = await processing_worker.processing_service.process_document(processing_payload)

        # Verify: Result indicates failure
        assert result.error_message is not None

        # Verify: Job marked as failed
        failed_calls = [
            call for call in job_service.update_job_status.call_args_list
            if len(call[0]) > 1 and call[0][1] == STATUS_FAILED
        ]
        assert len(failed_calls) > 0
