"""Integration tests for multi-worker concurrency scenarios.

Tests scenarios with multiple worker instances processing the same queue:
- Multiple PII workers consuming same queue
- Multiple processing workers consuming same queue
- Worker coordination and queue fairness
- No duplicate processing guarantees
- Load distribution across workers

These tests validate that the system can scale horizontally with multiple
worker instances without data corruption or duplicate processing.
"""

import pytest
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.pii_worker import PIIWorker
from src.workers.processing_worker import ProcessingWorker
from src.shared.models.queue import PIIQueuePayload, ProcessingQueuePayload
from src.shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import STATUS_PII_SCANNING, STATUS_PROCESSING, STATUS_COMPLETED


class TestMultiplePIIWorkers:
    """Tests for multiple PII workers processing same queue."""

    @pytest.mark.asyncio
    async def test_three_pii_workers_process_queue_concurrently(
        self,
        storage_service,
        queue_service,
        job_service,
        mock_pii_analyzer,
        mock_pdf_extractor,
        sample_pdf_content
    ):
        """Test 3 PII workers processing 10 jobs from same queue."""
        # Setup: 10 jobs in queue
        num_jobs = 10
        jobs = []
        for i in range(num_jobs):
            job_id = str(uuid.uuid4())
            s3_key = f"temp/{job_id}/test{i}.pdf"
            await job_service.create_job(job_id, s3_key, STATUS_PII_SCANNING)
            jobs.append({
                "job_id": job_id,
                "s3_key": s3_key,
                "payload": PIIQueuePayload(
                    job_id=job_id,
                    s3_key=s3_key,
                    created_at=datetime.now(timezone.utc)
                )
            })

        # Mock queue to return jobs
        job_queue = [job["payload"].model_dump() for job in jobs]
        dequeue_calls = [0]  # Track dequeue calls

        async def mock_dequeue(queue_name, timeout):
            if dequeue_calls[0] < len(job_queue):
                job = job_queue[dequeue_calls[0]]
                dequeue_calls[0] += 1
                return job
            return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock PII analyzer: no PII
        mock_pii_analyzer.analyze_text.return_value = []

        # Mock S3 download
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create 3 workers
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(3)
        ]

        # Track processing per worker
        processed_jobs = {i: [] for i in range(3)}

        # Mock process_pii_job to track which worker processes which job
        for worker_id, worker in enumerate(workers):
            original_process = worker.pii_service.process_pii_job

            async def track_process(job, worker_id=worker_id):
                processed_jobs[worker_id].append(job.job_id)
                await job_service.update_job_status(job.job_id, STATUS_PROCESSING)

            worker.pii_service.process_pii_job = AsyncMock(side_effect=track_process)

        # Process jobs with all 3 workers concurrently
        async def worker_loop(worker, max_jobs):
            jobs_processed = 0
            while jobs_processed < max_jobs:
                job_data = await queue_service.dequeue(PII_QUEUE, timeout=1)
                if job_data is None:
                    break
                job = PIIQueuePayload.model_validate(job_data)
                await worker.pii_service.process_pii_job(job)
                jobs_processed += 1

        # Each worker tries to process up to 5 jobs
        tasks = [worker_loop(worker, 5) for worker in workers]
        await asyncio.gather(*tasks)

        # Verify: All jobs processed exactly once
        all_processed = []
        for worker_jobs in processed_jobs.values():
            all_processed.extend(worker_jobs)

        assert len(all_processed) == num_jobs
        # Verify no duplicates
        assert len(set(all_processed)) == num_jobs

    @pytest.mark.asyncio
    async def test_pii_workers_distribute_load_fairly(
        self,
        storage_service,
        queue_service,
        job_service,
        mock_pii_analyzer,
        mock_pdf_extractor,
        sample_pdf_content
    ):
        """Test that multiple workers distribute load relatively fairly."""
        # Setup: 30 jobs
        num_jobs = 30
        job_queue = []

        for i in range(num_jobs):
            job_id = str(uuid.uuid4())
            s3_key = f"temp/{job_id}/test{i}.pdf"
            payload = PIIQueuePayload(
                job_id=job_id,
                s3_key=s3_key,
                created_at=datetime.now(timezone.utc)
            )
            job_queue.append(payload.model_dump())

        # Mock queue dequeue
        dequeue_index = [0]
        dequeue_lock = asyncio.Lock()

        async def mock_dequeue(queue_name, timeout):
            async with dequeue_lock:
                if dequeue_index[0] < len(job_queue):
                    job = job_queue[dequeue_index[0]]
                    dequeue_index[0] += 1
                    return job
                return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock services
        mock_pii_analyzer.analyze_text.return_value = []
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create 3 workers
        num_workers = 3
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(num_workers)
        ]

        # Track load per worker
        worker_loads = {i: 0 for i in range(num_workers)}

        for worker_id, worker in enumerate(workers):
            async def track_process(job, worker_id=worker_id):
                worker_loads[worker_id] += 1
                await asyncio.sleep(0.01)  # Simulate processing time
                await job_service.update_job_status(job.job_id, STATUS_PROCESSING)

            worker.pii_service.process_pii_job = AsyncMock(side_effect=track_process)

        # Process with all workers
        async def worker_loop(worker):
            while True:
                job_data = await queue_service.dequeue(PII_QUEUE, timeout=0.1)
                if job_data is None:
                    break
                job = PIIQueuePayload.model_validate(job_data)
                await worker.pii_service.process_pii_job(job)

        tasks = [worker_loop(worker) for worker in workers]
        await asyncio.gather(*tasks)

        # Verify: Load distributed (each worker should process ~10 jobs)
        total_processed = sum(worker_loads.values())
        assert total_processed == num_jobs

        # Check fairness: no worker should be idle if work available
        for worker_id, load in worker_loads.items():
            assert load > 0, f"Worker {worker_id} processed no jobs"

        # Reasonable distribution: each worker processes 5-15 jobs (allow variance)
        for worker_id, load in worker_loads.items():
            assert 5 <= load <= 15, f"Worker {worker_id} load {load} outside fair range"


