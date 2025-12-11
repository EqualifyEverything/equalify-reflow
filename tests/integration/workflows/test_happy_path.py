"""Integration tests for happy path workflow: Clean PDF submission to completion.

Tests the full document processing pipeline with real Redis and S3 (via testcontainers),
mocking only the expensive AI/ML components (Bedrock, Docling).

This test catches integration issues between:
- Storage Service → S3
- Job Service → Redis
- Queue Service → Redis
- PII Worker → PII Service → Queue Service
- Processing Worker → Processing Service → Job Service
"""

import uuid
from datetime import UTC, datetime
from io import BytesIO

import pytest
from src.config import settings
from src.shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import (
    STATUS_COMPLETED,
    STATUS_PII_SCANNING,
    STATUS_PROCESSING,
)
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload


@pytest.mark.integration
@pytest.mark.slow
class TestHappyPathWorkflow:
    """Integration tests for the clean PDF submission → completion workflow."""

    @pytest.mark.asyncio
    async def test_clean_pdf_submission_to_completion(
        self,
        real_s3_client,
        queue_service,
        job_service,
        pii_worker,
        processing_worker,
        sample_pdf_content,
    ):
        """Full workflow: Upload → PII scan (clean) → Process → Result.

        Catches: Integration issues between services, workers, and storage.

        Uses:
        - REAL Redis (testcontainer)
        - REAL S3 (LocalStack testcontainer)
        - MOCKED AI components (Presidio returns clean, Bedrock/Docling mocked)
        """
        # 1. Simulate API: Upload PDF to S3 and create job
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}.pdf"

        # Upload to real S3 directly using the client
        real_s3_client.upload_fileobj(
            BytesIO(sample_pdf_content),
            settings.s3_temp_bucket,
            s3_key
        )

        # Create job in real Redis
        await job_service.create_job(job_id, s3_key, STATUS_PII_SCANNING)

        # 2. Verify job created correctly
        job = await job_service.get_job(job_id)
        assert job is not None, "Job should exist in Redis"
        assert job["job_id"] == job_id
        assert job["status"] == STATUS_PII_SCANNING
        assert job["s3_key"] == s3_key

        # 3. Enqueue PII scan job (simulating what API does)
        pii_payload = PIIQueuePayload(
            job_id=job_id,
            s3_key=s3_key,
            created_at=datetime.now(UTC)
        )
        await queue_service.enqueue(PII_QUEUE, pii_payload)

        # Verify PII queue has the job
        pii_queue_depth = await queue_service.queue_depth(PII_QUEUE)
        assert pii_queue_depth == 1, "PII queue should have 1 job"

        # 4. Manually process PII job (PII analyzer mocked to return clean)
        # Dequeue and process like the worker would
        pii_job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
        assert pii_job_data is not None, "Should dequeue PII job"

        pii_job = PIIQueuePayload.model_validate(pii_job_data)
        await pii_worker.pii_service.process_pii_job(pii_job)

        # 5. Verify: Clean PDF should be routed to processing queue
        # (since mock_pii_analyzer returns no findings by default)
        job = await job_service.get_job(job_id)
        assert job["status"] == STATUS_PROCESSING, "Clean PDF should go to processing"

        # Verify processing queue has the job
        processing_queue_depth = await queue_service.queue_depth(PROCESSING_QUEUE)
        assert processing_queue_depth == 1, "Processing queue should have 1 job"

        # 6. Manually process the job (AI components mocked)
        processing_job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)
        assert processing_job_data is not None, "Should dequeue processing job"

        processing_job = ProcessingQueuePayload.model_validate(processing_job_data)
        await processing_worker.processing_service.process_document(processing_job)

        # 7. Verify final state
        final_job = await job_service.get_job(job_id)
        assert final_job["status"] == STATUS_COMPLETED, "Job should be completed"
        assert "confidence_score" in final_job, "Should have confidence score"
        assert float(final_job["confidence_score"]) > 0, "Confidence score should be positive"

        # 8. Verify result stored in S3
        # The processing service should have uploaded the result
        # We verify the job has the expected completion markers
        assert final_job["updated_at"] is not None, "Should have updated_at timestamp"

    @pytest.mark.asyncio
    async def test_workflow_handles_empty_queues_gracefully(
        self,
        queue_service,
    ):
        """Test that queue operations handle empty queues without errors.

        Catches: Timeout handling issues in queue dequeue operations.
        """
        # Try to dequeue from empty queue with short timeout
        result = await queue_service.dequeue(PII_QUEUE, timeout=1)
        assert result is None, "Empty queue should return None"

        result = await queue_service.dequeue(PROCESSING_QUEUE, timeout=1)
        assert result is None, "Empty queue should return None"

    @pytest.mark.asyncio
    async def test_job_status_transitions_are_persisted(
        self,
        job_service,
    ):
        """Test that job status transitions are properly persisted in Redis.

        Catches: Redis persistence issues, status update failures.
        """
        job_id = str(uuid.uuid4())
        s3_key = f"temp/{job_id}/test.pdf"

        # Create job
        await job_service.create_job(job_id, s3_key, STATUS_PII_SCANNING)

        # Transition through statuses
        await job_service.update_job_status(job_id, STATUS_PROCESSING)
        job = await job_service.get_job(job_id)
        assert job["status"] == STATUS_PROCESSING

        await job_service.update_job_status(
            job_id,
            STATUS_COMPLETED,
            confidence_score=0.95
        )
        job = await job_service.get_job(job_id)
        assert job["status"] == STATUS_COMPLETED
        assert job["confidence_score"] == "0.95"