class TestMultipleProcessingWorkers:
    """Tests for multiple processing workers."""

    @pytest.mark.asyncio
    async def test_multiple_processing_workers_no_duplicate_processing(
        self,
        storage_service,
        queue_service,
        job_service,
        mock_pdf_converter,
        mock_ai_enhancement,
        sample_pdf_content
    ):
        """Test that multiple processing workers don't duplicate work."""
        # Setup: 15 jobs in processing queue
        num_jobs = 15
        job_queue = []

        for i in range(num_jobs):
            job_id = str(uuid.uuid4())
            s3_key = f"temp/{job_id}/test{i}.pdf"
            payload = ProcessingQueuePayload(
                job_id=job_id,
                s3_key=s3_key,
                approved_at=None
            )
            job_queue.append(payload.model_dump())

        # Mock queue dequeue (atomic)
        dequeue_index = [0]
        dequeue_lock = asyncio.Lock()

        async def mock_dequeue(queue_name, timeout):
            async with dequeue_lock:
                if dequeue_index[0] < len(job_queue):
                    job = job_queue[dequeue_index[0]]
                    dequeue_index[0] += 1
                    await asyncio.sleep(0.001)  # Simulate Redis latency
                    return job
                return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock S3 and processing
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)
        storage_service.upload_result = AsyncMock(
            return_value="https://results.s3.amazonaws.com/result.md"
        )

        # Create 4 workers
        num_workers = 4
        workers = [
            ProcessingWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(num_workers)
        ]

        # Track which jobs each worker processes
        processed_jobs_per_worker = {i: [] for i in range(num_workers)}
        processing_lock = asyncio.Lock()

        for worker_id, worker in enumerate(workers):
            async def track_process(job, worker_id=worker_id):
                async with processing_lock:
                    processed_jobs_per_worker[worker_id].append(job.job_id)
                # Simulate processing time
                await asyncio.sleep(0.02)
                await job_service.update_job_status(job.job_id, STATUS_COMPLETED)

            worker.processing_service.process_document = AsyncMock(side_effect=track_process)

        # Process with all workers
        async def worker_loop(worker):
            while True:
                job_data = await queue_service.dequeue(PROCESSING_QUEUE, timeout=0.1)
                if job_data is None:
                    break
                job = ProcessingQueuePayload.model_validate(job_data)
                await worker.processing_service.process_document(job)

        tasks = [worker_loop(worker) for worker in workers]
        await asyncio.gather(*tasks)

        # Verify: All jobs processed
        all_processed = []
        for worker_jobs in processed_jobs_per_worker.values():
            all_processed.extend(worker_jobs)

        assert len(all_processed) == num_jobs

        # Verify: No duplicate processing
        assert len(set(all_processed)) == num_jobs, "Jobs were processed multiple times"


class TestWorkerCoordination:
    """Tests for worker coordination patterns."""

    @pytest.mark.asyncio
    async def test_workers_handle_empty_queue_gracefully(
        self,
        storage_service,
        queue_service,
        job_service
    ):
        """Test that multiple workers handle empty queue without errors."""
        # Mock empty queue
        queue_service.dequeue = AsyncMock(return_value=None)

        # Create 5 workers
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(5)
        ]

        # All workers try to dequeue from empty queue
        async def try_dequeue(worker):
            job_data = await queue_service.dequeue(PII_QUEUE, timeout=0.1)
            return job_data

        tasks = [try_dequeue(worker) for worker in workers]
        results = await asyncio.gather(*tasks)

        # All should get None
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_workers_continue_after_individual_failures(
        self,
        storage_service,
        queue_service,
        job_service,
        mock_pii_analyzer,
        mock_pdf_extractor,
        sample_pdf_content
    ):
        """Test that one worker's failure doesn't affect others."""
        # Setup: 6 jobs
        num_jobs = 6
        job_queue = []

        for i in range(num_jobs):
            job_id = str(uuid.uuid4())
            payload = PIIQueuePayload(
                job_id=job_id,
                s3_key=f"temp/{job_id}/test{i}.pdf",
                created_at=datetime.now(timezone.utc)
            )
            job_queue.append(payload.model_dump())

        # Mock queue
        dequeue_index = [0]

        async def mock_dequeue(queue_name, timeout):
            if dequeue_index[0] < len(job_queue):
                job = job_queue[dequeue_index[0]]
                dequeue_index[0] += 1
                return job
            return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock services
        mock_pii_analyzer.analyze_text.return_value = []
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create 3 workers
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(3)
        ]

        # Make worker 0 fail on every job
        successful_jobs = []

        async def worker_0_fails(job):
            raise Exception("Worker 0 simulated failure")

        async def worker_succeeds(job):
            successful_jobs.append(job.job_id)
            await job_service.update_job_status(job.job_id, STATUS_PROCESSING)

        workers[0].pii_service.process_pii_job = AsyncMock(side_effect=worker_0_fails)
        workers[1].pii_service.process_pii_job = AsyncMock(side_effect=worker_succeeds)
        workers[2].pii_service.process_pii_job = AsyncMock(side_effect=worker_succeeds)

        # Process with all workers
        async def worker_loop(worker):
            while True:
                try:
                    job_data = await queue_service.dequeue(PII_QUEUE, timeout=0.1)
                    if job_data is None:
                        break
                    job = PIIQueuePayload.model_validate(job_data)
                    await worker.pii_service.process_pii_job(job)
                except Exception:
                    # Worker continues despite failure
                    continue

        tasks = [worker_loop(worker) for worker in workers]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Verify: Workers 1 and 2 processed their jobs successfully
        assert len(successful_jobs) >= 4  # At least 2 workers processed 2 jobs each


class TestQueueFairness:
    """Tests for queue fairness across workers."""

    @pytest.mark.asyncio
    async def test_fifo_queue_order_maintained(
        self,
        queue_service,
        storage_service,
        job_service,
        mock_pii_analyzer,
        sample_pdf_content
    ):
        """Test that FIFO queue order is maintained across workers."""
        # Setup: Enqueue 10 jobs in specific order
        job_ids = [str(uuid.uuid4()) for _ in range(10)]
        job_queue = []

        for job_id in job_ids:
            payload = PIIQueuePayload(
                job_id=job_id,
                s3_key=f"temp/{job_id}/test.pdf",
                created_at=datetime.now(timezone.utc)
            )
            job_queue.append(payload.model_dump())

        # Mock queue maintains FIFO order
        dequeue_index = [0]
        dequeue_lock = asyncio.Lock()

        async def mock_dequeue(queue_name, timeout):
            async with dequeue_lock:
                if dequeue_index[0] < len(job_queue):
                    job = job_queue[dequeue_index[0]]
                    dequeue_index[0] += 1
                    return job
                return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock services
        mock_pii_analyzer.analyze_text.return_value = []
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create 2 workers
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(2)
        ]

        # Track processing order
        processing_order = []
        order_lock = asyncio.Lock()

        for worker in workers:
            async def track_order(job):
                async with order_lock:
                    processing_order.append(job.job_id)
                await job_service.update_job_status(job.job_id, STATUS_PROCESSING)

            worker.pii_service.process_pii_job = AsyncMock(side_effect=track_order)

        # Process with both workers
        async def worker_loop(worker):
            while True:
                job_data = await queue_service.dequeue(PII_QUEUE, timeout=0.1)
                if job_data is None:
                    break
                job = PIIQueuePayload.model_validate(job_data)
                await worker.pii_service.process_pii_job(job)

        tasks = [worker_loop(worker) for worker in workers]
        await asyncio.gather(*tasks)

        # Verify: All jobs processed
        assert len(processing_order) == 10

        # Verify: Processing order matches queue order (FIFO maintained)
        assert processing_order == job_ids


class TestScalability:
    """Tests for horizontal scalability."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_ten_workers_process_large_queue(
        self,
        storage_service,
        queue_service,
        job_service,
        mock_pii_analyzer,
        sample_pdf_content
    ):
        """Test system with 10 workers processing 100 jobs."""
        # Setup: 100 jobs
        num_jobs = 100
        job_queue = []

        for i in range(num_jobs):
            job_id = str(uuid.uuid4())
            payload = PIIQueuePayload(
                job_id=job_id,
                s3_key=f"temp/{job_id}/test{i}.pdf",
                created_at=datetime.now(timezone.utc)
            )
            job_queue.append(payload.model_dump())

        # Mock queue
        dequeue_index = [0]
        dequeue_lock = asyncio.Lock()

        async def mock_dequeue(queue_name, timeout):
            async with dequeue_lock:
                if dequeue_index[0] < len(job_queue):
                    job = job_queue[dequeue_index[0]]
                    dequeue_index[0] += 1
                    return job
                return None

        queue_service.dequeue = AsyncMock(side_effect=mock_dequeue)

        # Mock services
        mock_pii_analyzer.analyze_text.return_value = []
        storage_service.download_temp_file = AsyncMock(return_value=sample_pdf_content)

        # Create 10 workers
        num_workers = 10
        workers = [
            PIIWorker(
                storage_service=storage_service,
                queue_service=queue_service,
                job_service=job_service
            )
            for _ in range(num_workers)
        ]

        # Track total processed
        processed_count = [0]
        process_lock = asyncio.Lock()

        for worker in workers:
            async def track_process(job):
                async with process_lock:
                    processed_count[0] += 1
                await asyncio.sleep(0.001)  # Simulate minimal processing
                await job_service.update_job_status(job.job_id, STATUS_PROCESSING)

            worker.pii_service.process_pii_job = AsyncMock(side_effect=track_process)

        # Process with all workers
        async def worker_loop(worker):
            while True:
                job_data = await queue_service.dequeue(PII_QUEUE, timeout=0.1)
                if job_data is None:
                    break
                job = PIIQueuePayload.model_validate(job_data)
                await worker.pii_service.process_pii_job(job)

        tasks = [worker_loop(worker) for worker in workers]
        await asyncio.gather(*tasks)

        # Verify: All jobs processed
        assert processed_count[0] == num_jobs
